import csv

import pytest

from jobsearch import ai
from jobsearch.career_setup import run_setup

CSV = """name,aliases,locations,career_url,ats_type,render_mode
Example Tech AG,Example Tech|ExTech,Musterstadt,,,
Muster Industrie GmbH,,Beispielstadt,https://muster.example/jobs,other,static
Beispiel Software SE,Beispiel Group,Musterstadt,,,
"""


class SetupAi:
    """Answers every company in the prompt with a made-up career page."""

    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail

    def __call__(self, prompt, *, model, schema, system, check=None, tools=(), timeout=None):
        self.calls.append((prompt, tools))
        if self.fail:
            raise ai.AiError(self.fail)
        names = [line.split("] ", 1)[1].split(" (", 1)[0]
                 for line in prompt.split("# Companies\n\n", 1)[1].splitlines() if line.startswith("- [")]
        data = {"companies": [{"id": i, "career_url": f"https://{n.split()[0].lower()}.example/karriere",
                               "ats_type": "personio", "render_mode": "static", "note": ""}
                              for i, n in enumerate(names)]}
        check(data)
        return ai.Answer(data, "claude-test")


def rows(path):
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


@pytest.fixture
def companies(tmp_path):
    path = tmp_path / "companies.csv"
    path.write_text(CSV, encoding="utf-8")
    return path


def test_setup_fills_only_missing_career_pages(companies):
    fake = SetupAi()
    result = run_setup(companies, model="sonnet", ask=fake)
    assert result.filled == 2
    by_name = {r["name"]: r for r in rows(companies)}
    assert by_name["Example Tech AG"]["career_url"] == "https://example.example/karriere"
    assert by_name["Example Tech AG"]["ats_type"] == "personio"
    assert by_name["Example Tech AG"]["aliases"] == "Example Tech|ExTech"  # other columns untouched
    assert by_name["Muster Industrie GmbH"]["career_url"] == "https://muster.example/jobs"  # already set, kept
    assert "Muster Industrie" not in fake.calls[0][0]
    assert set(fake.calls[0][1]) == {"WebSearch", "WebFetch"}
    assert (companies.parent / "companies.csv.bak").read_text(encoding="utf-8") == CSV


def test_failure_keeps_what_earlier_batches_found(companies, monkeypatch):
    monkeypatch.setattr("jobsearch.career_setup.BATCH_SIZE", 1)
    fake = SetupAi()
    calls = iter([None, "Claude-Kontingent erschöpft"])

    def flaky(prompt, **kw):
        fake.fail = next(calls)
        return fake(prompt, **kw)

    result = run_setup(companies, model="sonnet", ask=flaky)
    assert result.filled == 1 and "Kontingent" in result.error
    by_name = {r["name"]: r for r in rows(companies)}
    assert by_name["Example Tech AG"]["career_url"]
    assert by_name["Beispiel Software SE"]["career_url"] == ""
    assert run_setup(companies, model="sonnet", ask=SetupAi()).filled == 1  # a rerun does only the rest


def test_recheck_replaces_pages_that_did_not_work(companies):
    fake = SetupAi()
    run_setup(companies, model="sonnet", ask=fake,
              recheck={"Muster Industrie GmbH": "zeigt keine Stellen"})
    prompt = fake.calls[0][0]
    assert "Muster Industrie GmbH" in prompt
    assert "https://muster.example/jobs" in prompt and "zeigt keine Stellen" in prompt
    by_name = {r["name"]: r for r in rows(companies)}
    assert by_name["Muster Industrie GmbH"]["career_url"] == "https://muster.example/karriere"
    assert by_name["Muster Industrie GmbH"]["ats_type"] == "personio"


def test_a_list_saved_with_bom_is_read_and_keeps_its_bom(companies):
    companies.write_text(CSV, encoding="utf-8-sig")
    result = run_setup(companies, model="sonnet", ask=SetupAi())
    assert result.filled == 2
    raw = companies.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbfname,") and raw.count(b"\xef\xbb\xbf") == 1
