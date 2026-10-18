import json
from datetime import date

from jobsearch import ai
from jobsearch.career import FetchError, page_text, run_career_scan, scan_due
from jobsearch.companies import TargetCompany, normalize_company
from jobsearch.eligibility import Rules

from .conftest import FIXTURES

PAGE = """<!doctype html><html><head><title>Karriere</title>
<style>.x { color: red }</style><script>var tracking = "abc";</script></head>
<body>
<nav><a href="/">Start</a></nav>
<h1>Offene Stellen</h1>
<ul>
  <li><a href="/jobs/123?src=web&amp;session=s1">Projektingenieur (m/w/d)</a> <span>Musterstadt</span></li>
  <li><a href="https://jobs.example.org/456">Konstrukteur   Maschinenbau</a><span>Beispielstadt</span></li>
</ul>
<noscript>Bitte JavaScript aktivieren</noscript>
</body></html>"""


def test_visible_text_keeps_job_links():
    page = page_text(PAGE, "https://muster.example/karriere/")
    assert "Offene Stellen" in page.text
    assert "Projektingenieur (m/w/d) <https://muster.example/jobs/123?src=web&session=s1>" in page.text
    assert "Konstrukteur Maschinenbau <https://jobs.example.org/456>" in page.text
    assert "tracking" not in page.text and "color" not in page.text and "JavaScript" not in page.text


def test_hash_ignores_changing_links_but_not_changing_jobs():
    base = page_text(PAGE, "https://muster.example/karriere/").hash
    assert page_text(PAGE.replace("session=s1", "session=s2"), "https://muster.example/karriere/").hash == base
    assert page_text(PAGE.replace("Konstrukteur", "Elektroniker"), "https://muster.example/karriere/").hash != base

RULES = Rules(30, 70, ("Werkstudent",))
PLACES = {"Musterstadt": (51.32, 9.49), "Beispielstadt": (51.54, 9.92)}


def company(name, url, ats="other", render="unknown"):
    return TargetCompany(name, (normalize_company(name),), "Musterstadt", url, ats, render)


class FakeGazetteer:
    def locate(self, place):
        return PLACES.get(place)


class FakeFetch:
    """`rendered` holds what a browser would show; without it, browser mode fails like a missing Playwright."""

    def __init__(self, pages, rendered=None):
        self.pages, self.rendered = pages, rendered
        self.calls = []

    def __call__(self, url, render_mode="static"):
        self.calls.append(url)
        if render_mode == "browser":
            if self.rendered is None:
                raise FetchError("braucht einen Browser, Playwright ist nicht installiert")
            return self.rendered[url]
        page = self.pages.get(url)
        if page is None or isinstance(page, Exception):
            raise page or FetchError("HTTP 404")
        return page


class ExtractAi:
    """Returns the given postings per company found in the prompt."""

    def __init__(self, postings, fail=None):
        self.postings, self.fail = postings, fail
        self.calls = 0

    def __call__(self, prompt, *, model, schema, system, check=None, tools=()):
        self.calls += 1
        if self.fail:
            raise ai.AiError(self.fail)
        pages = json.loads(prompt.split("# Pages\n\n", 1)[1])
        data = {"pages": [{"page_id": p["page_id"], "postings": self.postings.get(p["company"], [])}
                          for p in pages]}
        check(data)
        return ai.Answer(data, "claude-haiku-test")


def scan(conn, cfg, companies, fetch, ask, day=date(2026, 10, 15)):
    return run_career_scan(conn, cfg, companies, RULES, fetch=fetch, ask=ask, gazetteer=FakeGazetteer(), today=day)


DETAIL = "<h1>Projektingenieur</h1><p>Aufgaben: Anlagen planen und in Betrieb nehmen. " + "Mehr Text. " * 40 + "</p>"
JOB = {"title": "Projektingenieur (m/w/d)", "location": "Musterstadt", "url": "https://muster.example/jobs/1"}
TARGETS = [company("Muster Industrie GmbH", "https://muster.example/karriere")]


def test_changed_page_is_extracted_into_jobs(conn, cfg):
    fetch = FakeFetch({"https://muster.example/karriere": PAGE, "https://muster.example/jobs/1": DETAIL})
    counts = scan(conn, cfg, TARGETS, fetch, ExtractAi({"Muster Industrie GmbH": [JOB]}))
    assert (counts.extracted, counts.new_jobs) == (1, 1)
    job = conn.execute("SELECT * FROM jobs").fetchone()
    assert (job["title"], job["eligibility"], job["is_target_company"]) == ("Projektingenieur (m/w/d)", "eligible", 1)
    posting = conn.execute("SELECT * FROM postings").fetchone()
    assert posting["source"] == "career_page" and posting["url"] == "https://muster.example/jobs/1"
    assert "Aufgaben: Anlagen planen" in posting["description"]  # fetched from the detail page


