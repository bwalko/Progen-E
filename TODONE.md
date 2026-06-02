# TODONE

## Better SVG Map Data Plan

### Completed Geometry Foundation Work

- Added a generated world-map geometry layer:
  - deterministic geometry versioning
  - route-aware continent and region layout
  - dependency-light bounded Voronoi-style region polygons
  - generated region features, route edges, and river paths

- Refactored settlement-local geography to consume pre-existing region geography:
  - settlement pins now anchor to generated region features
  - local geography JSON includes `source_geometry_version`
  - local geography JSON includes normalized `region_cell_polygon`
  - existing settlement/browser JSON shape remains compatible

- Added focused regression coverage:
  - byte-stable deterministic world geometry
  - valid cells and feature anchors for every configured region
  - renderable route edge points
  - settlement site slots mapped to generated geography features
  - existing Gradio place browser rendering remains covered

### Completed Polygonal Terrain Pass

- Replaced the original TODO plan with implemented micro-cell terrain generation in `library.world_map_geometry`:
  - deterministic continent-clipped micro-region cells
  - authored-region ownership layered over those micro-cells, preserving existing region IDs for saves, settlements, births, and routes
  - elevation and moisture values per micro-cell
  - terrain-family assignment from generated physics plus authored region cues
  - micro-cell adjacency and route paths through shared cell borders
  - river paths that carry segment ownership, reach coastal boundaries, and avoid stacking on identical channels
  - river channel, bank, mouth, corridor, floodplain, and carved-land polygons for SVG rendering
  - aggregated authored-region footprints derived from owned micro-cells

- Updated map rendering/debug coverage:
  - visible micro-cell terrain layer with terrain texture/shade semantics
  - river-water, bank, corridor, mouth, and cut-mask layers
  - settlement, feature, polity, label, and route overlays preserved above the terrain
  - SVG data attributes expose micro-cell IDs for inspection

- Added focused regression coverage for the polygonal terrain pass:
  - every configured region owns multiple micro-cells
  - micro-cell elevation, moisture, river distance, terrain family, coastal flags, and floodplain/channel flags stay in valid ranges
  - river segments preserve local region ownership
  - coastal rivers terminate on coastline boundaries
  - channels are not duplicated across rivers
  - settlement and arbitrary point projection stay inside the declared authored-region footprint
  - debug SVG output includes the noisy map, river, and overlay layers

### Completed World Map Polish Pass

- Switched the visible SVG region-boundary layer from anonymous micro-edge snippets to dissolved authored-region footprints:
  - one data-rich `region-boundary` path per configured region
  - boundary paths reuse the region cells aggregated from same-region micro-cells
  - the dissolved boundary layer renders under coast and lake styling so coastline/water presentation stays authoritative
- Preserved micro-edge segments for terrain blending, hillshade, and coast rendering.
- Confirmed explicit ocean and lake water-cell layers remain part of the SVG/debug test surface.

## Scale Population Simulation Toward Millions

### Completed Performance Work

- Added durable late-year profiling records:
  - `utils/run_population_simulation.py --profile-last-years N` sets the profiling window for one run
  - console output still prints the phase breakdown
  - `unit_test/population_sim_profile.tsv` records one row per profiled phase with run metadata
  - a small `profile_smoke` run verified the recording path

- Built shared annual indexes so expensive modules do not repeatedly scan all alive people:
  - `children_by_parent`
  - `household_members_by_adult`
  - `dependent_minor_count_by_adult`
  - `grandparent_relief_by_household`
  - `alive_by_settlement`
  - `alive_by_region`
  - `largest_active_settlement_id`

- Used deterministic capped samples for settlement/regional behavior and candidate decisions:
  - default cap: 1,000 people per decision pool
  - groups at or below the cap use the full population
  - exact counts, persistence, and conservation-style accounting remain exact
  - decision samples are seeded by year, scope, world, and run salt so results are reproducible
  - implemented first targets: government candidate pools, treasury leader-spend choices, warfare/usurpation candidates, settlement social contact/couple candidate pools
  - future behavior pools should keep using this pattern while exact accounting paths stay unsampled

- Tuned resource-pressure migration for the default 5% population scale:
  - raised `MIGRATION_MAX_OUTFLOW_SHARE` from `0.045` to `0.08` so highly over-cap regions relieve pressure faster
  - capped planned migration representatives by the surplus above `MIGRATION_PRESSURE_THRESHOLD × effective_cap` so mild pressure does not over-drain below the target
  - added regression coverage for both high-pressure outflow and near-threshold surplus behavior

