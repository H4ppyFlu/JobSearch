from __future__ import annotations

import json
from pathlib import Path

import pytest

from jobsearch import db
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
