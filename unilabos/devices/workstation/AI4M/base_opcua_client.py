"""向后兼容：实现位于 unilabos.devices.workstation.base_opcua_client。"""
from unilabos.devices.workstation.base_opcua_client import (  # noqa: F401
    BaseOpcUaClient,
    OpcUaClientWithSubscription,
    OpcUaNode,
)
