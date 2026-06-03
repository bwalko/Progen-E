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

`save.sqlite` schema v8 is single-world and uses compact checkpoint rows: runtime tables do **not** include a `world` column, common `Person` fields live in typed `simulation_people` columns, and `person_json` keeps compact extension payload such as genome/mind-body arrays keyed through `config/genome_save_columns.csv`. `simulation_events` keeps sparse factual `payload_json` detail but also stores common query keys (`primary_person_id`, `secondary_person_id`, `settlement_id`, `region_id`), `simulation_event_people(event_id, person_id, role)` for person timelines, `simulation_event_moves` / `simulation_event_moves_readable` for normalized `settlement_moved` route details, and `simulation_event_records` / `simulation_event_records_readable` for in-world/admin memory records attached to factual events. Event records can transition through states such as `lost`, `sealed`, `rumored`, and `rediscovered`; rediscovery can create a linked factual `event_rediscovered` row. `library.event_memory_lifecycle` now runs after annual save checkpoint persistence and reviews a bounded deterministic shard of old persisted records so ordinary in-world history can fade, be lost, or resurface while admin factual events stay append-only. The folder path (`worlds/<id>/`) is the world identity. The immutable `config.sqlite` tables still keep their `world` column because they are imported from shared CSV config.

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

## Movement and Migration

- **Residence movement is implemented.** `Person.current_settlement_id` is mutable residence, while `birthplace_*` fields stay immutable for genealogy.
- Resource-pressure migration runs from `record_year_summary` through `library.simulation_migration.simulation_migration_annual_tick`.
- Job-seeker migration can move households during the careers tick.
- Move events are logged as `settlement_moved` in `simulation_events`; normal runs store route details in `simulation_event_moves` and expose readable `from_region_id`, `to_region_id`, `cross_region`, and `move_reason` through `simulation_event_moves_readable`. Verbose event logging keeps those fields in `payload_json` too.
- Cross-region coupling can happen indirectly after residence changes and social ticks; tune or extend that behavior in the social/migration modules rather than treating movement as absent.

## Genome-Driven Incidents

`library.simulation_incidents.simulation_incidents_annual_tick` currently runs after household care and before government. Current generators sample bounded adult candidate pools by settlement and score rare genome-driven incidents from trait propensities plus local pressure:

- `murder` records structured killer/victim/witness/motive/place/genome context and marks victims dead before same-year government succession runs. Murder event-memory records default to `violent_crime_record` with `rumored` visibility. The murder generator is tuned as a detailed-population homicide rate through `config/incident_rates.csv` (`target_per_10k_per_year`, currently about 4 murders per 10,000 detailed medieval people per year), population-scaled settlement trials, and a population-scaled annual cap, while still requiring a sufficiently high violent-actor propensity candidate.
- `property_crime` records non-lethal theft/fraud/extortion incidents with perpetrator/target/witness/motive/loss/place/genome context. Property-crime event-memory records default to `property_crime_record` with `rumored` visibility, but the current slice does not yet mutate household or economy balances. Property-crime visibility is also era-tuned in `config/incident_rates.csv`; the first medieval row deliberately raises the chance/cap multipliers after the murder undercount fix suggested the non-lethal crime slice was too quiet too.
- `affair_scandal` records non-lethal exposure/rumor incidents for existing paramour pairs where at least one participant has a spouse/partner outside the paramour tie. Payloads include accused person, paramour, betrayed partner(s), witnesses, motive, place, exposure score, and genome context. Scandal event-memory records default to `scandal_record` with `rumored` visibility, but the current slice does not yet dissolve couples or paramours. Scandal visibility has a smaller era multiplier in `config/incident_rates.csv` so it can be reviewed alongside crime without treating it as total crime incidence.
- `public_virtue` records positive public events such as heroic rescue, public mercy, arbitration, or loyal service. Payloads include benefactor, beneficiary, witnesses, motive, place, relief value, public-virtue score, and genome context. Public-virtue event-memory records default to `public_virtue_record` with `public_known` visibility, but the current slice does not yet mutate reputation, patronage, health, or economy balances.
- `knowledge_culture` records inventions, discoveries, legal precedents, artistic triumphs, or scholarly breakthroughs. Payloads include creator, optional patron, witnesses, motive, knowledge domain, novelty value, place, knowledge/culture score, and genome context. Knowledge/culture event-memory records default to `knowledge_record` with `public_known` visibility, but the current slice does not yet mutate technology, law, culture, jobs, or polity doctrine.

`library.event_prose` derives deterministic authored prose from existing event and event-record readable rows. Use it for factual admin summaries and public chronicle text instead of storing long generated prose in `save.sqlite`; the text remains traceable to `event_id`, `record_id`, and `prose_variant_key`.

`utils.gradio_data_browser` exposes those prose rows through an explicit-load History tab for admin truth, public chronicle records, rumors, lost records, and rediscoveries. Keep this surface button-driven for large event tables; do not add tab-select autoloads for event grids until the browser tab-load issue is understood.

`library.event_history_report` and `utils/util_event_history_report.py` provide the first event-rate/prose tuning artifact. A fixed-seed 80-year `event_tuning_sample` run with 80 starting couples and seed `20260603` now produces reviewable counts across all five tracked incident slices without flooding the save. Normal simulation also runs `library.event_memory_lifecycle.event_memory_lifecycle_annual_tick_for_save` after the annual save checkpoint, so old public/private/rumored records can become `lost` and old lost/sealed records can resurface as linked `event_rediscovered` facts.
