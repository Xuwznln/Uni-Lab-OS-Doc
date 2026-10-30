"""使用已审核的 r3 示例验证公共协议边界。"""

import json
from copy import deepcopy

import pytest

from unilabos.scheduling.protocol import (
    CONTRACT_DIR,
    EngineError,
    decode_message,
    encode_message,
    validate_message,
)

EXAMPLES = json.loads(
    (CONTRACT_DIR / "scheduler-engine-v1.examples.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("case", EXAMPLES["cases"], ids=lambda case: case["name"])
def test_reviewed_wire_examples(case):
    if case["valid"]:
        validate_message(case["message"])
        assert decode_message(encode_message(case["message"])) == case["message"]
    else:
        with pytest.raises(EngineError):
            validate_message(case["message"])


@pytest.mark.parametrize("wire", [
    b'{"a": 1, "a": 2}', b'{"a": NaN}', b"\xff",
    b'{"a": 1e999}', b'{"a": 9007199254740992}', b'{"a":"\\ud800"}',
])
def test_decoder_rejects_ambiguous_or_invalid_json(wire):
    with pytest.raises(EngineError, match="INVALID_REQUEST"):
        decode_message(wire)


def test_request_limit_is_measured_in_utf8_bytes():
    wire = '{"message":"调度"}'.encode()
    with pytest.raises(EngineError, match="LIMIT_EXCEEDED"):
        decode_message(wire, max_bytes=len(wire) - 1)


def test_unsupported_schema_has_distinct_error_code():
    request = deepcopy(EXAMPLES["cases"][0]["message"])
    request["schema_version"] = "2.0"
    with pytest.raises(EngineError, match="UNSUPPORTED_SCHEMA"):
        validate_message(request)
