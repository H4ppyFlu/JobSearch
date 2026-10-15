import pytest

from jobsearch.eligibility import Rules, decide, distance_km, settle_remote

RULES = Rules(radius_km=30, radius_target_company_km=70, exclusions=("Praktik", "Werkstudent", "Duales Studium"))


@pytest.mark.parametrize(
    "title, distance, target, homeoffice, expected",
    [
        ("Projektingenieur", 5, False, False, "eligible"),
        ("Projektingenieur", 30, False, False, "eligible"),
        ("Projektingenieur", 45, False, False, "ineligible"),
        ("Projektingenieur", 45, True, False, "eligible"),
        ("Projektingenieur", 69, True, False, "eligible"),
        ("Projektingenieur", 75, True, False, "ineligible"),
        ("Projektingenieur", 400, False, True, "unclear"),
        ("Projektingenieur", None, False, False, "unclear"),
        ("Pflichtpraktikum Vertrieb", 5, False, False, "ineligible"),
        ("WERKSTUDENT Datenanalyse", 5, True, True, "ineligible"),
        ("Duales Studium Informatik", 5, False, False, "ineligible"),
    ],
)
def test_decide(title, distance, target, homeoffice, expected):
    assert decide(RULES, title, distance, target, homeoffice).eligibility == expected


def test_reason_names_the_matching_exclusion():
    assert decide(RULES, "Praktikant Marketing", 1, False, False).reason == "Ausschlussliste: Praktik"


@pytest.mark.parametrize(
    "text, fully_remote",
    [
        ("Die Stelle ist zu 100% remote möglich.", True),
        ("Sie arbeiten 100 % Homeoffice.", True),
        ("Arbeiten Sie vollständig remote aus ganz Deutschland.", True),
        ("komplett im Homeoffice", True),
        ("ortsunabhängiges Arbeiten", True),
        ("Wir sind ein Remote-First-Unternehmen", True),
        ("Bis zu 3 Tage Homeoffice pro Woche", False),
        ("Hybrides Arbeiten mit Remote-Anteil", False),
        ("Mobiles Arbeiten nach Absprache", False),
    ],
)
def test_settle_remote(text, fully_remote):
    assert settle_remote(text).eligibility == ("unclear" if fully_remote else "ineligible")


def test_kassel_to_goettingen_straight_line_is_within_target_radius():
    assert 37 < distance_km(51.3127, 9.4797, 51.5413, 9.9158) < 42
