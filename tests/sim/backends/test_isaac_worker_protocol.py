import json

import pytest

from unilabos.sim.backends.isaac.protocol import decode_request, encode_error, encode_response


def test_decode_request_reads_operation_and_args():
    op, args = decode_request(b'{"op":"step","args":{"dt":0.05}}')

    assert op == "step"
    assert args == {"dt": 0.05}


def test_decode_request_rejects_missing_operation():
    with pytest.raises(ValueError, match="RPC request missing op"):
        decode_request(b'{"args":{}}')


def test_decode_request_rejects_non_object_args():
    with pytest.raises(ValueError, match="RPC request args must be an object"):
        decode_request(b'{"op":"step","args":[1,2]}')


def test_encode_response_matches_client_decode_shape():
    body = encode_response({"ok_value": 1})

    assert json.loads(body.decode("utf-8")) == {"ok": True, "result": {"ok_value": 1}}


def test_encode_error_matches_client_decode_shape():
    body = encode_error("bad scene")

    assert json.loads(body.decode("utf-8")) == {"ok": False, "error": "bad scene"}
