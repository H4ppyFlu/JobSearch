import dataclasses
import json
from datetime import date

import pytest

from jobsearch import ai, digest, notify
from jobsearch.eligibility import HOMEOFFICE_REASON
from jobsearch.scoring import AI_NOT_REMOTE_REASON, run_scoring

from .conftest import FakeAi, add_job

TODAY = date.today().isoformat()


@pytest.fixture
def cfg(cfg, tmp_path):
    profile = tmp_path / "profile.md"
    profile.write_text("# Profil\nBeispielkenntnisse: Projektplanung, Datenanalyse.", encoding="utf-8")
    return dataclasses.replace(cfg, profile_path=profile)


def test_scores_newest_first_up_to_the_cap(conn, cfg):
    old = add_job(conn, "Alt", first_seen="2026-10-01")
    new = add_job(conn, "Neu", first_seen="2026-10-15")
    add_job(conn, "Raus", eligibility="ineligible", reason="Entfernung 80 km")
    fake = FakeAi()
    counts = run_scoring(conn, dataclasses.replace(cfg, max_scored_per_run=1), ask=fake)
    assert counts.scored == 1 and counts.backlog == 1
    assert conn.execute("SELECT job_id FROM fit_scores").fetchone()[0] == new
    counts = run_scoring(conn, cfg, ask=fake)
    assert counts.scored == 1 and counts.backlog == 0
    assert {r[0] for r in conn.execute("SELECT job_id FROM fit_scores")} == {old, new}
    assert run_scoring(conn, cfg, ask=fake).scored == 0  # already scored Jobs are left alone
    assert len(fake.prompts) == 2


def test_prompt_contains_profile_notes_and_jobs(conn, cfg):
    job = add_job(conn, "Projektingenieur")
    with conn:
        conn.execute("INSERT INTO feedback_notes (job_id, status, note, created_at) VALUES (?, 'not_interesting', "
                     "'Zu viel Reisetätigkeit', '2026-10-14T10:00:00')", (job,))
    fake = FakeAi()
    run_scoring(conn, cfg, ask=fake)
    prompt = fake.prompts[0]
    assert "Beispielkenntnisse" in prompt
    assert "Zu viel Reisetätigkeit" in prompt
    assert '"title": "Projektingenieur"' in prompt


def test_stored_score(conn, cfg):
    job = add_job(conn, "Projektingenieur")
    run_scoring(conn, cfg, ask=FakeAi(tier=3, remote="hybrid"))
    score = dict(conn.execute("SELECT * FROM fit_scores").fetchone())
    assert (score["job_id"], score["tier"], score["model"], score["low_confidence"]) == (job, 3, "claude-test", 0)
    assert json.loads(score["missing_skills"]) == ["SAP"]
    assert len(score["profile_hash"]) == 16
    assert conn.execute("SELECT remote_type FROM jobs").fetchone()[0] == "hybrid"


def test_missing_description_or_snippet_is_low_confidence(conn, cfg):
    add_job(conn, "Ohne Text", description=None)
    add_job(conn, "Nur Snippet", snippet=True)
    counts = run_scoring(conn, cfg, ask=FakeAi())
    assert counts.low_confidence == 2


def test_remote_candidates_are_settled_by_the_ai(conn, cfg):
    reason = f"{HOMEOFFICE_REASON} (Hinweis: '100% remote')"
    full = add_job(conn, "Remote A", eligibility="unclear", reason=reason)
    run_scoring(conn, cfg, ask=FakeAi(remote="full"))
    hybrid = add_job(conn, "Remote B", eligibility="unclear", reason=reason)
    add_job(conn, "Ohne Beschreibung", eligibility="unclear", reason=HOMEOFFICE_REASON, description=None)
    counts = run_scoring(conn, cfg, ask=FakeAi(remote="hybrid"))
    assert counts.scored == 1 and counts.remote_rejected == 1
    rows = {r["id"]: (r["eligibility"], r["ineligible_reason"]) for r in conn.execute("SELECT * FROM jobs")}
    assert rows[full] == ("eligible", None)
    assert rows[hybrid] == ("ineligible", AI_NOT_REMOTE_REASON)


def test_ai_failure_keeps_jobs_unscored_and_is_recorded(conn, cfg):
    add_job(conn, "Projektingenieur")
    counts = run_scoring(conn, cfg, ask=FakeAi(fail="Claude-Kontingent erschöpft"))
    assert counts.error == "Claude-Kontingent erschöpft" and counts.backlog == 1
    assert conn.execute("SELECT count(*) FROM fit_scores").fetchone()[0] == 0
    run = conn.execute("SELECT kind, ok, error FROM runs").fetchone()
    assert tuple(run) == ("score", 0, "Claude-Kontingent erschöpft")


def test_wrong_job_ids_are_rejected():
    from jobsearch.scoring import check_answer
    with pytest.raises(ai.InvalidAnswer, match="fehlende Jobs"):
        check_answer({1, 2})({"scores": [{"job_id": 1}]})
    with pytest.raises(ai.InvalidAnswer, match="mehrfach"):
        check_answer({1})({"scores": [{"job_id": 1}, {"job_id": 1}]})


def test_digest_and_toast(conn, cfg, tmp_path):
    add_job(conn, "Projektingenieur", first_seen=TODAY)
    add_job(conn, "Wartet", first_seen="2026-01-01")
    counts = run_scoring(conn, dataclasses.replace(cfg, max_scored_per_run=1), ask=FakeAi(tier=3))
    path, report = digest.write_digest(conn, tmp_path / "digests", date.today(), counts)
    text = path.read_text(encoding="utf-8")
    assert "## ★★★ Sehr passend (1)" in text
    assert "[Projektingenieur](https://x/" in text
    assert "**Fehlt:** SAP" in text
    assert "Warten auf Bewertung: 1." in text
    assert notify.message(report, counts) == ("JobSearch", "Neu: 1× ★★★\n1 Jobs warten auf Bewertung")


def test_no_toast_when_nothing_new(conn, cfg, tmp_path):
    add_job(conn, "Projektingenieur", first_seen=TODAY)
    counts = run_scoring(conn, cfg, ask=FakeAi(tier=1))
    _, report = digest.write_digest(conn, tmp_path, date.today(), counts)
    assert notify.message(report, counts) is None
