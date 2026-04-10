# Phage Display Device Mesh Worker Prompts

This file captures the universal worker prompt and the per-device adaptations for
`Uni-Lab-OS/unilabos/devices/_phage_display`.

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
4. Update the target driver's `@device(...)` decorator to include:
   model={
     "type": "device",
     "mesh": "<mesh_folder>",
   },
5. Do not touch other drivers or other workers' files.

Required outputs
- `Uni-Lab-OS/unilabos/device_mesh/devices/<mesh_folder>/macro_device.xacro`
- `Uni-Lab-OS/unilabos/device_mesh/devices/<mesh_folder>/meshes/<...>.stl`
- optional `.glb` if a good one is available and easy to keep
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

Fallback geometry helper
- Prefer the shared helper script for box models:
  `/home/xzye/projects/DPTech/LeapLab/Uni-Lab-OS/unilabos/device_mesh/tools/generate_box_stl.py`
- The helper writes a simple meter-based box STL with:
  `--width --depth --height --output --name`
- Keep access points in xacro frames and `meta.json` even if the STL itself is only a box

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

Implementation rules
- You are not alone in the codebase. Do not revert unrelated changes and do not edit files outside your ownership.
- Keep ownership to:
  - the target driver file
  - the new or existing device mesh folder for this device
- Use the existing `hamilton_vantage` package as the main reference
- Validate the edited Python file with `python3 -m py_compile <driver>`
- If you create a fallback STL, keep coordinates in meters and make the mesh loadable by the xacro

