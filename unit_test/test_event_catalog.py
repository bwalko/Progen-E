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


class TestEventCatalog(unittest.TestCase):
    def tearDown(self) -> None:
        clear_event_catalog_cache()

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
