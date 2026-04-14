# Phage-Display Device Footprints, Layout Optimization, and `-g` Export

## Summary
Build missing 2D footprints for the phage-display device set, add a phage-display preset to the Lab3D test frontend, run DE layout optimization against the workflow described in `Uni-Lab-OS/.project_phage_display/phage_display_workflow_v7_NL.txt`, manually sanity-check the result, and export a physical-setup-only Uni-Lab graph JSON shaped like `Uni-Lab-OS/.project_phage_display/phage_display_unilab_overlap.json`.

Use the workflow action counts only to weight proximity and workflow importance. The actual physical layout to optimize/export is a single-instance unique-device manifest rather than the 18 unique workflow `device_id`s. In practice this means adding only one `vantage_backend`, one `cytomat_backend`, one `cytiva_akta_pure`, one `telesis_bio_bioxp_3250`, and so on.

## Key Changes
- Fix the footprint extraction path and alias pipeline before regenerating data.
  - Update `extract_footprints.py` to detect both `macro_device.xacro` and `modal.xacro`.
  - Extend opening extraction to use `_access_joint` / `_access_link` markers in addition to `socket` joints.
  - Correct the extractor’s default registry path so targeted extraction works from this repo without ad hoc CLI overrides.
- Add targeted footprint support for the requested device set.
  - Regenerate or patch in entries for: `agilent_biotek_406_fx`, `applied_biosystems_seqstudio_genetic_analyzer`, `bd_facsmelody`, `cytiva_akta_pure`, `cytiva_biacore_8k_plus`, `cytomat_backend`, `hettich_rotanta_460_robotic`, `molecular_devices_qpix_420`, `qiagen_qiacube_connect`, `tecan_resolvex_a200`, `telesis_bio_bioxp_3250`.
  - Add alias-backed public footprint/catalog IDs for `vantage_backend -> hamilton_vantage` and `robotic_arm.SCARA_with_slider.moveit.virtual -> arm_slider`.
  - For `robotic_arm.SCARA_with_slider.moveit.virtual`, derive the footprint from the slider rail only, not the full arm assembly. Use the `arm_slider` mesh folder but extract only the slideway collision/visual mesh so the 2D footprint is the rail body.
- Make catalog resolution accept public workflow IDs, not just mesh-folder IDs.
  - `resolve_device`, registry loading, and frontend `/devices` data should surface `vantage_backend` and `robotic_arm.SCARA_with_slider.moveit.virtual` as valid selectable IDs.
  - Preserve exact workflow-facing IDs for export and scene usage; use mesh-folder aliases only internally for footprint/model lookup.
- Add a Lab3D frontend preset for fast visual validation.
  - Add a “Phage Display Preset” action in `static/lab3d.html` that clears the current scene and inserts one instance of each catalog device type once:
    `agilent_biotek_406_fx`, `applied_biosystems_seqstudio_genetic_analyzer`, `bd_facsmelody`, `cytiva_akta_pure`, `cytiva_biacore_8k_plus`, `cytomat_backend`, `hettich_rotanta_460_robotic`, `molecular_devices_qpix_420`, `qiagen_qiacube_connect`, `robotic_arm.SCARA_with_slider.moveit.virtual`, `tecan_resolvex_a200`, `telesis_bio_bioxp_3250`, `vantage_backend`.
  - This preset matches the optimizer/export scope: one instance per unique device, not the full workflow duplicate set.
- Add a reproducible phage-display layout workflow artifact.
  - Materialize a single-instance device manifest from `phage_display_workflow_v7.json` by collapsing duplicate workflow `device_id`s to their unique public device class.
  - Translate `phage_display_workflow_v7_NL.txt` into a checked-in intents artifact following `llm_skill/layout_intent_translator.md`. Do not depend on a live external LLM at runtime; save the translated intents JSON for deterministic reruns.
  - Encode these optimizer inputs:
    - `seeder = workflow_cluster`
    - `run_de = true`
    - `maxiter = 400`
    - `seed = 42`
    - `angle_granularity = 4`
    - `strategy = currenttobest1bin`
    - `mutation = [0.5, 1.0]`
    - `recombination = 0.7`
    - `crossover_mode = device`
  - Use a derived lab envelope, not a hardcoded room.
    - Start from footprint area and longest device span.
    - Compute an initial lab size that fits a central slider rail with devices on both sides.
    - If optimization fails, expand width/depth by 0.5 m per retry until success, capped at 4 retries.
