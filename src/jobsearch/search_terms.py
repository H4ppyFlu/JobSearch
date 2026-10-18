"""Search terms: the manual ones from private/search_terms.txt plus terms derived from the Profile.

Derived terms come from one claude -p call, made only when the Profile hash changed.
If that call fails, the previous derived terms stay in use and it is tried again next run.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from importlib import resources

from . import ai
from .scoring import profile_hash

log = logging.getLogger(__name__)

MIN_TERMS, MAX_TERMS = 5, 8
MAX_WORDS = 4  # the prompt asks for at most three, this leaves some slack before a retry
SYSTEM_PROMPT = "You pick search terms for a job search and answer only with the requested JSON."

SCHEMA = {
    "type": "object",
    "properties": {
        "terms": {"type": "array", "items": {"type": "string"}, "minItems": MIN_TERMS, "maxItems": MAX_TERMS},
    },
    "required": ["terms"],
    "additionalProperties": False,
}


@dataclass
class DerivedResult:
    terms: list[str] = field(default_factory=list)
    changed: bool = False
    error: str | None = None


def build_prompt(profile: str) -> str:
    rubric = resources.files("jobsearch.prompts").joinpath("search_terms.md").read_text(encoding="utf-8")
    return f"{rubric}\n# Profile\n\n{profile.strip()}\n"


def check_answer(data: dict) -> None:
    """Each term is sent as one query, so it has to be short and plain."""
    terms = [t.strip() for t in data["terms"]]
    for term in terms:
        if not term:
            raise ai.InvalidAnswer("leerer Suchbegriff")
        if len(term.split()) > MAX_WORDS or any(c in term for c in '()"'):
            raise ai.InvalidAnswer(f"kein einfacher Suchbegriff: {term}")
    if len({t.casefold() for t in terms}) != len(terms):
        raise ai.InvalidAnswer("doppelte Suchbegriffe")


def derived_terms(conn: sqlite3.Connection) -> list[str]:
    return [r["term"] for r in conn.execute("SELECT term FROM search_terms WHERE origin = 'derived' ORDER BY term")]


def refresh_derived(conn: sqlite3.Connection, profile: str, *, model: str, ask=ai.ask) -> DerivedResult:
    """Derive new terms if the Profile changed since the last derivation."""
    previous = derived_terms(conn)
    if is_current(conn, profile):
        return DerivedResult(previous)
    try:
        answer = ask(build_prompt(profile), model=model, schema=SCHEMA, system=SYSTEM_PROMPT, check=check_answer)
    except ai.AiError as e:
        log.error("Deriving Search terms failed: %s", e)
        return DerivedResult(previous, error=str(e))
    terms = [t.strip() for t in answer.data["terms"]]
    with conn:
        conn.execute("DELETE FROM search_terms WHERE origin = 'derived'")
        conn.executemany("INSERT INTO search_terms (term, origin, profile_hash) VALUES (?, 'derived', ?)",
                         [(t, profile_hash(profile)) for t in terms])
    return DerivedResult(terms, changed=True)


def switched_off(manual_lines: list[str]) -> list[str]:
    """Terms named in a '-term' line of private/search_terms.txt."""
    return [line[1:].strip() for line in manual_lines if line.startswith("-")]


def is_current(conn: sqlite3.Connection, profile: str) -> bool:
    """Whether the stored derived terms come from this Profile."""
    hashes = {r[0] for r in conn.execute("SELECT profile_hash FROM search_terms WHERE origin = 'derived'")}
    return hashes == {profile_hash(profile)}


def active_terms(manual_lines: list[str], derived: list[str]) -> list[str]:
    """Manual terms first, then derived ones. A line '-term' switches that term off, derived or not."""
    off = {t.casefold() for t in switched_off(manual_lines)}
    seen: set[str] = set()
    terms = []
    for term in [line for line in manual_lines if not line.startswith("-")] + derived:
        key = term.casefold()
        if key not in off and key not in seen:
            seen.add(key)
            terms.append(term)
    return terms
