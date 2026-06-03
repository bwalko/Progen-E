# Migration tuning (resource pressure)

Use this when adjusting out-migration under the engine’s soft regional caps. Authoritative constants live in [`library/simulation_migration.py`](../library/simulation_migration.py); effective caps combine config `carrying_capacity` with a per-region multiplier (see [`SimulationContext.effective_regional_population_cap`](../library/simulation_context.py)) and [`CARRYING_CAPACITY_FROM_CONFIG_FRACTION`](../library/geography.py) (default `0.05` of census vs route-era capacity).

## Constants (quick reference)

| Name | Role |
|------|------|
| `MIGRATION_PRESSURE_THRESHOLD` | Minimum `census / effective_cap` before a region tries out-migration. |
| `MIGRATION_MAX_OUTFLOW_SHARE` | Upper bound on trial count as a fraction of regional population per year. |
| `MIGRATION_TRIALS_PER_EXCESS_PRESSURE` | Scales trials with pressure above the threshold. |
| `_CAP_DRIFT_LOW` / `_CAP_DRIFT_HIGH` | Random-walk multipliers on the effective cap after the move phase. |
| `_CAP_MULT_FLOOR` / `_CAP_MULT_CEIL` | Clamp for the effective-cap multiplier. |

## Recommended workflow

1. Refresh config SQLite after CSV edits: `python utils/util_load_config.py --world default` (and optional `python utils/util_check_config_sqlite_vs_csv.py --world default`).
2. Run a multi-year population scenario against the real world DB, e.g. `python utils/run_population_simulation.py --years 200` (see [`utils/run_population_simulation.py`](../utils/run_population_simulation.py) for env vars such as `POPULATION_GROWTH_SIM_SEED`, `HISTORY_SIM_RESET_WORLD`).
3. Inspect aggregate growth: `python utils/util_print_alive_by_year.py` pointing at the run’s `yearly_summary.csv` (see script `--help`).
4. If you need move-level detail, query `save.sqlite` `simulation_event_moves_readable` joined to `simulation_events` by `event_id` (or filter the view directly by `event_type = 'settlement_moved'`). Schema v7+ normalizes `moved_person_id`, `from_settlement_id`, `to_settlement_id`, `from_region_id`, `to_region_id`, `cross_region`, and `move_reason` (`resource_pressure_migration`) out of normal `payload_json`.

Change one constant at a time, re-run a fixed-seed scenario, and compare `yearly_summary.csv` and regional population proxies before/after.

Current tuning note: high-pressure regions may now plan up to **8%** outflow per year, but planned representatives are capped by the integer surplus above `MIGRATION_PRESSURE_THRESHOLD × effective_cap` so barely-over-threshold regions do not drain below the target simply because the max outflow share is higher.

## Checkpoint note

`region_effective_cap_multiplier` and `next_person_id` are written to `simulation_meta` on **every** `checkpoint_simulation_to_save` call (full snapshot rewrites all tables; partial snapshot still flushes events and updates meta). Full people/settlement snapshots remain on the `full_snapshot=True` cadence from `SimulationContext`.
