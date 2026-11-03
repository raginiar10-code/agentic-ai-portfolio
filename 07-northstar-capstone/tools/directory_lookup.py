from __future__ import annotations

import json
import logging
from pathlib import Path

from langchain_core.tools import tool

logger = logging.getLogger(__name__)

_THERAPISTS_PATH = Path(__file__).parent.parent / "kb" / "therapists.json"


def _normalize(value: str) -> str:
    """Make category/city strings comparable regardless of casing or spacing.

    e.g. "Occupational Therapy" and "occupational-therapy" both become "occupational-therapy",
    so the Pathway Agent can pass either a human-readable name or a KB id and still match.
    """
    return value.strip().lower().replace(" ", "-")


def _load_therapists() -> list[dict]:
    """Read therapists.json fresh on every call — the file is tiny (16 entries), so no caching needed."""
    with _THERAPISTS_PATH.open(encoding="utf-8") as f:
        return json.load(f)


# The @tool decorator (from langchain_core.tools) turns this plain function into a LangChain
# Tool object. That matters later in agents/pathway.py: the decorated version carries its own
# name/description/argument-schema (auto-derived from the type hints and this docstring), which
# is what gets handed to ChatOpenAI.bind_tools([...]) so the model knows this tool exists and
# how to call it.
@tool
def directory_lookup(therapy_categories: list[str], city: str, limit: int = 5) -> list[dict]:
    """Look up fictional specialist therapists by therapy category and city.

    Reads therapists.json, filters entries whose specialization matches one of
    therapy_categories AND whose city matches city, and returns up to `limit` matches.
    """
    logger.info(f"directory_lookup: categories={therapy_categories} city={city!r} limit={limit}")

    # Input validation per IMPLEMENTATION_PLAN.md's Day 3 risk note ("Tool call failures (bad
    # params) — add input validation inside the tool"). Raising loudly here, rather than
    # silently returning [], makes a misconfigured caller (the Pathway Agent calling this
    # without a city) visible immediately instead of looking like "no therapists found".
    if not city:
        raise ValueError("directory_lookup requires a non-empty city.")
    if not therapy_categories:
        raise ValueError("directory_lookup requires at least one therapy category.")

    normalized_categories = {_normalize(c) for c in therapy_categories}
    normalized_city = _normalize(city)

    therapists = _load_therapists()
    # List comprehension with two conditions chained by "and": keep a therapist only if BOTH
    # their specialization is one of the requested categories AND their city matches.
    matches = [
        t
        for t in therapists
        if _normalize(t["specialization"]) in normalized_categories and _normalize(t["city"]) == normalized_city
    ]

    logger.info(f"directory_lookup: found {len(matches)} match(es)")
    # Slicing [:limit] caps the result at `limit` entries even if more matched.
    return matches[:limit]
