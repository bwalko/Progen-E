# Progen-E

Progen-E is a procedural history simulation project. It builds worlds full of
people, settlements, governments, events, rumors, lost records, rediscoveries,
and explorable map data.

This is not a polished game yet. It is a working simulation stack and toolkit:
run a world, inspect the save, tune the systems, and watch a long-form history
emerge from many small rules.

## Feature Overview

### Living Histories

- Simulates multi-generation populations across authored worlds.
- Tracks births, deaths, partners, children, households, jobs, moves, offices,
  incidents, and public memory.
- Uses deterministic seeded runs so the same world and seed can be replayed.
- Stores the world clock and mutable simulation state per world.
- Keeps factual history append-only while allowing in-world knowledge of that
  history to change over time.
- Supports long runs that can be resumed from checkpointed `save.sqlite` files.

### People With Inheritance

- Generates people from configurable species, ethnicities, life stages, names,
  and traits.
- Models genome and mind/body values as signed magnitudes around an ideal center.
- Mixes child genomes from parents instead of generating every child from
  scratch.
- Uses birth year as the canonical age source.
- Applies surname inheritance rules, including kin-template exceptions.
- Interprets traits into narrative identity and physique callouts.
- Preserves family anchors across detailed and passive population modes.

### Families, Partners, And Households

- Forms couples from settlement and regional candidate pools.
- Supports partner residence reconciliation when couples live apart.
- Models fertility, conception probability, litter size, and birth events.
- Handles paramour ties, distance-based dissolves, and exposure through scandal.
- Supports same-sex official couples with configurable social friction and
  prosperity/resource-pressure effects.
- Routes uncovered minors through parent, extended-family, and settlement-level
  care paths.
- Counts grandparents as partial household care support.
- Records household stress events such as childcare shortfalls and orphan routing.

### Settlements And Daily Survival

- Assigns jobs from era, skill, settlement demand, market saturation, and
  prosperity logic.
- Tracks job assignment, job loss, rehire chances, unemployment, and job-seeker
  migration.
- Models household prosperity and settlement/regional economic conditions.
- Uses food/resource pressure to influence migration, fertility, acceptance, and
  other social pressures.
- Lets regional treasury holders spend resources to improve local stability or
  prosperity.
- Records movement history with normalized readable move details.

### Migration And Expansion

- Moves people along configured and inferred region routes.
- Uses resource-pressure out-migration toward neighboring regions with headroom.
- Supports zero-point colony seeding with coast and river spacing.
- Tracks birthplace, current settlement, settlement moves, and cross-region moves.
- Preserves detailed residents and aggregate cohort balance during migration and
  checkpointing.
- Can promote passive residents into detailed people when migration into a focal
  settlement needs local context.

### Governments And Power

- Bootstraps polities from configured eras, polity types, titles, and population
  thresholds.
- Supports settlement-grain and region-grain governments.
- Can form counties, tribes, duchies, kingdoms, city states, republic-like forms,
  and other configured tiers.
- Promotes polities in place when population crosses higher-tier thresholds.
- Supports vassals when larger regional polities absorb settlement-level powers.
- Tracks office seats, office holders, dynasties, alliances, campaigns, and
  territory.
- Supports hereditary succession, merit takeovers, elections, vacancies, terms,
  usurpation pressure, and settlement-level offices.
- Lets office selection promote passive adults into detailed people when no
  detailed candidate exists.
- Includes warfare/campaign scaffolding and checkpoint persistence.

### Rare Incidents And Consequences

- Generates bounded rare incident slices from detailed people instead of flooding
  the event log.
- Current incident families include:
  - murder;
  - property crime;
  - affair scandal;
  - public virtue;
  - knowledge and culture breakthroughs.
- Scores incidents from genome tendencies, local pressure, relationships,
  witnesses, motives, and place context.
- Marks murder victims dead before same-year government succession.
- Lets scandals end paramour ties and damage official relationships.
- Lets property crime affect household prosperity and local stability.
- Lets public virtue create relief, obligations, and reputation marks.
- Lets knowledge/culture events shift regional domain state, prosperity,
  patronage obligations, and creator reputation.
- Uses catalog-backed incident variants so event flavor can be tuned through
  config.
- Supports era-tunable incident rates through configuration.

### Memory, Rumor, And Rediscovery

- Separates factual events from what people in the world know or remember.
- Creates event-memory records for public, private, rumored, lost, sealed, and
  rediscovered history.
- Ages old records through a deterministic yearly lifecycle.
- Allows ordinary in-world records to become lost over time.
- Allows lost or sealed records to resurface as linked `event_rediscovered`
  facts.
- Keeps admin truth available even when public memory has faded.
- Generates deterministic prose for factual summaries, public chronicle rows,
  rumors, lost records, and rediscoveries.
- Provides event-history reports for counts, visibility states, tracked incident
  slices, numeric metrics, and chronicle samples.

