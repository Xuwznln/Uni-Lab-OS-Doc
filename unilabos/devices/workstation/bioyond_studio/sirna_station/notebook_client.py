#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实验记录本(notebook) 云端客户端 helper。

职责：
  1. OSS 直传(仅二进制资产需要)：GET /lab/storage/token → PUT 预签名 URL，返回 public_url。
  2. 读取/写入实验记录本：GET /lab/notebook/detail、PATCH /lab/notebook/lab-record。
  3. 构造 Slate 节点：table(原生表格) / img(图片) / file(附件) / p(段落)。

鉴权与地址：复用 edge 的 Lab AK/SK(``BasicConfig.auth_secret()`` →
``Authorization: Lab base64(ak:sk)``) 与 ``HTTPConfig.remote_addr``(已含 ``/api/v1``)。
URL 一律拼成 ``{remote_addr}/lab/...``，不再额外加 ``/api/v1``。

节点 schema 已在测试环境端到端验证(前端可正确渲染 table/img/file)。
"""

from __future__ import annotations

import mimetypes
import os
from typing import Any, Dict, List, Optional, Sequence

import requests

from unilabos.config.config import BasicConfig, HTTPConfig
from unilabos.utils import logger

# notebook 记录本默认每列像素宽(前端 EditorToolbar 默认 140)
_DEFAULT_COL_WIDTH = 160
# 写入 lab_record 的硬约束：必须 editing 才能写
_LAB_RECORD_STATUS_EDITING = "editing"


# ---------------------------------------------------------------------------
# 基础：鉴权头 / base url
# ---------------------------------------------------------------------------

def _base_url() -> str:
    """edge 远程地址(已含 /api/v1)；去掉末尾斜杠。"""
    return (HTTPConfig.remote_addr or "").rstrip("/")


def _lab_headers() -> Dict[str, str]:
    """Lab base64(ak:sk) 鉴权头；在调用时读取，避免导入期 ak/sk 尚未注入。"""
    secret = BasicConfig.auth_secret()
    if not secret:
        raise RuntimeError(
            "notebook_client 缺少 Lab 鉴权(ak/sk)：BasicConfig.auth_secret() 为空，"
            "请确认 edge 已通过 --ak/--sk 或配置注入。"
        )
    return {"Authorization": f"Lab {secret}"}


def _guess_content_type(filename: str) -> str:
    ctype, _ = mimetypes.guess_type(filename)
    return ctype or "application/octet-stream"


# ---------------------------------------------------------------------------
# OSS 上传
# ---------------------------------------------------------------------------

def upload_to_oss(
    file_path: str,
    scene: str = "image",
    content_type: Optional[str] = None,
    timeout: float = 60.0,
) -> Dict[str, Any]:
    """上传本地文件到 OSS，返回 img/file 节点所需的元信息。

    Args:
        file_path: 本地文件路径。
        scene: 存储场景，图片用 ``image``，附件用 ``file``。
        content_type: 显式 Content-Type；缺省按扩展名推断。
            注意：后端按 content_type 签名，PUT 必须发送相同值，否则签名校验失败。

    Returns:
        {"url": <public_url>, "path": <对象key>, "name": <文件名>,
         "size": <字节数>, "mimeType": <content_type>}
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"待上传文件不存在: {file_path}")

    filename = os.path.basename(file_path)
    ctype = content_type or _guess_content_type(filename)

    token_resp = requests.get(
        f"{_base_url()}/lab/storage/token",
        params={"scene": scene, "filename": filename, "content_type": ctype},
        headers=_lab_headers(),
        timeout=timeout,
    )
    token_resp.raise_for_status()
    body = token_resp.json()
    if body.get("code") != 0 or "data" not in body:
        raise RuntimeError(f"获取 storage token 失败: {body}")
    data = body["data"]
    put_url = data["url"]
    put_ct = data.get("content_type") or ctype

    with open(file_path, "rb") as fh:
        put_resp = requests.put(
            put_url, data=fh.read(), headers={"Content-Type": put_ct}, timeout=timeout
        )
    put_resp.raise_for_status()

    size = os.path.getsize(file_path)
    public_url = data.get("public_url") or ""
    if not public_url:
        logger.warning(
            f"[notebook_client] storage token 未返回 public_url(scene={scene} name={filename})，"
            "img/file 节点 url 将为空，前端可能无法展示；请确认该 bucket/scene 为公开读。"
        )
    logger.info(f"[notebook_client] OSS 上传成功 scene={scene} name={filename} url={public_url}")
    return {
        "url": public_url,
        "path": data.get("path", ""),
        "name": filename,
        "size": size,
        "mimeType": ctype,
    }


