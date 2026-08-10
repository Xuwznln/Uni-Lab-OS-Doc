#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
DataCore 大装置数据接入公共工具

把设备产出的 CSV 推送到 DataCore 接入接口（HTTP Basic Auth + multipart 上传）。
接入凭据由各设备的图 JSON 配置提供（datacore_config），不在代码中硬编码。
"""

import os
import time
from typing import Any, Callable, Dict, Optional

import requests

# 默认接入地址（可用环境变量 DATACORE_INGEST_URL 覆盖）
DATACORE_INGEST_URL = "https://datacore.dp.qifalab.cn/api/ingest/big-device-csv"


def push_csv_to_datacore(csv_path, url=None, device_id=None, device_key=None,
                         timeout=20, total_timeout=60, retry_interval=5,
                         log: Callable[[str], Any] = print):
    """
    把 CSV 文件推送到 DataCore 大装置数据接入接口（HTTP Basic Auth + multipart 上传）。
    带失败重试：在 total_timeout 的总时间预算内反复尝试，单次请求超时不超过剩余预算。

    等价 curl:
        curl -u 'device_id:****' -F 'file=@your.csv;type=text/csv' \\
            https://datacore.dp.qifalab.cn/api/ingest/big-device-csv

    Args:
        csv_path:       本地 CSV 文件路径
        url:            接入地址，默认取环境变量 DATACORE_INGEST_URL 或内置常量
        device_id:      设备号（Basic Auth 用户名），默认取环境变量 DATACORE_DEVICE_ID
        device_key:     密钥（Basic Auth 密码），默认取环境变量 DATACORE_DEVICE_KEY
        timeout:        单次请求超时（秒）
        total_timeout:  失败重试的总时间预算（秒），默认 60（约 1 分钟）
        retry_interval: 两次尝试之间的等待（秒）
        log:            日志输出函数，默认 print，设备侧可传 logger.info

    Returns:
        bool: 推送是否成功
    """
    url = url or os.getenv("DATACORE_INGEST_URL", DATACORE_INGEST_URL)
    device_id = device_id or os.getenv("DATACORE_DEVICE_ID", "")
    device_key = device_key or os.getenv("DATACORE_DEVICE_KEY", "")

    if not csv_path or not os.path.exists(csv_path):
        log(f"[DataCore] 错误: CSV 文件不存在 {csv_path}")
        return False

    if not device_id or not device_key:
        log("[DataCore] 错误: 缺少接入凭据（device_id / device_key），请检查图 JSON 中的 datacore_config")
        return False

    filename = os.path.basename(csv_path)
    if not filename.lower().endswith(".csv"):
        log(f"[DataCore] 警告: {filename} 不是 .csv，仍按 text/csv 上传，请确认服务端能解析")

    # 一次性读入内存，重试时复用，避免每次重新读盘
    with open(csv_path, "rb") as f:
        file_bytes = f.read()

    deadline = time.monotonic() + max(1.0, float(total_timeout))
    attempt = 0
    last_err = None
    while True:
        remaining = deadline - time.monotonic()
        # 首次必做；之后预算耗尽则停止
        if attempt >= 1 and remaining <= 0:
            break
        attempt += 1
        # 单次请求超时不超过剩余预算，保证整体不会明显超过 total_timeout
        req_timeout = max(1.0, min(float(timeout), remaining)) if remaining > 0 else float(timeout)
        log(f"[DataCore] 第 {attempt} 次推送 {filename} 到 {url}"
            f"（剩余预算 {max(0.0, remaining):.0f}s，单次超时 {req_timeout:.0f}s）")
        try:
            response = requests.post(
                url,
                auth=(device_id, device_key),
                files={"file": (filename, file_bytes, "text/csv")},
                timeout=req_timeout,
            )
            response.raise_for_status()
            log(f"[DataCore] 推送成功（第 {attempt} 次）: {filename} -> HTTP {response.status_code}")
            return True
        except requests.exceptions.RequestException as e:
            last_err = e
            resp_text = e.response.text if getattr(e, "response", None) is not None else "无响应"
            log(f"[DataCore] 第 {attempt} 次推送失败: {e}; 服务器响应: {resp_text}")
        except Exception as e:
            last_err = e
            log(f"[DataCore] 第 {attempt} 次推送异常: {e}")

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(float(retry_interval), remaining))

    log(f"[DataCore] 推送最终失败（共 {attempt} 次，总预算 {total_timeout}s）: {filename}；最后错误: {last_err}")
    return False


def push_csv_by_config(csv_path: str, datacore_config: Optional[Dict[str, Any]],
                       tag: str = "", log: Callable[[str], Any] = print) -> bool:
    """
    按图 JSON 中的 datacore_config 推送 CSV，异常全部吞掉，绝不影响主流程。

    Args:
        csv_path:        本地 CSV 文件路径
        datacore_config: 设备图 JSON 中的 datacore_config 字典，支持字段：
                         enabled / ingest_url / device_id / device_key /
                         timeout / total_timeout / retry_interval
                         为空或 enabled=false 时跳过推送
        tag:             日志前缀标识，一般传节点名
        log:             日志输出函数，默认 print

    Returns:
        bool: 推送是否成功（跳过时返回 False）
    """
    prefix = f"[{tag}] " if tag else ""
    cfg = datacore_config or {}

    if not cfg:
        log(f"{prefix}[DataCore] 未配置 datacore_config，跳过推送: {csv_path}")
        return False
    if not cfg.get("enabled", True):
        log(f"{prefix}[DataCore] datacore_config.enabled=false，跳过推送: {csv_path}")
        return False

    try:
        ok = push_csv_to_datacore(
            csv_path,
            url=cfg.get("ingest_url"),
            device_id=cfg.get("device_id"),
            device_key=cfg.get("device_key"),
            timeout=cfg.get("timeout", 20),
            total_timeout=cfg.get("total_timeout", 60),
            retry_interval=cfg.get("retry_interval", 5),
            log=log,
        )
        log(f"{prefix}[DataCore] 推送{'成功' if ok else '失败'}: {csv_path}")
        return ok
    except Exception as e:
        log(f"{prefix}[DataCore] 推送异常: {e}")
        return False
