"""r3 已审核语义反例及请求/响应配对，独立于算法实现。"""

import json
from copy import deepcopy

import pytest

from unilabos.scheduling.protocol import CONTRACT_DIR, EngineError
from unilabos.scheduling.validation import validate_decision, validate_snapshot

EXAMPLES = json.loads((CONTRACT_DIR / "scheduler-engine-v1.examples.json").read_text(encoding="utf-8"))
CASES = {case["name"]: case for case in EXAMPLES["cases"]}
SNAPSHOTS = [
    case for case in EXAMPLES["cases"]
    if case["valid"]
    and "snapshot" in case["message"].get("body", {})
    and case.get("semantic_scope") != "pair_only"
]


@pytest.mark.parametrize("case", SNAPSHOTS, ids=lambda case: case["name"])
def test_reviewed_snapshot_semantics(case):
    expected = case.get("expected_semantic_error")
    if expected:
        with pytest.raises(EngineError, match=expected):
            validate_snapshot(case["message"])
    else:
        validate_snapshot(case["message"])


@pytest.mark.parametrize("pair", EXAMPLES["pairs"], ids=lambda pair: pair["response"])
def test_reviewed_response_semantics(pair):
    request = CASES[pair["request"]]["message"]
    response = CASES[pair["response"]]["message"]
    if pair["expected_error"]:
        with pytest.raises(EngineError, match=pair["expected_error"]):
            validate_decision(request, response)
    else:
        validate_decision(request, response)


def test_counter_overflow_is_rejected_before_engine_call():
    request = deepcopy(CASES["decide_request"]["message"])
    request["body"]["snapshot"]["revision"] = "18446744073709551616"
    with pytest.raises(EngineError, match="SNAPSHOT_INVALID"):
        validate_snapshot(request)
