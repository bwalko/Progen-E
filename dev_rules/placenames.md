# Placename Length And Composition

Use this when tuning settlement display names, local feature names, or the
visible locative suffix branch in `library/placenames_generation.py`.

## Evidence Baseline

Primary historical comparison:

- Keith Briggs, "Domesday Book place-name forms":
  https://keithbriggs.info/DB_place-name_forms.html
- The page describes PDFs assembled from original Domesday place-name spellings,
  notes that each PDF has about 32.4k name forms, and says the files are intended
  for broad statistical studies.

Local extraction used the alphabetic PDF from that page:

- Source file: `tmp/pdfs/DB_place-name_forms_alphabetic.pdf`
- Extraction method: first token from rows ending in a recognized county code
  plus folio marker, with letters counted after ligature normalization.
- Parsed rows: 31,229 entries, 13,952 unique normalized forms. This is slightly
  below the source page's about-32.4k count because the quick parser ignores
  wrapped or irregular rows; it is still a large enough baseline for length
  tuning.

Domesday original-spelling length distribution, entries:

| Metric | Letters |
| --- | ---: |
| Mean | 8.13 |
| Median | 8 |
| 75th percentile | 9 |
| 90th percentile | 11 |
| 95th percentile | 12 |
| 99th percentile | 13 |
| Maximum parsed form | 16 |

Additional entry facts:

- 88.1% of parsed Domesday forms are 10 letters or shorter.
- 2.0% are longer than 12 letters.
- No parsed form is longer than 16 letters.
- Domesday spellings ending in `bi` or `by` are about 5.4% of entries and 3.7%
  of unique forms. Exact `by` is rare in the manuscript spellings because `bi`
  is a common Domesday spelling of the same visible family.

Progen-E evidence from the current checkout:

- Current `worlds/default/save.sqlite` has only 3 active settlement rows with
  display names, so it is symptom evidence rather than a broad sample.
- Those 3 active display names are 15, 18, and 20 letters.
- All 3 have locative etymology segments; 2 of 3 visibly end in a `-by` family
  suffix (`-havenby` or `-fordby`).
- A fresh 240-name `generate_settlement_name(...)` sample using current config
  had median length 11, 95th percentile 15, max 18, and 30.0% longer than 12.
  It produced no visible `-by` names because that helper does not apply the
  locative display branch.

Interpretation:

- The earlier `-by` flood has been reduced in the core generator, but old saves
  can still show stale long locative names.
- Even without locative suffixes, current generated settlement names are too
  long against an 11th-century English/Domesday baseline.
- The main current pressure comes from long patronymic stems plus settlement
  affixes; locative display can then add `by`, `fordby`, `havenby`, or `wellby`
  on top of an already-long base.

## Tuning Targets

Treat these as display-name targets, not etymology targets. Etymology may remain
longer and should continue to preserve the naming trail.

- Default settlement display names should be 1-2 visible stems.
- 3 visible stems should be uncommon and should use short components.
- 4 visible stems should be disabled for routine settlement display names.
- Target median: 7-9 letters.
- Target 75th percentile: 10 letters or less.
- Target 90th percentile: 12 letters or less.
- Target 95th percentile: 13 letters or less.
- Routine hard cap: 16 letters. A generated settlement label above this should
  be rerolled, shortened, or fall back to a simpler mode.
- Global visible `-by` family names should be flavor, not the default. Use the
  Domesday `bi`/`by` baseline as a rough cap: about 5% globally is plausible;
  higher rates should require a deliberately Scandinavian/Danelaw-like region
  or culture mix.

## Implementation Guidance

- Add an explicit display-length budget near settlement-name composition.
  Measure letters after `normalize_placename_stem` / display formatting, not raw
  etymology text.
- Prefer rerolling or selecting a shorter mode over blind truncation. Truncation
  can create ugly or culturally meaningless fragments.
- For patronymic names, avoid using very long first names as settlement stems.
  If the chosen personal name is already longer than about 8-9 letters, pick a
  shorter eligible name, use a short form, or fall back to a non-patronymic mode.
- For locative display, do not append `by`, `fordby`, `havenby`, or `wellby` if
  the result would exceed the target budget. Keep the locative anchor in
  etymology even when the visible name stays short.
- Keep visible suffix and etymology separate. The user should be able to see
  "named by a ford" in the detail sheet without the map becoming a wall of
  `-fordby` labels.
- Add deterministic distribution tests. A good regression should generate a
  fixed sample from current config and assert median, 90th/95th percentile, max,
  and visible `-by` family rate stay under the chosen thresholds.

