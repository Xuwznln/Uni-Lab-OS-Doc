"""Edge-side simulation pair pipeline.

Consumes the Device Square simulation pair relations (Phase 1B):
- hints:  read package-bundled pair hints for upload (contract C-2)
- bundle/resolve/generate/cache: resolve cloud relations and compile them into a
  Phase 1A compatible device_pair.yaml consumed by ``unilabos.registry.pair_registry``.

See product_designs root docs:
- 08_simulation_pair_registry_phase1b_plan.md
- 08_simulation_pair_my_part_impl_manual.md
"""

from unilabos.sim.pairs.hints import collect_all_pair_hints, read_pair_hints

__all__ = ["collect_all_pair_hints", "read_pair_hints"]
