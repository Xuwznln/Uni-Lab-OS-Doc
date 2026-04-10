# Phage Display Device Mesh Rollout Prompts

This document contains:

1. A universal worker prompt for `_phage_display` device mesh work
2. A per-device adapted prompt for each driver under `Uni-Lab-OS/unilabos/devices/_phage_display`

Reference assets:

- Example mesh package: `/home/xzye/projects/DPTech/LeapLab/Uni-Lab-OS/unilabos/device_mesh/devices/hamilton_vantage/`
- Fallback STL helper: `/home/xzye/projects/DPTech/LeapLab/Uni-Lab-OS/unilabos/device_mesh/tools/generate_box_stl.py`

## Universal Prompt

```text
You are responsible for exactly one `_phage_display` device in Uni-Lab-OS. Use web search to find a usable 3D representation for the instrument, then wire it into Uni-Lab-OS.

Goals
1. Inspect the target driver file and identify the decorated `@device(...)` class and device id.
2. Search the web for the exact instrument or the best representative physical model.
   - First preference: downloadable `.stl`
   - Second preference: downloadable `.glb` plus `.stl` conversion or paired assets
   - Third preference: an existing `.xacro` / URDF / CAD model we can adapt
   - Fallback: collect reliable external dimensions and visible opening / loading / tray / door access locations from manuals, brochures, product figures, or videos, then build a simple box-based `.stl` and `macro_device.xacro` from those measurements.
3. Add or update a device mesh package under `Uni-Lab-OS/unilabos/device_mesh/devices/<mesh_folder>/`.
4. Update the target driver’s `@device(...)` decorator to include:
   model={
     "type": "device",
     "mesh": "<mesh_folder>",
   },
5. Do not touch other drivers or other workers’ files.

Required outputs
- `Uni-Lab-OS/unilabos/device_mesh/devices/<mesh_folder>/macro_device.xacro`
- `Uni-Lab-OS/unilabos/device_mesh/devices/<mesh_folder>/meshes/<...>.stl`
- optional `.glb` if you found a good one and it is easy to keep
- `Uni-Lab-OS/unilabos/device_mesh/devices/<mesh_folder>/meta.json`
- updated target driver file with the `model` block in `@device(...)`

`macro_device.xacro` requirements
- Match the style of `hamilton_vantage/macro_device.xacro`
- Define a macro named `<mesh_folder>`
- Support params:
  `parent_link`, `station_name`, `device_name`, `x`, `y`, `z`, `rx`, `ry`, `r`, `mesh_path`
- Include a fixed base link and visual + collision geometry
- If using fallback dimensions, expose `width`, `depth`, `height` params with sensible defaults
- Include access-point frames as fixed child links / joints on the base link when applicable
  Examples: front door, loading tray, plate slot, carousel opening, pipetting deck opening
- Add short XML comments explaining any assumptions

`meta.json` requirements
- Keep `fileName` and `related`
- Also include:
  - `model_strategy`: `downloaded_model` or `fallback_box`
  - `sources`: array of URLs used
  - `dimensions_m`: width / depth / height in meters
  - `access_points`: array with `name`, `description`, `face`, `center_m`, `size_m` when known
  - `notes`: brief assumptions / uncertainty

Research rules
- Prefer vendor manuals / official specs for dimensions
- Prefer openly accessible CAD / mesh sources when available
- Record exact URLs in `meta.json`
- If a source is ambiguous, say so in `notes`
- If the driver is generic rather than vendor-specific, choose the closest representative instrument matching the class description and record that assumption

Fallback STL rules
- If you cannot obtain a usable downloadable mesh, call:
  `python3 /home/xzye/projects/DPTech/LeapLab/Uni-Lab-OS/unilabos/device_mesh/tools/generate_box_stl.py --width <m> --depth <m> --height <m> --output <path> --name <solid_name>`
- Keep all opening / tray / door semantics in xacro access-point frames and in `meta.json`, even if the STL itself is only a box
- Keep geometry units in meters

Implementation rules
- You are not alone in the codebase. Do not revert unrelated changes and do not edit files outside your ownership.
- Keep ownership to:
  - the target driver file
  - the new or existing device mesh folder for this device
- Use the existing `hamilton_vantage` package as the main reference
- Validate your edited Python file with `python3 -m py_compile <driver>`
- If you create a fallback STL, keep coordinates in meters and make the mesh loadable by the xacro

At the end, report:
- whether you found a real downloadable model or used fallback geometry
- the URLs you relied on
- the files you changed
- any uncertainty that still remains
```

