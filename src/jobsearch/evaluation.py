"""The eval: a read-only look at what the runs stored. No AI call, and it works retroactively.

So far it shows when the settings changed (`jobsearch eval --changes`). Each Daily run stores a
snapshot of what was in effect, and the dates where the snapshot differs are the split points
for comparing results before and after a change.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

LABELS = {
    "model_scoring": "Modell Bewertung",
    "model_extraction": "Modell Karriereseiten",
    "prompt_scoring": "Prompt Bewertung",
    "prompt_career_extract": "Prompt Karriereseiten",
    "prompt_search_terms": "Prompt Suchbegriffe",
    "profile": "Profil",
    "search_terms": "Suchbegriffe",
    "exclusions": "Ausschlussliste",
    "target_companies": "Zielunternehmen",
    "alert_mail": "Alert-Mail",
    "radius_km": "Radius (km)",
    "radius_target_company_km": "Radius Zielunternehmen (km)",
    "remote_max_window_days": "Zeitfenster Remote (Tage)",
    "reopening_days": "Reopening nach (Tagen)",
    "max_scored_per_run": "Bewertungen pro Lauf",
    "feedback_notes_in_prompt": "Feedback notes im Prompt",
}
# Stored as hashes, so only "changed" can be said about them.
HASHED = {"prompt_scoring", "prompt_career_extract", "prompt_search_terms", "profile", "exclusions",
          "target_companies"}


@dataclass(frozen=True)
class Change:
    day: str  # of the first Daily run with the new settings
    lines: list[str]


def snapshots(conn: sqlite3.Connection) -> list[tuple[str, dict]]:
    rows = conn.execute("""SELECT started_at, settings_json FROM runs
                           WHERE kind = 'daily' AND settings_json IS NOT NULL ORDER BY id""")
    return [(r["started_at"], json.loads(r["settings_json"])) for r in rows]


def _value(value) -> str:
    if isinstance(value, bool):
        return "an" if value else "aus"
    return "–" if value is None else str(value)


def describe(old: dict, new: dict) -> list[str]:
    """What differs between two snapshots, one German line per setting."""
    lines = []
    for key in dict.fromkeys([*new, *old]):
        before, after = old.get(key), new.get(key)
        if before == after:
            continue
        label = LABELS.get(key, key)
        if isinstance(before, list) or isinstance(after, list):
            before, after = before or [], after or []
            parts = [f"+ {t}" for t in after if t not in before] + [f"− {t}" for t in before if t not in after]
            lines.append(f"{label}: {', '.join(parts)}")
        elif key in HASHED:
            state = "fehlt" if after is None else "vorhanden" if before is None else "geändert"
            lines.append(f"{label}: {state}")
        else:
            lines.append(f"{label}: {_value(before)} → {_value(after)}")
    return lines


def changes(snaps: list[tuple[str, dict]]) -> list[Change]:
    result = []
    previous = None
    for started_at, snap in snaps:
        if previous is None:
            result.append(Change(started_at[:10], ["Beginn der Aufzeichnung"]))
        else:
            lines = describe(previous, snap)
            if lines:
                result.append(Change(started_at[:10], lines))
        previous = snap
    return result


def unmarked_scores(conn: sqlite3.Connection) -> int:
    """Fit scores from before prompt versions were stored."""
    return conn.execute("SELECT count(*) FROM fit_scores WHERE prompt_hash IS NULL").fetchone()[0]


def changes_report(conn: sqlite3.Connection) -> list[str]:
    found = changes(snapshots(conn))
    if not found:
        lines = ["Noch keine Daily runs mit gespeicherten Einstellungen. Die Aufzeichnung beginnt mit dem nächsten."]
    else:
        lines = ["Änderungen der Einstellungen (je erster Daily run mit den neuen Werten):"]
        for change in found:
            lines.append(f"  {change.day}  {change.lines[0]}")
            lines.extend(f"              {line}" for line in change.lines[1:])
    unmarked = unmarked_scores(conn)
    if unmarked:
        lines.append(f"Fit scores ohne Prompt-Version: {unmarked}. Sie stammen von vor der Aufzeichnung, "
                     "ihre Version zeigt 'git log' von scoring.md im Vergleich mit dem Bewertungsdatum.")
    return lines
