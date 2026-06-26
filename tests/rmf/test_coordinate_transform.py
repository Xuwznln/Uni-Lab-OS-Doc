"""坐标变换单测（#18 §5.2）。"""

import math

import pytest

from unilabos.sim.fleet.rmf.coordinate_transform import (
    deg_to_rad,
    normalize_yaw,
    pascal_to_rmf,
    rmf_to_pascal,
)


def test_pantry_mm_to_m():
    x, y, yaw = pascal_to_rmf(x_mm=1990000, y_mm=638364, rotation_z=1.5708)
    assert round(x, 3) == 1990.0
    assert round(y, 3) == -638.364  # Y 翻转
    assert round(yaw, 4) == -1.5708


def test_y_flip_direction():
    # y_mm > 0 → y_m < 0（屏幕系 Y 向下 → Cartesian Y 向上）
    _, y_pos, _ = pascal_to_rmf(0, 1000, 0)
    assert y_pos < 0
    # 关闭翻转时同号
    _, y_noflip, _ = pascal_to_rmf(0, 1000, 0, y_flip=False)
    assert y_noflip > 0


def test_round_trip():
    x_mm, y_mm, rot = 123456.0, -78900.0, 0.5
    x_m, y_m, yaw = pascal_to_rmf(x_mm, y_mm, rot)
    bx, by, brot = rmf_to_pascal(x_m, y_m, yaw)
    assert math.isclose(bx, x_mm, abs_tol=1e-6)
    assert math.isclose(by, y_mm, abs_tol=1e-6)
    assert math.isclose(brot, rot, abs_tol=1e-9)


def test_deg_to_rad():
    assert math.isclose(deg_to_rad(90), math.pi / 2, abs_tol=1e-9)
    assert math.isclose(deg_to_rad(180), math.pi, abs_tol=1e-9)


def test_normalize_yaw_range():
    assert math.isclose(normalize_yaw(3 * math.pi), math.pi, abs_tol=1e-9)
    assert -math.pi < normalize_yaw(-3.5 * math.pi) <= math.pi


def test_scale_zero_inverse_raises():
    with pytest.raises(ValueError):
        rmf_to_pascal(1.0, 1.0, 0.0, scale=0.0)
