---
name: RNA浓度检测提取脚本
overview: 编写一个 Python 脚本，读取 BioTek Synergy H1 荧光酶标仪 CSV（96 孔 RFU）与孔板图 xlsx，按样本聚合并经一个可替换的标准曲线函数换算成 RNA 浓度，按报告文档格式输出 RNA浓度检测 CSV。
todos:
  - id: scaffold
    content: 在 unilabos/script/rna_concentration.py 搭建 CLI 骨架（argparse + 中文 docstring，仿 probe_work_flow_list.py）
    status: pending
  - id: parse-fluor
    content: 解析 BioTek CSV：定位 Results、读 8×12 RFU 网格（丢弃末列滤光片标记）、抓元信息
    status: pending
  - id: parse-platemap
    content: 解析 细胞培养板.xlsx，建立 孔位→样本标签 映射
    status: pending
  - id: calc
    content: 实现可替换的占位换算函数 fluor_to_concentration + 空白扣除
    status: pending
  - id: aggregate-output
    content: 按样本聚合复孔（均值/SD/n），写出 rna_concentration.csv（仅 Sample Name + Nucleic Acid(ng/uL)，不含 A260 列）
    status: pending
  - id: verify
    content: 用提供的两份文件试跑，检查输出行数/样本对应/占位浓度合理
    status: pending
  - id: qpcr-scaffold
    content: 在 unilabos/script/qpcr_plate_curves.py 搭建 CLI 骨架（argparse + 中文 docstring）
    status: pending
  - id: qpcr-parse-xml
    content: iterparse 解析 XML：建样本号→孔位映射 + 每孔前 45 循环扩增荧光
    status: pending
  - id: qpcr-mapping
    content: 实现 96→384 区块映射，给每个 384 孔打上 样本名 + target/reference + 复孔号
    status: pending
  - id: qpcr-output
    content: 写 xlsx：板排布 sheet + 扩增曲线 sheet(数据表 + 嵌入 openpyxl 原生折线图)
    status: pending
  - id: qpcr-verify
    content: 试跑校验：A1/B1=target、A2/B2=reference，样品9-ID 落在左上角 2×2
    status: pending
  - id: stat-scaffold
    content: 在 unilabos/script/qpcr_stats.py 搭建 CLI（或并入 qpcr_plate_curves.py），复用 96→384 映射
    status: pending
  - id: stat-ct-source
    content: 实现可插拔 CT 来源 get_ct(well)：--ct-table / 自算Cq / 占位，缺扩增时标 Undetermined
    status: pending
  - id: stat-compute
    content: 复刻公式 ΔCT=目的-内参、Ave.Blank、ΔΔCT、2^(-ΔΔCT)、Mean、SD(n-1)
    status: pending
  - id: stat-output
    content: 输出 qPCR统计与分析 sheet：前置「位置」列(目的孔 内参孔)，每样本2复孔布局，写公式
    status: pending
isProject: false
---

## 目标
仅实现「RNA浓度检测」一项。输入是荧光酶标仪数据 + 孔板图，输出仿照报告文档格式的 CSV。

## 输入
- 荧光数据：`/Users/dp/python/Uni-Lab-OS-sirna/data/test0420_260604_124254.csv`（BioTek Synergy H1，96 孔，Ex485/Em528）。结构：前置元信息（Software Version / Date / Reader 等），`Results` 段后是表头 `\t1..12`，随后 8 行 `A..H` 的 RFU 值（每行末尾还有一个 `485,528` 滤光片标记列，需丢弃）。
- 孔板图：`/Users/dp/python/Uni-Lab-OS-sirna/data/细胞培养板.xlsx`（8×12，单元格为「空白对照」或「样品N-ID」，N=1..11，由 `data/gen_plate.py` 生成）。

## 输出
- 文件：`rna_concentration.csv`（路径用 `--out` 指定，默认放 `data/` 下）。
- 列：`Sample Name, Nucleic Acid(ng/uL)`（**不再输出 A260/A280、A260/A230**，荧光法测不到，按你确认去掉）。保留顶部 `录入者签名及日期 / 复核人签名及日期 / 电子数据存储位置` 注释行。
- 每个样本一行（空白对照 + 样品1..11），浓度取复孔均值；附加 `n复孔`、`SD` 作为额外列。

