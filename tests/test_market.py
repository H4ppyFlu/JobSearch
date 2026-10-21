import json
from datetime import date

from jobsearch import db
from jobsearch.cli import cmd_market
from jobsearch.market import active_companies

from .conftest import add_job

TODAY = date(2026, 10, 21)


def test_a_company_with_three_jobs_in_reach_is_active(conn):
    for title in ("Planer", "Disponent", "Stelle X"):
        add_job(conn, title, company="Beispiel Technik GmbH", first_seen="2026-10-01")
    add_job(conn, "Planer", company="Muster GmbH", first_seen="2026-10-01")
    add_job(conn, "Disponent", company="Muster GmbH", first_seen="2026-10-01")

    found = active_companies(conn, TODAY)

    assert [(c.name, c.jobs) for c in found] == [("Beispiel Technik GmbH", 3)]


def test_old_generic_and_ineligible_jobs_do_not_count(conn):
    company = "Beispiel Technik GmbH"
    add_job(conn, "Planer", company=company, first_seen="2026-07-23")  # day 91 before today: outside
    add_job(conn, "Disponent", company=company, first_seen="2026-07-24")  # day 90: inside
    add_job(conn, "Stelle X", company=company, first_seen="2026-10-01", eligibility="ineligible")
    add_job(conn, "Techniker", company=company, first_seen="2026-10-01", eligibility="unclear")
    generic = add_job(conn, "Viele Stellen", company=company, first_seen="2026-10-01")
    conn.execute("UPDATE jobs SET is_generic = 1 WHERE id = ?", (generic,))
    add_job(conn, "Werkplaner", company=company, first_seen="2026-10-02")

    assert active_companies(conn, TODAY) == []

    add_job(conn, "Stelle Y", company="Beispiel Technik AG", first_seen="2026-10-03")  # same company
    assert [c.jobs for c in active_companies(conn, TODAY)] == [3]


def add_score(conn, job_id, tier, scored_at="2026-10-02T08:00:00", missing=(), matching=(), low_confidence=False):
    with conn:
        conn.execute(
            """INSERT INTO fit_scores (job_id, tier, reasoning, matching_skills, missing_skills, low_confidence,
                                       profile_hash, model, scored_at) VALUES (?, ?, 'Passt.', ?, ?, ?, 'ppp', 'claude-test', ?)""",
            (job_id, tier, json.dumps(list(matching)), json.dumps(list(missing)), int(low_confidence), scored_at))


def test_a_single_reopening_makes_a_company_active(conn):
    earlier = add_job(conn, "Planer", company="Muster GmbH", first_seen="2026-05-01")
    again = add_job(conn, "Planer", company="muster gmbh", first_seen="2026-10-01")
    conn.execute("UPDATE jobs SET reopening_of = ? WHERE id = ?", (earlier, again))
    add_job(conn, "Disponent", company="Muster GmbH", first_seen="2026-10-02")

    [found] = active_companies(conn, TODAY)

    assert (found.name, found.jobs, found.reopenings) == ("Muster GmbH", 2, 1)


def test_best_tier_counts_the_newest_score_of_each_job(conn):
    jobs = [add_job(conn, t, company="Beispiel Technik GmbH", first_seen="2026-10-01") for t in ("A", "B", "C")]
    add_score(conn, jobs[0], 3, scored_at="2026-10-01T08:00:00")
    add_score(conn, jobs[0], 1, scored_at="2026-10-05T08:00:00")  # rescored: now ★
    add_score(conn, jobs[1], 2)

    [found] = active_companies(conn, TODAY)

    assert found.best_tier == 2


def test_only_companies_off_the_target_list_get_the_note(conn):
    for company in ("Beispiel Technik GmbH", "Muster GmbH"):
        for title in ("A", "B", "C"):
            add_job(conn, title, company=company, first_seen="2026-10-01")
    conn.execute("UPDATE jobs SET is_target_company = 1 WHERE company = 'Muster GmbH' AND title = 'A'")

    notes = {c.name: (c.is_target, c.note) for c in active_companies(conn, TODAY)}

    assert notes == {"Beispiel Technik GmbH": (False, "Kandidat für companies.csv"), "Muster GmbH": (True, "")}


def test_market_command_prints_the_companies_in_german(cfg, capsys):
    conn = db.connect(cfg.db_path)
    jobs = [add_job(conn, t, company="Beispiel Technik GmbH", first_seen="2026-10-01") for t in ("A", "B", "C")]
    add_score(conn, jobs[0], 3)

    assert cmd_market(cfg, today=TODAY) == 0

    out = capsys.readouterr().out
    assert "Aktive Firmen" in out
    assert "Beispiel Technik GmbH" in out and "★★★" in out and "Kandidat für companies.csv" in out


def test_market_command_without_active_companies_says_so(cfg, capsys):
    assert cmd_market(cfg, today=TODAY) == 0
    assert "Keine aktiven Firmen" in capsys.readouterr().out
