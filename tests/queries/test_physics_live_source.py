from unilabos.queries.physics_live_source import PhysicsLiveSource


class FakePhysics:
    name = "fake"

    def __init__(self):
        self.observations = {
            "arm": {
                "entity_id": "arm",
                "pose": {"xyz": [0.1, 0.2, 0.3], "quat_xyzw": [0, 0, 0, 1], "frame_id": "world"},
                "joint_positions": [1.0, 2.0],
                "joint_names": ["j1", "j2"],
            },
            "tool": {
                "entity_id": "tool",
                "tcp_pose": [0.4, 0.5, 0.6, 0.0, 0.0, 0.0],
            },
        }

    def get_observation(self, entity_id):
        if entity_id not in self.observations:
            raise KeyError(entity_id)
        return dict(self.observations[entity_id])


def test_physics_live_source_maps_pose_dict_to_query_pose():
    source = PhysicsLiveSource(FakePhysics())

    pose = source.query_pose("arm")

    assert pose.xyz == [0.1, 0.2, 0.3]
    assert pose.quat_xyzw == [0, 0, 0, 1]
    assert pose.frame_id == "world"
    assert pose.source == "physics_live:fake"


def test_physics_live_source_maps_tcp_pose_rotvec_to_pose():
    source = PhysicsLiveSource(FakePhysics())

    pose = source.query_pose("tool")

    assert pose.xyz == [0.4, 0.5, 0.6]
    assert pose.quat_xyzw == [0.0, 0.0, 0.0, 1.0]


def test_physics_live_source_maps_observation_to_state():
    source = PhysicsLiveSource(FakePhysics())

    state = source.query_state("arm")

    assert state.values["entity_id"] == "arm"
    assert state.values["joint_positions"] == [1.0, 2.0]
    assert state.values["joint_names"] == ["j1", "j2"]
    assert state.source == "physics_live:fake"


def test_physics_live_source_missing_target_returns_none():
    source = PhysicsLiveSource(FakePhysics())

    assert source.query_pose("missing") is None
    assert source.query_state("missing") is None


def test_physics_live_source_frame_mismatch_returns_none():
    source = PhysicsLiveSource(FakePhysics())

    assert source.query_pose("arm", frame="base_link") is None
