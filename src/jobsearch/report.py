"""The Weekly report: the pipeline's own metrics, read by the AI, turned into Recommendations.

This module builds what the AI sees. Every number is a Metric with a stable ID and the count it
rests on, so the AI can cite IDs instead of writing numbers, and the report fills in the values.
The input holds aggregates only, plus the candidate's own Search terms and rule-made reasons:
no text from job ads, so this call has no Third-party text.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from .config import ConfigError
from .evaluation import (SOURCES, STAGES, Change, Period, Trace, changes, read_found, score_tables, snapshots,
                         stage_metrics, term_metrics, trace)

_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.casefold().translate(_UMLAUTS)).strip("-") or "x"


@dataclass(frozen=True)
class Metric:
    id: str
    value: int
    n: int  # the count the value rests on
    label: str  # German, for the report


@dataclass
class ReportInput:
    metrics: list[Metric] = field(default_factory=list)
    terms: dict[str, str] = field(default_factory=dict)  # slug in the IDs -> the Search term
    changes: list[Change] = field(default_factory=list)  # settings changes since the last report

    def term_slug(self, term: str) -> str:
        """A stable slug per term; terms that slug alike get -2, -3, ..."""
        for key, known in self.terms.items():
            if known == term:
                return key
        base = key = slug(term)
        number = 1
        while key in self.terms:
            number += 1
            key = f"{base}-{number}"
        self.terms[key] = term
        return key

    def add(self, id: str, value: int, n: int, label: str, keep_empty: bool = False) -> None:
        """A metric resting on nothing says nothing and only costs tokens, so it is left out."""
        if n or keep_empty:
            self.metrics.append(Metric(id, value, n, label))

    def to_prompt_json(self) -> str:
        return json.dumps({"metrics": [m.__dict__ for m in self.metrics],
                           "changes": [{"day": c.day, "lines": c.lines} for c in self.changes]},
                          ensure_ascii=False, separators=(",", ":"))


def _periods(today: date) -> list[tuple[str, str, Period]]:
    """Suffix of the IDs, German label, and the period: the last 7 days and the whole history."""
    week = Period("7 Tage", (today - timedelta(days=6)).isoformat(), (today + timedelta(days=1)).isoformat())
    return [("7d", "letzte 7 Tage", week), ("all", "gesamt", Period("gesamt"))]


def _stages(inp: ReportInput, conn: sqlite3.Connection, key: str, name: str, period: Period) -> None:
    s = stage_metrics(conn, period)
    for source, label in SOURCES.items():
        n = s.postings.get(source, 0)
        inp.add(f"stage.postings.{source}.{key}", n, n, f"Neue Postings {label} ({name})")
    inp.add(f"stage.jobs.{key}", s.jobs, s.jobs, f"Neue Jobs ({name})")
    for state in ("eligible", "unclear", "ineligible"):
        inp.add(f"stage.{state}.{key}", s.eligibility[state], s.jobs, f"Jobs {state} ({name})")
    for reason, n in sorted(s.reasons.items()):
        inp.add(f"stage.ineligible.{slug(reason)}.{key}", n, s.eligibility["ineligible"],
                f"Ineligible wegen {reason} ({name})")
    inp.add(f"stage.generic.{key}", s.generic, s.jobs, f"Sammelanzeigen ({name})")
    for tier in (3, 2, 1):
        inp.add(f"stage.tier{tier}.{key}", s.tiers[tier], s.scored, f"Bewertet mit {tier} Sternen ({name})")
    inp.add(f"stage.low_confidence.{key}", s.low_confidence, s.scored, f"Geringe Sicherheit ({name})")
    inp.add(f"stage.unscored.{key}", s.unscored, s.eligibility["eligible"], f"Eligible, noch unbewertet ({name})")


def _terms(inp: ReportInput, conn: sqlite3.Connection, key: str, name: str, period: Period) -> None:
    for t in term_metrics(conn, period):
        prefix, label = f"term.{inp.term_slug(t.term)}", f"Suchbegriff '{t.term}'"
        inp.add(f"{prefix}.jobs.{key}", t.jobs, t.jobs, f"{label}: Jobs ({name})", keep_empty=True)
        inp.add(f"{prefix}.only_here.{key}", t.only_here, t.jobs, f"{label}: nur von diesem Begriff gefunden ({name})")
        inp.add(f"{prefix}.eligible.{key}", t.eligible, t.jobs, f"{label}: eligible ({name})")
        scored = sum(t.tiers.values())
        for tier in (3, 2, 1):
            inp.add(f"{prefix}.tier{tier}.{key}", t.tiers[tier], scored, f"{label}: {tier} Sterne ({name})")
        inp.add(f"{prefix}.positive.{key}", t.positive, t.jobs, f"{label}: positiver Job status ({name})")


_LABELS = {"positive": "positiv", "negative": "negativ", None: "ohne Label"}


def _scores(inp: ReportInput, conn: sqlite3.Connection, key: str, name: str, period: Period) -> None:
    t = score_tables(conn, period).get("alle")
    if not t:
        return
    for tier in (3, 2, 1):
        in_tier = sum(t[(tier, lab)] for lab in _LABELS)
        for lab, german in _LABELS.items():
            inp.add(f"score.tier{tier}.{lab or 'unlabeled'}.{key}", t[(tier, lab)], in_tier,
                    f"{tier} Sterne, Job status {german} ({name})")
    top_labeled = t[(3, "positive")] + t[(3, "negative")]
    if top_labeled:
        inp.add(f"score.tier3.precision.{key}", round(100 * t[(3, "positive")] / top_labeled), top_labeled,
                f"Treffsicherheit 3 Sterne in Prozent ({name})")
    positives = sum(t[(tier, "positive")] for tier in (3, 2, 1))
    inp.add(f"score.missed_in_tier1.{key}", t[(1, "positive")], positives,
            f"Positive Jobs, die nur 1 Stern bekamen ({name})")
    inp.add(f"score.labeled.{key}", positives + sum(t[(tier, "negative")] for tier in (3, 2, 1)),
            sum(t.values()), f"Bewertete Jobs mit Label ({name})")


def _traces(conn: sqlite3.Connection, found_path: Path) -> list[Trace]:
    try:
        found = read_found(found_path)
    except ConfigError:  # the file is optional
        return []
    since = conn.execute("SELECT min(substr(started_at, 1, 10)) FROM runs WHERE kind = 'daily'").fetchone()[0]
    return [trace(conn, f, since) for f in found]


def _funnel(inp: ReportInput, traces: list[Trace], key: str, name: str, period: Period) -> None:
    """Openings found elsewhere, per stage. For the week, those whose found_on lies in it."""
    if period.since:
        traces = [t for t in traces if t.found.found_on and period.since <= t.found.found_on < period.until]
    for stage, n in Counter(t.stage for t in traces).items():
        inp.add(f"funnel.{stage}.{key}", n, len(traces), f"Fremdfunde: {STAGES[stage][0]} ({name})")


def report_input(conn: sqlite3.Connection, today: date, found_path: Path, last_report_at: str | None) -> ReportInput:
    inp = ReportInput()
    since = last_report_at[:10] if last_report_at else ""
    inp.changes = [c for c in changes(snapshots(conn)) if c.day >= since]
    traces = _traces(conn, found_path)
    for key, name, period in reversed(_periods(today)):  # the whole history first, so slugs follow its order
        _stages(inp, conn, key, name, period)
        _terms(inp, conn, key, name, period)
        _scores(inp, conn, key, name, period)
        _funnel(inp, traces, key, name, period)
    return inp
