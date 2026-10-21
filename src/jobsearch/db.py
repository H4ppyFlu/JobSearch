"""SQLite access and schema migrations.

Migrations are the numbered files in migrations/. The applied version is kept
in SQLite's user_version, so a migration runs exactly once per database.
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3
from collections.abc import Callable
from datetime import datetime
from importlib import resources
from pathlib import Path

from .usage import CallUsage


# The id of a Job's newest Fit score, for a query whose jobs table is `j`. A rescored Job has several.
NEWEST_SCORE_ID = "(SELECT max(id) FROM fit_scores WHERE job_id = j.id)"


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def connect(path: Path | str, run_migrations: bool = True) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if run_migrations:
        migrate(conn)
    return conn


def _migrations() -> list[tuple[int, str]]:
    found = []
    for entry in resources.files("jobsearch.migrations").iterdir():
        if entry.name.endswith(".sql"):
            found.append((int(entry.name.split("_", 1)[0]), entry.read_text(encoding="utf-8")))
    return sorted(found)


def migrate(conn: sqlite3.Connection) -> None:
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for version, sql in _migrations():
        if version > current:
            with conn:
                conn.executescript(sql)
                conn.execute(f"PRAGMA user_version = {version}")


def start_run(conn: sqlite3.Connection, kind: str, settings: dict | None = None) -> int:
    """`settings` is the snapshot of what was in effect, for the eval."""
    with conn:
        cur = conn.execute(
            "INSERT INTO runs (kind, started_at, settings_json) VALUES (?, ?, ?)",
            (kind, now(), json.dumps(settings, ensure_ascii=False) if settings is not None else None),
        )
    return cur.lastrowid


def finish_run(conn: sqlite3.Connection, run_id: int, ok: bool, counts_json: str | None, error: str | None = None) -> None:
    with conn:
        conn.execute(
            "UPDATE runs SET finished_at = ?, ok = ?, counts_json = ?, error = ? WHERE id = ?",
            (now(), int(ok), counts_json, error, run_id),
        )


def usage_recorder(conn: sqlite3.Connection, run_id: int | None = None) -> Callable[[CallUsage], None]:
    """Stores the AI usage of each claude -p call, committed at once so it survives a later crash."""
    def record(usage: CallUsage) -> None:
        row = {"run_id": run_id, **dataclasses.asdict(usage)}
        with conn:
            conn.execute(f"INSERT INTO ai_calls ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                         list(row.values()))
    return record
