import dataclasses
import io
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from jobsearch import guard


@pytest.fixture
def profile(tmp_path):
    folder = tmp_path / "Beispielprojekt"
    folder.mkdir()
    path = folder / "profil.md"
    path.write_text("Profil", encoding="utf-8")
    return path


def test_the_profile_itself_is_protected(profile):
    assert guard.touches_profile(str(profile), profile)


def test_a_file_next_to_the_profile_is_protected(profile):
    assert guard.touches_profile(str(profile.parent / "notizen.md"), profile)


def test_a_file_in_a_subfolder_is_protected(profile):
    assert guard.touches_profile(str(profile.parent / "unter" / "datei.md"), profile)


def test_a_path_with_dot_dot_is_resolved(profile, tmp_path):
    sneaky = tmp_path / "anderswo" / ".." / profile.parent.name / "profil.md"
    assert guard.touches_profile(str(sneaky), profile)


def test_the_folder_itself_is_protected(profile):
    assert guard.touches_profile(str(profile.parent), profile)


@pytest.mark.skipif(not guard.CASE_INSENSITIVE, reason="paths are case-insensitive only on Windows")
def test_a_different_case_is_protected_on_windows(profile):
    assert guard.touches_profile(str(profile).upper(), profile)


def test_a_file_in_the_repo_is_allowed(profile, tmp_path):
    assert not guard.touches_profile(str(tmp_path / "repo" / "src" / "modul.py"), profile)


def test_a_sibling_folder_with_the_same_prefix_is_allowed(profile):
    assert not guard.touches_profile(str(profile.parent.parent / (profile.parent.name + "2") / "x.md"), profile)


def test_without_profile_path_everything_is_allowed():
    assert not guard.touches_profile("C:/irgendwo/datei.md", None)


def test_bash_command_naming_the_profile_folder_is_flagged(profile):
    assert guard.command_names_profile(f'echo x > "{profile.parent}/neu.md"', profile)


def test_bash_command_with_forward_slashes_is_flagged(profile):
    assert guard.command_names_profile(f"rm {profile.parent.as_posix()}/profil.md", profile)


def test_bash_command_in_git_bash_form_is_flagged(profile):
    posix = profile.parent.as_posix()
    if len(posix) > 1 and posix[1] == ":":
        posix = "/" + posix[0].lower() + posix[2:]
    assert guard.command_names_profile(f"cat {posix}/profil.md", profile)


@pytest.mark.parametrize("form", ["$BEISPIEL_DIR", "${BEISPIEL_DIR}", "$env:BEISPIEL_DIR", "%BEISPIEL_DIR%"])
def test_bash_command_reaching_the_folder_through_a_variable_is_flagged(profile, monkeypatch, form):
    monkeypatch.setenv("BEISPIEL_DIR", str(profile.parent.parent))
    assert guard.command_names_profile(f"cat {form}/{profile.parent.name}/profil.md", profile)


def test_bash_command_reaching_the_folder_through_home_is_flagged(profile, monkeypatch):
    monkeypatch.setenv("HOME", str(profile.parent.parent))
    monkeypatch.setenv("USERPROFILE", str(profile.parent.parent))
    assert guard.command_names_profile(f"cat ~/{profile.parent.name}/profil.md", profile)


def test_bash_command_naming_a_sibling_folder_with_the_same_prefix_passes(profile):
    assert not guard.command_names_profile(f"ls {profile.parent}2/datei.md", profile)
    assert not guard.command_names_profile(f"ls {profile.parent}-alt", profile)


def test_other_bash_commands_pass(profile):
    assert not guard.command_names_profile("ls src && python -m pytest", profile)
    assert not guard.command_names_profile("ls src", None)


def run_hook(payload, profile, monkeypatch):
    monkeypatch.setattr(guard, "_profile_path", lambda: profile)
    stdin = io.StringIO(payload if isinstance(payload, str) else json.dumps(payload))
    stderr = io.StringIO()
    code = guard.profile_hook(stdin, stderr)
    return code, stderr.getvalue()


