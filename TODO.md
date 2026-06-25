# TODO

## Clarified Scope Notes

- The Phonecians/Phoenician-inspired request has been implemented as a generic
  maritime mercantile expansion v1; see `TODONE.md`. Future work should treat
  that source material as inspiration for simulation patterns, not as an
  instruction to add literal Phoenician/Punic culture, names, religion, or
  historical events.
- A full commodity economy remains outside that v1 scope. Add it as a new
  explicit TODO before extending the current geography/job/knowledge-driven
  trade-network model into commodity production, cargo, prices, or supply chains.

## Completed Context: City-State Dynamics

The generic city-state V1 and V2 layers are complete; see `TODONE.md` for the
completion evidence. Future city-state work should be added as a fresh concrete
TODO only when there is a specific new generator, consequence ledger,
prose/report/browser need, measured tuning problem, or performance regression to
handle.

## Completed Context: Genome-Driven Events, Lost Records, And Chronicle Prose

The event-system foundation is complete across Workstreams 1-8. `TODONE.md`
contains the completion evidence for ontology/catalog, contextual genome scoring,
visibility/loss/rediscovery, consequences, prose, browser/query surfaces,
persistence/performance, and tests/tuning.

Future event work should be added as a fresh concrete TODO only when there is a
specific new generator, consequence ledger, prose/report/browser need, measured
tuning problem, or performance regression to handle.

## Completed Context: Remarkable Archetype Events

The rare remarkable-archetype event layer for ancient-history-inspired
individual types is complete; see `TODONE.md` for config, generation,
persistence, browser/report, and validation evidence. The configured
percentages apportion triggered rare opportunities, not the total detailed
population.

Future archetype work should be added as a fresh concrete TODO only when there
is a specific tuning drift, missing archetype/event-family mapping, prose/report
gap, browser inspection need, or measured performance problem.

## Completed Context: Gradio Navigation And Discovery

The Gradio navigation/discovery enhancement pass is complete; see `TODONE.md`
for tab-local person lookup, genealogy, place-history, Discover-tab, and
validation evidence. Future Gradio work should be added as a fresh concrete TODO
only when there is a specific navigation bug, missing detail surface, lookup
workflow, visual genealogy requirement, or measured browser performance problem.

## Polygonal World Map Generation

Context for completed functionality: polygonal map generation and the current
terrain/river/texture tuning pass are implemented; see `TODONE.md` for the
completed geometry/rendering/debug details.

Future map work should be added as a fresh concrete TODO only when there is a
specific measured visual/debug gap from generated SVGs, map debug JSON, browser
behavior, or map-seed comparison fixtures. Do not keep a broad "continue map
tuning" item open without a concrete symptom and completion boundary.

## Scale Population Simulation Toward Millions

### Current Baseline And Targets

- A fresh post-optimization 250-year run with 250 starting couples took about
  18 minutes 10 seconds and ended with 13,598 detailed alive people
  (`unit_test/population_sim_timing.tsv`, `prod_timing_250_foreground`,
  seed `320062422`).
- The previous post-indexing 250-year / 250-couple baseline took about
  24 minutes 30 seconds and ended with 14,513 alive people.
- An older pre-indexing 250-year / 250-couple run took about 2 hours 26 minutes and ended with 115,976 total people / 17,926 alive.
- Late-run cost is still too high: roughly 15K active people can make the final 10-year slices take tens of minutes.
- Near-term target: make 15K active people run the late 10 years in under 5 minutes.
- Long-term target: support populations in the millions and histories in the tens of millions.

### Save DB Storage Efficiency

Pre-alpha save-file policy:

- Do **not** spend engineering effort preserving backward compatibility for old `save.sqlite` files yet.
- Until beta, save schemas may break freely when that keeps the simulator simpler, faster, or cleaner.
- Prefer clear failure messages, delete/rebuild instructions, and fresh generated saves over migration code for old pre-alpha save files.
- Start caring about durable save migrations only once the project enters beta.

Older finding from the pre-v3 large `worlds/default/save.sqlite`:

- File size: about 2.25GB.
- `simulation_events`: 3,262,904 rows; `payload_json` is about 1.05GB of text.
- `simulation_people`: 319,939 rows; `person_json` is about 640MB of text.
- Save schema v2 through v14 already removed save-side `world` columns,
  flattened common `Person` fields into typed columns, compacted
  genome/mind-body payloads, added normalized event-person links, normalized
  common place IDs, added the first hybrid passive/cohort tables, normalized
  `settlement_moved` route detail, added default event-memory records, added
  regional domain-state rows and diffusion, added obligation/reputation/legal
  fallout/adjudication/faction/institution ledgers, and added innovation
  discovery/adoption/era state tables. See `TODONE.md`.
