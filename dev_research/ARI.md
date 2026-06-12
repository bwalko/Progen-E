Progen-E: Narrative Heat and ARI
================================

Purpose
-------

Progen-E should eventually identify not only powerful or high-status people, but also people whose lives are historically interesting, contradictory, consequential, strange, or memorable.

This document proposes two related metrics:

1. **Narrative Heat**

2. **ARI: Archive Recognition Index**

These should be related but not identical.

* * *

Narrative Heat
--------------

**Narrative Heat** measures how story-shaped a person's life is.

It does not mean the person was good, famous, powerful, or historically recognized. It means their life contains events, contradictions, consequences, relationships, or unusual patterns that could produce an interesting biography.

A person with high Narrative Heat might be:

* a founder of something (polity, other institution, not literal founder people who are sprung out of thin air)

* a murderer

* an artist

* a political schemer

* a scandalous matriarch

* a forgotten soldier

* a person whose descendants reshaped a region

* someone whose life included rare, contradictory, or high-consequence events

Narrative Heat asks:

> How likely is this life to create a memorable historical paragraph?

* * *

ARI: Archive Recognition Index
------------------------------

**ARI** stands for **Archive Recognition Index**.

ARI measures how likely the archive, history, institutions, family memory, or cultural record are to notice and preserve this person's life.

ARI is not the same as Narrative Heat.

A person can have:

* **High Narrative Heat, Low ARI**  
  A fascinating life that history mostly forgets.

* **Low Narrative Heat, High ARI**  
  A dull but well-documented noble, official, patron, or institutional figure.

* **High Narrative Heat, High ARI**  
  A person whose life was both dramatic and remembered.

* **Low Narrative Heat, Low ARI**  
  An ordinary life that leaves little trace in the historical record.

Narrative Heat measures story potential.  
ARI measures historical recognition.

* * *

Design Principle
----------------

This system should avoid making history only remember kings, warlords, founders, and elites.

Powerful people should often have high ARI because institutions preserve their names.

But Progen-E should also have a way to notice strange, overlooked, contradictory, or locally important people.

The goal is not to make every person special. The goal is to let the simulation occasionally point to a life and say:

> Do not lose this one.

* * *

Narrative Heat Components
-------------------------

A first-pass Narrative Heat score could be built from several sub-scores.
    narrative_heat =
        event_heat
      + contradiction_heat
      + consequence_heat
      + social_reach_heat
      + rarity_heat
      + volatility_heat
      + legacy_heat

The final score can be normalized to `0.0 to 1.0` or clamped to `0 to 100`.

Store both the total score and the component scores. The component scores will help future biography generation explain why someone matters.

Example fields:
    narrative_heat_total
    narrative_heat_events
    narrative_heat_contradictions
    narrative_heat_consequences
    narrative_heat_social
    narrative_heat_rarity
    narrative_heat_volatility
    narrative_heat_legacy

* * *

1. Event Heat

-------------

Event Heat measures memorable life events.

Possible event weights:
    murder: +25
    founded settlement: +30
    created knowledge/art: +15
    major office/job: +10
    war/raid participation: +12
    leader/diplomat/court schemer: +8
    crime: +8
    legal fallout: +8
    reputation mark: +10
    large status swing: +10
    death in unusual event: +15
    many children: +5 to +15
    extreme lifespan: +10
    late-life major event: +12

These values should be tuned over time.

* * *

2. Contradiction Heat

---------------------

Contradiction Heat measures interesting tension between traits, roles, and life outcomes.

People become memorable when they do not fit clean archetypes.

Examples:
    amorous + modest
    nurturing + negligent
    passive + explosive temper
    humble + crowd inciter
    witty + extremely oblivious
    lawless + diplomat
    miserly + patronage debt
    artist + murderer
    raider + respected official

Example implementation:
    double ContradictionHeat(Person p)
    {
        double heat = 0;

        if (p.HasTag("nurturing") && p.HasTag("negligent"))
            heat += 8;

        if (p.HasTag("very passive") && p.HasTag("explosive temper"))
            heat += 8;

        if (p.HasTag("humble") && p.HasTag("crowd inciter"))
            heat += 6;

        if (p.HasTag("modest") && p.HasTag("very amorous"))
            heat += 6;

        if (p.HasTag("very witty") && p.HasTag("extremely oblivious"))
            heat += 6;

        if (p.HasTag("lawless") && p.JobHistoryContains("diplomat"))
            heat += 8;

        return heat;
    }

This list should grow gradually as the simulation produces more interesting cases.

* * *

3. Consequence Heat

-------------------

Consequence Heat measures how much this person's life affected others.

Possible weights:
    childcare shortfall death/runaway: +6 each, capped
    obligations: +4 each
    reputation marks: +8 each
    legal fallout: +8 each
    knowledge effects: +10 each
    partner leaving for paramour: +4 each
    household breakup: +3 each
    victims caused by person: +12 each
    major feud or grievance: +10

This category should be capped so one type of repeated event does not dominate the entire score.

Example:
    double consequenceHeat = Math.Min(rawConsequenceHeat, 40);

