# Simulation engine bootstrap

Reference for integrating and testing full simulation runs (`SimulationContext`) against the per-world SQLite layout under `library/world_paths.py`.

## Paths

| Item | Location |
|------|----------|
| World folder | [`world_directory(world_id)`](library/world_paths.py) (`worlds/default` when `world_id` is `"default"`) |
| Immutable config DB | [`config_db_path(world_id)`](../library/world_paths.py) → `worlds/<id>/config.sqlite` |
| Mutable save DB | Beside config → `save.sqlite` ([`derive_save_db_path_from_config`](../library/world_paths.py)) |
| Run scratch / CSV store roots | `worlds/<id>/temp/` (each run typically uses `temp/simulation_run_<uuid>/` via `SimulationFileStore`) |

Ensure the world directory and `temp/` exist before a run; [`ensure_world_directories`](../library/world_bootstrap.py) does this (`SimulationContext.create` calls it).

`save.sqlite` schema v3 is single-world and uses compact people checkpoint rows: runtime tables do **not** include a `world` column, common `Person` fields live in typed `simulation_people` columns, and `person_json` keeps compact extension payload such as genome/mind-body arrays keyed through `config/genome_save_columns.csv`. The folder path (`worlds/<id>/`) is the world identity. The immutable `config.sqlite` tables still keep their `world` column because they are imported from shared CSV config.

## Reset world for tests

**Flag:** `reset_world_for_test` on `SimulationContext.create` ([`simulation_context.py`](../library/simulation_context.py)), **or** environment variable **`HISTORY_SIM_RESET_WORLD`** set to `1`, `true`, or `yes` (combined with OR).

When enabled:

- Deletes `save.sqlite` for that world **only when starting a scheduled run** (`start_year` is not `None`). **Resume** (`start_year=None`) keeps the existing save so `try_load_simulation_checkpoint` can restore people and regional meta (see `dev_rules/government.md`).
- You still typically want CSV refresh afterward (see below); the bootstrap path aligns with integration tests that restart from a known blank save each run.

**Warning:** Parallel test workers must not share the same world folder; overwriting `worlds/default` concurrently will corrupt SQLite.

## When `config/*.csv` is imported into `config.sqlite`

On `SimulationContext.create` ([`simulation_context.py`](../library/simulation_context.py)), parameter `refresh_config`: `True` \| `False` \| **`None` (auto)**

| `refresh_config` | Behavior |
|------------------|----------|
| `True` | Always reload all `config/*.csv` into the per-world `config.sqlite`. |
| `False` | Never reload; use existing `config.sqlite` on disk. |
| `None` (default) | **Auto:** refresh if **reset-world** is on; **else** refresh if `save.sqlite` has **no** `simulation_people` checkpoint rows; **else** refresh if **`start_year`** is passed to `SimulationContext.create` (a new scheduled run clears checkpoints and is treated like a fresh CSV pull). Otherwise skip refresh when resuming (`start_year is None`) with an existing population checkpoint. Explicit `refresh_config=False` still wins. |

## Zero-point multi-colony foundation

**Default colony count:** **3** (`DEFAULT_FOUNDATION_COLONY_COUNT` in [`zero_point_colonies.py`](../library/zero_point_colonies.py)).

For a **zero-point** run (`zero_point_foundation=True` on `SimulationContext.create`):

- Only a subset of regions get primary settlements (coast/river picks spaced by route friction; one per continent when possible).
- Each colony has a distinct **species/ethnic** pair and **10** male/female founder couples by default (`COUPLES_PER_FOUNDATION_COLONY`).
- Founder **birth years** are staggered; each founder is fertile at the start year and has **at least 10 simulation years** remaining before `max_fertility_age` (when that field is set).

Foundation specs can be passed explicitly as `FoundationColonySpec` or left default (auto regions + Human/Middle English, Dwarf/Old Norse, Gnome/Old English).

## Annual tick ordering (zero-point)

[`simulate_calendar_year_ordered_settlements`](../library/zero_point_colonies.py) processes **pairing and births** in **colony order** (settlement 1, then 2, then 3, …) with people scoped by `birthplace_region_id`. **Mortality** is applied **once per year for everyone** after all colony passes (birth ordering is sequential; mortality is still global).

## Upcoming work

- **Movement** between settlements / regions is not implemented; people keep their founding `birthplace_region_id` unless generation rules change elsewhere.
- Cross-region coupling and migration should be explicitly added later.
