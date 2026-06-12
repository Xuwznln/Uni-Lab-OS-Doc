from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Dict, Optional

import requests

from unilabos.registry.decorators import action, device, not_action, topic_config

if TYPE_CHECKING:
    from unilabos.ros.nodes.base_device_node import BaseROS2DeviceNode


SEQUENCE_STATUS_TEXT = {
    0: "Unknown",
    1: "Running",
    2: "Completed",
    3: "Paused",
    4: "Stopped",
    5: "Error",
}


@device(
    id="huapu_hplc",
    category=["hplc"],
    description="华谱 HPLC HTTP API 驱动",
    displayname="华谱 HPLC",
)
class HuapuHPLC:
    """华谱 HPLC HTTP API 驱动。"""

    _ros_node: BaseROS2DeviceNode

    def __init__(
        self,
        device_id: Optional[str] = None,
        host: str = "10.10.10.1",
        port: int = 8001,
        base_url: str = "",
        timeout: float = 10.0,
        default_project_id: int = 0,
        default_sequence_method_id: int = 0,
        default_process_method_id: int = 0,
        default_report_method_id: int = 0,
        default_instrument_id: int = 0,
        export_path: str = "D:\\",
        default_seq_tray_num: int = 0,
        default_tray_num: int = 0,
        default_x_position: str = "A",
        default_y_position: int = 1,
        poll_interval: float = 5.0,
        poll_timeout: float = 7200.0,
        **kwargs,
    ):
        """
        初始化华谱 HPLC。

        Args:
            device_id[设备ID]: 设备实例 ID，默认使用 huapu_hplc。
            host[主机地址]: 华谱 HTTP 服务 IP，默认 10.10.10.1。
            port[端口]: 华谱 HTTP 服务端口，默认 8001。
            base_url[服务地址]: 完整服务地址；为空时按 host 和 port 生成。
            timeout[请求超时(s)]: 单次 HTTP 请求超时时间。
            default_project_id[默认项目ID]: 未显式传入 project_id 时使用。
            default_sequence_method_id[默认序列方法ID]: 未显式传入 sequence_method_id 时使用。
            default_process_method_id[默认处理方法ID]: 未显式传入 process_method_id 时使用。
            default_report_method_id[默认报告方法ID]: 未显式传入 report_method_id 时使用。
            default_instrument_id[默认仪器ID]: 未显式传入 instrument_id 时使用。
            export_path[报告导出路径]: 运行序列时传给 exportPath。
            default_seq_tray_num[默认序列盘号]: addSequenceMethod 的 seqTrayNum。
            default_tray_num[默认样品盘号]: 样品默认 trayNum。
            default_x_position[默认X坐标]: 样品默认 xPosition。
            default_y_position[默认Y坐标]: 样品默认 yPosition。
            poll_interval[轮询间隔(s)]: run_sample 等待序列完成时的轮询间隔。
            poll_timeout[轮询超时(s)]: run_sample 等待序列完成的总超时时间。
        """
        self.device_id = device_id or "huapu_hplc"
        self.host = host
        self.port = int(port)
        self.base_url = (base_url or f"http://{host}:{self.port}").rstrip("/")
        self.timeout = timeout
        self.default_project_id = int(default_project_id)
        self.default_sequence_method_id = int(default_sequence_method_id)
        self.default_process_method_id = int(default_process_method_id)
        self.default_report_method_id = int(default_report_method_id)
        self.default_instrument_id = int(default_instrument_id)
        self.export_path = export_path
        self.default_seq_tray_num = int(default_seq_tray_num)
        self.default_tray_num = int(default_tray_num)
        self.default_x_position = default_x_position
        self.default_y_position = int(default_y_position)
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout
        self.session = requests.Session()
        self.data: Dict[str, Any] = {
            "status": "Idle",
            "message": "",
            "last_proc_inst_id": "",
            "last_sequence_status": "Unknown",
        }

    @not_action
    def post_init(self, ros_node: BaseROS2DeviceNode) -> None:
        self._ros_node = ros_node

    @not_action
    def _make_success(self, data: Any = None, message: str = "", raw: Any = None) -> Dict[str, Any]:
        return {"success": True, "message": message, "data": data, "raw": raw}

    @not_action
    def _make_error(self, message: str, raw: Any = None) -> Dict[str, Any]:
        self.data["status"] = "Error"
        self.data["message"] = message
        return {"success": False, "message": message, "data": None, "raw": raw}

    @not_action
    def _post(self, endpoint: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"{self.base_url}{endpoint}"
        try:
            response = self.session.post(
                url,
                json=payload or {},
                headers={"Content-Type": "application/json;charset=UTF-8"},
                timeout=self.timeout,
            )
            response.raise_for_status()
            raw = response.json()
        except requests.exceptions.RequestException as exc:
            return self._make_error(f"HTTP 请求失败: {exc}")
        except ValueError as exc:
            return self._make_error(f"响应不是 JSON: {exc}")

        if isinstance(raw, dict) and "code" in raw:
            code = raw.get("code")
            success = str(code) == "0"
            message = str(raw.get("msg") or "")
            if success:
                self.data["message"] = message
                return self._make_success(raw.get("data"), message, raw)
            return self._make_error(message or f"接口返回失败 code={code}", raw)

        self.data["message"] = ""
        return self._make_success(raw, raw=raw)

    @not_action
    def _resolve_project_id(self, project_id: int) -> int:
        return int(project_id or self.default_project_id)

    @not_action
    def _resolve_instrument_id(self, instrument_id: int) -> int:
        return int(instrument_id or self.default_instrument_id)

    @not_action
    def _sequence_status_name(self, status_code: Any) -> str:
        try:
            return SEQUENCE_STATUS_TEXT.get(int(status_code), "Unknown")
        except (TypeError, ValueError):
            return "Unknown"

    @not_action
    def _build_sample(
        self,
        tray_num: int,
        x_position: str,
        y_position: int,
        sample_vol: float,
        sample_name: str,
        delay: float,
        batch_num: str,
    ) -> Dict[str, Any]:
        sample = {
            "trayNum": int(tray_num),
            "xPosition": x_position,
            "yPosition": int(y_position),
        }
        if sample_vol > 0:
            sample["sampleVol"] = float(sample_vol)
        if sample_name:
            sample["sampleName"] = sample_name
        if delay > 0:
            sample["delay"] = float(delay)
        if batch_num:
            sample["batchNum"] = batch_num
        return sample

    @action(always_free=True, description="获取所有项目")
    def list_projects(self) -> Dict[str, Any]:
        return self._post("/project/findAllProjectList")

    @action(always_free=True, description="获取项目中的序列、处理、报告方法")
    def list_methods(self, project_id: int = 0) -> Dict[str, Any]:
        """
        Args:
            project_id[项目ID]: 为空或 0 时使用默认项目 ID。
        """
        project_id = self._resolve_project_id(project_id)
        methods = {
            "sequence_methods": self.list_sequence_methods(project_id),
            "process_methods": self.list_process_methods(project_id),
            "report_methods": self.list_report_methods(project_id),
        }
        failed_messages = [item["message"] for item in methods.values() if not item.get("success")]
        if failed_messages:
            message = "；".join(message for message in failed_messages if message)
            self.data["status"] = "Error"
            self.data["message"] = message
            return {"success": False, "message": message, "data": methods}
        return {
            "success": True,
            "message": "",
            "data": methods,
        }

    @action(always_free=True, description="获取项目中的序列方法")
    def list_sequence_methods(self, project_id: int = 0) -> Dict[str, Any]:
        project_id = self._resolve_project_id(project_id)
        return self._post("/project/findSequenceMethodList", {"projectId": project_id})

    @action(always_free=True, description="获取项目中的处理方法")
    def list_process_methods(self, project_id: int = 0) -> Dict[str, Any]:
        project_id = self._resolve_project_id(project_id)
        return self._post("/project/findProcessMethodList", {"projectId": project_id})

    @action(always_free=True, description="获取项目中的报告方法")
    def list_report_methods(self, project_id: int = 0) -> Dict[str, Any]:
        project_id = self._resolve_project_id(project_id)
        return self._post("/project/findReportMethodList", {"projectId": project_id})

    @action(always_free=True, description="获取仪器系统列表")
    def list_instruments(self) -> Dict[str, Any]:
        return self._post("/project/findInstrumentList")

    @action(description="新建序列方法")
    def create_sequence(
        self,
        proc_inst_id: str,
        project_id: int = 0,
        sequence_method_id: int = 0,
        instrument_id: int = 0,
        name: str = "",
        sample_name: str = "",
        sample_vol: float = 0.0,
        tray_num: int = -1,
        x_position: str = "",
        y_position: int = 0,
        delay: float = 0.0,
        batch_num: str = "",
        seq_tray_num: int = -1,
    ) -> Dict[str, Any]:
        """
        Args:
            proc_inst_id[流程ID]: 用户指定的流程 ID，字符串类型，同一项目内必须唯一。
            project_id[项目ID]: 为空或 0 时使用默认项目 ID。
            sequence_method_id[参考序列方法ID]: 为空或 0 时使用默认序列方法 ID。
            instrument_id[仪器ID]: 为空或 0 时使用默认仪器 ID。
            name[序列方法名称]: 为空时自动使用 proc_inst_id 生成名称。
            sample_name[样品名称]: 样品名称。
            sample_vol[进样量(uL)]: 进样量，0 时不传。
            tray_num[样品盘号]: -1 时使用默认样品盘号。
            x_position[X坐标]: 为空时使用默认 X 坐标。
            y_position[Y坐标]: 为空或 0 时使用默认 Y 坐标。
            delay[下一针延迟(min)]: 下一针延迟，0 时不传。
            batch_num[批号]: 样品批号。
            seq_tray_num[序列盘号]: -1 时使用默认序列盘号。
        """
        project_id = self._resolve_project_id(project_id)
        sequence_method_id = int(sequence_method_id or self.default_sequence_method_id)
        instrument_id = self._resolve_instrument_id(instrument_id)
        seq_tray_num = self.default_seq_tray_num if seq_tray_num < 0 else int(seq_tray_num)
        tray_num = self.default_tray_num if tray_num < 0 else int(tray_num)
        x_position = x_position or self.default_x_position
        y_position = int(y_position or self.default_y_position)
        sample = self._build_sample(tray_num, x_position, y_position, sample_vol, sample_name, delay, batch_num)
        payload = {
            "procInstId": str(proc_inst_id),
            "projectId": project_id,
            "sequenceMethodId": sequence_method_id,
            "name": name or f"sequence_{proc_inst_id}",
            "seqTrayNum": seq_tray_num,
            "sampleArr": [sample],
            "instIdArr": [instrument_id] if instrument_id else [],
        }
        result = self._post("/project/addSequenceMethod", payload)
        if result["success"]:
            self.data["status"] = "SequenceCreated"
            self.data["last_proc_inst_id"] = str(proc_inst_id)
        return result

    @action(description="运行序列")
    def run_sequence(
        self,
        proc_inst_id: str,
        process_method_id: int = 0,
        report_method_id: int = 0,
        shut_down: int = 0,
        export_path: str = "",
    ) -> Dict[str, Any]:
        """
        Args:
            proc_inst_id[流程ID]: 已创建序列使用的流程 ID。
            process_method_id[处理方法ID]: 为空或 0 时使用默认处理方法 ID。
            report_method_id[报告方法ID]: 为空或 0 时使用默认报告方法 ID。
            shut_down[完成后关机]: 0-否，1-是。
            export_path[报告导出路径]: 为空时使用默认导出路径。
        """
        payload = {
            "procInstId": str(proc_inst_id),
            "proMethodId": int(process_method_id or self.default_process_method_id),
            "reportMethodId": int(report_method_id or self.default_report_method_id),
            "shutDown": int(shut_down),
            "exportPath": export_path or self.export_path,
        }
        result = self._post("/project/runSequence", payload)
        if result["success"]:
            self.data["status"] = "Running"
            self.data["last_proc_inst_id"] = str(proc_inst_id)
        return result

    @action(description="检测序列是否完成")
    def wait_sequence_complete(
        self,
        proc_inst_id: str,
        wait: bool = True,
        poll_interval: float = 5.0,
        poll_timeout: float = 500.0,
    ) -> Dict[str, Any]:
        """
        轮询序列状态，检测 HPLC 序列是否运行完成。

        Args:
            proc_inst_id[流程ID]: 已运行序列使用的流程 ID。
            wait[等待完成]: True 时轮询等待至序列结束；False 时只查询一次当前状态。
            poll_interval[轮询间隔(s)]: 为空或 0 时使用初始化配置的轮询间隔。
            poll_timeout[轮询超时(s)]: 为空或 0 时使用初始化配置的轮询超时时间。
        """
        interval = poll_interval if poll_interval > 0 else self.poll_interval
        timeout = poll_timeout if poll_timeout > 0 else self.poll_timeout

        def _build_result(status_result: Dict[str, Any]) -> Dict[str, Any]:
            status_data = status_result.get("data")
            status_code = status_data.get("status") if isinstance(status_data, dict) else None
            status_name = self._sequence_status_name(status_code)
            return {
                "success": status_code == 2,
                "completed": status_code in (2, 4, 5),
                "message": status_name,
                "data": {
                    "procInstId": str(proc_inst_id),
                    "status_code": status_code,
                    "sequence_status": status_data,
                },
                "raw": status_result.get("raw"),
            }

        status_result = self.get_sequence_status(proc_inst_id)
        if not status_result["success"]:
            return status_result
        status_data = status_result.get("data")
        status_code = status_data.get("status") if isinstance(status_data, dict) else None
        if not wait or status_code in (2, 4, 5):
            return _build_result(status_result)

        deadline = time.monotonic() + timeout
        last_status = status_result
        while time.monotonic() < deadline:
            time.sleep(interval)
            last_status = self.get_sequence_status(proc_inst_id)
            status_data = last_status.get("data")
            status_code = status_data.get("status") if isinstance(status_data, dict) else None
            if status_code in (2, 4, 5):
                return _build_result(last_status)

        return self._make_error(f"等待序列完成超时: procInstId={proc_inst_id}", last_status)

    @action(description="控制序列")
    def control_sequence(self, action: int, proc_inst_id: str = "") -> Dict[str, Any]:
        """
        Args:
            action[控制动作]: 1-暂停，2-重开，3-停止所有序列。
            proc_inst_id[流程ID]: 调试记录要求复用流程 ID，若服务端忽略该字段也可留空。
        """
        payload = {"action": int(action)}
        if proc_inst_id:
            payload["procInstId"] = str(proc_inst_id)
        result = self._post("/project/controlSequence", payload)
        if result["success"] and int(action) == 3:
            self.data["status"] = "Stopped"
        return result

    @action(always_free=True, description="获取序列状态")
    def get_sequence_status(self, proc_inst_id: str) -> Dict[str, Any]:
        result = self._post("/project/getSequenceStatus", {"procInstId": str(proc_inst_id)})
        if result["success"] and isinstance(result.get("data"), dict):
            status_name = self._sequence_status_name(result["data"].get("status"))
            self.data["last_sequence_status"] = status_name
            self.data["status"] = status_name
        return result

    @action(always_free=True, description="获取设备状态")
    def get_device_status(self, instrument_id: int = 0) -> Dict[str, Any]:
        instrument_id = self._resolve_instrument_id(instrument_id)
        return self._post("/project/getDeviceStatus", {"instrumentId": instrument_id})

    @action(always_free=True, description="获取错误详细信息")
    def get_error_detail(self, instrument_id: int = 0) -> Dict[str, Any]:
        instrument_id = self._resolve_instrument_id(instrument_id)
        return self._post("/project/getErrorDetail", {"instrumentId": instrument_id})

    @action(always_free=True, description="获取样品盘传感器状态")
    def find_tray_sensor(self, instrument_id: int = 0, tray_num: int = -1) -> Dict[str, Any]:
        instrument_id = self._resolve_instrument_id(instrument_id)
        tray_num = self.default_tray_num if tray_num < 0 else int(tray_num)
        return self._post("/project/findTraySensor", {"instrumentId": instrument_id, "trayNum": tray_num})

    @action(description="单样品建序列并运行")
    def run_sample(
        self,
        proc_inst_id: str,
        project_id: int = 0,
        sequence_method_id: int = 0,
        process_method_id: int = 0,
        report_method_id: int = 0,
        instrument_id: int = 0,
        name: str = "",
        sample_name: str = "",
        sample_vol: float = 0.0,
        tray_num: int = -1,
        x_position: str = "",
        y_position: int = 0,
        delay: float = 0.0,
        batch_num: str = "",
        shut_down: int = 0,
        export_path: str = "",
        wait: bool = True,
    ) -> Dict[str, Any]:
        """
        Args:
            proc_inst_id[流程ID]: 用户指定的流程 ID，字符串类型，同一项目内必须唯一。
            project_id[项目ID]: 为空或 0 时使用默认项目 ID。
            sequence_method_id[参考序列方法ID]: 为空或 0 时使用默认序列方法 ID。
            process_method_id[处理方法ID]: 为空或 0 时使用默认处理方法 ID。
            report_method_id[报告方法ID]: 为空或 0 时使用默认报告方法 ID。
            instrument_id[仪器ID]: 为空或 0 时使用默认仪器 ID。
            name[序列方法名称]: 为空时自动使用 proc_inst_id 生成名称。
            sample_name[样品名称]: 样品名称。
            sample_vol[进样量(uL)]: 进样量。
            tray_num[样品盘号]: -1 时使用默认样品盘号。
            x_position[X坐标]: 为空时使用默认 X 坐标。
            y_position[Y坐标]: 为空或 0 时使用默认 Y 坐标。
            delay[下一针延迟(min)]: 下一针延迟。
            batch_num[批号]: 样品批号。
            shut_down[完成后关机]: 0-否，1-是。
            export_path[报告导出路径]: 为空时使用默认导出路径。
            wait[等待完成]: 是否轮询等待序列完成。
        """
        create_result = self.create_sequence(
            proc_inst_id=proc_inst_id,
            project_id=project_id,
            sequence_method_id=sequence_method_id,
            instrument_id=instrument_id,
            name=name,
            sample_name=sample_name,
            sample_vol=sample_vol,
            tray_num=tray_num,
            x_position=x_position,
            y_position=y_position,
            delay=delay,
            batch_num=batch_num,
        )
        if not create_result["success"]:
            return create_result

        run_result = self.run_sequence(
            proc_inst_id=proc_inst_id,
            process_method_id=process_method_id,
            report_method_id=report_method_id,
            shut_down=shut_down,
            export_path=export_path,
        )
        if not run_result["success"] or not wait:
            return run_result

        deadline = time.monotonic() + self.poll_timeout
        last_status: Dict[str, Any] = run_result
        while time.monotonic() < deadline:
            time.sleep(self.poll_interval)
            last_status = self.get_sequence_status(proc_inst_id)
            status_data = last_status.get("data")
            status_code = status_data.get("status") if isinstance(status_data, dict) else None
            if status_code in (2, 4, 5):
                return {
                    "success": status_code == 2,
                    "message": self._sequence_status_name(status_code),
                    "data": {
                        "procInstId": str(proc_inst_id),
                        "sequence_status": status_data,
                        "exportPath": export_path or self.export_path,
                    },
                    "raw": last_status.get("raw"),
                }

        return self._make_error(f"等待序列完成超时: procInstId={proc_inst_id}", last_status)

    @property
    @topic_config()
    def status(self) -> str:
        return self.data.get("status", "Unknown")

    @property
    @topic_config()
    def message(self) -> str:
        return self.data.get("message", "")

    @property
    @topic_config()
    def last_proc_inst_id(self) -> str:
        return str(self.data.get("last_proc_inst_id", ""))

    @property
    @topic_config()
    def last_sequence_status(self) -> str:
        return self.data.get("last_sequence_status", "Unknown")
