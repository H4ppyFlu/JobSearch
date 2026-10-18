import pytest

from jobsearch import ai
from jobsearch.search_terms import active_terms, check_answer, derived_terms, refresh_derived
from tests.conftest import TERMS, TermsAi

PROFILE = "Erfahrung in Planung und Anlagenbau, sucht Teilzeit in Musterstadt."


def test_first_run_derives_terms_from_the_profile(conn):
    fake = TermsAi()
    result = refresh_derived(conn, PROFILE, model="sonnet", ask=fake)
    assert result.changed and result.error is None
    assert derived_terms(conn) == sorted(TERMS)
    assert PROFILE in fake.prompts[0]


def test_unchanged_profile_makes_no_ai_call(conn):
    refresh_derived(conn, PROFILE, model="sonnet", ask=TermsAi())
    fake = TermsAi()
    result = refresh_derived(conn, PROFILE, model="sonnet", ask=fake)
    assert fake.prompts == []
    assert not result.changed and result.terms == sorted(TERMS)


def test_changed_profile_replaces_the_derived_terms(conn):
    refresh_derived(conn, PROFILE, model="sonnet", ask=TermsAi())
    new = ["Konstrukteur", "Technischer Zeichner", "CAD-Konstrukteur", "Produktentwickler", "Versuchsingenieur"]
    result = refresh_derived(conn, PROFILE + "\nNeu: Konstruktion.", model="sonnet", ask=TermsAi(new))
    assert result.changed
    assert derived_terms(conn) == sorted(new)


def test_ai_failure_keeps_the_previous_terms_and_retries_next_run(conn):
    refresh_derived(conn, PROFILE, model="sonnet", ask=TermsAi())
    changed = PROFILE + "\nNeu: Konstruktion."
    result = refresh_derived(conn, changed, model="sonnet", ask=TermsAi(fail="Claude-Kontingent erschöpft"))
    assert result.error == "Claude-Kontingent erschöpft"
    assert result.terms == sorted(TERMS) and derived_terms(conn) == sorted(TERMS)
    retry = TermsAi(["Konstrukteur", "Technischer Zeichner", "CAD-Konstrukteur", "Produktentwickler", "Prüfer"])
    assert refresh_derived(conn, changed, model="sonnet", ask=retry).changed


@pytest.mark.parametrize("terms", [
    ["Werkplaner", "werkplaner", "Anlagenplaner", "Bauleiter", "Planer"],  # duplicate
    ["Werkplaner", "", "Anlagenplaner", "Bauleiter", "Planer"],  # empty
    ["Werkplaner", "Ingenieur (m/w/d) im Anlagenbau Musterstadt", "Anlagenplaner", "Bauleiter", "Planer"],
])
def test_unusable_terms_are_rejected(terms):
    with pytest.raises(ai.InvalidAnswer):
        check_answer({"terms": terms})


def test_active_terms_are_manual_then_derived_without_duplicates():
    assert active_terms(["Projektleiter", "Bauleiter"], ["bauleiter", "Anlagenplaner"]) == \
        ["Projektleiter", "Bauleiter", "Anlagenplaner"]


def test_minus_line_switches_a_term_off():
    assert active_terms(["Projektleiter", "-anlagenplaner", "- Bauleiter"], ["Anlagenplaner", "Bauleiter", "Planer"]) \
        == ["Projektleiter", "Planer"]