def test_hook_blocks_an_edit_of_the_profile(profile, monkeypatch):
    code, err = run_hook({"tool_name": "Edit", "tool_input": {"file_path": str(profile)}}, profile, monkeypatch)
    assert code == 2
    assert "Profil" in err


def test_hook_blocks_a_notebook_next_to_the_profile(profile, monkeypatch):
    payload = {"tool_name": "NotebookEdit", "tool_input": {"notebook_path": str(profile.parent / "n.ipynb")}}
    assert run_hook(payload, profile, monkeypatch)[0] == 2


def test_hook_allows_an_edit_in_the_repo(profile, monkeypatch, tmp_path):
    payload = {"tool_name": "Write", "tool_input": {"file_path": str(tmp_path / "repo" / "a.py")}}
    assert run_hook(payload, profile, monkeypatch) == (0, "")


def test_hook_allows_everything_without_profile_path(monkeypatch):
    payload = {"tool_name": "Edit", "tool_input": {"file_path": "C:/irgendwo/a.md"}}
    assert run_hook(payload, None, monkeypatch)[0] == 0


def test_hook_blocks_a_bash_command_naming_the_profile_folder(profile, monkeypatch):
    payload = {"tool_name": "Bash", "tool_input": {"command": f"echo x >> {profile}"}}
    code, err = run_hook(payload, profile, monkeypatch)
    assert code == 2 and "Profil" in err


def test_hook_allows_other_bash_commands(profile, monkeypatch):
    payload = {"tool_name": "PowerShell", "tool_input": {"command": "Get-ChildItem src"}}
    assert run_hook(payload, profile, monkeypatch)[0] == 0


def test_broken_input_blocks(profile, monkeypatch):
    code, err = run_hook("{kein json", profile, monkeypatch)
    assert code == 2
    assert "Schutz" in err


def test_a_crash_while_loading_the_config_blocks_a_write_tool(monkeypatch):
    def broken():
        raise RuntimeError("kaputt")

    monkeypatch.setattr(guard, "_profile_path", broken)
    stderr = io.StringIO()
    payload = json.dumps({"tool_name": "Write", "tool_input": {"file_path": "a.md"}})
    assert guard.profile_hook(io.StringIO(payload), stderr) == 2
    assert "kaputt" in stderr.getvalue()


def test_a_crash_on_a_shell_tool_reports_without_blocking(monkeypatch):
    def broken():
        raise RuntimeError("kaputt")

    monkeypatch.setattr(guard, "_profile_path", broken)
    stderr = io.StringIO()
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}})
    assert guard.profile_hook(io.StringIO(payload), stderr) == 1
    assert "kaputt" in stderr.getvalue()


def test_a_write_tool_without_a_path_blocks(profile, monkeypatch):
    assert run_hook({"tool_name": "Edit", "tool_input": {}}, profile, monkeypatch)[0] == 2


# Keeping personal data out of commits

EXAMPLES = Path(__file__).parent.parent / "private.example"

ENV = {
    "PROFILE_PATH": r"C:\Daten\Beispielprojekt\lebenslauf.md",
    "HOME_LOCATION": "Musterhausen",
    "HOME_LAT": "50.1234",
    "HOME_LON": "8.5678",
    "IMAP_HOST": "imap.beispielmail.test",
    "IMAP_USER": "kandidat@beispielmail.test",
    "IMAP_PASSWORD": "Geheim-Wort-42",
}


