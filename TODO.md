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

## Completed Context: Genome-Driven Events, Lost Records, And Chronicle Prose

The event-system foundation is complete across Workstreams 1-8. `TODONE.md`
contains the completion evidence for ontology/catalog, contextual genome scoring,
visibility/loss/rediscovery, consequences, prose, browser/query surfaces,
persistence/performance, and tests/tuning.

Future event work should be added as a fresh concrete TODO only when there is a
specific new generator, consequence ledger, prose/report/browser need, measured
tuning problem, or performance regression to handle.

## Sanity Checks and Bugfixes

- I see zero evidence of potential serial killer like lives. If anything, the propensity for murder has become much more one-off. I simmed 200 years with not a single person committing more than one murder. Remember that the crime rates (and all other rates) need to take into consideration the full population, which includes all non-detailed people.

- When I looked at the table in SQLite for the non-detailed lightweight people, it had zero records. This may be because it just hasn't been fully implemented yet.

- The non-detailed to detailed person ratio is still very small, it probably needs to be closer to 100-1 than 8-1.

## Paramour Fertility And Out-Of-Wedlock Children

Add support for paramour relationships to produce children.

Completion boundary:

- Confirm how paramour relationships are represented in the current relationship
  model and fertility/birth logic.
- Allow paramour pairs to be eligible for conception/birth when biologically and
  socially appropriate.
- Enforce the existing invariant that no woman can have more than one birth in
  a simulation year, even if she has both spouse and paramour relationships.
- Mark children born from paramour relationships as out of wedlock / bastards in
  person state, event payloads, and any relevant browser or report surfaces.
- Add focused regression coverage for:
  - a paramour relationship producing a child;
  - spouse and paramour eligibility in the same year producing at most one birth
    for the woman;
  - the resulting child carrying an out-of-wedlock/bastard marker.

## Polity Office History And Ruler Timelines

Make it easy to inspect who held polity positions over time, especially the top
position.

Completion boundary:

- Confirm where current officeholding, succession, abdication, usurpation,
  vacancy, and death-in-office events are persisted.
- Add or expose a durable office-history view/table keyed by polity, office,
  person, start year, end year, and end reason.
- Ensure the top position for each polity has a simple chronological timeline
  suitable for browser inspection and future chronicle prose.
- Add browser/report affordances that answer "who ruled this polity, and when?"
  without hand-querying event JSON.
- Add focused regression coverage for normal succession and at least one
  interrupted tenure path such as death, removal, usurpation, or vacancy.

## Genome Trait Center/Extreme Classification And Impact Module

Create a dedicated trait-impact module that classifies each genome trait's
center and extremes, then uses those classifications to make meaningful
behavioral, social, health, economic, and relationship consequences more common
for people at the center or extremes.

Design intent:

- Do not treat ordinary midpoint values in the middle of the double-bell curve
  as inherently meaningful.
- Lean harder into people whose traits are near the ideal center, roughly
  +/-10 from 0, or in the +/-90 extremes.
- Make extreme traits matter in practical, real-world ways. Traits such as
  fixation, domineering behavior, visible deformity, derangement, clinically
  compulsive behavior, or wastefulness with money should often create serious
  consequences, including real harm.
- Keep effects grounded in the simulation's social, medical, household,
  economic, legal, and reputation systems rather than only in flavor text.

Completion boundary:

- Inventory all genome traits and classify each trait's center, mild bands,
  strong bands, and extreme bands.
- Define which traits have beneficial center effects, harmful center effects,
  harmful extremes, useful extremes, or context-dependent extremes.
- Add reusable APIs for trait-band lookup and practical consequence generation.
- Route high-impact trait effects into existing systems where possible:
  mortality/health, work capacity, household stability, finances, violence,
  reputation, legal fallout, marriage/paramour dynamics, care burden, and
  social standing.
- Add tests that prove ordinary midpoints stay mostly ordinary while center or
  extreme values measurably affect outcomes.

## Childbirth Mortality

Add death from childbirth as a simulation possibility.

Completion boundary:

- Identify the birth pipeline and determine where maternal mortality can be
  evaluated without creating duplicate births or broken parent/child links.
- Model childbirth death risk with age, health/body condition, prior births,
  settlement care capacity, and relevant trait or prosperity modifiers.
- Record maternal death in person state, events, family relationships, and any
  affected household-care or dependent-child logic.
- Ensure the newborn outcome is handled separately from the mother's outcome,
  including stillbirth/infant-death hooks only if those are already supported or
  explicitly added in this workstream.
- Add regression coverage for mother survives birth, mother dies from
  childbirth, and parent/child records remaining coherent after the death.

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

- Use the latest 250-year profile row
  (`2026-06-06T02:33:21.528152+00:00`) to choose the next measured hot path.
  The largest late-year buckets are currently incident generation, career
  reassignment, innovation, checkpoint event flushing, household-care indexes,
  job migration, government scoring, and trade networks.

- Run another fresh full 250-year / 250-couple production-scale timing
  comparison only after the next meaningful performance changes; use the
  1,089.742 second `prod_timing_250_foreground` row as the current baseline.

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

Context for completed functionality needed by remaining hybrid-population work:
passive people, aggregate cohorts, the new `simulation_people_nondetailed`
city-directory table, save/readable tables, office promotion, marriage
promotion, migration-arrival promotion, focus promotion for user inspection and
narrative spotlighting, yearly mixed-mode summaries, and scale/calibration smoke
utilities are implemented; see `TODONE.md` for details.

Remaining hybrid-population work in this section:

1. Make `simulation_people_nondetailed` the default background-population path
   for production runs once larger benchmark and smoke results are reviewed.