- Remaining bigger wins are likely:
  - continue moving high-volume event detail out of JSON when another specific event family proves hot;
  - keep moving background population from aggregate/passive compatibility paths
    toward the SQLite city-directory model in `simulation_people_nondetailed`;
  - keep JSON only for sparse detail or extension fields.
- Keep human-readable inspection as a first-class need. Prefer readable views, browser helpers, or a future derived `world.sqlite` over making the canonical save easy to inspect only by storing long JSON keys everywhere.
- Treat a generated `world.sqlite` / UI projection as a later project stage, not the primary fix. The canonical `save.sqlite` still needs to become compact because it controls write cost, resume cost, and disk growth during long runs.

### Gradio Browser Hazards

- **Do not rely on tab-select autoloading for large browser grids yet.** Autoloading grids when switching Gradio tabs appears to have an unresolved issue that can make the browser unstable or confusing. We do not understand the root cause yet.
- Prefer explicit `Load` buttons for People, Settlements, Regions, Polities, SQLite tables, and similarly heavy views until this is isolated.
- If revisiting tab autoload, make a minimal reproduction first and verify:
  - no duplicate loads fire when switching tabs;
  - active filters/world selectors are preserved;
  - selected-row state does not go stale;
  - large grids do not block or race the UI;
  - failures produce visible status text instead of silent broken tables.
- Treat any `.select(...)` handler on a Gradio `Tab` that populates a grid as risky until proven otherwise.

### Immediate Performance Work

- Latest 250-year profile row reviewed:
  `2026-06-06T02:33:21.528152+00:00`, 10 profiled late years,
  13,598 alive, 254.574 profiled seconds. The largest current buckets are
  incident generation, career reassignment, innovation, checkpoint event
  flushing, household-care indexes, job migration, government scoring, and
  trade networks.

- Next concrete performance step: run another fresh full 250-year /
  250-couple production-scale timing comparison against the 1,089.742 second
  `prod_timing_250_foreground` baseline, now that incident generation and
  career reassignment have had a bounded optimization pass.

### Hybrid Population Architecture

The likely route to millions alive is not to run full annual individual logic for everyone. Use a hybrid model:

- **Detailed people:** aggressively simulated individuals with full `Person`, genome, mind/body, relationships, events, careers, household dynamics, and government relevance.
- **Non-detailed city-directory people:** extremely light SQLite rows that exist
  in the world and count for demographics/history, but do not receive expensive
  annual simulation.
- **Aggregate cohorts:** legacy/compatibility settlement-region buckets for
  background demographic and economic mass.

Candidate passive-person fields:

- `person_id`
- `name`
- `birthyear`
- `deathyear`
- `gender / sex as needed by reproduction and demographics`
- `birthplace_region_id`
- `birthplace_settlement_id`
- `current_settlement_id`
- `job` or `job_family`
- `partner_person_id`
- `father_id`
- `mother_id`
- minimal child links or child counts
- coarse status/prosperity bucket if needed

Avoid storing genome, full trait maps, detailed annual events, extensive relationship state, and full career/economy state for passive people.

Context for completed hybrid-population functionality:
passive people, aggregate cohorts, the new `simulation_people_nondetailed`
city-directory table, save/readable tables, office promotion, marriage
promotion, migration-arrival promotion, focus promotion for user inspection and
narrative spotlighting, yearly mixed-mode summaries, and scale/calibration smoke
utilities are implemented. The latest pass also added non-detailed-first shared
promotion routing, v1 inferred backfill anchors for promoted city-directory
people, economy/migration calibration bounds, city-directory scale
reconciliation, global person-ID collision repair, and a measured government
scoring optimization; see `TODONE.md` for validation details.

Add a new concrete hybrid-population TODO only when a longer mixed-mode
replicate or UI/reporting pass shows a specific drift, promotion-source bias,
missing backfill anchor, or scale regression.

### Concrete Hybrid Follow-up: Persist Directory Names Before Promotion

- Persist lightweight display names for newly seeded and SQL-born
  `simulation_people_nondetailed` rows, then use that stored name during
  promotion instead of generating the visible name only when a row becomes
  detailed.
- Keep the implementation compatible with scale: use cached culture-specific
  name pools or another bounded deterministic strategy rather than invoking the
  full detailed-person generator for every city-directory row.
