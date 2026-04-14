Reference: see `Uni-Lab-OS/drivers_phage/00_project_rules.md`.

# Phage Protocol Device Update

## 1. Concise Summary Table

| Bundle | Suggested device | Availability class | Alternatives | Required actions | Follow-up needed |
|---|---|---:|---|---|---|
| Sample preparation and liquid handling | Hamilton Vantage自动化移液工作站 (`vantage_backend`) | (a) | `star_backend`, `li_ha`, `liquid_handler.prcxi` | `pick_up_tips`, `aspirate`, `dispense`, `mix`, `move_plate` | No |
| Incubation, cooling, and shaking | Cytomat微孔板自动培养存储系统 (`cytomat_backend`) | (a) | `incubator_shaker_stack`, `bio_shake`, `incubator` | `set_temperature`, `fetch_plate`, `shake`, storage/hotel actions | No |
| Centrifugation | Hettich ROTANTA 460 Robotic (`hettich_rotanta_460_robotic`, max `6446 x g`) | (b) | `eppendorf_centrifuge_5910_ri`, `v_spin_backend` | `load`, `spin`, `unload`, door interlocks | Deep search + guessed driver; confirm the draft `8000 x g` step can be adjusted |
| Sealing, unsealing, plate washing, bulk dispensing | Agilent BioTek 406 FX Washer Dispenser (`agilent_biotek_406_fx`) | (b) | `sealer`, `peeler`, `vantage_backend` | `seal`, `peel`, `wash_plate`, `bulk_dispense` | Deep search + guessed driver |
| Fluorescent staining and flow cytometry | BD FACSMelody Cell Sorter (`bd_facsmelody`) | (c) | manual flow sorting service | `set_gate`, `sort_cells`, `record_sort_count`, `export_fcs` | Deep search + guessed driver |
| Phage recovery: acid elution, infection, amplification | Hamilton Vantage自动化移液工作站 (`vantage_backend`) + Cytomat微孔板自动培养存储系统 (`cytomat_backend`) | (a) | `star_backend`, `bio_shake` | `dispense`, `mix`, chilled hold, `shake`, warm incubation | No |
| Sterile filtration (`0.22 um`) | Tecan Resolvex A200 positive-pressure workstation (`tecan_resolvex_a200`) | (c) | manual sterile filtration | `filter`, pressure profile control, endpoint handling | Deep search + guessed driver |
| Colony picking and monoclonal screening | Molecular Devices QPix 420 colony picking system (`molecular_devices_qpix_420`) | (c) | `molecular_devices_backend`, manual colony picking | `image_plate`, `select_colony`, `pick_colony`, `inoculate_clone` | Deep search + guessed driver |
| ELISA plate reading | Molecular Devices 多功能酶标仪 (`molecular_devices_backend`) | (a) | `bio_tek_plate_reader_backend`, `clari_ostar_backend`, `plate_reader` | `read_absorbance`, `read_fluorescence`, tray control | No |
| Plasmid extraction and DNA sequencing | QIAGEN QIAcube Connect (`qiagen_qiacube_connect`) + Applied Biosystems SeqStudio Genetic Analyzer (`applied_biosystems_seqstudio_genetic_analyzer`) | (c) | manual miniprep, external Sanger service | `extract_plasmid`, `run_sequence`, `export_sequence` | Deep search + guessed drivers |
| Vector construction and transformation | Telesis Bio BioXp 3250 automated construct assembly workstation (`telesis_bio_bioxp_3250`) | (c) | `vantage_backend` | `assemble_vector`, `track_construct`, `transform_cells` | Deep search + guessed driver |
| Protein expression, purification, affinity/specificity validation | Cytiva AKTA pure automated protein purification system (`cytiva_akta_pure`) + Cytiva Biacore 8K+ high-throughput SPR affinity analyzer (`cytiva_biacore_8k_plus`) | (c) | manual chromatography, outsourced affinity testing | `load_sample`, `wash_column`, `elute_fraction`, `measure_binding`, `compare_binding` | Deep search + guessed drivers |
| Automation support | SCARA 机械臂滑轨系统 (`robotic_arm.SCARA_with_slider.moveit.virtual`) | (b) | `vantage_backend`, `dexarm`, `cytomat_backend` | `pick_and_place`, `set_position`, multi-station transfer, hotel handoff | Local registry entry; confirm deployment readiness outside the virtual/MoveIt stack |

### Device Catalog

