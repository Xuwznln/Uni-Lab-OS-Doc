#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""场景一（报告基因检测 / 双荧光素酶）结果计算。

纯函数模块，供 sirna_station 的 ``compute_experiment1_result`` 节点 import。
不依赖 gin/ROS/网络，便于单测。

数据流（详见 plan/实验一结果节点_*.plan.md）:
  1) 96 孔板布局 xlsx(实验一细胞培养板.xlsx, 多 sheet 细胞培养板1..4)
       -> 每孔样本标签(空白对照 / 样品N-ID)
  2) material-info(384孔板).parameters.pipettingInfo.WellMappings
       -> 96 孔位 -> 384 孔位 映射 {"A1":"B2", ...}
  3) BioTek Synergy H1 384 孔 CSV(renilla + firefly, Results 段 16x24 发光值)
       -> {384 孔位: 发光值}
  4) 计算: ratio = Renilla / Firefly
       抑制率 = 1 - ratio / mean(空白对照组 ratio)
       按样本聚合 mean/SD(抑制率); 空白对照结果列留空。
"""

from __future__ import annotations

import csv
import json
import os
import re
import statistics
from typing import Any, Dict, List, Optional, Tuple

try:
    import openpyxl  # noqa: F401
    _OPENPYXL_OK = True
except Exception:  # pragma: no cover - 轻量环境无 openpyxl 时延迟报错
    _OPENPYXL_OK = False

ROWS96 = "ABCDEFGH"               # 96 孔 8 行
ROWS384 = "ABCDEFGHIJKLMNOP"      # 384 孔 16 行

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_HERE, "..", ".."))
DEFAULT_PLATE_MAP = os.path.join(_REPO, "data", "实验一细胞培养板.xlsx")


# ===========================================================================
# 样本标签辅助
# ===========================================================================

def is_blank(label: str) -> bool:
    """是否空白对照。"""
    return "空白" in (label or "")


def sample_sort_key(label: str) -> Tuple[int, int]:
    """排序：空白对照在前，其余按样品编号升序。"""
    if is_blank(label):
        return (-1, 0)
    m = re.search(r"(\d+)", label or "")
    return (0, int(m.group(1)) if m else 999)


# ===========================================================================
# 1) 96 孔板布局 xlsx
# ===========================================================================

def read_plate_map_sheet(xlsx_path: str, sheet_name: Optional[str] = None) -> Dict[str, str]:
    """读 96 孔板布局某个 sheet，返回 {孔位: 标签}，孔位如 ``A1``..``H12``。

    Args:
        xlsx_path: xlsx 路径。
        sheet_name: sheet 名（如 ``细胞培养板1``）；缺省取活动 sheet。
    """
    if not _OPENPYXL_OK:
        raise RuntimeError("缺少 openpyxl 依赖，无法读取孔板布局 xlsx")
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    ws = wb[sheet_name] if sheet_name else wb.active
    labels: Dict[str, str] = {}
    for r in range(1, 9):          # 8 行
        for c in range(1, 13):     # 12 列
            cell = ws.cell(row=r + 1, column=c + 1).value
            if cell is not None and str(cell).strip():
                labels[f"{ROWS96[r - 1]}{c}"] = str(cell).strip()
    return labels


# ===========================================================================
# 2) material-info -> 96->384 孔位映射
# ===========================================================================

def parse_well_mapping(material_info_data: Dict[str, Any]) -> Dict[str, str]:
    """从 384孔板 material-info 的 ``data`` 解析 96->384 孔位映射。

    结构（live 确认）: data.parameters(JSON 串) -> pipettingInfo(JSON 串)
        -> WellMappings: [{"Source": "A1", "Target": "B2"}, ...]
    返回 {"A1": "B2", ...}（Source=96 孔位, Target=384 孔位）。

    注意: 细胞培养板自身的 parameters 为 null，映射只在 384孔板 上。
    """
    if not isinstance(material_info_data, dict):
        return {}
    params = material_info_data.get("parameters")
    if not params:
        return {}
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except (json.JSONDecodeError, TypeError):
            return {}
    pip = params.get("pipettingInfo") if isinstance(params, dict) else None
    if isinstance(pip, str):
        try:
            pip = json.loads(pip)
        except (json.JSONDecodeError, TypeError):
            return {}
    well_mappings = (pip or {}).get("WellMappings") if isinstance(pip, dict) else None
    mapping: Dict[str, str] = {}
    for item in well_mappings or []:
        if not isinstance(item, dict):
            continue
        src = item.get("Source")
        tgt = item.get("Target")
        if src and tgt:
            mapping[str(src).strip()] = str(tgt).strip()
    return mapping


# ===========================================================================
# 3) BioTek 384 孔 CSV
# ===========================================================================

def parse_biotek_384_csv(csv_path: str) -> Tuple[Dict[str, float], Dict[str, str]]:
    """解析 BioTek Synergy H1 384 孔发光 CSV。

    返回 (grid, meta):
      grid: {孔位: 发光值}，孔位如 ``A1``..``P24``（丢弃每行末尾的 ``Lum`` 标记列）。
      meta: 含 Date/Reader/ProtocolFilePath 等摘要，用于通道判别与注释。
    """
    rows: List[List[str]] = []
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        for line in csv.reader(f, delimiter="\t"):
            rows.append(line)

    meta: Dict[str, str] = {}
    for r in rows:
        if not r:
            continue
        key = r[0].strip()
        if key in ("Date", "Time", "Reader Type:", "Reader Serial Number:") and len(r) > 1:
            meta[key.rstrip(":")] = r[1].strip()
        elif key == "Protocol File Path:" and len(r) > 1:
            meta["ProtocolFilePath"] = r[1].strip()

    grid: Dict[str, float] = {}
    in_results = False
    for r in rows:
        if r and r[0].strip() == "Results":
            in_results = True
            continue
        if in_results and r and len(r[0].strip()) == 1 and r[0].strip() in ROWS384:
            row_letter = r[0].strip()
            for c in range(1, 25):  # 24 列
                if c < len(r):
                    cell = r[c].strip()
                    if cell and re.fullmatch(r"-?\d+(\.\d+)?", cell):
                        grid[f"{row_letter}{c}"] = float(cell)
    return grid, meta


def detect_channel(csv_path: str, meta: Optional[Dict[str, str]] = None) -> Optional[str]:
    """判别 384 CSV 是 renilla 还是 firefly。

    优先看 CSV 内 ``Protocol File Path`` 是否含 ``Renilla.prt`` / ``Firefly.prt``，
    兜底看文件名。返回 ``"renilla"`` / ``"firefly"`` / ``None``。
    """
    hay = ""
    if meta:
        hay += str(meta.get("ProtocolFilePath") or "")
    hay = (hay + " " + os.path.basename(csv_path or "")).lower()
    if "renilla" in hay:
        return "renilla"
    if "firefly" in hay:
        return "firefly"
    return None


# ===========================================================================
# 4) 计算: ratio / 抑制率 / mean / SD
# ===========================================================================

def _round(v: Optional[float], n: int) -> Optional[float]:
    return None if v is None else round(v, n)


def compute_exp1_result(plates: List[Dict[str, Any]]) -> Dict[str, Any]:
    """计算实验一结果（双荧光素酶 ratio / 抑制率）。

    Args:
        plates: 每块板一项，字段:
            - plate_name (str): 板名(细胞培养板1..4)
            - seq (int): 板号
            - labels (dict): {96孔位: 样本标签}
            - mapping (dict): {96孔位: 384孔位}
            - renilla (dict): {384孔位: 发光值}
            - firefly (dict): {384孔位: 发光值}

    Returns:
        {"columns": [...], "data": [行 dict...], "meta": {...}}
        - ratio = Renilla / Firefly
        - 抑制率 = 1 - ratio / mean(空白对照组 ratio)
        - 抑制率均值/SD 按样本标签聚合(同组仅首行展示)
        - 空白对照结果列(抑制率/均值/SD)留空
    """
    # 先把所有孔的 ratio 算出来
    wells: List[Dict[str, Any]] = []
    for plate in plates:
        labels = plate.get("labels") or {}
        mapping = plate.get("mapping") or {}
        renilla = plate.get("renilla") or {}
        firefly = plate.get("firefly") or {}
        plate_name = plate.get("plate_name") or f"细胞培养板{plate.get('seq', '')}"
        for w96 in sorted(labels.keys(), key=_well96_sort_key):
            label = labels[w96]
            w384 = mapping.get(w96)
            ren = renilla.get(w384) if w384 else None
            ff = firefly.get(w384) if w384 else None
            ratio = (ren / ff) if (ren is not None and ff) else None
            wells.append({
                "plate": plate_name,
                "seq": plate.get("seq"),
                "well96": w96,
                "well384": w384 or "",
                "label": label,
                "renilla": ren,
                "firefly": ff,
                "ratio": ratio,
            })

    # 空白对照组 ratio 均值（作分母）——按板分别聚合 {seq: blank_mean}
    blank_ratios_by_seq: Dict[Any, List[float]] = {}
    for w in wells:
        if is_blank(w["label"]) and w["ratio"] is not None:
            blank_ratios_by_seq.setdefault(w.get("seq"), []).append(w["ratio"])
    blank_mean_by_seq: Dict[Any, Optional[float]] = {
        seq: (statistics.mean(vals) if vals else None)
        for seq, vals in blank_ratios_by_seq.items()
    }

    # 抑制率（空白对照不算，结果列留空）——用本板空白均值作分母
    inhib_by_group: Dict[Tuple[Any, str], List[float]] = {}
    for w in wells:
        blank_mean = blank_mean_by_seq.get(w.get("seq"))
        if is_blank(w["label"]) or w["ratio"] is None or not blank_mean:
            w["inhibition"] = None
        else:
            w["inhibition"] = 1.0 - w["ratio"] / blank_mean
            inhib_by_group.setdefault((w.get("seq"), w["label"]), []).append(w["inhibition"])

    # 按板×样本聚合 抑制率 均值/SD
    stats_by_group: Dict[Tuple[Any, str], Tuple[Optional[float], Optional[float]]] = {}
    for key, vals in inhib_by_group.items():
        mean = statistics.mean(vals) if vals else None
        sd = statistics.stdev(vals) if len(vals) > 1 else (0.0 if vals else None)
        stats_by_group[key] = (mean, sd)

    # 排序：板号 -> 样本 -> 96 孔位；同板同样本组仅首行展示 均值/SD
    wells.sort(key=lambda w: (w.get("seq") or 0, sample_sort_key(w["label"]), _well96_sort_key(w["well96"])))

    data: List[Dict[str, Any]] = []
    prev_key = None
    for w in wells:
        group_key = (w.get("seq"), w["label"])
        if not is_blank(w["label"]) and group_key != prev_key:
            mean, sd = stats_by_group.get(group_key, (None, None))
            prev_key = group_key
        else:
            mean = sd = None
        data.append({
            "plate": w["plate"],
            "well": w["well96"],
            "sample": w["label"],
            "renilla": _fmt(w["renilla"], 0),
            "firefly": _fmt(w["firefly"], 0),
            "ratio": _fmt(w["ratio"], 2),
            "inhibition": _fmt(w["inhibition"], 4),
            "inhibition_mean": _fmt(mean, 4),
            "inhibition_sd": _fmt(sd, 4),
        })

    columns = [
        {"name": "板", "key": "plate"},
        {"name": "孔位", "key": "well"},
        {"name": "样本", "key": "sample"},
        {"name": "Renilla", "key": "renilla"},
        {"name": "Firefly", "key": "firefly"},
        {"name": "ratio", "key": "ratio"},
        {"name": "抑制率", "key": "inhibition"},
        {"name": "抑制率均值", "key": "inhibition_mean"},
        {"name": "抑制率SD", "key": "inhibition_sd"},
    ]
    return {
        "columns": columns,
        "data": data,
        "meta": {
            "blank_mean_ratio_by_seq": {
                seq: _round(v, 4) for seq, v in sorted(
                    blank_mean_by_seq.items(), key=lambda kv: kv[0] or 0
                )
            },
            "plate_count": len(plates),
            "well_count": len(wells),
        },
    }


def _fmt(v: Optional[float], n: int) -> str:
    """数值转字符串；None -> 空串（空白对照结果列留空走这里）。"""
    if v is None:
        return ""
    if n == 0:
        return str(int(round(v)))
    return f"{round(v, n)}"


def _well96_sort_key(well: str) -> Tuple[int, int]:
    """``A1``..``H12`` 排序：先行后列。"""
    m = re.fullmatch(r"([A-H])(\d{1,2})", well or "")
    if not m:
        return (99, 99)
    return (ROWS96.index(m.group(1)), int(m.group(2)))