- Refactored household/care duty hot paths:
  - `childcare_duty_factor`
  - `dependent_minors_in_implicit_household`
  - `_household_ids_for_job_move`
  - callers in careers, household care, economy, and government
  - indexed path is now the default for annual callers; legacy scan is retained only as a fallback/test comparison path

- Cached per-year resource pressure facts:
  - effective regional cap
  - alive count by region
  - food pressure by settlement
  - computed pressure by person where needed
  - annual callers now share `YearResourceFacts` across careers, economy, migration, social pressure checks, birth pressure checks, and zero-point colony births

- Cached species/life-stage lookup data for `refresh_current_people_life_stages`:
  - context now builds one `(species, ethnic) -> species row` lookup from cached species config
  - annual life-stage refresh reuses that lookup for every living person instead of querying SQLite per person

- Measured checkpoint and file-store cost separately from simulation logic:
  - yearly file-store timing is split into yearly-summary staging, current-people snapshot staging, and flush-if-due
  - save timing is split into full checkpoint snapshots vs lighter event/meta checkpoints
  - finalization timing separates run-store event import, final full checkpoint, and final file-store flush
  - final report timing separates text report build/write, people JSON build/write, and places GeoJSON build/write

### Completed Milestone Work

- Built annual household/resource indexes and profiled the final years on smoke runs.
- Ran a post-indexing 250-year / 250-couple production-scale timing comparison:
  - `unit_test/population_sim_timing.tsv` includes a 1,470 second run ending with 14,513 alive people.
  - Future full-scale comparisons are still useful after meaningful new performance changes.

### Completed Save DB Storage Work

- Added explicit `save.sqlite` schema versioning:
  - `SAVE_SCHEMA_VERSION`
  - `save_metadata(save_schema_version)`
  - `PRAGMA user_version`
  - future-schema guardrails so newer saves fail clearly instead of opening silently

- Added a safe rebuild path for schema upgrades:
  - writes a fresh SQLite file first
  - copies/transforms data through the target schema
  - can swap the original only when explicitly requested

- Removed the save-side `world` column in schema v2:
  - `worlds/<world_id>/save.sqlite` is now the world boundary
  - core simulation checkpoint tables are single-world
  - government checkpoint tables are single-world
  - config tables still keep `world` because they are imported from shared CSV config

- Updated save consumers and tests for the v2 schema:
  - world clock stores one row per save
  - checkpoint/bootstrap logic counts people without a world filter
  - Gradio browser tolerates both old and new save schemas for inspection
  - polity map utility no longer needs `--sim-world`

- Added compact `simulation_people` checkpoint storage in save schema v3:
  - common `Person` fields are stored as typed columns for cheaper writes, reads, sorting, and filtering
  - `person_json` now keeps extension payload only, currently compact genome/mind-body arrays and derived trait tags
  - checkpoint resume reconstructs full `Person` records from columns plus extension JSON
  - Gradio people browsing reads either compact columns or older JSON-only rows

- Compacted genome and mind/body checkpoint payloads:
  - added `config/genome_save_columns.csv` as the configurable slot-to-trait mapping
  - save payloads use short keys (`g` for genome, `mb` for mind/body) with trait values stored as arrays
  - runtime `Person.genome` / `Person.mind_body` remain named dictionaries so existing simulation logic keeps using trait names

### Completed Save Schema v4 Event Normalization

- Added typed `simulation_events` columns for common query keys:
  - `primary_person_id`
  - `secondary_person_id`
  - `settlement_id`
  - `region_id`
- Added `simulation_event_people(event_id, person_id, role)` for person timeline queries.
- New event writes populate both the typed columns and relation rows.
- Existing v3 event payloads are backfilled when the save schema is ensured or rebuilt.
- Gradio person timelines prefer `simulation_event_people` and fall back to JSON scanning for legacy/fixture saves.
- `payload_json` remains for sparse event detail and human-readable inspection.

### Completed Save Schema v5 Place-Key Normalization

- Normalized high-volume settlement/region references to integer surrogate keys:
  - `simulation_region_lookup(region_key, region_id)`
  - `simulation_settlement_lookup(settlement_key, settlement_id, region_key)`
  - `simulation_people` birthplace/current settlement place columns
  - `simulation_events` common settlement/region columns
  - `simulation_regions` and `simulation_settlements` checkpoint rows
- Added readable inspection views:
  - `simulation_regions_readable`
  - `simulation_settlements_readable`
  - `simulation_events_readable`
