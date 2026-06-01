"""RosLiveSource 缓存/优先级逻辑单测(不依赖 ROS2)。"""

from types import SimpleNamespace

from unilabos.queries.engine import QueryEngine, QueryNotFound
from unilabos.queries.ros_live_source import RosLiveSource, build_live_query_engine


def test_update_and_query_pose():
    live = RosLiveSource()
    live.update_pose("balance_1", [0.6, 0.0, 0.05], frame_id="robot_base")
    pose = live.query_pose("balance_1")
    assert pose is not None
    assert pose.xyz == [0.6, 0.0, 0.05]
    assert pose.frame_id == "robot_base"
    assert pose.source == "ros_live"


def test_query_pose_missing_returns_none():
    assert RosLiveSource().query_pose("nope") is None


def test_frame_mismatch_returns_none():
    live = RosLiveSource()
    live.update_pose("x", [0, 0, 0], frame_id="lab_world")
    assert live.query_pose("x", frame="robot_base") is None
    assert live.query_pose("x", frame="lab_world") is not None


def test_max_age_staleness(monkeypatch):
    import unilabos.queries.ros_live_source as mod

    t = {"now": 1000.0}
    monkeypatch.setattr(mod.time, "monotonic", lambda: t["now"])
    live = RosLiveSource(max_age_s=1.0)
    live.update_state("dev", {"a": 1})
    assert live.query_state("dev") is not None
    t["now"] += 2.0  # exceed max_age
    assert live.query_state("dev") is None


def test_on_joint_states_caches_state():
    live = RosLiveSource()
    msg = SimpleNamespace(
        name=["j1", "j2"],
        position=[0.1, 0.2],
        velocity=[0.0, 0.0],
        header=SimpleNamespace(frame_id="ur5"),
    )
    live._on_joint_states(msg)
    state = live.query_state("ur5")
    assert state.values["positions"] == [0.1, 0.2]
    assert live.query_state("j1").values["position"] == 0.1


def test_engine_prefers_live_over_static():
    """live source 置首:同名目标命中 live,不落到 static。"""

    class StaticSource:
        name = "static"

        def query_pose(self, target, frame=None):
            from unilabos.queries.models import Pose

            return Pose(xyz=[9, 9, 9], source="static")

        def query_state(self, target):
            return None

        def query_affordance(self, target, kind=None):
            return []

        def query_action_schema(self, action):
            return None

        def query_safety_zones(self):
            return []

    live, engine = build_live_query_engine(static_sources=[StaticSource()])
    # before live update -> falls through to static
    assert engine.query_pose("obj").xyz == [9, 9, 9]
    # after live update -> live wins
    live.update_pose("obj", [1, 2, 3])
    assert engine.query_pose("obj").xyz == [1, 2, 3]


def test_build_live_query_engine_no_node():
    live, engine = build_live_query_engine()
    assert isinstance(live, RosLiveSource)
    assert isinstance(engine, QueryEngine)
    with __import__("pytest").raises(QueryNotFound):
        engine.query_pose("missing")
