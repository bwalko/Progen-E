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

## Follow-Up: Innovation Catalog Curation And Balance

- Review `config/innovations.csv` after parser generation:
  - replace rough Earth-specific source titles with stronger local analogue
    names where needed;
  - mark questionable rows `unreviewed` or `inactive`;
  - add `prerequisite_ids` for obvious dependencies;
  - tune category ranks, starter prevalence, spreadability, and complexity for
    gameplay rather than source-order fidelity.
- Run a short fixed-seed smoke after major catalog edits and compare:
  - startup seeded innovation counts by year;
  - military frontier advancement rates;
  - same-polity vs route diffusion counts;
  - prosperity and port-network score deltas from adopted innovations.

## Primary Task: Genome-Driven Events, Lost Records, And Chronicle Prose

Build a simulation layer where genomes, relationships, offices, household stress,
settlement pressure, and historical context can produce memorable events:
crimes, scandals, discoveries, acts of mercy, betrayals, reforms, rescues, and
private incidents that may or may not survive into the historical record.

This should become a first-class narrative/history system, not just a pile of
random event rows. Every event should have:

- a real simulation cause;
- involved people and places;
- consequence hooks where appropriate;
- record visibility and preservation state;
- prose suitable for chronicles, admin inspection, and later UI display.

Important new rule:

- **Every event can be lost, hidden, distorted, rediscovered, or visible only to
  the simulation admin.** This includes existing event types such as births,
  deaths, couple formation/dissolution, migration, job events, succession,
  warfare, and settlement moves. The admin/debug surface may inspect the full
  truth, but in-world historical memory should be partial and recoverable.

### Design Principles

- Genome values are propensities, not deterministic moral labels. A murder,
  betrayal, invention, heroic rescue, or reform should require genome fit plus
  circumstance: relationship conflict, scarcity, status pressure, officeholding,
  proximity, faction tension, grief, insult, debt, war, migration, or opportunity.
- Preserve the centered genome semantics: `0` is ideal, moderate magnitudes are
  ordinary, and extreme magnitudes are rare/dysfunctional or uncanny depending on
  direction and trait.
- Avoid flooding `simulation_events`. Use rates, caps, settlement/polity focus,
  importance scoring, and passive/cohort promotion only where a detailed event is
  narratively or historically worth materializing.
- Separate "what happened" from "what was recorded". A true event may be:
  witnessed, rumored, sealed, forgotten, misattributed, rediscovered, or known
  only through admin/debug views.
- Make prose evocative but grounded. The event text should feel like a chronicle,
  court roll, household memory, rumor, inscription, or later reconstruction,
  while still linking back to structured payload data.

### Workstream 1: Event Ontology And Catalog

Goal: define event families, structured fields, trigger context, consequence
hooks, visibility behavior, and prose slots before writing many event generators.

Initial event families to catalog:

- **Violent crime:** assault, duel, feud killing, murder, attempted murder,
  domestic killing, assassination attempt.
- **Property and survival crime:** theft, livestock theft, storehouse robbery,
  debt evasion, fraud, extortion, hoarding during scarcity.
- **Sexual and household scandal:** affair exposed, disputed parentage,
  elopement, coerced patronage, abandonment, neglect, inheritance fraud.
- **Political crime:** conspiracy, treason, forged claim, bribery, sabotage,
  usurpation plot, false accusation.
- **Religious/cultural conflict:** heresy accusation, sacrilege, false prophecy,
  cult founding, censorship, purge, iconoclasm.
- **Public virtue:** rescue, mercy, arbitration, loyal service, whistleblowing,
  refusal of unjust orders.
- **Knowledge and culture:** invention, discovery, legal precedent, artistic
  triumph, famous performance, scholarly breakthrough, failed experiment.
- **Private life that may later matter:** grudge formed, secret kindness,
  blackmail, rumor, hidden patronage, apprenticeship breakthrough, mental
  collapse, hermitage, adoption/fostering.

For each event type, define:

- event key;
- event family;
- minimum people/place context;
- genome traits/composites that raise/lower probability;
- non-genome preconditions;
- likely witnesses;
- possible consequences;
- default historical importance range;
- preservation/loss defaults;
- prose tone variants.

Do not try to implement the entire catalog at once. The first vertical slice
should cover a small but representative set:

