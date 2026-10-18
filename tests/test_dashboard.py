import json
import re
from datetime import datetime, timedelta

import pytest

from jobsearch import db
from jobsearch.dashboard import create_app
from jobsearch.scoring import build_prompt, recent_notes

from .conftest import add_job


@pytest.fixture
def conn(cfg):
    connection = db.connect(cfg.db_path)
    yield connection
    connection.close()


@pytest.fixture
def client(cfg, conn):
    app = create_app(cfg)
    app.testing = True
    return app.test_client()


def score(conn, job_id, tier, low=False, reasoning="Passt gut."):
    with conn:
        conn.execute(
            """INSERT INTO fit_scores (job_id, tier, reasoning, matching_skills, missing_skills, low_confidence,
                                       profile_hash, model, scored_at)
               VALUES (?, ?, ?, ?, ?, ?, 'h', 'claude-test', '2026-10-15T08:00:00')""",
            (job_id, tier, reasoning, json.dumps(["Projektplanung"]), json.dumps(["SAP"]), int(low)),
        )


def page(client, url="/"):
    response = client.get(url)
    assert response.status_code == 200
    return response.get_data(as_text=True)


def test_list_shows_jobs_in_reach_best_first(client, conn):
    score(conn, add_job(conn, "Projektingenieur"), 2)
    score(conn, add_job(conn, "Entwicklungsingenieur"), 3)
    add_job(conn, "Werkstudent Datenanalyse", eligibility="ineligible", reason="Ausschlusswort")
    html = page(client)
    assert html.index("Entwicklungsingenieur") < html.index("Projektingenieur")
    assert "★★★" in html
    assert "Werkstudent" not in html


@pytest.fixture
def mixed(conn):
    """One distinct Job per filter value."""
    score(conn, add_job(conn, "Alpha Sehr passend"), 3)
    score(conn, add_job(conn, "Beta Passend", source="indeed"), 2)
    add_job(conn, "Gamma Unbewertet", first_seen="2026-10-17")
    add_job(conn, "Delta Ausserhalb", eligibility="ineligible", reason="zu weit")
    with conn:
        conn.execute("UPDATE jobs SET status = 'applied' WHERE title = 'Beta Passend'")


@pytest.mark.parametrize("query, shown", [
    ("tier=3", {"Alpha"}),
    ("tier=none", {"Gamma"}),
    ("status=applied", {"Beta"}),
    ("eligibility=ineligible", {"Delta"}),
    ("eligibility=all", {"Alpha", "Beta", "Gamma", "Delta"}),
    ("source=indeed", {"Beta"}),
    ("since=2026-10-16", {"Gamma"}),
])
def test_filters(client, mixed, query, shown):
    html = page(client, f"/?{query}")
    assert {name for name in ("Alpha", "Beta", "Gamma", "Delta") if f"{name} " in html} == shown


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 15, 8, 0)

    def __call__(self):
        return self.now


def test_new_since_last_visit(cfg, conn):
    clock = Clock()
    client = create_app(cfg, now=clock).test_client()
    add_job(conn, "Alter Job")
    page(client)  # first visit
    add_job(conn, "Frischer Job")
    clock.now += timedelta(hours=5)
    html = page(client)
    assert 'Frischer Job</a> <span class="badge new">neu</span>' in html
    assert 'Alter Job</a> <span class="badge new">' not in html
    clock.now += timedelta(minutes=10)  # changing a filter right after keeps the markers
    assert 'Frischer Job</a> <span class="badge new">neu</span>' in page(client)
    clock.now += timedelta(hours=5)
    assert 'badge new' not in page(client)


def test_badges(client, conn):
    score(conn, add_job(conn, "Projektingenieur"), 2, low=True)
    generic = add_job(conn, "Ingenieure (m/w/d) gesucht", company="Example Tech AG")
    with conn:
        conn.execute("UPDATE jobs SET is_generic = 1, is_target_company = 1 WHERE id = ?", (generic,))
    html = page(client)
    assert "geringe Sicherheit" in html
    assert "Sammelanzeige" in html
    assert "Zielunternehmen" in html