def test_unchanged_page_needs_no_ai_and_keeps_jobs_fresh(conn, cfg):
    fetch = FakeFetch({"https://muster.example/karriere": PAGE, "https://muster.example/jobs/1": "<p>Text</p>"})
    scan(conn, cfg, TARGETS, fetch, ExtractAi({"Muster Industrie GmbH": [JOB]}))
    again = ExtractAi({})
    counts = scan(conn, cfg, TARGETS, fetch, again, day=date(2026, 10, 22))
    assert again.calls == 0 and counts.unchanged == 1
    assert conn.execute("SELECT last_seen FROM jobs").fetchone()[0] == "2026-10-22"
    assert conn.execute("SELECT last_seen FROM postings").fetchone()[0] == "2026-10-22"


def test_failing_page_is_reported_after_two_scans(conn, cfg):
    fetch = FakeFetch({})
    assert scan(conn, cfg, TARGETS, fetch, ExtractAi({})).warnings == []
    warnings = scan(conn, cfg, TARGETS, fetch, ExtractAi({}), day=date(2026, 10, 22)).warnings
    assert "Muster Industrie GmbH" in warnings[0] and "HTTP 404" in warnings[0]


def test_page_without_jobs_is_reported_after_two_scans(conn, cfg):
    first = FakeFetch({"https://muster.example/karriere": PAGE})
    assert scan(conn, cfg, TARGETS, first, ExtractAi({})).warnings == []
    second = FakeFetch({"https://muster.example/karriere": PAGE.replace("Offene", "Aktuelle")})
    warnings = scan(conn, cfg, TARGETS, second, ExtractAi({}), day=date(2026, 10, 22)).warnings
    assert "keine Stellen" in warnings[0]


def test_ai_failure_retries_the_page_next_time(conn, cfg):
    fetch = FakeFetch({"https://muster.example/karriere": PAGE, "https://muster.example/jobs/1": "<p>Text</p>"})
    counts = scan(conn, cfg, TARGETS, fetch, ExtractAi({}, fail="Claude-Kontingent erschöpft"))
    assert "Kontingent" in counts.error
    retry = ExtractAi({"Muster Industrie GmbH": [JOB]})
    assert scan(conn, cfg, TARGETS, fetch, retry, day=date(2026, 10, 16)).new_jobs == 1
    assert retry.calls == 1


def test_companies_without_career_page_are_skipped(conn, cfg):
    fetch = FakeFetch({})
    counts = scan(conn, cfg, [company("Example Tech AG", "")], fetch, ExtractAi({}))
    assert counts.pages == 0 and fetch.calls == []


def test_scan_is_due_weekly(conn):
    assert scan_due(conn, date(2026, 10, 15), 7)  # never ran
    with conn:
        conn.execute("INSERT INTO runs (kind, started_at, ok) VALUES ('career_scan', '2026-10-15T08:00:00', 1)")
        conn.execute("INSERT INTO runs (kind, started_at, ok) VALUES ('career_scan', '2026-10-20T08:00:00', 0)")
    assert not scan_due(conn, date(2026, 10, 21), 7)  # a failed run does not count, the ok one is recent
    assert scan_due(conn, date(2026, 10, 22), 7)


def test_personio_feed_needs_no_ai(conn, cfg):
    targets = [company("Muster Industrie GmbH", "https://muster.jobs.personio.de/", ats="personio")]
    fetch = FakeFetch({"https://muster.jobs.personio.de/xml": (FIXTURES / "personio.xml").read_text(encoding="utf-8")})
    no_ai = ExtractAi({})
    counts = scan(conn, cfg, targets, fetch, no_ai)
    assert no_ai.calls == 0 and (counts.feeds, counts.new_jobs) == (1, 2)
    posting = conn.execute("SELECT * FROM postings WHERE title LIKE 'Projekt%'").fetchone()
    assert posting["url"] == "https://muster.jobs.personio.de/job/1001"
    assert posting["location_raw"] == "Musterstadt; Beispielstadt"
    assert "Deine Aufgaben" in posting["description"] and "Anlagen planen" in posting["description"]
    assert posting["is_snippet"] == 0
    remote = conn.execute("SELECT j.eligibility FROM jobs j WHERE j.title LIKE 'Software%'").fetchone()
    assert remote["eligibility"] == "unclear"  # remote place, waits for the full-remote check


