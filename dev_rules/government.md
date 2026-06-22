# Government & polity simulation

Runtime polities, hereditary and merit offices, vassals, campaigns, and alliances live in RAM on `SimulationContext` and persist to **`save.sqlite`** (not `config.sqlite`).

## Modules

| Area | Module |
|------|--------|
| Leadership composites | `library/leadership.py` |
| Config CSV → catalog | `library/government_catalog.py` |
| RAM dataclasses & helpers | `library/polity.py` |
| Annual tick (bootstrap, succession, vacancies, calls warfare) | `library/simulation_government.py` |
| Campaigns, battles, alliances hooks | `library/simulation_warfare.py` |
| DDL + checkpoint/load | `library/government_checkpoint.py` |
| Economy “ruler” credit | `library/simulation_economy.py` (`person_holds_government_treasury_seat`) |

## Tick order

`SimulationContext.record_year_summary` runs **`simulation_government_annual_tick`** after household care and **before** economy so the same tick’s treasury logic can credit current seat-holders.

### Feudal bootstrap tier (population, scaled)

Among types listed in the era’s `allowed_polity_type_ids`, **`pick_polity_type_for_region_population`** (in `library/government_catalog.py`) picks the **highest** tier whose `min_population_to_form` (from `government_polity_types.csv`) is met by **alive people in that region**; otherwise the **lowest** tier in the list.

CSV thresholds are **real-world counts** and are multiplied by `world_start.population_scale` (the same global modifier used for region carrying capacity in [`library/geography.py`](../library/geography.py); see [`population_scale_for_world`](../library/geography.py)) before comparison. With the default feudal ladder (`kingdom;duchy;county`), default real-world thresholds (**county** ≥ 5,000 / split 50,000, **duchy** ≥ 50,000 / split 1,000,000, **kingdom** ≥ 1,000,000), and the default `population_scale=0.05`, effective alive thresholds are **county≈250**, **duchy≈2,500**, **kingdom≈50,000**. Small worlds remain at the county tier; raising `population_scale` (or lowering CSV thresholds) yields kingdoms sooner.

### Counties per region & lazy naming

- **Settlement-grain polities** (`jurisdiction_grain=settlement` in `government_polity_types.csv`, e.g. **county**, **band**, **city_state**) get one open `simulation_polity_territory` row per qualifying **settlement** (`target_kind=settlement`, `target_id=<settlement_id>`), not one row for the whole region. A region can therefore host **multiple** counties (one per anchor settlement that clears the scaled `min_population_to_form` for that tier).
- **Region-grain** types (tribe, duchy, kingdom, republic, …) still hold a single `(target_kind=region, target_id=<region_id>)` row. When a new region-tier polity is bootstrapped (e.g. a **duchy** appears because regional population crosses that tier), existing independent settlement-grain polities in the same region that are marked **vassalizable** in the catalog become **vassals** (`parent_polity_id` set on the child polity).
- **Lazy geographic names**: until regional alive count reaches a scaled naming floor (`NAMING_MIN_POPULATION_REAL` × `population_scale` in `library/place_namer.py`), `_region_display` uses a short placeholder. Above the threshold, `region_geographic_label` runs once per region and the result is stored in `SimulationContext.region_display_label_overrides`, persisted under `simulation_meta` (`region_display_label_overrides_json`). Polities created with placeholder names are renamed with `polity_geographic_label` when their scoped population crosses the same threshold (`polity_named` events). **City takeover**: if one settlement dominates the region (ratio and margin vs the second-largest settlement), the override may switch to `{settlement} Country`; if the dominance condition fails for several consecutive years, the label can revert to a fresh geographic label (hysteresis in `region_naming_aux_json`).
- **`polity_for_region`** returns only a polity with an open **region** row for that `region_id` (settlement-only counties do not satisfy this). Use **`polities_in_region`** to list every active polity tied to a region (region rows plus settlements in that region). **`polity_for_settlement`** resolves the settlement-grain owner of a settlement.

Existing polities **do** promote in place each tick via `_maybe_promote_polity` when their alive population crosses a higher tier's scaled threshold; landless polities are dissolved by `_dissolve_landless_polities`.

### Settlement-level leadership

Universal titles (`polity_type_id='*'` in `government_titles.csv`, role `settlement_merit`) are sized per settlement by `_ensure_settlement_offices` each tick under **every active polity** that either owns the settlement’s **region** (region-grain realm) **or** holds that settlement as **settlement-grain** territory (e.g. a county anchored on that settlement). Each settlement gets seat counts derived from `min_population_for_first_holder` and `pop_per_holder` (both interpreted as real-world counts and scaled by `world_start.population_scale`):

