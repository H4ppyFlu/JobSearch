"""The Daily run: collect Postings, decide Eligibility, store everything.

Two Bundesagentur queries per Search term:
- a radius query around home without a date limit. It returns every offer that is
  currently online, so last_seen stays accurate for every Job in reach.
- a nationwide query limited to recent offers, keeping only those with home office.
  The API has no home office filter, so this is the only way to find remote Jobs.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import date

import requests

from . import db
from .companies import TargetCompany, match_target_company
from .config import Config
from .eligibility import HOMEOFFICE_REASON, Rules, decide, distance_km, settle_remote
from .matching import JobIndex, MatchRules, Placement, attach
from .sources import Posting
from .sources.bundesagentur import BundesagenturClient, SchemaChanged

log = logging.getLogger(__name__)


@dataclass
class Counts:
    postings_seen: int = 0
    new_jobs: int = 0
    eligible: int = 0
    unclear: int = 0
    ineligible: int = 0
    descriptions_fetched: int = 0
    description_failures: int = 0
    remote_dropped: int = 0
    match_pending: int = 0
    reopenings: int = 0
    errors: list[str] = field(default_factory=list)


def remote_window_days(conn: sqlite3.Connection, today: date, max_days: int) -> int:
    """Days since the last successful Daily run, so missed days are caught up."""
    row = conn.execute(
        "SELECT max(started_at) FROM runs WHERE kind = 'daily' AND ok = 1"
    ).fetchone()
    if not row[0]:
        return max_days
    last = date.fromisoformat(row[0][:10])
    return max(1, min(max_days, (today - last).days + 1))


def place(posting: Posting, cfg: Config, targets: list[TargetCompany]) -> Placement:
    nearest, best = None, None
    for loc in posting.locations:
        if loc.lat is None or loc.lon is None:
            continue
        d = distance_km(cfg.home_lat, cfg.home_lon, loc.lat, loc.lon)
        if best is None or d < best:
            nearest, best = loc, d
    if best is None:
        best = posting.distance_km
        nearest = posting.locations[0] if posting.locations else None
    return Placement(best, nearest, match_target_company(posting.company, targets) is not None)


def collect(client: BundesagenturClient, cfg: Config, terms: list[str], window: int, counts: Counts) -> dict[str, Posting]:
    found: dict[str, Posting] = {}
    for term in terms:
        try:
            for p in client.search(term, location=cfg.home_location, radius_km=cfg.radius_target_company_km):
                found.setdefault(p.source_id, p)
            for p in client.search(term, published_within_days=window):
                if p.homeoffice_possible:
                    found.setdefault(p.source_id, p)
        except (requests.RequestException, SchemaChanged, ValueError) as e:
            log.error("Bundesagentur search for %r failed: %s", term, e)
            counts.errors.append(f"Suche '{term}': {e}")
    return found


def fetch_missing_descriptions(conn: sqlite3.Connection, client: BundesagenturClient, counts: Counts) -> None:
    """Full text for Postings of Jobs in reach. Ineligible Jobs are skipped to save requests."""
    rows = conn.execute(
        """SELECT p.id, p.source_id FROM postings p JOIN jobs j ON j.id = p.job_id
           WHERE p.source = 'bundesagentur' AND p.description IS NULL
             AND j.eligibility IN ('eligible', 'unclear')"""
    ).fetchall()
    for row in rows:
        try:
            text = client.description(row["source_id"])
        except requests.RequestException as e:
            log.warning("Description for %s failed: %s", row["source_id"], e)
            counts.description_failures += 1
            continue
        if text is None:
            # External offers (partner portals) have no description on the page.
            counts.description_failures += 1
            continue
        with conn:
            conn.execute("UPDATE postings SET description = ? WHERE id = ?", (text, row["id"]))
        counts.descriptions_fetched += 1


def settle_remote_jobs(conn: sqlite3.Connection, counts: Counts) -> None:
    """Home office Jobs outside the radius: keep only those whose text suggests fully remote."""
    rows = conn.execute(
        """SELECT j.id, p.description FROM jobs j JOIN postings p ON p.job_id = j.id
           WHERE j.eligibility = 'unclear' AND j.ineligible_reason = ? AND p.description IS NOT NULL""",
        (HOMEOFFICE_REASON,),
    ).fetchall()
    with conn:
        for row in rows:
            decision = settle_remote(row["description"])
            conn.execute("UPDATE jobs SET eligibility = ?, ineligible_reason = ? WHERE id = ?",
                         (decision.eligibility, decision.reason, row["id"]))
            if decision.eligibility == "ineligible":
                counts.remote_dropped += 1


def run_daily(
    conn: sqlite3.Connection,
    cfg: Config,
    client: BundesagenturClient,
    targets: list[TargetCompany],
    rules: Rules,
    terms: list[str],
    today: date | None = None,
) -> Counts:
    today = today or date.today()
    counts = Counts()
    run_id = db.start_run(conn, "daily")
    try:
        window = remote_window_days(conn, today, cfg.remote_max_window_days)
        log.info("Daily run: %d Search terms, remote window %d days", len(terms), window)
        found = collect(client, cfg, terms, window, counts)
        counts.postings_seen = len(found)
        index = JobIndex.load(conn)
        match_rules = MatchRules(cfg.reopening_days, cfg.radius_target_company_km)
        with conn:
            for posting in found.values():
                placement = place(posting, cfg, targets)
                decision = decide(rules, posting.title, placement.distance_km,
                                  placement.is_target_company, posting.homeoffice_possible)
                _, outcome = attach(conn, posting, placement, decision, today.isoformat(), index, match_rules)
                if outcome:
                    counts.new_jobs += 1
                    counts.match_pending += outcome == "pending"
                    counts.reopenings += outcome == "reopening"
                    setattr(counts, decision.eligibility, getattr(counts, decision.eligibility) + 1)
        fetch_missing_descriptions(conn, client, counts)
        settle_remote_jobs(conn, counts)
    except Exception as e:
        db.finish_run(conn, run_id, False, json.dumps(counts.__dict__, ensure_ascii=False), repr(e))
        raise
    ok = not counts.errors
    db.finish_run(conn, run_id, ok, json.dumps(counts.__dict__, ensure_ascii=False),
                  "; ".join(counts.errors) or None)
    return counts