* * *

4. Social Reach Heat

--------------------

Social Reach Heat measures how many people, households, families, settlements, or institutions this person touched.

Possible weights:
    unique partners: +2 each
    paramours: +3 each
    children: +1 each, capped at 12
    patrons: +5 each
    connected to high-status people: +5 to +15
    settlement ties: +3 each
    cross-cultural family ties: +5
    large descendant network: +5 to +20

This helps identify people who are historically sticky even if they were not rulers or famous officials.

* * *

5. Rarity Heat

--------------

Rarity Heat measures unusual traits, paths, events, or timing.

Possible weights:
    rare job: +8
    rare job transition: +5
    rare trait extreme: +3 each
    very old age: +10
    major event after old age threshold: +12
    cross-cultural partnership/child: +5
    unexpected career after old age: +8
    unusual cause of death: +10
    unusually long paramour relationship: +6

Late-life major events should matter. A murder at age 98, for example, should strongly increase Rarity Heat.

* * *

6. Volatility Heat

------------------

Volatility Heat measures how much the person's life changed shape.

Possible factors:
    number of job changes
    number of partner changes
    status swings
    reputation direction changes
    settlement moves
    switches between respectable and criminal roles
    large changes in wealth or social status

Example:
    double VolatilityHeat(Person p)
    {
        double heat = 0;

        heat += Math.Min(p.JobChangeCount * 2, 12);
        heat += Math.Min(p.PartnerChangeCount * 2, 16);
        heat += Math.Min(p.StatusSwingCount * 5, 15);

        if (p.HasCriminalRole() && p.HasCourtOrDiplomaticRole())
            heat += 10;

        return heat;
    }

A path such as `charlatan -> raider -> diplomat -> court schemer` should produce strong Volatility Heat.

* * *

7. Legacy Heat

--------------

Legacy Heat measures whether something from the person's life persisted.

Possible weights:
    living descendants: +1 each, capped
    founded lineage: +10
    knowledge/art effect: +10 to +25 depending novelty
    settlement named after family: +10
    remembered event with cultural effect: +15
    obligation or reputation persists after death: +8
    descendant becomes important: +5 to +20

Legacy Heat may become more useful once Progen-E has stronger history, memory, and chronicle systems.

* * *

First-Pass Narrative Heat Function
----------------------------------

    double NarrativeHeat(Person p)
    {
        double heat = 0;
    
        heat += EventHeat(p);
        heat += ContradictionHeat(p);
        heat += Math.Min(ConsequenceHeat(p), 40);
        heat += Math.Min(SocialReachHeat(p), 35);
        heat += RarityHeat(p);
        heat += VolatilityHeat(p);
        heat += LegacyHeat(p);
    
        // Small random factor keeps borderline cases from being too deterministic.
        heat += Random.Shared.NextDouble() * 5.0;
    
        return Math.Clamp(heat, 0, 100);
    }

This should be treated as a tuning scaffold, not a final formula.

* * *

ARI Components
--------------

ARI should measure recognition, not inherent interestingness.

Possible ARI factors:
    official status
    wealth
    noble or elite family connection
    public office
    religious office
    military rank
    legal records
    patronage records
    authored works
    created knowledge/art
    founded settlement
    descendants who preserved memory
    connection to famous people
    local chronicler interest
    institutional recordkeeping quality

ARI should also include bias.

Some people are more likely to be remembered because they had status, money, scribes, descendants, allies, or institutions preserving their names.

Others may have high Narrative Heat but low ARI because they were poor, isolated, stigmatized, politically inconvenient, or only locally known.

* * *

First-Pass ARI Formula
----------------------

    double ArchiveRecognitionIndex(Person p)
    {
        double ari = 0;
    
        ari += OfficialStatusScore(p);
        ari += WealthScore(p);
        ari += FamilyPrestigeScore(p);
        ari += PublicRoleScore(p);
        ari += LegalRecordScore(p);
        ari += KnowledgeOrArtRecordScore(p);
        ari += FounderOrInstitutionScore(p);
        ari += DescendantMemoryScore(p);
        ari += ChroniclerInterestScore(p);
    
        // Narrative heat can influence recognition, but should not define it.
        ari += NarrativeHeat(p) * 0.25;
    
        // Some people are deliberately forgotten or poorly recorded.
        ari -= SuppressionOrObscurityPenalty(p);
    
        return Math.Clamp(ari, 0, 100);
    }

* * *

Hidden Heat vs Official Recognition
-----------------------------------

One useful derived concept:
    hidden_heat = narrative_heat - ARI

High hidden heat means the person lived an interesting life but may not be officially remembered.

These are good candidates for special life-summary attention.

Examples:
    High Narrative Heat, Low ARI:
    "Interesting but forgotten."

    High Narrative Heat, High ARI:
    "Interesting and remembered."

    Low Narrative Heat, High ARI:
    "Documented but not very interesting."

    Low Narrative Heat, Low ARI:
    "Ordinary or poorly preserved."

* * *

Violet Marginalia / Ari-Flavored Layer
--------------------------------------

Optional internal concept:

