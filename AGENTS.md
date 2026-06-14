# History Project Agent Guide

Use this file first when starting a new session in this repository.

## Primary References

- `dev_rules/session_start.md` - startup checklist, workflow order, and production-first code placement (`library` vs `unit_test` vs `temp/`; see §4 guardrails).
- `dev_rules/genome.md` - genome trait semantics and RNG design.
- `dev_rules/config_schemas.md` - config CSV schema reference.
- `dev_rules/module_map.md` - where key logic lives across `library/`, `utils/`, and `unit_test/`.
- `dev_rules/government.md` - polities, titles, warfare tick, save tables, `util_print_polity_map.py`.
- `dev_rules/known_gotchas.md` - current mismatches and common pitfalls.
- `dev_rules/migration_tuning.md` - workflow and knobs for resource-pressure migration vs `yearly_summary.csv` / `MIGRATION_*` constants.

## Fast Start

1. Reload config data into the **per-world** config SQLite (default world: `worlds/default/config.sqlite`):

   - `python utils/util_load_config.py --world default`
   - Optional: confirm the DB is at least as new as the CSVs — `python utils/util_check_config_sqlite_vs_csv.py --world default` (exits `1` if stale or missing).

   `SimulationContext.create(..., refresh_config=None)` (**auto**: refresh when the world save has no checkpoint people, on `reset_world_for_test` / `HISTORY_SIM_RESET_WORLD`, or when passing `start_year`). Set **`refresh_config=True`** to always rebuild from CSV; use **`refresh_config=False`** for a **temporary or fixture** `config.sqlite` (for example in unit tests) so imports do not overwrite the database under test. See `dev_rules/simulation_engine.md`.

2. Run unit tests:

   - `python -m unittest unit_test.test_world_time unit_test.test_birth_surname_rule unit_test.test_population_growth_100_years unit_test.test_simulation_migration unit_test.test_save_checkpoint unit_test.test_simulation_government unit_test.test_simulation_warfare unit_test.test_place_namer`

   Longer population runs use the same canonical report files as that test (only the reported duration changes): `python utils/run_population_simulation.py --years 400`

   Local machine guidance: hostname **Nazuna** is the fast desktop and can run aggressive/full test or simulation checks. Other machines, including the current laptop, should prefer targeted tests and short smoke runs unless the user explicitly asks for a long/aggressive run.

3. For population simulation requests, prefer runtime override patterns in commands (for example changing `STARTING_COUPLES`) instead of editing tests unless asked.

4. **Working set vs save.sqlite (long runs):** Full checkpoints **upsert** `simulation_people` and `simulation_settlements` (historical rows stay in `save.sqlite`; no blanket `DELETE` those tables on snap). After each full snapshot, **`prune_ancient_dead_from_ram`** drops from `ctx.people` anyone dead longer than **`working_set_dead_retention_years`** (default **20**). **`try_load_simulation_checkpoint`** only hydrates alive + recent-dead into RAM; **`simulation_events`** remain append-only. New simulation features that add per-person state should stay compatible with this pattern (bounded RAM, full history on disk).

## Tracking Docs

- After every substantive repo change, do a completion pass on `TODO.md` and `TODONE.md`: record completed tracked work in `TODONE.md`, remove or update the matching actionable item in `TODO.md`, and note in the final response if no tracking-doc edit was warranted.
- `TODO.md` should contain only actionable remaining work plus the minimum context needed to choose and implement the next task.
- Completed functionality belongs in `TODONE.md`, not as long "already done" prose in `TODO.md`.
- If completed context must remain in `TODO.md` because later work depends on it, label it clearly as context for completed functionality needed by next functionality.
- Every workstream must have a realistic completion boundary. Do not turn a completed workstream into a never-ending list of speculative follow-ups; create a new TODO only when the next item is concrete, useful, and worth the added runtime or maintenance cost.

## Project Facts To Preserve

- **Progen-E** layout: each world has a folder under `worlds/<world_id>/` with **`config.sqlite`** (imported from `config/*.csv`, not written during simulation) and **`save.sqlite`** (mutable simulation state: `world_state` clock plus **`simulation_*`** checkpoint/state tables for people, settlements, couples, event records, regional domain states, obligation ledgers, reputation marks, legal fallout rows, and append-only **`simulation_events`**). `SimulationContext.record_year_summary` and **`finalize_run()`** persist to the save DB.
- Legacy root **`Progen-E.sqlite`** (if it still exists from older layouts) is obsolete; the project uses **`worlds/<world_id>/config.sqlite`**. You may delete the legacy file after confirming nothing references it.
- `world_start` is immutable config (in `config.sqlite`); runtime time progression is stored in **`save.sqlite`** (`world_state`).
- `Person.birthyear` is canonical; age is always `simulation_year - birthyear`.
- Genome values are signed magnitudes around the ideal centerpoint (see `dev_rules/genome.md`).
