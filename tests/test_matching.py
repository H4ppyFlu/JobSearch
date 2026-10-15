import dataclasses
from datetime import date

import pytest

from jobsearch import digest
from jobsearch.eligibility import Decision
from jobsearch.matching import JobIndex, MatchRules, Placement, attach, title_similarity, title_tokens
from jobsearch.scoring import run_scoring
from jobsearch.sources import Location, Posting

from .conftest import FakeAi

RULES = MatchRules(reopening_days=21, radius_query_km=70)
NEAR = Placement(5.0, Location("34117 Kassel", 51.315, 9.49), False)
FAR = Placement(400.0, Location("20095 Hamburg", 53.55, 10.0), False)
ELIGIBLE = Decision("eligible")


@pytest.fixture
def cfg(cfg, tmp_path):
    profile = tmp_path / "profile.md"
    profile.write_text("# Profil\nBeispielkenntnisse: Projektplanung.", encoding="utf-8")
    return dataclasses.replace(cfg, profile_path=profile)


def post(conn, source_id, title="Projektingenieur (m/w/d)", company="Muster Industrie GmbH", day="2026-10-01",
         source="bundesagentur", placement=NEAR, decision=ELIGIBLE, description="Aufgaben: Projekte planen."):
    """Attach a Posting the way the Daily run does, with a fresh index from the database."""
    posting = Posting(source, source_id, title, company, url=f"https://x/{source_id}", description=description)
    with conn:
        return attach(conn, posting, placement, decision, day, JobIndex.load(conn), RULES)


def jobs(conn):
    return {r["id"]: dict(r) for r in conn.execute("SELECT * FROM jobs")}


def postings_of(conn, job_id):
    return sorted(r[0] for r in conn.execute("SELECT source FROM postings WHERE job_id = ?", (job_id,)))


# The four cases the plan asks for


def test_same_job_on_two_sources_is_merged(conn, cfg):
    first, _ = post(conn, "BA-1")
    second, outcome = post(conn, "IN-1", title="Projektingenieur (w/m/d)", company="Muster Industrie",
                           source="indeed", day="2026-10-03")
    assert outcome == "pending"
    assert jobs(conn)[second]["match_candidate"] == first
    counts = run_scoring(conn, cfg, ask=FakeAi(same={second}))
    assert counts.merged == 1 and counts.scored == 1  # the first Job is scored, the second merged into it
    assert list(jobs(conn)) == [first]
    assert postings_of(conn, first) == ["bundesagentur", "indeed"]
    assert jobs(conn)[first]["last_seen"] == "2026-10-03"


def test_same_title_for_two_different_jobs_stays_apart(conn, cfg):
    first, _ = post(conn, "BA-1")
    second, outcome = post(conn, "BA-2", day="2026-10-02")
    assert outcome == "pending"
    fake = FakeAi()
    counts = run_scoring(conn, cfg, ask=fake)
    assert counts.merged == 0 and counts.scored == 2
    assert jobs(conn)[second]["match_candidate"] is None
    candidate = next(j for j in fake.jobs_in(fake.prompts[0]) if j["job_id"] == second)["candidate"]
    assert candidate["title"] == "Projektingenieur (m/w/d)" and "Projekte planen" in candidate["description"]


def test_repost_within_21_days_waits_for_the_ai(conn, cfg):
    first, _ = post(conn, "BA-1", day="2026-10-01")
    run_scoring(conn, cfg, ask=FakeAi())  # the first Job is already scored when the repost comes
    second, outcome = post(conn, "BA-2", day="2026-10-20")  # 19 days later
    assert outcome == "pending"
    counts = run_scoring(conn, cfg, ask=FakeAi(same={second}))
    assert counts.merged == 1
    assert jobs(conn)[first]["last_seen"] == "2026-10-20"
    assert conn.execute("SELECT count(*) FROM fit_scores").fetchone()[0] == 1  # no second score


