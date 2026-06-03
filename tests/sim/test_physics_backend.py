from unilabos.sim.physics_backend import PhysicsBackend


class FakePhysics:
    name = "fake"

    def reset(self) -> None:
        self.reset_called = True

    def step(self, dt: float) -> None:
        self.dt = dt

    def load_scene(self, scene_path: str) -> None:
        self.scene_path = scene_path

    def get_observation(self, entity_id: str):
        return {"entity_id": entity_id}

    def set_command(self, entity_id: str, command):
        self.command = (entity_id, command)

    def attach_rigid_body(self, name: str, asset_path: str, pose):
        return f"{name}:body"

    def get_joint_states(self, body_id: str):
        return {"joint_1": 0.0}

    def apply_wrench(self, body_id: str, wrench):
        self.wrench = (body_id, wrench)

    def register_contact_callback(self, callback):
        self.contact_callback = callback

    def render(self, camera: str, width: int, height: int) -> bytes:
        return f"{camera}:{width}x{height}".encode()


def test_physics_backend_protocol_runtime_check():
    assert isinstance(FakePhysics(), PhysicsBackend)


def test_physics_backend_extended_contract_methods():
    backend = FakePhysics()
    assert backend.attach_rigid_body("arm", "arm.urdf", {"xyz": [0, 0, 0]}) == "arm:body"
    assert backend.get_joint_states("arm:body") == {"joint_1": 0.0}
    backend.apply_wrench("arm:body", {"force": [1, 0, 0]})
    assert backend.wrench == ("arm:body", {"force": [1, 0, 0]})


def test_physics_backend_scene_and_render_contract():
    backend = FakePhysics()
    backend.load_scene("/tmp/lab.usd")
    assert backend.scene_path == "/tmp/lab.usd"
    assert backend.render("/World/Camera", 320, 240) == b"/World/Camera:320x240"
