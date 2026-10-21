import json
import sqlite3
from datetime import date

import pytest

from jobsearch import ai, db, market
from jobsearch.cli import cmd_market
from jobsearch.evaluation import Period
from jobsearch.market import active_companies, group_skills, skill_table, ungrouped_texts

from .conftest import add_job, record_usage

TODAY = date(2026, 10, 21)


def test_a_company_with_three_jobs_in_reach_is_active(conn):
    for title in ("Planer", "Disponent", "Stelle X"):
        add_job(conn, title, company="Beispiel Technik GmbH", first_seen="2026-10-01")
    add_job(conn, "Planer", company="Muster GmbH", first_seen="2026-10-01")
    add_job(conn, "Disponent", company="Muster GmbH", first_seen="2026-10-01")

    found = active_companies(conn, TODAY)

    assert [(c.name, c.jobs) for c in found] == [("Beispiel Technik GmbH", 3)]


def test_old_generic_and_ineligible_jobs_do_not_count(conn):
    company = "Beispiel Technik GmbH"
    add_job(conn, "Planer", company=company, first_seen="2026-07-23")  # day 91 before today: outside
    add_job(conn, "Disponent", company=company, first_seen="2026-07-24")  # day 90: inside
    add_job(conn, "Stelle X", company=company, first_seen="2026-10-01", eligibility="ineligible")
    add_job(conn, "Techniker", company=company, first_seen="2026-10-01", eligibility="unclear")
    generic = add_job(conn, "Viele Stellen", company=company, first_seen="2026-10-01")
    conn.execute("UPDATE jobs SET is_generic = 1 WHERE id = ?", (generic,))
    add_job(conn, "Werkplaner", company=company, first_seen="2026-10-02")

    assert active_companies(conn, TODAY) == []

    add_job(conn, "Stelle Y", company="Beispiel Technik AG", first_seen="2026-10-03")  # same company
    assert [c.jobs for c in active_companies(conn, TODAY)] == [3]


def add_score(conn, job_id, tier, scored_at="2026-10-02T08:00:00", missing=(), matching=(), low_confidence=False):
    with conn:
        conn.execute(
            """INSERT INTO fit_scores (job_id, tier, reasoning, matching_skills, missing_skills, low_confidence,
                                       profile_hash, model, scored_at) VALUES (?, ?, 'Passt.', ?, ?, ?, 'ppp', 'claude-test', ?)""",
            (job_id, tier, json.dumps(list(matching)), json.dumps(list(missing)), int(low_confidence), scored_at))


def test_a_single_reopening_makes_a_company_active(conn):
    earlier = add_job(conn, "Planer", company="Muster GmbH", first_seen="2026-05-01")
    again = add_job(conn, "Planer", company="muster gmbh", first_seen="2026-10-01")
    conn.execute("UPDATE jobs SET reopening_of = ? WHERE id = ?", (earlier, again))
    add_job(conn, "Disponent", company="Muster GmbH", first_seen="2026-10-02")

    [found] = active_companies(conn, TODAY)

    assert (found.name, found.jobs, found.reopenings) == ("Muster GmbH", 2, 1)


def test_best_tier_counts_the_newest_score_of_each_job(conn):
    jobs = [add_job(conn, t, company="Beispiel Technik GmbH", first_seen="2026-10-01") for t in ("A", "B", "C")]
    add_score(conn, jobs[0], 3, scored_at="2026-10-01T08:00:00")
    add_score(conn, jobs[0], 1, scored_at="2026-10-05T08:00:00")  # rescored: now ★
    add_score(conn, jobs[1], 2)

    [found] = active_companies(conn, TODAY)

    assert found.best_tier == 2


def test_only_companies_off_the_target_list_get_the_note(conn):
    for company in ("Beispiel Technik GmbH", "Muster GmbH"):
        for title in ("A", "B", "C"):
            add_job(conn, title, company=company, first_seen="2026-10-01")
    conn.execute("UPDATE jobs SET is_target_company = 1 WHERE company = 'Muster GmbH' AND title = 'A'")

    notes = {c.name: (c.is_target, c.note) for c in active_companies(conn, TODAY)}

    assert notes == {"Beispiel Technik GmbH": (False, "Kandidat für companies.csv"), "Muster GmbH": (True, "")}


def test_market_command_prints_the_companies_in_german(cfg, capsys):
    conn = db.connect(cfg.db_path)
    jobs = [add_job(conn, t, company="Beispiel Technik GmbH", first_seen="2026-10-01") for t in ("A", "B", "C")]
    add_score(conn, jobs[0], 3)

    assert cmd_market(cfg, today=TODAY) == 0

    out = capsys.readouterr().out
    assert "Aktive Firmen" in out
    assert "Beispiel Technik GmbH" in out and "★★★" in out and "Kandidat für companies.csv" in out


