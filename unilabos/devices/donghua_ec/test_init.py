import os
import sys
import asyncio
import logging
from pathlib import Path

# Ensure repository is on PYTHONPATH
repo_root = Path(__file__).resolve().parents[3]
sys.path.append(str(repo_root))

from unilabos.devices.donghua_ec.donghua_ec import DonghuaEC

logging.basicConfig(level=logging.INFO)

async def main():
    iface_dir = Path(__file__).resolve().parent / "x64release" / "DHInterface"
    drv = DonghuaEC(config={"interface_dir": str(iface_dir)})
    ok = await drv.initialize()
    res = drv.get_machine_ids()
    import time as _t
    out = Path(__file__).resolve().parent / "init_output.txt"
    try:
        if out.exists():
            out.unlink()
    except Exception:
        pass
    with open(out, "w", encoding="utf-8") as f:
        f.write(f"initialize: {ok}\n")
        f.write(f"get_machine_ids: {res}\n")
        f.write("version: reflect_linear_scan_signature\n")
        try:
            import System
            elec = drv._elec
            t = elec.GetType()
            sig_lines = []
            for m in t.GetMethods():
                name = m.Name
                if ("Linear" in name) or ("Scan" in name) or ("Volt" in name) or ("Chrono" in name) or ("Potent" in name):
                    ps_types = [str(p.ParameterType.FullName) for p in m.GetParameters()]
                    ps_names = [str(p.Name) for p in m.GetParameters()]
                    sig_lines.append(name + ":" + str(ps_types) + " | names=" + str(ps_names))
            f.write("linear_scan_related_methods:\n")
            for ln in sig_lines:
                f.write(ln + "\n")
            # dump all Start_* signatures for debugging
            all_start = []
            for m in t.GetMethods():
                name = m.Name
                if name.startswith("Start_"):
                    ps_types = [str(p.ParameterType.FullName) for p in m.GetParameters()]
                    ps_names = [str(p.Name) for p in m.GetParameters()]
                    all_start.append(name + ":" + str(ps_types) + " | names=" + str(ps_names))
            f.write("all_start_methods:\n")
            for ln in all_start:
                f.write(ln + "\n")
        except Exception as e:
            f.write(f"reflect_error: {e}\n")
        f.write("version: lsv_v1\n")
    with open(out, "a", encoding="utf-8") as f:
        f.write("version: lsv_v1\n")
    # Cyclic Voltammetry (Multi)
    cv_params = dict(
        is_use_initial_potential=True,
        initial_potential=-1.0,
        initial_potential_vs_type=0,
        top_potential1=1.0,
        top_potential1_vs_type=0,
        top_potential2=-2.0,
        top_potential2_vs_type=0,
        is_use_finally_potential=True,
        finally_potential=-1.0,
        finally_potential_vs_type=0,
        scan_rate=0.2,
        cycle_count=2,
        is_voltage_rand_auto=1,
        voltage_rand="1000",
        is_current_rand_auto=1,
        current_rand="1000",
        is_voltage_filter_auto=1,
        voltage_filter="10Hz",
        is_current_filter_auto=1,
        current_filter="10Hz",
        machine_id=2,
    )
    with open(out, "a", encoding="utf-8") as f:
        f.write(f"start_cv_params: {cv_params}\n")
    try:
        start_cv = drv.start_cyclic_voltammetry_multi(**cv_params)
    except Exception as e:
        start_cv = {"success": False, "return_info": str(e), "machine_id": 2}
    try:
        if not start_cv.get("success"):
            drv.data["last_result_type"] = "cyclic_voltammetry"
    except Exception:
        pass
    with open(out, "a", encoding="utf-8") as f:
        f.write(f"start_cv: {start_cv}\n")
    try:
        rt4 = drv.start_realtime_output(machine_id=2, interval=0.5)
    except Exception as e:
        rt4 = {"success": False, "error": str(e)}
    with open(out, "a", encoding="utf-8") as f:
        f.write(f"start_realtime_output_cv: {rt4}\n")
    try:
        import System
        elec = drv._elec
        rts = []
        try:
            rts = list(elec.GetResultDataType(2))
        except Exception:
            rts = []
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"result_types: {rts}\n")
        print("result_types:", rts)
        details = []
        for cand in rts:
            try:
                s = elec.GetData(cand, 2)
            except Exception:
                s = None
            d = {"cand": cand, "len": 0, "d3": (), "d4": (), "d7": ()}
            try:
                it = list(s)
                d["len"] = len(it)
            except Exception:
                pass
            try:
                a0 = list(elec.SplitData(s, 3, 0)); a1 = list(elec.SplitData(s, 3, 1)); a2 = list(elec.SplitData(s, 3, 2))
                d["d3"] = (len(a0), len(a1), len(a2))
            except Exception:
                pass
            try:
                b0 = list(elec.SplitData(s, 4, 0)); b1 = list(elec.SplitData(s, 4, 1)); b2 = list(elec.SplitData(s, 4, 2)); b3 = list(elec.SplitData(s, 4, 3))
                d["d4"] = (len(b0), len(b1), len(b2), len(b3))
            except Exception:
                pass
            try:
                c0 = list(elec.SplitData(s, 7, 0)); c1 = list(elec.SplitData(s, 7, 1)); c2 = list(elec.SplitData(s, 7, 2)); c3 = list(elec.SplitData(s, 7, 3)); c4 = list(elec.SplitData(s, 7, 4)); c5 = list(elec.SplitData(s, 7, 5)); c6 = list(elec.SplitData(s, 7, 6))
                d["d7"] = (len(c0), len(c1), len(c2), len(c3), len(c4), len(c5), len(c6))
            except Exception:
                pass
            details.append(d)
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"data_details: {details}\n")
        print("data_details:", details)
    except Exception:
        pass
    try:
        import time as _t
        _t.sleep(10.0)
    except Exception:
        pass
    try:
        stop_rt4 = drv.stop_realtime_output(machine_id=2)
    except Exception as e:
        stop_rt4 = {"success": False, "error": str(e)}
    with open(out, "a", encoding="utf-8") as f:
        f.write(f"stop_realtime_output_cv: {stop_rt4}\n")
    try:
        exp_cv = drv.export_cyclic_voltammetry_data(machine_id=2, dest_dir=str(Path(__file__).resolve().parent / "exports" / "循环伏安"))
    except Exception as e:
        exp_cv = {"success": False, "error": str(e)}
    with open(out, "a", encoding="utf-8") as f:
        f.write(f"export_cyclic_voltammetry: {exp_cv}\n")
    try:
        stop_cv = drv.stop_experiment(machine_id=2)
    except Exception as e:
        stop_cv = {"success": False, "error": str(e)}
    with open(out, "a", encoding="utf-8") as f:
        f.write(f"stop_experiment_cv: {stop_cv}\n")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print("error:", e)
