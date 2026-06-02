from unilabos.sim.context import RuntimeContext, _reset_for_test, init_runtime_context
from unilabos.sim.device_physics import dispatch_device_command


class RecordingPhysics:
    name = "recording"

    def __init__(self):
        self.commands = []

    def set_command(self, entity_id, command):
        self.commands.append((entity_id, command))


def setup_function():
    _reset_for_test()


def teardown_function():
    _reset_for_test()


def test_dispatch_device_command_noops_without_physics():
    assert dispatch_device_command("valve", {"type": "set_position"}) is False


def test_dispatch_device_command_sends_to_runtime_physics():
    physics = RecordingPhysics()
    init_runtime_context(RuntimeContext(mode="sim", physics=physics, physics_backend_name="fake"))

    assert dispatch_device_command("valve", {"type": "set_position", "position": 3}) is True
    assert physics.commands == [("valve", {"type": "set_position", "position": 3})]