def test_market_command_without_active_companies_says_so(cfg, capsys):
    assert cmd_market(cfg, today=TODAY) == 0
    assert "Keine aktiven Firmen" in capsys.readouterr().out


class SkillAi:
    """Stands in for ai.ask. Puts each skill text into the group `group(text)`; `answer` can change the
    parsed answer before the check, to send broken ones."""

    def __init__(self, group=lambda text: text.split()[0], answer=None, fail=None):
        self.group, self.answer, self.fail = group, answer, fail
        self.prompts = []

    def skills_in(self, prompt):
        return json.loads(prompt.split("# Skills\n\n", 1)[1])

    def existing_in(self, prompt):
        return json.loads(prompt.split("# Existing groups\n\n", 1)[1].split("\n\n# Skills", 1)[0])

    def __call__(self, prompt, *, purpose, model, schema, system, check=None, usage_recorder=None):
        self.prompts.append(prompt)
        self.purpose = purpose
        if self.fail:
            raise ai.AiError(self.fail)
        record_usage(usage_recorder, purpose)
        data = {"groups": [{"index": s["index"], "group": self.group(s["text"])} for s in self.skills_in(prompt)]}
        if self.answer:
            data = self.answer(data)
        try:
            check(data)
        except ai.InvalidAnswer as e:
            raise ai.AiError(f"Claude hat zweimal keine brauchbare Antwort geliefert: {e}") from None
        return ai.Answer(data, "claude-test")


def groups(conn):
    return dict(conn.execute("SELECT text, group_name FROM skill_groups").fetchall())


def test_only_ungrouped_texts_of_good_jobs_are_sent(conn):
    good = add_job(conn, "A", first_seen="2026-10-01")
    add_score(conn, good, 1, scored_at="2026-10-01T08:00:00", missing=["Altes Thema"])
    add_score(conn, good, 3, missing=["Tabulix  PM"], matching=["Excel"])  # newest score counts
    weak = add_job(conn, "B", first_seen="2026-10-01")
    add_score(conn, weak, 1, missing=["Nur in schwachen Jobs"])
    other = add_job(conn, "C", first_seen="2026-10-01")
    add_score(conn, other, 2, missing=["Excel", "Python"])
    conn.execute("INSERT INTO skill_groups (text, group_name) VALUES ('Python', 'Python')")
    fake = SkillAi()

    result = group_skills(conn, model="haiku", ask=fake)

    assert len(fake.prompts) == 1 and fake.purpose == "skill_groups"
    assert sorted(s["text"] for s in fake.skills_in(fake.prompts[0])) == ["Excel", "Tabulix PM"]
    assert fake.existing_in(fake.prompts[0]) == ["Python"]
    assert groups(conn) == {"Python": "Python", "Excel": "Excel", "Tabulix PM": "Tabulix"}
    assert (result.grouped, result.left, result.error) == (2, 0, None)


def test_a_run_with_nothing_new_makes_no_call(conn):
    job = add_job(conn, "A", first_seen="2026-10-01")
    add_score(conn, job, 2, missing=["Excel"])
    fake = SkillAi()
    group_skills(conn, model="haiku", ask=fake)

    group_skills(conn, model="haiku", ask=fake)

    assert len(fake.prompts) == 1


def skills_job(conn, texts, tier=2):
    job = add_job(conn, "A", first_seen="2026-10-01")
    add_score(conn, job, tier, missing=texts)
    return job


def test_calls_and_texts_per_run_are_limited(conn, monkeypatch):
    monkeypatch.setattr(market, "MAX_SKILLS_PER_CALL", 2)
    monkeypatch.setattr(market, "MAX_CALLS_PER_RUN", 2)
    skills_job(conn, [f"Skill {i}" for i in range(5)])
    fake = SkillAi(group=lambda text: f"Gruppe {text}")

    result = group_skills(conn, model="haiku", ask=fake)

    assert [len(fake.skills_in(p)) for p in fake.prompts] == [2, 2]
    assert fake.existing_in(fake.prompts[1]) == ["Gruppe Skill 0", "Gruppe Skill 1"]
    assert (result.grouped, result.calls, result.left) == (4, 2, 1)
    group_skills(conn, model="haiku", ask=fake)
    assert [s["text"] for s in fake.skills_in(fake.prompts[2])] == ["Skill 4"]


