"""One-time setup for the Career scan: find each Target company's career page with web search.

The only step where the AI browses. The result is written back to private/companies.csv for the
candidate to check, and only into fields that are still empty, so hand-made entries survive.
"""

from __future__ import annotations

import csv
import io
import logging
import shutil
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

from . import ai

log = logging.getLogger(__name__)

BATCH_SIZE = 10
TIMEOUT_SECONDS = 2700  # web research for a whole batch takes much longer than scoring
FIELDS = ("career_url", "ats_type", "render_mode")
ATS_TYPES = ["personio", "workday", "successfactors", "softgarden", "smartrecruiters", "greenhouse", "lever",
             "recruitee", "dvinci", "rexx", "other", "unknown"]
SYSTEM = "You research company career pages on the web and answer only with the requested JSON."

SCHEMA = {
    "type": "object",
    "properties": {
        "companies": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "career_url": {"type": "string"},
                    "ats_type": {"type": "string", "enum": ATS_TYPES},
                    "render_mode": {"type": "string", "enum": ["static", "browser", "unknown"]},
                    "note": {"type": "string"},
                },
                "required": ["id", "career_url", "ats_type", "render_mode", "note"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["companies"],
    "additionalProperties": False,
}


@dataclass
class SetupResult:
    filled: int = 0
    not_found: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    error: str | None = None


def build_prompt(rows: list[dict], recheck: dict[str, str]) -> str:
    rubric = resources.files("jobsearch.prompts").joinpath("career_setup.md").read_text(encoding="utf-8")
    lines = []
    for i, row in enumerate(rows):
        extra = ", ".join(x for x in ((row.get("aliases") or "").replace("|", ", "), row.get("locations") or "") if x)
        line = f"- [{i}] {row['name']}" + (f" ({extra})" if extra else "")
        if row["name"] in recheck:
            line += f". Previous URL {row.get('career_url') or '(none)'} did not work: {recheck[row['name']]}"
        lines.append(line)
    return f"{rubric}\n\n# Companies\n\n" + "\n".join(lines) + "\n"


def check_answer(count: int):
    """Companies are answered by their number, since the model tends to tidy up names."""
    def check(data: dict) -> None:
        ids = sorted(c["id"] for c in data.get("companies", []))
        if ids != list(range(count)):
            raise ai.InvalidAnswer(f"Firmen fehlen oder doppelt: erwartet 0 bis {count - 1}, erhalten {ids}")
    return check


def run_setup(path: Path, *, model: str, ask=ai.ask, recheck: dict[str, str] | None = None) -> SetupResult:
    """`recheck` names companies whose page failed or listed nothing, with the problem. Their
    entries are replaced; all other entries are only filled where empty."""
    recheck = recheck or {}
    text = path.read_text(encoding="utf-8")
    reader = csv.DictReader(io.StringIO(text, newline=""))
    fieldnames = list(reader.fieldnames or [])
    for name in FIELDS:
        if name not in fieldnames:
            fieldnames.append(name)
    rows = list(reader)
    todo = [r for r in rows if (r.get("name") or "").strip()
            and (not (r.get("career_url") or "").strip() or r["name"] in recheck)]
    result = SetupResult()
    if not todo:
        return result
    backup = path.with_name(path.name + ".bak")
    if not backup.exists():  # keep the state from before the very first setup
        shutil.copy(path, backup)
    for start in range(0, len(todo), BATCH_SIZE):
        batch = todo[start:start + BATCH_SIZE]
        try:
            answer = ask(build_prompt(batch, recheck), model=model, schema=SCHEMA, system=SYSTEM,
                         check=check_answer(len(batch)), tools=("WebSearch", "WebFetch"),
                         timeout=TIMEOUT_SECONDS)
        except ai.AiError as e:
            log.error("Career setup failed: %s", e)
            result.error = str(e)
            break
        found = {c["id"]: c for c in answer.data["companies"]}
        for i, row in enumerate(batch):
            info = found[i]
            if info["note"]:
                result.notes.append(f"{row['name']}: {info['note']}")
            if not info["career_url"]:
                result.not_found.append(row["name"])
                continue
            for name in FIELDS:
                if row["name"] in recheck or not (row.get(name) or "").strip():
                    row[name] = info[name]
            result.filled += 1
        _write(path, fieldnames, rows)  # after every batch, so a later failure keeps what was found
    return result


def _write(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(path)
