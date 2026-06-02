from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


DEFAULT_ROBOARM_URDF = (
    "/home/ubuntu/canonical/Uni-Lab-OS/robot_assets/roboarm_chem_04/urdf/roboarm_chem_04_query.urdf"
)
DEFAULT_TABLE_USD = (
    "/home/ubuntu/Matterix/source/matterix_assets/data/infrastructure/tables/"
    "table-thorlabs-75x90/table.usda"
)
DEFAULT_HOTPLATE_USD = (
    "/home/ubuntu/Matterix/source/matterix_assets/data/instruments/"
    "hotplate_start_button/hotplate_start_button.usda"
)
DEFAULT_BEAKER_USD = (
    "/home/ubuntu/Matterix/source/matterix_assets/data/labware/beaker500ml/beaker-500ml.usda"
)


Vector3 = tuple[float, float, float]


@dataclass(frozen=True)
class Placement:
    key: str
    label: str
    prim_path: str
    asset_kind: str
    asset_path: str | None
    translation: Vector3
    rotation_xyz: Vector3 = (0.0, 0.0, 0.0)
    scale: Vector3 = (1.0, 1.0, 1.0)
    color: Vector3 | None = None
    note: str = ""


@dataclass(frozen=True)
class CameraSpec:
    prim_path: str
    translation: Vector3
    rotation_xyz: Vector3
    focal_length: float = 28.0


@dataclass(frozen=True)
class IsaacLabLayout:
    name: str
    display_name: str
    meters_per_unit: float
    up_axis: str
    placements: tuple[Placement, ...]
    camera: CameraSpec
    query_targets: dict[str, str]
    notes: tuple[str, ...] = ()


def central_island_layout(
    *,
    robot_urdf: str = DEFAULT_ROBOARM_URDF,
    table_usd: str = DEFAULT_TABLE_USD,
    hotplate_usd: str = DEFAULT_HOTPLATE_USD,
    beaker_usd: str = DEFAULT_BEAKER_USD,
) -> IsaacLabLayout:
    """返回 RoboArm Chem 04 中央机械臂岛布局。"""
    placements = (
        Placement(
            key="table",
            label="Thorlabs 75x90 主桌",
            prim_path="/World/Lab/ThorlabsTable",
            asset_kind="usd",
            asset_path=table_usd,
            translation=(0.0, 0.0, 0.0),
            note="主桌中心对齐世界原点。",
        ),
        Placement(
            key="robot",
            label="RoboArm Chem 04",
            prim_path="/World/Lab/RoboArmChem04",
            asset_kind="urdf",
            asset_path=robot_urdf,
            translation=(0.0, 0.0, 0.82),
            note="机械臂底座放在主桌中央，后续可基于 URDF 实测高度微调。",
        ),
        Placement(
            key="hotplate",
            label="Hotplate",
            prim_path="/World/Lab/Hotplate",
            asset_kind="usd",
            asset_path=hotplate_usd,
            translation=(-0.26, 0.12, 0.86),
            scale=(1.0, 1.0, 1.0),
            note="左前反应加热区。",
        ),
        Placement(
            key="beaker",
            label="Beaker 500ml",
            prim_path="/World/Lab/Beaker500ml",
            asset_kind="usd",
            asset_path=beaker_usd,
            translation=(-0.24, -0.08, 0.88),
            scale=(1.0, 1.0, 1.0),
            note="烧杯放在左前侧，便于 query pose 和渲染验收。",
        ),
        Placement(
            key="reagent_tray",
            label="Reagent Tray",
            prim_path="/World/Lab/ReagentTray",
            asset_kind="marker",
            asset_path=None,
            translation=(0.28, 0.12, 0.865),
            scale=(0.18, 0.12, 0.025),
            color=(0.22, 0.58, 0.32),
            note="试剂/耗材暂用可视 marker，等实际资产确定后替换。",
        ),
        Placement(
            key="instrument_slot",
            label="Instrument Slot",
            prim_path="/World/Lab/InstrumentSlot",
            asset_kind="marker",
            asset_path=None,
            translation=(0.28, -0.08, 0.865),
            scale=(0.20, 0.12, 0.025),
            color=(0.82, 0.55, 0.18),
            note="右侧仪器占位，后续替换为泵、阀、天平或光谱仪 USD。",
        ),
        Placement(
            key="transfer_deck",
            label="Transfer Deck",
            prim_path="/World/Lab/TransferDeck",
            asset_kind="marker",
            asset_path=None,
            translation=(0.0, -0.28, 0.865),
            scale=(0.22, 0.10, 0.02),
            color=(0.06, 0.45, 0.43),
            note="前侧转运区，适合录屏时展示目标点和后续放置动作。",
        ),
    )
    return IsaacLabLayout(
        name="roboarm_chem_04_central_island",
        display_name="中央机械臂岛",
        meters_per_unit=1.0,
        up_axis="Z",
        placements=placements,
        camera=CameraSpec(
            prim_path="/World/Camera",
            translation=(1.35, -1.65, 1.65),
            rotation_xyz=(60.0, 0.0, 39.0),
        ),
        query_targets={
            "robot": "/World/Lab/RoboArmChem04",
            "table": "/World/Lab/ThorlabsTable",
            "hotplate": "/World/Lab/Hotplate",
            "beaker": "/World/Lab/Beaker500ml",
            "transfer_deck": "/World/Lab/TransferDeck",
        },
        notes=(
            "首版优先展示真实 PNG 渲染、query 物理态和 Uni-Lab-OS 到 Isaac worker 的闭环。",
            "试剂托盘、仪器位、转运区先用 marker 占位，便于录屏说明和后续替换真实 USD。",
        ),
    )