## 脚本设计
- 位置/风格：`/Users/dp/python/Uni-Lab-OS-sirna/unilabos/script/rna_concentration.py`，仿现有 [unilabos/script/probe_work_flow_list.py](/Users/dp/python/Uni-Lab-OS-sirna/unilabos/script/probe_work_flow_list.py)：`argparse` CLI、中文 docstring、依赖最小（`openpyxl` 读孔板图，标准库 `csv`）。
- 参数：`--fluor-csv`、`--plate-map`、`--out`、标曲参数 `--slope`、`--intercept`、空白处理 `--blank-mode`（subtract/none）。
- 处理流程：
  1. 解析 BioTek CSV：定位 `Results`，读 8×12 RFU 网格（丢弃末列滤光片标记），同时抓 Date/Reader/Ex-Em 作为注释。
  2. 解析孔板图 xlsx：建立 `孔位(A1..H12) → 标签` 映射。
  3. 按孔位 join：每孔 → (标签, RFU)。
  4. 计算空白均值并按 `--blank-mode` 扣除。
  5. 调用**可替换的占位换算函数** `fluor_to_concentration(rfu_blank_corrected, slope, intercept)`（默认线性：`conc = slope*x + intercept`），单独成函数、注释清楚“占位，待真实标曲替换”。
  6. 按样本标签聚合复孔（均值 + SD + n）。
  7. 写出 CSV（注释行 + Sample Name + Nucleic Acid(ng/uL)，无 A260 列）。

## 待你后续提供 / 确认
- 真实的荧光→浓度标准曲线（斜率/截距或标准品数据），替换占位函数。
- 复孔是否按样本取均值（当前默认是）。

---

# 第二部分：qPCR板排布及扩增曲线

## 目标
解析 384 孔 qPCR XML，结合 96 孔细胞培养板的样本身份，输出带样本/基因标注的板排布 + 每孔逐循环扩增曲线（CSV）。

## 输入
- qPCR 原始数据：`/Users/dp/python/Uni-Lab-OS-sirna/data/场景二实验数据-qpcr20260520181417.xml`（Roche LightCycler 480，384 孔，SYBR Green）。
  - `<analyses>/<AnalysisSample>/{name=Sample N, Position}`：样本号↔孔位（已核实**行优先**：Sample1=A1、Sample2=A2、…、Sample24=A24、Sample25=B1）。
  - `<Acquisitions>/<Sample Number=N>/<Acq Number=k>/<Chan>/Fluor`：**第 1–45 点 = 扩增（恒温~60°C 逐循环）**，第 46 点起 = 熔解。协议 `amplification` Cycles=45。
- 孔板图（样本身份来源）：`/Users/dp/python/Uni-Lab-OS-sirna/data/细胞培养板.xlsx`（96 孔 8×12，单元格为「空白对照」或「样品N-ID」）。

## 96 孔 → 384 孔 映射规则（用户提供，已确认）
- 96 孔 (R 行 1..8, C 列 1..12) → 384 孔 2×2 区块：行 = 2R-1, 2R；列 = 2C-1, 2C。
- 例：96 孔 A1（样品9-ID）→ 384 的 A1、B1、A2、B2。
- **左列（奇数列 2C-1）= target / 目的基因**（2 复孔：行 2R-1 与 2R）。
- **右列（偶数列 2C）= reference / 内参基因**（2 复孔：行 2R-1 与 2R）。
- 据此每个 384 孔可标注：`样本名（样品N-ID/空白对照）+ 基因(target/reference) + 复孔号(1/2)`。

## 输出（默认写到 data/，`--out-dir` 可改）
- 主输出：**单个 xlsx**`qpcr_plate_and_curves.xlsx`，仿报告含多 sheet：
  - sheet「板排布」：16×24（A–P × 1–24）网格，单元格 = `样本名|target/reference`。
  - sheet「扩增曲线」：数据表（每个 384 孔一行：`Position, 96孔源, 样本名, 基因(target/ref), 复孔号, Cycle_1..Cycle_45` 荧光）+ **嵌入的折线图**（X=循环 1..45，Y=荧光，每孔一条曲线），图直接插入该 sheet。
- 绘图用 **matplotlib 生成 png 静态图**，再经 openpyxl `drawing.image.Image` 插入「扩增曲线」sheet（**已确认方案**）。
  - **画全部 384 孔曲线**（X=循环 1..45，Y=荧光）；为避免过密，按 target/reference 用不同颜色、细线 + 半透明，图例从简（或不放完整图例）。
  - png 同时落盘到 `--out-dir`（如 `qpcr_amp_curves.png`）便于单独查看。
- 新增依赖（需安装）：`matplotlib`、`pillow`（openpyxl 插图依赖 Pillow）。
- 可选 `--also-csv` 额外导出 CSV；`--with-melt` 增加「熔解曲线」sheet。

## 脚本设计
- 位置/风格：`/Users/dp/python/Uni-Lab-OS-sirna/unilabos/script/qpcr_plate_curves.py`，仿 [unilabos/script/probe_work_flow_list.py](/Users/dp/python/Uni-Lab-OS-sirna/unilabos/script/probe_work_flow_list.py)：`argparse` CLI、中文 docstring；`xml.etree.ElementTree`（iterparse 流式，文件 17MB）+ `openpyxl`（读孔板图、写 xlsx、插图）+ `matplotlib`（绘 png）。
- 参数：`--xml`、`--plate-map`、`--out-dir`、`--cycles`（默认 45）、`--with-melt`、`--also-csv`。
- 处理流程：
  1. iterparse 解析 `<analyses>` 建 `样本号→孔位`，反推 `孔位→样本号`。
  2. iterparse 遍历 `<Acquisitions>`，每孔取前 45 个 Acq 的 Fluor 作扩增曲线（按需收集熔解）。
  3. 读 96 孔板图，按映射规则给每个 384 孔打 `样本名 + target/reference + 复孔号`。
  4. matplotlib 画全部 384 孔扩增曲线 → 存 png；openpyxl 写 xlsx：板排布 sheet（网格）+ 扩增曲线 sheet（数据表 + 用 `Image` 插入 png）。（+ 可选熔解 sheet、可选 CSV）。