1. murder or feud killing;
2. theft or fraud;
3. affair/scandal;
4. heroic rescue or public mercy;
5. invention/discovery or legal precedent.

### Workstream 2: Genome And Context Scoring

Goal: create reusable scoring helpers so event systems can ask "who is likely to
do this under these conditions?" without hardcoding ad hoc trait math in every
module.

Candidate scoring inputs:

- signed genome traits: `justice`, `empathy`, `honesty`, `ambition`, `courage`,
  `temperance`, `patience`, `assertiveness`, `persuasion`, `perception`,
  `discipline`, `focus`, `loyalty`, `mating drive`, `nurturance`,
  `neurochemical`, `resilience`, `civics`, `curiosity`, `creativity`;
- composite tags from `genome_composites.csv` such as `Con Artist`, `Tyrant`,
  `Fanatic`, `Berserker`, `Criminal Mastermind`, `Thief`, `Detective`,
  `Hanging Judge`, `Merciful Judge`, `Inventor`, `Scholar`, `Cult Leader`,
  `Jealous Rival`, `Dangerous Beauty`, `Good Neighbor`, and `Truth-Teller`;
- current role: ruler, title holder, heir, spouse, parent, household head,
  unemployed worker, migrant, soldier, priest/shaman, trader, artisan, child,
  elder;
- current pressures: famine/resource pressure, unemployment, debt, crowding,
  bereavement, succession crisis, war, relationship strain, settlement move,
  status fall, rival nearby;
- opportunity: co-residence, same settlement, same office court, shared job
  market, same household, travel/migration route, battlefield, market day.

Execution notes:

- Build probability from several small factors rather than one giant threshold.
- Include protective factors: strong empathy, temperance, justice, patience,
  loyalty, household support, civic order, guards, witnesses, stable prosperity.
- Use seeded RNG and existing deterministic test patterns.
- Keep annual loops bounded; do not scan every possible pair globally.

### Workstream 3: Visibility, Loss, Distortion, And Rediscovery

Goal: add an event-memory layer that distinguishes reality from preserved
history.

Potential record states:

- `admin_known`: true event known to the simulator/debug admin.
- `public_known`: known in-world during or near the event year.
- `private_known`: known only to involved people or close witnesses.
- `rumored`: partially known, uncertain, or socially distorted.
- `sealed`: preserved but hidden by authority, household, temple, guild, or court.
- `lost`: no active in-world memory remains.
- `rediscovered`: later recovered through archive, confession, witness,
  investigation, oral tradition, inscription, grave, ruined settlement, or admin
  promotion/reconstruction.
- `misattributed`: wrong actor, victim, place, motive, year, or cause is attached
  to the public memory.

Questions to answer before implementation:

- Should record state live on `simulation_events`, a side table, or both?
- Should multiple records exist for one true event: court record, rumor, later
  chronicle, household memory?
- How do we represent "truth" vs "public version" without bloating every event?
- Which existing event types should default to always admin-known but not always
  public-known?
- Can rediscovery produce a new event row like `event_rediscovered`, linked to
  the original event id?

Candidate schema direction:

- Keep `simulation_events` as the factual append-only backbone.
- Add a normalized event-memory / event-record table rather than stuffing all
  record state into payload JSON.
- Store compact structured fields such as:
  - `event_id`
  - `record_type`
  - `visibility_state`
  - `known_since_year`
  - `lost_year`
  - `rediscovered_year`
  - `confidence`
  - `source_person_id`
  - `source_institution_id` or future equivalent
  - `preserving_place_id`
  - `public_actor_person_id`
  - `public_victim_person_id`
  - `distortion_json`
  - `prose_variant_key`

Initial v8 foundation now exists:

- `simulation_event_records` stores one default memory/admin record per factual
  event.
- `simulation_event_records_readable` exposes event metadata beside memory state.
- New events create default records immediately; older v7 events backfill records
  when schema is ensured/rebuilt.
- Helpers can now mark records `lost`, `sealed`, `rumored`, or `rediscovered`.
- Rediscovery can create a linked factual `event_rediscovered` row.

Initial murder/feud-killing vertical slice now exists:

