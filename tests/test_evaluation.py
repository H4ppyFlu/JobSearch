import json
from collections import Counter
from datetime import date

import pytest

from jobsearch import db
from jobsearch.cli import cmd_eval
from jobsearch.eligibility import NOT_FULLY_REMOTE_REASON
from jobsearch.evaluation import (Period, changes, changes_report, describe, label, periods, reason_kind,
                                  score_report, score_tables, stage_metrics, stage_report, term_metrics)
from jobsearch.scoring import AI_NOT_REMOTE_REASON

from .conftest import add_job

BASE = {"model_scoring": "sonnet", "prompt_scoring": "aaa", "profile": "ppp",
        "search_terms": ["Planer", "Projektleiter"], "alert_mail": True, "radius_km": 30}


def add_run(conn, started_at, settings, kind="daily"):
    with conn:
        conn.execute("INSERT INTO runs (kind, started_at, settings_json) VALUES (?, ?, ?)",
                     (kind, started_at, json.dumps(settings) if settings is not None else None))


def test_describe_names_each_changed_setting():
    new = {**BASE, "search_terms": ["Planer", "Bauleiter"], "prompt_scoring": "bbb", "radius_km": 40,
           "alert_mail": False, "profile": None}
    assert describe(BASE, new) == [
        "Prompt Bewertung: geändert",
        "Profil: fehlt",
        "Suchbegriffe: + Bauleiter, − Projektleiter",
        "Alert-Mail: an → aus",
        "Radius (km): 30 → 40",
    ]


def test_a_setting_added_later_shows_up_as_new():
    assert describe(BASE, {**BASE, "reopening_days": 21}) == ["Reopening nach (Tagen): – → 21"]


def test_only_runs_with_different_settings_are_changes():
    snaps = [("2026-10-15T08:00:00", BASE), ("2026-10-16T08:00:00", BASE),
             ("2026-10-17T08:00:00", {**BASE, "model_scoring": "opus"}), ("2026-10-18T08:00:00", {**BASE, "model_scoring": "opus"})]
    found = changes(snaps)
    assert [(c.day, c.lines) for c in found] == [
        ("2026-10-15", ["Beginn der Aufzeichnung"]),
        ("2026-10-17", ["Modell Bewertung: sonnet → opus"]),
    ]


def test_report_skips_runs_without_snapshot_and_other_kinds(conn):
    add_run(conn, "2026-10-14T08:00:00", None)  # from before the snapshot existed
    add_run(conn, "2026-10-15T08:00:00", BASE)
    add_run(conn, "2026-10-15T09:00:00", None, kind="score")
    add_run(conn, "2026-10-16T08:00:00", {**BASE, "radius_km": 25})
    report = changes_report(conn)
    assert report[1] == "  2026-10-15  Beginn der Aufzeichnung"
    assert report[2] == "  2026-10-16  Radius (km): 30 → 25"
    assert len(report) == 3


def test_report_without_snapshots_says_when_recording_starts(conn):
    assert "Die Aufzeichnung beginnt mit dem nächsten" in changes_report(conn)[0]


def test_report_counts_fit_scores_from_before_prompt_versions(conn):
    with conn:
        job = conn.execute("""INSERT INTO jobs (company, title, eligibility, first_seen, last_seen)
                              VALUES ('Muster GmbH', 'Planer', 'eligible', '2026-10-15', '2026-10-15')""").lastrowid
        conn.execute("""INSERT INTO fit_scores (job_id, tier, reasoning, profile_hash, model, scored_at)
                        VALUES (?, 2, 'Passt.', 'ppp', 'claude-test', '2026-10-15T09:00:00')""", (job,))
    assert "Fit scores ohne Prompt-Version: 1." in changes_report(conn)[-1]