@pytest.fixture
def root(tmp_path):
    shutil.copytree(EXAMPLES, tmp_path / "private.example")
    private = tmp_path / "private"
    private.mkdir()
    (private / "exclusions.txt").write_text("# Kommentar\nPraktik\nVolontariat\n", encoding="utf-8")
    (private / "search_terms.txt").write_text("Gleisplaner\n-Schienenmeister\nIT\n", encoding="utf-8")
    (private / "companies.csv").write_text(
        "name,aliases,locations,career_url,ats_type,render_mode\n"
        "Fiktiv Werke GmbH,Fiktivgruppe|FW,Waldhausen/Bergdorf,https://karriere.fiktiv-werke.test/jobs,,\n",
        encoding="utf-8")
    return tmp_path


def make_cfg(cfg, root):
    return dataclasses.replace(cfg, root=root)


def values(cfg, root, env=ENV):
    notes = []
    found = guard.collect_values(make_cfg(cfg, root), env, notes)
    return {v.text: v for v in found}, notes


def test_values_come_from_every_source(cfg, root):
    found, _ = values(cfg, root)
    for text in [ENV["PROFILE_PATH"], "Musterhausen", "50.1234", "8.5678", "imap.beispielmail.test",
                 "kandidat@beispielmail.test", "Geheim-Wort-42", "Volontariat", "Gleisplaner",
                 "Fiktiv Werke GmbH", "Fiktivgruppe", "Waldhausen", "Bergdorf",
                 "https://karriere.fiktiv-werke.test/jobs"]:
        assert text in found, text


def test_profile_path_is_also_found_in_other_spellings(cfg, root):
    found, _ = values(cfg, root)
    for text in ["C:/Daten/Beispielprojekt/lebenslauf.md", "/c/Daten/Beispielprojekt/lebenslauf.md",
                 "C:/Daten/Beispielprojekt", "/c/Daten/Beispielprojekt"]:
        assert text in found, text


def test_a_switched_off_search_term_is_matched_without_its_dash(cfg, root):
    found, _ = values(cfg, root)
    assert "Schienenmeister" in found and "-Schienenmeister" not in found


def test_short_values_are_skipped(cfg, root):
    found, _ = values(cfg, root)
    assert "IT" not in found and "FW" not in found


def test_values_also_in_private_example_are_skipped(cfg, root):
    found, _ = values(cfg, root)
    assert "Praktik" not in found


def test_comments_in_private_example_do_not_make_a_value_generic(cfg, root):
    with (root / "private.example" / "exclusions.txt").open("a", encoding="utf-8") as f:
        f.write("# zum Beispiel kein Volontariat\n")
    found, _ = values(cfg, root)
    assert "Volontariat" in found


def test_a_company_list_saved_with_bom_is_read(cfg, root):
    path = root / "private" / "companies.csv"
    path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8-sig")
    found, _ = values(cfg, root)
    assert "Fiktiv Werke GmbH" in found


def test_allowlisted_values_are_skipped(cfg, root):
    (root / "private" / "allowed_terms.txt").write_text("# erlaubt\ngleisplaner\n", encoding="utf-8")
    found, _ = values(cfg, root)
    assert "Gleisplaner" not in found and "Volontariat" in found


def test_derived_terms_come_from_the_database(cfg, root):
    from jobsearch import db
    (root / "data").mkdir()
    conn = db.connect(root / "data" / "jobsearch.sqlite")
    with conn:
        conn.execute("INSERT INTO search_terms (term, origin) VALUES ('Trassenplaner', 'derived')")
    conn.close()
    found, _ = values(cfg, root)
    assert found["Trassenplaner"].kind == "abgeleiteter Suchbegriff"


def test_missing_sources_are_skipped_with_a_note(cfg, tmp_path):
    shutil.copytree(EXAMPLES, tmp_path / "private.example")
    found, notes = values(cfg, tmp_path, env={})
    assert found == {}
    assert any("exclusions.txt" in n for n in notes)
    assert any("Datenbank" in n for n in notes)


def scan(cfg, root, lines):
    found, _ = values(cfg, root)
    return guard.scan([("datei.py", i + 1, line) for i, line in enumerate(lines)], list(found.values()))


