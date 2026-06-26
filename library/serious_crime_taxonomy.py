"""Shared serious-crime category labels and conservative inference helpers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


SERIOUS_CRIME_CATEGORY_LABELS: dict[str, str] = {
    "ordinary_murder": "ordinary murder",
    "feud_revenge_murder": "feud or revenge murder",
    "robbery_property_murder": "robbery or property-related murder",
    "outlaw_raid_killing": "outlaw raid killing",
    "war_political_legal_killing": "war, political, or legal killing",
    "spree_panic_killing": "spree or panic killing",
    "predatory_murder": "predatory murder",
    "serial_predatory_murder": "serial predatory murder",
}

SERIAL_PREDATION_CATEGORIES: frozenset[str] = frozenset(
    {"predatory_murder", "serial_predatory_murder"}
)

FEUD_REVENGE_INCIDENT_KINDS: frozenset[str] = frozenset(
    {
        "domestic_murder",
        "feud_killing",
        "feud_murder",
        "kin_killing",
    }
)

FEUD_REVENGE_MOTIVES: frozenset[str] = frozenset(
    {
        "partner_conflict",
        "paramour_conflict",
        "kin_conflict",
        "settlement_grievance",
        "work_rivalry",
        "revenge",
    }
)

FEUD_REVENGE_DETAILS: frozenset[str] = frozenset(
    {
        "household_grievance",
        "intimate_rivalry",
        "neighborhood_feud",
        "workplace_rivalry",
    }
)

PROPERTY_MURDER_MOTIVES: frozenset[str] = frozenset(
    {
        "property_gain",
        "robbery",
        "inheritance_plot",
        "debt_or_hardship",
        "extortion",
        "enrichment",
    }
)

PROPERTY_MURDER_DETAILS: frozenset[str] = frozenset(
    {
        "debt_dispute",
        "inheritance_plot",
        "robbery",
        "property_gain",
        "exposure_threat",
    }
)

SPREE_PANIC_INCIDENT_KINDS: frozenset[str] = frozenset(
    {
        "rash_brawl_killing",
        "panic_killing",
        "spree_killing",
    }
)

WAR_POLITICAL_LEGAL_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "battle_fought",
        "campaign_started",
        "campaign_ended",
        "legal_adjudication",
        "political_crime",
        "city_state_occupation_imposed",
        "city_state_liberated",
        "city_state_tyranny_usurpation",
        "city_state_exile_decreed",
    }
)


def serious_crime_category_label(category: object) -> str:
    """Return a stable public/debug label for a serious-crime category key."""

    key = str(category or "").strip()
    if not key:
        return "ordinary murder"
    return SERIOUS_CRIME_CATEGORY_LABELS.get(key, key.replace("_", " "))


def clean_serious_crime_category(category: object) -> str:
    """Normalize unknown or missing category keys to ordinary murder."""

    key = str(category or "").strip()
    if key in SERIOUS_CRIME_CATEGORY_LABELS:
        return key
    return "ordinary_murder"


def murder_taxonomy_category(
    *,
    incident_kind: object,
    motive: object = None,
    motive_detail: object = None,
    hidden_linked_kill_count: int = 0,
    serial_murder_classification: bool = False,
) -> str:
    """Classify a murder into the bounded V2 taxonomy.

    This is classification only. It does not make a person offend or increase
    murder volume.
    """

    kind = str(incident_kind or "").strip()
    motive_key = str(motive or "").strip()
    detail = str(motive_detail or "").strip()
    if kind == "predatory_murder":
        if bool(serial_murder_classification) or int(hidden_linked_kill_count or 0) >= 3:
            return "serial_predatory_murder"
        return "predatory_murder"
    if kind in SPREE_PANIC_INCIDENT_KINDS or detail == "deliberate_quarrel":
        return "spree_panic_killing"
    if (
        kind in FEUD_REVENGE_INCIDENT_KINDS
        or motive_key in FEUD_REVENGE_MOTIVES
        or detail in FEUD_REVENGE_DETAILS
    ):
        return "feud_revenge_murder"
    if motive_key in PROPERTY_MURDER_MOTIVES or detail in PROPERTY_MURDER_DETAILS:
        return "robbery_property_murder"
    return "ordinary_murder"


def murder_payload_taxonomy_category(payload: Mapping[str, Any]) -> str:
    """Return a murder payload category, inferring V1/older records when needed."""

    explicit = str(
        payload.get("serious_crime_category")
        or payload.get("murder_taxonomy")
        or ""
    ).strip()
    if explicit in SERIOUS_CRIME_CATEGORY_LABELS:
        return explicit
    context = payload.get("crime_context")
    motive_detail = payload.get("motive_detail")
    if isinstance(context, Mapping) and motive_detail in (None, ""):
        motive_detail = context.get("motive_detail")
    return murder_taxonomy_category(
        incident_kind=payload.get("incident_kind"),
        motive=payload.get("motive") or payload.get("motive_category"),
        motive_detail=motive_detail,
        hidden_linked_kill_count=_int_value(payload.get("hidden_linked_kill_count")),
        serial_murder_classification=bool(payload.get("serial_murder_classification")),
    )


def event_serious_crime_category(
    event_type: object,
    payload: Mapping[str, Any],
) -> str | None:
    """Classify serious violent/death events for report diagnostics."""

    event_key = str(event_type or payload.get("event_type") or "").strip()
    explicit = str(
        payload.get("serious_crime_category")
        or payload.get("violent_death_category")
        or ""
    ).strip()
    if explicit in SERIOUS_CRIME_CATEGORY_LABELS:
        return explicit
    if event_key == "murder":
        return murder_payload_taxonomy_category(payload)
    if event_key == "outlaw_raid" and _int_value(
        payload.get("casualties")
        or payload.get("fatalities")
        or payload.get("death_count")
        or payload.get("killed_count")
    ) > 0:
        return "outlaw_raid_killing"
    if event_key in WAR_POLITICAL_LEGAL_EVENT_TYPES and (
        _int_value(
            payload.get("casualties")
            or payload.get("fatalities")
            or payload.get("death_count")
            or payload.get("killed_count")
        )
        > 0
        or str(payload.get("outcome") or "").strip() == "execution"
    ):
        return "war_political_legal_killing"
    return None


def serial_classification_eligible_category(category: object) -> bool:
    return str(category or "").strip() in SERIAL_PREDATION_CATEGORIES


def _int_value(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