@pytest.mark.parametrize("broken", [
    lambda d: {"groups": d["groups"][:-1]},  # an index missing
    lambda d: {"groups": d["groups"] + [{"index": 7, "group": "Extra"}]},  # an index not sent
    lambda d: {"groups": d["groups"] + d["groups"][:1]},  # an index twice
    lambda d: {"groups": [{**g, "group": " "} for g in d["groups"]]},  # empty name
    lambda d: {"groups": [{**g, "group": "x" * 61} for g in d["groups"]]},  # name too long
])
def test_a_broken_answer_leaves_the_texts_ungrouped(conn, broken):
    skills_job(conn, ["Excel", "Tabulix"])

    result = group_skills(conn, model="haiku", ask=SkillAi(answer=broken))

    assert groups(conn) == {}
    assert (result.grouped, result.left) == (0, 2)
    assert "keine brauchbare Antwort" in result.error


def test_an_existing_group_keeps_its_spelling(conn):
    conn.execute("INSERT INTO skill_groups (text, group_name) VALUES ('Tabulix', 'Tabulix')")
    skills_job(conn, ["Tabulix PM", "tabulix-Module"])

    group_skills(conn, model="haiku", ask=SkillAi(group=lambda text: " tabulix "))

    assert set(groups(conn).values()) == {"Tabulix"}


def test_regroup_sends_everything_again(conn):
    skills_job(conn, ["Excel", "Tabulix"])
    group_skills(conn, model="haiku", ask=SkillAi())
    fake = SkillAi(group=lambda text: "Office")

    group_skills(conn, model="haiku", ask=fake, regroup=True)

    assert fake.existing_in(fake.prompts[0]) == []
    assert groups(conn) == {"Excel": "Office", "Tabulix": "Office"}


def test_market_command_groups_new_skills(cfg, capsys):
    conn = db.connect(cfg.db_path)
    skills_job(conn, ["Excel", "Tabulix"])

    assert cmd_market(cfg, today=TODAY, ask=SkillAi()) == 0

    assert "2 neue Skill-Texte gruppiert (1 KI-Aufruf)" in capsys.readouterr().out
    assert groups(conn) == {"Excel": "Excel", "Tabulix": "Tabulix"}


def test_a_failing_ai_still_gives_the_report(cfg, capsys):
    conn = db.connect(cfg.db_path)
    for title in ("A", "B", "C"):
        add_job(conn, title, company="Beispiel Technik GmbH", first_seen="2026-10-01")
    skills_job(conn, ["Excel"])

    assert cmd_market(cfg, today=TODAY, ask=SkillAi(fail="Claude-Kontingent erschöpft")) == 1

    captured = capsys.readouterr()
    assert "Beispiel Technik GmbH" in captured.out
    assert "Skill-Gruppen nicht aktualisiert: Claude-Kontingent erschöpft" in captured.err


def test_the_migration_adds_skill_groups_to_an_existing_database(tmp_path):
    path = tmp_path / "old.sqlite"
    old = sqlite3.connect(path)
    for version, sql in db._migrations():
        if version <= 8:
            old.executescript(sql)
    old.execute("PRAGMA user_version = 8")
    old.execute("""INSERT INTO jobs (company, title, eligibility, first_seen, last_seen)
                   VALUES ('Muster GmbH', 'Planer', 'eligible', '2026-10-01', '2026-10-01')""")
    old.commit()
    old.close()

    conn = db.connect(path)

    assert conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1
    assert groups(conn) == {}


def group_as(conn, mapping):
    with conn:
        conn.executemany("INSERT INTO skill_groups (text, group_name) VALUES (?, ?)", mapping.items())


def test_skill_table_counts_each_group_once_per_job_and_side(conn):
    recent = add_job(conn, "A", first_seen="2026-10-01")
    add_score(conn, recent, 3, missing=["Tabulix PM", "Tabulix MM", "Excel"], matching=["Excel"])
    old = add_job(conn, "B", first_seen="2026-05-01")
    add_score(conn, old, 2, missing=["Tabulix PM"], matching=["Python"], low_confidence=True)
    conn.execute("UPDATE jobs SET status = 'applied' WHERE id = ?", (old,))
    rescored = add_job(conn, "C", first_seen="2026-10-01")
    add_score(conn, rescored, 2, scored_at="2026-10-01T08:00:00", missing=["Tabulix PM"])
    add_score(conn, rescored, 1, scored_at="2026-10-03T08:00:00", missing=["Tabulix PM"])  # newest is ★: left out
    group_as(conn, {"Tabulix PM": "Tabulix", "Tabulix MM": "Tabulix", "Excel": "Excel", "Python": "Python"})

    rows = {r.group: r for r in skill_table(conn, Period("gesamt"), TODAY)}

    sap, excel = rows["Tabulix"], rows["Excel"]
    assert (sap.missing_recent, sap.missing_total, sap.matching_recent, sap.matching_total) == (1, 2, 0, 0)
    assert (sap.positive, sap.low_confidence) == (1, 1)
    assert (excel.missing_total, excel.matching_total, excel.positive) == (1, 1, 0)
    assert (rows["Python"].positive, rows["Python"].low_confidence) == (0, 0)  # only "fehlt" has "davon"
    assert list(rows) == ["Tabulix", "Excel", "Python"]  # by "fehlt, total"