At the end, report:
- whether a real downloadable model was found or fallback geometry was used
- the URLs used
- the files changed
- any uncertainty that still remains
```

## Device Adaptations

Each worker owns exactly one driver file and one mesh folder.

1. `access2_backend`
   - Driver: `Uni-Lab-OS/unilabos/devices/_phage_display/access2_backend.py`
   - Mesh folder: `Uni-Lab-OS/unilabos/device_mesh/devices/access2_backend/`
   - Notes: generic microplate centrifuge backend; if exact hardware is unclear, use the closest representative automated plate centrifuge and document the assumption.

2. `bio_shake`
   - Driver: `Uni-Lab-OS/unilabos/devices/_phage_display/bio_shake.py`
   - Mesh folder: `Uni-Lab-OS/unilabos/device_mesh/devices/bio_shake/`
   - Notes: target a representative INHECO-style microplate thermoshaker if needed.

3. `bio_tek_plate_reader_backend`
   - Driver: `Uni-Lab-OS/unilabos/devices/_phage_display/bio_tek_plate_reader_backend.py`
   - Mesh folder: `Uni-Lab-OS/unilabos/device_mesh/devices/bio_tek_plate_reader_backend/`
   - Notes: if research identifies a specific Agilent BioTek reader model with a better canonical slug, use that slug and wire the decorator accordingly.

4. `centrifuge`
   - Driver: `Uni-Lab-OS/unilabos/devices/_phage_display/centrifuge.py`
   - Mesh folder: `Uni-Lab-OS/unilabos/device_mesh/devices/centrifuge/`
   - Notes: generic front end; choose a representative automated plate centrifuge form factor and include the primary door / loading access point.

5. `clari_ostar_backend`
   - Driver: `Uni-Lab-OS/unilabos/devices/_phage_display/clari_ostar_backend.py`
   - Mesh folder: `Uni-Lab-OS/unilabos/device_mesh/devices/clari_ostar_backend/`
   - Notes: if a better canonical BMG CLARIOstar slug is supported by research, use that slug and wire the decorator accordingly.

6. `cytomat_backend`
   - Driver: `Uni-Lab-OS/unilabos/devices/_phage_display/cytomat_backend.py`
   - Mesh folder: `Uni-Lab-OS/unilabos/device_mesh/devices/cytomat_backend/`
   - Notes: prioritize loading-tray / transfer opening access points because plates are explicitly exposed.

7. `agilent_biotek_406_fx`
   - Driver: `Uni-Lab-OS/unilabos/devices/_phage_display/guessed_agilent_biotek_406_fx.py`
   - Mesh folder: `Uni-Lab-OS/unilabos/device_mesh/devices/agilent_biotek_406_fx/`
   - Notes: target the Agilent BioTek 406 FX washer/dispenser specifically.

8. `applied_biosystems_seqstudio_genetic_analyzer`
   - Driver: `Uni-Lab-OS/unilabos/devices/_phage_display/guessed_applied_biosystems_seqstudio_genetic_analyzer.py`
   - Mesh folder: `Uni-Lab-OS/unilabos/device_mesh/devices/applied_biosystems_seqstudio_genetic_analyzer/`
   - Notes: target the Applied Biosystems SeqStudio Genetic Analyzer and capture cartridge / consumable access points when possible.

9. `bd_facsmelody`
   - Driver: `Uni-Lab-OS/unilabos/devices/_phage_display/guessed_bd_facsmelody.py`
   - Mesh folder: `Uni-Lab-OS/unilabos/device_mesh/devices/bd_facsmelody/`
   - Notes: target BD FACSMelody and estimate sample-loading / sort-output access faces if no model is available.

10. `cytiva_akta_pure`
   - Driver: `Uni-Lab-OS/unilabos/devices/_phage_display/guessed_cytiva_akta_pure.py`
   - Mesh folder: `Uni-Lab-OS/unilabos/device_mesh/devices/cytiva_akta_pure/`
   - Notes: target Cytiva AKTA pure and record front-panel / rack access assumptions.

11. `cytiva_biacore_8k_plus`
   - Driver: `Uni-Lab-OS/unilabos/devices/_phage_display/guessed_cytiva_biacore_8k_plus.py`
   - Mesh folder: `Uni-Lab-OS/unilabos/device_mesh/devices/cytiva_biacore_8k_plus/`
   - Notes: target Cytiva Biacore 8K+ and include sample rack / plate-loading access estimates.

12. `eppendorf_centrifuge_5910_ri`
   - Driver: `Uni-Lab-OS/unilabos/devices/_phage_display/guessed_eppendorf_centrifuge_5910_ri.py`
   - Mesh folder: `Uni-Lab-OS/unilabos/device_mesh/devices/eppendorf_centrifuge_5910_ri/`
   - Notes: target the Eppendorf 5910 Ri specifically and include lid-opening access geometry.

13. `hettich_rotanta_460_robotic`
   - Driver: `Uni-Lab-OS/unilabos/devices/_phage_display/guessed_hettich_rotanta_460_robotic.py`
   - Mesh folder: `Uni-Lab-OS/unilabos/device_mesh/devices/hettich_rotanta_460_robotic/`
   - Notes: target the Hettich Rotanta 460 Robotic specifically and include robot transfer / door access frames.

14. `molecular_devices_qpix_420`
   - Driver: `Uni-Lab-OS/unilabos/devices/_phage_display/guessed_molecular_devices_qpix_420.py`
   - Mesh folder: `Uni-Lab-OS/unilabos/device_mesh/devices/molecular_devices_qpix_420/`
   - Notes: target the QPix 420 colony picker and include plate deck / loading access assumptions.

15. `qiagen_qiacube_connect`
   - Driver: `Uni-Lab-OS/unilabos/devices/_phage_display/guessed_qiagen_qiacube_connect.py`
   - Mesh folder: `Uni-Lab-OS/unilabos/device_mesh/devices/qiagen_qiacube_connect/`
   - Notes: target QIAGEN QIAcube Connect and include front-door / rotor / consumables access assumptions.

16. `tecan_resolvex_a200`
   - Driver: `Uni-Lab-OS/unilabos/devices/_phage_display/guessed_tecan_resolvex_a200.py`
   - Mesh folder: `Uni-Lab-OS/unilabos/device_mesh/devices/tecan_resolvex_a200/`
   - Notes: target the Tecan Resolvex A200 specifically and capture front deck / plate transfer access.

17. `telesis_bio_bioxp_3250`
   - Driver: `Uni-Lab-OS/unilabos/devices/_phage_display/guessed_telesis_bio_bioxp_3250.py`
   - Mesh folder: `Uni-Lab-OS/unilabos/device_mesh/devices/telesis_bio_bioxp_3250/`
   - Notes: target the BioXp 3250 synthetic biology workstation and estimate cartridge / consumable access areas if needed.

18. `incubator`
   - Driver: `Uni-Lab-OS/unilabos/devices/_phage_display/incubator.py`
   - Mesh folder: `Uni-Lab-OS/unilabos/device_mesh/devices/incubator/`
   - Notes: generic automated incubator; prioritize loading tray location from the driver API and add matching access frames.

19. `incubator_shaker_stack`
   - Driver: `Uni-Lab-OS/unilabos/devices/_phage_display/incubator_shaker_stack.py`
   - Mesh folder: `Uni-Lab-OS/unilabos/device_mesh/devices/incubator_shaker_stack/`
   - Notes: use the stack form factor and include one access frame per unit loading tray when reasonable.

20. `li_ha`
   - Driver: `Uni-Lab-OS/unilabos/devices/_phage_display/li_ha.py`
   - Mesh folder: `Uni-Lab-OS/unilabos/device_mesh/devices/li_ha/`
   - Notes: generic liquid-handling workstation; choose a representative Tecan-style LiHa deck and include deck opening / pipetting access faces.

21. `molecular_devices_backend`
   - Driver: `Uni-Lab-OS/unilabos/devices/_phage_display/molecular_devices_backend.py`
   - Mesh folder: `Uni-Lab-OS/unilabos/device_mesh/devices/molecular_devices_backend/`
   - Notes: generic Molecular Devices microplate reader; prefer a representative SpectraMax-style reader and include drawer access.

22. `peeler`
   - Driver: `Uni-Lab-OS/unilabos/devices/_phage_display/peeler.py`
   - Mesh folder: `Uni-Lab-OS/unilabos/device_mesh/devices/peeler/`
   - Notes: generic plate desealer; choose a representative benchtop plate peeler and include the front plate insertion opening.

23. `plate_reader`
   - Driver: `Uni-Lab-OS/unilabos/devices/_phage_display/plate_reader.py`
   - Mesh folder: `Uni-Lab-OS/unilabos/device_mesh/devices/plate_reader/`
   - Notes: generic reader front end; choose a representative microplate reader with tray access.

24. `sealer`
   - Driver: `Uni-Lab-OS/unilabos/devices/_phage_display/sealer.py`
   - Mesh folder: `Uni-Lab-OS/unilabos/device_mesh/devices/sealer/`
   - Notes: generic plate sealer; capture the plate insertion slot and any vertical clamp / press access assumptions.

25. `star_backend`
   - Driver: `Uni-Lab-OS/unilabos/devices/_phage_display/star_backend.py`
   - Mesh folder: `Uni-Lab-OS/unilabos/device_mesh/devices/star_backend/`
   - Notes: Hamilton STAR liquid handler; if research supports a more canonical slug like `hamilton_star`, that is acceptable if the decorator is wired consistently.

26. `v_spin_backend`
   - Driver: `Uni-Lab-OS/unilabos/devices/_phage_display/v_spin_backend.py`
   - Mesh folder: `Uni-Lab-OS/unilabos/device_mesh/devices/v_spin_backend/`
   - Notes: automated centrifuge backend; prefer the exact model if identifiable, otherwise use a representative plate centrifuge with a front access door.

27. `vantage_backend`
   - Driver: `Uni-Lab-OS/unilabos/devices/_phage_display/vantage_backend.py`
   - Mesh folder: `Uni-Lab-OS/unilabos/device_mesh/devices/hamilton_vantage/`
   - Notes: this already exists as the reference example; the worker should preserve the current mesh package, add source-backed metadata, and add access-point frames / notes for the existing geometry.