## Adapted Prompts

### access2_backend

```text
Apply the universal prompt in this document to `access2_backend`.

Ownership:
- `/home/xzye/projects/DPTech/LeapLab/Uni-Lab-OS/unilabos/devices/_phage_display/access2_backend.py`
- `/home/xzye/projects/DPTech/LeapLab/Uni-Lab-OS/unilabos/device_mesh/devices/access2_backend/`

Suggested mesh folder: `access2_backend`.
This is a microplate centrifuge backend. If the exact physical model is unclear, choose the closest representative automated plate centrifuge form factor and document that assumption in `meta.json`.
Include the primary door / loading access point.
Do not edit any other files.
```

### bio_shake

```text
Apply the universal prompt in this document to `bio_shake`.

Ownership:
- `/home/xzye/projects/DPTech/LeapLab/Uni-Lab-OS/unilabos/devices/_phage_display/bio_shake.py`
- `/home/xzye/projects/DPTech/LeapLab/Uni-Lab-OS/unilabos/device_mesh/devices/bio_shake/`

Suggested mesh folder: `bio_shake`.
Target a representative INHECO-style microplate thermoshaker if the exact unit is ambiguous, and record that assumption.
Include the plate-loading face / lid access location if it can be estimated.
Do not edit any other files.
```

### bio_tek_plate_reader_backend

```text
Apply the universal prompt in this document to `bio_tek_plate_reader_backend`.

Ownership:
- `/home/xzye/projects/DPTech/LeapLab/Uni-Lab-OS/unilabos/devices/_phage_display/bio_tek_plate_reader_backend.py`
- `/home/xzye/projects/DPTech/LeapLab/Uni-Lab-OS/unilabos/device_mesh/devices/bio_tek_plate_reader_backend/`

Suggested mesh folder: `bio_tek_plate_reader_backend`, but if research identifies a specific Agilent BioTek reader model with a better canonical slug, you may use that instead and wire the decorator accordingly.
Include tray / drawer access-point frames if applicable.
Do not edit any other files.
```

### centrifuge

```text
Apply the universal prompt in this document to `centrifuge`.

Ownership:
- `/home/xzye/projects/DPTech/LeapLab/Uni-Lab-OS/unilabos/devices/_phage_display/centrifuge.py`
- `/home/xzye/projects/DPTech/LeapLab/Uni-Lab-OS/unilabos/device_mesh/devices/centrifuge/`

Suggested mesh folder: `centrifuge`.
This is a generic front-end, so choose the closest representative automated plate centrifuge form factor and document the assumption.
Include the primary door / loading access point in the xacro.
Do not edit any other files.
```

### clari_ostar_backend

```text
Apply the universal prompt in this document to `clari_ostar_backend`.

Ownership:
- `/home/xzye/projects/DPTech/LeapLab/Uni-Lab-OS/unilabos/devices/_phage_display/clari_ostar_backend.py`
- `/home/xzye/projects/DPTech/LeapLab/Uni-Lab-OS/unilabos/device_mesh/devices/clari_ostar_backend/`

Suggested mesh folder: `clari_ostar_backend`, but if a better canonical slug like a BMG CLARIOstar model name is clearly supported by research, you may use that instead and wire the decorator accordingly.
Include plate drawer access-point frames if applicable.
Do not edit any other files.
```

### cytomat_backend

```text
Apply the universal prompt in this document to `cytomat_backend`.

Ownership:
- `/home/xzye/projects/DPTech/LeapLab/Uni-Lab-OS/unilabos/devices/_phage_display/cytomat_backend.py`
- `/home/xzye/projects/DPTech/LeapLab/Uni-Lab-OS/unilabos/device_mesh/devices/cytomat_backend/`

Suggested mesh folder: `cytomat_backend`, unless research supports a more exact Cytomat family slug.
Prioritize dimensions plus loading-tray / transfer opening access points because this device explicitly exposes plates.
Do not edit any other files.
```

### agilent_biotek_406_fx

```text
Apply the universal prompt in this document to `agilent_biotek_406_fx`.

Ownership:
- `/home/xzye/projects/DPTech/LeapLab/Uni-Lab-OS/unilabos/devices/_phage_display/guessed_agilent_biotek_406_fx.py`
- `/home/xzye/projects/DPTech/LeapLab/Uni-Lab-OS/unilabos/device_mesh/devices/agilent_biotek_406_fx/`

Suggested mesh folder: `agilent_biotek_406_fx`.
Target the Agilent BioTek 406 FX washer/dispenser specifically.
Include front deck / plate-loading access frames if applicable.
Do not edit any other files.
```

