import io
import zipfile

import pytest

from jobsearch import daily
from jobsearch.geo import Gazetteer, download
from jobsearch.sources import Location, Posting

HOME = (51.3127, 9.4797)  # Kassel

# GeoNames columns: country, postcode, place, admin1, code1, admin2, code2, admin3, code3, lat, lon, accuracy
ROWS = {
    "DE": [
        "DE\t34117\tKassel\tHessen\tHE\t\t\tKassel\t06611\t51.3166\t9.4912\t4",
        "DE\t34119\tKassel\tHessen\tHE\t\t\tKassel\t06611\t51.3152\t9.4647\t4",
        "DE\t37073\tGöttingen\tNiedersachsen\tNI\t\t\tGöttingen\t03159\t51.5413\t9.9158\t4",
        "DE\t11111\tMusterstadt am See\tHessen\tHE\t\t\tNord\t06001\t51.40\t9.60\t4",
        "DE\t99999\tMusterstadt (Fern)\tBayern\tBY\t\t\tSüd\t09001\t48.10\t11.50\t4",
        "DE\t22222\tNeustadt\tHessen\tHE\t\t\tNord\t06001\t51.20\t9.30\t4",
        "DE\t33333\tNeustadt\tBayern\tBY\t\t\tSüd\t09001\t49.70\t12.10\t4",
    ],
    "CH": ["CH\t4001\tBeispielort\tBasel-Stadt\tBS\t\t\t\t\t47.5584\t7.5733\t4"],
}


class FakeSession:
    def get(self, url, timeout=None):
        country = url.rsplit("/", 1)[1].split(".")[0]
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr(f"{country}.txt", "\n".join(ROWS[country]))
        return type("Response", (), {"content": buffer.getvalue(), "raise_for_status": lambda self: None})()


@pytest.fixture
def gazetteer(tmp_path):
    path = tmp_path / "geo" / "places.tsv"
    assert download(path, FakeSession()) == 7  # Kassel's two postcodes form one place
    return Gazetteer.load(path, HOME)


def test_exact_name(gazetteer):
    lat, lon = gazetteer.locate("Göttingen")
    assert (lat, lon) == pytest.approx((51.5413, 9.9158))


def test_postcodes_of_one_place_are_averaged(gazetteer):
    assert gazetteer.locate("Kassel") == pytest.approx((51.3159, 9.4780), abs=1e-3)


def test_postcode_wins_over_name(gazetteer):
    assert gazetteer.locate("37073 Irgendwo") == pytest.approx((51.5413, 9.9158))


def test_short_name_resolves_to_nearest_longer_name(gazetteer):
    assert gazetteer.locate("Musterstadt") == pytest.approx((51.40, 9.60))


def test_ambiguous_name_resolves_to_nearest(gazetteer):
    assert gazetteer.locate("Neustadt") == pytest.approx((51.20, 9.30))


def test_switzerland_is_included(gazetteer):
    assert gazetteer.locate("Beispielort") is not None


def test_unknown_place(gazetteer):
    assert gazetteer.locate("Nirgendwo") is None
    assert gazetteer.locate("") is None


def test_missing_file_means_no_gazetteer(tmp_path):
    assert Gazetteer.load(tmp_path / "missing.tsv", HOME) is None


def test_daily_locates_place_names(gazetteer):
    counts = daily.Counts()
    posting = Posting("indeed", "jk-1", "Projektingenieur", "Muster GmbH",
                      locations=[Location("Göttingen"), Location("Nirgendwo")])
    daily.locate(posting, gazetteer, counts)
    assert posting.locations[0].lat == pytest.approx(51.5413)
    assert posting.locations[1].lat is None and counts.places_unknown == 1