- Rebuild logic can transform v3/v4 text-place saves into the v5 key schema.
- Normal event writes compact duplicate settlement/region slugs out of `payload_json`; `SimulationContext.create(..., verbose_event_logging=True)` or `run_population_simulation.py --verbose-event-logging` preserves raw self-contained event payloads for debugging-heavy runs.

### Completed Save Schema v6 Hybrid Population Foundation

- Added `library.passive_population`:
  - `PassivePerson`
  - `PassivePersonRecord`
  - `PassiveCohort`
- Added runtime state and helpers on `SimulationContext`:
  - `passive_people`
  - `passive_cohorts`
  - `add_passive_person(...)`
  - `add_passive_cohort(...)`
  - `_record_inferred_simulation_event(...)`
- Added passive/cohort save tables:
  - `simulation_people_light`
  - `simulation_cohorts`
  - `simulation_promotion_log`
- Added readable inspection views:
  - `simulation_people_light_readable`
  - `simulation_cohorts_readable`
- Added `simulation_events.event_origin`:
  - `generated` for ordinary simulation events
  - `inferred` / `backfilled` for promotion and reverse-generation history
  - compact event payloads omit `event_origin` just like duplicated place slugs
- Checkpoint save/load now roundtrips passive people and cohorts.
- Passive people share the global `person_id` sequence but do **not** enter `current_people_ids`, detailed alive counts, social contact loops, government candidate pools, migration loops, or other detailed-person event thresholds.
- Added regression tests for passive-person/cohort roundtrips, inferred event provenance, and passive people staying out of detailed alive counts.

### Completed Save Schema v7 Movement Event Normalization

- Added `simulation_event_moves` for high-volume `settlement_moved` details:
  - moved person id
  - from/to settlement keys
  - from/to region keys
  - cross-region flag
  - move reason
  - deferred-move request/apply years and source/group labels
- Added `simulation_event_moves_readable` so movement history can be inspected without rehydrating JSON payloads.
- New `settlement_moved` writes populate the normalized move table and compact duplicate movement detail out of normal `payload_json`.
- Verbose event logging still preserves raw self-contained movement payloads for debugging runs.
- Schema ensure/rebuild can backfill movement rows from older raw/verbose `settlement_moved` payloads when the route detail is still present.

### Completed Hybrid Mixed-Mode Population Pass

- Replaced static background cohort snapshots in the canonical population runner with passive demographic evolution:
  - initial passive cohorts are seeded from regional capacity minus detailed residents;
  - later cohorts age forward by one year;
  - passive deaths are applied by age;
  - passive births produce age-0 aggregate newborn cohorts;
  - adult cohorts track a coarse `partnered` status bucket for passive partnership mass;
  - detailed births above the configured detailed-active soft cap are absorbed into passive newborn cohorts instead of generating full `Person` records.

- Added passive-to-detailed promotion for office selection:
  - government head-seat and merit/election vacancy paths can promote a passive adult when the detailed candidate pool is empty;
  - the source aggregate cohort is decremented when a person is promoted;
  - promotion records inferred events so generated history can distinguish synthesized anchors from ordinary annual simulation.

- Added plausible full `Person` synthesis from passive facts:
  - promotion preserves known birth year, gender, species, ethnic/culture, birthplace/current settlement, partner id, death year, and coarse prosperity/status where known;
  - missing genome, mind/body, names, fertility ages, appearance, and derived traits are generated through the normal person generator;
  - passive `species` and `ethnic` now roundtrip through `simulation_people_light` so post-checkpoint promotion keeps culture/species context.

- Added fuller mixed-mode reporting:
  - `yearly_summary.csv` now includes `detailed_alive_count`, `passive_person_alive_count`, `aggregate_cohort_alive_count`, `aggregate_cohort_partnered_count`, `mixed_mode_alive_count`, `passive_cohort_births_count`, and `passive_cohort_deaths_count`;
  - `run_population_simulation.py` exposes `--detailed-active-soft-cap` and still prints detailed plus latest aggregate cohort counts at run end.

- Added focused regression coverage:
  - passive cohorts age, birth, die, and keep deterministic counts;
  - passive newborn cohorts are retained even when background population scale is zero;
  - office promotion materializes a passive candidate into a detailed `Person` with genome/mind-body state;
  - passive person checkpoint roundtrip preserves species/ethnic fields.