2. Calibrate the v1 non-detailed job-family economy and migration effects
   against longer mixed-mode runs:
   - verify food-deficit, military-burden, craft/trade surplus, care/admin,
     prosperity, market-pull, and stability deltas stay in plausible ranges;
   - verify set-based non-detailed migration follows prosperity, food pressure,
     route access, headroom, stability, and market pull without draining source
     settlements too aggressively.
3. Route office, marriage, migration-context, focus, outlaw, war, crime,
   disaster, and discovery promotion hooks through non-detailed directory rows
   before falling back to legacy passive cohorts.
4. Add a maintained command/API for immediate or next-year promotion from the
   non-detailed directory by person id, settlement, region, job family, or
   reason.
5. Continue late-year profiling and optimization toward the 15K active / under
   5 minutes target.

### Detailed Population Fraction

Idea: model 0.1% of the population or less in full detail.

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

Completed context needed for the next calibration step: detailed-selection
variance, serial-predator scoring, report/TSV calibration metrics, resume-safe
mixed-mode calibration batches, and focused repeat-selection regression coverage
are implemented. See `TODONE.md` for the full completion record.

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

- Run and review longer `utils/run_mixed_mode_calibration.py` outputs comparing
  detailed promoted/founder variance against non-detailed city-directory
  demographic totals, using `--replicates N` plus the aggregate summary TSV
  when multiple targets or seeds are generated.
- For full serial-emergence proof, use a run shape that can reach roughly
  1.25M detailed person-years at the configured 4 murders per 10K detailed
  person-years. Current medium probes are useful for rate direction but not
  enough for the 100-murder guardrail or 500-murder emergence gates.
- Latest retained calibration chunk:
  `temp/mixed_mode_rate_gate_chunk.tsv` plus its summary and promotion-reason
  TSVs now contain two representative bundled-Python scenarios targeting 50,000
  population, 50 years, 20 starting couples, `--detailed-fraction 0.05`, and a
  1,000-2,500 detailed cap. The retained aggregate has 34,998 detailed
  person-years, 16 murders, murder rate `4.571690` per 10K detailed person-years
  against the configured target `4.000000`, target ratio `1.142922`,
  `murder_rate_calibration_status=within_target_band`, 8 serial-predator
  profiles out of 2,353 scored detailed people
  (`serial_predator_profile_share_of_scored_detailed=0.003400`), max serial
  propensity `0.769639`, and
  `hybrid_calibration_status=needs_more_serial_guardrail_sample`. This proves
  the 10-murder rate gate for the current run shape; the next gate is the
  100-murder serial guardrail sample.
- Next continuation command should resume the retained chunk rather than start
  over, for example:
  `python utils/run_mixed_mode_calibration.py --targets 50000 --replicates 20
  --years 50 --starting-couples 20 --detailed-fraction 0.05
  --min-detailed-cap 1000 --max-detailed-cap 2500
  --stop-after-total-murders 100 --write-incremental --resume-existing --output
  temp\mixed_mode_rate_gate_chunk.tsv`. After the 100-murder serial guardrail
  gate is reached, inspect `serial_murder_calibration_status` and
  `serial_murder_event_share_3plus` before deciding whether to proceed to
  `--stop-after-total-murders 500`.
- Earlier paused probe: the same representative shape with 6 replicates but
  without incremental output timed out at 300 seconds before producing row or
  summary artifacts; use `--write-incremental` for any future long probe.
- Representative bundled-Python probes can keep birth-settlement spin-off
  enabled because settlement naming and duplicate-site checks fall back when
  optional Shapely/world-map geometry is unavailable. Use
  `--disable-birth-settlement-spinoff` only for deliberate event-rate stress
  tests, not fully representative settlement-geography runs.
- Use calibration statuses rather than single small-run rates for retuning:
  murder-rate status is meaningful after at least 10 observed murders,
  serial-murder guardrail status after at least 100 observed murders, and
  serial emergence after at least 500 observed murders. Long probes should use
  `--write-incremental`, `--resume-existing`, and explicit sample-stop flags so
  partial evidence is retained and the aggregate summary names the next gate.
- Tune the numeric values for the reason-specific variance profiles for ruler,
  officeholder, elite, specialist, criminal/outlaw, religious, migrant/frontier,
  kinship-link, inspection, and spotlight promotion reasons after real
  mixed-mode runs show whether any class is too flat or too exaggerated.
- Verify 3+ murder repeat killers remain under the 1% murder-share guardrail in
  longer mixed-mode runs while the 500-murder emergence status is
  `serial_murder_emerged`, not `no_serial_murder_emerged`.
- If longer representative runs still show `no_serial_murder_emerged`, inspect
  whether generated repeat-capable profiles are too rarely present in active
  murder-eligible pools before changing the global homicide rate; the focused
  selection proof already shows the multiplier can produce guarded emergence
  when such a profile is present.
- Keep broad demographic totals sourced from aggregate and non-detailed people,
  not from the intentionally biased detailed sample.

### Passive-To-Detailed Promotion

Passive people can be promoted into detailed simulation by an event:

- inheritance or succession
- office selection
- marriage into a detailed family
- migration into a focal settlement
- war, crime, disaster, discovery, or scandal
- becoming economically or socially important
- user inspection
- random spotlight selection for narrative richness

When promoted:

- Generate a genome and full `Person` state conditioned on known facts.
- Fill in plausible current traits, career fitness, household prosperity, and social state.
- Backfill parents, grandparents, partner, and children as needed.
- Generate plausible past events only where useful, and mark them as inferred/backfilled.
- Do not replay "did they / did they not" yearly logic. The known facts are constraints: they were born, partnered, had children, migrated, held a job, or died because the passive model already established those outcomes.

This is almost a reverse generator:

- Current simulator: genome/person + yearly events produce life history.
- Promotion generator: known life-history anchors produce plausible genome/person + missing events.

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

