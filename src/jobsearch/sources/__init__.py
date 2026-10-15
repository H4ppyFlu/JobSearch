"""Sources deliver Postings in one shared shape, whatever they come from."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Location:
    label: str
    lat: float | None = None
    lon: float | None = None


@dataclass
class Posting:
    source: str
    source_id: str
    title: str
    company: str
    url: str | None = None
    locations: list[Location] = field(default_factory=list)
    distance_km: float | None = None  # as reported by the Source, if any
    homeoffice_possible: bool = False
    description: str | None = None
    is_snippet: bool = False
    raw: dict | None = None

    @property
    def location_raw(self) -> str:
        return "; ".join(loc.label for loc in self.locations if loc.label)
