import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "integration: tests that exercise process-level or ROS2 integration paths"
    )


@pytest.fixture
def ros_context():
    """Init/shutdown rclpy for an integration test (skips if rclpy unavailable)."""
    rclpy = pytest.importorskip("rclpy")
    created = False
    if not rclpy.ok():
        rclpy.init()
        created = True
    yield rclpy
    if created and rclpy.ok():
        rclpy.shutdown()
