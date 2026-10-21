import dataclasses
import shutil
from datetime import date, timedelta
from pathlib import Path

import pytest

from jobsearch.career import ScanCounts
from jobsearch import db, usage
from jobsearch.cli import career_summary, cmd_daily, cmd_status
from jobsearch.config import ConfigError
from tests.conftest import TERMS, TermsAi

EXAMPLES = Path(__file__).parent.parent / "private.example"


class RecordingClient:
    """A Bundesagentur client that finds nothing and remembers the Search terms it was asked for."""

    def __init__(self):
        self.terms = []

    def search(self, term, **kwargs):
        self.terms.append(term)
        return iter([])

    def description(self, refnr):
        raise AssertionError("no Postings, so no descriptions")


@pytest.fixture
def cfg(cfg, tmp_path):
    shutil.copytree(EXAMPLES, cfg.private_dir)
    (cfg.private_dir / "search_terms.txt").write_text("# Manuell\nProjektleiter\n-Bauleiter\n", encoding="utf-8")
    profile = tmp_path / "elsewhere" / "profil.md"
    profile.parent.mkdir()
    profile.write_text("Projektplanung und Anlagenbau in Musterstadt.", encoding="utf-8")
    return dataclasses.replace(cfg, profile_path=profile)


def test_daily_run_searches_manual_and_derived_terms(cfg):
    client = RecordingClient()
    assert cmd_daily(cfg, score=False, career="off", client=client, ask=TermsAi()) == 0
    expected = ["Projektleiter"] + [t for t in TERMS if t != "Bauleiter"]
    assert list(dict.fromkeys(client.terms)) == expected


def test_failed_derivation_is_a_warning_and_the_manual_terms_still_run(cfg, capsys):
    client = RecordingClient()
    assert cmd_daily(cfg, score=False, career="off", client=client, ask=TermsAi(fail="Claude-Kontingent erschöpft")) == 0
    assert set(client.terms) == {"Projektleiter"}
    assert "Suchbegriffe aus dem Profil nicht abgeleitet: Claude-Kontingent erschöpft" in capsys.readouterr().err


def test_without_profile_only_the_manual_terms_run(cfg):
    client = RecordingClient()
    fake = TermsAi()
    cmd_daily(dataclasses.replace(cfg, profile_path=None), score=False, career="off", client=client, ask=fake)
    assert set(client.terms) == {"Projektleiter"} and fake.prompts == []


def test_failed_first_derivation_without_manual_terms_names_the_ai_error(cfg):
    (cfg.private_dir / "search_terms.txt").write_text("# leer\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="Claude-Kontingent erschöpft"):
        cmd_daily(cfg, score=False, career="off", client=RecordingClient(), ask=TermsAi(fail="Claude-Kontingent erschöpft"))


def test_career_summary_names_dropped_postings_only_when_there_are_some():
    counts = ScanCounts(pages=3, unchanged=1, extracted=2, new_jobs=1)
    assert "verworfen" not in career_summary(counts)
    counts.ungrounded = 2
    assert career_summary(counts).endswith(", verworfen (nicht im Seitentext): 2")


def test_status_shows_the_ai_usage_of_the_last_seven_days(cfg, capsys):
    record = db.usage_recorder(db.connect(cfg.db_path))
    today = date.today()
    for day, cost in ((today, 0.04), (today - timedelta(days=6), 0.02), (today - timedelta(days=7), 5.0)):
        record(usage.CallUsage("scoring", "claude-test", 1, "ok", f"{day.isoformat()}T08:00:00", input_tokens=2,
                            output_tokens=400, cache_read_tokens=0, cache_creation_tokens=9000, cost_usd=cost))
    cmd_status(cfg)
    assert "KI-Verbrauch (7 Tage): 2 Aufrufe, API-Gegenwert 0.060 $" in capsys.readouterr().out