def test_migration_keeps_existing_rows(tmp_path):
    path = tmp_path / "old.sqlite"
    conn = db.connect(path, run_migrations=False)
    for version, sql in db._migrations():
        if version <= 5:
            conn.executescript(sql)
    conn.execute("PRAGMA user_version = 5")
    conn.execute("INSERT INTO runs (kind, started_at) VALUES ('daily', '2026-10-14T08:00:00')")
    conn.commit()
    conn.close()
    conn = db.connect(path)
    row = conn.execute("SELECT kind, settings_json FROM runs").fetchone()
    assert (row["kind"], row["settings_json"]) == ("daily", None)
    assert "prompt_hash" in {r["name"] for r in conn.execute("PRAGMA table_info(fit_scores)")}
    conn.close()


def score(conn, job_id, tier, low=0):
    with conn:
        conn.execute("""INSERT INTO fit_scores (job_id, tier, reasoning, low_confidence, profile_hash, model, scored_at)
                        VALUES (?, ?, 'Passt.', ?, 'ppp', 'claude-test', '2026-10-16T09:00:00')""", (job_id, tier, low))


def found_by(conn, job_id, *terms):
    posting = conn.execute("SELECT id FROM postings WHERE job_id = ?", (job_id,)).fetchone()[0]
    with conn:
        conn.executemany("INSERT INTO posting_terms (posting_id, term) VALUES (?, ?)", [(posting, t) for t in terms])


def test_periods_split_into_before_and_after():
    assert periods(None, None, None) == [Period("gesamt")]
    assert periods("2026-10-01", None, "2026-10-10") == [Period("vor 2026-10-10", "2026-10-01", "2026-10-10"),
                                                         Period("ab 2026-10-10", "2026-10-10", None)]
    with pytest.raises(ValueError):
        periods("2026-10-10", None, "2026-10-01")


@pytest.mark.parametrize("reason, kind", [
    ("Entfernung 55 km", "Entfernung"),
    ("Ausschlussliste: Praktikum", "Ausschlussliste"),
    (NOT_FULLY_REMOTE_REASON, "nicht voll remote (Regel)"),
    (AI_NOT_REMOTE_REASON, "nicht voll remote (KI)"),
    (None, "ohne Grund"),
])
def test_reason_kinds(reason, kind):
    assert reason_kind(reason) == kind


def test_stage_metrics_count_each_stage_within_the_period(conn):
    a = add_job(conn, "Planer", first_seen="2026-10-15")
    b = add_job(conn, "Bauleiter", first_seen="2026-10-15")
    add_job(conn, "Unscored", first_seen="2026-10-15")
    add_job(conn, "Weit weg", eligibility="ineligible", reason="Entfernung 80 km", first_seen="2026-10-15")
    add_job(conn, "Praktikum", eligibility="ineligible", reason="Ausschlussliste: Praktikum", first_seen="2026-10-15")
    add_job(conn, "Später", first_seen="2026-10-20", source="indeed")
    score(conn, a, 3)
    score(conn, b, 1, low=1)
    with conn:
        conn.execute("UPDATE jobs SET is_generic = 1 WHERE id = ?", (b,))
    add_run(conn, "2026-10-15T08:00:00", BASE)
    with conn:
        conn.execute("UPDATE runs SET ok = 1")
    m = stage_metrics(conn, Period("x", "2026-10-15", "2026-10-16"))
    assert m.daily_runs == 1
    assert m.postings == {"bundesagentur": 5}
    assert m.jobs == 5 and m.eligibility == {"eligible": 3, "ineligible": 2}
    assert m.reasons == {"Entfernung": 1, "Ausschlussliste": 1}
    assert m.tiers == {3: 1, 1: 1} and m.scored == 2
    assert (m.low_confidence, m.generic, m.unscored) == (1, 1, 1)