### applied_biosystems_seqstudio_genetic_analyzer

```text
Apply the universal prompt in this document to `applied_biosystems_seqstudio_genetic_analyzer`.

Ownership:
- `/home/xzye/projects/DPTech/LeapLab/Uni-Lab-OS/unilabos/devices/_phage_display/guessed_applied_biosystems_seqstudio_genetic_analyzer.py`
- `/home/xzye/projects/DPTech/LeapLab/Uni-Lab-OS/unilabos/device_mesh/devices/applied_biosystems_seqstudio_genetic_analyzer/`

Suggested mesh folder: `applied_biosystems_seqstudio_genetic_analyzer`.
Target the Applied Biosystems SeqStudio Genetic Analyzer specifically.
If useful, estimate cartridge / consumable access location from official figures.
Do not edit any other files.
```

### bd_facsmelody

```text
Apply the universal prompt in this document to `bd_facsmelody`.

Ownership:
- `/home/xzye/projects/DPTech/LeapLab/Uni-Lab-OS/unilabos/devices/_phage_display/guessed_bd_facsmelody.py`
- `/home/xzye/projects/DPTech/LeapLab/Uni-Lab-OS/unilabos/device_mesh/devices/bd_facsmelody/`

Suggested mesh folder: `bd_facsmelody`.
Target the BD FACSMelody specifically.
If no downloadable model exists, use official dimensions and estimate the sample-loading / sort-output access faces from documentation.
Do not edit any other files.
```

### cytiva_akta_pure

```text
Apply the universal prompt in this document to `cytiva_akta_pure`.

Ownership:
- `/home/xzye/projects/DPTech/LeapLab/Uni-Lab-OS/unilabos/devices/_phage_display/guessed_cytiva_akta_pure.py`
- `/home/xzye/projects/DPTech/LeapLab/Uni-Lab-OS/unilabos/device_mesh/devices/cytiva_akta_pure/`

Suggested mesh folder: `cytiva_akta_pure`.
Target the Cytiva AKTA pure specifically.
Include front-column / sample-loading access orientation if it can be inferred from official imagery.
Do not edit any other files.
```

### cytiva_biacore_8k_plus

```text
Apply the universal prompt in this document to `cytiva_biacore_8k_plus`.

Ownership:
- `/home/xzye/projects/DPTech/LeapLab/Uni-Lab-OS/unilabos/devices/_phage_display/guessed_cytiva_biacore_8k_plus.py`
- `/home/xzye/projects/DPTech/LeapLab/Uni-Lab-OS/unilabos/device_mesh/devices/cytiva_biacore_8k_plus/`

Suggested mesh folder: `cytiva_biacore_8k_plus`.
Target the Cytiva Biacore 8K+ specifically.
Estimate the consumable / plate-loading face only if it is supported by docs or figures.
Do not edit any other files.
```

### eppendorf_centrifuge_5910_ri

```text
Apply the universal prompt in this document to `eppendorf_centrifuge_5910_ri`.

Ownership:
- `/home/xzye/projects/DPTech/LeapLab/Uni-Lab-OS/unilabos/devices/_phage_display/guessed_eppendorf_centrifuge_5910_ri.py`
- `/home/xzye/projects/DPTech/LeapLab/Uni-Lab-OS/unilabos/device_mesh/devices/eppendorf_centrifuge_5910_ri/`

Suggested mesh folder: `eppendorf_centrifuge_5910_ri`.
Target the Eppendorf Centrifuge 5910 Ri specifically.
Prioritize top-lid access geometry and instrument envelope.
Do not edit any other files.
```

### hettich_rotanta_460_robotic

```text
Apply the universal prompt in this document to `hettich_rotanta_460_robotic`.

Ownership:
- `/home/xzye/projects/DPTech/LeapLab/Uni-Lab-OS/unilabos/devices/_phage_display/guessed_hettich_rotanta_460_robotic.py`
- `/home/xzye/projects/DPTech/LeapLab/Uni-Lab-OS/unilabos/device_mesh/devices/hettich_rotanta_460_robotic/`

Suggested mesh folder: `hettich_rotanta_460_robotic`.
Target the Hettich Rotanta 460 Robotic specifically.
Prioritize robot-facing loading interface and lid opening location.
Do not edit any other files.
```

### molecular_devices_qpix_420

