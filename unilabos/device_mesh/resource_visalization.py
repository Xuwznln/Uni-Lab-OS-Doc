import json
import os
import shutil
from pathlib import Path
import re

import yaml
from launch import LaunchService
from launch import LaunchDescription
from launch_ros.actions import Node as nd
import xacro
import xml.dom.minidom as _minidom
from lxml import etree


def _xacro_parse_utf8(inp, filename=None):
    """UTF-8 安全版本的 xacro.parse，修复 Windows 上 GBK 解码 xacro 文件失败的问题。"""
    f = None
    if inp is None:
        try:
            inp = f = open(filename, encoding="utf-8")
        except IOError as e:
            xacro.filestack.pop()
            raise xacro.XacroException(e.strerror + ": " + e.filename, exc=e)
    try:
        if isinstance(inp, str):
            return _minidom.parseString(inp)
        elif hasattr(inp, "read"):
            return _minidom.parse(inp)
        return inp
    finally:
        if f:
            f.close()


xacro.parse = _xacro_parse_utf8
from launch_param_builder import load_yaml
from launch_ros.parameter_descriptions import ParameterFile
from unilabos.registry.registry import lab_registry
from ament_index_python.packages import get_package_share_directory


def get_pattern_matches(folder, pattern):
    """Given all the files in the folder, find those that match the pattern.

    If there are groups defined, the groups are returned. Otherwise the path to the matches are returned.
    """
    matches = []
    if not folder.exists():
        return matches
    for child in folder.iterdir():
        if not child.is_file():
            continue
        m = pattern.search(child.name)
        if m:
            groups = m.groups()
            if groups:
                matches.append(groups[0])
            else:
                matches.append(child)
    return matches

