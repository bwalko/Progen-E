# Known Gotchas

Track behavior/documentation mismatches that can confuse future sessions.

## Population growth test scale

- File: `unit_test/test_population_growth_100_years.py`
- `STARTING_COUPLES` (default **10**) is the source of truth for founding couples in that test (20 founders at two people per couple).
- The module docstring describes the same scale; if you change the default scenario, update the constant and docstring together.

## SQLite dependency

- Most generation paths assume tables already exist in `worlds/default/config.sqlite` (or the active world’s config DB).
- If a table is missing, errors often suggest running `python utils/util_load_config.py --world default`.
- Run config reload first before diagnosing generation errors.

## Settlements are lazy; checkpoint column names

- Fresh simulations no longer pre-seed one settlement per region. A settlement row appears when someone is assigned there (founders, random births, offspring paths).
- The SQLite column `simulation_settlements.population_cap` still stores the **resident census snapshot**, not a capacity limit.
- Abandoned settlements remain in `settlements_by_id` / the checkpoint for history; empty-site abandonment uses escalating rolls controlled by `library/settlements.py` (`ABANDON_*` constants).
- Re-establishing a ghost town creates a **new** `settlement_id` (and founding year), optionally copying name/geo from the abandoned row at the same `site_slot`.

## Residence vs birthplace; partners and paramours; litter mortality

- **`Person.current_settlement_id`** is residence (updated by `move_person_to_settlement`); **`birthplace_*`** stays immutable for genealogy. Census uses residence (`SimulationContext._residence_settlement_id` / `_residence_region_id`), not birthplace alone.
- **`partner_person_id`** is the legal spouse; **`paramour_person_id`** is an optional second tie, set only via **`add_paramour_relationship`** / **`end_paramour_relationship`**. **`couples`** / **`paramours`** lists and person ids must stay in sync via context helpers.
- Each fertile female has **at most one birth attempt per calendar year** (`last_birth_event_year`), with the father drawn from **either** spouse **or** paramour for that attempt (not both in the same year).
- **Twins/triplets** share one birth event and father; infant mortality at age 0 applies a **flat additive** surcharge from **`birth_litter_size`** in `library/simulation_mortality.py`.

## Simulation context and ``save.sqlite`` flush

- Use ``with SimulationContext.create(...) as ctx`` so ``finalize_run()`` runs on exit (full checkpoint + flushed file-store). Omit the context manager only when testing **partial** persistence (e.g. ``checkpoint_simulation_to_save(..., full_snapshot=False)``) or when ``clear_world_checkpoint`` must be the last write for that sqlite file.

## Archival upsert and RAM working set

- On full checkpoint, ``simulation_people`` and ``simulation_settlements`` use ``INSERT OR REPLACE`` (no ``DELETE FROM … WHERE world`` for those tables), so **no loss** of previously written person/settlement rows.
- After the write, **RAM** prunes people dead longer than ``SimulationContext.working_set_dead_retention_years`` (default **20**) via ``library.world_save.prune_ancient_dead_from_ram``; ``father_id`` / ``mother_id`` may still point at ids that exist only on disk.
- **Resume** loads all settlements from SQLite but only **alive + recent dead** people using ``world_state.current_year`` — see ``person_belongs_in_working_ram`` in ``library/world_save.py``.
- **Birth spin-off with household move:** pass ``mother_person_id`` into ``having_sex_birth_event`` / ``generate_person_from_birth`` so spouses and dependent minors relocate with the mother when a new frontier settlement wins the spin-off roll.
- **Paramours:** ``library/simulation_social.simulation_social_annual_tick`` runs from ``record_year_summary`` — low-rate formation in the same settlement; pairs **dissolve** if ``settlement_separation`` exceeds the configured threshold.
- **Migration / soft caps:** ``effective_regional_population_cap`` = config ``carrying_capacity`` × ``region_effective_cap_multiplier`` (slow random walk each year). Multipliers and ``next_person_id`` are persisted in ``save.sqlite`` ``simulation_meta`` (``region_effective_cap_multiplier_json`` and ``next_person_id``) on **every** ``checkpoint_simulation_to_save`` (including ``full_snapshot=False``, which still flushes events and updates meta only). Full people/settlement snapshots follow the ``full_snapshot`` cadence. Restored on **resume** via ``try_load_simulation_checkpoint``. ``library/simulation_migration.simulation_migration_annual_tick`` runs **before** the drift step so out-migration uses start-of-tick caps; adults 18+ in the region (one trial per couple via ``min(person_id, partner_id)``) move to least-loaded **route-neighbor** settlements weighted by headroom and friction; **cohabiting partners move together**. ``settlement_moved`` simulation events include ``from_region_id``, ``to_region_id``, ``cross_region``, and optional ``move_reason`` (``resource_pressure_migration`` for this path). ``simulation_regions.total_population_cap`` in checkpoints is still **census**, not config capacity. Tuning workflow: ``dev_rules/migration_tuning.md``.