```text
Apply the universal prompt in this document to `molecular_devices_qpix_420`.

Ownership:
- `/home/xzye/projects/DPTech/LeapLab/Uni-Lab-OS/unilabos/devices/_phage_display/guessed_molecular_devices_qpix_420.py`
- `/home/xzye/projects/DPTech/LeapLab/Uni-Lab-OS/unilabos/device_mesh/devices/molecular_devices_qpix_420/`

Suggested mesh folder: `molecular_devices_qpix_420`.
Target the Molecular Devices QPix 420 specifically.
Estimate the plate infeed / pick area access face if official imagery supports it.
Do not edit any other files.
```

### qiagen_qiacube_connect

```text
Apply the universal prompt in this document to `qiagen_qiacube_connect`.

Ownership:
- `/home/xzye/projects/DPTech/LeapLab/Uni-Lab-OS/unilabos/devices/_phage_display/guessed_qiagen_qiacube_connect.py`
- `/home/xzye/projects/DPTech/LeapLab/Uni-Lab-OS/unilabos/device_mesh/devices/qiagen_qiacube_connect/`

Suggested mesh folder: `qiagen_qiacube_connect`.
Target the QIAGEN QIAcube Connect specifically.
Include the front-access / lid-access orientation if visible in official product literature.
Do not edit any other files.
```

### tecan_resolvex_a200

```text
Apply the universal prompt in this document to `tecan_resolvex_a200`.

Ownership:
- `/home/xzye/projects/DPTech/LeapLab/Uni-Lab-OS/unilabos/devices/_phage_display/guessed_tecan_resolvex_a200.py`
- `/home/xzye/projects/DPTech/LeapLab/Uni-Lab-OS/unilabos/device_mesh/devices/tecan_resolvex_a200/`

Suggested mesh folder: `tecan_resolvex_a200`.
Target the Tecan Resolvex A200 specifically.
Prioritize front deck / plate access orientation for filtered-plate handling.
Do not edit any other files.
```

### telesis_bio_bioxp_3250

```text
Apply the universal prompt in this document to `telesis_bio_bioxp_3250`.

Ownership:
- `/home/xzye/projects/DPTech/LeapLab/Uni-Lab-OS/unilabos/devices/_phage_display/guessed_telesis_bio_bioxp_3250.py`
- `/home/xzye/projects/DPTech/LeapLab/Uni-Lab-OS/unilabos/device_mesh/devices/telesis_bio_bioxp_3250/`

Suggested mesh folder: `telesis_bio_bioxp_3250`.
Target the Telesis BIOXp 3250 specifically.
Estimate the loading / service access side only when clearly supported by sources.
Do not edit any other files.
```

### incubator

```text
Apply the universal prompt in this document to `incubator`.

Ownership:
- `/home/xzye/projects/DPTech/LeapLab/Uni-Lab-OS/unilabos/devices/_phage_display/incubator.py`
- `/home/xzye/projects/DPTech/LeapLab/Uni-Lab-OS/unilabos/device_mesh/devices/incubator/`

Suggested mesh folder: `incubator`.
This is a generic automated microplate incubator front-end.
Choose the closest representative automated incubator form factor with a loading tray and document the assumption.
Include the loading-tray access point in the xacro.
Do not edit any other files.
```

### incubator_shaker_stack

```text
Apply the universal prompt in this document to `incubator_shaker_stack`.

Ownership:
- `/home/xzye/projects/DPTech/LeapLab/Uni-Lab-OS/unilabos/devices/_phage_display/incubator_shaker_stack.py`
- `/home/xzye/projects/DPTech/LeapLab/Uni-Lab-OS/unilabos/device_mesh/devices/incubator_shaker_stack/`

Suggested mesh folder: `incubator_shaker_stack`.
This is a generic stacked incubator/shaker front-end.
Choose a representative stacked INHECO-style form factor, document the assumption, and include per-unit loading-tray access frames when they can be estimated.
Do not edit any other files.
```

### li_ha

```text
Apply the universal prompt in this document to `li_ha`.

Ownership:
- `/home/xzye/projects/DPTech/LeapLab/Uni-Lab-OS/unilabos/devices/_phage_display/li_ha.py`
- `/home/xzye/projects/DPTech/LeapLab/Uni-Lab-OS/unilabos/device_mesh/devices/li_ha/`

Suggested mesh folder: `li_ha`.
This is a generic liquid-handling workstation arm surface.
Choose the closest representative Tecan-style liquid handler deck form factor and record the assumption if an exact model is not identifiable.
Include deck-access / pipetting-area access frames.
Do not edit any other files.
```