- `library.simulation_incidents` runs after household care and before government.
- It samples bounded per-settlement adult candidate pools and scores severe
  violence propensity from genome traits plus local resource pressure.
- It records structured `murder` events with killer, victim, witnesses, motive,
  incident kind, settlement/region, actor propensity, historical importance, and
  genome signal payload.
- Murder victims are marked dead immediately so same-year government succession
  can react.
- Murder records default to `violent_crime_record` / `rumored` in event memory.

Initial theft/fraud vertical slice now exists:

- `library.simulation_incidents` also samples bounded non-lethal property crimes.
- It scores property-crime propensity from `justice`, `honesty`, `empathy`,
  `persuasion`, `perception`, `ambition`, `frugality`, and `adaptability`, with
  competence traits amplifying suspect intent rather than creating criminality
  by themselves.
- It records structured `property_crime` events with perpetrator, target,
  witnesses, incident kind, motive, loss value, settlement/region, resource
  pressure, historical importance, and genome signals.
- Property-crime records default to `property_crime_record` / `rumored`.
- Property-crime consequences now immediately apply a small target-household
  prosperity loss, a smaller perpetrator-household gain, and a local settlement
  prosperity/stability penalty. The event payload records those before/after
  consequence deltas for inspection.

Initial affair/scandal vertical slice now exists:

- `library.simulation_incidents` now samples existing paramour pairs where at
  least one participant has a spouse/partner outside the paramour tie.
- It scores scandal exposure propensity from `mating drive`, `loyalty`,
  `modesty`, `honesty`, `neurochemical`, `assertiveness`, `persuasion`, and
  `discipline`. This is exposure risk for an existing secret relationship, not
  a deterministic moral label.
- It records structured `affair_scandal` events with accused person, paramour,
  betrayed partner(s), witnesses, incident kind, motive, settlement/region,
  resource pressure, historical importance, and genome signals.
- Affair-scandal records default to `scandal_record` / `rumored`.
- Affair-scandal consequences now end the exposed paramour relationship,
  dissolve betrayed official couples when the exposed partner is still linked,
  nudge local stability downward, and record the relationship fallout in the
  scandal payload. `save.sqlite` schema v12 also persists
  `heir_legitimacy_rumor` and `inheritance_scandal` variants into
  `simulation_legal_fallout` / `simulation_legal_fallout_readable` rows.
  Deeper legal adjudication, inheritance resolution, and faction reactions
  remain future work.

Initial public-virtue vertical slice now exists:

- `library.simulation_incidents` now samples bounded positive public events so
  the incident system is not only crime and scandal.
- It scores costly public virtue from `empathy`, `justice`, `nurturance`,
  `civics`, `honesty`, `courage`, `assertiveness`, `discipline`,
  `resilience`, and `frugality`.
- It records structured `public_virtue` events with benefactor, beneficiary,
  witnesses, incident kind, motive, settlement/region, resource pressure,
  historical importance, relief value, and genome signals.
- Public-virtue records default to `public_virtue_record` / `public_known`.
- Public-virtue consequences now transfer modest household prosperity from the
  benefactor side to the beneficiary side, raise local prosperity/stability,
  reduce food pressure slightly, lift low/blank benefactor leadership
  reputation to `medium`, and persist an active `relief_debt` obligation from
  beneficiary to benefactor. `save.sqlite` schema v11+ also persists a
  source-event-backed leadership reputation mark for the benefactor. Faction
  trust and health/legal fallout remain future work.

Initial knowledge/culture vertical slice now exists:

- `library.simulation_incidents` now samples bounded breakthroughs so history
  can preserve discoveries, inventions, legal precedents, and famous cultural
  acts alongside crimes and scandals.
- It scores knowledge/culture propensity from `curiosity`, `creativity`,
  `intellect`, `focus`, `perception`, `discipline`, `civics`, `wit`, and
  `adaptability`.
- It records structured `knowledge_culture` events with creator, patron,
  witnesses, incident kind, knowledge domain, motive, settlement/region,
  resource pressure, historical importance, novelty value, and genome signals.
