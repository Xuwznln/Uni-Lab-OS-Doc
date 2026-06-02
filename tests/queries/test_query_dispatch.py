"""query_dispatch 传输无关分发逻辑单测(不依赖 ROS2)。"""

import json

from unilabos.api import QueryService, dispatch, dispatch_json
from unilabos.queries.ros_live_source import build_live_query_engine


def _service_with_pose():
    live, engine = build_live_query_engine()
    live.update_pose("balance_1.tare", [0.6, 0.0, 0.07], frame_id="robot_base")
    return QueryService(engine)


def test_dispatch_query_pose_ok():
    svc = _service_with_pose()
    out = dispatch(svc, "query_pose", {"target": "balance_1.tare"})
    assert out["ok"] is True
    assert out["op"] == "query_pose"
    assert out["result"]["xyz"] == [0.6, 0.0, 0.07]


def test_dispatch_not_found():
    svc = _service_with_pose()
    out = dispatch(svc, "query_pose", {"target": "nope"})
    assert out["ok"] is False
    assert out["code"] == "not_found"


def test_dispatch_unknown_op():
    svc = _service_with_pose()
    out = dispatch(svc, "drop_table", {})
    assert out["ok"] is False
    assert out["code"] == "unknown_op"


def test_dispatch_bad_args():
    svc = _service_with_pose()
    out = dispatch(svc, "query_pose", {"wrong": 1})
    assert out["ok"] is False
    assert out["code"] == "bad_args"


def test_dispatch_action_schema_builtin():
    svc = _service_with_pose()
    out = dispatch(svc, "query_action_schema", {"action": "press_button"})
    assert out["ok"] is True
    assert out["result"]["action"] == "press_button"


def test_dispatch_json_roundtrip():
    svc = _service_with_pose()
    command = json.dumps({"op": "query_pose", "args": {"target": "balance_1.tare"}})
    resp = json.loads(dispatch_json(svc, command))
    assert resp["ok"] is True
    assert resp["result"]["frame_id"] == "robot_base"


def test_dispatch_json_bad_json():
    svc = _service_with_pose()
    resp = json.loads(dispatch_json(svc, "{not json"))
    assert resp["ok"] is False
    assert resp["code"] == "bad_json"