| available_status | device_id | semantic_name | description |
|---|---|---|---|
| available | `access2_backend` | Agilent VSpin 微孔板离心机 | 一款用于实验室自动化系统的微孔板离心机，可对样品板进行装载、卸载、开关门、锁定转篮并执行离心运行。常用于微孔板样品的短时离心、收集液滴和自动化流程中的板级前处理。 |
| proposed | `agilent_biotek_406_fx` | Agilent BioTek 406 FX Washer Dispenser | Guessed driver for integrated microplate washing and bulk dispensing on a BioTek 406 FX-class workstation. |
| proposed | `applied_biosystems_seqstudio_genetic_analyzer` | Applied Biosystems SeqStudio Genetic Analyzer | Guessed high-level driver for the Applied Biosystems SeqStudio Genetic Analyzer. |
| proposed | `bd_facsmelody` | BD FACSMelody Cell Sorter | Guessed high-level driver for the BD FACSMelody fluorescence-activated cell sorter. |
| available | `bio_shake` | BioShake微孔板加热振荡器 | 这是一种实验室微孔板加热振荡器，可对板式样品进行控温并同时振荡混匀。它常用于样品孵育、反应混合、酶反应、核酸与蛋白相关实验等需要稳定温度和持续摇匀的流程，部分机型还支持锁板与主动冷却功能。 |
| available | `bio_tek_plate_reader_backend` | Agilent BioTek 多功能微孔板读板仪 | 这是一种台式微孔板读板仪，用于对微孔板中的样品进行吸光度、荧光和发光检测。设备通常带有载板抽屉、温度控制和振荡功能，适用于生化分析、细胞实验和高通量筛选等实验室检测工作。 |
| available | `centrifuge` | 实验室离心机 | 实验室离心机利用离心力分离液体样品中的不同组分，常用于样品沉降、相分离和前处理。该设备具备舱门控制、转篮/吊篮位置切换，以及按设定离心力和时间执行离心循环的能力，可用于自动化样品处理流程。 |
| available | `clari_ostar_backend` | CLARIOstar多功能酶标仪 | CLARIOstar 是一款多功能微孔板读板仪，可对微孔板样品进行吸光度、荧光和发光检测，常用于生化分析、细胞实验、药物筛选和其他高通量实验。 |
| available | `cytomat_backend` | Cytomat微孔板自动培养存储系统 | 这是一种用于微孔板自动存取、培养和转运的实验室培养存储设备，带有内部存储位、传送位和外部暴露位，可控制温度、CO2、湿度和O2，并支持振荡和条码读取。常用于细胞培养、高通量筛选和自动化工作站中的板管理。 |
| proposed | `cytiva_akta_pure` | Cytiva AKTA pure automated protein purification system | Guessed driver for a Cytiva AKTA pure class chromatography system used for affinity purification of phage-derived proteins. |
| proposed | `cytiva_biacore_8k_plus` | Cytiva Biacore 8K+ high-throughput SPR affinity analyzer | Guessed driver for a Cytiva Biacore 8K+ class SPR instrument used for affinity and specificity validation of purified binders. |
| proposed | `eppendorf_centrifuge_5910_ri` | Eppendorf Centrifuge 5910 Ri | Guessed driver for an Eppendorf Centrifuge 5910 Ri class centrifuge used for both low-speed cell pelleting and refrigerated high-speed clarification. |
| proposed | `hettich_rotanta_460_robotic` | Hettich ROTANTA 460 Robotic | Guessed high-level driver for the Hettich ROTANTA 460 Robotic automated centrifuge. |
| available | `incubator` | 自动微孔板培养箱 | 一种用于存放并温控培养微孔板或培养板的自动化实验室培养箱，通常具有多个板位、装载托盘和可开闭门机构，可在设定温度下进行样品孵育，并支持板件取放与振荡混匀。常用于细胞培养、酶反应、样品保温和自动化流程中的板式孵育步骤。 |
| available | `incubator_shaker_stack` | INHECO孵育振荡器堆叠系统 | 由多个可堆叠孵育振荡单元组成的实验室自动化设备，带有装载托盘，可对样品或微孔板进行温度控制孵育与振荡混匀，常用于自动化培养、反应孵育和样品处理流程。 |
| available | `li_ha` | Tecan Freedom EVO 自动化液体处理工作站 | 一款用于实验室自动移液与板件搬运的台式自动化工作站，通常配备多通道 LiHa 液体处理臂，可完成吸液、分液、液面探测、一次性吸头装卸及多轴定位，并可结合 96 通道或板搬运模块执行样品制备、微孔板加样和高通量实验流程。 |
| available | `liquid_handler.prcxi` | PRCXI 液体处理工作站 | Copied from `unilabos/devices/liquid_handling/prcxi/`. This local available device does not ship an `info.txt` file like the other available-driver folders, so the semantic label here follows the report/CSV naming instead of `info.txt`. |
| available | `molecular_devices_backend` | Molecular Devices 多功能酶标仪 | 这是一种用于读取微孔板样品信号的多功能酶标仪，可进行吸光度、荧光、化学发光、荧光偏振和时间分辨荧光检测。设备通常用于生化分析、细胞实验、免疫检测、酶活性测定和高通量筛选，并支持控温与振荡等板上实验条件控制。 |
| proposed | `molecular_devices_qpix_420` | Molecular Devices QPix 420 colony picking system | Guessed high-level driver for the Molecular Devices QPix 420 microbial colony picker. |
| available | `peeler` | 微孔板揭膜机 | 用于自动剥离微孔板顶部封膜或粘性封板膜的实验室设备，常用于样品处理和自动化工作流中，在后续移液、读板或分析前打开微孔板。 |
| available | `plate_reader` | 多功能酶标仪 | 用于读取微孔板中各孔的吸光度、荧光和发光信号的实验仪器，常用于生化分析、细胞实验和板式检测流程。 |
| proposed | `qiagen_qiacube_connect` | QIAGEN QIAcube Connect | Guessed high-level driver for the QIAGEN QIAcube Connect plasmid-prep station. |
| available | `robotic_arm.SCARA_with_slider.moveit.virtual` | SCARA 机械臂滑轨系统 | 机械臂与滑块运动系统，基于MoveIt2运动规划框架的多自由度机械臂控制设备。该系统集成机械臂和线性滑块，通过ROS2和MoveIt2实现精确的轨迹规划和协调运动控制。支持笛卡尔空间和关节空间的运动规划、碰撞检测、逆运动学求解等功能。适用于复杂的pick-and-place操作、精密装配、多工位协作等需要高精度多轴协调运动的实验室自动化应用。 |
| available | `sealer` | 微孔板封膜机 | 用于实验室自动化中对微孔板进行封膜的设备，可执行封膜、开合机构控制，以及温度设置与读取。 |
| available | `star_backend` | Hamilton STAR液体处理工作站 | Hamilton STAR 自动化液体处理平台，用于实验室吸液、分液、装卸吸头、液位探测、板与载架搬运，并可控制 iSWAP、CoRe 96 头、autoload 载架装载及部分连接的加热冷却模块。 |
| proposed | `tecan_resolvex_a200` | Tecan Resolvex A200 positive-pressure workstation | Guessed high-level driver for the Tecan Resolvex A200 used for automated filter-plate processing. |
| proposed | `telesis_bio_bioxp_3250` | Telesis Bio BioXp 3250 automated construct assembly workstation | Guessed driver for a Telesis Bio BioXp 3250 class workstation used for automated construct assembly and cloning-class workflows. |
| available | `v_spin_backend` | 安捷伦 VSpin 离心机 | 用于实验室样品离心处理的自动化离心机，可进行转子位置控制、开关门、门锁与桶锁控制，并按设定的相对离心力或转速执行离心程序。 |
| available | `vantage_backend` | Hamilton Vantage自动化移液工作站 | Hamilton Vantage 自动化移液工作站，用于实验室自动移液、吸头装卸、液体吸取与分配、96通道板级处理、液面探测，以及通过集成夹爪搬运微孔板等实验耗材。 |