def test_term_metrics_show_unique_finds_tiers_positives_and_terms_without_finds(conn):
    a = add_job(conn, "Planer")
    b = add_job(conn, "Projektplaner")
    add_job(conn, "Ohne Begriff")
    found_by(conn, a, "Planer", "Projekt")
    found_by(conn, b, "Projekt")
    score(conn, a, 3)
    with conn:
        conn.execute("UPDATE jobs SET status = 'interesting' WHERE id = ?", (a,))
    add_run(conn, "2026-10-15T08:00:00", {**BASE, "search_terms": ["Planer", "Projekt", "Leerlauf"]})
    terms = {t.term: t for t in term_metrics(conn, Period("gesamt"))}
    assert (terms["Projekt"].jobs, terms["Projekt"].only_here) == (2, 1)
    assert (terms["Planer"].jobs, terms["Planer"].only_here, terms["Planer"].positive) == (1, 0, 1)
    assert terms["Planer"].tiers == {3: 1}
    assert terms["Leerlauf"].jobs == 0
    assert [t.term for t in term_metrics(conn, Period("gesamt"))][0] == "Projekt"  # most Jobs first


def test_stage_report_has_one_column_per_period(conn):
    add_job(conn, "Alt", first_seen="2026-10-10")
    add_job(conn, "Neu", first_seen="2026-10-20")
    report = stage_report(conn, periods(None, None, "2026-10-15"))
    assert report[1].split() == ["vor", "2026-10-15", "ab", "2026-10-15"]
    assert next(line for line in report if line.startswith("Neue Jobs")).split() == ["Neue", "Jobs", "1", "1"]


def test_eval_command_rejects_a_split_outside_the_period(cfg, capsys):
    assert cmd_eval(cfg, False, since=date(2026, 10, 15), split=date(2026, 10, 1)) == 2
    assert "liegt nicht im Zeitraum" in capsys.readouterr().err
    assert cmd_eval(cfg, False, until=date(2026, 10, 15)) == 0


def scored_job(conn, title, tier, status="new", prompt_hash=None, scored_at="2026-10-16T09:00:00",
               first_seen="2026-10-15"):
    job = add_job(conn, title, first_seen=first_seen)
    with conn:
        conn.execute("UPDATE jobs SET status = ? WHERE id = ?", (status, job))
        conn.execute("""INSERT INTO fit_scores (job_id, tier, reasoning, profile_hash, prompt_hash, model, scored_at)
                        VALUES (?, ?, 'Passt.', 'ppp', ?, 'claude-test', ?)""", (job, tier, prompt_hash, scored_at))
    return job


@pytest.mark.parametrize("status, expected", [
    ("interesting", "positive"), ("applied", "positive"), ("rejected", "positive"),
    ("not_interesting", "negative"), ("new", None),
])
def test_job_status_as_label(status, expected):
    assert label(status) == expected


def test_score_table_counts_tier_against_label(conn):
    scored_job(conn, "A", 3, "applied")
    scored_job(conn, "B", 3, "not_interesting")
    scored_job(conn, "C", 1, "interesting")
    scored_job(conn, "D", 1)
    assert score_tables(conn, Period("gesamt")) == {
        "alle": Counter({(3, "positive"): 1, (3, "negative"): 1, (1, "positive"): 1, (1, None): 1})}
    report = score_report(conn, [Period("gesamt")])
    assert "Treffsicherheit ★★★: 50 % (1 von 2)" in report
    assert "Übersehen in ★: 1 von 2 positiven" in report
    assert "Labels: 3 von 4 bewerteten Jobs" in report
    assert any(line.startswith("Wenige Labels") for line in report)


def test_scores_group_by_prompt_version_and_month(conn):
    scored_job(conn, "Alt", 2)
    scored_job(conn, "Neu", 2, prompt_hash="abcdef0123456789", scored_at="2026-11-02T09:00:00")
    assert set(score_tables(conn, Period("gesamt"), "prompt")) == {"Prompt vor der Aufzeichnung", "Prompt abcdef01"}
    assert set(score_tables(conn, Period("gesamt"), "month")) == {"2026-10", "2026-11"}


def test_score_report_without_scores_says_so(conn):
    assert score_report(conn, [Period("gesamt")]) == ["Im Zeitraum gibt es keine bewerteten Jobs."]