SHELL = "<html><body><div id='app'></div><a href='/about'>Über uns</a> Karriere bei uns</body></html>"


def test_without_a_browser_the_static_text_is_read_once(conn, cfg):
    """English job lists have no (m/w/d). Without Playwright the static text is the best there is."""
    english = PAGE.replace(" (m/w/d)", "")
    fetch = FakeFetch({"https://muster.example/karriere": english, "https://muster.example/jobs/1": "<p>Text</p>"})
    first = ExtractAi({"Muster Industrie GmbH": [JOB]})
    assert scan(conn, cfg, TARGETS, fetch, first).new_jobs == 1 and first.calls == 1
    again = ExtractAi({})
    scan(conn, cfg, TARGETS, fetch, again, day=date(2026, 10, 22))
    assert again.calls == 0  # unchanged


def test_page_without_job_titles_is_rendered_in_a_browser(conn, cfg):
    fetch = FakeFetch({"https://muster.example/karriere": SHELL, "https://muster.example/jobs/1": "<p>Text</p>"},
                      rendered={"https://muster.example/karriere": PAGE})
    counts = scan(conn, cfg, TARGETS, fetch, ExtractAi({"Muster Industrie GmbH": [JOB]}))
    assert (counts.rendered, counts.extracted, counts.new_jobs) == (1, 1, 1)


def test_a_new_url_starts_counting_again(conn, cfg):
    fetch = FakeFetch({})
    scan(conn, cfg, TARGETS, fetch, ExtractAi({}))
    scan(conn, cfg, TARGETS, fetch, ExtractAi({}), day=date(2026, 10, 22))
    fixed = [company("Muster Industrie GmbH", "https://muster.example/stellen")]
    assert scan(conn, cfg, fixed, fetch, ExtractAi({}), day=date(2026, 10, 29)).warnings == []


def test_job_without_place_gets_the_company_locations(conn, cfg):
    no_place = dict(JOB, location="")
    fetch = FakeFetch({"https://muster.example/karriere": PAGE, "https://muster.example/jobs/1": "<p>Text</p>"})
    scan(conn, cfg, TARGETS, fetch, ExtractAi({"Muster Industrie GmbH": [no_place]}))
    job = conn.execute("SELECT eligibility, location FROM jobs").fetchone()
    assert tuple(job) == ("eligible", "Musterstadt")


def test_a_failed_scan_in_between_does_not_stop_the_refresh(conn, cfg):
    pages = {"https://muster.example/karriere": PAGE, "https://muster.example/jobs/1": "<p>Text</p>"}
    scan(conn, cfg, TARGETS, FakeFetch(pages), ExtractAi({"Muster Industrie GmbH": [JOB]}))
    scan(conn, cfg, TARGETS, FakeFetch({}), ExtractAi({}), day=date(2026, 10, 22))  # page down
    scan(conn, cfg, TARGETS, FakeFetch(pages), ExtractAi({}), day=date(2026, 10, 29))  # back, unchanged
    assert conn.execute("SELECT last_seen FROM jobs").fetchone()[0] == "2026-10-29"


def test_tracking_parameters_do_not_make_a_new_posting(conn, cfg):
    pages = {"https://muster.example/karriere": PAGE, "https://muster.example/jobs/1": "<p>Text</p>"}
    tracked = dict(JOB, url="https://muster.example/jobs/1?utm_source=web&sid=abc")
    scan(conn, cfg, TARGETS, FakeFetch(pages), ExtractAi({"Muster Industrie GmbH": [tracked]}))
    changed = dict(pages, **{"https://muster.example/karriere": PAGE.replace("Offene", "Aktuelle")})
    retracked = dict(JOB, url="https://muster.example/jobs/1?utm_source=mail&sid=xyz")
    counts = scan(conn, cfg, TARGETS, FakeFetch(changed), ExtractAi({"Muster Industrie GmbH": [retracked]}),
                  day=date(2026, 10, 22))
    assert counts.new_jobs == 0
    assert conn.execute("SELECT count(*) FROM postings").fetchone()[0] == 1


def test_a_job_page_without_real_text_leaves_the_snippet(conn, cfg):
    fetch = FakeFetch({"https://muster.example/karriere": PAGE, "https://muster.example/jobs/1": "<div id=app></div>"})
    scan(conn, cfg, TARGETS, fetch, ExtractAi({"Muster Industrie GmbH": [JOB]}))
    row = conn.execute("SELECT description, is_snippet FROM postings").fetchone()
    assert tuple(row) == (None, 1)
