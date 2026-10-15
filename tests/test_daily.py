from datetime import date

from jobsearch.companies import TargetCompany, normalize_company
from jobsearch.daily import remote_window_days, run_daily
from jobsearch.eligibility import Rules
from jobsearch.sources import Location, Posting

RULES = Rules(30, 70, ("Werkstudent",))
TARGETS = [TargetCompany("Example Tech AG", (normalize_company("Example Tech"),), "Kassel", "", "", "")]
KASSEL = Location("34117 Kassel", 51.315, 9.49)
GOETTINGEN = Location("37073 Göttingen", 51.5413, 9.9158)
HAMBURG = Location("20095 Hamburg", 53.5503, 10.0006)


def posting(n, title, company="Muster Industrie GmbH", loc=KASSEL, homeoffice=False):
    return Posting("bundesagentur", f"ID-{n}", title, company, url=f"https://x/{n}",
                   locations=[loc], homeoffice_possible=homeoffice)


class FakeClient:
    def __init__(self, radius, nationwide, descriptions=None):
        self.radius, self.nationwide = radius, nationwide
        self.descriptions = descriptions or {}
        self.windows = []
        self.description_calls = []

    def search(self, term, *, location=None, radius_km=None, published_within_days=None):
        if location:
            return iter(self.radius)
        self.windows.append(published_within_days)
        return iter(self.nationwide)

    def description(self, refnr):
        self.description_calls.append(refnr)
        return self.descriptions.get(refnr, f"Beschreibung {refnr}")


def jobs(conn):
    return {r["title"]: dict(r) for r in conn.execute("SELECT * FROM jobs")}


def test_daily_run_stores_and_classifies(conn, cfg):
    client = FakeClient(
        radius=[
            posting(1, "Projektingenieur"),
            posting(2, "Werkstudent Datenanalyse"),
            posting(3, "Entwicklungsingenieur", company="Example Tech AG", loc=GOETTINGEN),
            posting(4, "Konstrukteur", loc=GOETTINGEN),
        ],
        nationwide=[
            posting(5, "Softwareentwickler remote", loc=HAMBURG, homeoffice=True),
            posting(6, "Softwareentwickler vor Ort", loc=HAMBURG, homeoffice=False),
            posting(7, "Softwareentwickler hybrid", loc=HAMBURG, homeoffice=True),
        ],
        descriptions={"ID-5": "Sie arbeiten 100% remote.", "ID-7": "2 Tage Homeoffice pro Woche."},
    )
    counts = run_daily(conn, cfg, client, TARGETS, RULES, ["Ingenieur"], today=date(2026, 10, 15))

    stored = jobs(conn)
    assert stored["Projektingenieur"]["eligibility"] == "eligible"
    assert stored["Werkstudent Datenanalyse"]["eligibility"] == "ineligible"
    assert stored["Entwicklungsingenieur"]["eligibility"] == "eligible"  # Target company, about 40 km
    assert stored["Entwicklungsingenieur"]["is_target_company"] == 1
    assert stored["Konstrukteur"]["eligibility"] == "ineligible"
    assert stored["Softwareentwickler remote"]["eligibility"] == "unclear"  # the AI confirms later
    assert stored["Softwareentwickler hybrid"]["eligibility"] == "ineligible"
    assert "Softwareentwickler vor Ort" not in stored  # nationwide without home office is dropped
    assert counts.new_jobs == 6
    assert counts.remote_dropped == 1
    # Descriptions only for Jobs in reach
    assert sorted(client.description_calls) == ["ID-1", "ID-3", "ID-5", "ID-7"]


def test_second_run_same_day_adds_nothing(conn, cfg):
    client = FakeClient([posting(1, "Projektingenieur")], [])
    run_daily(conn, cfg, client, TARGETS, RULES, ["Ingenieur"], today=date(2026, 10, 15))
    counts = run_daily(conn, cfg, client, TARGETS, RULES, ["Ingenieur"], today=date(2026, 10, 15))
    assert counts.new_jobs == 0
    assert conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1
    assert client.description_calls == ["ID-1"]


def test_seen_again_updates_last_seen(conn, cfg):
    client = FakeClient([posting(1, "Projektingenieur")], [])
    run_daily(conn, cfg, client, TARGETS, RULES, ["Ingenieur"], today=date(2026, 10, 15))
    run_daily(conn, cfg, client, TARGETS, RULES, ["Ingenieur"], today=date(2026, 10, 18))
    job = jobs(conn)["Projektingenieur"]
    assert (job["first_seen"], job["last_seen"]) == ("2026-10-15", "2026-10-18")


def test_failed_description_is_retried_next_run(conn, cfg):
    client = FakeClient([posting(1, "Projektingenieur")], [], descriptions={"ID-1": None})
    counts = run_daily(conn, cfg, client, TARGETS, RULES, ["Ingenieur"], today=date(2026, 10, 15))
    assert counts.description_failures == 1
    client.descriptions = {}
    counts = run_daily(conn, cfg, client, TARGETS, RULES, ["Ingenieur"], today=date(2026, 10, 16))
    assert counts.descriptions_fetched == 1


def test_remote_window_catches_up_missed_days(conn, cfg):
    assert remote_window_days(conn, date(2026, 10, 15), 14) == 14  # first run ever
    conn.execute("INSERT INTO runs (kind, started_at, ok) VALUES ('daily', '2026-10-11T08:00:00', 1)")
    conn.execute("INSERT INTO runs (kind, started_at, ok) VALUES ('daily', '2026-10-14T08:00:00', 0)")
    assert remote_window_days(conn, date(2026, 10, 15), 14) == 5  # failed run does not count
    assert remote_window_days(conn, date(2026, 10, 11), 14) == 1
    assert remote_window_days(conn, date(2026, 12, 1), 14) == 14
