import uuid
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import JointState 
from rclpy.callback_groups import ReentrantCallbackGroup
from unilabos.ros.nodes.base_device_node import BaseROS2DeviceNode

class JointRepublisher(BaseROS2DeviceNode):
    def __init__(self,device_id, registry_name, resource_tracker, **kwargs):
        super().__init__(
            driver_instance=self,
            device_id=device_id,
            registry_name=registry_name,
            status_types={},
            action_value_mappings={},
            hardware_interface={},
            print_publish=False,
            resource_tracker=resource_tracker,  
            device_uuid=kwargs.get("uuid", str(uuid.uuid4())),
        )  
        
        # print('-'*20,device_id)
        # 创建订阅者
        self.create_subscription(
            JointState,               
            '/joint_states',         
            self.listener_callback,  
            10,
            callback_group=ReentrantCallbackGroup()
        )

        # Link E：Sim 反向驱动 Edge。Sim 控制模式下 Edge 不启动 controller，
        # 收到 joint_command.set 后按 joint 名直接发布 JointState（话题可配）。
        self._register_isaac_joint_command()

    def _register_isaac_joint_command(self):
        try:
            from unilabos.config.config import SimGatewayConfig
            from unilabos.sim.isaac_gateway import get_active_gateway
        except Exception:
            return
        topic = getattr(SimGatewayConfig, "joint_command_topic", "/joint_states")
        self._joint_cmd_pub = self.create_publisher(JointState, topic, 10)
        gateway = get_active_gateway()
        if gateway is not None:
            gateway.add_joint_command_handler(self._on_isaac_joint_command)

    def _on_isaac_joint_command(self, payload: dict):
        """Link E handler：将 Sim 下发的目标关节按名直接发布为 JointState 驱动 Edge。"""
        names = payload.get("joint_names") or []
        positions = payload.get("target_positions_rad") or []
        if not names or len(names) != len(positions):
            return {"status": "rejected",
                    "error": {"code": "INVALID_PAYLOAD", "message": "names/positions mismatch"}}
        pub = getattr(self, "_joint_cmd_pub", None)
        if pub is None:
            return {"status": "rejected",
                    "error": {"code": "DEVICE_NOT_CONTROLLABLE", "message": "joint command publisher not ready"}}
        msg = JointState()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.name = list(names)
        msg.position = [float(p) for p in positions]
        pub.publish(msg)
        return {"status": "accepted", "executed_positions_rad": [float(p) for p in positions]}

    def listener_callback(self, msg:JointState):

        try:
            json_dict = {}
            json_dict["name"]           = list(msg.name)
            json_dict["position"]       = list(msg.position)
            json_dict["velocity"]       = list(msg.velocity)
            json_dict["effort"]         = list(msg.effort)

            self._forward_to_isaac(json_dict)

        except Exception as e:
            print(e)

    def _forward_to_isaac(self, joint_dict: dict):
        """Link B: best-effort forward /joint_states to Isaac Sim gateway (if enabled)."""
        try:
            from unilabos.sim.isaac_gateway import get_active_gateway
        except Exception:
            return
        gateway = get_active_gateway()
        if gateway is None:
            return
        names = joint_dict.get("name") or []
        positions = joint_dict.get("position") or []
        if not names or not positions:
            return
        velocities = joint_dict.get("velocity") or None
        efforts = joint_dict.get("effort") or None
        gateway.publish_joint_state(
            device_id=self.device_id,
            base_frame="world",
            joint_names=list(names),
            joint_positions_rad=list(positions),
            joint_velocities=list(velocities) if velocities else None,
            joint_efforts=list(efforts) if efforts else None,
        )


def main():

    rclpy.init()
    subscriber = JointRepublisher()
    rclpy.spin(subscriber)
    subscriber.destroy_node()
    rclpy.shutdown()


if __name__ == '__main__':
    main()