def layout_to_manifest(layout: IsaacLabLayout, *, output_stage: str) -> dict[str, Any]:
    return {
        "layout": layout.name,
        "display_name": layout.display_name,
        "output_stage": str(output_stage),
        "meters_per_unit": layout.meters_per_unit,
        "up_axis": layout.up_axis,
        "camera": _camera_to_dict(layout.camera),
        "query_targets": dict(layout.query_targets),
        "placements": [_placement_to_dict(placement) for placement in layout.placements],
        "notes": list(layout.notes),
    }


def validate_layout_assets(
    layout: IsaacLabLayout,
    *,
    exists: Callable[[str], bool] | None = None,
) -> list[str]:
    exists = exists or (lambda path: Path(path).exists())
    missing: list[str] = []
    for placement in layout.placements:
        if placement.asset_kind == "marker":
            continue
        if not placement.asset_path or not exists(placement.asset_path):
            missing.append(f"{placement.key}: {placement.asset_path}")
    return missing


def render_builder_script(layout: IsaacLabLayout, *, default_output_stage: str) -> str:
    manifest = layout_to_manifest(layout, output_stage=default_output_stage)
    manifest_json = json.dumps(manifest, indent=2, ensure_ascii=False)
    return f'''#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

LAYOUT = json.loads(r"""{manifest_json}""")


def _kit_exec_requested() -> bool:
    return os.environ.get("UNILABOS_ISAAC_KIT_EXEC") == "1" or "--kit-exec" in sys.argv


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the RoboArm Chem 04 central island Isaac stage")
    parser.add_argument("--out", default=LAYOUT["output_stage"])
    parser.add_argument("--headless", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--kit-exec", action="store_true", help="run inside an already started Isaac Kit --exec session")
    parser.add_argument("--warmup-steps", type=int, default=8)
    return parser.parse_args()


class _KitExecAppAdapter:
    def update(self):
        import omni.kit.app

        omni.kit.app.get_app().update()


def _vec3(values):
    from pxr import Gf

    return Gf.Vec3d(float(values[0]), float(values[1]), float(values[2]))


def _resolve_package_urdf(source: Path) -> Path:
    text = source.read_text(encoding="utf-8")
    if "package://" not in text:
        return source
    package_root = source.parents[1]
    package_name = package_root.name
    resolved = text.replace(f"package://{{package_name}}/", str(package_root) + "/")
    resolved = resolved.replace("package://", str(package_root) + "/")
    out = Path("/tmp/roboarm_chem_04_resolved.urdf")
    out.write_text(resolved, encoding="utf-8")
    return out


def _set_xform(prim, placement):
    from pxr import Gf, UsdGeom

    xform = UsdGeom.Xformable(prim)
    xform.ClearXformOpOrder()
    xform.AddTranslateOp(UsdGeom.XformOp.PrecisionDouble).Set(_vec3(placement["translation"]))
    xform.AddRotateXYZOp(UsdGeom.XformOp.PrecisionDouble).Set(Gf.Vec3d(*[float(v) for v in placement["rotation_xyz"]]))
    xform.AddScaleOp(UsdGeom.XformOp.PrecisionDouble).Set(Gf.Vec3d(*[float(v) for v in placement["scale"]]))


def _define_usd_reference(stage, placement):
    from pxr import UsdGeom

    prim = UsdGeom.Xform.Define(stage, placement["prim_path"]).GetPrim()
    prim.GetReferences().AddReference(placement["asset_path"])
    _set_xform(prim, placement)
    return prim


def _define_marker(stage, placement):
    from pxr import Gf, UsdGeom

    prim = UsdGeom.Cube.Define(stage, placement["prim_path"]).GetPrim()
    UsdGeom.Cube(prim).CreateSizeAttr(1.0)
    color = placement.get("color") or [0.2, 0.6, 0.55]
    UsdGeom.Gprim(prim).CreateDisplayColorAttr([Gf.Vec3f(*[float(v) for v in color])])
    _set_xform(prim, placement)
    return prim


def _enable_urdf_extension(sim_app):
    try:
        try:
            from isaacsim.core.utils.extensions import enable_extension
        except Exception:
            from omni.isaac.core.utils.extensions import enable_extension

        enable_extension("isaacsim.asset.importer.urdf")
        sim_app.update()
    except Exception as exc:
        print(f"[lab layout] could not enable URDF importer: {{exc}}", flush=True)


def _move_imported_prim(stage, imported_path: str | None, target_path: str) -> str | None:
    if not imported_path or imported_path == target_path:
        return imported_path
    prim = stage.GetPrimAtPath(str(imported_path))
    if not prim or not prim.IsValid():
        return imported_path
    try:
        import omni.kit.commands

        omni.kit.commands.execute("MovePrim", path_from=str(imported_path), path_to=str(target_path))
        moved = stage.GetPrimAtPath(target_path)
        if moved and moved.IsValid():
            return target_path
    except Exception as exc:
        print(f"[lab layout] imported URDF prim move skipped: {{exc}}", flush=True)
    return imported_path


def _import_urdf(stage, placement, sim_app):
    import omni.kit.commands

    _enable_urdf_extension(sim_app)
    source = Path(placement["asset_path"]).expanduser()
    if not source.exists():
        raise FileNotFoundError(f"URDF asset not found: {{source}}")
    resolved = _resolve_package_urdf(source)
    status, import_config = omni.kit.commands.execute("URDFCreateImportConfig")
    if not status:
        raise RuntimeError("URDFCreateImportConfig failed")
    for name, value in {{
        "merge_fixed_joints": False,
        "fix_base": True,
        "make_default_prim": False,
        "self_collision": False,
        "import_inertia_tensor": True,
        "create_physics_scene": True,
    }}.items():
        method = f"set_{{name}}"
        try:
            if hasattr(import_config, method):
                getattr(import_config, method)(value)
            elif hasattr(import_config, name):
                setattr(import_config, name, value)
        except Exception:
            pass
    kwargs = {{
        "urdf_path": str(resolved),
        "import_config": import_config,
    }}
    imported_path = None
    result = omni.kit.commands.execute("URDFParseAndImportFile", **kwargs)
    sim_app.update()
    if isinstance(result, tuple):
        status = bool(result[0])
        if len(result) > 1:
            imported_path = str(result[1])
    else:
        status = bool(result)
        if isinstance(result, str):
            imported_path = result
    if not status:
        raise RuntimeError(f"URDFParseAndImportFile failed for {{placement['asset_path']}}")
    _move_imported_prim(stage, imported_path, placement["prim_path"])
    prim = stage.GetPrimAtPath(placement["prim_path"])
    if not prim or not prim.IsValid():
        prim = stage.DefinePrim(placement["prim_path"], "Xform")
    _set_xform(prim, placement)
    return prim


def _define_camera(stage, camera):
    from pxr import Gf, UsdGeom

    camera_prim = UsdGeom.Camera.Define(stage, "/World/Camera")
    camera_prim.GetFocalLengthAttr().Set(float(camera["focal_length"]))
    prim = camera_prim.GetPrim()
    xform = UsdGeom.Xformable(prim)
    xform.ClearXformOpOrder()
    xform.AddTranslateOp().Set(_vec3(camera["translation"]))
    xform.AddRotateXYZOp().Set(Gf.Vec3f(*[float(v) for v in camera["rotation_xyz"]]))
    return prim


def _define_lighting(stage):
    from pxr import UsdLux

    dome = UsdLux.DomeLight.Define(stage, "/World/Lights/Dome")
    dome.CreateIntensityAttr(450.0)
    distant = UsdLux.DistantLight.Define(stage, "/World/Lights/Key")
    distant.CreateIntensityAttr(650.0)
    distant.CreateAngleAttr(0.35)


def build_stage(output_stage: str, sim_app) -> dict:
    from pxr import UsdGeom
    import omni.usd

    output = Path(output_stage)
    output.parent.mkdir(parents=True, exist_ok=True)
    ctx = omni.usd.get_context()
    ctx.new_stage()
    sim_app.update()
    stage = ctx.get_stage()
    if stage is None:
        raise RuntimeError("Isaac USD context did not create a stage")
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, float(LAYOUT["meters_per_unit"]))
    world = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(world.GetPrim())
    UsdGeom.Xform.Define(stage, "/World/Lab")
    UsdGeom.Xform.Define(stage, "/World/Lights")
    _define_lighting(stage)
    _define_camera(stage, LAYOUT["camera"])
    for placement in LAYOUT["placements"]:
        if placement["asset_kind"] == "usd":
            _define_usd_reference(stage, placement)
        elif placement["asset_kind"] == "urdf":
            _import_urdf(stage, placement, sim_app)
        elif placement["asset_kind"] == "marker":
            _define_marker(stage, placement)
        else:
            raise ValueError(f"unknown asset kind: {{placement['asset_kind']}}")
    stage.GetRootLayer().Export(str(output))
    try:
        stage.GetRootLayer().Save()
    except Exception:
        pass
    return {{
        "stage": str(output),
        "camera": LAYOUT["camera"]["prim_path"],
        "query_targets": LAYOUT["query_targets"],
    }}


def main() -> int:
    args = parse_args()
    if args.kit_exec or _kit_exec_requested():
        app = _KitExecAppAdapter()
        exit_code = 0
        try:
            result = build_stage(args.out, app)
            for _ in range(max(0, int(args.warmup_steps))):
                app.update()
            print(json.dumps(result, ensure_ascii=False), flush=True)
        except Exception:
            import traceback

            exit_code = 1
            traceback.print_exc()
        finally:
            try:
                import omni.kit.app

                omni.kit.app.get_app().post_quit()
            except Exception:
                pass
        return exit_code

    from isaacsim import SimulationApp

    app = SimulationApp({{"headless": bool(args.headless)}})
    try:
        result = build_stage(args.out, app)
        for _ in range(max(0, int(args.warmup_steps))):
            app.update()
        print(json.dumps(result, ensure_ascii=False), flush=True)
    finally:
        app.close()
    return 0


if __name__ == "__main__":
    if _kit_exec_requested():
        main()
    else:
        raise SystemExit(main())
'''


def _placement_to_dict(placement: Placement) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "key": placement.key,
        "label": placement.label,
        "prim_path": placement.prim_path,
        "asset_kind": placement.asset_kind,
        "asset_path": placement.asset_path,
        "translation": list(placement.translation),
        "rotation_xyz": list(placement.rotation_xyz),
        "scale": list(placement.scale),
        "note": placement.note,
    }
    if placement.color is not None:
        payload["color"] = list(placement.color)
    return payload


def _camera_to_dict(camera: CameraSpec) -> dict[str, Any]:
    return {
        "prim_path": camera.prim_path,
        "translation": list(camera.translation),
        "rotation_xyz": list(camera.rotation_xyz),
        "focal_length": camera.focal_length,
    }
