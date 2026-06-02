import unittest

from unilabos.hal.adapters.ur_adapter import URHAL
from unilabos.queries.models import Pose
from unilabos.sim.context import RuntimeContext, _reset_for_test, init_runtime_context


class FakeRTDEControl:
    def __init__(self):
        self.calls = []

    def moveL(self, tcp_pose, speed, acceleration):
        self.calls.append(("moveL", list(tcp_pose), speed, acceleration))

    def moveJ(self, joints, speed, acceleration):
        self.calls.append(("moveJ", list(joints), speed, acceleration))


class FakeRTDEReceive:
    def getActualTCPPose(self):
        return [0.1, 0.2, 0.3, 0.0, 0.0, 0.0]

    def getActualQ(self):
        return [0.0, 0.1, 0.2, 0.3, 0.4, 0.5]

    def getActualQd(self):
        return [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]


class FakeSimBackend:
    def __init__(self):
        self.commands = []

    def get_observation(self, entity_id):
        return {
            "tcp_pose": [0.4, 0.5, 0.6, 0.0, 0.0, 0.0],
            "joint_positions": [1, 2, 3, 4, 5, 6],
            "joint_velocities": [0, 0, 0, 0, 0, 0],
        }

    def set_command(self, entity_id, command):
        self.commands.append((entity_id, command))


class URAdapterTest(unittest.TestCase):
    def test_real_mode_can_use_injected_rtde_clients(self):
        control = FakeRTDEControl()
        receive = FakeRTDEReceive()
        hal = URHAL(host="192.0.2.10", rtde_control=control, rtde_receive=receive)

        pose = hal.get_pose()
        state = hal.get_joint_state()
        hal.move_l(Pose(xyz=[0.7, 0.8, 0.9]), speed=0.2)
        hal.move_j([0, 1, 2, 3, 4, 5], speed=0.3)

        self.assertEqual([0.1, 0.2, 0.3], pose.xyz)
        self.assertEqual([0.0, 0.0, 0.0, 1.0], pose.quat_xyzw)
        self.assertEqual([0.0, 0.1, 0.2, 0.3, 0.4, 0.5], state.positions)
        self.assertEqual("moveL", control.calls[0][0])
        self.assertEqual([0.7, 0.8, 0.9, 0.0, 0.0, 0.0], control.calls[0][1])
        self.assertEqual("moveJ", control.calls[1][0])

    def test_sim_mode_uses_physics_backend_contract(self):
        backend = FakeSimBackend()
        hal = URHAL(host="sim", robot_id="ur5_left", mode="sim", sim_backend=backend)

        pose = hal.get_pose()
        hal.close_gripper()

        self.assertEqual([0.4, 0.5, 0.6], pose.xyz)
        self.assertEqual(
            ("ur5_left", {"type": "gripper", "state": "closed"}),
            backend.commands[-1],
        )

    def test_sim_mode_defaults_to_runtime_physics_backend(self):
        _reset_for_test()
        backend = FakeSimBackend()
        init_runtime_context(RuntimeContext(mode="sim", physics=backend, physics_backend_name="fake"))
        try:
            hal = URHAL(host="sim", robot_id="ur5_runtime", mode="sim")

            hal.move_j([0, 1, 2, 3, 4, 5], speed=0.4)

            self.assertEqual(
                (
                    "ur5_runtime",
                    {"type": "move_j", "joint_positions": [0, 1, 2, 3, 4, 5], "speed": 0.4},
                ),
                backend.commands[-1],
            )
        finally:
            _reset_for_test()


if __name__ == "__main__":
    unittest.main()