def test_a_hit_names_file_line_and_kind(cfg, root):
    hits = scan(cfg, root, ["x = 1", 'ORT = "musterhausen"'])
    assert [(h.file, h.line, h.value.kind) for h in hits] == [("datei.py", 2, ".env HOME_LOCATION")]


def test_only_whole_words_match(cfg, root):
    assert scan(cfg, root, ["Volontariatsstelle", "Gleisplanerin"]) == []
    assert len(scan(cfg, root, ["(Volontariat)", "gleisplaner, x"])) == 2


def test_coordinates_need_their_full_digits(cfg, root):
    assert scan(cfg, root, ["50.12345", "150.1234"]) == []
    assert len(scan(cfg, root, ["lat=50.1234"])) == 1


def test_the_password_is_masked_in_the_report(cfg, root):
    hits = scan(cfg, root, ["pw = 'Geheim-Wort-42'"])
    report = guard.format_hits(hits)
    assert "Geheim-Wort-42" not in report and "IMAP_PASSWORD" in report
    assert "Musterhausen" in guard.format_hits(scan(cfg, root, ["Musterhausen"]))


def test_staged_diff_yields_added_lines():
    diff = (
        "diff --git a/neu.txt b/neu.txt\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/neu.txt\n"
        "@@ -0,0 +1,2 @@\n"
        "+erste\n"
        "+zweite\n"
        "diff --git a/alt.py b/alt.py\n"
        "--- a/alt.py\n"
        "+++ b/alt.py\n"
        "@@ -10,2 +12,1 @@\n"
        "-weg\n"
        "+neu hier\n"
    )
    assert guard.added_lines(diff) == [("neu.txt", 1, "erste"), ("neu.txt", 2, "zweite"), ("alt.py", 12, "neu hier")]


def test_an_added_line_with_a_unicode_line_separator_stays_whole():
    diff = "diff --git a/a.txt b/a.txt\n--- a/a.txt\n+++ b/a.txt\n@@ -1,0 +2,1 @@\n+vorne\u2028Gleisplaner\n"
    assert guard.added_lines(diff) == [("a.txt", 2, "vorne\u2028Gleisplaner")]


def test_staged_lines_include_every_file_name_even_without_added_lines():
    assert guard.staged_lines("", ["leer.txt", "bild.png"]) == [("leer.txt", 0, "leer.txt"),
                                                                ("bild.png", 0, "bild.png")]


def test_git_runs_in_the_repository_of_the_hook_not_the_config_root(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(kwargs)
        return subprocess.CompletedProcess(cmd, 0, stdout="")

    monkeypatch.setattr(guard.subprocess, "run", fake_run)
    guard.staged_lines_from_git()
    assert calls and all("cwd" not in kw for kw in calls)


def test_commit_message_comment_lines_are_scanned_too():
    # git commit -m keeps lines starting with #, so they are checked like any other line
    assert guard.message_lines("Fix\n\n# Gleisplaner\n") == [
        ("Commit-Nachricht", 1, "Fix"), ("Commit-Nachricht", 2, ""), ("Commit-Nachricht", 3, "# Gleisplaner")]


def test_commit_message_below_the_scissors_is_ignored():
    text = "Fix\n# ------------------------ >8 ------------------------\n+Gleisplaner\n"
    assert guard.message_lines(text) == [("Commit-Nachricht", 1, "Fix")]


def test_init_points_git_at_the_tracked_hooks(tmp_path):
    (tmp_path / ".git").mkdir()
    calls = []
    message = guard.enable_commit_check(tmp_path, run=lambda cmd, **kw: calls.append((cmd, kw["cwd"])))
    assert calls == [(["git", "config", "core.hooksPath", ".githooks"], tmp_path)]
    assert "aktiv" in message


def test_init_outside_a_git_repository_does_nothing(tmp_path):
    message = guard.enable_commit_check(tmp_path, run=lambda *a, **kw: pytest.fail("no git call expected"))
    assert "Kein Git-Repository" in message
