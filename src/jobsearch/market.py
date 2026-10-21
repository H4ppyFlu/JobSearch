"""The Market report: active companies and skill groups across all scored Jobs.

- Active companies: companies with several Jobs in reach within WINDOW_DAYS, or a Reopening,
  counted by `normalize_company`. Generic jobs are left out, since staffing agencies would
  otherwise top the list. Plain Python, no AI.
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta

from .companies import normalize_company

WINDOW_DAYS = 90
MIN_JOBS = 3
# The newest Fit score of a Job; a Job can be scored again, for example after a merge.
NEWEST_SCORE = """LEFT JOIN fit_scores s ON s.id = (SELECT id FROM fit_scores WHERE job_id = j.id
                                                   ORDER BY scored_at DESC, id DESC LIMIT 1)"""


@dataclass
class CompanySignal:
    name: str  # the most frequent spelling
    jobs: int = 0
    best_tier: int | None = None  # of the newest Fit scores, None while no Job is scored
    reopenings: int = 0
    is_target: bool = False
    spellings: Counter = field(default_factory=Counter, repr=False, compare=False)

    @property
    def note(self) -> str:
        return "" if self.is_target else "Kandidat für companies.csv"


def window_start(today: date) -> str:
    """First day of the last WINDOW_DAYS days, today included."""
    return (today - timedelta(days=WINDOW_DAYS - 1)).isoformat()


def active_companies(conn: sqlite3.Connection, today: date) -> list[CompanySignal]:
    """Companies with MIN_JOBS or more eligible Jobs first seen in the window, or a Reopening there.
    Most Jobs first. Nothing is written to companies.csv; the note only names candidates."""
    rows = conn.execute(
        f"""SELECT j.company, j.is_target_company, j.reopening_of, s.tier FROM jobs j {NEWEST_SCORE}
            WHERE j.eligibility = 'eligible' AND j.is_generic = 0 AND j.first_seen >= ?""", (window_start(today),))
    found: dict[tuple[str, ...], CompanySignal] = {}
    for r in rows:
        key = normalize_company(r["company"])
        if not key:
            continue
        c = found.setdefault(key, CompanySignal(r["company"]))
        c.spellings[r["company"]] += 1
        c.jobs += 1
        c.reopenings += r["reopening_of"] is not None
        c.is_target = c.is_target or bool(r["is_target_company"])
        if r["tier"] is not None:
            c.best_tier = max(c.best_tier or 0, r["tier"])
    for c in found.values():
        c.name = min(c.spellings, key=lambda name: (-c.spellings[name], name))
    active = [c for c in found.values() if c.jobs >= MIN_JOBS or c.reopenings]
    return sorted(active, key=lambda c: (-c.jobs, -c.reopenings, c.name.casefold()))


STARS = {3: "★★★", 2: "★★", 1: "★", None: "–"}


def company_report(companies: list[CompanySignal]) -> list[str]:
    title = f"Aktive Firmen (letzte {WINDOW_DAYS} Tage, Jobs in Reichweite, ohne Sammelanzeigen):"
    if not companies:
        return [title, f"  Keine aktiven Firmen: keine Firma mit {MIN_JOBS} oder mehr Jobs oder einem Reopening."]
    header = ("Firma", "Jobs", "bestes Tier", "Reopenings", "")
    rows = [(c.name, str(c.jobs), STARS[c.best_tier], str(c.reopenings), c.note) for c in companies]
    widths = [max(len(row[i]) for row in (header, *rows)) for i in range(len(header))]

    def line(row: tuple[str, ...]) -> str:
        name, *numbers, note = row
        cells = [name.ljust(widths[0])] + [v.rjust(w) for v, w in zip(numbers, widths[1:-1])] + [note]
        return ("  " + "  ".join(cells)).rstrip()

    return [title, line(header)] + [line(row) for row in rows]
