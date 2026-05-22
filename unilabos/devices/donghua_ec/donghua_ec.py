import logging
import os
import time
import threading
from pathlib import Path
from typing import Any, Dict, Optional

LOGGER_NAME = "DonghuaEC"

class DonghuaEC:
    """Donghua Electrochemistry controller (DH700x) via ECCore.dll.
    Currently exposes the "开路电位(能源)" entry point. Additional interfaces
    can be added incrementally.
    """

    def __init__(self, device_id: Optional[str] = None, config: Optional[Dict[str, Any]] = None, **kwargs):
        self.device_id = device_id or "donghua_ec"
        self.config = config or {}
        # Allow overriding via env for quick testing
        # 优先使用外部传入/环境变量，其次尝试本地随仓库自带的 x64release/DHInterface
        interface_dir_cfg = self.config.get("interface_dir") or os.getenv("DONGHUA_EC_INTERFACE_DIR")
        dll_path_cfg = self.config.get("dll_path") or os.getenv("DONGHUA_EC_DLL")

        fallback_dir = Path(__file__).resolve().parent / "x64release" / "DHInterface"
        self.interface_dir = Path(interface_dir_cfg).expanduser() if interface_dir_cfg else (fallback_dir if fallback_dir.exists() else None)
        self.dll_path = Path(dll_path_cfg).expanduser() if dll_path_cfg else None
        self.machine_id = int(self.config.get("machine_id", 0))

        self._elec = None
        self.logger = logging.getLogger(f"{LOGGER_NAME}.{self.device_id}")
        self.data: Dict[str, Any] = {"status": "offline", "machine_id": self.machine_id}
        self._rt_threads: Dict[int, threading.Thread] = {}
        self._rt_stop_flags: Dict[int, threading.Event] = {}
        self._rt_files: Dict[int, Path] = {}
        self._rt_header_written: Dict[int, bool] = {}

    @property
    def status(self) -> str:
        """Expose device status for ROS节点属性读取."""
        return self.data.get("status", "unknown")

    # lifecycle -----------------------------------------------------
    def post_init(self, ros_node=None):
        self.logger.info("post_init called for DonghuaEC")
        try:
            self._ensure_driver()
            self.data["status"] = "ready"
        except Exception as exc:
            self.logger.warning("Auto initialize failed: %s", exc)

    async def initialize(self) -> bool:
        self._ensure_driver()
        self.data["status"] = "ready"
        return True

    async def cleanup(self) -> bool:
        if self._elec:
            try:
                self._elec.Exit()
            except Exception as exc:  # pragma: no cover - best effort cleanup
                self.logger.warning("Failed to exit DonghuaEC driver: %s", exc)
            finally:
                self._elec = None
        self.data["status"] = "offline"
        return True

    # driver helpers ------------------------------------------------
    def _ensure_driver(self):
        if self._elec:
            return

        if self.interface_dir is None:
            raise ValueError("interface_dir is not configured. Set config.interface_dir or DONGHUA_EC_INTERFACE_DIR.")
        if self.dll_path is None:
            self.dll_path = self.interface_dir / "ECCore.dll"
        if not self.dll_path.exists():
            raise FileNotFoundError(f"ECCore.dll not found at {self.dll_path}")

        try:
            import clr  # type: ignore
        except ImportError as exc:  # pragma: no cover - runtime dependency
            raise RuntimeError(
                "pythonnet is required to load ECCore.dll. Install via `pip install pythonnet`."
            ) from exc

        clr.AddReference(str(self.dll_path))
        try:
            from ECCore import ElecMachines  # type: ignore
        except Exception as exc:  # pragma: no cover - runtime dependency
            raise RuntimeError(f"Failed to import ElecMachines from {self.dll_path}: {exc}") from exc

        self._elec = ElecMachines()
        ok = self._elec.Init(str(self.interface_dir))
        if not ok:
            raise RuntimeError(f"ElecMachines.Init failed for {self.interface_dir}")
        self.logger.info("DonghuaEC driver initialized via %s", self.interface_dir)

    # discovery -----------------------------------------------------
    def get_machine_ids(self):
        self._ensure_driver()
        ids = list(self._elec.GetMachineId())
        self.logger.info("Detected machines: %s", ids)
        return {"success": True, "machine_ids": ids}

    # experiment ----------------------------------------------------
    def start_open_circuit_energy(
        self,
        time_per_point: float = 0.1,
        continue_time: float = 120.0,
        is_use_excursion_rate: bool = False,
        excursion_rate: float = 0.0,
        is_voltage_trig: bool = True,
        voltage_or_current_trig_direction: int = 0,
        voltage_or_current_trig_value: float = 0.0,
        capacity_trig_direction: int = 0,
        capacity_trig_value: float = 0.0,
        is_use_resolution: bool = False,
        resolution: float = 10.0,
        is_use_delta_i: bool = False,
        delta_i: float = 0.0,
        is_use_delta_q: bool = False,
        delta_q: float = 0.0,
        is_voltage_rand_auto: int = 0,
        voltage_rand: str = "1000",
        is_current_rand_auto: int = 0,
        current_rand: str = "1000",
        machine_id: Optional[int] = None,
    ):
        """Invoke Start_EnergyOpenCircuit on ECCore.

        Returns a dict with success flag and return_info (error string if any).
        """
        self._ensure_driver()
        mid = int(self.machine_id if machine_id is None else machine_id)

        self.logger.info(
            "Start_EnergyOpenCircuit params: mid=%s, tpp=%s, ct=%s, voltTrig=%s, voltDir=%s, voltVal=%s, capDir=%s, capVal=%s, resFlag=%s, res=%s, dIFlag=%s, dI=%s, dQFlag=%s, dQ=%s, voltAuto=%s, voltRand=%s, currAuto=%s, currRand=%s",
            mid,
            time_per_point,
            continue_time,
            is_voltage_trig,
            voltage_or_current_trig_direction,
            voltage_or_current_trig_value,
            capacity_trig_direction,
            capacity_trig_value,
            is_use_resolution,
            resolution,
            is_use_delta_i,
            delta_i,
            is_use_delta_q,
            delta_q,
            is_voltage_rand_auto,
            voltage_rand,
            is_current_rand_auto,
            current_rand,
        )

        err = self._elec.Start_EnergyOpenCircuit(
            bool(is_use_excursion_rate),
            float(excursion_rate),
            bool(is_voltage_trig),
            int(voltage_or_current_trig_direction),
            float(voltage_or_current_trig_value),
            int(capacity_trig_direction),
            float(capacity_trig_value),
            float(time_per_point),
            float(continue_time),
            bool(is_use_resolution),
            float(resolution),
            bool(is_use_delta_i),
            float(delta_i),
            bool(is_use_delta_q),
            float(delta_q),
            int(is_voltage_rand_auto),
            str(voltage_rand),
            int(is_current_rand_auto),
            str(current_rand),
            mid,
        )

        success = err is None or err == ""
        self.data.update({"status": "experimenting" if success else "error", "last_machine_id": mid})
        if success:
            self.data["last_result_type"] = "energy"
        if success:
            self.data["last_experiment_start_ts"] = time.time()
        if success:
            self.logger.info("Start_EnergyOpenCircuit dispatched on machine %s", mid)
        else:
            self.logger.error("Start_EnergyOpenCircuit failed on machine %s: %s", mid, err)
        return {"success": success, "return_info": err or "ok", "machine_id": mid}

    def start_eis(
        self,
        is_sync_start: bool = True,
        start_freq: float = 10000.0,
        end_freq: float = 0.1,
        amplitude: float = 0.01,
        interval_type: int = 1,
        point_count: int = 10,
        voltage: float = 0.0,
        voltage_vs_type: int = 0,
        is_voltage_rand_auto: int = 0,
        voltage_rand: str = "10000",
        is_current_rand_auto: int = 1,
        current_rand: str = "1000",
        is_voltage_filter_auto: int = 1,
        voltage_filter: str = "10Hz",
        is_current_filter_auto: int = 1,
        current_filter: str = "10Hz",
        machine_id: Optional[int] = None,
        delay_time: float = 0.0,
        data_quality: int = 1,
    ):
        self._ensure_driver()
        mid = int(self.machine_id if machine_id is None else machine_id)

        self.logger.info(
            "Start_EIS params: mid=%s, sync=%s, sf=%s, ef=%s, amp=%s, itype=%s, pc=%s, volt=%s, vs=%s, vAuto=%s, vRand=%s, cAuto=%s, cRand=%s, vFiltAuto=%s, vFilt=%s, cFiltAuto=%s, cFilt=%s, delay=%s, dq=%s",
            mid,
            bool(is_sync_start),
            start_freq,
            end_freq,
            amplitude,
            interval_type,
            point_count,
            voltage,
            voltage_vs_type,
            is_voltage_rand_auto,
            voltage_rand,
            is_current_rand_auto,
            current_rand,
            is_voltage_filter_auto,
            voltage_filter,
            is_current_filter_auto,
            current_filter,
            delay_time,
            data_quality,
        )

        if bool(is_sync_start) and hasattr(self._elec, "Start_EIS_All"):
            err = self._elec.Start_EIS_All(
                True,
                float(start_freq),
                float(end_freq),
                float(amplitude),
                int(interval_type),
                int(point_count),
                float(voltage),
                int(voltage_vs_type),
                int(is_voltage_rand_auto),
                str(voltage_rand),
                int(is_current_rand_auto),
                str(current_rand),
                int(is_voltage_filter_auto),
                str(voltage_filter),
                int(is_current_filter_auto),
                str(current_filter),
                float(delay_time),
                int(data_quality),
            )
        else:
            err = self._elec.Start_EIS(
                True,
                float(start_freq),
                float(end_freq),
                float(amplitude),
                int(interval_type),
                int(point_count),
                float(voltage),
                int(voltage_vs_type),
                int(is_voltage_rand_auto),
                str(voltage_rand),
                int(is_current_rand_auto),
                str(current_rand),
                int(is_voltage_filter_auto),
                str(voltage_filter),
                int(is_current_filter_auto),
                str(current_filter),
                mid,
                float(delay_time),
                int(data_quality),
            )

        success = err is None or err == ""
        self.data.update({"status": "experimenting" if success else "error", "last_machine_id": mid})
        if success:
            self.data["last_result_type"] = "impedance"
            self.data["last_experiment_start_ts"] = time.time()
            self.logger.info("Start_EIS dispatched on machine %s", mid)
        else:
            self.logger.error("Start_EIS failed on machine %s: %s", mid, err)
        return {"success": success, "return_info": err or "ok", "machine_id": mid}

    def start_gitt(
        self,
        machine_id: Optional[int] = None,
        current: float = 1.0,
        is_voltage_trig: bool = True,
        voltage_or_current_trig_direction: int = 0,
        voltage_or_current_trig_value: float = 0.0,
        capacity_trig_direction: int = 0,
        capacity_trig_value: float = 0.0,
        time_per_point_cc: float = 0.1,
        continue_time_cc: float = 60.0,
        is_use_resolution: bool = False,
        resolution: float = 10.0,
        is_use_delta_i: bool = False,
        delta_i: float = 0.0,
        is_use_delta_q: bool = False,
        delta_q: float = 0.0,
        is_voltage_rand_auto_cc: int = 0,
        voltage_rand_cc: str = "10000",
        is_current_rand_auto_cc: int = 1,
        current_rand_cc: str = "1000",
        is_use_excursion_rate: bool = False,
        excursion_rate: float = 0.0,
        time_per_point_oc: float = 0.1,
        continue_time_oc: float = 60.0,
        is_voltage_rand_auto_oc: int = 0,
        voltage_rand_oc: str = "1000",
        is_current_rand_auto_oc: int = 0,
        current_rand_oc: str = "1000",
    ):
        self._ensure_driver()
        mid = int(self.machine_id if machine_id is None else machine_id)

        try:
            self._elec.PrepareLoopExperiment(mid)
        except Exception as exc:
            self.logger.error("PrepareLoopExperiment failed on machine %s: %s", mid, exc)
            return {"success": False, "machine_id": mid, "return_info": str(exc)}

        try:
            self._elec.AddCommonLoop("", "1", 1, mid)
            self._elec.AddDisChargeLoop("1", "1-1", mid)
        except Exception as exc:
            self.logger.error("Add loop failed on machine %s: %s", mid, exc)
            return {"success": False, "machine_id": mid, "return_info": str(exc)}

        try:
            err1 = self._elec.Add_ConstantCurrent(
                "1-1",
                float(current),
                bool(is_voltage_trig),
                int(voltage_or_current_trig_direction),
                float(voltage_or_current_trig_value),
                int(capacity_trig_direction),
                float(capacity_trig_value),
                float(time_per_point_cc),
                float(continue_time_cc),
                bool(is_use_resolution),
                float(resolution),
                bool(is_use_delta_i),
                float(delta_i),
                bool(is_use_delta_q),
                float(delta_q),
                int(is_voltage_rand_auto_cc),
                str(voltage_rand_cc),
                int(is_current_rand_auto_cc),
                str(current_rand_cc),
                mid,
            )
        except Exception as exc:
            err1 = str(exc)

        try:
            err2 = self._elec.Add_EnergyOpenCircuit(
                "1-1",
                bool(is_use_excursion_rate),
                float(excursion_rate),
                bool(is_voltage_trig),
                int(voltage_or_current_trig_direction),
                float(voltage_or_current_trig_value),
                int(capacity_trig_direction),
                float(capacity_trig_value),
                float(time_per_point_oc),
                float(continue_time_oc),
                bool(is_use_resolution),
                float(resolution),
                bool(is_use_delta_i),
                float(delta_i),
                bool(is_use_delta_q),
                float(delta_q),
                int(is_voltage_rand_auto_oc),
                str(voltage_rand_oc),
                int(is_current_rand_auto_oc),
                str(current_rand_oc),
                mid,
            )
        except Exception as exc:
            err2 = str(exc)

        ok1 = err1 is None or err1 == ""
        ok2 = err2 is None or err2 == ""
        if not (ok1 and ok2):
            return {"success": False, "machine_id": mid, "return_info": f"{err1 or ''}; {err2 or ''}"}

        try:
            self._elec.StartLoopExperiment(mid)
        except Exception as exc:
            return {"success": False, "machine_id": mid, "return_info": str(exc)}

        self.data.update({"status": "experimenting", "last_machine_id": mid})
        self.data["last_result_type"] = "gitt"
        self.data["last_experiment_start_ts"] = time.time()
        self.logger.info("Start_GITT dispatched on machine %s", mid)
        return {"success": True, "machine_id": mid, "return_info": "ok"}

    def start_linear_scan_voltammetry(
        self,
        start_voltage: float = 0.0,
        start_voltage_vs_type: int = 0,
        end_voltage: float = 1.0,
        end_voltage_vs_type: int = 0,
        scan_rate: float = 0.01,
        point_count: int = 100,
        is_voltage_rand_auto: int = 0,
        voltage_rand: str = "1000",
        is_current_rand_auto: int = 1,
        current_rand: str = "1000",
        is_voltage_filter_auto: int = 1,
        voltage_filter: str = "10Hz",
        is_current_filter_auto: int = 1,
        current_filter: str = "10Hz",
        machine_id: Optional[int] = None,
        delay_time: float = 0.0,
    ):
        self._ensure_driver()
        mid = int(self.machine_id if machine_id is None else machine_id)

        self.logger.info(
            "Start_Linear_Scan_Voltammetry params: mid=%s, sv=%s, svs=%s, ev=%s, evs=%s, rate=%s, pc=%s, vAuto=%s, vRand=%s, cAuto=%s, cRand=%s, vFiltAuto=%s, vFilt=%s, cFiltAuto=%s, cFilt=%s, delay=%s",
            mid,
            start_voltage,
            start_voltage_vs_type,
            end_voltage,
            end_voltage_vs_type,
            scan_rate,
            point_count,
            is_voltage_rand_auto,
            voltage_rand,
            is_current_rand_auto,
            current_rand,
            is_voltage_filter_auto,
            voltage_filter,
            is_current_filter_auto,
            current_filter,
            delay_time,
        )

        try:
            import System
            err = self._elec.Start_Linear_Scan_Voltammetry(
                System.Single(float(start_voltage)),
                System.Int32(int(start_voltage_vs_type)),
                System.Single(float(end_voltage)),
                System.Int32(int(end_voltage_vs_type)),
                System.Single(float(scan_rate)),
                System.Int32(int(is_voltage_rand_auto)),
                System.String(str(voltage_rand)),
                System.Int32(int(is_current_rand_auto)),
                System.String(str(current_rand)),
                System.Int32(int(is_voltage_filter_auto)),
                System.String(str(voltage_filter)),
                System.Int32(int(is_current_filter_auto)),
                System.String(str(current_filter)),
                System.Int32(int(mid)),
                System.Single(float(delay_time)),
            )
        except Exception:
            try:
                import System
                err = self._elec.Start_Linear_Scan_Voltammetry_New(
                    System.Single(float(start_voltage)),
                    System.Int32(int(start_voltage_vs_type)),
                    System.Single(float(end_voltage)),
                    System.Int32(int(end_voltage_vs_type)),
                    System.Single(float(scan_rate)),
                    System.Single(float(0.001)),
                    System.Int32(int(is_voltage_rand_auto)),
                    System.String(str(voltage_rand)),
                    System.Int32(int(is_current_rand_auto)),
                    System.String(str(current_rand)),
                    System.Int32(int(is_voltage_filter_auto)),
                    System.String(str(voltage_filter)),
                    System.Int32(int(is_current_filter_auto)),
                    System.String(str(current_filter)),
                    System.Int32(int(mid)),
                    System.Single(float(delay_time)),
                )
            except Exception as exc2:
                err = str(exc2)
        except Exception as exc:
            err = str(exc)

        success = err is None or err == ""
        self.data.update({"status": "experimenting" if success else "error", "last_machine_id": mid})
        if success:
            self.data["last_result_type"] = "linear_scan"
            self.data["last_experiment_start_ts"] = time.time()
            self.logger.info("Start_Linear_Scan_Voltammetry dispatched on machine %s", mid)
        else:
            self.logger.error("Start_Linear_Scan_Voltammetry failed on machine %s: %s", mid, err)
        return {"success": success, "return_info": err or "ok", "machine_id": mid}

    def start_cyclic_voltammetry_multi(
        self,
        is_use_initial_potential: bool = True,
        initial_potential: float = -1.0,
        initial_potential_vs_type: int = 0,
        top_potential1: float = 1.0,
        top_potential1_vs_type: int = 0,
        top_potential2: float = -2.0,
        top_potential2_vs_type: int = 0,
        is_use_finally_potential: bool = True,
        finally_potential: float = -1.0,
        finally_potential_vs_type: int = 0,
        scan_rate: float = 0.2,
        cycle_count: int = 2,
        is_voltage_rand_auto: int = 1,
        voltage_rand: str = "1000",
        is_current_rand_auto: int = 1,
        current_rand: str = "1000",
        is_voltage_filter_auto: int = 1,
        voltage_filter: str = "10Hz",
        is_current_filter_auto: int = 1,
        current_filter: str = "10Hz",
        machine_id: Optional[int] = None,
        delay_time: float = 0.0,
    ):
        self._ensure_driver()
        mid = int(self.machine_id if machine_id is None else machine_id)

        self.logger.info(
            "Start_Circle_Voltammetry_Multi params: mid=%s, useInit=%s, ip=%s, ipvs=%s, tp1=%s, tp1vs=%s, tp2=%s, tp2vs=%s, useFin=%s, fp=%s, fpvs=%s, rate=%s, cycles=%s, vAuto=%s, vRand=%s, cAuto=%s, cRand=%s, vFiltAuto=%s, vFilt=%s, cFiltAuto=%s, cFilt=%s, delay=%s",
            mid,
            bool(is_use_initial_potential),
            initial_potential,
            initial_potential_vs_type,
            top_potential1,
            top_potential1_vs_type,
            top_potential2,
            top_potential2_vs_type,
            bool(is_use_finally_potential),
            finally_potential,
            finally_potential_vs_type,
            scan_rate,
            cycle_count,
            is_voltage_rand_auto,
            voltage_rand,
            is_current_rand_auto,
            current_rand,
            is_voltage_filter_auto,
            voltage_filter,
            is_current_filter_auto,
            current_filter,
            delay_time,
        )

        try:
            import System
            err = self._elec.Start_Circle_Voltammetry_Multi(
                bool(is_use_initial_potential),
                System.Single(float(initial_potential)),
                System.Int32(int(initial_potential_vs_type)),
                System.Single(float(top_potential1)),
                System.Int32(int(top_potential1_vs_type)),
                System.Single(float(top_potential2)),
                System.Int32(int(top_potential2_vs_type)),
                bool(is_use_finally_potential),
                System.Single(float(finally_potential)),
                System.Int32(int(finally_potential_vs_type)),
                System.Single(float(scan_rate)),
                System.Int32(int(cycle_count)),
                System.Int32(int(is_voltage_rand_auto)),
                System.String(str(voltage_rand)),
                System.Int32(int(is_current_rand_auto)),
                System.String(str(current_rand)),
                System.Int32(int(is_voltage_filter_auto)),
                System.String(str(voltage_filter)),
                System.Int32(int(is_current_filter_auto)),
                System.String(str(current_filter)),
                System.Int32(int(mid)),
                System.Single(float(delay_time)),
            )
        except Exception:
            try:
                import System
                err = self._elec.Start_Circle_Voltammetry_Multi_New(
                    System.Single(float(0.001)),
                    bool(is_use_initial_potential),
                    System.Single(float(initial_potential)),
                    System.Int32(int(initial_potential_vs_type)),
                    System.Single(float(top_potential1)),
                    System.Int32(int(top_potential1_vs_type)),
                    System.Single(float(top_potential2)),
                    System.Int32(int(top_potential2_vs_type)),
                    bool(is_use_finally_potential),
                    System.Single(float(finally_potential)),
                    System.Int32(int(finally_potential_vs_type)),
                    System.Single(float(scan_rate)),
                    System.Int32(int(cycle_count)),
                    System.Int32(int(is_voltage_rand_auto)),
                    System.String(str(voltage_rand)),
                    System.Int32(int(is_current_rand_auto)),
                    System.String(str(current_rand)),
                    System.Int32(int(is_voltage_filter_auto)),
                    System.String(str(voltage_filter)),
                    System.Int32(int(is_current_filter_auto)),
                    System.String(str(current_filter)),
                    System.Int32(int(mid)),
                    System.Single(float(delay_time)),
                )
            except Exception as exc2:
                err = str(exc2)
        except Exception as exc:
            err = str(exc)

        success = err is None or err == ""
        self.data.update({"status": "experimenting" if success else "error", "last_machine_id": mid})
        if success:
            self.data["last_result_type"] = "cyclic_voltammetry"
            self.data["last_experiment_start_ts"] = time.time()
            self.logger.info("Start_Circle_Voltammetry_Multi dispatched on machine %s", mid)
        else:
            self.logger.error("Start_Circle_Voltammetry_Multi failed on machine %s: %s", mid, err)
        return {"success": success, "return_info": err or "ok", "machine_id": mid}

    def start_chronopotentiometry_param(
        self,
        time_per_point: float = 0.1,
        continue_time: float = 10.0,
        current: float = 0.1,
        voltage_rand: str = "1000",
        is_current_rand_auto: int = 1,
        current_rand: str = "1000",
        is_voltage_filter_auto: int = 1,
        voltage_filter: str = "10Hz",
        is_current_filter_auto: int = 1,
        current_filter: str = "10Hz",
        machine_id: Optional[int] = None,
    ):
        self._ensure_driver()
        mid = int(self.machine_id if machine_id is None else machine_id)

        self.logger.info(
            "Start_ChronopotentiometryParam params: mid=%s, tpp=%s, ct=%s, curr=%s, vRand=%s, cAuto=%s, cRand=%s, vFiltAuto=%s, vFilt=%s, cFiltAuto=%s, cFilt=%s",
            mid,
            time_per_point,
            continue_time,
            current,
            voltage_rand,
            is_current_rand_auto,
            current_rand,
            is_voltage_filter_auto,
            voltage_filter,
            is_current_filter_auto,
            current_filter,
        )

        try:
            import System
            err = self._elec.Start_ChronopotentiometryParam(
                System.Single(float(time_per_point)),
                System.Single(float(continue_time)),
                System.Single(float(current)),
                System.String(str(voltage_rand)),
                System.Int32(int(is_current_rand_auto)),
                System.String(str(current_rand)),
                System.Int32(int(is_voltage_filter_auto)),
                System.String(str(voltage_filter)),
                System.Int32(int(is_current_filter_auto)),
                System.String(str(current_filter)),
                System.Int32(int(mid)),
            )
        except Exception as exc:
            err = str(exc)

        success = err is None or err == ""
        self.data.update({"status": "experimenting" if success else "error", "last_machine_id": mid})
        if success:
            self.data["last_result_type"] = "chrono_potentiometry"
            self.data["last_experiment_start_ts"] = time.time()
            self.logger.info("Start_ChronopotentiometryParam dispatched on machine %s", mid)
        else:
            self.logger.error("Start_ChronopotentiometryParam failed on machine %s: %s", mid, err)
        return {"success": success, "return_info": err or "ok", "machine_id": mid}

    def stop_experiment(self, machine_id: Optional[int] = None):
        self._ensure_driver()
        mid = int(self.machine_id if machine_id is None else machine_id)
        try:
            self._elec.StopExperiment(mid)
            self.data["status"] = "stopped"
            self.logger.info("Stopped experiment on machine %s", mid)
            return {"success": True, "machine_id": mid}
        except Exception as exc:
            self.logger.error("StopExperiment failed on machine %s: %s", mid, exc)
            return {"success": False, "machine_id": mid, "return_info": str(exc)}

    def export_open_circuit_data(self, machine_id: Optional[int] = None, dest_dir: Optional[str] = None):
        self._ensure_driver()
        mid = int(self.machine_id if machine_id is None else machine_id)
        base = Path(self.interface_dir) / "SourceData"
        if not base.exists():
            return {"success": False, "files": [], "reason": "no SourceData"}
        start_ts = float(self.data.get("last_experiment_start_ts", 0))
        date_dirs = [p for p in base.iterdir() if p.is_dir()]
        date_dirs.sort(key=lambda p: p.name, reverse=True)
        candidates = []
        for d in date_dirs:
            for root, dirs, files in os.walk(d):
                for fn in files:
                    if not fn.lower().endswith(".txt"):
                        continue
                    p = Path(root) / fn
                    name = fn
                    cond1 = (f"{mid}号机" in name) or ("开路电位" in name) or ("OpenCircuit" in name) or ("EnergyOpenCircuit" in name)
                    cond2 = start_ts == 0 or Path(p).stat().st_mtime >= start_ts
                    if cond1 and cond2:
                        candidates.append(p)
            if candidates:
                break
        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        if not candidates:
            for d in date_dirs:
                for root, dirs, files in os.walk(d):
                    for fn in files:
                        if not fn.lower().endswith(".txt"):
                            continue
                        p = Path(root) / fn
                        name = fn
                        cond1 = (f"{mid}号机" in name) or ("开路电位" in name) or ("OpenCircuit" in name) or ("EnergyOpenCircuit" in name)
                        if cond1:
                            candidates.append(p)
                if candidates:
                    break
            candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        if not candidates:
            return {"success": False, "files": []}
        out_dir_default = Path(__file__).resolve().parent / "exports" / "开路电位"
        out_dir = Path(dest_dir).expanduser() if dest_dir else out_dir_default
        out_dir.mkdir(parents=True, exist_ok=True)
        copied = []
        for p in candidates[:5]:
            to = out_dir / p.name
            try:
                data = Path(p).read_bytes()
                to.write_bytes(data)
                copied.append(str(to))
            except Exception:
                pass
        return {"success": True, "files": copied, "dest": str(out_dir)}

    def export_eis_data(self, machine_id: Optional[int] = None, dest_dir: Optional[str] = None):
        self._ensure_driver()
        mid = int(self.machine_id if machine_id is None else machine_id)
        base = Path(self.interface_dir) / "SourceData"
        if not base.exists():
            return {"success": False, "files": [], "reason": "no SourceData"}
        start_ts = float(self.data.get("last_experiment_start_ts", 0))
        date_dirs = [p for p in base.iterdir() if p.is_dir()]
        date_dirs.sort(key=lambda p: p.name, reverse=True)
        candidates = []
        for d in date_dirs:
            for root, dirs, files in os.walk(d):
                if "阻抗" not in root and "Impedance" not in root and "EIS" not in root:
                    continue
                for fn in files:
                    if not fn.lower().endswith(".txt"):
                        continue
                    p = Path(root) / fn
                    name = fn
                    cond1 = (f"{mid}号机" in name) or ("阻抗" in name) or ("Impedance" in name) or ("EIS" in name)
                    cond2 = start_ts == 0 or Path(p).stat().st_mtime >= start_ts
                    if cond1 and cond2:
                        candidates.append(p)
            if candidates:
                break
        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        if not candidates:
            for d in date_dirs:
                for root, dirs, files in os.walk(d):
                    if "阻抗" not in root and "Impedance" not in root and "EIS" not in root:
                        continue
                    for fn in files:
                        if not fn.lower().endswith(".txt"):
                            continue
                        p = Path(root) / fn
                        name = fn
                        cond1 = (f"{mid}号机" in name) or ("阻抗" in name) or ("Impedance" in name) or ("EIS" in name)
                        if cond1:
                            candidates.append(p)
                if candidates:
                    break
            candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        if not candidates:
            return {"success": False, "files": []}
        out_dir_default = Path(__file__).resolve().parent / "exports" / "阻抗"
        out_dir = Path(dest_dir).expanduser() if dest_dir else out_dir_default
        out_dir.mkdir(parents=True, exist_ok=True)
        copied = []
        for p in candidates[:5]:
            to = out_dir / p.name
            try:
                data = Path(p).read_bytes()
                to.write_bytes(data)
                copied.append(str(to))
            except Exception:
                pass
        return {"success": True, "files": copied, "dest": str(out_dir)}

    def export_gitt_data(self, machine_id: Optional[int] = None, dest_dir: Optional[str] = None):
        self._ensure_driver()
        mid = int(self.machine_id if machine_id is None else machine_id)
        base = Path(self.interface_dir) / "SourceData"
        if not base.exists():
            return {"success": False, "files": [], "reason": "no SourceData"}
        start_ts = float(self.data.get("last_experiment_start_ts", 0))
        date_dirs = [p for p in base.iterdir() if p.is_dir()]
        date_dirs.sort(key=lambda p: p.name, reverse=True)
        candidates = []
        for d in date_dirs:
            for root, dirs, files in os.walk(d):
                if ("恒电流间歇滴定" not in root and "恒电流间歇滴定法" not in root and "GITT" not in root):
                    continue
                for fn in files:
                    if not fn.lower().endswith(".txt"):
                        continue
                    p = Path(root) / fn
                    name = fn
                    cond1 = (f"{mid}号机" in name) or ("恒电流间歇滴定" in name) or ("恒电流间歇滴定法" in name) or ("恒电流间歇滴定法(GITT)" in name) or ("GITT" in name)
                    cond2 = start_ts == 0 or Path(p).stat().st_mtime >= start_ts
                    if cond1 and cond2:
                        candidates.append(p)
            if candidates:
                break
        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        if not candidates:
            for d in date_dirs:
                for root, dirs, files in os.walk(d):
                    if ("恒电流间歇滴定" not in root and "恒电流间歇滴定法" not in root and "GITT" not in root):
                        continue
                    for fn in files:
                        if not fn.lower().endswith(".txt"):
                            continue
                        p = Path(root) / fn
                        name = fn
                        cond1 = (f"{mid}号机" in name) or ("恒电流间歇滴定" in name) or ("恒电流间歇滴定法" in name) or ("恒电流间歇滴定法(GITT)" in name) or ("GITT" in name)
                        if cond1:
                            candidates.append(p)
                if candidates:
                    break
            candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        if not candidates:
            return {"success": False, "files": []}
        out_dir_default = Path(__file__).resolve().parent / "exports" / "恒电流间歇滴定法"
        out_dir = Path(dest_dir).expanduser() if dest_dir else out_dir_default
        out_dir.mkdir(parents=True, exist_ok=True)
        copied = []
        for p in candidates[:5]:
            to = out_dir / p.name
            try:
                data = Path(p).read_bytes()
                to.write_bytes(data)
                copied.append(str(to))
            except Exception:
                pass
        return {"success": True, "files": copied, "dest": str(out_dir)}

    def export_linear_scan_data(self, machine_id: Optional[int] = None, dest_dir: Optional[str] = None):
        self._ensure_driver()
        mid = int(self.machine_id if machine_id is None else machine_id)
        base = Path(self.interface_dir) / "SourceData"
        if not base.exists():
            return {"success": False, "files": [], "reason": "no SourceData"}
        start_ts = float(self.data.get("last_experiment_start_ts", 0))
        date_dirs = [p for p in base.iterdir() if p.is_dir()]
        date_dirs.sort(key=lambda p: p.name, reverse=True)
        candidates = []
        for d in date_dirs:
            for root, dirs, files in os.walk(d):
                if ("线性扫描" not in root and "线扫" not in root and "Linear" not in root and "LineScan" not in root and "Voltamm" not in root):
                    continue
                for fn in files:
                    if not fn.lower().endswith(".txt"):
                        continue
                    p = Path(root) / fn
                    name = fn
                    cond1 = (f"{mid}号机" in name) or ("线性扫描" in name) or ("线扫" in name) or ("Linear" in name) or ("LineScan" in name) or ("Voltamm" in name)
                    cond2 = start_ts == 0 or Path(p).stat().st_mtime >= start_ts
                    if cond1 and cond2:
                        candidates.append(p)
            if candidates:
                break
        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        if not candidates:
            for d in date_dirs:
                for root, dirs, files in os.walk(d):
                    if ("线性扫描" not in root and "线扫" not in root and "Linear" not in root and "LineScan" not in root and "Voltamm" not in root):
                        continue
                    for fn in files:
                        if not fn.lower().endswith(".txt"):
                            continue
                        p = Path(root) / fn
                        name = fn
                        cond1 = (f"{mid}号机" in name) or ("线性扫描" in name) or ("线扫" in name) or ("Linear" in name) or ("LineScan" in name) or ("Voltamm" in name)
                        if cond1:
                            candidates.append(p)
                if candidates:
                    break
            candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        if not candidates:
            return {"success": False, "files": []}
        out_dir_default = Path(__file__).resolve().parent / "exports" / "线性扫描"
        out_dir = Path(dest_dir).expanduser() if dest_dir else out_dir_default
        out_dir.mkdir(parents=True, exist_ok=True)
        copied = []
        candidates.sort(key=lambda p: Path(p).stat().st_mtime, reverse=True)
        for p in candidates[:1]:
            to = out_dir / p.name
            try:
                data = Path(p).read_bytes()
                to.write_bytes(data)
                copied.append(str(to))
            except Exception:
                pass
        return {"success": True, "files": copied, "dest": str(out_dir)}

    def export_cyclic_voltammetry_data(self, machine_id: Optional[int] = None, dest_dir: Optional[str] = None):
        self._ensure_driver()
        mid = int(self.machine_id if machine_id is None else machine_id)
        base = Path(self.interface_dir) / "SourceData"
        if not base.exists():
            return {"success": False, "files": [], "reason": "no SourceData"}
        start_ts = float(self.data.get("last_experiment_start_ts", 0))
        date_dirs = [p for p in base.iterdir() if p.is_dir()]
        date_dirs.sort(key=lambda p: p.name, reverse=True)
        candidates = []
        for d in date_dirs:
            for root, dirs, files in os.walk(d):
                if ("循环伏安" not in root and "循环" not in root and "Cyclic" not in root and "Circle" not in root and "Voltamm" not in root):
                    continue
                for fn in files:
                    if not fn.lower().endswith(".txt"):
                        continue
                    p = Path(root) / fn
                    name = fn
                    cond1 = (f"{mid}号机" in name) or ("循环伏安" in name) or ("循环" in name) or ("Cyclic" in name) or ("Circle" in name) or ("Voltamm" in name)
                    cond2 = start_ts == 0 or Path(p).stat().st_mtime >= start_ts
                    if cond1 and cond2:
                        candidates.append(p)
            if candidates:
                break
        if not candidates:
            return {"success": False, "files": []}
        out_dir_default = Path(__file__).resolve().parent / "exports" / "循环伏安"
        out_dir = Path(dest_dir).expanduser() if dest_dir else out_dir_default
        out_dir.mkdir(parents=True, exist_ok=True)
        copied = []
        candidates.sort(key=lambda p: Path(p).stat().st_mtime, reverse=True)
        for p in candidates[:1]:
            to = out_dir / p.name
            try:
                data = Path(p).read_bytes()
                to.write_bytes(data)
                copied.append(str(to))
            except Exception:
                pass
        return {"success": True, "files": copied, "dest": str(out_dir)}

    def export_chronopotentiometry_data(self, machine_id: Optional[int] = None, dest_dir: Optional[str] = None):
        self._ensure_driver()
        mid = int(self.machine_id if machine_id is None else machine_id)
        base = Path(self.interface_dir) / "SourceData"
        if not base.exists():
            return {"success": False, "files": [], "reason": "no SourceData"}
        start_ts = float(self.data.get("last_experiment_start_ts", 0))
        date_dirs = [p for p in base.iterdir() if p.is_dir()]
        date_dirs.sort(key=lambda p: p.name, reverse=True)
        candidates = []
        for d in date_dirs:
            for root, dirs, files in os.walk(d):
                if ("计时电位法" not in root and "Chrono" not in root and "Potent" not in root and "Potentiometry" not in root):
                    continue
                for fn in files:
                    if not fn.lower().endswith(".txt"):
                        continue
                    p = Path(root) / fn
                    name = fn
                    cond1 = (f"{mid}号机" in name) or ("计时电位法" in name) or ("Chrono" in name) or ("Potent" in name) or ("Potentiometry" in name)
                    cond2 = start_ts == 0 or Path(p).stat().st_mtime >= start_ts
                    if cond1 and cond2:
                        candidates.append(p)
            if candidates:
                break
        if not candidates:
            return {"success": False, "files": []}
        out_dir_default = Path(__file__).resolve().parent / "exports" / "计时电位法"
        out_dir = Path(dest_dir).expanduser() if dest_dir else out_dir_default
        out_dir.mkdir(parents=True, exist_ok=True)
        copied = []
        candidates.sort(key=lambda p: Path(p).stat().st_mtime, reverse=True)
        for p in candidates[:1]:
            to = out_dir / p.name
            try:
                data = Path(p).read_bytes()
                to.write_bytes(data)
                copied.append(str(to))
            except Exception:
                pass
        return {"success": True, "files": copied, "dest": str(out_dir)}

    def start_realtime_output(self, machine_id: Optional[int] = None, interval: float = 0.5, dest_dir: Optional[str] = None):
        self._ensure_driver()
        mid = int(self.machine_id if machine_id is None else machine_id)
        if mid in self._rt_threads and self._rt_threads[mid].is_alive():
            return {"success": True, "running": True, "machine_id": mid, "file": str(self._rt_files.get(mid, ""))}
        mode = str(self.data.get("last_result_type") or "")
        subdir = (
            "开路电位"
            if mode == "energy"
            else (
                "阻抗"
                if mode == "impedance"
                else (
                    "恒电流间歇滴定法"
                    if mode == "gitt"
                    else (
                        "线性扫描"
                        if mode == "linear_scan"
                        else (
                            "循环伏安"
                            if mode == "cyclic_voltammetry"
                            else ("计时电位法" if mode == "chrono_potentiometry" else "开路电位")
                        )
                    )
                )
            )
        )
        out_dir = Path(dest_dir).expanduser() if dest_dir else (Path(self.interface_dir) / "SourceData" / time.strftime("%Y-%m-%d") / subdir)
        out_dir.mkdir(parents=True, exist_ok=True)
        exp_name = (
            "开路电位"
            if subdir == "开路电位"
            else (
                "控制电位EIS"
                if subdir == "阻抗"
                else (
                    "恒电流间歇滴定法(GITT)"
                    if subdir == "恒电流间歇滴定法"
                    else (
                        "循环伏安(多循环)"
                        if subdir == "循环伏安"
                        else ("计时电位法" if subdir == "计时电位法" else "线性扫描(LSV)")
                    )
                )
            )
        )
        out_file = out_dir / (f"{mid}号机({exp_name}).txt")
        stop_event = threading.Event()
        self._rt_stop_flags[mid] = stop_event
        self._rt_files[mid] = out_file

        def _run():
            try:
                f = out_file.open("w", encoding="utf-8")
            except Exception:
                return
            last_type = None
            header_written = False
            def _fmt(val: float, width: int = 30) -> str:
                try:
                    return f"{val:>{width}.6f}"
                except Exception:
                    return f"{str(val):>{width}}"
            while not stop_event.is_set():
                try:
                    exping = True
                    if hasattr(self._elec, "IsExperimenting"):
                        try:
                            exping = bool(self._elec.IsExperimenting(mid))
                        except Exception:
                            exping = True
                    if not exping:
                        break
                    result_types = []
                    if hasattr(self._elec, "GetResultDataType"):
                        try:
                            result_types = list(self._elec.GetResultDataType(mid))
                        except Exception:
                            result_types = []
                    rt = None
                    s = None
                    samples = None
                    if last_type is not None:
                        try:
                            s_try = self._elec.GetData(last_type, mid)
                        except Exception:
                            s_try = None
                        if s_try is not None and not (isinstance(s_try, str) and s_try.strip() == "System.Single[]"):
                            s = s_try
                            rt = last_type
                            samples_try = None
                            try:
                                import System
                                if hasattr(self._elec, "SplitData"):
                                    if mode == "impedance":
                                        a0 = list(self._elec.SplitData(s_try, 7, 0)); a1 = list(self._elec.SplitData(s_try, 7, 1)); a2 = list(self._elec.SplitData(s_try, 7, 2)); a3 = list(self._elec.SplitData(s_try, 7, 3)); a4 = list(self._elec.SplitData(s_try, 7, 4)); a5 = list(self._elec.SplitData(s_try, 7, 5)); a6 = list(self._elec.SplitData(s_try, 7, 6)); samples_try = (a0, a1, a2, a3, a4, a5, a6)
                                    elif mode == "linear_scan":
                                        a0 = list(self._elec.SplitData(s_try, 4, 0)); a1 = list(self._elec.SplitData(s_try, 4, 1)); a2 = list(self._elec.SplitData(s_try, 4, 2)); _a3 = list(self._elec.SplitData(s_try, 4, 3)); samples_try = (a0, a1, a2)
                                    elif mode == "cyclic_voltammetry":
                                        a0 = list(self._elec.SplitData(s_try, 4, 0)); a1 = list(self._elec.SplitData(s_try, 4, 1)); a2 = list(self._elec.SplitData(s_try, 4, 2)); _a3 = list(self._elec.SplitData(s_try, 4, 3)); samples_try = (a0, a1, a2)
                                    elif mode == "chrono_potentiometry":
                                        a0 = list(self._elec.SplitData(s_try, 4, 0)); a1 = list(self._elec.SplitData(s_try, 4, 1)); a2 = list(self._elec.SplitData(s_try, 4, 2)); _a3 = list(self._elec.SplitData(s_try, 4, 3))
                                        samples_try = (a0, a1, a2)
                                    else:
                                        a0 = list(self._elec.SplitData(s_try, 7, 0)); a1 = list(self._elec.SplitData(s_try, 7, 1)); a2 = list(self._elec.SplitData(s_try, 7, 2)); a3 = list(self._elec.SplitData(s_try, 7, 3)); a4 = list(self._elec.SplitData(s_try, 7, 4)); a5 = list(self._elec.SplitData(s_try, 7, 5)); a6 = list(self._elec.SplitData(s_try, 7, 6)); samples_try = (a0, a1, a2, a3, a4, a5, a6)
                            except Exception:
                                samples_try = None
                            if samples_try is not None:
                                samples = samples_try
                    if samples is None and result_types:
                        for cand in result_types:
                            try:
                                if hasattr(self._elec, "GetData"):
                                    s_try = self._elec.GetData(cand, mid)
                                else:
                                    s_try = None
                            except Exception:
                                s_try = None
                            if s_try is None:
                                continue
                            if isinstance(s_try, str) and s_try.strip() == "System.Single[]":
                                continue
                            samples_try = None
                            try:
                                import System
                                if hasattr(self._elec, "SplitData"):
                                    if mode == "impedance":
                                        a0 = list(self._elec.SplitData(s_try, 7, 0))
                                        a1 = list(self._elec.SplitData(s_try, 7, 1))
                                        a2 = list(self._elec.SplitData(s_try, 7, 2))
                                        a3 = list(self._elec.SplitData(s_try, 7, 3))
                                        a4 = list(self._elec.SplitData(s_try, 7, 4))
                                        a5 = list(self._elec.SplitData(s_try, 7, 5))
                                        a6 = list(self._elec.SplitData(s_try, 7, 6))
                                        samples_try = (a0, a1, a2, a3, a4, a5, a6)
                                    elif mode == "linear_scan":
                                        a0 = list(self._elec.SplitData(s_try, 4, 0))
                                        a1 = list(self._elec.SplitData(s_try, 4, 1))
                                        a2 = list(self._elec.SplitData(s_try, 4, 2))
                                        _a3 = list(self._elec.SplitData(s_try, 4, 3))
                                        samples_try = (a0, a1, a2)
                                    elif mode == "cyclic_voltammetry":
                                        a0 = list(self._elec.SplitData(s_try, 4, 0))
                                        a1 = list(self._elec.SplitData(s_try, 4, 1))
                                        a2 = list(self._elec.SplitData(s_try, 4, 2))
                                        _a3 = list(self._elec.SplitData(s_try, 4, 3))
                                        def _is_monotonic(arr):
                                            try:
                                                return len(arr) > 1 and all(arr[i] <= arr[i+1] for i in range(len(arr)-1))
                                            except Exception:
                                                return False
                                        if _is_monotonic(a0):
                                            t_arr = a0; x1, x2 = a1, a2
                                        elif _is_monotonic(a1):
                                            t_arr = a1; x1, x2 = a0, a2
                                        elif _is_monotonic(a2):
                                            t_arr = a2; x1, x2 = a0, a1
                                        else:
                                            t_arr = a0; x1, x2 = a1, a2
                                        try:
                                            r1 = (max(x1) - min(x1)) if len(x1) > 0 else 0.0
                                            r2 = (max(x2) - min(x2)) if len(x2) > 0 else 0.0
                                        except Exception:
                                            r1, r2 = 0.0, 0.0
                                        e_arr, i_arr = (x1, x2) if r1 >= r2 else (x2, x1)
                                        samples_try = (t_arr, e_arr, i_arr)
                                    elif mode == "chrono_potentiometry":
                                        a0 = list(self._elec.SplitData(s_try, 4, 0))
                                        a1 = list(self._elec.SplitData(s_try, 4, 1))
                                        a2 = list(self._elec.SplitData(s_try, 4, 2))
                                        _a3 = list(self._elec.SplitData(s_try, 4, 3))
                                        samples_try = (a0, a1, a2)
                                    else:
                                        a0 = list(self._elec.SplitData(s_try, 7, 0))
                                        a1 = list(self._elec.SplitData(s_try, 7, 1))
                                        a2 = list(self._elec.SplitData(s_try, 7, 2))
                                        a3 = list(self._elec.SplitData(s_try, 7, 3))
                                        a4 = list(self._elec.SplitData(s_try, 7, 4))
                                        a5 = list(self._elec.SplitData(s_try, 7, 5))
                                        a6 = list(self._elec.SplitData(s_try, 7, 6))
                                        samples_try = (a0, a1, a2, a3, a4, a5, a6)
                            except Exception:
                                samples_try = None
                            if samples_try is None:
                                try:
                                    it_try = list(s_try)
                                    if mode == "impedance" and len(it_try) % 7 == 0:
                                        n = len(it_try) // 7
                                        a0 = [it_try[k * 7 + 0] for k in range(n)]
                                        a1 = [it_try[k * 7 + 1] for k in range(n)]
                                        a2 = [it_try[k * 7 + 2] for k in range(n)]
                                        a3 = [it_try[k * 7 + 3] for k in range(n)]
                                        a4 = [it_try[k * 7 + 4] for k in range(n)]
                                        a5 = [it_try[k * 7 + 5] for k in range(n)]
                                        a6 = [it_try[k * 7 + 6] for k in range(n)]
                                        samples_try = (a0, a1, a2, a3, a4, a5, a6)
                                    elif mode == "linear_scan" and len(it_try) % 4 == 0:
                                        n = len(it_try) // 4
                                        a0 = [it_try[k * 4 + 0] for k in range(n)]
                                        a1 = [it_try[k * 4 + 1] for k in range(n)]
                                        a2 = [it_try[k * 4 + 2] for k in range(n)]
                                        _a3 = [it_try[k * 4 + 3] for k in range(n)]
                                        samples_try = (a0, a1, a2)
                                    elif mode == "linear_scan" and len(it_try) % 3 == 0:
                                        n = len(it_try) // 3
                                        a0 = [it_try[k * 3 + 0] for k in range(n)]
                                        a1 = [it_try[k * 3 + 1] for k in range(n)]
                                        a2 = [it_try[k * 3 + 2] for k in range(n)]
                                        samples_try = (a0, a1, a2)
                                    elif mode == "cyclic_voltammetry" and len(it_try) % 4 == 0:
                                        n = len(it_try) // 4
                                        a0 = [it_try[k * 4 + 0] for k in range(n)]
                                        a1 = [it_try[k * 4 + 1] for k in range(n)]
                                        a2 = [it_try[k * 4 + 2] for k in range(n)]
                                        _a3 = [it_try[k * 4 + 3] for k in range(n)]
                                        def _is_monotonic(arr):
                                            try:
                                                return len(arr) > 1 and all(arr[i] <= arr[i+1] for i in range(len(arr)-1))
                                            except Exception:
                                                return False
                                        if _is_monotonic(a0):
                                            t_arr = a0; x1, x2 = a1, a2
                                        elif _is_monotonic(a1):
                                            t_arr = a1; x1, x2 = a0, a2
                                        elif _is_monotonic(a2):
                                            t_arr = a2; x1, x2 = a0, a1
                                        else:
                                            t_arr = a0; x1, x2 = a1, a2
                                        try:
                                            r1 = (max(x1) - min(x1)) if len(x1) > 0 else 0.0
                                            r2 = (max(x2) - min(x2)) if len(x2) > 0 else 0.0
                                        except Exception:
                                            r1, r2 = 0.0, 0.0
                                        e_arr, i_arr = (x1, x2) if r1 >= r2 else (x2, x1)
                                        samples_try = (t_arr, e_arr, i_arr)
                                    elif mode == "cyclic_voltammetry" and len(it_try) % 3 == 0:
                                        n = len(it_try) // 3
                                        a0 = [it_try[k * 3 + 0] for k in range(n)]
                                        a1 = [it_try[k * 3 + 1] for k in range(n)]
                                        a2 = [it_try[k * 3 + 2] for k in range(n)]
                                        def _is_monotonic(arr):
                                            try:
                                                return len(arr) > 1 and all(arr[i] <= arr[i+1] for i in range(len(arr)-1))
                                            except Exception:
                                                return False
                                        if _is_monotonic(a0):
                                            t_arr = a0; x1, x2 = a1, a2
                                        elif _is_monotonic(a1):
                                            t_arr = a1; x1, x2 = a0, a2
                                        elif _is_monotonic(a2):
                                            t_arr = a2; x1, x2 = a0, a1
                                        else:
                                            t_arr = a0; x1, x2 = a1, a2
                                        try:
                                            r1 = (max(x1) - min(x1)) if len(x1) > 0 else 0.0
                                            r2 = (max(x2) - min(x2)) if len(x2) > 0 else 0.0
                                        except Exception:
                                            r1, r2 = 0.0, 0.0
                                        e_arr, i_arr = (x1, x2) if r1 >= r2 else (x2, x1)
                                        samples_try = (t_arr, e_arr, i_arr)
                                    elif mode == "chrono_potentiometry" and len(it_try) % 4 == 0:
                                        n = len(it_try) // 4
                                        a0 = [it_try[k * 4 + 0] for k in range(n)]
                                        a1 = [it_try[k * 4 + 1] for k in range(n)]
                                        a2 = [it_try[k * 4 + 2] for k in range(n)]
                                        _a3 = [it_try[k * 4 + 3] for k in range(n)]
                                        samples_try = (a0, a1, a2)
                                    elif mode == "chrono_potentiometry" and len(it_try) % 3 == 0:
                                        n = len(it_try) // 3
                                        a0 = [it_try[k * 3 + 0] for k in range(n)]
                                        a1 = [it_try[k * 3 + 1] for k in range(n)]
                                        a2 = [it_try[k * 3 + 2] for k in range(n)]
                                        samples_try = (a0, a1, a2)
                                    elif len(it_try) % 7 == 0:
                                        n = len(it_try) // 7
                                        a0 = [it_try[k * 7 + 0] for k in range(n)]
                                        a1 = [it_try[k * 7 + 1] for k in range(n)]
                                        a2 = [it_try[k * 7 + 2] for k in range(n)]
                                        a3 = [it_try[k * 7 + 3] for k in range(n)]
                                        a4 = [it_try[k * 7 + 4] for k in range(n)]
                                        a5 = [it_try[k * 7 + 5] for k in range(n)]
                                        a6 = [it_try[k * 7 + 6] for k in range(n)]
                                        samples_try = (a0, a1, a2, a3, a4, a5, a6)
                                except Exception:
                                    samples_try = None
                            ok_try = False
                            if samples_try is not None:
                                try:
                                    t_try = samples_try[0]
                                    ok_try = len(t_try) > 0
                                    if ok_try and len(t_try) >= 2:
                                        ok_try = all(t_try[i] <= t_try[i+1] for i in range(len(t_try)-1))
                                except Exception:
                                    ok_try = False
                            if ok_try:
                                rt = cand
                                s = s_try
                                samples = samples_try
                                break
                    if rt is None and last_type is not None:
                        try:
                            s = self._elec.GetData(last_type, mid)
                        except Exception:
                            s = None
                        rt = last_type if s is not None else None
                    if rt is None or s is None:
                        time.sleep(interval)
                        continue
                    last_type = rt
                    if samples is None:
                        samples = None
                    try:
                        import System
                        if hasattr(self._elec, "SplitData"):
                            if mode == "impedance":
                                arr0 = list(self._elec.SplitData(s, 7, 0))
                                arr1 = list(self._elec.SplitData(s, 7, 1))
                                arr2 = list(self._elec.SplitData(s, 7, 2))
                                arr3 = list(self._elec.SplitData(s, 7, 3))
                                arr4 = list(self._elec.SplitData(s, 7, 4))
                                arr5 = list(self._elec.SplitData(s, 7, 5))
                                arr6 = list(self._elec.SplitData(s, 7, 6))
                                samples = (arr0, arr1, arr2, arr3, arr4, arr5, arr6)
                            elif mode == "linear_scan":
                                arr0 = list(self._elec.SplitData(s, 4, 0))
                                arr1 = list(self._elec.SplitData(s, 4, 1))
                                arr2 = list(self._elec.SplitData(s, 4, 2))
                                _arr3 = list(self._elec.SplitData(s, 4, 3))
                                samples = (arr0, arr1, arr2)
                            elif mode == "cyclic_voltammetry":
                                arr0 = list(self._elec.SplitData(s, 4, 0))
                                arr1 = list(self._elec.SplitData(s, 4, 1))
                                arr2 = list(self._elec.SplitData(s, 4, 2))
                                _arr3 = list(self._elec.SplitData(s, 4, 3))
                                def _is_monotonic(arr):
                                    try:
                                        return len(arr) > 1 and all(arr[i] <= arr[i+1] for i in range(len(arr)-1))
                                    except Exception:
                                        return False
                                if _is_monotonic(arr0):
                                    t_arr = arr0; x1, x2 = arr1, arr2
                                elif _is_monotonic(arr1):
                                    t_arr = arr1; x1, x2 = arr0, arr2
                                elif _is_monotonic(arr2):
                                    t_arr = arr2; x1, x2 = arr0, arr1
                                else:
                                    t_arr = arr0; x1, x2 = arr1, arr2
                                try:
                                    r1 = (max(x1) - min(x1)) if len(x1) > 0 else 0.0
                                    r2 = (max(x2) - min(x2)) if len(x2) > 0 else 0.0
                                except Exception:
                                    r1, r2 = 0.0, 0.0
                                e_arr, i_arr = (x1, x2) if r1 >= r2 else (x2, x1)
                                samples = (t_arr, e_arr, i_arr)
                            elif mode == "chrono_potentiometry":
                                arr0 = list(self._elec.SplitData(s, 4, 0))
                                arr1 = list(self._elec.SplitData(s, 4, 1))
                                arr2 = list(self._elec.SplitData(s, 4, 2))
                                _arr3 = list(self._elec.SplitData(s, 4, 3))
                                samples = (arr0, arr1, arr2)
                            else:
                                arr0 = list(self._elec.SplitData(s, 7, 0))
                                arr1 = list(self._elec.SplitData(s, 7, 1))
                                arr2 = list(self._elec.SplitData(s, 7, 2))
                                arr3 = list(self._elec.SplitData(s, 7, 3))
                                arr4 = list(self._elec.SplitData(s, 7, 4))
                                arr5 = list(self._elec.SplitData(s, 7, 5))
                                arr6 = list(self._elec.SplitData(s, 7, 6))
                                samples = (arr0, arr1, arr2, arr3, arr4, arr5, arr6)
                    except Exception:
                        samples = None
                    if samples is None:
                        try:
                            it = list(s)
                            if mode == "impedance" and len(it) % 7 == 0:
                                n = len(it) // 7
                                arr0 = [it[k * 7 + 0] for k in range(n)]
                                arr1 = [it[k * 7 + 1] for k in range(n)]
                                arr2 = [it[k * 7 + 2] for k in range(n)]
                                arr3 = [it[k * 7 + 3] for k in range(n)]
                                arr4 = [it[k * 7 + 4] for k in range(n)]
                                arr5 = [it[k * 7 + 5] for k in range(n)]
                                arr6 = [it[k * 7 + 6] for k in range(n)]
                                samples = (arr0, arr1, arr2, arr3, arr4, arr5, arr6)
                            elif mode == "linear_scan" and len(it) % 4 == 0:
                                n = len(it) // 4
                                arr0 = [it[k * 4 + 0] for k in range(n)]
                                arr1 = [it[k * 4 + 1] for k in range(n)]
                                arr2 = [it[k * 4 + 2] for k in range(n)]
                                _arr3 = [it[k * 4 + 3] for k in range(n)]
                                samples = (arr0, arr1, arr2)
                            elif mode == "linear_scan" and len(it) % 3 == 0:
                                n = len(it) // 3
                                arr0 = [it[k * 3 + 0] for k in range(n)]
                                arr1 = [it[k * 3 + 1] for k in range(n)]
                                arr2 = [it[k * 3 + 2] for k in range(n)]
                                samples = (arr0, arr1, arr2)
                            elif mode == "cyclic_voltammetry" and len(it) % 4 == 0:
                                n = len(it) // 4
                                arr0 = [it[k * 4 + 0] for k in range(n)]
                                arr1 = [it[k * 4 + 1] for k in range(n)]
                                arr2 = [it[k * 4 + 2] for k in range(n)]
                                _arr3 = [it[k * 4 + 3] for k in range(n)]
                                def _is_monotonic(arr):
                                    try:
                                        return len(arr) > 1 and all(arr[i] <= arr[i+1] for i in range(len(arr)-1))
                                    except Exception:
                                        return False
                                if _is_monotonic(arr0):
                                    t_arr = arr0; x1, x2 = arr1, arr2
                                elif _is_monotonic(arr1):
                                    t_arr = arr1; x1, x2 = arr0, arr2
                                elif _is_monotonic(arr2):
                                    t_arr = arr2; x1, x2 = arr0, arr1
                                else:
                                    t_arr = arr0; x1, x2 = arr1, arr2
                                try:
                                    r1 = (max(x1) - min(x1)) if len(x1) > 0 else 0.0
                                    r2 = (max(x2) - min(x2)) if len(x2) > 0 else 0.0
                                except Exception:
                                    r1, r2 = 0.0, 0.0
                                e_arr, i_arr = (x1, x2) if r1 >= r2 else (x2, x1)
                                samples = (t_arr, e_arr, i_arr)
                            elif mode == "cyclic_voltammetry" and len(it) % 3 == 0:
                                n = len(it) // 3
                                arr0 = [it[k * 3 + 0] for k in range(n)]
                                arr1 = [it[k * 3 + 1] for k in range(n)]
                                arr2 = [it[k * 3 + 2] for k in range(n)]
                                def _is_monotonic(arr):
                                    try:
                                        return len(arr) > 1 and all(arr[i] <= arr[i+1] for i in range(len(arr)-1))
                                    except Exception:
                                        return False
                                if _is_monotonic(arr0):
                                    t_arr = arr0; x1, x2 = arr1, arr2
                                elif _is_monotonic(arr1):
                                    t_arr = arr1; x1, x2 = arr0, arr2
                                elif _is_monotonic(arr2):
                                    t_arr = arr2; x1, x2 = arr0, arr1
                                else:
                                    t_arr = arr0; x1, x2 = arr1, arr2
                                try:
                                    r1 = (max(x1) - min(x1)) if len(x1) > 0 else 0.0
                                    r2 = (max(x2) - min(x2)) if len(x2) > 0 else 0.0
                                except Exception:
                                    r1, r2 = 0.0, 0.0
                                e_arr, i_arr = (x1, x2) if r1 >= r2 else (x2, x1)
                                samples = (t_arr, e_arr, i_arr)
                            elif mode == "chrono_potentiometry" and len(it) % 4 == 0:
                                n = len(it) // 4
                                arr0 = [it[k * 4 + 0] for k in range(n)]
                                arr1 = [it[k * 4 + 1] for k in range(n)]
                                arr2 = [it[k * 4 + 2] for k in range(n)]
                                _arr3 = [it[k * 4 + 3] for k in range(n)]
                                samples = (arr0, arr1, arr2)
                            elif mode == "chrono_potentiometry" and len(it) % 3 == 0:
                                n = len(it) // 3
                                arr0 = [it[k * 3 + 0] for k in range(n)]
                                arr1 = [it[k * 3 + 1] for k in range(n)]
                                arr2 = [it[k * 3 + 2] for k in range(n)]
                                samples = (arr0, arr1, arr2)
                            elif mode == "cyclic_voltammetry" and len(it) % 3 == 0:
                                n = len(it) // 3
                                arr0 = [it[k * 3 + 0] for k in range(n)]
                                arr1 = [it[k * 3 + 1] for k in range(n)]
                                arr2 = [it[k * 3 + 2] for k in range(n)]
                                def _is_monotonic(arr):
                                    try:
                                        return len(arr) > 1 and all(arr[i] <= arr[i+1] for i in range(len(arr)-1))
                                    except Exception:
                                        return False
                                if _is_monotonic(arr0):
                                    t_arr = arr0; x1, x2 = arr1, arr2
                                elif _is_monotonic(arr1):
                                    t_arr = arr1; x1, x2 = arr0, arr2
                                elif _is_monotonic(arr2):
                                    t_arr = arr2; x1, x2 = arr0, arr1
                                else:
                                    t_arr = arr0; x1, x2 = arr1, arr2
                                try:
                                    r1 = (max(x1) - min(x1)) if len(x1) > 0 else 0.0
                                    r2 = (max(x2) - min(x2)) if len(x2) > 0 else 0.0
                                except Exception:
                                    r1, r2 = 0.0, 0.0
                                e_arr, i_arr = (x1, x2) if r1 >= r2 else (x2, x1)
                                samples = (t_arr, e_arr, i_arr)
                            elif len(it) % 7 == 0:
                                n = len(it) // 7
                                arr0 = [it[k * 7 + 0] for k in range(n)]
                                arr1 = [it[k * 7 + 1] for k in range(n)]
                                arr2 = [it[k * 7 + 2] for k in range(n)]
                                arr3 = [it[k * 7 + 3] for k in range(n)]
                                arr4 = [it[k * 7 + 4] for k in range(n)]
                                arr5 = [it[k * 7 + 5] for k in range(n)]
                                arr6 = [it[k * 7 + 6] for k in range(n)]
                                samples = (arr0, arr1, arr2, arr3, arr4, arr5, arr6)
                        except Exception:
                            samples = None
                    if samples is None:
                        time.sleep(interval)
                        continue
                    if not header_written:
                        if mode == "impedance":
                            header = (
                                f"{'Time(s)':>30}" + f"{'Zre(Ω)':>30}" + f"{'Zim(Ω)':>30}" +
                                f"{'Z(Ω)':>30}" + f"{'Freq(Hz)':>30}" + f"{'Phase(°)':>30}" + f"{'EDC(V)':>30}"
                            )
                        elif mode == "linear_scan":
                            header = (
                                f"{'Time(s)':>30}" + f"{'E(mV)':>30}" + f"{'I(mA)':>30}"
                            )
                        elif mode == "cyclic_voltammetry":
                            header = (
                                f"{'Time(s)':>30}" + f"{'E(mV)':>30}" + f"{'I(mA)':>30}"
                            )
                        elif mode == "chrono_potentiometry":
                            header = (
                                f"{'Time(s)':>30}" + f"{'E(mV)':>30}" + f"{'I(mA)':>30}"
                            )
                        else:
                            header = (
                                f"{'Time(s)':>30}" + f"{'E(mV)':>30}" + f"{'I(mA)':>30}" +
                                f"{'Q(mC)':>30}" + f"{'Capacity(mAh)':>30}" + f"{'Energy(Wh)':>30}" + f"{'P(W)':>30}"
                            )
                        f.write(header + "\n")
                        f.flush()
                        header_written = True
                    if mode == "impedance":
                        t_arr, zre_arr, zim_arr, z_arr, f_arr, ph_arr, edc_arr = samples
                        count = min(len(t_arr), len(zre_arr), len(zim_arr), len(z_arr), len(f_arr), len(ph_arr), len(edc_arr))
                        for idx in range(count):
                            line = (
                                _fmt(t_arr[idx]) + _fmt(zre_arr[idx]) + _fmt(zim_arr[idx]) +
                                _fmt(z_arr[idx]) + _fmt(f_arr[idx]) + _fmt(ph_arr[idx]) + _fmt(edc_arr[idx])
                            )
                            f.write(line + "\n")
                    elif mode == "linear_scan":
                        t_arr, e_arr, i_arr = samples
                        count = min(len(t_arr), len(e_arr), len(i_arr))
                        for idx in range(count):
                            line = (
                                _fmt(t_arr[idx]) + _fmt(e_arr[idx]) + _fmt(i_arr[idx])
                            )
                            f.write(line + "\n")
                    elif mode == "cyclic_voltammetry":
                        t_arr, e_arr, i_arr = samples
                        count = min(len(t_arr), len(e_arr), len(i_arr))
                        for idx in range(count):
                            line = (
                                _fmt(t_arr[idx]) + _fmt(e_arr[idx]) + _fmt(i_arr[idx])
                            )
                            f.write(line + "\n")
                    elif mode == "chrono_potentiometry":
                        t_arr, e_arr, i_arr = samples
                        count = min(len(t_arr), len(e_arr), len(i_arr))
                        for idx in range(count):
                            line = (
                                _fmt(t_arr[idx]) + _fmt(e_arr[idx]) + _fmt(i_arr[idx])
                            )
                            f.write(line + "\n")
                    else:
                        t_arr, e_arr, i_arr, q_arr, cap_arr, en_arr, p_arr = samples
                        count = min(len(t_arr), len(e_arr), len(i_arr), len(q_arr), len(cap_arr), len(en_arr), len(p_arr))
                        for idx in range(count):
                            line = (
                                _fmt(t_arr[idx]) + _fmt(e_arr[idx]) + _fmt(i_arr[idx]) +
                                _fmt(q_arr[idx]) + _fmt(cap_arr[idx]) + _fmt(en_arr[idx]) + _fmt(p_arr[idx])
                            )
                            f.write(line + "\n")
                    f.flush()
                except Exception:
                    time.sleep(interval)
            try:
                f.close()
            except Exception:
                pass

        th = threading.Thread(target=_run, daemon=True)
        self._rt_threads[mid] = th
        th.start()
        return {"success": True, "running": True, "machine_id": mid, "file": str(out_file)}

    def stop_realtime_output(self, machine_id: Optional[int] = None):
        mid = int(self.machine_id if machine_id is None else machine_id)
        ev = self._rt_stop_flags.get(mid)
        th = self._rt_threads.get(mid)
        if ev:
            ev.set()
        ok = True
        if th and th.is_alive():
            th.join(timeout=2.0)
        return {"success": ok, "machine_id": mid, "file": str(self._rt_files.get(mid, ""))}

    def test_open_circuit_energy(
        self,
        machine_id: Optional[int] = None,
        interval: float = 0.5,
        output_dir: Optional[str] = None,
        stop_after: bool = True,
        time_per_point: float = 0.1,
        continue_time: float = 120.0,
        is_use_excursion_rate: bool = False,
        excursion_rate: float = 0.0,
        is_voltage_trig: bool = True,
        voltage_or_current_trig_direction: int = 0,
        voltage_or_current_trig_value: float = 0.0,
        capacity_trig_direction: int = 0,
        capacity_trig_value: float = 0.0,
        is_use_resolution: bool = False,
        resolution: float = 10.0,
        is_use_delta_i: bool = False,
        delta_i: float = 0.0,
        is_use_delta_q: bool = False,
        delta_q: float = 0.0,
        is_voltage_rand_auto: int = 0,
        voltage_rand: str = "1000",
        is_current_rand_auto: int = 0,
        current_rand: str = "1000",
    ):
        res = self.start_open_circuit_energy(
            time_per_point=time_per_point,
            continue_time=continue_time,
            is_use_excursion_rate=is_use_excursion_rate,
            excursion_rate=excursion_rate,
            is_voltage_trig=is_voltage_trig,
            voltage_or_current_trig_direction=voltage_or_current_trig_direction,
            voltage_or_current_trig_value=voltage_or_current_trig_value,
            capacity_trig_direction=capacity_trig_direction,
            capacity_trig_value=capacity_trig_value,
            is_use_resolution=is_use_resolution,
            resolution=resolution,
            is_use_delta_i=is_use_delta_i,
            delta_i=delta_i,
            is_use_delta_q=is_use_delta_q,
            delta_q=delta_q,
            is_voltage_rand_auto=is_voltage_rand_auto,
            voltage_rand=voltage_rand,
            is_current_rand_auto=is_current_rand_auto,
            current_rand=current_rand,
            machine_id=machine_id,
        )
        if not bool(res.get("success")):
            return {"success": False, "machine_id": int(self.machine_id if machine_id is None else machine_id), "return_info": str(res.get("return_info"))}
        mid = int(self.machine_id if machine_id is None else machine_id)
        rt = self.start_realtime_output(machine_id=mid, interval=interval)
        while True:
            done = False
            if hasattr(self._elec, "IsExperimenting"):
                try:
                    done = not bool(self._elec.IsExperimenting(mid))
                except Exception:
                    done = False
            if done:
                break
            time.sleep(0.5)
        st = self.stop_realtime_output(machine_id=mid)
        exp = self.export_open_circuit_data(machine_id=mid, dest_dir=output_dir)
        if bool(stop_after):
            try:
                self.stop_experiment(machine_id=mid)
            except Exception:
                pass
        ok = bool(res.get("success")) and bool(rt.get("success")) and bool(exp.get("success"))
        return {"success": ok, "machine_id": mid, "return_info": str(res.get("return_info", "")), "realtime_file": str(st.get("file", "")), "export_files": exp.get("files", []), "export_dest": str(exp.get("dest", ""))}

    def test_eis(
        self,
        is_sync_start: bool = True,
        start_freq: float = 10000.0,
        end_freq: float = 0.1,
        amplitude: float = 0.01,
        interval_type: int = 1,
        point_count: int = 10,
        voltage: float = 0.0,
        voltage_vs_type: int = 0,
        is_voltage_rand_auto: int = 0,
        voltage_rand: str = "10000",
        is_current_rand_auto: int = 1,
        current_rand: str = "1000",
        is_voltage_filter_auto: int = 1,
        voltage_filter: str = "10Hz",
        is_current_filter_auto: int = 1,
        current_filter: str = "10Hz",
        machine_id: Optional[int] = None,
        delay_time: float = 0.0,
        data_quality: int = 1,
        interval: float = 0.5,
        output_dir: Optional[str] = None,
        wait_seconds: float = 10.0,
        stop_after: bool = True,
    ):
        res = self.start_eis(
            is_sync_start=is_sync_start,
            start_freq=start_freq,
            end_freq=end_freq,
            amplitude=amplitude,
            interval_type=interval_type,
            point_count=point_count,
            voltage=voltage,
            voltage_vs_type=voltage_vs_type,
            is_voltage_rand_auto=is_voltage_rand_auto,
            voltage_rand=voltage_rand,
            is_current_rand_auto=is_current_rand_auto,
            current_rand=current_rand,
            is_voltage_filter_auto=is_voltage_filter_auto,
            voltage_filter=voltage_filter,
            is_current_filter_auto=is_current_filter_auto,
            current_filter=current_filter,
            machine_id=machine_id,
            delay_time=delay_time,
            data_quality=data_quality,
        )
        if not bool(res.get("success")):
            return {"success": False, "machine_id": int(self.machine_id if machine_id is None else machine_id), "return_info": str(res.get("return_info"))}
        mid = int(self.machine_id if machine_id is None else machine_id)
        rt = self.start_realtime_output(machine_id=mid, interval=interval)
        while True:
            done = False
            if hasattr(self._elec, "IsExperimenting"):
                try:
                    done = not bool(self._elec.IsExperimenting(mid))
                except Exception:
                    done = False
            if done:
                break
            time.sleep(0.5)
        st = self.stop_realtime_output(machine_id=mid)
        exp = self.export_eis_data(machine_id=mid, dest_dir=output_dir)
        if bool(stop_after):
            try:
                self.stop_experiment(machine_id=mid)
            except Exception:
                pass
        ok = bool(res.get("success")) and bool(rt.get("success")) and bool(exp.get("success"))
        return {"success": ok, "machine_id": mid, "return_info": str(res.get("return_info", "")), "realtime_file": str(st.get("file", "")), "export_files": exp.get("files", []), "export_dest": str(exp.get("dest", ""))}

    def test_gitt(
        self,
        machine_id: Optional[int] = None,
        current: float = 1.0,
        is_voltage_trig: bool = True,
        voltage_or_current_trig_direction: int = 0,
        voltage_or_current_trig_value: float = 0.0,
        capacity_trig_direction: int = 0,
        capacity_trig_value: float = 0.0,
        time_per_point_cc: float = 0.1,
        continue_time_cc: float = 60.0,
        is_use_resolution: bool = False,
        resolution: float = 10.0,
        is_use_delta_i: bool = False,
        delta_i: float = 0.0,
        is_use_delta_q: bool = False,
        delta_q: float = 0.0,
        is_voltage_rand_auto_cc: int = 0,
        voltage_rand_cc: str = "10000",
        is_current_rand_auto_cc: int = 1,
        current_rand_cc: str = "1000",
        is_use_excursion_rate: bool = False,
        excursion_rate: float = 0.0,
        time_per_point_oc: float = 0.1,
        continue_time_oc: float = 60.0,
        is_voltage_rand_auto_oc: int = 0,
        voltage_rand_oc: str = "1000",
        is_current_rand_auto_oc: int = 0,
        current_rand_oc: str = "1000",
        interval: float = 0.5,
        output_dir: Optional[str] = None,
        wait_seconds: float = 10.0,
        stop_after: bool = True,
    ):
        res = self.start_gitt(
            machine_id=machine_id,
            current=current,
            is_voltage_trig=is_voltage_trig,
            voltage_or_current_trig_direction=voltage_or_current_trig_direction,
            voltage_or_current_trig_value=voltage_or_current_trig_value,
            capacity_trig_direction=capacity_trig_direction,
            capacity_trig_value=capacity_trig_value,
            time_per_point_cc=time_per_point_cc,
            continue_time_cc=continue_time_cc,
            is_use_resolution=is_use_resolution,
            resolution=resolution,
            is_use_delta_i=is_use_delta_i,
            delta_i=delta_i,
            is_use_delta_q=is_use_delta_q,
            delta_q=delta_q,
            is_voltage_rand_auto_cc=is_voltage_rand_auto_cc,
            voltage_rand_cc=voltage_rand_cc,
            is_current_rand_auto_cc=is_current_rand_auto_cc,
            current_rand_cc=current_rand_cc,
            is_use_excursion_rate=is_use_excursion_rate,
            excursion_rate=excursion_rate,
            time_per_point_oc=time_per_point_oc,
            continue_time_oc=continue_time_oc,
            is_voltage_rand_auto_oc=is_voltage_rand_auto_oc,
            voltage_rand_oc=voltage_rand_oc,
            is_current_rand_auto_oc=is_current_rand_auto_oc,
            current_rand_oc=current_rand_oc,
        )
        if not bool(res.get("success")):
            return {"success": False, "machine_id": int(self.machine_id if machine_id is None else machine_id), "return_info": str(res.get("return_info"))}
        mid = int(self.machine_id if machine_id is None else machine_id)
        rt = self.start_realtime_output(machine_id=mid, interval=interval)
        while True:
            done = False
            if hasattr(self._elec, "IsExperimenting"):
                try:
                    done = not bool(self._elec.IsExperimenting(mid))
                except Exception:
                    done = False
            if done:
                break
            time.sleep(0.5)
        st = self.stop_realtime_output(machine_id=mid)
        exp = self.export_gitt_data(machine_id=mid, dest_dir=output_dir)
        if bool(stop_after):
            try:
                self.stop_experiment(machine_id=mid)
            except Exception:
                pass
        ok = bool(res.get("success")) and bool(rt.get("success")) and bool(exp.get("success"))
        return {"success": ok, "machine_id": mid, "return_info": str(res.get("return_info", "")), "realtime_file": str(st.get("file", "")), "export_files": exp.get("files", []), "export_dest": str(exp.get("dest", ""))}

    def test_linear_scan_voltammetry(
        self,
        start_voltage: float = 0.0,
        start_voltage_vs_type: int = 0,
        end_voltage: float = 1.0,
        end_voltage_vs_type: int = 0,
        scan_rate: float = 0.01,
        point_count: int = 100,
        is_voltage_rand_auto: int = 0,
        voltage_rand: str = "1000",
        is_current_rand_auto: int = 1,
        current_rand: str = "1000",
        is_voltage_filter_auto: int = 1,
        voltage_filter: str = "10Hz",
        is_current_filter_auto: int = 1,
        current_filter: str = "10Hz",
        machine_id: Optional[int] = None,
        delay_time: float = 0.0,
        interval: float = 0.5,
        output_dir: Optional[str] = None,
        wait_seconds: float = 10.0,
        stop_after: bool = True,
    ):
        res = self.start_linear_scan_voltammetry(
            start_voltage=start_voltage,
            start_voltage_vs_type=start_voltage_vs_type,
            end_voltage=end_voltage,
            end_voltage_vs_type=end_voltage_vs_type,
            scan_rate=scan_rate,
            point_count=point_count,
            is_voltage_rand_auto=is_voltage_rand_auto,
            voltage_rand=voltage_rand,
            is_current_rand_auto=is_current_rand_auto,
            current_rand=current_rand,
            is_voltage_filter_auto=is_voltage_filter_auto,
            voltage_filter=voltage_filter,
            is_current_filter_auto=is_current_filter_auto,
            current_filter=current_filter,
            machine_id=machine_id,
            delay_time=delay_time,
        )
        if not bool(res.get("success")):
            return {"success": False, "machine_id": int(self.machine_id if machine_id is None else machine_id), "return_info": str(res.get("return_info"))}
        mid = int(self.machine_id if machine_id is None else machine_id)
        rt = self.start_realtime_output(machine_id=mid, interval=interval)
        while True:
            done = False
            if hasattr(self._elec, "IsExperimenting"):
                try:
                    done = not bool(self._elec.IsExperimenting(mid))
                except Exception:
                    done = False
            if done:
                break
            time.sleep(0.5)
        st = self.stop_realtime_output(machine_id=mid)
        exp = self.export_linear_scan_data(machine_id=mid, dest_dir=output_dir)
        if bool(stop_after):
            try:
                self.stop_experiment(machine_id=mid)
            except Exception:
                pass
        ok = bool(res.get("success")) and bool(rt.get("success")) and bool(exp.get("success"))
        return {"success": ok, "machine_id": mid, "return_info": str(res.get("return_info", "")), "realtime_file": str(st.get("file", "")), "export_files": exp.get("files", []), "export_dest": str(exp.get("dest", ""))}

    def test_cyclic_voltammetry_multi(
        self,
        is_use_initial_potential: bool = True,
        initial_potential: float = -1.0,
        initial_potential_vs_type: int = 0,
        top_potential1: float = 1.0,
        top_potential1_vs_type: int = 0,
        top_potential2: float = -2.0,
        top_potential2_vs_type: int = 0,
        is_use_finally_potential: bool = True,
        finally_potential: float = -1.0,
        finally_potential_vs_type: int = 0,
        scan_rate: float = 0.2,
        cycle_count: int = 2,
        is_voltage_rand_auto: int = 1,
        voltage_rand: str = "1000",
        is_current_rand_auto: int = 1,
        current_rand: str = "1000",
        is_voltage_filter_auto: int = 1,
        voltage_filter: str = "10Hz",
        is_current_filter_auto: int = 1,
        current_filter: str = "10Hz",
        machine_id: Optional[int] = None,
        delay_time: float = 0.0,
        interval: float = 0.5,
        output_dir: Optional[str] = None,
        wait_seconds: float = 10.0,
        stop_after: bool = True,
    ):
        res = self.start_cyclic_voltammetry_multi(
            is_use_initial_potential=is_use_initial_potential,
            initial_potential=initial_potential,
            initial_potential_vs_type=initial_potential_vs_type,
            top_potential1=top_potential1,
            top_potential1_vs_type=top_potential1_vs_type,
            top_potential2=top_potential2,
            top_potential2_vs_type=top_potential2_vs_type,
            is_use_finally_potential=is_use_finally_potential,
            finally_potential=finally_potential,
            finally_potential_vs_type=finally_potential_vs_type,
            scan_rate=scan_rate,
            cycle_count=cycle_count,
            is_voltage_rand_auto=is_voltage_rand_auto,
            voltage_rand=voltage_rand,
            is_current_rand_auto=is_current_rand_auto,
            current_rand=current_rand,
            is_voltage_filter_auto=is_voltage_filter_auto,
            voltage_filter=voltage_filter,
            is_current_filter_auto=is_current_filter_auto,
            current_filter=current_filter,
            machine_id=machine_id,
            delay_time=delay_time,
        )
        if not bool(res.get("success")):
            return {"success": False, "machine_id": int(self.machine_id if machine_id is None else machine_id), "return_info": str(res.get("return_info"))}
        mid = int(self.machine_id if machine_id is None else machine_id)
        rt = self.start_realtime_output(machine_id=mid, interval=interval)
        while True:
            done = False
            if hasattr(self._elec, "IsExperimenting"):
                try:
                    done = not bool(self._elec.IsExperimenting(mid))
                except Exception:
                    done = False
            if done:
                break
            time.sleep(0.5)
        st = self.stop_realtime_output(machine_id=mid)
        exp = self.export_cyclic_voltammetry_data(machine_id=mid, dest_dir=output_dir)
        if bool(stop_after):
            try:
                self.stop_experiment(machine_id=mid)
            except Exception:
                pass
        ok = bool(res.get("success")) and bool(rt.get("success")) and bool(exp.get("success"))
        return {"success": ok, "machine_id": mid, "return_info": str(res.get("return_info", "")), "realtime_file": str(st.get("file", "")), "export_files": exp.get("files", []), "export_dest": str(exp.get("dest", ""))}

    def test_chronopotentiometry_param(
        self,
        time_per_point: float = 0.1,
        continue_time: float = 10.0,
        current: float = 0.1,
        voltage_rand: str = "1000",
        is_current_rand_auto: int = 1,
        current_rand: str = "1000",
        is_voltage_filter_auto: int = 1,
        voltage_filter: str = "10Hz",
        is_current_filter_auto: int = 1,
        current_filter: str = "10Hz",
        machine_id: Optional[int] = None,
        interval: float = 0.5,
        output_dir: Optional[str] = None,
        wait_seconds: float = 10.0,
        stop_after: bool = True,
    ):
        res = self.start_chronopotentiometry_param(
            time_per_point=time_per_point,
            continue_time=continue_time,
            current=current,
            voltage_rand=voltage_rand,
            is_current_rand_auto=is_current_rand_auto,
            current_rand=current_rand,
            is_voltage_filter_auto=is_voltage_filter_auto,
            voltage_filter=voltage_filter,
            is_current_filter_auto=is_current_filter_auto,
            current_filter=current_filter,
            machine_id=machine_id,
        )
        if not bool(res.get("success")):
            return {"success": False, "machine_id": int(self.machine_id if machine_id is None else machine_id), "return_info": str(res.get("return_info"))}
        mid = int(self.machine_id if machine_id is None else machine_id)
        rt = self.start_realtime_output(machine_id=mid, interval=interval)
        while True:
            done = False
            if hasattr(self._elec, "IsExperimenting"):
                try:
                    done = not bool(self._elec.IsExperimenting(mid))
                except Exception:
                    done = False
            if done:
                break
            time.sleep(0.5)
        st = self.stop_realtime_output(machine_id=mid)
        exp = self.export_chronopotentiometry_data(machine_id=mid, dest_dir=output_dir)
        if bool(stop_after):
            try:
                self.stop_experiment(machine_id=mid)
            except Exception:
                pass
        ok = bool(res.get("success")) and bool(rt.get("success")) and bool(exp.get("success"))
        return {"success": ok, "machine_id": mid, "return_info": str(res.get("return_info", "")), "realtime_file": str(st.get("file", "")), "export_files": exp.get("files", []), "export_dest": str(exp.get("dest", ""))}
