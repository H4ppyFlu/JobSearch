"""Target companies from private/companies.csv and matching company names against them."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

from .config import ConfigError

# Words that say nothing about which company it is.
_NOISE = {
    "gmbh", "mbh", "ag", "se", "kg", "kgaa", "co", "ug", "ohg", "ev", "eg", "inc", "ltd", "llc",
    "holding", "gruppe", "group", "und", "the", "deutschland", "germany", "gesellschaft", "fuer",
}
_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


def normalize_company(name: str) -> tuple[str, ...]:
    """Company name as comparable tokens: lowercase, no legal form, no punctuation."""
    cleaned = re.sub(r"[^a-z0-9]+", " ", name.lower().translate(_UMLAUTS))
    return tuple(t for t in cleaned.split() if t not in _NOISE)


@dataclass(frozen=True)
class TargetCompany:
    name: str
    keys: tuple[tuple[str, ...], ...]
    locations: str
    career_url: str
    ats_type: str
    render_mode: str


def load_target_companies(path: Path) -> list[TargetCompany]:
    if not path.exists():
        raise ConfigError(f"{path} is missing. Run 'jobsearch init' and fill it in.")
    companies = []
    # utf-8-sig: a list saved from a spreadsheet often starts with a BOM, which would hide the "name" column
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            name = (row.get("name") or "").strip()
            if not name:
                continue
            names = [name] + [a.strip() for a in (row.get("aliases") or "").split("|") if a.strip()]
            keys = tuple(k for k in (normalize_company(n) for n in names) if k)
            companies.append(TargetCompany(
                name=name,
                keys=keys,
                locations=(row.get("locations") or "").strip(),
                career_url=(row.get("career_url") or "").strip(),
                ats_type=(row.get("ats_type") or "").strip(),
                render_mode=(row.get("render_mode") or "").strip(),
            ))
    return companies


def match_target_company(company: str, targets: list[TargetCompany]) -> TargetCompany | None:
    """The Target company whose name or alias tokens all appear in the given company name."""
    tokens = set(normalize_company(company))
    if not tokens:
        return None
    for target in targets:
        if any(set(key) <= tokens for key in target.keys):
            return target
    return None
