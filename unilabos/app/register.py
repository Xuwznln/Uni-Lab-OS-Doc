import time
from typing import Any, Dict, List, Optional, Tuple

from unilabos.utils.log import logger
from unilabos.utils.tools import fast_dumps as _fast_dumps, normalize_json as _normalize_device

# 后端 gzip 中间件对「解压后」请求体有 64MB 硬上限（防 gzip bomb），超出会被静默截断，
# 导致后端 JSON 解析报 "unexpected EOF"。这里客户端主动分批，保证每批解压后远低于该上限。
_MAX_BATCH_DECOMPRESSED_BYTES = 40 * 1024 * 1024  # 单批解压后体积上限，留足冗余
_MAX_BATCH_COUNT = 200  # 单批条数上限，避免条数过多


def _chunk_by_size(items: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """按「序列化后体积 + 条数」双阈值贪心分批。

    每批控制在 `_MAX_BATCH_DECOMPRESSED_BYTES` 与 `_MAX_BATCH_COUNT` 之内；
    单条即超阈值时，让它独占一批并打 warning（后端仍可能拒绝，但至少不拖垮其它批）。
    """
    batches: List[List[Dict[str, Any]]] = []
    current: List[Dict[str, Any]] = []
    current_bytes = 0
    # 外层 {"resources": [...]} 的固定开销，估算即可
    envelope_overhead = len(b'{"resources": []}')

    for item in items:
        try:
            item_bytes = len(_fast_dumps(item)) + 1  # +1 预留逗号分隔符
        except Exception:
            # 序列化失败的条目交给下游 resource_registry 再暴露具体错误
            item_bytes = 0

        if item_bytes > _MAX_BATCH_DECOMPRESSED_BYTES:
            logger.warning(
                f"[UniLab Register] 单个资源序列化后约 {item_bytes / 1024 / 1024:.1f}MB，"
                f"超过单批上限 {_MAX_BATCH_DECOMPRESSED_BYTES / 1024 / 1024:.0f}MB，将独占一批上传"
            )

        would_exceed_bytes = current and (current_bytes + item_bytes + envelope_overhead) > _MAX_BATCH_DECOMPRESSED_BYTES
        would_exceed_count = len(current) >= _MAX_BATCH_COUNT
        if would_exceed_bytes or would_exceed_count:
            batches.append(current)
            current = []
            current_bytes = 0

        current.append(item)
        current_bytes += item_bytes

    if current:
        batches.append(current)
    return batches


def _register_in_batches(http_client, items: List[Dict[str, Any]], kind_label: str, base_tag: str) -> None:
    """分批上传设备/资源注册表，逐批 POST 并汇总结果。

    后端 `/lab/resource` 按 (lab_id, name) 逐条 upsert，分批之间互不覆盖，因此分批安全。
    """
    batches = _chunk_by_size(items)
    total = len(items)
    total_batches = len(batches)
    logger.info(f"[UniLab Register] {kind_label} 共 {total} 个，分 {total_batches} 批上传")

    ok_count = 0
    failed_count = 0
    overall_start = time.time()

    for idx, batch in enumerate(batches, start=1):
        tag = base_tag if total_batches == 1 else f"{base_tag}_batch{idx}"
        try:
            start_time = time.time()
            response = http_client.resource_registry({"resources": batch}, tag=tag)
            cost_time = time.time() - start_time

            res_data = response.json() if response.status_code == 200 else {}
            body_code = res_data.get("code", 0)
            skipped = res_data.get("data", {}).get("skipped", False)

            if response.status_code in (200, 201) and body_code in (0, None):
                ok_count += len(batch)
                if skipped:
                    logger.info(
                        f"[UniLab Register] {kind_label} 第 {idx}/{total_batches} 批跳过"
                        f"（内容未变化）{len(batch)} 个 {cost_time:.3f}s"
                    )
                else:
                    logger.info(
                        f"[UniLab Register] {kind_label} 第 {idx}/{total_batches} 批成功 "
                        f"{len(batch)} 个 {cost_time:.3f}s"
                    )
            else:
                failed_count += len(batch)
                logger.error(
                    f"[UniLab Register] {kind_label} 第 {idx}/{total_batches} 批失败: "
                    f"HTTP {response.status_code}, body={response.text} {cost_time:.3f}s"
                )
        except Exception as e:
            failed_count += len(batch)
            logger.error(f"[UniLab Register] {kind_label} 第 {idx}/{total_batches} 批异常: {e}")

    overall_cost = time.time() - overall_start
    if failed_count == 0:
        logger.info(
            f"[UniLab Register] {kind_label} 全部上传完成: 成功 {ok_count}/{total} 个，"
            f"共 {total_batches} 批 {overall_cost:.3f}s"
        )
    else:
        logger.error(
            f"[UniLab Register] {kind_label} 上传部分失败: 成功 {ok_count}，失败 {failed_count}，"
            f"共 {total} 个 / {total_batches} 批 {overall_cost:.3f}s"
        )


def register_devices_and_resources(lab_registry, gather_only=False) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """
    注册设备和资源到服务器（仅支持HTTP）
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

    if gather_only:
        return devices_to_register, resources_to_register

    if devices_to_register:
        _register_in_batches(
            http_client,
            list(devices_to_register.values()),
            kind_label="设备",
            base_tag="device_registry",
        )

    if resources_to_register:
        _register_in_batches(
            http_client,
            list(resources_to_register.values()),
            kind_label="资源",
            base_tag="resource_registry",
        )
