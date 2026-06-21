"""Government / polity checkpoint tables in ``save.sqlite`` (schema + save/load)."""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from library.simulation_context import SimulationContext

META_NEXT_POLITY_ID = "next_gov_polity_id"
META_NEXT_SEAT_ID = "next_gov_seat_id"
META_NEXT_DYNASTY_ID = "next_gov_dynasty_id"
META_NEXT_CAMPAIGN_ID = "next_gov_campaign_id"
META_NEXT_ALLIANCE_ID = "next_gov_alliance_id"


def ensure_government_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS simulation_polities (
            polity_id INTEGER PRIMARY KEY,
            polity_type_id TEXT NOT NULL,
            parent_polity_id INTEGER,
            name TEXT NOT NULL DEFAULT '',
            capital_settlement_id TEXT,
            founding_dynasty_id INTEGER,
            founded_sim_year INTEGER NOT NULL DEFAULT 0,
            dissolved_sim_year INTEGER,
            status TEXT NOT NULL DEFAULT 'active',
            notes_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_simulation_polities_parent
        ON simulation_polities (parent_polity_id);

        CREATE TABLE IF NOT EXISTS simulation_polity_territory (
            row_id INTEGER PRIMARY KEY AUTOINCREMENT,
            polity_id INTEGER NOT NULL,
            target_kind TEXT NOT NULL,
            target_id TEXT NOT NULL,
            since_sim_year INTEGER NOT NULL,
            until_sim_year INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_gov_territory_polity
        ON simulation_polity_territory (polity_id);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_gov_territory_open_unique
        ON simulation_polity_territory(polity_id, target_kind, target_id)
        WHERE until_sim_year IS NULL;

        CREATE TABLE IF NOT EXISTS simulation_office_seats (
            seat_id INTEGER PRIMARY KEY,
            polity_id INTEGER NOT NULL,
            title_id TEXT NOT NULL,
            slot_index INTEGER NOT NULL DEFAULT 0,
            scope_settlement_id TEXT,
            vacant_since_sim_year INTEGER,
            status TEXT NOT NULL DEFAULT 'active',
            holder_person_id INTEGER,
            term_expires_sim_year INTEGER
        );
        DROP INDEX IF EXISTS idx_gov_seat_unique_slot;
        CREATE UNIQUE INDEX IF NOT EXISTS idx_gov_seat_unique_slot
        ON simulation_office_seats(
            polity_id, title_id, COALESCE(scope_settlement_id, ''), slot_index
        );

        CREATE TABLE IF NOT EXISTS simulation_office_holdings (
            holding_id INTEGER PRIMARY KEY AUTOINCREMENT,
            seat_id INTEGER NOT NULL,
            holder_person_id INTEGER NOT NULL,
            start_sim_year INTEGER NOT NULL,
            end_sim_year INTEGER,
            end_reason TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_gov_holdings_seat
        ON simulation_office_holdings(seat_id);
        CREATE INDEX IF NOT EXISTS idx_gov_holdings_holder
        ON simulation_office_holdings(holder_person_id);

        CREATE TABLE IF NOT EXISTS simulation_dynasties (
            dynasty_id INTEGER PRIMARY KEY,
            founder_person_id INTEGER NOT NULL,
            house_name TEXT NOT NULL DEFAULT '',
            founded_sim_year INTEGER NOT NULL,
            extinct_sim_year INTEGER,
            line TEXT NOT NULL DEFAULT 'agnatic'
        );

        CREATE TABLE IF NOT EXISTS simulation_alliances (
            alliance_id INTEGER PRIMARY KEY,
            polity_a_id INTEGER NOT NULL,
            polity_b_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            since_sim_year INTEGER NOT NULL,
            until_sim_year INTEGER,
            payload_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_gov_alliances_polities
        ON simulation_alliances (polity_a_id, polity_b_id);

        CREATE TABLE IF NOT EXISTS simulation_campaigns (
            campaign_id INTEGER PRIMARY KEY,
            attacker_polity_id INTEGER NOT NULL,
            defender_polity_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            objective_json TEXT NOT NULL DEFAULT '{}',
            start_sim_year INTEGER NOT NULL,
            end_sim_year INTEGER,
            outcome TEXT NOT NULL DEFAULT 'ongoing',
            attacker_force_strength REAL NOT NULL DEFAULT 0,
            defender_force_strength REAL NOT NULL DEFAULT 0,
            attacker_treasury_spent REAL NOT NULL DEFAULT 0,
            defender_treasury_spent REAL NOT NULL DEFAULT 0,
            siege_target_settlement_id TEXT,
            siege_years INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS simulation_battles (
            battle_id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER NOT NULL,
            sim_year INTEGER NOT NULL,
            location_settlement_id TEXT,
            attacker_force_strength REAL NOT NULL,
            defender_force_strength REAL NOT NULL,
            attacker_casualties INTEGER NOT NULL DEFAULT 0,
            defender_casualties INTEGER NOT NULL DEFAULT 0,
            outcome TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_gov_battles_campaign
        ON simulation_battles(campaign_id);
        """
    )
    conn.executescript(
        """
        DROP VIEW IF EXISTS simulation_office_history_readable;
        CREATE VIEW simulation_office_history_readable AS
        SELECT
            h.holding_id,
            s.polity_id,
            COALESCE(pol.name, '') AS polity_name,
            s.seat_id,
            s.title_id AS office_id,
            s.title_id,
            s.slot_index,
            s.scope_settlement_id,
            h.holder_person_id,
            TRIM(
                COALESCE(p.first_name, '') || ' ' || COALESCE(p.last_name, '')
            ) AS holder_name,
            h.start_sim_year,
            h.end_sim_year,
            h.end_reason,
            CASE
                WHEN h.end_sim_year IS NULL THEN 'current'
                ELSE 'ended'
            END AS holding_status
        FROM simulation_office_holdings h
        JOIN simulation_office_seats s
          ON s.seat_id = h.seat_id
        LEFT JOIN simulation_polities pol
          ON pol.polity_id = s.polity_id
        LEFT JOIN simulation_people p
          ON p.person_id = h.holder_person_id;
        """
    )


def _meta_get(conn: sqlite3.Connection, world: str, key: str, default: int) -> int:
    row = conn.execute(
        "SELECT meta_value FROM simulation_meta WHERE meta_key = ?",
        (key,),
    ).fetchone()
    if row is None or row[0] is None:
        return default
    try:
        return int(str(row[0]).strip())
    except (TypeError, ValueError):
        return default


def _meta_set(cur: sqlite3.Cursor, world: str, key: str, value: int) -> None:
    cur.execute(
        """
        INSERT OR REPLACE INTO simulation_meta (meta_key, meta_value)
        VALUES (?, ?)
        """,
        (key, str(int(value))),
    )


def insert_ongoing_campaign(
    conn: sqlite3.Connection,
    *,
    world: str,
    campaign: dict[str, Any],
) -> None:
    ensure_government_schema(conn)
    conn.execute(
        """
        INSERT OR REPLACE INTO simulation_campaigns (
            campaign_id, attacker_polity_id, defender_polity_id, kind,
            objective_json, start_sim_year, end_sim_year, outcome,
            attacker_force_strength, defender_force_strength,
            attacker_treasury_spent, defender_treasury_spent,
            siege_target_settlement_id, siege_years
        ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(campaign["campaign_id"]),
            int(campaign["attacker_polity_id"]),
            int(campaign["defender_polity_id"]),
            str(campaign["kind"]),
            json.dumps(campaign.get("objective") or {}, separators=(",", ":")),
            int(campaign["start_sim_year"]),
            str(campaign.get("outcome") or "ongoing"),
            float(campaign.get("attacker_force_strength") or 0.0),
            float(campaign.get("defender_force_strength") or 0.0),
            float(campaign.get("attacker_treasury_spent") or 0.0),
            float(campaign.get("defender_treasury_spent") or 0.0),
            campaign.get("siege_target_settlement_id"),
            int(campaign.get("siege_years") or 0),
        ),
    )


def clear_government_tables(conn: sqlite3.Connection, *, world: str) -> None:
    ensure_government_schema(conn)
    for tbl in (
        "simulation_battles",
        "simulation_campaigns",
        "simulation_alliances",
        "simulation_office_holdings",
        "simulation_office_seats",
        "simulation_polity_territory",
        "simulation_dynasties",
        "simulation_polities",
    ):
        try:
            conn.execute(f'DELETE FROM "{tbl}"')
        except sqlite3.OperationalError:
            pass


def government_checkpoint_payload(ctx: "SimulationContext") -> dict[str, Any]:
    polities = [
        {
            "polity_id": p.polity_id,
            "polity_type_id": p.polity_type_id,
            "parent_polity_id": p.parent_polity_id,
            "name": p.name,
            "capital_settlement_id": p.capital_settlement_id,
            "founding_dynasty_id": p.founding_dynasty_id,
            "founded_sim_year": p.founded_sim_year,
            "dissolved_sim_year": p.dissolved_sim_year,
            "status": p.status,
            "notes": p.notes,
        }
        for p in getattr(ctx, "gov_polities", {}).values()
    ]
    territory_open = [
        {
            "polity_id": t.polity_id,
            "target_kind": t.target_kind,
            "target_id": t.target_id,
            "since_sim_year": t.since_sim_year,
        }
        for t in getattr(ctx, "gov_territory_rows", [])
    ]
    seats = [
        {
            "seat_id": s.seat_id,
            "polity_id": s.polity_id,
            "title_id": s.title_id,
            "slot_index": s.slot_index,
            "scope_settlement_id": s.scope_settlement_id,
            "vacant_since_sim_year": s.vacant_since_sim_year,
            "status": s.status,
            "holder_person_id": s.holder_person_id,
            "term_expires_sim_year": s.term_expires_sim_year,
        }
        for s in getattr(ctx, "gov_office_seats", {}).values()
    ]
    dynasties = [
        {
            "dynasty_id": d.dynasty_id,
            "founder_person_id": d.founder_person_id,
            "house_name": d.house_name,
            "founded_sim_year": d.founded_sim_year,
            "extinct_sim_year": d.extinct_sim_year,
            "line": d.line,
        }
        for d in getattr(ctx, "gov_dynasties", {}).values()
    ]
    alliances = [
        {
            "alliance_id": a.alliance_id,
            "polity_a_id": a.polity_a_id,
            "polity_b_id": a.polity_b_id,
            "kind": a.kind,
            "since_sim_year": a.since_sim_year,
            "until_sim_year": a.until_sim_year,
            "payload": a.payload,
        }
        for a in getattr(ctx, "gov_alliances", [])
        if a.until_sim_year is None
    ]
    campaigns = [
        {
            "campaign_id": c.campaign_id,
            "attacker_polity_id": c.attacker_polity_id,
            "defender_polity_id": c.defender_polity_id,
            "kind": c.kind,
            "objective": c.objective,
            "start_sim_year": c.start_sim_year,
            "end_sim_year": c.end_sim_year,
            "outcome": c.outcome,
            "attacker_force_strength": c.attacker_force_strength,
            "defender_force_strength": c.defender_force_strength,
            "attacker_treasury_spent": c.attacker_treasury_spent,
            "defender_treasury_spent": c.defender_treasury_spent,
            "siege_target_settlement_id": c.siege_target_settlement_id,
            "siege_years": c.siege_years,
        }
        for c in getattr(ctx, "gov_campaigns", [])
        if (c.outcome or "").strip().lower() == "ongoing"
    ]
    return {
        "polities": polities,
        "territory_open": territory_open,
        "seats": seats,
        "dynasties": dynasties,
        "alliances": alliances,
        "campaigns": campaigns,
    }


def government_meta_ids(ctx: "SimulationContext") -> dict[str, int]:
    return {
        "next_polity_id": int(getattr(ctx, "next_gov_polity_id", 1)),
        "next_seat_id": int(getattr(ctx, "next_gov_seat_id", 1)),
        "next_dynasty_id": int(getattr(ctx, "next_gov_dynasty_id", 1)),
        "next_campaign_id": int(getattr(ctx, "next_gov_campaign_id", 1)),
        "next_alliance_id": int(getattr(ctx, "next_gov_alliance_id", 1)),
    }


def checkpoint_government(ctx: "SimulationContext", cur: sqlite3.Cursor) -> None:
    """Persist government RAM state; call inside an open save transaction."""
    ensure_government_schema(cur.connection)
    payload = government_checkpoint_payload(ctx)
    polities = payload["polities"]
    seats = payload["seats"]
    territory_open = payload["territory_open"]
    dynasties = payload["dynasties"]
    alliances = payload["alliances"]
    campaigns = payload["campaigns"]

    cur.execute("DELETE FROM simulation_polities")
    for p in polities:
        cur.execute(
            """
            INSERT INTO simulation_polities (
                polity_id, polity_type_id, parent_polity_id, name,
                capital_settlement_id, founding_dynasty_id, founded_sim_year,
                dissolved_sim_year, status, notes_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(p["polity_id"]),
                str(p["polity_type_id"]),
                p.get("parent_polity_id"),
                str(p.get("name") or ""),
                p.get("capital_settlement_id"),
                p.get("founding_dynasty_id"),
                int(p["founded_sim_year"]),
                p.get("dissolved_sim_year"),
                str(p.get("status") or "active"),
                json.dumps(p.get("notes") or {}, separators=(",", ":")),
            ),
        )

    cur.execute(
        "DELETE FROM simulation_polity_territory WHERE until_sim_year IS NULL",
    )
    for t in territory_open:
        cur.execute(
            """
            INSERT INTO simulation_polity_territory (
                polity_id, target_kind, target_id, since_sim_year, until_sim_year
            ) VALUES (?, ?, ?, ?, NULL)
            """,
            (
                int(t["polity_id"]),
                str(t["target_kind"]),
                str(t["target_id"]),
                int(t["since_sim_year"]),
            ),
        )

    cur.execute("DELETE FROM simulation_office_seats")
    for s in seats:
        cur.execute(
            """
            INSERT INTO simulation_office_seats (
                seat_id, polity_id, title_id, slot_index, scope_settlement_id,
                vacant_since_sim_year, status, holder_person_id, term_expires_sim_year
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(s["seat_id"]),
                int(s["polity_id"]),
                str(s["title_id"]),
                int(s.get("slot_index") or 0),
                s.get("scope_settlement_id"),
                s.get("vacant_since_sim_year"),
                str(s.get("status") or "active"),
                s.get("holder_person_id"),
                s.get("term_expires_sim_year"),
            ),
        )

    cur.execute("DELETE FROM simulation_dynasties")
    for d in dynasties:
        cur.execute(
            """
            INSERT INTO simulation_dynasties (
                dynasty_id, founder_person_id, house_name,
                founded_sim_year, extinct_sim_year, line
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                int(d["dynasty_id"]),
                int(d["founder_person_id"]),
                str(d.get("house_name") or ""),
                int(d["founded_sim_year"]),
                d.get("extinct_sim_year"),
                str(d.get("line") or "agnatic"),
            ),
        )

    cur.execute("DELETE FROM simulation_alliances WHERE until_sim_year IS NULL")
    for a in alliances:
        cur.execute(
            """
            INSERT INTO simulation_alliances (
                alliance_id, polity_a_id, polity_b_id, kind,
                since_sim_year, until_sim_year, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(a["alliance_id"]),
                int(a["polity_a_id"]),
                int(a["polity_b_id"]),
                str(a["kind"]),
                int(a["since_sim_year"]),
                a.get("until_sim_year"),
                json.dumps(a.get("payload") or {}, separators=(",", ":")),
            ),
        )

    cur.execute(
        "DELETE FROM simulation_campaigns WHERE outcome = ?",
        ("ongoing",),
    )
    for c in campaigns:
        cur.execute(
            """
            INSERT OR REPLACE INTO simulation_campaigns (
                campaign_id, attacker_polity_id, defender_polity_id, kind,
                objective_json, start_sim_year, end_sim_year, outcome,
                attacker_force_strength, defender_force_strength,
                attacker_treasury_spent, defender_treasury_spent,
                siege_target_settlement_id, siege_years
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(c["campaign_id"]),
                int(c["attacker_polity_id"]),
                int(c["defender_polity_id"]),
                str(c["kind"]),
                json.dumps(c.get("objective") or {}, separators=(",", ":")),
                int(c["start_sim_year"]),
                c.get("end_sim_year"),
                str(c.get("outcome") or "ongoing"),
                float(c.get("attacker_force_strength") or 0.0),
                float(c.get("defender_force_strength") or 0.0),
                float(c.get("attacker_treasury_spent") or 0.0),
                float(c.get("defender_treasury_spent") or 0.0),
                c.get("siege_target_settlement_id"),
                int(c.get("siege_years") or 0),
            ),
        )

    ids = government_meta_ids(ctx)
    _meta_set(cur, ctx.world.strip(), META_NEXT_POLITY_ID, ids["next_polity_id"])
    _meta_set(cur, ctx.world.strip(), META_NEXT_SEAT_ID, ids["next_seat_id"])
    _meta_set(cur, ctx.world.strip(), META_NEXT_DYNASTY_ID, ids["next_dynasty_id"])
    _meta_set(cur, ctx.world.strip(), META_NEXT_CAMPAIGN_ID, ids["next_campaign_id"])
    _meta_set(cur, ctx.world.strip(), META_NEXT_ALLIANCE_ID, ids["next_alliance_id"])


def apply_loaded_government(
    ctx: "SimulationContext",
    polities: list[Any],
    territory: list[Any],
    seats: list[Any],
    dynasties: list[Any],
    alliances: list[Any],
    campaigns: list[Any],
    meta_ids: dict[str, int],
) -> None:
    ctx.gov_polities = {p.polity_id: p for p in polities}
    ctx.gov_territory_rows = list(territory)
    ctx.gov_office_seats = {s.seat_id: s for s in seats}
    ctx.gov_dynasties = {d.dynasty_id: d for d in dynasties}
    ctx.gov_alliances = list(alliances)
    ctx.gov_campaigns = list(campaigns)
    ctx.next_gov_polity_id = int(meta_ids.get("next_polity_id", 1))
    ctx.next_gov_seat_id = int(meta_ids.get("next_seat_id", 1))
    ctx.next_gov_dynasty_id = int(meta_ids.get("next_dynasty_id", 1))
    ctx.next_gov_campaign_id = int(meta_ids.get("next_campaign_id", 1))
    ctx.next_gov_alliance_id = int(meta_ids.get("next_alliance_id", 1))


def load_government(
    ctx: "SimulationContext",
    conn: sqlite3.Connection,
    *,
    valid_person_ids: frozenset[int] | None = None,
) -> None:
    """Restore government state from SQLite into ``ctx``."""
    from library.polity import (
        AllianceState,
        CampaignState,
        DynastyState,
        OfficeSeatState,
        PolityState,
        TerritoryOpenRow,
    )

    ensure_government_schema(conn)

    polity_rows = conn.execute(
        "SELECT * FROM simulation_polities ORDER BY polity_id",
    ).fetchall()
    if not polity_rows:
        apply_loaded_government(ctx, [], [], [], [], [], [], {})
        return

    polities: list[PolityState] = []
    for r in polity_rows:
        rd = dict(r)
        notes_raw = rd.get("notes_json") or "{}"
        try:
            notes = json.loads(notes_raw) if isinstance(notes_raw, str) else {}
        except json.JSONDecodeError:
            notes = {}
        polities.append(
            PolityState(
                polity_id=int(rd["polity_id"]),
                polity_type_id=str(rd["polity_type_id"] or ""),
                parent_polity_id=(
                    int(rd["parent_polity_id"])
                    if rd.get("parent_polity_id") is not None
                    else None
                ),
                name=str(rd.get("name") or ""),
                capital_settlement_id=(
                    str(rd["capital_settlement_id"]).strip()
                    if rd.get("capital_settlement_id")
                    else None
                ),
                founding_dynasty_id=(
                    int(rd["founding_dynasty_id"])
                    if rd.get("founding_dynasty_id") is not None
                    else None
                ),
                founded_sim_year=int(rd.get("founded_sim_year") or 0),
                dissolved_sim_year=(
                    int(rd["dissolved_sim_year"])
                    if rd.get("dissolved_sim_year") is not None
                    else None
                ),
                status=str(rd.get("status") or "active"),
                notes=notes,
            )
        )

    terr_rows = conn.execute(
        """
        SELECT polity_id, target_kind, target_id, since_sim_year
        FROM simulation_polity_territory
        WHERE until_sim_year IS NULL
        """,
    ).fetchall()
    territory: list[TerritoryOpenRow] = [
        TerritoryOpenRow(
            polity_id=int(dict(r)["polity_id"]),
            target_kind=str(dict(r)["target_kind"] or ""),
            target_id=str(dict(r)["target_id"] or ""),
            since_sim_year=int(dict(r)["since_sim_year"] or 0),
        )
        for r in terr_rows
    ]

    seat_rows = conn.execute(
        "SELECT * FROM simulation_office_seats ORDER BY seat_id",
    ).fetchall()
    seats: list[OfficeSeatState] = []
    for r in seat_rows:
        rd = dict(r)
        seats.append(
            OfficeSeatState(
                seat_id=int(rd["seat_id"]),
                polity_id=int(rd["polity_id"]),
                title_id=str(rd["title_id"] or ""),
                slot_index=int(rd.get("slot_index") or 0),
                scope_settlement_id=(
                    str(rd["scope_settlement_id"]).strip()
                    if rd.get("scope_settlement_id")
                    else None
                ),
                vacant_since_sim_year=(
                    int(rd["vacant_since_sim_year"])
                    if rd.get("vacant_since_sim_year") is not None
                    else None
                ),
                status=str(rd.get("status") or "active"),
                holder_person_id=(
                    int(rd["holder_person_id"])
                    if rd.get("holder_person_id") is not None
                    else None
                ),
                term_expires_sim_year=(
                    int(rd["term_expires_sim_year"])
                    if rd.get("term_expires_sim_year") is not None
                    else None
                ),
            )
        )

    dyn_rows = conn.execute(
        "SELECT * FROM simulation_dynasties ORDER BY dynasty_id",
    ).fetchall()
    dynasties: list[DynastyState] = []
    for r in dyn_rows:
        rd = dict(r)
        dynasties.append(
            DynastyState(
                dynasty_id=int(rd["dynasty_id"]),
                founder_person_id=int(rd["founder_person_id"]),
                house_name=str(rd.get("house_name") or ""),
                founded_sim_year=int(rd.get("founded_sim_year") or 0),
                extinct_sim_year=(
                    int(rd["extinct_sim_year"])
                    if rd.get("extinct_sim_year") is not None
                    else None
                ),
                line=str(rd.get("line") or "agnatic"),
            )
        )

    ally_rows = conn.execute(
        """
        SELECT * FROM simulation_alliances
        WHERE until_sim_year IS NULL
        ORDER BY alliance_id
        """,
    ).fetchall()
    alliances: list[AllianceState] = []
    for r in ally_rows:
        rd = dict(r)
        raw_p = rd.get("payload_json") or "{}"
        try:
            payload = json.loads(raw_p) if isinstance(raw_p, str) else {}
        except json.JSONDecodeError:
            payload = {}
        alliances.append(
            AllianceState(
                alliance_id=int(rd["alliance_id"]),
                polity_a_id=int(rd["polity_a_id"]),
                polity_b_id=int(rd["polity_b_id"]),
                kind=str(rd["kind"] or ""),
                since_sim_year=int(rd["since_sim_year"] or 0),
                until_sim_year=None,
                payload=payload,
            )
        )

    camp_rows = conn.execute(
        """
        SELECT * FROM simulation_campaigns
        WHERE outcome = ? OR outcome IS NULL OR outcome = ''
        ORDER BY campaign_id
        """,
        ("ongoing",),
    ).fetchall()
    campaigns: list[CampaignState] = []
    for r in camp_rows:
        rd = dict(r)
        raw_o = rd.get("objective_json") or "{}"
        try:
            objective = json.loads(raw_o) if isinstance(raw_o, str) else {}
        except json.JSONDecodeError:
            objective = {}
        campaigns.append(
            CampaignState(
                campaign_id=int(rd["campaign_id"]),
                attacker_polity_id=int(rd["attacker_polity_id"]),
                defender_polity_id=int(rd["defender_polity_id"]),
                kind=str(rd["kind"] or ""),
                objective=objective,
                start_sim_year=int(rd["start_sim_year"] or 0),
                end_sim_year=(
                    int(rd["end_sim_year"])
                    if rd.get("end_sim_year") is not None
                    else None
                ),
                outcome=str(rd.get("outcome") or "ongoing"),
                attacker_force_strength=float(rd.get("attacker_force_strength") or 0.0),
                defender_force_strength=float(rd.get("defender_force_strength") or 0.0),
                attacker_treasury_spent=float(rd.get("attacker_treasury_spent") or 0.0),
                defender_treasury_spent=float(rd.get("defender_treasury_spent") or 0.0),
                siege_target_settlement_id=(
                    str(rd["siege_target_settlement_id"]).strip()
                    if rd.get("siege_target_settlement_id")
                    else None
                ),
                siege_years=int(rd.get("siege_years") or 0),
            )
        )

    if valid_person_ids is not None:
        from dataclasses import replace as _rep

        seats_fixed: list[OfficeSeatState] = []
        for s in seats:
            hid = s.holder_person_id
            if hid is not None and hid not in valid_person_ids:
                seats_fixed.append(
                    _rep(
                        s,
                        holder_person_id=None,
                        vacant_since_sim_year=None,
                    )
                )
            else:
                seats_fixed.append(s)
        seats = seats_fixed

    max_p = max((p.polity_id for p in polities), default=0)
    max_s = max((s.seat_id for s in seats), default=0)
    max_d = max((d.dynasty_id for d in dynasties), default=0)
    max_c = max((c.campaign_id for c in campaigns), default=0)
    max_a = max((a.alliance_id for a in alliances), default=0)

    meta_ids = {
        "next_polity_id": max(
            _meta_get(conn, ctx.world.strip(), META_NEXT_POLITY_ID, 1), max_p + 1
        ),
        "next_seat_id": max(_meta_get(conn, ctx.world.strip(), META_NEXT_SEAT_ID, 1), max_s + 1),
        "next_dynasty_id": max(_meta_get(conn, ctx.world.strip(), META_NEXT_DYNASTY_ID, 1), max_d + 1),
        "next_campaign_id": max(
            _meta_get(conn, ctx.world.strip(), META_NEXT_CAMPAIGN_ID, 1), max_c + 1
        ),
        "next_alliance_id": max(
            _meta_get(conn, ctx.world.strip(), META_NEXT_ALLIANCE_ID, 1), max_a + 1
        ),
    }

    apply_loaded_government(
        ctx,
        polities,
        territory,
        seats,
        dynasties,
        alliances,
        campaigns,
        meta_ids,
    )


def append_office_holding(
    conn: sqlite3.Connection,
    *,
    world: str,
    seat_id: int,
    holder_person_id: int,
    start_sim_year: int,
    ensure_schema: bool = True,
) -> int:
    if ensure_schema:
        ensure_government_schema(conn)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO simulation_office_holdings (
            seat_id, holder_person_id, start_sim_year, end_sim_year, end_reason
        ) VALUES (?, ?, ?, NULL, NULL)
        """,
        (int(seat_id), int(holder_person_id), int(start_sim_year)),
    )
    return int(cur.lastrowid or 0)


def close_office_holding(
    conn: sqlite3.Connection,
    *,
    world: str,
    seat_id: int,
    holder_person_id: int,
    end_sim_year: int,
    end_reason: str,
    ensure_schema: bool = True,
) -> None:
    if ensure_schema:
        ensure_government_schema(conn)
    conn.execute(
        """
        UPDATE simulation_office_holdings
        SET end_sim_year = ?, end_reason = ?
        WHERE seat_id = ? AND holder_person_id = ?
          AND end_sim_year IS NULL
        """,
        (
            int(end_sim_year),
            str(end_reason),
            int(seat_id),
            int(holder_person_id),
        ),
    )


def append_battle_row(
    conn: sqlite3.Connection,
    *,
    world: str,
    campaign_id: int,
    sim_year: int,
    location_settlement_id: str | None,
    attacker_force_strength: float,
    defender_force_strength: float,
    attacker_casualties: int,
    defender_casualties: int,
    outcome: str,
) -> None:
    ensure_government_schema(conn)
    conn.execute(
        """
        INSERT INTO simulation_battles (
            campaign_id, sim_year, location_settlement_id,
            attacker_force_strength, defender_force_strength,
            attacker_casualties, defender_casualties, outcome
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(campaign_id),
            int(sim_year),
            location_settlement_id,
            float(attacker_force_strength),
            float(defender_force_strength),
            int(attacker_casualties),
            int(defender_casualties),
            str(outcome),
        ),
    )


def close_territory_row(
    conn: sqlite3.Connection,
    *,
    world: str,
    polity_id: int,
    target_kind: str,
    target_id: str,
    until_sim_year: int,
) -> None:
    ensure_government_schema(conn)
    conn.execute(
        """
        UPDATE simulation_polity_territory
        SET until_sim_year = ?
        WHERE polity_id = ? AND target_kind = ? AND target_id = ?
          AND until_sim_year IS NULL
        """,
        (int(until_sim_year), int(polity_id), target_kind, target_id),
    )


def insert_territory_open(
    conn: sqlite3.Connection,
    *,
    world: str,
    polity_id: int,
    target_kind: str,
    target_id: str,
    since_sim_year: int,
) -> None:
    ensure_government_schema(conn)
    conn.execute(
        """
        INSERT INTO simulation_polity_territory (
            polity_id, target_kind, target_id, since_sim_year, until_sim_year
        ) VALUES (?, ?, ?, ?, NULL)
        """,
        (int(polity_id), target_kind, target_id, int(since_sim_year)),
    )


def update_campaign_row(
    conn: sqlite3.Connection,
    *,
    world: str,
    campaign_id: int,
    fields: dict[str, Any],
) -> None:
    if not fields:
        return
    ensure_government_schema(conn)
    cols = ", ".join(f'"{k}" = ?' for k in fields)
    vals = list(fields.values())
    vals.append(int(campaign_id))
    conn.execute(
        f'UPDATE simulation_campaigns SET {cols} WHERE campaign_id = ?',
        vals,
    )
