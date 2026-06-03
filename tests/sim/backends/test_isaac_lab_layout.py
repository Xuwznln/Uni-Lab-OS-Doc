from __future__ import annotations

import json

from unilabos.sim.backends.isaac.lab_layout import (
    central_island_layout,
    layout_to_manifest,
    render_builder_script,
)


def test_central_island_layout_uses_roboarm_and_matterix_assets():
    layout = central_island_layout()

    assert layout.name == "roboarm_chem_04_central_island"
    assert layout.query_targets["robot"] == "/World/Lab/RoboArmChem04"
    assert layout.query_targets["hotplate"] == "/World/Lab/Hotplate"
    assert layout.query_targets["beaker"] == "/World/Lab/Beaker500ml"
    assert layout.camera.prim_path == "/World/Camera"

    placements = {placement.key: placement for placement in layout.placements}
    assert placements["robot"].asset_kind == "urdf"
    assert placements["robot"].asset_path.endswith("/roboarm_chem_04_query.urdf")
    assert placements["table"].asset_path.endswith("/table-thorlabs-75x90/table.usda")
    assert placements["hotplate"].asset_path.endswith("/hotplate_start_button/hotplate_start_button.usda")
    assert placements["beaker"].asset_path.endswith("/beaker500ml/beaker-500ml.usda")
    assert placements["transfer_deck"].asset_kind == "marker"
    assert placements["robot"].translation == (0.0, 0.0, 0.82)


def test_layout_manifest_is_json_serializable_and_keeps_query_targets():
    layout = central_island_layout()
    manifest = layout_to_manifest(layout, output_stage="/tmp/roboarm_lab_a.usda")

    encoded = json.dumps(manifest, ensure_ascii=False)

    assert "中央机械臂岛" in encoded
    assert manifest["output_stage"] == "/tmp/roboarm_lab_a.usda"
    assert manifest["camera"]["prim_path"] == "/World/Camera"
    assert manifest["query_targets"]["robot"] == "/World/Lab/RoboArmChem04"
    assert len(manifest["placements"]) >= 6


def test_render_builder_script_contains_isaac_stage_camera_and_urdf_import():
    layout = central_island_layout()
    script = render_builder_script(layout, default_output_stage="/tmp/roboarm_lab_a.usda")

    header = script.split("def parse_args", 1)[0]
    assert "LAYOUT = json.loads" in header
    assert "SimulationApp" in script
    assert "URDFCreateImportConfig" in script
    assert "URDFParseAndImportFile" in script
    assert '"dest_path": placement["prim_path"]' not in script
    assert 'enable_extension("isaacsim.asset.importer.urdf")' in script
    assert "sim_app.update()" in script
    assert 'parser.add_argument("--kit-exec"' in script
    assert 'os.environ.get("UNILABOS_ISAAC_KIT_EXEC")' in script
    assert "os._exit" not in script
    assert "def _kit_exec_requested()" in script
    assert "if _kit_exec_requested():\n        main()\n    else:\n        raise SystemExit(main())" in script
    assert "post_quit()" in script
    assert "/World/Lab/RoboArmChem04" in script
    assert "/World/Lab/ThorlabsTable" in script
    assert "/World/Lab/Hotplate" in script
    assert "/World/Lab/Beaker500ml" in script
    assert 'UsdGeom.Camera.Define(stage, "/World/Camera")' in script
    assert "ctx.new_stage()" in script
    assert "UsdGeom.XformOp.PrecisionDouble" in script
    assert "stage.GetRootLayer().Export(str(output))" in script
    assert 'stage.GetRootLayer().Save()' in script