# ---------------------------------------------------------------------------
# notebook detail / lab-record
# ---------------------------------------------------------------------------

def get_notebook_detail(uuid: str, timeout: float = 30.0) -> Dict[str, Any]:
    """GET /lab/notebook/detail?uuid= ，返回 data 字典。"""
    resp = requests.get(
        f"{_base_url()}/lab/notebook/detail",
        params={"uuid": uuid},
        headers=_lab_headers(),
        timeout=timeout,
    )
    resp.raise_for_status()
    body = resp.json()
    if body.get("code") != 0:
        raise RuntimeError(f"获取 notebook detail 失败: {body}")
    return body.get("data", {}) or {}


def save_lab_record(
    uuid: str,
    lab_record: List[Dict[str, Any]],
    timeout: float = 30.0,
) -> None:
    """PATCH /lab/notebook/lab-record，内联数组写入并保持 editing。"""
    payload = {
        "uuid": uuid,
        "lab_record": lab_record,
        "lab_record_status": _LAB_RECORD_STATUS_EDITING,
    }
    resp = requests.patch(
        f"{_base_url()}/lab/notebook/lab-record",
        headers={**_lab_headers(), "Content-Type": "application/json"},
        json=payload,
        timeout=timeout,
    )
    resp.raise_for_status()
    body = resp.json()
    if body.get("code") != 0:
        raise RuntimeError(f"保存 lab_record 失败: {body}")
    logger.info(f"[notebook_client] lab_record 已保存 uuid={uuid} blocks={len(lab_record)}")


# ---------------------------------------------------------------------------
# Slate 节点构造(schema 已前端验证)
# ---------------------------------------------------------------------------

def text_block(text: str) -> Dict[str, Any]:
    """段落节点 p。"""
    return {"type": "p", "children": [{"text": str(text)}]}


def _cell(tag: str, text: Any) -> Dict[str, Any]:
    return {"type": tag, "children": [{"type": "p", "children": [{"text": str(text)}]}]}


def build_table_node(
    header: Sequence[Any],
    rows: Sequence[Sequence[Any]],
    col_sizes: Optional[Sequence[int]] = None,
) -> Dict[str, Any]:
    """原生表格节点 table(tr/th/td + colSizes)；无需 OSS。"""
    ncol = len(header)
    if col_sizes is None:
        col_sizes = [_DEFAULT_COL_WIDTH] * ncol
    trs: List[Dict[str, Any]] = [
        {"type": "tr", "children": [_cell("th", h) for h in header]}
    ]
    for row in rows:
        trs.append({"type": "tr", "children": [_cell("td", c) for c in row]})
    return {"type": "table", "colSizes": list(col_sizes), "children": trs}


def build_image_node(meta: Dict[str, Any], width: int = 600) -> Dict[str, Any]:
    """图片节点 img(void)；meta 来自 upload_to_oss。"""
    return {
        "type": "img",
        "url": meta.get("url", ""),
        "path": meta.get("path", ""),
        "name": meta.get("name", ""),
        "size": meta.get("size", 0),
        "mimeType": meta.get("mimeType", "image/png"),
        "width": width,
        "uploadStatus": "done",
        "children": [{"text": ""}],
    }


def build_file_node(meta: Dict[str, Any]) -> Dict[str, Any]:
    """附件节点 file(可下载卡片)；meta 来自 upload_to_oss。"""
    return {
        "type": "file",
        "url": meta.get("url", ""),
        "path": meta.get("path", ""),
        "name": meta.get("name", ""),
        "size": meta.get("size", 0),
        "mimeType": meta.get("mimeType", "application/octet-stream"),
        "uploadStatus": "done",
        "children": [{"text": ""}],
    }


# ---------------------------------------------------------------------------
# 高层：追加块并保存
# ---------------------------------------------------------------------------

def append_blocks_to_notebook(uuid: str, blocks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """读取记录本 → 校验 editing → 在已有内容后追加 blocks → 保存。

    Returns:
        {"appended": int, "total": int}
    """
    detail = get_notebook_detail(uuid)
    status = detail.get("lab_record_status")
    if status != _LAB_RECORD_STATUS_EDITING:
        raise RuntimeError(
            f"记录本不可写: lab_record_status={status}(需为 editing)；uuid={uuid}"
        )

    existing = detail.get("lab_record")
    # lab_record 兼容：内联数组直接用；空 dict/None/字符串(OSS URL) 一律当作空文档重建
    if isinstance(existing, list):
        base: List[Dict[str, Any]] = list(existing)
    else:
        base = []

    new_record = base + list(blocks)
    save_lab_record(uuid, new_record)
    return {"appended": len(blocks), "total": len(new_record)}
