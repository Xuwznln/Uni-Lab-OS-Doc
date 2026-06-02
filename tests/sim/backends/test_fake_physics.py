from unilabos.sim.backends.fake_physics import FakePhysicsBackend
from unilabos.sim.physics_backend import PhysicsBackend


def test_fake_backend_satisfies_physics_protocol():
    assert isinstance(FakePhysicsBackend(), PhysicsBackend)


def test_fake_backend_records_scene_commands_and_steps():
    backend = FakePhysicsBackend()
    backend.load_scene("/tmp/lab.usd")
    backend.set_command("arm", {"type": "move_j", "joint_positions": [1.0, 2.0]})
    backend.step(0.05)

    assert backend.scene_path == "/tmp/lab.usd"
    assert backend.commands["arm"] == {"type": "move_j", "joint_positions": [1.0, 2.0]}
    assert backend.sim_time == 0.05
    assert backend.get_observation("arm")["last_command"]["type"] == "move_j"


def test_fake_backend_tracks_joint_states_and_rigid_bodies():
    backend = FakePhysicsBackend()
    backend.set_joint_states("arm", {"joint_1": 1.25, "joint_2": -0.5})
    body_id = backend.attach_rigid_body("beaker", "beaker.usd", {"xyz": [0, 0, 0]})

    assert body_id == "beaker"
    assert backend.get_joint_states("arm") == {"joint_1": 1.25, "joint_2": -0.5}
    assert backend.get_observation("beaker")["asset_path"] == "beaker.usd"


def test_fake_backend_render_returns_png_like_bytes():
    backend = FakePhysicsBackend()
    image = backend.render("/World/Camera", 320, 240)

    assert image.startswith(b"\x89PNG\r\n\x1a\n")
    assert b"/World/Camera" in image


def test_fake_backend_contact_callback_receives_applied_wrench_event():
    backend = FakePhysicsBackend()
    events = []
    backend.register_contact_callback(events.append)

    backend.apply_wrench("arm", {"force": [1, 0, 0]})

    assert events == [{"type": "wrench", "body_id": "arm", "wrench": {"force": [1, 0, 0]}}]
