"""已安装宿主与外部插件使用同一套冻结 r3 正反例，算法选择允许不同。"""

import json
import sys
from copy import deepcopy
from uuid import uuid4

from unilabos.scheduling.protocol import CONTRACT_DIR, decode_message, encode_message
from unilabos.scheduling.startup import configured_engine
from unilabos.scheduling.validation import validate_decision

examples = json.loads((CONTRACT_DIR / "scheduler-engine-v1.examples.json").read_text(encoding="utf-8"))
cases = {item["name"]: item for item in examples["cases"]}
selected = configured_engine({
    "UNILABOS_SCHEDULING_ENGINE": "plugin", "UNILABOS_SCHEDULING_MANIFEST": sys.argv[2],
})
assert selected.engine_id == "example.newest_task"
try:
    for case in examples["cases"]:
        if not case["valid"] or "snapshot" not in case["message"].get("body", {}) or case.get("semantic_scope") == "pair_only":
            continue
        request = deepcopy(case["message"])
        body = request["body"]
        body["instance_id"] = str(uuid4())
        body["checkpoint"]["engine_id"] = selected.engine_id
        creation = deepcopy(cases["create_request"]["message"])
        creation["request_id"] = str(uuid4())
        creation["body"].update(
            **{key: body[key] for key in ("instance_id", "domain_id", "run_epoch")},
            config={}, config_id=body["checkpoint"]["config_id"],
        )
        creation["body"]["restore"] = {
            "mode": "rebuild", "checkpoint": None, "feedback_base_seq": body["checkpoint"]["feedback_cursor"],
            "allow_model_reset": False,
        }
        for message in (creation, request):
            response = decode_message(selected.transport.call(encode_message(message), timeout_ms=5000))
            expected = case.get("expected_semantic_error") if message is request else None
            if expected:
                assert not response["ok"] and response["error"]["code"] == expected, (case["name"], response)
            else:
                assert response["ok"], (case["name"], response)
                if message is request:
                    validate_decision(request, response, capabilities=selected.transport.description["capabilities"])
        close = deepcopy(cases["close_request"]["message"])
        close.update(request_id=str(uuid4()))
        close["body"].update(**{key: body[key] for key in ("instance_id", "domain_id", "run_epoch")})
        assert decode_message(selected.transport.call(encode_message(close), timeout_ms=5000))["ok"]
finally:
    selected.transport.shutdown()