- Use this fixed constraint strategy for the phage-display layout.
  - One global `reachable_by` intent for the SCARA rail to all arm-served catalog devices.
  - One global `min_spacing` intent with `min_gap = 0.08`.
  - Workflow/cluster intents covering:
    - Main high-traffic chain around `vantage_backend`, `hettich_rotanta_460_robotic`, `cytomat_backend`, `agilent_biotek_406_fx`, `bd_facsmelody`, `tecan_resolvex_a200`
    - Validation cluster around `molecular_devices_qpix_420`, `qiagen_qiacube_connect`, `applied_biosystems_seqstudio_genetic_analyzer`, `bd_facsmelody`, `agilent_biotek_406_fx`
    - BioXp/AKTA adjacency: `telesis_bio_bioxp_3250` with `cytiva_akta_pure`
    - Final assay handoff: `cytiva_akta_pure`, `vantage_backend`, `cytiva_biacore_8k_plus`
    - Early side branch weighting between `vantage_backend` and `cytomat_backend`
- Export a physical-setup-only Uni-Lab graph JSON.
  - Match the sample structure in `phage_display_unilab_overlap.json`: top-level `nodes` + `edges`.
  - Include `host_node`.
  - Emit one node per unique device using bare public/catalog IDs such as `vantage_backend`, not workflow instance IDs such as `vantage_backend_2`.
  - Set each device node’s `class` to the catalog/public class, `type` to `device`, and write layout into both `position` and `pose.position` / `pose.rotation`.
  - Use deterministic UUIDs per node so reruns are stable.
  - Do not include workflow execution nodes or workflow edges in this export.

## Public Interfaces / Artifacts
- `/devices` will expose the new/aliased device IDs needed by the phage-display workflow and frontend preset.
- `footprints.json` will gain footprint entries for the requested devices and public aliases.
- A checked-in phage-display intents artifact will define the exact translated optimizer input.
- A new exported graph JSON in `.project_phage_display/` will be the final `unilabos -g` artifact.

## Test Plan
- Device/footprint tests:
  - Assert `load_footprints()` contains every requested catalog/public ID.
  - Assert alias resolution works for `vantage_backend` and `robotic_arm.SCARA_with_slider.moveit.virtual`.
  - Assert the SCARA public footprint bbox is rail-only and materially smaller in depth than the old combined-arm footprint.
- Extractor tests:
  - Assert `macro_device.xacro` is discovered.
  - Assert opening extraction recognizes `_access_joint` links on representative devices.
- Catalog/server tests:
  - Assert `/devices` returns the phage-display preset IDs.
  - Assert frontend preset insertion produces 13 selected device types.
- Export compatibility tests:
  - Load the exported JSON through `graphio.read_node_link_json()` successfully.
  - If environment permits, verify `unilabos -g <exported.json> --check_mode --skip_env_check` can parse it without rewriting files.
- Optimization acceptance:
  - DE returns `success=true`.
  - Manual inspection in `/lab3d` confirms:
    - all arm-served devices are arranged around the slider rail rather than isolated far away
    - no overlaps/collisions
    - `vantage_backend` and `cytomat_backend` remain sensibly placed for both the hot path and the side-branch interactions implied by the workflow
    - `telesis_bio_bioxp_3250` and `cytiva_akta_pure` remain paired
    - Biacore sits near the final protein-output side, not near the early prep cluster

## Assumptions
- “Add one of each to the test frontend” means one instance per unique catalog device type for UI inspection.
- The optimization/export target is the single-instance unique-device manifest, not the 18 unique physical workflow instance IDs.
- The skill file is used as the translation spec, but the translated intents are saved as a repo artifact instead of requiring a live external LLM on every rerun.
- The final export should be physical setup only, using the structure of `Uni-Lab-OS/.project_phage_display/phage_display_unilab_overlap.json`.