### molecular_devices_backend

```text
Apply the universal prompt in this document to `molecular_devices_backend`.

Ownership:
- `/home/xzye/projects/DPTech/LeapLab/Uni-Lab-OS/unilabos/devices/_phage_display/molecular_devices_backend.py`
- `/home/xzye/projects/DPTech/LeapLab/Uni-Lab-OS/unilabos/device_mesh/devices/molecular_devices_backend/`

Suggested mesh folder: `molecular_devices_backend`.
This is a generic Molecular Devices plate reader backend.
If the exact model is not obvious, use a representative Molecular Devices multi-mode reader form factor and document the assumption.
Include plate drawer access-point frames if applicable.
Do not edit any other files.
```

### peeler

```text
Apply the universal prompt in this document to `peeler`.

Ownership:
- `/home/xzye/projects/DPTech/LeapLab/Uni-Lab-OS/unilabos/devices/_phage_display/peeler.py`
- `/home/xzye/projects/DPTech/LeapLab/Uni-Lab-OS/unilabos/device_mesh/devices/peeler/`

Suggested mesh folder: `peeler`.
This is a generic plate desealer.
Choose the closest representative automated microplate peeler form factor and document the assumption.
Include plate infeed / output or main opening access frames if they can be inferred.
Do not edit any other files.
```

### plate_reader

```text
Apply the universal prompt in this document to `plate_reader`.

Ownership:
- `/home/xzye/projects/DPTech/LeapLab/Uni-Lab-OS/unilabos/devices/_phage_display/plate_reader.py`
- `/home/xzye/projects/DPTech/LeapLab/Uni-Lab-OS/unilabos/device_mesh/devices/plate_reader/`

Suggested mesh folder: `plate_reader`.
This is a generic plate reader front-end.
Choose the closest representative benchtop multi-mode reader form factor and document the assumption.
Include the plate drawer access point.
Do not edit any other files.
```

### sealer

```text
Apply the universal prompt in this document to `sealer`.

Ownership:
- `/home/xzye/projects/DPTech/LeapLab/Uni-Lab-OS/unilabos/devices/_phage_display/sealer.py`
- `/home/xzye/projects/DPTech/LeapLab/Uni-Lab-OS/unilabos/device_mesh/devices/sealer/`

Suggested mesh folder: `sealer`.
This is a generic plate sealer.
Choose the closest representative automated microplate sealer form factor and document the assumption.
Include the plate-loading opening / tray access point.
Do not edit any other files.
```

### star_backend

```text
Apply the universal prompt in this document to `star_backend`.

Ownership:
- `/home/xzye/projects/DPTech/LeapLab/Uni-Lab-OS/unilabos/devices/_phage_display/star_backend.py`
- `/home/xzye/projects/DPTech/LeapLab/Uni-Lab-OS/unilabos/device_mesh/devices/star_backend/`

Suggested mesh folder: `star_backend`, though `hamilton_star` is acceptable if clearly better supported by research and wired consistently.
Target a Hamilton STAR liquid handling workstation specifically.
Prioritize deck envelope and loading-tray access orientation.
Do not edit any other files.
```

### v_spin_backend

```text
Apply the universal prompt in this document to `v_spin_backend`.

Ownership:
- `/home/xzye/projects/DPTech/LeapLab/Uni-Lab-OS/unilabos/devices/_phage_display/v_spin_backend.py`
- `/home/xzye/projects/DPTech/LeapLab/Uni-Lab-OS/unilabos/device_mesh/devices/v_spin_backend/`

Suggested mesh folder: `v_spin_backend`.
Target the Agilent V-Spin style automated centrifuge if possible.
Prioritize front door / loading access geometry.
Do not edit any other files.
```

### vantage_backend

```text
Apply the universal prompt in this document to `vantage_backend`.

Ownership:
- `/home/xzye/projects/DPTech/LeapLab/Uni-Lab-OS/unilabos/devices/_phage_display/vantage_backend.py`
- `/home/xzye/projects/DPTech/LeapLab/Uni-Lab-OS/unilabos/device_mesh/devices/hamilton_vantage/`

Suggested mesh folder: `hamilton_vantage`.
This device already has a mesh package. Upgrade it rather than replacing it blindly:
- preserve working behavior
- enrich `meta.json` with sources, dimensions, and notes
- add access-point frames to `macro_device.xacro`
- only replace the STL if you find a clearly better asset or need a more accurate fallback
Do not edit any other files.
```