def test_detail_shows_postings_score_and_reopening_chain(client, conn):
    earlier = add_job(conn, "Projektingenieur", first_seen="2026-08-01", description="Alte Ausschreibung.")
    job = add_job(conn, "Projektingenieur", description="Aufgaben: Anlagen planen und in Betrieb nehmen.")
    score(conn, job, 3, reasoning="Deine Erfahrung in der Anlagenplanung passt genau.")
    with conn:
        conn.execute("UPDATE jobs SET reopening_of = ? WHERE id = ?", (earlier, job))
        conn.execute(
            """INSERT INTO postings (job_id, source, source_id, url, title, company, description, is_snippet,
                                     first_seen, last_seen)
               VALUES (?, 'indeed', 'jk-1', 'https://indeed.example/viewjob?jk=1', 'Projektingenieur',
                       'Muster GmbH', 'Kurz.', 1, '2026-10-15', '2026-10-15')""",
            (job,),
        )
    html = page(client, f"/job/{job}")
    assert "Anlagen planen und in Betrieb nehmen" in html  # the longest description
    assert "Deine Erfahrung in der Anlagenplanung passt genau." in html
    assert "Projektplanung" in html and "SAP" in html
    assert f'href="https://x/{job}"' in html and 'href="https://indeed.example/viewjob?jk=1"' in html
    assert "Bundesagentur" in html and "Indeed" in html
    assert f'href="/job/{earlier}"' in html  # Reopening of
    assert f'href="/job/{job}"' in page(client, f"/job/{earlier}")  # and back


def test_unknown_job_is_404(client):
    assert client.get("/job/999").status_code == 404


def test_list_links_to_detail(client, conn):
    job = add_job(conn, "Projektingenieur")
    assert f'href="/job/{job}"' in page(client)


def set_status(client, job_id, status, note=""):
    html = page(client, f"/job/{job_id}")
    token = re.search(r'name="token" value="([^"]+)"', html)[1]
    return client.post(f"/job/{job_id}/status", data={"status": status, "note": note, "token": token})


def test_status_with_note_reaches_the_next_scoring_prompt(cfg, conn):
    client = create_app(cfg, now=Clock()).test_client()
    job = add_job(conn, "Projektingenieur")
    response = set_status(client, job, "not_interesting", "Zu viel Reisetätigkeit.")
    assert response.status_code == 303
    html = page(client, f"/job/{job}")
    assert "Zu viel Reisetätigkeit." in html and "nicht interessant" in html
    prompt = build_prompt("Profil", recent_notes(conn, cfg.feedback_notes_in_prompt), [])
    assert "Projektingenieur (Muster GmbH), Status not_interesting: Zu viel Reisetätigkeit." in prompt


def test_applied_sets_the_date_once(cfg, conn):
    clock = Clock()
    client = create_app(cfg, now=clock).test_client()
    job = add_job(conn, "Projektingenieur")
    set_status(client, job, "applied")
    clock.now += timedelta(days=3)
    set_status(client, job, "applied")
    row = conn.execute("SELECT status, applied_on FROM jobs WHERE id = ?", (job,)).fetchone()
    assert tuple(row) == ("applied", "2026-10-15")
    assert conn.execute("SELECT count(*) FROM feedback_notes").fetchone()[0] == 0  # no note, no entry
    assert "Beworben am 2026-10-15" in page(client, f"/job/{job}")


def test_status_change_needs_the_page_token(client, conn):
    job = add_job(conn, "Projektingenieur")
    assert client.post(f"/job/{job}/status", data={"status": "applied"}).status_code == 400
    assert client.post(f"/job/{job}/status", data={"status": "erfunden", "token": "x"}).status_code == 400
    assert conn.execute("SELECT status FROM jobs").fetchone()[0] == "new"


def test_other_hosts_are_refused(client, conn):
    """A DNS-rebinding page reaches 127.0.0.1 under its own host name."""
    assert client.get("/", headers={"Host": "evil.example:8765"}).status_code == 400
    assert client.get("/", headers={"Host": "127.0.0.1:8765"}).status_code == 200