## 2. Protocol Overview

The protocol should be executed as an automation-first station chain rather than as a literal replay of the draft tube/flask layout:

1. Preprocess positive and negative cells plus the phage library on the liquid handler, then shift into chilled plate/deep-well incubation.
2. Run negative and positive selections in plate-compatible carriers so incubator, shaker, and centrifuge handoffs stay robotic.
3. Perform fluorescent staining on the liquid handler; only move into a sorter-compatible carrier at the final staging step.
4. Sort the highest-binding cell fraction on a dedicated FACS station and recover the sorted material into chilled collection carriers.
5. Elute, neutralize, infect, and amplify using the existing liquid-handler and incubator stack; rewrite the flask amplification step into deep-well or automation-friendly culture blocks where possible.
6. Sterile-filter recovered phage through a dedicated filtration station before deciding whether another enrichment loop is required.
7. Plate for clone isolation, pick monoclonal colonies automatically, and rescreen them with local plate-reader support and, when needed, the same FACS station.
8. Extract plasmids, sequence inserts, assemble expression constructs, purify the protein product, and validate affinity/specificity on dedicated downstream stations that are currently missing locally.

## 3. Per-Bundle Analysis

### Bundle 1: Sample preparation and liquid handling

`vantage_backend` wins because it combines deep pipetting support with integrated gripper operations in a locally validated driver. `star_backend` and `li_ha` remain credible alternatives, but Vantage gives the cleanest single backbone for both fluid work and deck logistics.

### Bundle 2: Incubation, cooling, and shaking

`cytomat_backend` is the strongest local fit because its metadata explicitly includes temperature, `CO2`, humidity, `O2`, shaking, barcode, and storage/exposed-position moves. That is a closer match to the chilled cell-binding stages than a simple thermoshaker. `incubator_shaker_stack` and `bio_shake` remain useful alternates for stripped-down plate incubation.

