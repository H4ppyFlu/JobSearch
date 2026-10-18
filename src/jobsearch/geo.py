"""Place names to coordinates, for Sources that give only a place name (Indeed alert mails).

Uses the GeoNames postal code files for DE and CH (CC BY 4.0, https://www.geonames.org),
downloaded once by `jobsearch init` into data/geo/places.tsv. Postal codes of one
place and district are averaged into one point. A name that exists several times,
or a short name like "Musterstadt" for "Musterstadt am See", resolves to the
candidate nearest home: alert mails are already limited to a radius around home.
"""

from __future__ import annotations

import io
import logging
import re
import zipfile
from collections import defaultdict
from pathlib import Path

import requests

from .eligibility import distance_km

log = logging.getLogger(__name__)

GEONAMES_URL = "https://download.geonames.org/export/zip/{country}.zip"
COUNTRIES = ("DE", "CH")
_POSTCODE = re.compile(r"\b(\d{5}|\d{4})\b")


def _key(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower())


def download(path: Path, session=None) -> int:
    """Fetch the postal code files and write one row per place and district: name, postcode, lat, lon."""
    session = session or requests.Session()
    groups: dict[tuple[str, str], list[tuple[str, float, float]]] = defaultdict(list)
    for country in COUNTRIES:
        response = session.get(GEONAMES_URL.format(country=country), timeout=60)
        response.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            text = archive.read(f"{country}.txt").decode("utf-8")
        for line in text.splitlines():
            f = line.split("\t")
            if len(f) < 11 or not f[9] or not f[10]:
                continue
            groups[(f[2], f[0] + f[8])].append((f[1], float(f[9]), float(f[10])))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as out:
        for (name, _), points in sorted(groups.items()):
            lat = sum(p[1] for p in points) / len(points)
            lon = sum(p[2] for p in points) / len(points)
            out.write(f"{name}\t{min(p[0] for p in points)}\t{lat:.4f}\t{lon:.4f}\n")
            for code, plat, plon in points:
                out.write(f"\t{code}\t{plat:.4f}\t{plon:.4f}\n")  # postcode rows have no name
    return len(groups)


class Gazetteer:
    def __init__(self, home: tuple[float, float]):
        self.home = home
        self.by_name: dict[str, list[tuple[float, float]]] = defaultdict(list)
        self.by_postcode: dict[str, list[tuple[float, float]]] = defaultdict(list)

    @classmethod
    def load(cls, path: Path, home: tuple[float, float]) -> Gazetteer | None:
        if not path.exists():
            return None
        gazetteer = cls(home)
        for line in path.read_text(encoding="utf-8").splitlines():
            name, code, lat, lon = line.split("\t")
            point = (float(lat), float(lon))
            if name:
                gazetteer.by_name[_key(name)].append(point)
            else:
                gazetteer.by_postcode[code].append(point)
        return gazetteer

    def _nearest(self, points: list[tuple[float, float]]) -> tuple[float, float] | None:
        if not points:
            return None
        return min(points, key=lambda p: distance_km(self.home[0], self.home[1], p[0], p[1]))

    def locate(self, place: str) -> tuple[float, float] | None:
        """Coordinates for a place text like "Musterstadt", "34117 Kassel" or "Musterstadt am See"."""
        for code in _POSTCODE.findall(place):
            if code in self.by_postcode:
                return self._nearest(self.by_postcode[code])
        name = _key(_POSTCODE.sub(" ", place))
        if not name:
            return None
        if name in self.by_name:
            return self._nearest(self.by_name[name])
        # "Musterstadt" for "Musterstadt am See" or "Musterstadt (Elbe)"
        prefixed = [p for key, points in self.by_name.items()
                    if key.startswith(name + " ") or key.startswith(name + "(") for p in points]
        return self._nearest(prefixed)