### Big-Population Mode

- Supports detailed people for high-resolution stories.
- Supports passive people for real but low-cost individuals.
- Supports aggregate cohorts for very large background populations.
- Evolves passive cohorts through aging, deaths, births, and partnered adult
  buckets.
- Absorbs detailed births above the active soft cap into passive newborn cohorts.
- Tracks mixed-mode alive counts across detailed, passive-person, and aggregate
  populations.
- Promotes passive people into detailed simulation for offices, migration
  context, and marriage into detailed families.
- Preserves passive partner and child facts so promotion can reconstruct family
  history anchors.
- Includes scale smoke and calibration utilities for 100K, 1M, and 10M target
  populations without materializing millions of detailed people.

### World Maps And Places

- Builds deterministic generated world-map geometry from authored regions.
- Uses micro-cells for terrain, elevation, moisture, river routing, and region
  ownership.
- Renders SVG maps with terrain, rivers, banks, floodplains, mouths, lakes,
  ocean shelves, settlements, labels, routes, features, and polity overlays.
- Dissolves authored-region footprints for readable region boundaries.
- Keeps coastline-aware placement for coastal landmarks and harbor markers.
- Anchors settlement pins to generated geographic features.
- Keeps settlement labels prioritized over anchor-feature labels.
- Enforces one active map thing per micro-polygon.
- Exposes map debug data for tuning and inspection.

### Names And Language Flavor

- Generates personal names and settlement/place names from config.
- Uses ethnic proto-place words and feature concepts for toponyms.
- Applies sound-law passes for Indo-European-style place-name evolution.
- Includes repair and erosion rules to keep generated names pronounceable.
- Reduces overuse of locative settlement names.
- Builds lazy geographic labels for regions and polities as populations grow.
- Supports city-dominance naming, such as a major settlement lending its name to
  a surrounding country.

### Persistence And Inspection

- Uses one folder per world under `worlds/<world_id>/`.
- Stores immutable imported config in `config.sqlite`.
- Stores mutable simulation state in `save.sqlite`.
- Uses schema-versioned save files with readable inspection views.
- Stores compact typed checkpoint rows for people, places, events, movements,
  event records, domain states, obligations, reputation marks, legal fallout,
  passive people, cohorts, promotions, and government state.
- Keeps ancient detailed dead people archived on disk while pruning old dead
  people from RAM after full snapshots.
- Restores alive and recent-dead detailed people as the working set on resume.
- Keeps event history append-only while event-memory records can change state.

### Browser And Reports

- Includes a read-only Gradio data browser for people, places, raw tables, maps,
  runs, and history.
- Provides explicit-load History views for:
  - admin factual truth;
  - public chronicle records;
  - rumors;
  - lost records;
  - rediscoveries;
  - history summary tables.
- Uses normalized person-event links for faster person timelines.
- Delegates event prose to the simulation library so browser UI stays thin.
- Keeps large browser tables behind explicit load buttons for stability.
- Writes population reports, people JSON, places GeoJSON, timing logs, and
  profiling rows from simulation runs.
- Writes event-history tuning artifacts under `temp/event_history_report/`.

### Tuning And Verification

- Keeps most behavior data-driven through CSV config imported into per-world
  SQLite files.
- Provides config schema references for worlds, geography, genomes, jobs,
  governments, events, rates, and save columns.
- Includes focused unit tests for generation, surnames, world time, checkpoints,
  population growth, household care, economy, migration, government, warfare,
  maps, event history, and mixed-mode promotion paths.
- Supports timing and profiling logs for long population runs.
- Prefers production code paths in tests so simulator behavior does not drift
  into one-off fixtures.

## Common Entry Points

- Rebuild the default world config:
  - `python utils/util_load_config.py --world default`
- Check whether default config SQLite is stale:
  - `python utils/util_check_config_sqlite_vs_csv.py --world default`
- Run a population simulation:
  - `python utils/run_population_simulation.py --years 100`
- Run a mixed-mode scale smoke:
  - `python utils/run_mixed_mode_scale_smoke.py --years 10`
- Write an event-history report:
  - `python utils/util_event_history_report.py --world default`
- Open the read-only data browser:
  - `python utils/gradio_data_browser.py`

## Useful References

- `AGENTS.md` - startup checklist and project-specific operating notes.
- `dev_rules/session_start.md` - workflow and implementation guardrails.
- `dev_rules/module_map.md` - where the main systems live.
- `dev_rules/simulation_engine.md` - run and checkpoint behavior.
- `dev_rules/config_schemas.md` - config CSV schema reference.
- `dev_rules/government.md` - polity, title, succession, and warfare notes.
- `dev_rules/genome.md` - trait magnitude and inheritance semantics.
- `TODO.md` - active design and implementation queue.
- `TODONE.md` - completed feature history.
