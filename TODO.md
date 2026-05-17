# TODO

## Scale Population Simulation Toward Millions

### Current Baseline

- A 250-year run with 250 starting couples ended with 115,976 total people and 17,926 alive.
- Wall time was about 2 hours 26 minutes, which is a major improvement over the prior 250/250 run.
- Late-run cost is still too high: roughly 15K active people can make the final 10-year slices take tens of minutes.
- Near-term target: make 15K active people run the late 10 years in under 5 minutes.
- Long-term target: support populations in the millions and histories in the tens of millions.

### Save DB Storage Efficiency

Current finding from `worlds/default/save.sqlite`:

- File size: about 2.25GB.
- `simulation_events`: 3,262,904 rows; `payload_json` is about 1.05GB of text.
- `simulation_people`: 319,939 rows; `person_json` is about 640MB of text.
- The `world = default` column is obvious dead weight in save tables because the world folder already names the save. It is not the largest byte source, but removing it should simplify keys/indexes and stop repeating a value that cannot vary inside one save.
- The biggest wins are likely:
  - flatten common `Person` fields into real columns;
  - normalize repeated settlement/region text IDs into integer keys;
  - redesign high-volume event payloads so common ids/roles are columns or relation rows;
  - keep JSON only for sparse detail or extension fields.
- Compacting current `person_json` with JSON separators would save roughly 53MB before any schema change. Short-key JSON can save more, but raw short keys make database peeking worse.
- Keep human-readable inspection as a first-class need. Prefer readable views, browser helpers, or a future derived `world.sqlite` over making the canonical save easy to inspect only by storing long JSON keys everywhere.
- Treat a generated `world.sqlite` / UI projection as a later project stage, not the primary fix. The canonical `save.sqlite` still needs to become compact because it controls write cost, resume cost, and disk growth during long runs.

Implementation direction:

1. Flatten hot `simulation_people` fields into columns; leave sparse extension data in JSON.
2. Store genome/mind-body data as a compact versioned representation or fixed trait table/array rather than repeating 30 long JSON trait keys per person.
3. Normalize settlement/region IDs to integer surrogate keys while retaining readable slugs in lookup tables.
4. Redesign `simulation_events` around common columns plus `simulation_event_people(event_id, person_id, role)` for timeline queries; reserve JSON for rare detail.
5. Consider optional verbose event logging for debugging-heavy runs.

### Immediate Performance Work

- Run late-year profiling on serious population runs:

  ```powershell
  $env:HISTORY_SIM_PROFILE_LAST_N_YEARS='10'
  python utils/run_population_simulation.py --years 250 --starting-couples 250 --progress
  ```

- Run a full 250-year / 250-couple production-scale timing comparison after the local performance work.

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

- `simulation_people_detailed`: full JSON/person payload for detailed or promoted people.
- `simulation_people_light`: minimal passive person rows.
- `simulation_cohorts`: aggregate people by year, settlement/region, age band, gender/sex, culture/species, job family, status bucket.
- `simulation_events`: append-only; include a flag for generated vs inferred/backfilled events.
- `simulation_promotion_log`: record why a passive person became detailed and what was synthesized.

Important invariant:

- A passive person is real and counts historically.
- Promotion enriches the record; it should not change basic facts already established by the passive model unless explicitly repairing inconsistent data.

### Research / Design Notes

- Sample size controls random error, but representativeness controls bias. A small, well-designed sample is better than a large biased sample.
- A broad public-opinion style sample can represent society-level attitudes, but only if selection is probability-like or carefully weighted.
- For simulation, use stratified weighted sampling rather than a simple global random 0.1%.
- Game/simulation design can use "level of detail" logic: spend computation where the player/history/narrative is looking, keep the rest as aggregate state, and materialize detail when it becomes relevant.

### Proposed Milestones

1. Run a full 250-year / 250-couple production-scale timing comparison.
2. Get 15K active people / 10 late years under 5 minutes.
3. Add a minimal passive-person schema and import/export path.
4. Prototype passive births/deaths/partnerships at settlement or cohort level.
5. Add passive-to-detailed promotion for one event type, probably office selection or marriage into a detailed family.
6. Add inferred/backfilled event tagging.
7. Run mixed-mode simulations at 100K, 1M, and 10M historical scale.