- Completion boundary: readable non-detailed views show non-empty names for
  seeded and SQL-born rows, promoted people keep the same stored name in their
  backfilled history, and focused tests cover both seeded rows and annual SQL
  births without a material seeding slowdown.

### Detailed Population Fraction

Current production-run default: when no explicit detailed cap is supplied,
detailed births are not capped. `--target-nondetailed-detailed-ratio` is
report/calibration context only for `utils/run_population_simulation.py`; it
does not derive a runtime cap. Explicit detailed caps still opt into efficiency
capping, and an explicit cap of `0` still disables the cap.

Research note: modeling 0.1% of the population or less in full detail may be
useful later for broad society-level probes, but that is not the current
default sampling target.

Challenge / calibration:

- 0.1% of 1,000,000 people is 1,000 detailed people.
- A random sample of roughly 1,000 can estimate broad population proportions to within a few percentage points under ideal probability-sampling assumptions.
- But simulation is not a clean survey:
  - selection bias matters more than raw sample size;
  - rare groups and elites will be underrepresented;
  - local settlements and small polities need much denser coverage;
  - network effects, inheritance, dynasties, migration chains, and unusual outliers do not behave like independent survey responses.

So 0.1% may be reasonable for broad society mood, cultural tendencies, and macro social attitudes if the detailed set is stratified and weighted. It is not enough by itself for politics, local stories, kinship, war leadership, technological change, or rare-event history.

Better rule:

- Keep a capped weighted detailed sample for broad social signals.
- Oversample important strata:
  - rulers and officeholders
  - wealthy/high-status households
  - frontier settlements
  - migrants
  - unusual genome/personality extremes
  - founders of lineages
  - military, religious, criminal, scholarly, and trade-network actors
  - regions or settlements near instability
- Maintain minimum detailed counts per active settlement/polity, even when the global detailed fraction is tiny.

### Detailed People Should Be More Exceptional Than Baseline

As non-detailed people expand into the storage layer for normal population mass,
the detailed-person sample should increasingly represent people with unusual
importance, variance, or narrative salience.

Completed context needed for future calibration work: detailed-selection
variance, serial-predator scoring, report/TSV calibration metrics, resume-safe
mixed-mode calibration batches, non-detailed city-directory calibration,
full-population homicide accounting, detailed-cap report fields, and
focused repeat-selection regression coverage are implemented. See `TODONE.md`
for the full completion record.

Design intent:

- Treat non-detailed people as the main home for normal/background demographic
  behavior.
- Let detailed people deviate more often from the baseline, because they are
  more likely to be selected for specialness, importance, visibility, or unusual
  outcomes.
- Do not make every detailed person extraordinary in the same direction. The
  goal is a higher-variance sample, not universal competence, heroism, villainy,
  wealth, or status.

Completion boundary:

- Latest representative non-detailed-backed probe:
  `temp/mixed_mode_nondetailed_representative_probe.tsv` plus its summary and
  promotion-reason TSVs contain one representative scenario targeting 50,000
  population, 50 years, 20 starting couples, `--detailed-fraction 0.05`, and a
  1,000-2,500 detailed cap. It produced
  `population_backend=nondetailed_directory`, `report_non_detailed_alive_people=41104`,
  `mixed_person_years=2354447`, 944 total murder rows,
  `murder_per_10k_mixed_person_years=4.009434` against target `4.000000`,
  `murder_rate_calibration_status=within_target_band`,
  `serial_murder_event_share_3plus=0.009534`,
  `serial_murder_calibration_status=within_real_life_guardrail`,
  `serial_murder_emergence_status=serial_murder_emerged`, and
  `hybrid_calibration_status=within_hybrid_calibration_targets`.
- Future calibration work should be opened only for a concrete drift or
  robustness question, such as a multi-seed replicate batch showing the
  full-population homicide rate, serial guardrail, or detailed-selection
  variance falling outside target bands.
- Keep broad demographic totals sourced from aggregate and non-detailed people,
  not from the intentionally biased detailed sample.

### Concrete Follow-up: Full 100-Couple / 300-Year Mixed-Pop Scale Smoke

- Remaining long validation: run the requested 100-couple / 300-year temp-world
  smoke when a long calibration window is acceptable:
  `python utils/run_population_simulation.py --world-id temp_mixed_pop_scale_probe --reset-world --years 300 --starting-couples 100 --seed 20260624 --use-nondetailed-directory --skip-report-files --skip-timing-log --progress`.
