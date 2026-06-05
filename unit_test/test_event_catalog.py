"""Tests for authored event-catalog rows."""

from __future__ import annotations

import random
import sqlite3
import tempfile
import unittest
from pathlib import Path

from library.config_import import load_all_csvs_into_sqlite
from library.event_catalog import (
    choose_event_catalog_kind,
    clear_event_catalog_cache,
    event_catalog_entries,
)
from library.event_ontology import clear_event_ontology_cache, event_ontology_entries


class TestEventCatalog(unittest.TestCase):
    def tearDown(self) -> None:
        clear_event_catalog_cache()
        clear_event_ontology_cache()

    def test_authored_catalog_loads_expanded_incident_kinds(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            cfg = Path(td) / "config.sqlite"
            load_all_csvs_into_sqlite(cfg)
            clear_event_catalog_cache()

            property_kinds = {
                row.incident_kind
                for row in event_catalog_entries(
                    db_path=cfg,
                    event_type="property_crime",
                    any_tags=("theft", "fraud", "succession"),
                )
            }
            virtue_kinds = {
                row.incident_kind
                for row in event_catalog_entries(
                    db_path=cfg,
                    event_type="public_virtue",
                    any_tags=("rescue", "succession"),
                )
            }
            knowledge_kinds = {
                row.incident_kind
                for row in event_catalog_entries(
                    db_path=cfg,
                    event_type="knowledge_culture",
                    any_tags=("invention", "legal", "succession"),
                )
            }

        self.assertIn("storehouse_robbery", property_kinds)
        self.assertIn("inheritance_fraud", property_kinds)
        self.assertIn("river_rescue", virtue_kinds)
        self.assertIn("succession_arbitration", virtue_kinds)
        self.assertIn("improved_plow", knowledge_kinds)
        self.assertIn("succession_precedent", knowledge_kinds)

    def test_catalog_covers_every_workstream_ontology_key(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            cfg = Path(td) / "config.sqlite"
            load_all_csvs_into_sqlite(cfg)
            clear_event_catalog_cache()
            clear_event_ontology_cache()

            ontology_entries = event_ontology_entries(db_path=cfg)
            catalog_entries = event_catalog_entries(db_path=cfg)

        ontology_keys = {entry.event_key for entry in ontology_entries}
        catalog_kinds = {entry.incident_kind for entry in catalog_entries}
        missing_keys = sorted(ontology_keys - catalog_kinds)
        self.assertEqual([], missing_keys)

        ontology_families = {entry.event_family for entry in ontology_entries}
        catalog_families = {entry.event_family for entry in catalog_entries}
        missing_families = sorted(ontology_families - catalog_families)
        self.assertEqual([], missing_families)

        catalog_event_types = {entry.event_type for entry in catalog_entries}
        self.assertIn("political_crime", catalog_event_types)
        self.assertIn("religious_cultural_conflict", catalog_event_types)
        self.assertIn("private_life", catalog_event_types)

        alias_weights = {
            entry.incident_kind: entry.selection_weight
            for entry in catalog_entries
            if entry.incident_kind in {"rescue", "mercy", "arbitration"}
        }
        self.assertEqual(
            {"rescue": 0.0, "mercy": 0.0, "arbitration": 0.0},
            alias_weights,
        )

    def test_dormant_workstream_rows_do_not_expand_active_variant_pools(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            cfg = Path(td) / "config.sqlite"
            load_all_csvs_into_sqlite(cfg)
            clear_event_catalog_cache()

            domestic_murder_kinds = {
                entry.incident_kind
                for entry in event_catalog_entries(
                    db_path=cfg,
                    event_type="murder",
                    any_tags=("domestic", "household"),
                )
            }
            property_debt_kinds = {
                entry.incident_kind
                for entry in event_catalog_entries(
                    db_path=cfg,
                    event_type="property_crime",
                    any_tags=("debt", "hoarding", "scarcity"),
                )
            }
            public_rescue_draws = {
                choose_event_catalog_kind(
                    db_path=cfg,
                    event_type="public_virtue",
                    any_tags=("rescue",),
                    default="heroic_rescue",
                    rng=random.Random(seed),
                )
                for seed in range(40)
            }

        self.assertNotIn("domestic_killing", domestic_murder_kinds)
        self.assertNotIn("debt_evasion", property_debt_kinds)
        self.assertNotIn("scarcity_hoarding", property_debt_kinds)
        self.assertNotIn("rescue", public_rescue_draws)

    def test_missing_catalog_table_uses_legacy_fallback_kinds(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            cfg = Path(td) / "config.sqlite"
            sqlite3.connect(cfg).close()
            clear_event_catalog_cache()

            kind = choose_event_catalog_kind(
                db_path=cfg,
                event_type="public_virtue",
                any_tags=("rescue",),
                default="public_mercy",
                rng=random.Random(123),
            )
            kinds = {
                row.incident_kind
                for row in event_catalog_entries(
                    db_path=cfg,
                    event_type="knowledge_culture",
                    any_tags=("legal",),
                )
            }

        self.assertEqual(kind, "heroic_rescue")
        self.assertEqual(kinds, {"legal_precedent"})


if __name__ == "__main__":
    unittest.main()