- `settlement_leader` — 1 holder once a settlement reaches 100 (real-world) people.
- `settlement_alderman` — 1 holder at 600 real-world people, +1 every additional 500.

Surplus seats (when a settlement shrinks) are not removed; their merit cycle simply leaves them vacant.

### Hereditary vs merit takeover (succession)

Each death-vacated hereditary seat (titles with `salic_primogeniture` / `cognatic_primogeniture` selection) first rolls `government_titles.merit_takeover_chance` in `_succession_tick`. On a hit, the seat is filled by `_fill_merit_or_election` (an "ambitious leader" displaces the heir) and the resulting `office_succession` event records `via=merit_takeover` with `previous_holder_id`; on a miss the primogeniture chain runs and the event records `via=hereditary`. Default tuning: counts mix freely (`0.40`), dukes mostly hereditary (`0.10`), kings nearly always hereditary (`0.05`); tribal chiefs `0.20`, city basileis `0.15`. The in-life `usurp_base_chance` follows the same gradient (highest for counts, lowest for kings). Universal merit titles (`settlement_leader`, `settlement_alderman`) and other `merit_*` / `election_*` selection rules don't enter this path — they are always merit/elected via `_fill_vacancies` and `_term_expiry`.

### Force-backed office scoring

`government_titles.force_authority_01` marks titles whose authority plausibly depends on personally credible force or physical enforcement. `_government_scored_candidate_pool` and head-seat pickers keep the existing `male_weight`, leadership/military/career-fitness, and childcare-duty terms, then apply `library.work_body_fit` as a soft multiplier only for titles with force demand. Low-force civic offices should remain mostly leadership/career driven; high-force offices such as chiefs, generals, kings, sheriffs, bailiffs, dukes, and counts receive additional body-power weighting. Era tools and `world_start.magic_physical_leveling_01` soften this demand the same way they do for jobs.

## Config CSVs (`config/`)

Imported into the world’s `config.sqlite` with the rest of the config (see `dev_rules/config_schemas.md` summary table):

- `government_eras.csv` — historical-year bands → allowed polity types + default succession style.
- `government_polity_types.csv` — grain (region/settlement), head title, split thresholds, vassal flags.
- `government_titles.csv` — selection rules (`merit`, hereditary styles), term years, male weights, force-authority demand, usurp parameters.
- `government_starting_polities.csv` — optional seed rows (optional for auto-bootstrap).

Reload after edits::

    python utils/util_load_config.py --world default

## Save tables (`save.sqlite`)

Created/migrated via `ensure_checkpoint_schema` → `government_checkpoint.ensure_government_schema`:

- `simulation_polities`, `simulation_polity_territory` — open territory uses `until_sim_year IS NULL`.
- `simulation_office_seats`, `simulation_office_holdings` — seat definitions vs time-bounded holdings.
- `simulation_dynasties`, `simulation_alliances`, `simulation_campaigns`.

These save tables follow the single-world save schema and do not store a `world` column; `worlds/<id>/save.sqlite` is the world boundary.

Full snapshots call `checkpoint_government` from `checkpoint_simulation_snapshot`; resume loads via `load_government` inside `try_load_simulation_checkpoint`.

## CLI: polity map

Print open **region** ownership from a save DB::

    python utils/util_print_polity_map.py --world-id default
    python utils/util_print_polity_map.py --save worlds/default/save.sqlite

## Population report

`library/population_growth_runner.build_population_growth_report` accepts optional **`ctx=`** and appends a short “Government (end-of-run RAM state)” appendix when provided (`write_population_growth_report_files` passes the context through).

## Tests

- `unit_test/test_simulation_government.py` — bootstrap, settlement-grain counties, promotion + vassals.
- `unit_test/test_place_namer.py` — geographic labels, naming threshold, lazy `_region_display` / city takeover.
- `unit_test/test_simulation_warfare.py` — no-op campaign advance smoke.
- `unit_test/test_save_checkpoint.py` — includes government checkpoint round-trip.

## Resume vs `HISTORY_SIM_RESET_WORLD`

Environment `HISTORY_SIM_RESET_WORLD=1` deletes `save.sqlite` only when **starting** a run (`start_year` is not `None`). Resume (`start_year=None`) must keep the save so `try_load_simulation_checkpoint` can restore people and regional meta.