**Violet Marginalia** is a rare archival attention marker.

It should not mean the person is famous. It should not mean they are powerful. It should not make the whole simulation revolve around them.

It means the system detected a life with unusual human texture, especially if that life might otherwise be overlooked.

This is where the ARI idea can quietly preserve the spirit of Ari: not by making the world purple, but by making the archive better at noticing.

Possible selection logic:
    violet_marginalia_chance =
        narrative_heat * 0.004
      + contradiction_heat * 0.006
      + hidden_heat * 0.005
      + strange_life_bonus
      + forgotten_person_bonus
      - already_famous_penalty

The goal is to notice:
    overlooked lives
    contradictory people
    people with one strange shining event
    people whose lives are not powerful, but are deeply human
    people history might flatten or forget

Example output:
    Violet Marginalia:
    Remembered less for rank than for contradiction: a witty, oblivious, lawless matriarch whose long life bent through scandal, raiding, court intrigue, and one final murder.

* * *

Biography Summary Usage
-----------------------

The future life-summary system should use these scores to decide both whether to summarize a person and what kind of summary to produce.

Examples:
    High event heat:
    Focus on major deeds.

    High contradiction heat:
    Focus on personality tension and paradox.

    High consequence heat:
    Focus on damage, influence, obligations, victims, or social fallout.

    High social reach:
    Focus on family network, partnerships, descendants, and settlement ties.

    High rarity heat:
    Focus on unusual events, rare careers, extreme age, or unlikely outcomes.

    High volatility heat:
    Focus on transformation across life stages.

    High legacy heat:
    Focus on what persisted after death.

    High ARI:
    Frame as historically recognized.

    Low ARI but high Narrative Heat:
    Frame as obscure, local, fragmentary, or rediscovered.

* * *

Implemented Shape
-----------------

The current implementation lives in `library/person_archive_scores.py` as a derived read model. It does not change the core idea above:

* **Narrative Heat** is story potential.
* **ARI** is archive recognition.
* **Hidden Heat** is story potential that exceeds archive recognition.
* **Violet Marginalia** is a rare attention marker for unusually human traces.

ARI is recognition, not moral worth, inherent importance, goodness, fame, or power. A high ARI means the archive is more likely to identify and preserve a person. A low ARI does not mean the person mattered less.

Cached tables:

* `simulation_person_archive_scores` keeps the numeric columns used for indexed top-N retrieval: totals, component scores, bucket labels, `component_json`, and `score_version`.
* `simulation_person_archive_score_reasons` stores bounded explanation rows refreshed with the score rows. Fields are `person_id`, `component_key`, `axis`, `contribution`, `source_kind`, `source_id`, `source_year`, `role`, `label`, `explanation`, `sort_rank`, and `score_version`.
* Reason rows are derived cache rows. Existing saves can open without manual migration; the table is created by `ensure_person_archive_score_schema(...)` and populated on the next score refresh.
* Positive `contribution` values explain story or recognition drivers. Negative `contribution` values explain obscurity, suppression, stigma, sparse public records, or preservation bias.

Component keys:

* Narrative Heat: `narrative_heat_events`, `narrative_heat_contradictions`, `narrative_heat_consequences`, `narrative_heat_social`, `narrative_heat_rarity`, `narrative_heat_volatility`, `narrative_heat_legacy`.
* ARI: `ari_official_status`, `ari_wealth`, `ari_family_prestige`, `ari_public_role`, `ari_legal_records`, `ari_knowledge_art`, `ari_founder_institution`, `ari_descendant_memory`, `ari_chronicler_interest`, `ari_suppression_obscurity_penalty`.
* Derived: `hidden_heat`, `violet_marginalia_score`.

`component_json` version 2 is intended to be stable for Python tools and LLM context. Top-level keys include `schema`, `score_version`, `formula_version`, `summary`, `totals`, `components`, `bucket_labels`, `top_event_types`, `top_roles`, `evidence_counts`, `data_caveats`, `top_reason_summaries`, `top_reasons`, `source_ids`, and `flags`. The old convenience keys such as `event_count`, `child_count`, `office_holding_count`, and `public_record_count` remain for compatibility.

Read APIs:

* `load_person_archive_score(conn, person_id)` returns the cached numeric row and remains compatible with v1 callers.
* `load_person_archive_explanation(conn, person_id, max_reasons=12)` returns a dict with scores, buckets, component metadata, caveats, top reasons, and source ids.
* `top_person_archive_scores(...)` still reads cached numeric columns and does not recompute formulas.

Browser/detail views must read cached rows only. Score refresh happens from checkpoint or maintenance paths, not while rendering person sheets.

* * *

Guiding Principle
-----------------

Progen-E should remember that history is not only made by rulers.

Some people matter because they founded cities.  
Some people matter because they destroyed households.  
Some people matter because they created art.  
Some people matter because they became legends.  
Some people matter because they were misremembered.  
Some people matter because they should have vanished from the archive, but did not.

Narrative Heat finds the bonfires.

ARI asks whether history saw the smoke.

Violet Marginalia points to the ember under the floorboard.