## 已解决 / 不再缺失
- 样本身份：由 96 孔板图 + 映射规则补齐（之前的主要缺口已解决）。

## 仍需注意 / 待确认
- 荧光为原始值（XML 无基线校正后数据）；如需基线校正后续再加。
- 扩增曲线图：matplotlib 画全部 384 孔（target/reference 配色区分）→ png 插入 Excel。需先 `pip install matplotlib pillow`。
- CT/Cq 与 ΔΔCT 统计属第三部分（qPCR统计与分析），本部分不含。

---

# 第三部分：qPCR统计与分析

## 目标
生成 qPCR统计与分析表，结构沿用报告「公式显示 / qPCR统计与分析」sheet（公式已逐格解码），并在最前面新增「位置」列。

## 已解码的表结构与公式（来自 xlsx，可完全复刻）
- 列：`样本 | 内参基因(CT) | 目的基因(CT) | ΔCT | Ave.Blank | ΔΔCT | 2^(-ΔΔCT) | Mean | SD`，每样本占 2 行（2 复孔）。
- `ΔCT = 目的 − 内参`（`D=C-B`）
- `Ave.Blank = AVERAGE(空白对照组的 ΔCT)`（`E5=AVERAGE(D5:D6)`，全表用 `$E$5`）
- `ΔΔCT = ΔCT − Ave.Blank`（`F=D-$E$5`）
- `2^(-ΔΔCT)`（`G=2^(-F)`）
- `Mean = AVERAGE(每样本各复孔的 2^-ΔΔCT)`、`SD = STDEV(...)`（样本标准差 n-1）

## 新增「位置」列（按用户要求）
- 放在最前面一列。每行 = 一个复孔对（目的孔 + 内参孔，同一 384 行的奇/偶相邻列）。
- 取值形如 `A1 A2`（目的孔 内参孔，空格分隔，对应用户示例 "A3 G5" 的格式）。
- 来源：96→384 映射（见第二部分），rep1=384 行 2R-1，rep2=384 行 2R。

## CT（Cq）计算方式 —— 阈值法（用户已确认）
- 定义：在扩增曲线图上画一条水平阈值线（报告里是 0.318599，本数据用原始荧光，**默认阈值=3**，`--threshold` 可调）。
  **每个孔的曲线从下往上穿过阈值线的交点对应的横轴循环数 = 该孔 CT。**
- 算法 `ct_threshold(vals, thr)`：
  - 在扩增段（第 1–45 循环）逐点找首个满足 `vals[i-1] < thr <= vals[i]` 的区间；
  - 线性插值 `CT = i + (thr - vals[i-1]) / (vals[i] - vals[i-1])`（1-based 循环号）；
  - 无交点 → `Undetermined`；起点即 ≥ 阈值的情况按 CT≈1 处理并在日志标注。
- 每个 384 孔都算 CT；目的基因孔(target)、内参基因孔(reference) 各取自己的 CT。
- 本数据实测（阈值=3）：173 孔可定 CT、211 孔 Undetermined，且多为 CT≈1（因无真扩增，曲线在 2~3.5 平噪）。**方法正确，待真扩增数据即可得正常 CT。**

## 分组（来自 96→384 映射 + 96 孔板图）
- 目的=左列(奇)、内参=右列(偶)，各 2 复孔。
- 空白对照 = 96 孔板图中标「空白对照」的细胞 → 映射到对应 384 孔。
- 注意：本实验「空白对照」在 96 孔板里出现多次，复孔数会多于报告的 2 个；`Ave.Blank` 取全部空白 ΔCT 的均值。

## 「位置」列与表布局（结合阈值法）
- 每行 = 一个复孔对：目的孔(target) + 内参孔(reference)。位置列形如 `A1 A2`（目的孔 内参孔）。
- B 列内参 CT = 该内参孔阈值法 CT；C 列目的 CT = 该目的孔阈值法 CT；其余按已解码公式。
- CT 为 Undetermined 时该格写 `Undetermined`，对应 ΔCT 及下游留空。

## 输出
- 写入同一个 `qpcr_plate_and_curves.xlsx`，新增 sheet「qPCR统计与分析」（或单独 xlsx，`--out` 控制）。
- 用 openpyxl 写 Excel **公式**（贴合报告，Excel 里可见公式）；CT 列写数值。

## 备注
- CT 算法（阈值法 + 默认阈值 3）已确认，可直接实现；本数据因无扩增结果多为 Undetermined/≈1，换真扩增数据即正常。