def test_reading_details_keeps_the_visit_going(cfg, conn):
    clock = Clock()
    client = create_app(cfg, now=clock).test_client()
    page(client)
    job = add_job(conn, "Frischer Job")
    clock.now += timedelta(hours=5)
    page(client)
    for _ in range(3):  # 90 minutes of reading details
        clock.now += timedelta(minutes=30)
        page(client, f"/job/{job}")
    assert 'Frischer Job</a> <span class="badge new">' in page(client)


def test_a_visit_ends_after_some_hours_even_with_steady_use(cfg, conn):
    clock = Clock()
    client = create_app(cfg, now=clock).test_client()
    add_job(conn, "Alter Job")
    page(client)
    for _ in range(20):
        clock.now += timedelta(minutes=50)
        html = page(client)
    assert "badge new" not in html


def test_odd_token_is_a_400_not_a_crash(client, conn):
    job = add_job(conn, "Projektingenieur")
    assert client.post(f"/job/{job}/status", data={"status": "applied", "token": "ä"}).status_code == 400


def test_a_note_without_a_new_status_keeps_the_change_date(cfg, conn):
    clock = Clock()
    client = create_app(cfg, now=clock).test_client()
    job = add_job(conn, "Projektingenieur")
    set_status(client, job, "interesting")
    clock.now += timedelta(days=2)
    set_status(client, job, "interesting", "Noch eine Notiz.")
    assert conn.execute("SELECT status_changed_at FROM jobs").fetchone()[0] == "2026-10-15T08:00:00"


def test_error_pages_are_german(client):
    assert "nicht gefunden" in client.get("/job/999").get_data(as_text=True)


def test_status_can_be_set_straight_from_the_list(client, conn):
    job = add_job(conn, "Werkstudent Vertrieb")
    html = page(client, "/?tier=none")
    token = re.search(r'name="token" value="([^"]+)"', html)[1]
    assert f'action="/job/{job}/status"' in html
    response = client.post(f"/job/{job}/status", data={"status": "not_interesting", "token": token,
                                                        "next": "/?tier=none"})
    assert response.status_code == 303 and response.headers["Location"] == "/?tier=none"
    assert conn.execute("SELECT status FROM jobs").fetchone()[0] == "not_interesting"


@pytest.mark.parametrize("target", ["https://evil.example/", "//evil.example/", "job/1"])
def test_redirect_after_status_stays_on_the_dashboard(client, conn, target):
    job = add_job(conn, "Projektingenieur")
    token = re.search(r'name="token" value="([^"]+)"', page(client))[1]
    response = client.post(f"/job/{job}/status", data={"status": "applied", "token": token, "next": target})
    assert response.headers["Location"] == f"/job/{job}"


def test_search_term_filter(client, conn):
    found_twice = add_job(conn, "Alpha Projektingenieur")
    found_once = add_job(conn, "Beta Simulationsingenieur")
    add_job(conn, "Gamma aus Alert-Mail", source="indeed")
    with conn:
        conn.executemany(
            "INSERT INTO posting_terms (posting_id, term) SELECT id, ? FROM postings WHERE job_id = ?",
            [("Projektingenieur", found_twice), ("Simulation", found_twice), ("Simulation", found_once)],
        )
    html = page(client)
    assert '<option value="Projektingenieur"' in html and '<option value="Simulation"' in html
    assert {n for n in ("Alpha", "Beta", "Gamma") if f"{n} " in page(client, "/?term=Simulation")} == {"Alpha", "Beta"}
    assert {n for n in ("Alpha", "Beta", "Gamma") if f"{n} " in page(client, "/?term=Projektingenieur")} == {"Alpha"}
    assert "Gefunden über: Projektingenieur, Simulation" in page(client, f"/job/{found_twice}")


def test_list_shows_the_search_terms_per_job(client, conn):
    job = add_job(conn, "Projektingenieur")
    with conn:
        conn.executemany("INSERT INTO posting_terms (posting_id, term) SELECT id, ? FROM postings WHERE job_id = ?",
                         [("Simulation", job), ("Projektingenieur", job)])
    html = page(client)
    assert "<th class=\"hide-small\">Suchbegriff</th>" in html
    assert '<td class="hide-small">Projektingenieur, Simulation</td>' in html