- Knowledge/culture records default to `knowledge_record` / `public_known`.
- Knowledge/culture consequences now apply modest settlement/region prosperity
  and stability effects, transfer patronage support from patron to creator when
  a patron exists, lift low/blank creator status reputation to `middle-high`,
  and record a structured per-domain `knowledge_state` delta in the event
  payload. `save.sqlite` schema v9+ persists those deltas into regional
  `simulation_domain_states` / `simulation_domain_states_readable` rows during
  event flush/backfill. `save.sqlite` schema v10 now also persists active
  `patronage_debt` obligations from creator to patron when patronage exists.
  `save.sqlite` schema v11+ persists a source-event-backed status reputation
  mark for the creator. Deeper diffusion, schools, guilds, doctrine, and craft
  institutions remain future work.

Event-catalog expansion now exists:

- `config/event_catalog.csv` defines authored event/incident kind rows with
  family, display label, context tags, consequence profile, default memory
  expectations, and selection weight.
- `library.event_catalog` loads the catalog from config SQLite and falls back
  to legacy built-in rows for old or fixture databases without the table.
- `library.simulation_incidents` now uses catalog-backed variant pools while
  keeping the existing bounded trait/context gates. The catalog expands:
  - violent and property crime (`kin_killing`, `ambush_killing`,
    `storehouse_robbery`, `livestock_theft`, `debt_fraud`,
    `inheritance_fraud`, `market_extortion`);
  - scandal/succession risk (`heir_legitimacy_rumor`,
    `inheritance_scandal`);
  - rescue/virtue (`river_rescue`, `fire_rescue`, `famine_mercy`,
    `boundary_arbitration`, `succession_arbitration`,
    `oath_kept_under_pressure`);
  - knowledge/legal/invention rows (`improved_plow`, `water_lift`,
    `kiln_improvement`, `medicinal_discovery`, `inheritance_judgment`,
    `succession_precedent`, `calendar_reform`).

Existing events to retrofit:

- Births may be admin-known but only locally recorded, later lost, or preserved
  in lineage memory.
- Deaths may have a known true cause, public cause, suspected cause, or lost
  burial memory.
- Couple formation/dissolution may be private, public, scandalous, forgotten, or
  later inferred.
- Migrations may be remembered in household tradition or lost except as
  demographic fact.
- Office succession, warfare, title changes, and settlement moves should usually
  have strong record preservation but can still be distorted, suppressed, or
  rediscovered.

### Workstream 4: Consequences And Simulation Feedback

Goal: ensure events can matter beyond prose.

Possible consequence hooks:

- death, injury, fertility/household disruption, relationship dissolution;
- feud creation, revenge risk, protection obligations;
- job loss, promotion, exile, migration, imprisonment/future punishment;
- title vacancy, succession challenge, usurpation, office distrust;
- settlement unrest, religious movement, faction formation, patronage shift;
- passive-to-detailed promotion for witnesses, victims, suspects, heirs, or
  rediscoverers;
- later rediscovery creating scandal, legitimacy crisis, reform, prosecution, or
  cult/legend.

Implementation guardrail:

- Add consequences only for the vertical-slice event types first. Do not make
  every catalog entry fully consequential before the basic event-memory system is
  proven.

### Workstream 5: Poetic Prose And Narrative Presentation

Goal: produce event prose that is flavorful, variable, and still data-grounded.

Initial prose-rendering foundation now exists:

- `library.event_prose` renders deterministic text from existing
  `simulation_events_readable` and `simulation_event_records_readable` rows.
- It does not store generated prose in `save.sqlite`; prose is derived on demand
  from structured payloads, `event_id`, `record_id`, visibility state, and
  `prose_variant_key`.
- It exposes factual admin summaries for true events and public chronicle prose
  for `public_known`, `rumored`, and `rediscovered` records.
- Initial templates cover the first event vertical slices (`murder`,
  `property_crime`, `affair_scandal`, `public_virtue`, `knowledge_culture`) plus
  generic birth, death, rediscovery, lost, sealed, private, and admin-known
  records.
- The current prose is intentionally authored/template-based. It is stable for
  tests and ready for browser/query integration, but it still needs more tone
  variants, richer names/titles/kinship, and review against real multi-year
  samples.

Recommendation for first implementation:

- Start with a data-driven prose catalog written by Codex/LLM offline and stored
  as config or library templates.
- Use structured event payloads to fill names, places, kinship labels, offices,
  motives, witnesses, and uncertainty.
