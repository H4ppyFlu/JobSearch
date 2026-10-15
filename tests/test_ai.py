import json
import subprocess

import pytest

from jobsearch import ai


@pytest.fixture(autouse=True)
def claude_on_path(monkeypatch):
    monkeypatch.setattr(ai.shutil, "which", lambda name: "claude")


def envelope(structured=None, is_error=False, result="", model="claude-sonnet-test"):
    return json.dumps({"type": "result", "is_error": is_error, "result": result,
                       "structured_output": structured, "modelUsage": {model: {}}})


class FakeRunner:
    """Plays back prepared claude -p outputs and records the calls."""

    def __init__(self, *outputs):
        self.outputs = list(outputs)
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append((cmd, kwargs))
        stdout, code, stderr = self.outputs.pop(0)
        return subprocess.CompletedProcess(cmd, code, stdout, stderr)


def ask(runner, check=None):
    return ai.ask("prompt", model="sonnet", schema={"type": "object"}, system="sys", check=check, runner=runner)


def test_returns_structured_answer_and_model():
    runner = FakeRunner((envelope({"ok": True}), 0, ""))
    answer = ask(runner)
    assert answer.data == {"ok": True}
    assert answer.model == "claude-sonnet-test"
    cmd, kwargs = runner.calls[0]
    assert kwargs["input"] == "prompt"
    assert cmd[cmd.index("--tools") + 1] == ""
    assert "--bare" not in cmd  # would ignore the subscription login


def test_invalid_answer_is_retried_once():
    def check(data):
        if not data.get("ok"):
            raise ai.InvalidAnswer("nicht ok")
    runner = FakeRunner((envelope({"ok": False}), 0, ""), (envelope({"ok": True}), 0, ""))
    assert ask(runner, check).data == {"ok": True}
    assert len(runner.calls) == 2


def test_gives_up_after_second_invalid_answer():
    runner = FakeRunner((envelope(None), 0, ""), (envelope(None), 0, ""))
    with pytest.raises(ai.AiError, match="zweimal"):
        ask(runner)


@pytest.mark.parametrize(
    "output, expected",
    [
        ((envelope(is_error=True, result="Claude usage limit reached. Resets at 5pm."), 1, ""), "Kontingent"),
        (("", 1, "Invalid API key · Please run /login"), "nicht angemeldet"),
        (("", 1, "something broke"), "fehlgeschlagen: something broke"),
    ],
)
def test_failures_are_not_retried_and_explained(output, expected):
    runner = FakeRunner(output)
    with pytest.raises(ai.AiError, match=expected):
        ask(runner)
    assert len(runner.calls) == 1


def test_missing_cli(monkeypatch):
    monkeypatch.setattr(ai.shutil, "which", lambda name: None)
    with pytest.raises(ai.AiError, match="nicht gefunden"):
        ask(FakeRunner())