def test_skill_table_honours_the_period_and_leaves_ungrouped_texts_out(conn):
    early = add_job(conn, "A", first_seen="2026-09-01")
    add_score(conn, early, 2, missing=["Tabulix PM"])
    late = add_job(conn, "B", first_seen="2026-10-10")
    add_score(conn, late, 2, missing=["Tabulix PM", "Neu"])
    group_as(conn, {"Tabulix PM": "Tabulix"})

    [sap] = skill_table(conn, Period("x", since="2026-10-01", until="2026-10-11"), TODAY)

    assert (sap.group, sap.missing_total, sap.missing_recent) == ("Tabulix", 1, 1)
    assert ungrouped_texts(conn) == ["Neu"]


def test_market_command_shows_the_top_groups_and_the_ungrouped_count(cfg, capsys, monkeypatch):
    monkeypatch.setattr(market, "TOP_GROUPS", 2)
    conn = db.connect(cfg.db_path)
    skills_job(conn, ["Tabulix", "Excel", "Python"])
    skills_job(conn, ["Tabulix", "Excel"])
    skills_job(conn, ["Tabulix", "Später"])
    group_as(conn, {"Tabulix": "Tabulix", "Excel": "Excel", "Python": "Python"})

    assert cmd_market(cfg, today=TODAY, ask=SkillAi(fail="Claude-Kontingent erschöpft")) == 1

    out = capsys.readouterr().out
    assert "Skill-Gruppen (★★ und ★★★, gesamt" in out
    assert [line.split()[0] for line in out.splitlines() if line.startswith("  Tabulix") or line.startswith("  Excel")
            or line.startswith("  Python")] == ["Tabulix", "Excel"]
    assert "ohne Gruppe (Texte, alle Zeiträume): 1" in out


def test_market_command_limits_the_table_to_the_period(cfg, capsys):
    conn = db.connect(cfg.db_path)
    skills_job(conn, ["Tabulix"])
    group_as(conn, {"Tabulix": "Tabulix"})

    cmd_market(cfg, today=TODAY, since=date(2026, 10, 2), ask=SkillAi())

    out = capsys.readouterr().out
    assert "ab 2026-10-02" in out and "Keine Skill-Gruppen im Zeitraum" in out


@pytest.mark.parametrize("fake", [SkillAi(fail="Claude-Kontingent erschöpft"),
                                  SkillAi(answer=lambda d: {"groups": d["groups"][:-1]})])
def test_a_failed_regroup_keeps_the_old_groups(conn, fake):
    skills_job(conn, ["Excel", "Tabulix"])
    group_skills(conn, model="haiku", ask=SkillAi())

    result = group_skills(conn, model="haiku", ask=fake, regroup=True)

    assert groups(conn) == {"Excel": "Excel", "Tabulix": "Tabulix"}
    assert result.grouped == 0 and "bisherigen Gruppen bleiben" in result.error


def test_a_regroup_too_large_for_one_run_keeps_the_old_groups(conn, monkeypatch):
    monkeypatch.setattr(market, "MAX_SKILLS_PER_CALL", 1)
    monkeypatch.setattr(market, "MAX_CALLS_PER_RUN", 1)
    skills_job(conn, ["Excel", "Tabulix"])
    group_as(conn, {"Excel": "Excel", "Tabulix": "Tabulix"})

    result = group_skills(conn, model="haiku", ask=SkillAi(group=lambda text: "Office"), regroup=True)

    assert groups(conn) == {"Excel": "Excel", "Tabulix": "Tabulix"}
    assert "bisherigen Gruppen bleiben" in result.error


def test_market_command_rejects_since_after_until(cfg, capsys):
    assert cmd_market(cfg, since=date(2026, 10, 10), until=date(2026, 10, 1), ask=SkillAi()) == 2
    assert "--since liegt nach --until" in capsys.readouterr().err