- Include tone/style variants:
  - terse annal;
  - court record;
  - household memory;
  - rumor;
  - priestly/temple chronicle;
  - bardic/legendary retelling;
  - later scholarly reconstruction;
  - admin/debug factual summary.
- Keep every generated prose string traceable to `event_id`, `record_id`, and
  prose template key.

Reasons to start with authored/template prose:

- deterministic tests are easier;
- no model download/runtime dependency;
- prose can be tuned to the world tone;
- structured payloads stay clean;
- the system can still have hundreds of variants per family.

Small local LLM exploration track:

- After the template system works, run a contained spike on a very small local
  HuggingFace text model for optional paraphrase generation.
- The local model should never be required for core simulation determinism.
- It may generate display-only prose from structured records, not decide facts.
- Cache generated prose by event/record/template seed so reruns are stable.
- Evaluate whether output quality is actually better than a large authored table
  before adopting it.

Prose quality targets:

- Names and places should feel embedded, not pasted in.
- Public uncertainty should read naturally: "it was said", "the court held",
  "the household never spoke plainly", "a later hand records".
- Lost/rediscovered records should have their own language: damaged rolls,
  temple ledgers, grave markers, confessions, old songs, boundary stones, ruined
  house sites, witnesses near death.
- Admin prose should stay precise and compact even when public prose is poetic.

### Workstream 6: Admin, Browser, And Query Surfaces

Goal: make the split between factual event, public record, lost memory, and
rediscovery inspectable.

Initial browser/query foundation now exists:

- `utils.gradio_data_browser` has a History tab that loads event/prose rows only
  when the user clicks `Load History`.
- The tab can inspect factual admin truth, public chronicle rows, rumor-only
  rows, lost-history rows, and rediscovery rows.
- Rediscovery rows include both restored original records and linked
  `event_rediscovered` events.
- Filters include exact event type, search text, limit, and offset.
- The browser uses `library.event_prose` instead of duplicating prose templates
  in UI code.

Needed views/tools:

- admin/debug view: all factual events regardless of visibility;
- in-world chronicle view: only public/preserved/rediscovered records;
- rumor view: uncertain or distorted records;
- lost-history view: admin-only list of lost events for debugging/tuning;
- person event timeline: true events plus known records involving a person;
- place chronicle: settlement/region/polity visible memory;
- rediscovery log: records that resurfaced and how.

Gradio/browser caution:

- Keep large event tables behind explicit load/filter controls.
- Do not rely on tab autoload for heavy event grids until the existing Gradio
  tab-select hazard is understood.

### Workstream 7: Persistence, Save Size, And Performance

Goal: add event richness without returning to huge JSON-heavy saves.

Storage priorities:

- Keep common fields normalized where they are queried often.
- Avoid storing long prose in every factual event row if multiple record variants
  can derive from compact records and template keys.
- Normalize high-volume event-record fields if this system creates many rows.
- Use payload JSON only for sparse details and extension fields.
- Preserve readable SQLite views for development inspection.

Performance priorities:

- Event generation should sample likely candidates rather than scanning all
  pairs.
- Passive/cohort populations should only create detailed rows through explicit
  promotion, narrative sampling, user focus, or historically important events.
- Loss/rediscovery ticks should be cheap and probably batched by year/place.
- Add counters/timing for event generation, event memory updates, prose rendering,
  and rediscovery scans once the first vertical slice exists.

### Workstream 8: Tests And Tuning

Minimum test coverage:

- deterministic event generation for a seeded small world;
- trait/context scoring helpers;
- event payload person/place links;
- lost/public/private/rumored/rediscovered state transitions;
- existing event types receiving default record visibility;
- prose template rendering with missing optional fields;
- save/load round-trip for event records;
- readable views exposing admin vs public history correctly;
- performance smoke test proving annual event generation is bounded.

Tuning artifacts:

- TSV/CSV report of event counts by type, year, settlement, and visibility state.
- Event importance distribution.
- Lost vs preserved vs rediscovered rates.
- Crime incidence under scarcity vs stability.
- Public chronicle sample output for prose review.

Initial tuning/report pass now exists:

- `library.event_history_report` builds deterministic event-history reports from
  `simulation_events_readable` and `simulation_event_records_readable`.
