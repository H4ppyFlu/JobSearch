import pytest

from jobsearch.sources.bundesagentur import BundesagenturClient, SchemaChanged, parse_description, to_posting

from .conftest import fixture_json, fixture_text


class FakeResponse:
    def __init__(self, payload=None, text=""):
        self.payload, self.text, self.encoding = payload, text, None

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class FakeSession:
    """Answers search requests page by page and detail requests with HTML."""

    def __init__(self, pages=(), detail_html=""):
        self.headers = {}
        self.pages = list(pages)
        self.detail_html = detail_html
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        if "jobdetail" in url:
            return FakeResponse(text=self.detail_html)
        page = params["page"]
        return FakeResponse(self.pages[page - 1] if page <= len(self.pages) else {"maxErgebnisse": 0})


def test_to_posting_maps_fields():
    p = to_posting(fixture_json("ba_search_page1.json")["ergebnisliste"][0])
    assert p.source_id == "10000-0000000001-S"
    assert p.company == "Muster Industrie GmbH"
    assert p.location_raw == "34117 Kassel"
    assert p.locations[0].lat == pytest.approx(51.315)
    assert p.url.endswith("/jobdetail/10000-0000000001-S")
    assert p.homeoffice_possible is False


def test_to_posting_falls_back_to_occupation_without_title():
    p = to_posting({"referenznummer": "R-1", "hauptberuf": "Elektroniker/in", "firma": "X GmbH"})
    assert p.title == "Elektroniker/in"


def test_to_posting_rejects_unknown_shape():
    with pytest.raises(SchemaChanged):
        to_posting({"titel": "x"})


def test_search_follows_pages_until_total_reached():
    session = FakeSession([fixture_json("ba_search_page1.json"), fixture_json("ba_search_page2.json")])
    client = BundesagenturClient(session, delay=0)
    ids = [p.source_id for p in client.search("Ingenieur", location="Kassel", radius_km=70)]
    assert ids == ["10000-0000000001-S", "10000-0000000002-S", "10000-0000000003-S"]
    assert len(session.calls) == 2
    assert session.calls[0][1]["umkreis"] == 70


def test_search_with_no_results():
    client = BundesagenturClient(FakeSession([{"maxErgebnisse": 0}]), delay=0)
    assert list(client.search("Nichts")) == []


def test_parse_description_from_ng_state():
    assert parse_description(fixture_text("ba_detail.html")).startswith("# Ihre Aufgaben")


def test_parse_description_without_state():
    assert parse_description("<html></html>") is None
