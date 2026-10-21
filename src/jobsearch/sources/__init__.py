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


def _legacy_ipv4(host: str) -> ipaddress.IPv4Address | None:
    """IPv4 in the old forms resolvers still accept: 2130706433, 0x7f.1, 127.1, 0177.0.0.1."""
    parts = host.split(".")
    if not 1 <= len(parts) <= 4:
        return None
    numbers = []
    for part in parts:
        try:
            if part[:2] in ("0x", "0X"):
                numbers.append(int(part[2:], 16))
            elif len(part) > 1 and part.startswith("0"):
                numbers.append(int(part, 8))
            else:
                numbers.append(int(part, 10))
        except ValueError:
            return None
    # The last part fills all remaining bytes: a.b.c.d, a.b.16bit, a.24bit or one 32-bit number
    *head, last = numbers
    if any(n > 255 for n in head) or last >= 256 ** (4 - len(head)):
        return None
    value = last
    for i, n in enumerate(head):
        value += n << (8 * (3 - i))
    return ipaddress.IPv4Address(value)


def is_local_host(host: str) -> bool:
    """True for localhost and for IP literals in loopback, private, link-local or unspecified ranges,
    also in the old IPv4 spellings. Host names are not resolved, so a public name pointing at a local
    address is not caught."""
    host = host.strip().strip("[]").rstrip(".").lower()
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = _legacy_ipv4(host) if host else None
        if address is None:
            return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return address.is_loopback or address.is_private or address.is_link_local or address.is_unspecified


def is_web_url(url: str) -> bool:
    """An http(s) URL with a host. A malformed URL is simply not one, it never raises."""
    try:
        parts = urlparse(url)
        return parts.scheme in ("http", "https") and bool(parts.hostname)
    except ValueError:
        return False


def safe_url(url: str | None) -> str | None:
    """The URL if it is an http(s) link with a host, else None. Postings come from third parties and
    their links end up as href in the dashboard and the Digest, where javascript: or data: would run."""
    url = (url or "").strip()
    return url if is_web_url(url) else None