- `utils/util_event_history_report.py` writes report artifacts under
  `temp/event_history_report/<world>/`:
  - `summary.txt`;
  - `event_counts_by_type.tsv`;
  - `event_counts_by_year_type.tsv`;
  - `event_visibility_counts.tsv`;
  - `tracked_incident_counts.tsv`;
  - `event_metric_summaries.tsv`;
  - `public_chronicle_samples.tsv`.
- The first fixed-seed scratch sample used:
  `python utils/run_population_simulation.py --world-id event_tuning_sample --reset-world --years 80 --starting-couples 80 --seed 20260603 --skip-report-files --skip-timing-log --passive-population-scale 0 --detailed-active-soft-cap 0`
- Final sample report:
  - total events: 18,213;
  - total records: 18,213;
  - save size: about 18.1 MB;
  - tracked incident counts: `murder` 6, `property_crime` 3,
    `affair_scandal` 2, `public_virtue` 6, `knowledge_culture` 3;
  - memory lifecycle counts included 39 still-lost records, 2 original records
    in `rediscovered` state, and 2 linked `event_rediscovered` factual events.
- Rate/gate tuning now makes all five vertical slices show up in an 80-year
  review sample without flooding the save.
- Murder was retuned after a 200-year larger run reported only 85 murders among
  about 119,000 deaths. The current detailed-population target is about 4
  murders per 10,000 people per year, with population-scaled settlement trials
  and a population-scaled annual cap so 15,000-20,000 detailed people can
  produce historically plausible multi-murder years.
- Incident rates are now era-tunable through `config/incident_rates.csv`.
  Murder reads its target from `target_per_10k_per_year`; other tracked incident
  slices use chance/cap multipliers. The initial medieval rows preserve the
  murder target while raising property-crime visibility and modestly raising
  scandal visibility because those counts were likely under-sampled by the same
  pre-fix issue.
- Post-`incident_rates.csv` fixed-seed rerun completed with the same 80-year
  `event_tuning_sample` command:
  - total events: 17,871;
  - total records: 17,871;
  - save size: 17,719,296 bytes;
  - tracked incident counts: `murder` 7, `property_crime` 45,
    `affair_scandal` 4, `public_virtue` 2, `knowledge_culture` 1;
  - compared with the previous 80-year report, `murder` moved 6 -> 7,
    `property_crime` moved 3 -> 45, and `affair_scandal` moved 2 -> 4.
- Normal simulation now runs an automatic memory-aging/loss/rediscovery tick
  after the annual save checkpoint. It reviews a deterministic bounded shard of
  old event-memory records, marks some ordinary in-world records `lost`, and can
  turn old lost/sealed records into `rediscovered` records with a linked factual
  `event_rediscovered` event.
- Lifecycle-rate tuning now treats high-volume private records differently from
  rare incident records. A fresh 80-year fixed-seed `event_tuning_sample` rerun
  using the same command produced:
  - total events: 17,363;
  - total records: 17,363;
  - save size: 17,252,352 bytes;
  - tracked incident counts: `murder` 11, `property_crime` 43,
    `affair_scandal` 4, `public_virtue` 5, `knowledge_culture` 1;
  - high-volume work records stayed private: 0 lost and 0 rediscovered across
    `job_assigned`, `job_lost`, `unemployment_started`, and
    `unemployment_ended`;
  - lineage birth memory remained durable: 1 lost birth record out of 2,555.

### Suggested Milestones

1. Design the event catalog schema and write the first catalog rows/templates for
   the vertical slice.
2. Add event-memory persistence and default visibility records for existing event
   types.
3. Implement the first detailed event generator, preferably murder/feud killing,
   because it exercises genome scoring, death consequences, witnesses, record
   visibility, public uncertainty, and rediscovery.
4. Add theft/fraud and affair/scandal to test non-death crimes. Done for the
   initial vertical slices and first consequence hooks.
5. Add one positive public-virtue event and one knowledge/culture event so the
   system is not only crime-shaped. Done for the initial vertical slices and
   first consequence hooks.
6. Build prose rendering for factual admin summaries and public chronicle
   records. Done for the initial authored-template foundation.
7. Add browser/readable views for admin truth, public records, rumors, lost
   events, and rediscoveries. Done for the initial explicit-load History
   browser surface.
