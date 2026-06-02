"""RoboUniLabOSRemote 经 local_transport 的全链路单测(client->dispatch->service,无 ROS2)。"""

import pytest

from unilabos.api import QueryService
from unilabos.queries.ros_live_source import build_live_query_engine
from unilabos_client import RemoteQueryError, RoboUniLabOSRemote, local_transport


def _remote_with_live():
    live, engine = build_live_query_engine()
    live.update_pose("hotplate_1.start_button", [0.6, 0.0, 0.05], frame_id="robot_base")
    live.update_state("ur5", {"positions": [0.1, 0.2]})
    service = QueryService(engine)
    return RoboUniLabOSRemote(local_transport(service)), live


def test_remote_query_pose():
    client, _ = _remote_with_live()
    pose = client.query_pose("hotplate_1.start_button")
    assert pose["xyz"] == [0.6, 0.0, 0.05]
    assert pose["frame_id"] == "robot_base"


def test_remote_query_state():
    client, _ = _remote_with_live()
    state = client.query_state("ur5")
    assert state["values"]["positions"] == [0.1, 0.2]


def test_remote_query_action_schema_builtin():
    client, _ = _remote_with_live()
    schema = client.query_action_schema("press_button")
    assert schema["action"] == "press_button"


def test_remote_not_found_raises():
    client, _ = _remote_with_live()
    with pytest.raises(RemoteQueryError) as ei:
        client.query_pose("does_not_exist")
    assert ei.value.code == "not_found"


def test_remote_live_update_reflected():
    client, live = _remote_with_live()
    live.update_pose("vial_a", [0.3, 0.1, 0.2])
    assert client.query_pose("vial_a")["xyz"] == [0.3, 0.1, 0.2]