def test_reopening_after_30_days(conn, cfg, tmp_path):
    first, _ = post(conn, "BA-1", day="2026-09-01")
    second, outcome = post(conn, "BA-2", day="2026-10-01")
    assert outcome == "reopening"
    job = jobs(conn)[second]
    assert (job["reopening_of"], job["match_candidate"]) == (first, None)
    fake = FakeAi()
    run_scoring(conn, cfg, ask=fake)
    assert "candidate" not in fake.jobs_in(fake.prompts[0])[0]  # no AI question for a Reopening
    text = digest.write_digest(conn, tmp_path, date(2026, 10, 1))[0].read_text(encoding="utf-8")
    assert "## Wieder ausgeschrieben (1)" in text and "zuerst gesehen am 2026-09-01" in text


# Edge cases


def test_same_ad_back_after_absence_is_a_reopening(conn):
    first, _ = post(conn, "BA-1", day="2026-09-01")
    second, outcome = post(conn, "BA-1", day="2026-10-01")
    assert outcome == "reopening" and second != first
    assert jobs(conn)[second]["reopening_of"] == first
    assert postings_of(conn, second) == ["bundesagentur"] and postings_of(conn, first) == []


def test_same_ad_seen_again_soon_is_just_seen(conn):
    first, _ = post(conn, "BA-1", day="2026-10-01")
    assert post(conn, "BA-1", day="2026-10-05") == (first, None)
    assert jobs(conn)[first]["last_seen"] == "2026-10-05"


def test_remote_job_is_never_an_automatic_reopening(conn):
    """Remote Jobs are only seen inside the nationwide date window, so their last_seen says little."""
    post(conn, "BA-1", day="2026-09-01", placement=FAR)
    _, outcome = post(conn, "BA-2", day="2026-10-01", placement=FAR)
    assert outcome == "pending"


def test_different_company_or_title_is_no_candidate(conn):
    post(conn, "BA-1")
    assert post(conn, "BA-2", company="Beispiel Software SE")[1] == "new"
    assert post(conn, "BA-3", title="Vertriebsmitarbeiter Innendienst")[1] == "new"


def test_ineligible_postings_are_not_matched(conn):
    post(conn, "BA-1")
    assert post(conn, "BA-2", decision=Decision("ineligible", "Entfernung 80 km"))[1] == "new"


def test_generic_jobs_are_never_merged_or_candidates(conn, cfg):
    first, _ = post(conn, "BA-1")
    second, _ = post(conn, "BA-2", day="2026-10-02")
    counts = run_scoring(conn, cfg, ask=FakeAi(generic={first}, same={second}))
    assert counts.generic == 1 and counts.merged == 0  # the candidate turned out generic
    assert jobs(conn)[first]["is_generic"] == 1
    assert post(conn, "BA-3", day="2026-10-03")[1] == "pending"  # matches the second, not the generic one
    assert jobs(conn)[max(jobs(conn))]["match_candidate"] == second


def test_chain_of_same_jobs_in_one_batch_collapses_into_the_oldest(conn, cfg):
    first, _ = post(conn, "BA-1")
    second, _ = post(conn, "BA-2", day="2026-10-02")
    third, _ = post(conn, "BA-3", day="2026-10-03")
    assert jobs(conn)[third]["match_candidate"] in (first, second)
    run_scoring(conn, cfg, ask=FakeAi(same={second, third}))
    assert list(jobs(conn)) == [first]
    assert conn.execute("SELECT count(*) FROM postings WHERE job_id = ?", (first,)).fetchone()[0] == 3


@pytest.mark.parametrize(
    "a, b, similar",
    [
        ("Projektingenieur (m/w/d)", "Projektingenieur (w/m/d)", True),
        ("Projektingenieur:in", "Projektingenieur (all genders)", True),
        ("Softwareentwickler Backend (m/w/d)", "Softwareentwickler (m/w/d) Backend", True),
        ("Projektingenieur (m/w/d)", "Projektingenieur Elektrotechnik (m/w/d)", True),
        ("Projektingenieur (m/w/d)", "Vertriebsingenieur (m/w/d)", False),
        ("Softwareentwickler Backend", "Softwareentwickler Frontend Mobile", False),
    ],
)
def test_title_similarity(a, b, similar):
    assert (title_similarity(title_tokens(a), title_tokens(b)) >= 0.5) is similar
