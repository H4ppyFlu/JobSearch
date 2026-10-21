from __future__ import annotations

import json
from pathlib import Path

import pytest

from jobsearch import ai, db
from jobsearch.config import Config

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def fixture_json(name: str) -> dict:
    return json.loads(fixture_text(name))


@pytest.fixture
def conn():
    connection = db.connect(":memory:")
    yield connection
    connection.close()


@pytest.fixture
def cfg(tmp_path) -> Config:
    return Config(
        root=tmp_path,
        profile_path=None,
        home_location="Kassel",
        home_lat=51.3127,
        home_lon=9.4797,
        radius_km=30,
        radius_target_company_km=70,
        remote_max_window_days=14,
        reopening_days=21,
        model_scoring="sonnet",
        max_scored_per_run=40,
        feedback_notes_in_prompt=20,
    )


def add_job(conn, title, eligibility="eligible", reason=None, description="Aufgaben: Projekte planen.",
            first_seen="2026-10-15", snippet=False, company="Muster GmbH", source="bundesagentur"):
    """A Job with one Posting, written straight to the database."""
    with conn:
        job_id = conn.execute(
            """INSERT INTO jobs (company, title, location, distance_km, eligibility, ineligible_reason,
                                 first_seen, last_seen) VALUES (?, ?, '34117 Kassel', 5, ?, ?, ?, ?)""",
            (company, title, eligibility, reason, first_seen, first_seen),
        ).lastrowid
        conn.execute(
            """INSERT INTO postings (job_id, source, source_id, url, title, company, description, is_snippet,
                                     first_seen, last_seen)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (job_id, source, f"ID-{job_id}", f"https://x/{job_id}", title, company, description, int(snippet),
             first_seen, first_seen),
        )
    return job_id


def record_usage(usage_recorder, purpose: str, model: str = "claude-test") -> None:
    """What ai.ask reports for one successful attempt, for fakes that stand in for it."""
    if usage_recorder:
        usage_recorder(ai.CallUsage(purpose, model, 1, "ok", "2026-10-15T08:00:00", input_tokens=10,
                                    output_tokens=20, cost_usd=0.01))


def ai_calls(conn) -> list[tuple]:
    return [tuple(r) for r in conn.execute("SELECT purpose, run_id, outcome FROM ai_calls ORDER BY id")]


class FakeAi:
    """Stands in for ai.ask. Rates every Job in the prompt with the given values.
    `same`, `generic` and `suspected` (injection_suspected) are sets of job ids for which the answer is true."""

    def __init__(self, tier=2, remote="unknown", fail=None, same=(), generic=(), suspected=()):
        self.tier, self.remote, self.fail = tier, remote, fail
        self.same, self.generic, self.suspected = set(same), set(generic), set(suspected)
        self.prompts = []

    def jobs_in(self, prompt):
        return json.loads(prompt.split("# Jobs\n\n", 1)[1])

    def __call__(self, prompt, *, purpose, model, schema, system, check=None, usage_recorder=None):
        self.prompts.append(prompt)
        self.purpose = purpose
        if self.fail:
            raise ai.AiError(self.fail)
        record_usage(usage_recorder, purpose)
        data = {"scores": [{"job_id": j["job_id"], "tier": self.tier, "reasoning": "Passt gut zu deinem Profil.",
                            "matching_skills": ["Projektplanung"], "missing_skills": ["SAP"],
                            "part_time": "unknown", "remote": self.remote,
                            "generic": j["job_id"] in self.generic, "same_as_candidate": j["job_id"] in self.same,
                            "injection_suspected": j["job_id"] in self.suspected}
                           for j in self.jobs_in(prompt)]}
        check(data)
        return ai.Answer(data, "claude-test")


TERMS = ["Werkplaner", "Anlagenplaner", "Projektleiter Anlagenbau", "Technischer Planer", "Bauleiter"]


class TermsAi:
    """Stands in for ai.ask and proposes the given Search terms."""

    def __init__(self, *answers, fail=None):
        self.answers = list(answers) or [TERMS]
        self.fail = fail
        self.prompts = []

    def __call__(self, prompt, *, purpose, model, schema, system, check=None, usage_recorder=None):
        self.prompts.append(prompt)
        self.purpose = purpose
        if self.fail:
            raise ai.AiError(self.fail)
        record_usage(usage_recorder, purpose)
        data = {"terms": self.answers[min(len(self.prompts), len(self.answers)) - 1]}
        check(data)
        return ai.Answer(data, "claude-test")
