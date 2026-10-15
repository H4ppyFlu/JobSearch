import pytest

from jobsearch.companies import load_target_companies, match_target_company, normalize_company


def test_normalize_drops_legal_forms_and_punctuation():
    assert normalize_company("Muster+Partner Messtechnik AG") == ("muster", "partner", "messtechnik")
    assert normalize_company("Möbel & Co. KG") == ("moebel",)


@pytest.fixture
def targets(tmp_path):
    path = tmp_path / "companies.csv"
    path.write_text(
        "name,aliases,locations,career_url,ats_type,render_mode\n"
        "NORDTEC AG,,Musterstadt,,,\n"
        "Muster und Partner,Muster+Partner,Beispielstadt,,,\n"
        "Institut BFI,Beispiel-Institut für Fertigungstechnik,Musterstadt,,,\n",
        encoding="utf-8",
    )
    return load_target_companies(path)


@pytest.mark.parametrize(
    "company, expected",
    [
        ("NORDTEC AG", "NORDTEC AG"),
        ("Nordtec Vertriebs-GmbH", "NORDTEC AG"),
        ("Muster+Partner Messtechnik AG", "Muster und Partner"),
        ("Beispiel-Institut für Fertigungstechnik BFI", "Institut BFI"),
        ("Supernordtec GmbH", None),
        ("Institut XYZ", None),
    ],
)
def test_match(targets, company, expected):
    found = match_target_company(company, targets)
    assert (found.name if found else None) == expected
