import subprocess
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

import pytest

from jobsearch.daily import run_daily
from jobsearch.scheduler import TASK_NAME, RunLock, TaskError, install_task, run_scheduled, task_xml, uninstall_task

from .test_daily import RULES, FakeClient

TODAY = date(2026, 10, 15)


class FakeDaily:
    """Stands in for the Daily run: records a run like the real one does."""

    def __init__(self, conn, ok=True):
        self.conn, self.ok, self.calls = conn, ok, 0

    def __call__(self):
        self.calls += 1
        with self.conn:
            self.conn.execute("INSERT INTO runs (kind, started_at, ok) VALUES ('daily', ?, ?)",
                              (f"{TODAY}T08:00:00", int(self.ok)))
        return 0 if self.ok else 1


def schedule(conn, cfg, daily, **kw):
    kw.setdefault("network_ok", lambda: True)
    kw.setdefault("toast", lambda title, body: None)
    kw.setdefault("sleep", lambda s: None)
    return run_scheduled(conn, cfg, daily=daily, today=TODAY, **kw)


def test_second_logon_on_the_same_day_does_nothing(conn, cfg):
    daily = FakeDaily(conn)
    assert schedule(conn, cfg, daily) == "ran"
    assert schedule(conn, cfg, daily) == "done today"
    assert daily.calls == 1


def test_a_run_with_errors_also_counts_as_done(conn, cfg):
    daily = FakeDaily(conn, ok=False)
    assert schedule(conn, cfg, daily) == "ran"
    assert schedule(conn, cfg, daily) == "done today"
    assert daily.calls == 1


def test_no_network_retries_with_backoff_then_gives_up_with_a_toast(conn, cfg):
    daily, toasts, sleeps = FakeDaily(conn), [], []
    result = schedule(conn, cfg, daily, network_ok=lambda: False,
                      toast=lambda title, body: toasts.append(body), sleep=sleeps.append)
    assert result == "offline"
    assert daily.calls == 0
    assert sleeps == [30, 60, 120]
    assert "Kein Netz" in toasts[0]


def test_network_that_comes_up_late_still_runs(conn, cfg):
    answers = iter([False, False, True])
    daily = FakeDaily(conn)
    assert schedule(conn, cfg, daily, network_ok=lambda: next(answers)) == "ran"
    assert daily.calls == 1


def test_a_run_still_going_blocks_a_second_one(conn, cfg):
    daily = FakeDaily(conn)
    with RunLock(cfg.data_dir / "run.lock") as held:
        assert held
        assert schedule(conn, cfg, daily) == "already running"
    assert daily.calls == 0
    assert schedule(conn, cfg, daily) == "ran"  # released afterwards


def test_four_day_gap_is_caught_up(conn, cfg):
    with conn:
        conn.execute("INSERT INTO runs (kind, started_at, ok) VALUES ('daily', '2026-10-11T08:00:00', 1)")
    client = FakeClient(radius=[], nationwide=[])
    daily = lambda: run_daily(conn, cfg, client, [], RULES, ["Ingenieur"], today=TODAY)
    assert schedule(conn, cfg, daily) == "ran"
    assert client.windows == [5]  # the four missed days plus the day of the last run


NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


def test_task_starts_at_logon_with_delay_as_the_current_user(tmp_path):
    root = ET.fromstring(task_xml(Path(r"C:\x\.venv\Scripts\pythonw.exe"), tmp_path, r"PC\musteruser"))
    trigger = root.find("t:Triggers/t:LogonTrigger", NS)
    assert trigger.find("t:Delay", NS).text == "PT5M"
    assert trigger.find("t:UserId", NS).text == r"PC\musteruser"
    principal = root.find("t:Principals/t:Principal", NS)
    assert principal.find("t:LogonType", NS).text == "InteractiveToken"  # the Claude login lives in the user session
    action = root.find("t:Actions/t:Exec", NS)
    assert action.find("t:Command", NS).text.endswith("pythonw.exe")
    assert action.find("t:Arguments", NS).text == "-m jobsearch run"
    assert action.find("t:WorkingDirectory", NS).text == str(tmp_path)
    assert root.find("t:Settings/t:DisallowStartIfOnBatteries", NS).text == "false"


def test_install_and_uninstall_call_schtasks(tmp_path):
    calls = []

    def runner(cmd, **kw):
        calls.append(cmd)
        if "/XML" in cmd:
            xml = Path(cmd[cmd.index("/XML") + 1]).read_text(encoding="utf-16")
            assert "LogonTrigger" in xml
        return subprocess.CompletedProcess(cmd, 0, "", "")

    install_task(tmp_path, runner=runner)  # the test interpreter is a venv with pythonw.exe beside it
    uninstall_task(runner=runner)
    assert calls[0][:4] == ["schtasks", "/Create", "/TN", TASK_NAME]
    assert calls[1] == ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"]


def test_schtasks_failure_is_reported_in_german(tmp_path):
    def runner(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, "", "ERROR: Access is denied.")

    with pytest.raises(TaskError, match="Aufgabe konnte nicht angelegt werden.*Access is denied"):
        install_task(tmp_path, runner=runner)