8. Run a multi-year sample and tune event rates, loss rates, prose quality, and
   save growth. Done for the first report/rate pass and the first automatic
   memory-aging/loss/rediscovery lifecycle.
9. Only after the authored prose system is working, evaluate a small local
   HuggingFace model as an optional display-only paraphraser.

Next event-system tasks:

- Run multi-year samples to tune the new durable consequence ledgers: faction
  memories, legal adjudications, inter-region domain diffusion, and
  knowledge-born institutions.
- Add dedicated browser/report summaries for those ledgers once sample shapes
  settle beyond the current readable save views.

## Polygonal World Map Generation

The first polygonal terrain pass is done. `library.world_map_geometry` now builds deterministic continent-clipped micro-cells, assigns them to authored regions, computes elevation/moisture/terrain families, routes rivers through micro-cell adjacency, carves river channels/floodplains, aggregates named region footprints from micro-cells, and renders noisy terrain/rivers in the debug/world SVG path. See `TODONE.md`.

Remaining map work:

1. Tune terrain classification, elevation gradients, river density, river mouth shape, and moisture spread against generated SVGs from multiple map seeds.
2. Tune the explicit lake and ocean cell styling against generated SVGs from multiple map seeds.
3. Decide whether to keep the current dependency-light micro-cell graph or introduce a reusable Delaunay/corner graph if a stable dependency is worth it.
4. Persist or export richer map debug data when tuning needs more than the current SVG/data attributes expose.
5. Add map-seed comparison fixtures or golden-light tests once the visual style settles enough to make regressions meaningful.

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
- Save schema v2/v3/v4/v5/v6/v7/v8/v9/v10/v11 already removed save-side `world` columns, flattened common `Person` fields into typed columns, compacted genome/mind-body payloads, added normalized event-person links, normalized common place IDs, added the first hybrid passive/cohort tables, normalized `settlement_moved` route detail, added default event-memory records, added regional domain-state rows derived from knowledge/culture events, added active obligation rows derived from public-virtue relief and knowledge patronage, and added reputation marks derived from public-virtue/knowledge consequence payloads. See `TODONE.md`.
- Remaining bigger wins are likely:
  - continue moving high-volume event detail out of JSON when another specific event family proves hot;
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
- save tables/views introduced in schema v6:
  - `simulation_people_light`
  - `simulation_people_light_readable`
  - `simulation_cohorts`
  - `simulation_cohorts_readable`
  - `simulation_promotion_log`

Hybrid background cohort generation now exists in the canonical population runner:

- each active settlement receives `simulation_cohorts` rows;
- the initial passive snapshot is based on configured regional carrying capacity minus detailed residents;
- later passive snapshots age forward, apply passive births/deaths, and mark partnered adult cohorts;
- detailed births over the configured detailed-active soft cap are absorbed into passive newborn cohorts;
- cohorts count in Gradio place stats through `simulation_cohorts_readable`;
- cohorts do not enter detailed annual event loops.

Office selection can now promote one passive adult into detailed simulation when no detailed candidate is available. Promotion synthesizes a full `Person` from passive facts and records inferred/backfilled events.

Migration arrivals can now promote a small capped number of passive adult residents in the destination settlement into detailed simulation for local context (`migration_into_focal_settlement`), before pairing runs for that year.

Yearly summaries now split detailed alive, passive-person alive, aggregate-cohort alive, aggregate partnered cohorts, mixed-mode alive, passive births, and passive deaths.

`utils/run_mixed_mode_scale_smoke.py` now exercises aggregate passive/cohort evolution at 100K, 1M, and 10M targets without materializing millions of detailed people. It writes `temp/mixed_mode_scale_smoke.tsv`.

`utils/run_mixed_mode_calibration.py` now runs the full population runner at 100K, 1M, and 10M mixed-mode targets with bounded detailed samples and aggregate settlement seeding. It writes `temp/mixed_mode_calibration.tsv`.

Longer 10-year full mixed-mode calibration now runs comfortably on local hardware and preserves the configured target closely after passive fertility tuning. Still missing: additional promotion triggers such as user inspection and narrative spotlighting.

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
3. Add another passive-to-detailed promotion trigger such as user inspection or narrative spotlighting.