### Bundle 3: Centrifugation

The draft protocol contains repeated low-speed `500 x g` tube spins plus a refrigerated `8000 x g` clarification step before filtration. `Hettich ROTANTA 460 Robotic` is now the suggested automation-first centrifuge because its public robotics story is much stronger than the benchtop-style alternatives: robotic hatch access, positioning modes, and integration-oriented design are all first-party documented. The key caveat is that the best first-party evidence only reaches `6,446 x g`, so the draft's explicit `8000 x g` recovery spin is not covered as written. That means this recommendation assumes the recovery-step speed can be adjusted experimentally or operationally. `Eppendorf Centrifuge 5910 Ri` remains the cleaner fallback when the `8000 x g` requirement must be preserved exactly, and `v_spin_backend` remains the local automation-oriented alternative.

### Bundle 4: Sealing, unsealing, plate washing, bulk dispensing

Local coverage is asymmetric: `sealer` and `peeler` are present, but a real plate washer is not. That is why the bundle recommendation shifts to `Agilent BioTek 406 FX` as the best unavailable upgrade, while keeping the local sealer/peeler and liquid handler as fallback pieces.

### Bundle 5: Fluorescent staining and flow cytometry

Only the reagent-addition part is locally covered. Real gating, sorting, and event export are absent, so the bundle needs a dedicated sorter. `BD FACSMelody` was chosen as the web-backed recommendation because it squarely covers the missing sorting workflow and is backed by strong vendor documentation.

### Bundle 6: Phage recovery, infection, amplification

No new station is required here if we accept an automation-first vessel rewrite. `vantage_backend` handles the liquid chemistry, and `cytomat_backend` handles the incubation and shaking side. This bundle is therefore available, but only as a deliberate multi-device workflow rather than as a single magic instrument.

### Bundle 7: Sterile filtration

The local inventory only exposes a virtual filter, so physical sterile filtration remains a real gap. `Tecan Resolvex A200` is the recommended unavailable station because it is purpose-built for automated positive-pressure processing and is easier to integrate into robotic plate/deep-well workflows than manual syringe filtering.

### Bundle 8: Colony picking and monoclonal screening

Automated colony picking is missing. `Molecular Devices QPix 420` closes that gap directly by handling colony imaging, selection, transfer, and tracking. Local plate readers still help with downstream screening, but they do not solve the picking bottleneck.

### Bundle 9: ELISA plate reading

This is one of the healthier local areas. `molecular_devices_backend` gives the best readout coverage and also exposes temperature/shaking functions that make it more versatile for follow-up clone assays. The main ELISA weakness sits upstream at plate washing, not at detection.

### Bundle 10: Plasmid extraction and DNA sequencing

The repo only partially hints at plasmid purification through protocol-capable liquid handlers, and it has no sequencer driver. The best clean split is `QIAcube Connect` for automated extraction and `SeqStudio Genetic Analyzer` for clone confirmation.

### Bundle 11: Vector construction and transformation

No local construct-assembly station exists. `BioXp 3250` is the best unavailable candidate because it directly targets automated construct generation and cloning-class workflows rather than acting as a generic liquid handler.

### Bundle 12: Protein expression, purification, and affinity/specificity validation

Local incubation hardware can support expression culture in an automation-friendly format, but the purification and binding-validation instruments are missing. The clean downstream pair is `ÄKTA pure` for purification and `Biacore 8K+` for SPR-based affinity/specificity validation.

### Bundle 13: Automation support

The workflow now benefits from a standalone transport layer because the device layout is no longer assumed to sit inside one tightly coupled workstation envelope. `robotic_arm.SCARA_with_slider.moveit.virtual` is the strongest local automation-support reference because it explicitly combines a robot arm with a linear slider and MoveIt-based motion planning, which is a better match for multi-station handoff than a fixed benchtop arm. `vantage_backend` still matters as an integrated local transfer backbone inside the liquid-handling station, and `cytomat_backend` remains the best hotel/storage component, but the SCARA-with-slider entry is the clearest top-level answer for cross-device reach.

## 4. Unavailable Devices Summary

The following unavailable devices need deep search folders and guessed drivers:

- `agilent_biotek_406_fx`
- `bd_facsmelody`
- `tecan_resolvex_a200`
- `molecular_devices_qpix_420`
- `qiagen_qiacube_connect`
- `applied_biosystems_seqstudio_genetic_analyzer`
- `telesis_bio_bioxp_3250`
- `cytiva_akta_pure`
- `cytiva_biacore_8k_plus`
- `eppendorf_centrifuge_5910_ri`
- `hettich_rotanta_460_robotic`
