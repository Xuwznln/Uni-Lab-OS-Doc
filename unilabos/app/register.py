import time
from typing import Any, Dict, Optional, Tuple

from unilabos.utils.log import logger
from unilabos.utils.tools import normalize_json as _normalize_device


def register_devices_and_resources(
    lab_registry, gather_only=False, id_prefixes=None
) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """
    注册设备和资源到服务器（仅支持HTTP）

    Args:
        id_prefixes: 可选的 id 前缀白名单（忽略大小写）。非空时仅上报 id 以这些前缀开头的设备/资源；
            为 None 或空时全量上报。
    """

    from unilabos.app.web.client import http_client

    logger.info("[UniLab Register] 开始注册设备和资源...")

    devices_to_register = {}
    for device_info in lab_registry.obtain_registry_device_info():
        devices_to_register[device_info["id"]] = _normalize_device(device_info)
        logger.trace(f"[UniLab Register] 收集设备: {device_info['id']}")

    resources_to_register = {}
    for resource_info in lab_registry.obtain_registry_resource_info():
        resources_to_register[resource_info["id"]] = resource_info
        logger.trace(f"[UniLab Register] 收集资源: {resource_info['id']}")

    if id_prefixes:
        prefixes = tuple(p.lower() for p in id_prefixes)

        def _match(rid: str) -> bool:
            return rid.lower().startswith(prefixes)

        device_total, resource_total = len(devices_to_register), len(resources_to_register)
        devices_to_register = {k: v for k, v in devices_to_register.items() if _match(k)}
        resources_to_register = {k: v for k, v in resources_to_register.items() if _match(k)}
        logger.info(
            f"[UniLab Register] 上报白名单过滤(前缀={list(id_prefixes)}): "
            f"设备 {device_total} -> {len(devices_to_register)}, "
            f"资源 {resource_total} -> {len(resources_to_register)}"
        )

    if gather_only:
        return devices_to_register, resources_to_register

    if devices_to_register:
        try:
            start_time = time.time()
            response = http_client.resource_registry(
                {"resources": list(devices_to_register.values())},
                tag="device_registry",
            )
            cost_time = time.time() - start_time
            res_data = response.json() if response.status_code == 200 else {}
            skipped = res_data.get("data", {}).get("skipped", False)
            if skipped:
                logger.info(
                    f"[UniLab Register] 设备注册跳过（内容未变化）"
                    f" {len(devices_to_register)} 个 {cost_time:.3f}s"
                )
            elif response.status_code in [200, 201]:
                logger.info(f"[UniLab Register] 成功注册 {len(devices_to_register)} 个设备 {cost_time:.3f}s")
            else:
                logger.error(f"[UniLab Register] 设备注册失败: {response.status_code}, {response.text} {cost_time:.3f}s")
        except Exception as e:
            logger.error(f"[UniLab Register] 设备注册异常: {e}")

    if resources_to_register:
        try:
            start_time = time.time()
            response = http_client.resource_registry(
                {"resources": list(resources_to_register.values())},
                tag="resource_registry",
            )
            cost_time = time.time() - start_time
            res_data = response.json() if response.status_code == 200 else {}
            skipped = res_data.get("data", {}).get("skipped", False)
            if skipped:
                logger.info(
                    f"[UniLab Register] 资源注册跳过（内容未变化）"
                    f" {len(resources_to_register)} 个 {cost_time:.3f}s"
                )
            elif response.status_code in [200, 201]:
                logger.info(f"[UniLab Register] 成功注册 {len(resources_to_register)} 个资源 {cost_time:.3f}s")
            else:
                logger.error(f"[UniLab Register] 资源注册失败: {response.status_code}, {response.text} {cost_time:.3f}s")
        except Exception as e:
            logger.error(f"[UniLab Register] 资源注册异常: {e}")
