# TODO

## Scale Population Simulation Toward Millions

### Current Baseline And Targets

- A post-indexing 250-year run with 250 starting couples took about 24 minutes 30 seconds and ended with 14,513 alive people (`unit_test/population_sim_timing.tsv`).
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
- Save schema v2/v3/v4/v5/v6 already removed save-side `world` columns, flattened common `Person` fields into typed columns, compacted genome/mind-body payloads, added normalized event-person links, normalized common place IDs, and added the first hybrid passive/cohort tables. See `TODONE.md`.
- Remaining bigger wins are likely:
  - continue moving high-volume event detail out of JSON when a specific event family proves hot;
  - use `simulation_people_light` / `simulation_cohorts` for background population instead of creating every background person as a full `Person`;
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

- Run late-year profiling on the next serious population run; profile rows are now recorded in `unit_test/population_sim_profile.tsv`:

  ```powershell
  python utils/run_population_simulation.py --years 250 --starting-couples 250 --progress --profile-last-years 10
  ```

- Run a fresh full 250-year / 250-couple production-scale timing comparison only after meaningful new performance changes. A post-indexing comparison already exists in `unit_test/population_sim_timing.tsv`.

### Hybrid Population Architecture

The likely route to millions alive is not to run full annual individual logic for everyone. Use a hybrid model:

- **Detailed people:** aggressively simulated individuals with full `Person`, genome, mind/body, relationships, events, careers, household dynamics, and government relevance.
- **Passive people:** extremely light placeholders that exist in the world and count for demographics/history, but do not receive expensive annual simulation.
- **Aggregate cohorts:** settlement/region-level buckets for background demographic and economic mass.

Candidate passive-person fields:

- `person_id`
- `name`
- `birthyear`
- `deathyear`
- `gender` / sex as needed by reproduction and demographics
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

Initial foundation already exists:

- `library.passive_population.PassivePerson`
- `library.passive_population.PassiveCohort`
- `SimulationContext.passive_people`
- `SimulationContext.passive_cohorts`
- `SimulationContext.add_passive_person(...)`
- `SimulationContext.add_passive_cohort(...)`
- save schema v6 tables/views:
  - `simulation_people_light`
  - `simulation_people_light_readable`
  - `simulation_cohorts`
  - `simulation_cohorts_readable`
  - `simulation_promotion_log`

First-pass aggregate background cohort generation now exists in the canonical population runner:

- each active settlement receives yearly `simulation_cohorts` rows;
- cohort totals are based on configured regional carrying capacity minus detailed residents;
- cohorts count in Gradio place stats through `simulation_cohorts_readable`;
- cohorts do not enter detailed annual event loops.

Still missing: richer passive births/deaths/aging, passive relationship dynamics, passive-to-detailed promotion, and mixed-mode population reports.

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
- `simulation_people_light`: minimal passive person rows.
- `simulation_cohorts`: aggregate people by year, settlement/region, age band, gender/sex, culture/species, job family, status bucket.
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
3. Replace the first-pass static background cohorts with passive births/deaths/aging/partnerships at settlement or cohort level.
4. Add passive-to-detailed promotion for one event type, probably office selection or marriage into a detailed family.
5. Generate plausible `Person` state from passive facts without replaying yearly event rolls.
6. Add mixed-mode reports that separately show detailed, passive, and cohort counts.
7. Run mixed-mode simulations at 100K, 1M, and 10M historical scale.
