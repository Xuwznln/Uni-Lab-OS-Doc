"""LLM 技能文档测试：用真实 LLM 验证模糊用户输入 → 结构化意图的翻译质量。

需要 ANTHROPIC_API_KEY 环境变量。无 key 时自动跳过。
测试覆盖：设备名模糊匹配、工作流顺序推理、约束类型选择、JSON 格式正确性。
"""
import json
import os

import pytest

HAS_API_KEY = bool(os.environ.get("ANTHROPIC_API_KEY"))
pytestmark = pytest.mark.skipif(not HAS_API_KEY, reason="ANTHROPIC_API_KEY not set")

# 读取技能文档
_SKILL_DOC_PATH = os.path.join(
    os.path.dirname(__file__), "..", "llm_skill", "layout_intent_translator.md"
)

# PCR 场景设备列表（模拟用户场景中已有的设备）
SCENE_DEVICE_LIST = """\
Devices in scene:
- thermo_orbitor_rs2_hotel: Thermo Orbitor RS2 Hotel (type: static, bbox: 0.68×0.52m)
- arm_slider: Arm Slider (type: articulation, bbox: 1.20×0.30m)
- opentrons_liquid_handler: Opentrons Liquid Handler (type: static, bbox: 0.65×0.60m)
- agilent_plateloc: Agilent PlateLoc (type: static, bbox: 0.35×0.40m)
- inheco_odtc_96xl: Inheco ODTC 96XL (type: static, bbox: 0.30×0.35m)
"""

VALID_DEVICE_IDS = {
    "thermo_orbitor_rs2_hotel",
    "arm_slider",
    "opentrons_liquid_handler",
    "agilent_plateloc",
    "inheco_odtc_96xl",
}

VALID_INTENT_TYPES = {
    "reachable_by", "close_together", "far_apart", "max_distance",
    "min_distance", "min_spacing", "workflow_hint",
    "face_outward", "face_inward", "align_cardinal",
}


def _call_llm(user_message: str) -> dict:
    """调用 LLM，使用技能文档作为 system prompt，返回解析后的 JSON。"""
    import anthropic

    with open(_SKILL_DOC_PATH) as f:
        skill_doc = f.read()

    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        system=skill_doc,
        messages=[
            {"role": "user", "content": f"{SCENE_DEVICE_LIST}\n\n{user_message}"},
        ],
    )

    # 从 response 中提取 JSON
    text = response.content[0].text
    # LLM 可能返回 ```json ... ``` 包裹的 JSON
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0]
    elif "```" in text:
        text = text.split("```")[1].split("```")[0]

    return json.loads(text.strip())


def _extract_all_device_ids(intents: list[dict]) -> set[str]:
    """从意图列表中提取所有引用的设备 ID。"""
    ids = set()
    for intent in intents:
        params = intent.get("params", {})
        if "arm" in params:
            ids.add(params["arm"])
        for key in ("targets", "devices"):
            if key in params:
                ids.update(params[key])
        for key in ("device_a", "device_b"):
            if key in params:
                ids.add(params[key])
    return ids


class TestLLMFuzzyDeviceResolution:
    """测试 LLM 能否将模糊设备名映射到精确 ID。"""

    def test_pcr_machine_resolves_to_inheco(self):
        """'PCR machine' 应解析为 inheco_odtc_96xl。"""
        result = _call_llm(
            "Keep the PCR machine close to the plate sealer"
        )
        intents = result["intents"]
        all_ids = _extract_all_device_ids(intents)
        assert "inheco_odtc_96xl" in all_ids, f"Expected inheco_odtc_96xl in {all_ids}"
        assert "agilent_plateloc" in all_ids, f"Expected agilent_plateloc in {all_ids}"

    def test_robot_resolves_to_articulation_type(self):
        """'the robot' / 'robot arm' 应解析为 arm_slider（唯一 articulation 类型）。"""
        result = _call_llm(
            "The robot should be able to reach the liquid handler and the storage hotel"
        )
        intents = result["intents"]
        all_ids = _extract_all_device_ids(intents)
        assert "arm_slider" in all_ids, f"Expected arm_slider in {all_ids}"
        assert "opentrons_liquid_handler" in all_ids
        assert "thermo_orbitor_rs2_hotel" in all_ids

    def test_all_resolved_ids_are_valid(self):
        """LLM 输出的所有设备 ID 必须来自场景设备列表。"""
        result = _call_llm(
            "Take plate from hotel, prepare sample in the pipetting robot, "
            "seal it, then run thermal cycling. The arm handles all transfers."
        )
        intents = result["intents"]
        all_ids = _extract_all_device_ids(intents)
        invalid = all_ids - VALID_DEVICE_IDS
        assert not invalid, f"LLM produced invalid device IDs: {invalid}"


