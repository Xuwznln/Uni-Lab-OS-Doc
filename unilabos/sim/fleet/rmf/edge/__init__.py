"""edge AGV 侧（非原生 ROS，HTTP 接收 RMF 指令 + mock 硬件，#18 §10.2–§10.4）。"""

from unilabos.sim.fleet.rmf.edge.mock_agv import MockAgvHardware

__all__ = ["MockAgvHardware"]
