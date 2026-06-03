import pytest

from unilabos.sim.backends.isaac.protocol import decode_response, encode_request


def test_encode_request_builds_compact_json_payload():
    payload = encode_request("set_command", {"entity_id": "arm", "command": {"type": "move_j"}})

    assert payload == b'{"op":"set_command","args":{"entity_id":"arm","command":{"type":"move_j"}}}'


def test_decode_response_returns_result_for_ok_payload():
    assert decode_response(b'{"ok":true,"result":{"joint_1":1.0}}') == {"joint_1": 1.0}


def test_decode_response_raises_for_worker_error():
    with pytest.raises(RuntimeError, match="Isaac worker RPC failed: bad scene"):
        decode_response(b'{"ok":false,"error":"bad scene"}')