- Include settlement distribution in the acceptance check: hamlets/villages
  should survive in plausible numbers, some chronically distressed small sites
  may abandon, regional-service and birth-spinoff founding reasons should be
  visible, and large high-capacity settlements should carry higher
  non-detailed:detailed ratios without reducing detailed representation.
- Run this as a clean reset or from a checkpoint made after the duplicate
  re-establishment fix. The 2026-06-24 default save reached year 1059 with
  hundreds of duplicate active same-slot organic settlements and should not be
  used as distribution or runtime evidence.
- Pre-performance-fix attempts should not be treated as current evidence: the
  300-year command timed out after 300 seconds before a progress line, and a
  30-year version timed out after 600 seconds before a complete checkpoint.
- Post-performance-fix bounded evidence on Nazuna:
  - 10-couple / 10-year smoke completed in 43.50 seconds with
    `detailed_active_soft_cap_mode=disabled_default`, `detailed_alive=108`,
    `nondetailed_alive=24604`, 14 towns, 12 cities, 26 polities, 364 office
    seats, and zero alive partnered non-detailed rows missing `partner_person_id`.
  - 100-couple / 10-year smoke completed in 56.82 seconds with
    `detailed_alive=339`, `nondetailed_alive=25454`, 17 towns, 12 cities,
    28 polities, 392 office seats, and zero alive partnered non-detailed rows
    missing `partner_person_id`.

### Completed Context: Passive-To-Detailed Promotion V1

Passive and non-detailed people can now be promoted into detailed simulation by
office selection, marriage into a detailed family, migration/focal-settlement
context, user inspection, narrative spotlighting, outlaw/crime context, and rare
remarkable-archetype discovery. Shared helper paths prefer
`simulation_people_nondetailed` rows before legacy passive cohorts where the
selector has enough place/person/job/gender context.

V1 promotion generates a full `Person` state conditioned on known facts and
records inferred birth, partnership, and child anchors where those facts are
available. Add a new TODO for promotion backfill v2 only when a concrete
workflow needs grandparents, exact partner synthesis, or richer inferred past
event reconstruction.

### Data Model Direction

Keep full historical fidelity without keeping every person expensive in RAM:

- `simulation_people`: current detailed-person checkpoint table; future work may rename/split to `simulation_people_detailed` if that makes promotion semantics clearer.
- `simulation_people_nondetailed`: primary city-directory rows for
  non-detailed people: birth/death, place keys, gender/species/culture,
  job-family bucket, coarse partnered state, optional exact family links, and
  name keys.
- `simulation_people_light`: legacy/spotlight passive person rows.
- `simulation_cohorts`: legacy aggregate people by year, settlement/region, age
  band, gender/sex, culture/species, job family, status bucket.
- `simulation_events`: append-only; `event_origin` marks `generated`, `inferred`, or `backfilled`.
- `simulation_promotion_log`: record why a passive person became detailed and what was synthesized.

Important invariant:

- A passive person is real and counts historically.
- Promotion enriches the record; it should not change basic facts already established by the passive model unless explicitly repairing inconsistent data.

### Research / Design Notes

- Sample size controls random error, but representativeness controls bias. A small, well-designed sample is better than a large biased sample.
- A broad public-opinion style sample can represent society-level attitudes, but only if selection is probability-like or carefully weighted.
- For simulation, use stratified weighted sampling rather than a simple global random 0.1%.
- Game/simulation design can use "level of detail" logic: spend computation where the player/history/narrative is looking, keep the rest as aggregate state, and materialize detail when it becomes relevant.

### Event Threshold Guardrails

- Passive people and aggregate cohorts must not automatically enter detailed annual event loops.
- Existing detailed-person thresholds should remain numerically unchanged unless intentionally retuned:
  - social contact rates and same-sex couple trial rates;
  - migration pressure thresholds and outflow shares;
  - government succession/warfare/usurpation rolls;
  - job churn and fertility probabilities.
- For very large worlds, distinguish **narrative sample rates** from **worldwide incidence rates**. Current social formation loops scale by a percentage/rate of the detailed candidate pool and still have absolute safety caps; passive/cohort population should be handled by aggregate demographic deltas or explicit promotion, not by multiplying detailed event rows.
- Aggregate/passive models should produce cohort-level demographic deltas first; only promotion, user focus, or explicitly sampled narrative events should create detailed event rows.

### Proposed Milestones

1. Use late-year profiling to confirm the next hot path for ~15K active people.
2. Get 15K active people / 10 late years under 5 minutes.

