# Migration tuning (resource pressure)

Use this when adjusting out-migration under the engine's soft regional caps. Authoritative constants live in [`library/simulation_migration.py`](../library/simulation_migration.py) for **detailed** people and [`library/nondetailed_population.py`](../library/nondetailed_population.py) for the **city-directory** backend.

Effective caps combine config `carrying_capacity` with a per-region multiplier (see [`SimulationContext.effective_regional_population_cap`](../library/simulation_context.py)) and [`CARRYING_CAPACITY_FROM_CONFIG_FRACTION`](../library/geography.py) (default `0.05` of census vs route-era capacity).

## Detailed migration constants (quick reference)

| Name | Role |
|------|------|
| `MIGRATION_PRESSURE_THRESHOLD` | Minimum `census / effective_cap` before a region tries out-migration. |
| `MIGRATION_MAX_OUTFLOW_SHARE` | Upper bound on trial count as a fraction of regional population per year. |
| `MIGRATION_TRIALS_PER_EXCESS_PRESSURE` | Scales trials with pressure above the threshold. |
| `_CAP_DRIFT_LOW` / `_CAP_DRIFT_HIGH` | Random-walk multipliers on the effective cap after the move phase. |
| `_CAP_MULT_FLOOR` / `_CAP_MULT_CEIL` | Clamp for the effective-cap multiplier. |

## Non-detailed SQL migration constants

| Name | Role |
|------|------|
| `NONDETAILED_MIGRATION_REGIONAL_PRESSURE_BASE` | Regional census/cap ratio must exceed this before outflow is considered. |
| `NONDETAILED_MIGRATION_FOOD_PRESSURE_BASE` | Food-pressure term baseline. |
| `NONDETAILED_MIGRATION_MAX_OUTFLOW_SHARE` | Per-settlement annual outflow cap as a share of local directory population. |
| `NONDETAILED_MIGRATION_OUTFLOW_PRESSURE_SCALE` | Scales outflow share with computed `source_pressure`. |

## Save-backed diagnostics

Before retuning, measure the current save:

```bash
python utils/util_audit_movement_rates.py --world default
python utils/util_audit_abandoned_settlements.py --world default
```

- Movement summary: `temp/movement_rate_audit.tsv` plus `temp/movement_rate_audit.yearly.tsv`
- Abandoned settlements vs mixed alive counts: `temp/abandoned_settlement_audit.tsv`

Interpretation hints:

- `nondetailed_migrant_share_vs_alive_end` near `1.0` means aggregate directory churn is far too high.
- Abandoned rows with `nondetailed_alive > 0` indicate evacuation failed (should be fixed by abandon-time relocation).

## Recommended workflow

1. Refresh config SQLite after CSV edits: `python utils/util_load_config.py --world default` (and optional `python utils/util_check_config_sqlite_vs_csv.py --world default`).
2. Run a multi-year population scenario against the real world DB, e.g. `python utils/run_population_simulation.py --years 200` (see [`utils/run_population_simulation.py`](../utils/run_population_simulation.py) for env vars such as `POPULATION_GROWTH_SIM_SEED`, `HISTORY_SIM_RESET_WORLD`).
3. Inspect aggregate growth: `python utils/util_print_alive_by_year.py` pointing at the run's `yearly_summary.csv` (see script `--help`).
4. If you need move-level detail, query `save.sqlite` `simulation_event_moves_readable` joined to `simulation_events` by `event_id` (or filter the view directly by `event_type = 'settlement_moved'`). Schema v7+ normalizes `moved_person_id`, `from_settlement_id`, `to_settlement_id`, `from_region_id`, `to_region_id`, `cross_region`, and `move_reason` (`resource_pressure_migration`) out of normal `payload_json`.
5. Re-run the audit utilities above and compare before/after one constant change.

Change one constant at a time, re-run a fixed-seed scenario, and compare `yearly_summary.csv`, audit TSVs, and regional population proxies before/after.

Current tuning note: high-pressure regions may now plan up to **8%** detailed outflow per year, but planned representatives are capped by the integer surplus above `MIGRATION_PRESSURE_THRESHOLD × effective_cap` so barely-over-threshold regions do not drain below the target simply because the max outflow share is higher.

Non-detailed directory migration was retuned downward (2026-06-26) after movement audits showed aggregate migrant share near total alive population; abandonment now evacuates directory residents before a site is marked `abandoned`.

Settlement abandonment (2026-06-26) uses **directory mixed viability** (`detailed_alive + nondetailed_alive`), not detailed-only census. A site abandons only when mixed population is empty, stays below `ABANDON_MIXED_POP_THRESHOLD` for multiple years, suffers sustained multi-year economic distress (`ABANDON_DISTRESS_*` metrics), or has an absorption founding reason (`absorbed`, `merged`, …). Viable low-resolution sites with directory residents but no detailed people promote ~1% (`settlement_low_resolution_sample`) instead of abandoning. Audit categories: `abandoned_empty`, `abandoned_economic`, `promoted_low_resolution_sample` in `temp/abandoned_settlement_audit.tsv`.

## Mixed-mode performance guidance

`utils/run_population_simulation.py` defaults to `--use-nondetailed-directory`. Use `--use-passive-cohorts --passive-population-scale 0` for **detailed-only** narrative runs without directory mass.

Profile short matched runs on a laptop:

```bash
python utils/run_population_simulation.py --reset-world --years 10 --starting-couples 100 --seed 639789854 --profile-last-years 10 --progress
python utils/run_population_simulation.py --reset-world --years 10 --starting-couples 100 --seed 639789854 --use-passive-cohorts --passive-population-scale 0 --profile-last-years 10 --progress
```

Inspect `unit_test/population_sim_profile.tsv` for `nondetailed.sql_migration`, `runner.nondetailed_directory`, and `summary.social`. Enable the directory backend when you need large mixed population counts and can accept extra annual SQL migration cost; prefer detailed-only for short narrative probes unless you need non-detailed census mass.

## Checkpoint note

`region_effective_cap_multiplier` and `next_person_id` are written to `simulation_meta` on **every** `checkpoint_simulation_to_save` call (full snapshot rewrites all tables; partial snapshot still flushes events and updates meta). Full people/settlement snapshots remain on the `full_snapshot=True` cadence from `SimulationContext`.
