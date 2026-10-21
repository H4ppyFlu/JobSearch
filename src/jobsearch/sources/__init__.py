"""Sources deliver Postings in one shared shape, whatever they come from."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from urllib.parse import urlparse


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


def is_local_host(host: str) -> bool:
    """True for localhost and for IP literals in loopback, private, link-local or unspecified ranges.
    Host names are not resolved, so a public name pointing at a local address is not caught."""
    host = host.strip().strip("[]").lower()
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or address.is_private or address.is_link_local or address.is_unspecified


def safe_url(url: str | None) -> str | None:
    """The URL if it is an http(s) link with a host, else None. Postings come from third parties and
    their links end up as href in the dashboard and the Digest, where javascript: or data: would run."""
    url = (url or "").strip()
    parts = urlparse(url)
    return url if parts.scheme.lower() in ("http", "https") and parts.hostname else None
