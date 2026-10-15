"""Attaching Postings to Jobs.

Phase 2 version: a Posting is recognized only by its source ID, and every new
Posting becomes its own Job. Matching across Sources, Generic jobs and
Reopenings follow in Phase 4.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from .eligibility import Decision
from .sources import Location, Posting


@dataclass(frozen=True)
class Placement:
    """Where a Posting sits relative to home."""
    distance_km: float | None
    nearest: Location | None
    is_target_company: bool


def attach(conn: sqlite3.Connection, posting: Posting, placement: Placement, decision: Decision, today: str) -> tuple[int, bool]:
    """Store or refresh a Posting. Returns (job_id, created)."""
    row = conn.execute(
        "SELECT id, job_id FROM postings WHERE source = ? AND source_id = ?",
        (posting.source, posting.source_id),
    ).fetchone()
    if row:
        conn.execute("UPDATE postings SET last_seen = ? WHERE id = ?", (today, row["id"]))
        if row["job_id"] is not None:
            conn.execute("UPDATE jobs SET last_seen = ? WHERE id = ? AND last_seen < ?", (today, row["job_id"], today))
        return row["job_id"], False

    nearest = placement.nearest
    cur = conn.execute(
        """INSERT INTO jobs (company, title, location, lat, lon, distance_km, is_target_company,
                             eligibility, ineligible_reason, first_seen, last_seen)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            posting.company, posting.title,
            nearest.label if nearest else posting.location_raw or None,
            nearest.lat if nearest else None, nearest.lon if nearest else None,
            None if placement.distance_km is None else round(placement.distance_km, 1),
            int(placement.is_target_company),
            decision.eligibility, decision.reason, today, today,
        ),
    )
    job_id = cur.lastrowid
    conn.execute(
        """INSERT INTO postings (job_id, source, source_id, url, title, company, location_raw,
                                 description, is_snippet, raw_json, first_seen, last_seen)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            job_id, posting.source, posting.source_id, posting.url, posting.title, posting.company,
            posting.location_raw or None, posting.description, int(posting.is_snippet),
            json.dumps(posting.raw, ensure_ascii=False) if posting.raw else None, today, today,
        ),
    )
    return job_id, True
