import io
import json

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
