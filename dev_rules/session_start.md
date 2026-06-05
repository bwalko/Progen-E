# Session Start Checklist

Use this checklist at the beginning of every coding session.

## 1) Initialize data dependencies

- Run: `python utils/util_load_config.py --world default` (writes `worlds/default/config.sqlite` from `config/*.csv`).
- Optional check before long runs or when debugging odd config errors: `python utils/util_check_config_sqlite_vs_csv.py --world default` (fails if `config.sqlite` is missing or older than any `config/*.csv` mtime).
- If tests rely on custom temp DBs, keep those isolated to test setup (do not modify another world’s DB in tests). Use **`SimulationContext.create(..., refresh_config=False)`** when loading a temp `config.sqlite` so the CSV import step does not replace your fixture.

## 2) Understand the execution flow

- Random generation entrypoints:
  - `library.generator.generate_person_random`
  - `library.generator.generate_person_from_birth`
- Shared random helpers:
  - `library.random_traits`
  - `library.random_names`
- Simulation clock:
  - `library.world_time`

## 3) Validate baseline behavior

- Run targeted tests first:
  - `python -m unittest unit_test.test_world_time`
  - `python -m unittest unit_test.test_birth_surname_rule`
- Run broader simulation test when needed (same one-liner as `AGENTS.md` Fast Start):
  - `python -m unittest unit_test.test_world_time unit_test.test_birth_surname_rule unit_test.test_population_growth_100_years unit_test.test_simulation_migration unit_test.test_save_checkpoint`

## 4) Implementation guardrails

- Do not hardcode ages from life stage names; use species thresholds and `birthyear`.
- Keep config as source-of-truth CSVs under `config/`; regenerate SQLite after config edits.
- Prefer function parameters/overrides for experiments (for example simulation year, couple count in runtime command wrappers) over editing constants unless requested.
- Keep outputs deterministic in tests via explicit RNG seeding.
- **Production simulator vs tests:** Implement simulation behavior in `library/` (and in `utils/` only for maintained CLIs that call into `library`). `unit_test/` should construct real `SimulationContext` / checkpoint paths, call production APIs, and assert outcomes. Use `unittest.mock` to stub side effects or prove a hook is still wired—not to host logic that the running simulator never executes. If a helper is needed (serialization, payload fields, resume hydration), add it next to the owning module in `library/` (private functions are fine); avoid parallel implementations under `unit_test/` or one-off copies under `temp/` that drift from production.

## 5) Tracking-doc discipline

- `TODO.md` should contain only actionable remaining work plus the minimum context needed to choose and implement the next task.
- Completed functionality belongs in `TODONE.md`, not as long "already done" prose in `TODO.md`.
- If completed context must remain in `TODO.md` because later work depends on it, label it clearly as context for completed functionality needed by the next task.
- Every workstream should have a realistic completion boundary. Do not turn a completed workstream into a never-ending list of speculative follow-ups; create a new TODO only when the next item is concrete, useful, and worth the added runtime or maintenance cost.