- Added richer passive family/partnership records:
  - `PassivePerson` now carries coarse partner name/birth/death years, partnership start/end years, passive child ids, and child birth years;
  - `simulation_people_light` and `simulation_people_light_readable` persist/expose these fields;
  - office promotion synthesizes partner and child timing facts when promoting from a partnered aggregate cohort;
  - passive promotion emits inferred `promotion_backfill_partnership` and `promotion_backfill_children` events so later narrative/detail systems can see the established passive family anchors;
  - checkpoint and promotion regression tests cover passive family roundtrip and inferred family backfill events.

- Added passive-to-detailed promotion for marriage into detailed families:
  - after normal same-settlement and same-region pairing, a bounded fallback can promote single adult passive people as spouses for unmatched detailed adults;
  - the source aggregate cohort is decremented and the promoted person enters the normal detailed couple path;
  - regression coverage verifies the passive spouse promotion, cohort decrement, and `marriage_into_detailed_family` promotion event.

- Added `utils/run_mixed_mode_scale_smoke.py` for hardware-friendly scale preflights:
  - seeds one active aggregate settlement per configured region;
  - evolves passive/cohort demographics for target historical populations without materializing detailed people;
  - writes `temp/mixed_mode_scale_smoke.tsv`;
  - default 10-year smoke targets completed locally:
    - 100K target: 94,890 aggregate alive, 14,741 cohort rows, 1.17s;
    - 1M target: 948,862 aggregate alive, 19,525 cohort rows, 1.71s;
    - 10M target: 9,488,947 aggregate alive, 22,569 cohort rows, 1.64s.

- Added `utils/run_mixed_mode_calibration.py` for full-runner mixed-mode calibration:
  - calls the canonical population-growth runner instead of only exercising passive cohorts directly;
  - seeds one aggregate active settlement per configured region so passive population can scale across the whole authored world;
  - bounds detailed people with a configurable target fraction and min/max detailed cap;
  - writes `temp/mixed_mode_calibration.tsv`;
  - local 3-year smoke with 5 starting couples and 50-500 detailed cap completed:
    - 100K target: 98,239 mixed alive, 12 detailed alive, 17,211 cohort rows, 5.10s;
    - 1M target: 982,097 mixed alive, 13 detailed alive, 23,220 cohort rows, 9.51s;
    - 10M target: 9,820,933 mixed alive, 12 detailed alive, 24,853 cohort rows, 9.44s.
  - local 10-year calibration with 10 starting couples and 100-1,000 detailed cap completed:
    - 100K target: 94,906 mixed alive, 37 detailed alive, 14,702 cohort rows, 28.59s;
    - 1M target: 949,001 mixed alive, 27 detailed alive, 19,582 cohort rows, 38.03s;
    - 10M target: 9,489,032 mixed alive, 26 detailed alive, 22,746 cohort rows, 37.97s.

- Tuned passive fertility for target-stable 10-year mixed-mode calibration:
  - raised `_PASSIVE_BIRTH_RATE` from `0.105` to `0.165`;
  - local 10-year calibration with 10 starting couples and 100-1,000 detailed cap now completes near target:
    - 100K target: 99,976 mixed alive, 37 detailed alive, 14,702 cohort rows, 28.39s;
    - 1M target: 999,948 mixed alive, 27 detailed alive, 19,582 cohort rows, 37.14s;
    - 10M target: 9,998,875 mixed alive, 26 detailed alive, 22,746 cohort rows, 40.19s.

### Event Threshold Decision

- Passive/cohort scale does not feed detailed-person event loops automatically.
- Social formation budgets now scale by the detailed candidate pool, with absolute safety caps:
  - `PARAMOUR_CONTACT_TRIAL_SHARE_OF_ELIGIBLE = 0.75`
  - `PARAMOUR_CONTACT_TRIAL_ABSOLUTE_CAP = 5_000`
  - `SAME_SEX_TRIAL_SHARE_OF_ELIGIBLE = 0.12`
  - `SAME_SEX_TRIAL_ABSOLUTE_CAP_PER_POOL = 1_000`
- These are detailed narrative sampling rates, not total incidence rates for passive/cohort populations.

## Gradio Data Browser

### Stable Browser Patterns

- Keep heavy data grids behind explicit load actions until the tab-autoload issue is understood.
- Existing browser helpers support manual loading/filtering for people, settlements, regions, polities, map detail, and raw SQLite tables.
- Person timeline lookups now use normalized event-person links when available.

### Not Done

- Tab-select autoloading for Gradio grids is intentionally **not** considered solved. See `TODO.md` before adding or keeping `.select(...)` handlers on tabs that populate large tables.
