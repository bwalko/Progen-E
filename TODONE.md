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

## Scale Population Simulation Toward Millions

### Completed Performance Work

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
- Still useful follow-up: run a full 250-year / 250-couple production-scale timing comparison.

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
