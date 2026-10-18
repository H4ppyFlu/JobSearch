"""Windows toast after a run, only when there is something worth looking at."""

from __future__ import annotations

import logging
from pathlib import Path

from .digest import DayReport
from .scoring import ScoreCounts

log = logging.getLogger(__name__)


def message(report: DayReport, scoring: ScoreCounts | None, warnings=()) -> tuple[str, str] | None:
    """Title and body of the toast, or None when there is nothing to say."""
    good = report.new_good
    parts = [f"{n}× {'★' * tier}" for tier, n in good.items() if n]
    lines = []
    if parts:
        lines.append(f"Neu: {', '.join(parts)}")
    if scoring and scoring.error:
        lines.append("Bewertung fehlgeschlagen, Details im Digest")
    if warnings:
        lines.append(f"{len(warnings)} Warnung(en), Details im Digest")
    if report.backlog:
        lines.append(f"{report.backlog} Jobs warten auf Bewertung")
    if not lines:
        return None
    return "JobSearch", "\n".join(lines)


def notify(report: DayReport, scoring: ScoreCounts | None, digest_path: Path, warnings=()) -> bool:
    """Show the toast. Clicking it opens the Digest."""
    msg = message(report, scoring, warnings)
    if msg is None:
        return False
    return toast(*msg, on_click=digest_path.resolve().as_uri())


def toast(title: str, body: str, on_click: str | None = None) -> bool:
    """Never raises: a toast is not worth failing a run."""
    try:
        from win11toast import notify as show
        show(title, body, on_click=on_click)
        return True
    except Exception as e:  # missing package, no Windows session, COM errors
        log.warning("Toast failed: %s", e)
        return False
