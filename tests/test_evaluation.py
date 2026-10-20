import json

from jobsearch import db
from jobsearch.evaluation import changes, changes_report, describe

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