class TestLLMWorkflowInterpretation:
    """测试 LLM 对工作流描述的理解和翻译。"""

    def test_pcr_workflow_full(self):
        """完整 PCR 工作流描述应生成 reachable_by + workflow_hint + close_together。"""
        result = _call_llm(
            "I need to set up a PCR workflow: take plate from the hotel, "
            "prepare the sample in the liquid handler, seal the plate, "
            "then run the thermal cycler. The robot arm handles all plate transfers. "
            "Keep the liquid handler and sealer close together."
        )
        intents = result["intents"]
        intent_types = {i["intent"] for i in intents}

        # 应包含核心意图类型
        assert "reachable_by" in intent_types, f"Missing reachable_by in {intent_types}"
        assert "workflow_hint" in intent_types, f"Missing workflow_hint in {intent_types}"

        # reachable_by 应包含所有工作流设备作为 targets
        reach_intents = [i for i in intents if i["intent"] == "reachable_by"]
        assert len(reach_intents) >= 1
        reach_targets = set()
        for ri in reach_intents:
            reach_targets.update(ri["params"].get("targets", []))
        # 至少液体处理器和热循环仪应在可达范围内
        assert "opentrons_liquid_handler" in reach_targets
        assert "inheco_odtc_96xl" in reach_targets

    def test_workflow_device_order(self):
        """workflow_hint 的设备顺序应反映工作流步骤。"""
        result = _call_llm(
            "PCR process: first the hotel dispenses a plate, then the opentrons "
            "prepares the sample, next the plateloc seals it, finally the thermal "
            "cycler runs PCR. Generate a workflow hint."
        )
        intents = result["intents"]
        wf_intents = [i for i in intents if i["intent"] == "workflow_hint"]
        assert len(wf_intents) >= 1, f"No workflow_hint found in {[i['intent'] for i in intents]}"

        devices = wf_intents[0]["params"]["devices"]
        # 验证顺序：hotel → liquid_handler → plateloc → thermal_cycler
        hotel_idx = devices.index("thermo_orbitor_rs2_hotel")
        lh_idx = devices.index("opentrons_liquid_handler")
        seal_idx = devices.index("agilent_plateloc")
        tc_idx = devices.index("inheco_odtc_96xl")
        assert hotel_idx < lh_idx < seal_idx < tc_idx, (
            f"Wrong workflow order: {devices}"
        )


class TestLLMOutputFormat:
    """测试 LLM 输出格式的正确性。"""

    def test_output_has_intents_array(self):
        """输出必须有 intents 数组。"""
        result = _call_llm("Keep all devices at least 30cm apart")
        assert "intents" in result
        assert isinstance(result["intents"], list)
        assert len(result["intents"]) > 0

    def test_each_intent_has_required_fields(self):
        """每个意图必须有 intent、params、description。"""
        result = _call_llm(
            "The robot arm should reach the liquid handler. "
            "Keep the thermal cycler away from the plate hotel."
        )
        for intent in result["intents"]:
            assert "intent" in intent, f"Missing 'intent' field: {intent}"
            assert "params" in intent, f"Missing 'params' field: {intent}"
            assert "description" in intent, f"Missing 'description' field: {intent}"

    def test_intent_types_are_valid(self):
        """所有意图类型必须是已知类型。"""
        result = _call_llm(
            "Set up a compact PCR line: hotel → liquid handler → sealer → thermal cycler. "
            "Robot arm handles transfers. Align everything neatly."
        )
        for intent in result["intents"]:
            assert intent["intent"] in VALID_INTENT_TYPES, (
                f"Unknown intent type: {intent['intent']}"
            )


class TestLLMInterpretThenOptimize:
    """端到端：LLM 翻译 → /interpret → /optimize → 验证布局。"""

    def test_llm_output_accepted_by_interpret_endpoint(self):
        """LLM 输出应能直接被 /interpret 端点接受。"""
        from fastapi.testclient import TestClient

        from ..server import app

        test_client = TestClient(app)

        llm_result = _call_llm(
            "Take plate from hotel, prepare sample in opentrons, "
            "seal plate then pcr cycle, arm_slider handles all transfers. "
            "Keep liquid handler and sealer close."
        )

        # /interpret 应接受 LLM 输出
        resp = test_client.post("/interpret", json=llm_result)
        assert resp.status_code == 200, f"Interpret failed: {resp.text}"
        data = resp.json()
        assert len(data["constraints"]) > 0, "No constraints generated"
        assert len(data["errors"]) == 0, f"Interpretation errors: {data['errors']}"

    def test_full_pipeline_llm_to_placement(self):
        """LLM 翻译 → interpret → optimize → 所有设备有 placement。"""
        from fastapi.testclient import TestClient

        from ..server import app

        test_client = TestClient(app)

        # Stage 1: LLM 翻译
        llm_result = _call_llm(
            "I want a PCR workflow lab. Take plate from the hotel, pipette in the "
            "liquid handler, seal with the plateloc, then thermal cycle. "
            "The robot arm does all transfers between devices. "
            "Minimum 15cm gap between everything."
        )

        # Stage 2: interpret
        interpret_resp = test_client.post("/interpret", json=llm_result)
        assert interpret_resp.status_code == 200
        interpret_data = interpret_resp.json()
        assert len(interpret_data["errors"]) == 0

        # Stage 3: optimize
        pcr_devices = [
            {"id": "thermo_orbitor_rs2_hotel", "name": "Plate Hotel", "device_type": "static"},
            {"id": "arm_slider", "name": "Robot Arm", "device_type": "articulation"},
            {"id": "opentrons_liquid_handler", "name": "Liquid Handler", "device_type": "static"},
            {"id": "agilent_plateloc", "name": "Plate Sealer", "device_type": "static"},
            {"id": "inheco_odtc_96xl", "name": "Thermal Cycler", "device_type": "static"},
        ]
        optimize_resp = test_client.post("/optimize", json={
            "devices": pcr_devices,
            "lab": {"width": 6.0, "depth": 4.0},
            "constraints": interpret_data["constraints"],
            "workflow_edges": interpret_data.get("workflow_edges", []),
            "run_de": True,
            "maxiter": 50,
            "seed": 42,
        })
        assert optimize_resp.status_code == 200
        data = optimize_resp.json()

        # Stage 4: 验证所有设备都有 placement
        placed_ids = {p["device_id"] for p in data["placements"]}
        expected_ids = {d["id"] for d in pcr_devices}
        assert placed_ids == expected_ids
        assert data["success"] is True
