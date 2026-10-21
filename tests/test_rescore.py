import dataclasses
from datetime import date

import pytest

from jobsearch import ai, db, digest, prompts
from jobsearch.cli import cmd_rescore
from jobsearch.scoring import run_rescoring, run_scoring

from .conftest import FakeAi, add_job, ai_calls


@pytest.fixture
def cfg(cfg, tmp_path):
    profile = tmp_path / "profile.md"
    profile.write_text("# Profil\nBeispielkenntnisse: Projektplanung, Datenanalyse.", encoding="utf-8")
    return dataclasses.replace(cfg, profile_path=profile)


def weak_job(conn, title, prompt_hash="altaltaltaltalt0", tier=1, first_seen="2026-10-10", **job):
    """A Job scored before today, by default ★ under an older prompt."""
    job_id = add_job(conn, title, first_seen=first_seen, **job)
    with conn:
        conn.execute("""INSERT INTO fit_scores (job_id, tier, reasoning, remote_signal, profile_hash, prompt_hash,
                                                model, scored_at)
                        VALUES (?, ?, 'Alt.', 'unknown', 'ppp', ?, 'claude-test', '2026-10-10T08:00:00')""",
                     (job_id, tier, prompt_hash))
    return job_id


def scores_of(conn, job_id):
    return [tuple(r) for r in conn.execute(
        "SELECT tier, sorted_out, prompt_hash FROM fit_scores WHERE job_id = ? ORDER BY id", (job_id,))]


class FailingOn(FakeAi):
    """A FakeAi whose call number `n` fails."""

    def __init__(self, n, **kwargs):
        super().__init__(**kwargs)
        self.n = n

    def __call__(self, prompt, **kwargs):
        if len(self.prompts) + 1 == self.n:
            self.prompts.append(prompt)
            raise ai.AiError("Claude-Kontingent erschöpft")
        return super().__call__(prompt, **kwargs)


def test_rescores_only_weak_new_jobs_in_reach_with_an_older_prompt(conn, cfg):
    old = weak_job(conn, "Koch")
    unversioned = weak_job(conn, "Florist", prompt_hash=None)
    marked = weak_job(conn, "Markiert")
    with conn:
        conn.execute("UPDATE jobs SET status = 'not_interesting' WHERE id = ?", (marked,))
    weak_job(conn, "Außerhalb", eligibility="ineligible", reason="Entfernung 80 km")
    weak_job(conn, "Gut", tier=2)
    weak_job(conn, "Schon neu", prompt_hash=prompts.version("scoring.md"))
    add_job(conn, "Unbewertet")
    fake = FakeAi(tier=1, sorted_out={old})

    counts = run_rescoring(conn, cfg, ask=fake)

    assert (counts.rescored, counts.sorted_out, counts.tier_changed, counts.left) == (2, 1, 0, 0)
    assert fake.purpose == "rescoring"
    assert [j["job_id"] for j in fake.jobs_in(fake.prompts[0])] == sorted([old, unversioned], reverse=True)
    current = prompts.version("scoring.md")
    assert scores_of(conn, old) == [(1, 0, "altaltaltaltalt0"), (1, 1, current)]  # the old row stays
    assert scores_of(conn, unversioned)[-1] == (1, 0, current)
    assert ai_calls(conn) == [("rescoring", None, "ok")]


def test_works_through_all_in_batches_and_a_second_call_makes_none(conn, cfg):
    jobs = [weak_job(conn, f"Stelle {i}", first_seen=f"2026-10-0{i}") for i in range(1, 6)]
    fake = FakeAi(tier=1)
    counts = run_rescoring(conn, dataclasses.replace(cfg, max_scored_per_run=2), ask=fake)
    assert (counts.rescored, counts.left, len(fake.prompts)) == (5, 0, 3)
    assert [j["job_id"] for j in fake.jobs_in(fake.prompts[0])] == [jobs[4], jobs[3]]  # newest first
    again = FakeAi()
    assert run_rescoring(conn, cfg, ask=again).rescored == 0 and again.prompts == []


def test_a_failing_batch_stops_and_the_next_call_continues(conn, cfg):
    for i in range(1, 4):
        weak_job(conn, f"Stelle {i}", first_seen=f"2026-10-0{i}")
    small = dataclasses.replace(cfg, max_scored_per_run=1)
    counts = run_rescoring(conn, small, ask=FailingOn(2))
    assert (counts.rescored, counts.left, counts.error) == (1, 2, "Claude-Kontingent erschöpft")
    counts = run_rescoring(conn, small, ask=FakeAi())
    assert (counts.rescored, counts.left, counts.error) == (2, 0, None)


def test_only_the_score_is_new(conn, cfg):
    other = add_job(conn, "Planer", company="Muster GmbH")
    remote = weak_job(conn, "Planer remote")
    generic = weak_job(conn, "Viele Stellen")
    already_generic = weak_job(conn, "Sammelanzeige")
    with conn:
        conn.execute("UPDATE jobs SET match_candidate = ?, remote_type = 'hybrid' WHERE id = ?", (other, remote))
        conn.execute("UPDATE jobs SET is_generic = 1 WHERE id = ?", (already_generic,))
    before = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM jobs")}

    run_rescoring(conn, cfg, ask=FakeAi(tier=2, remote="full", same={remote}, generic={generic}))

    after = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM jobs")}
    assert after[generic]["is_generic"] == 1 and after[already_generic]["is_generic"] == 1
    after[generic]["is_generic"] = 0
    assert after == before  # no merge, eligibility and remote_type untouched
    assert scores_of(conn, remote)[-1][0] == 2


def test_rescored_jobs_stay_out_of_the_digest(conn, cfg, tmp_path):
    weak_job(conn, "Koch")
    run_rescoring(conn, cfg, ask=FakeAi(tier=3))
    new = add_job(conn, "Neu heute", first_seen=date.today().isoformat())
    run_scoring(conn, cfg, ask=FakeAi(tier=1, sorted_out={new}))
    text = digest.write_digest(conn, tmp_path, date.today())[0].read_text(encoding="utf-8")
    assert "Koch" not in text and "1 neu bewertete Jobs" in text and "Von der KI aussortiert: 1" in text


def test_the_command_summarizes_and_names_the_jobs_that_moved_up(cfg, capsys):
    conn = db.connect(cfg.db_path)
    weak_job(conn, "Planer", company="Beispiel Technik GmbH")
    weak_job(conn, "Koch")
    assert cmd_rescore(cfg, ask=FakeAi(tier=3)) == 0
    out = capsys.readouterr().out
    assert "bewertet 2, davon aussortiert 0, Tier verändert 2, übrig 0" in out
    assert "★ → ★★★: Planer – Beispiel Technik GmbH" in out
    assert cmd_rescore(cfg, ask=FailingOn(1)) == 0  # nothing left, so no call


def test_the_command_names_the_error_and_what_is_left(cfg, capsys):
    weak_job(db.connect(cfg.db_path), "Koch")
    assert cmd_rescore(cfg, ask=FailingOn(1)) == 1
    err = capsys.readouterr().err
    assert "Claude-Kontingent erschöpft" in err and "übrig 1" in err
