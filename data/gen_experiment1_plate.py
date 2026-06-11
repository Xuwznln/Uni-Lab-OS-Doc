"""生成实验一孔板图 实验一细胞培养板.xlsx（4 块 96 孔板）。

仿 data/gen_plate.py，但一次产出 4 个 sheet：细胞培养板1 / 2 / 3 / 4，
每个 sheet 是一块 96 孔板(8x12)，单元格随机填 空白对照 / 样品N-ID。
sheet 名与提交期落盘文件 m 中的板名(细胞培养板1..4)一一对应，
作为 compute_experiment1_result 计算时「孔位 -> 样本身份」的来源。

用法:
    python3 data/gen_experiment1_plate.py
"""

import random
from collections import Counter

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

# 96 孔板：横向 1-12，纵向 A-H
COLS = list(range(1, 13))
ROWS = [chr(ord("A") + i) for i in range(8)]  # A-H
TOTAL = len(COLS) * len(ROWS)  # 96

# 板数量与样品数量
PLATE_COUNT = 4
SAMPLE_COUNT = 11
# 每种内容至少出现的次数，其余随机填满
MIN_EACH = 4

# 需要随机填充的内容：空白对照 + 样品1-ID ~ 样品N-ID
LABELS = ["空白对照"] + [f"样品{i}-ID" for i in range(1, SAMPLE_COUNT + 1)]
assert MIN_EACH * len(LABELS) <= TOTAL, "MIN_EACH 太大，放不下 96 个孔"

# 样式
header_fill = PatternFill("solid", fgColor="4472C4")
header_font = Font(color="FFFFFF", bold=True)
center = Alignment(horizontal="center", vertical="center")
thin = Side(style="thin", color="BFBFBF")
border = Border(left=thin, right=thin, top=thin, bottom=thin)


def fill_sheet(ws, plate_no: int) -> Counter:
    """在 ws 上画一块 96 孔板，返回各标签计数。"""
    # 先保证每种至少 MIN_EACH 个，剩余随机填充
    values = LABELS * MIN_EACH
    values += [random.choice(LABELS) for _ in range(TOTAL - len(values))]
    random.shuffle(values)

    # 左上角标题
    top_left = ws.cell(row=1, column=1, value=f"细胞培养板{plate_no}ID")
    top_left.fill = header_fill
    top_left.font = header_font
    top_left.alignment = center
    top_left.border = border

    # 顶部列号 1-12
    for j, c in enumerate(COLS, start=2):
        cell = ws.cell(row=1, column=j, value=c)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = center
        cell.border = border

    # 行标题 A-H + 填充内容
    idx = 0
    for i, r in enumerate(ROWS, start=2):
        rc = ws.cell(row=i, column=1, value=r)
        rc.fill = header_fill
        rc.font = header_font
        rc.alignment = center
        rc.border = border
        for j in range(2, len(COLS) + 2):
            cell = ws.cell(row=i, column=j, value=values[idx])
            cell.alignment = center
            cell.border = border
            idx += 1

    # 列宽
    ws.column_dimensions["A"].width = 14
    for j in range(2, len(COLS) + 2):
        ws.column_dimensions[ws.cell(row=1, column=j).column_letter].width = 12

    return Counter(values)


def main() -> None:
    wb = Workbook()
    default_ws = wb.active

    for plate_no in range(1, PLATE_COUNT + 1):
        ws = wb.create_sheet(title=f"细胞培养板{plate_no}")
        counts = fill_sheet(ws, plate_no)
        print(f"细胞培养板{plate_no}:")
        for label, n in sorted(counts.items()):
            print(f"  {label}: {n}")

    wb.remove(default_ws)
    out = "实验一细胞培养板.xlsx"
    wb.save(out)
    print("已生成:", out)


if __name__ == "__main__":
    main()