class ResourceVisualization:
    def __init__(self, device: dict, resource: dict, enable_rviz: bool = True, scene_json=None):
        """初始化资源可视化类
        
        该类用于将设备和资源的3D模型可视化展示。通过解析设备和资源的配置信息,
        从注册表中获取对应的3D模型文件,并使用ROS2和RViz进行可视化。
        
        Args:
            device (dict): 设备配置字典,包含设备的类型、位置等信息
            resource (dict): 资源配置字典,包含资源的类型、位置等信息 
            registry (dict): 注册表字典,包含设备和资源类型的注册信息
            enable_rviz (bool, optional): 是否启用RViz可视化. Defaults to True.
            scene_json (dict | None, optional): 实验室建筑场景图 {nodes, rootNodeIds}，
                非空时生成 world_scene 宏并合并进 full_dev. Defaults to None.
        """
        self.launch_service = LaunchService()
        self.launch_description = LaunchDescription()
        self.resource_dict = resource
        self.resource_model = {}
        self.resource_type = ['deck', 'plate', 'container', 'tip_rack']
        self.mesh_path = Path(__file__).parent.absolute()
        self.enable_rviz = enable_rviz
        self.scene_json = scene_json
        registry = lab_registry

        self.srdf_str = '''<?xml version="1.0" ?>
        <robot xmlns:xacro="http://ros.org/wiki/xacro" name="full_dev">

        </robot>
        '''
        self.robot_state_str= '''<?xml version="1.0" ?>
        <robot xmlns:xacro="http://ros.org/wiki/xacro" name="full_dev">
        <link name="world"/>
        </robot>
        '''
        self.root = etree.fromstring(self.robot_state_str)
        self.root_srdf = etree.fromstring(self.srdf_str)
                
        xacro_uri = self.root.nsmap["xacro"]

        self.moveit_nodes = {}
        self.moveit_nodes_kinematics = {}
        self.moveit_controllers_yaml = {
            "moveit_controller_manager": "moveit_simple_controller_manager/MoveItSimpleControllerManager",
            "moveit_simple_controller_manager": {
                "controller_names": []
            }
        }
        self.ros2_controllers_yaml = {
            "controller_manager": {
                "ros__parameters": {
                    "update_rate": 100,
                    "joint_state_broadcaster": {
                        "type": "joint_state_broadcaster/JointStateBroadcaster",
                    }
                }
            }
        }

        # 遍历设备节点
        for node in device.values():
            if (node['type'] in self.resource_type and node['class'] != '') or (node['type'] == 'device' and node['class'] != ''):
                model_config = {}
                if node['type'] in self.resource_type:
                    resource_class = node['class']
                    if resource_class not in registry.resource_type_registry.keys():
                        raise ValueError(f"{node['id']}资源类型 {resource_class} 未在注册表中注册")
                    elif "model" in registry.resource_type_registry[resource_class].keys():
                        model_config = registry.resource_type_registry[resource_class]['model']
                elif node['type'] == 'device' and node['class'] != '':
                    device_class = node['class']
                    if device_class not in registry.device_type_registry.keys():
                        raise ValueError(f"{node['id']}设备类型 {device_class} 未在注册表中注册")
                    elif "model" in registry.device_type_registry[device_class].keys():
                        model_config = registry.device_type_registry[device_class]['model']
                if model_config:
                    if model_config['type'] == 'resource':
                        self.resource_model[node['id']] = {
                            'mesh': f"{str(self.mesh_path)}/resources/{model_config['mesh']}",
                            'mesh_tf': model_config['mesh_tf']}
                        if 'children_mesh' in model_config:
                            if model_config['children_mesh'] is not None:
                                self.resource_model[f"{node['id']}_"] = {
                                    'mesh': f"{str(self.mesh_path)}/resources/{model_config['children_mesh']}",
                                    'mesh_tf': model_config['children_mesh_tf']
                                }
                    elif model_config['type'] == 'device':

                        new_include = etree.SubElement(self.root, f"{{{xacro_uri}}}include")
                        new_include.set("filename", f"{str(self.mesh_path)}/devices/{model_config['mesh']}/macro_device.xacro")
                        new_dev = etree.SubElement(self.root, f"{{{xacro_uri}}}{model_config['mesh']}")
                        # 默认挂载到 world，若设备 pose.extra 指定了 parent_link 则使用指定的父 link
                        parent_link = "world"
                        pose_extra = node.get("pose", {}).get("extra") or {}
                        if pose_extra.get("parent_link"):
                            parent_link = pose_extra["parent_link"]
                        new_dev.set("parent_link", parent_link)
                        new_dev.set("mesh_path", str(self.mesh_path))
                        new_dev.set("device_name", node["id"]+"_")
                        # if node["parent"] is not None:
                        #     new_dev.set("station_name", node["parent"]+'_')
                        if "position" in node:
                            new_dev.set("x",str(float(node["position"]["position"]["x"])/1000))
                            new_dev.set("y",str(float(node["position"]["position"]["y"])/1000))
                            new_dev.set("z",str(float(node["position"]["position"]["z"])/1000))
                        if "rotation" in node["config"]:
                            new_dev.set("rx",str(float(node["config"]["rotation"]["x"])))
                            new_dev.set("ry",str(float(node["config"]["rotation"]["y"])))
                            new_dev.set("r",str(float(node["config"]["rotation"]["z"])))
                        if "pose" in node:
                            new_dev.set("x",str(float(node["pose"]["position"]["x"])/1000))
                            new_dev.set("y",str(float(node["pose"]["position"]["y"])/1000))
                            new_dev.set("z",str(float(node["pose"]["position"]["z"])/1000))
                            new_dev.set("rx",str(float(node["pose"]["rotation"]["x"])))
                            new_dev.set("ry",str(float(node["pose"]["rotation"]["y"])))
                            new_dev.set("r",str(float(node["pose"]["rotation"]["z"])))
                        if "device_config" in node["config"]:
                            for key, value in node["config"]["device_config"].items():
                                new_dev.set(key, str(value))

                        # 添加ros2_controller
                        if node['class'].find('moveit.')!= -1:
                            new_include_controller = etree.SubElement(self.root, f"{{{xacro_uri}}}include")
                            new_include_controller.set("filename", f"{str(self.mesh_path)}/devices/{model_config['mesh']}/config/macro.ros2_control.xacro")
                            new_controller = etree.SubElement(self.root, f"{{{xacro_uri}}}{model_config['mesh']}_ros2_control")
                            new_controller.set("device_name", node["id"]+"_")
                            new_controller.set("mesh_path", str(self.mesh_path))

                            # 添加moveit的srdf
                            new_include_srdf = etree.SubElement(self.root_srdf, f"{{{xacro_uri}}}include")
                            new_include_srdf.set("filename", f"{str(self.mesh_path)}/devices/{model_config['mesh']}/config/macro.srdf.xacro")
                            new_srdf = etree.SubElement(self.root_srdf, f"{{{xacro_uri}}}{model_config['mesh']}_srdf")
                            new_srdf.set("device_name", node["id"]+"_")
                            self.moveit_nodes[node["id"]] = model_config['mesh']
                    else:
                        print("错误的注册表类型！")

        # 合并实验室建筑场景 world_scene（墙体 box / 地板 box 或 mesh）
        self._append_world_scene(xacro_uri)

        re = etree.tostring(self.root, encoding="unicode")
        doc = xacro.parse(re)
        xacro.process_doc(doc)
        self.urdf_str = doc.toxml()

        # Plan 20：关节名契约校验——比对仿真发布器期望关节名与 URDF 实有可动关节。
        self._validate_joint_contract(device)


        re_srdf = etree.tostring(self.root_srdf, encoding="unicode")
        doc_srdf = xacro.parse(re_srdf)
        xacro.process_doc(doc_srdf)
        self.urdf_str_srdf = doc_srdf.toxml()


        if self.moveit_nodes:
            self.moveit_init()

    def _append_world_scene(self, xacro_uri: str) -> None:
        """把建筑场景生成为 world_scene 宏并 include + 实例化进 full_dev / SRDF。

        启动时先清空再重建 temp_mesh/（含生成的 xacro 与 STL），再追加 include。
        失败时不影响设备部分的正常装配。
        """
        if not self.scene_json:
            return
        try:
            from unilabos.resources.architecture_scene import (
                generate_world_scene_srdf_xacro,
                generate_world_scene_xacro,
                parse_scene,
            )

            temp_mesh_dir = os.path.join(str(self.mesh_path), "temp_mesh")
            # 删除 -> 重建最新：清空除 .gitignore 外的所有产物
            if os.path.isdir(temp_mesh_dir):
                for child in os.listdir(temp_mesh_dir):
                    if child == ".gitignore":
                        continue
                    child_path = os.path.join(temp_mesh_dir, child)
                    if os.path.isdir(child_path):
                        shutil.rmtree(child_path, ignore_errors=True)
                    else:
                        try:
                            os.remove(child_path)
                        except OSError:
                            pass
            os.makedirs(temp_mesh_dir, exist_ok=True)

            groups = parse_scene(self.scene_json, temp_mesh_dir)
            if not groups:
                print("[scene] 场景解析结果为空，跳过 world_scene 合并")
                return

            world_scene_xacro = os.path.join(temp_mesh_dir, "world_scene.xacro")
            world_scene_srdf = os.path.join(temp_mesh_dir, "world_scene.srdf.xacro")
            generate_world_scene_xacro(groups, str(self.mesh_path), world_scene_xacro)
            generate_world_scene_srdf_xacro(groups, world_scene_srdf)

            # URDF：include + 实例化 world_scene 宏（world_base 挂到 world）
            scene_include = etree.SubElement(self.root, f"{{{xacro_uri}}}include")
            scene_include.set("filename", world_scene_xacro)
            scene_macro = etree.SubElement(self.root, f"{{{xacro_uri}}}world_scene")
            scene_macro.set("mesh_path", str(self.mesh_path))
            scene_macro.set("parent_link", "world")
            for attr in ("x", "y", "z", "rx", "ry", "r"):
                scene_macro.set(attr, "0")

            # SRDF：include + 实例化 world_scene_srdf 宏（建筑 link 两两 disable_collisions）
            srdf_include = etree.SubElement(self.root_srdf, f"{{{xacro_uri}}}include")
            srdf_include.set("filename", world_scene_srdf)
            etree.SubElement(self.root_srdf, f"{{{xacro_uri}}}world_scene_srdf")

            total = sum(len(g["elements"]) for g in groups)
            print(f"[scene] 已合并 {total} 个建筑构件（{len(groups)} 组 level/world_base）到 full_dev")
        except Exception as scene_err:  # noqa: BLE001 - 场景失败不应阻断设备装配
            print(f"[scene] world_scene 合并失败: {scene_err}")

    def _validate_joint_contract(self, device: dict) -> None:
        """Plan 20：校验仿真发布器关节名与装配 URDF 的可动关节是否一致。

        由环境变量 ``UNILAB_JOINT_CONTRACT`` 控制：
        - ``off``   跳过校验
        - ``warn``  仅告警（默认，保护未登记设备/既有部署）
        - ``error`` 不一致即抛 ``JointContractError``（demo/CI 推荐）
        """
        mode = os.environ.get("UNILAB_JOINT_CONTRACT", "warn").strip().lower()
        if mode == "off":
            return
        from unilabos.utils.log import logger
        from unilabos.device_mesh.joint_contract import JointContractError, validate_contract

        try:
            issues = validate_contract(self.urdf_str, device, lab_registry)
        except Exception as e:
            logger.warning(f"[关节契约] 校验过程异常（跳过）: {e}")
            return
        if not issues:
            logger.info("[关节契约] 校验通过：所有已登记设备关节名与 URDF 一致。")
            return
        for issue in issues:
            logger.error(str(issue))
        if mode == "error":
            raise JointContractError(
                f"关节名契约校验失败，共 {len(issues)} 处不一致（见上方日志）。"
                f"请对齐 registry model.joints 与设备模型 URDF，或设 UNILAB_JOINT_CONTRACT=warn 临时放行。"
            )
        logger.warning(
            f"[关节契约] 共 {len(issues)} 处不一致（warn 模式放行）；"
            f"如需开机即拦截请设 UNILAB_JOINT_CONTRACT=error。"
        )

    def moveit_init(self):

        for name, config in self.moveit_nodes.items():
            controller_dict = yaml.safe_load(open(f"{str(self.mesh_path)}/devices/{config}/config/ros2_controllers.yaml", "r"))
            moveit_dict = yaml.safe_load(open(f"{str(self.mesh_path)}/devices/{config}/config/moveit_controllers.yaml", "r"))
            kinematics_dict = yaml.safe_load(open(f"{str(self.mesh_path)}/devices/{config}/config/kinematics.yaml", "r"))
            
            for key_kinematics, value_kinematics in kinematics_dict.items():
                self.moveit_nodes_kinematics[f'{name}_{key_kinematics}'] = value_kinematics
            
            for key, value in controller_dict['controller_manager']['ros__parameters'].items():
                if key == 'update_rate' or key == 'joint_state_broadcaster':
                    continue
                self.ros2_controllers_yaml['controller_manager']['ros__parameters'][f"{name}_{key}"] = value
                controller_dict[key]['ros__parameters']['joints'] = [f"{name}_{joint}" for joint in controller_dict[key]['ros__parameters']['joints']]
                self.ros2_controllers_yaml[f"{name}_{key}"] = controller_dict[key]

            for controller_name in moveit_dict['moveit_simple_controller_manager']['controller_names']:
                self.moveit_controllers_yaml['moveit_simple_controller_manager']['controller_names'].append(f"{name}_{controller_name}")
                moveit_dict['moveit_simple_controller_manager'][controller_name]['joints'] = [f"{name}_{joint}" for joint in moveit_dict['moveit_simple_controller_manager'][controller_name]['joints']]
                self.moveit_controllers_yaml['moveit_simple_controller_manager'][f"{name}_{controller_name}"] = moveit_dict['moveit_simple_controller_manager'][controller_name]


    def create_launch_description(self) -> LaunchDescription:
        """
        创建launch描述，包含robot_state_publisher和move_group节点

        Args:
            urdf_str: URDF文本

        Returns:
            LaunchDescription: launch描述对象
        """
        # 检查ROS 2环境变量
        if "AMENT_PREFIX_PATH" not in os.environ:
            raise OSError(
                "ROS 2环境未正确设置。需要设置 AMENT_PREFIX_PATH 环境变量。\n"
                "请确保：\n"
                "1. 已安装ROS 2 (推荐使用 ros-humble-desktop-full)\n"
                "2. 已激活Conda环境: conda activate unilab\n"
                "3. 或手动source ROS 2 setup文件: source /opt/ros/humble/setup.bash\n"
                "4. 或者使用 --backend simple 参数跳过ROS依赖"
            )

        try:
            moveit_configs_utils_path = Path(get_package_share_directory("moveit_configs_utils"))
        except Exception as e:
            raise OSError(
                f"无法找到moveit_configs_utils包。请确保ROS 2和MoveIt 2已正确安装。\n"
                f"原始错误: {e}"
            )
        default_folder = moveit_configs_utils_path / "default_configs"
        planning_pattern = re.compile("^(.*)_planning.yaml$")
        pipelines = []

        for pipeline in get_pattern_matches(default_folder, planning_pattern):
            if pipeline not in pipelines:
                pipelines.append(pipeline)

        if "ompl" in pipelines:
            default_planning_pipeline = "ompl"
        else:
            default_planning_pipeline = pipelines[0]

        planning_pipelines = {
            "planning_pipelines": pipelines,
            "default_planning_pipeline": default_planning_pipeline,
        }

        for pipeline in pipelines:
            planning_pipelines[pipeline] = load_yaml(
                default_folder /  f"{pipeline}_planning.yaml"
            )

        if "ompl" in planning_pipelines:
            ompl_config = planning_pipelines["ompl"]
            if "planner_configs" not in ompl_config:
                ompl_config.update(load_yaml(default_folder / "ompl_defaults.yaml"))

        yaml.safe_dump(self.ros2_controllers_yaml, open(f"{str(self.mesh_path)}/ros2_controllers.yaml", "w"))

        robot_description_planning = {
            "default_velocity_scaling_factor": 0.1,
            "default_acceleration_scaling_factor": 0.1,
            "cartesian_limits": {
            "max_trans_vel": 1.0,
            "max_trans_acc": 2.25,
            "max_trans_dec": -5.0,
            "max_rot_vel": 1.57
            }
        }
        # 解析URDF文件
        robot_description = self.urdf_str
        urdf_str_srdf = self.urdf_str_srdf

        kinematics_dict = self.moveit_nodes_kinematics


        if self.moveit_nodes:

            controllers = []
            ros2_controllers = ParameterFile(f"{str(self.mesh_path)}/ros2_controllers.yaml", allow_substs=True)

            controllers.append(
                nd(
                    package="controller_manager",
                    executable="ros2_control_node",
                    output='screen',
                    parameters=[
                        {"robot_description": robot_description},
                        ros2_controllers,
                    ],
                    env=dict(os.environ)
                )
            )
            for controller in self.moveit_controllers_yaml['moveit_simple_controller_manager']['controller_names']:
                controllers.append(
                    nd(
                        package="controller_manager",
                        executable="spawner",
                        arguments=[f"{controller}", "--controller-manager", f"controller_manager"],
                        output="screen",
                        env=dict(os.environ)
                    )
                )
            controllers.append(
                nd(
                        package="controller_manager",
                        executable="spawner",
                        arguments=["joint_state_broadcaster", "--controller-manager", f"controller_manager"],
                        output="screen",
                        env=dict(os.environ)
                )
            )
            for i in controllers:
                self.launch_description.add_action(i)
        else:
            ros2_controllers = None

        # 创建robot_state_publisher节点
        robot_state_publisher = nd(
            package='robot_state_publisher',
            executable='robot_state_publisher',
            name='robot_state_publisher',
            output='screen',
            parameters=[{
                'robot_description': robot_description,
                'use_sim_time': False
            },
            # kinematics_dict
            ],
            env=dict(os.environ)
        )


        # 创建move_group节点
        moveit_params =[{
            'allow_trajectory_execution': True,
            'robot_description': robot_description,
            'robot_description_semantic': urdf_str_srdf,
            'robot_description_kinematics': kinematics_dict,
            'capabilities': '',
            'disable_capabilities': '',
            'monitor_dynamics': False,
            'publish_monitored_planning_scene': True,
            'publish_robot_description_semantic': True,
            'publish_planning_scene': True,
            'publish_geometry_updates': True,
            'publish_state_updates': True,
            'publish_transforms_updates': True,
            # 'robot_description_planning': robot_description_planning,
            },
            robot_description_planning,
            planning_pipelines,
            ]
        if self.moveit_controllers_yaml['moveit_simple_controller_manager']['controller_names']:
            moveit_params.append(self.moveit_controllers_yaml)

        move_group = nd(
            package='moveit_ros_move_group',
            executable='move_group',
            output='screen',
            parameters=moveit_params,
            env=dict(os.environ)
        )


        # 将节点添加到launch描述中
        self.launch_description.add_action(robot_state_publisher)
        # self.launch_description.add_action(joint_state_publisher_node)
        self.launch_description.add_action(move_group)

        # 如果启用RViz,添加RViz节点
        if self.enable_rviz:
            rviz_node = nd(
                package='rviz2',
                executable='rviz2',
                name='rviz2',
                arguments=['-d', f"{str(self.mesh_path)}/view_robot.rviz"],
                output='screen',
                parameters=[
                    {'robot_description_kinematics': kinematics_dict,
                     },
                    robot_description_planning,
                    planning_pipelines,

                ],
                env=dict(os.environ)
            )
            self.launch_description.add_action(rviz_node)

        return self.launch_description

    def start(self) -> None:
        """
        启动可视化服务

        Args:
            urdf_str: URDF文件路径
        """
        launch_description = self.create_launch_description()
        # print('--------------------------------')
        # print(self.moveit_controllers_yaml)
        # print('--------------------------------')
        # print(self.urdf_str)
        # print('--------------------------------')
        self.launch_service.include_launch_description(launch_description)
        self.launch_service.run()