"""The scheduled run: started by Task Scheduler at every logon, does the Daily run once per day."""

from __future__ import annotations

import getpass
import logging
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Callable
from xml.sax.saxutils import escape

from .config import Config

log = logging.getLogger(__name__)

RETRY_SECONDS = (30, 60, 120)
NETWORK_PROBE = ("rest.arbeitsagentur.de", 443)


class RunLock:
    """An OS lock on a file in data/. The OS drops it when the process dies, so a crash
    never leaves a stale lock behind. Entering returns False if another run holds it."""

    def __init__(self, path: Path):
        self.path = path
        self.file = None

    def __enter__(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = open(self.path, "a+")
        try:
            _lock(self.file)
        except OSError:
            self.file.close()
            self.file = None
            return False
        return True

    def __exit__(self, *exc) -> None:
        if self.file:
            _unlock(self.file)
            self.file.close()
            self.file = None


if os.name == "nt":
    import msvcrt

    def _lock(f) -> None:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock(f) -> None:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _lock(f) -> None:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(f) -> None:
        fcntl.flock(f, fcntl.LOCK_UN)


def ran_today(conn: sqlite3.Connection, today: date) -> bool:
    """A Daily run finished today, with or without errors. A lasting error (wrong mail password)
    must not repeat the run and its AI call at every logon. Errors are in that day's Digest,
    and the next day's remote window still reaches back to the last run without errors."""
    row = conn.execute(
        "SELECT 1 FROM runs WHERE kind = 'daily' AND ok IS NOT NULL AND substr(started_at, 1, 10) = ?",
        (today.isoformat(),),
    ).fetchone()
    return row is not None


def run_scheduled(
    conn: sqlite3.Connection,
    cfg: Config,
    *,
    daily: Callable[[], int],
    network_ok: Callable[[], bool],
    toast: Callable[[str, str], None],
    sleep: Callable[[float], None],
    today: date | None = None,
) -> str:
    """Run the Daily run unless one already succeeded today or is still going. Returns what happened."""
    with RunLock(cfg.data_dir / "run.lock") as held:
        if not held:
            log.info("Another run holds the lock, nothing to do")
            return "already running"
        return _run(conn, daily, network_ok, toast, sleep, today or date.today())


def _run(conn, daily, network_ok, toast, sleep, today: date) -> str:
    if ran_today(conn, today):
        log.info("Daily run already done today, nothing to do")
        return "done today"
    if not wait_for_network(network_ok, sleep):
        log.warning("No network after %d retries, giving up", len(RETRY_SECONDS))
        toast("JobSearch", "Kein Netz, der Lauf wird bei der nächsten Anmeldung nachgeholt.")
        return "offline"
    daily()
    return "ran"


def network_ok() -> bool:
    try:
        socket.create_connection(NETWORK_PROBE, timeout=10).close()
        return True
    except OSError:
        return False


def wait_for_network(network_ok: Callable[[], bool], sleep: Callable[[float], None]) -> bool:
    """Right after logon the network is often not up yet: retry with growing pauses."""
    for pause in RETRY_SECONDS:
        if network_ok():
            return True
        log.info("Network not up, retrying in %ds", pause)
        sleep(pause)
    return network_ok()


TASK_NAME = "JobSearch"


class TaskError(Exception):
    """Task Scheduler refused a change. The message is German, for the user."""


def task_xml(pythonw: Path, workdir: Path, user: str) -> str:
    """Task Scheduler definition: at logon of `user`, after 5 minutes, in the user's session
    (so `claude -p` finds the login), without a console window."""
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>JobSearch: Daily run einmal am Tag nach der Anmeldung</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{escape(user)}</UserId>
      <Delay>PT5M</Delay>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{escape(user)}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <ExecutionTimeLimit>PT2H</ExecutionTimeLimit>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(str(pythonw))}</Command>
      <Arguments>-m jobsearch run</Arguments>
      <WorkingDirectory>{escape(str(workdir))}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def _schtasks(args: list[str], what: str, runner) -> None:
    # schtasks writes in the console (OEM) code page, which matters for German messages.
    proc = runner(["schtasks", *args], capture_output=True, text=True,
                  encoding="oem" if os.name == "nt" else None, errors="replace")
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise TaskError(f"Aufgabe konnte nicht {what} werden: {detail}")


def install_task(workdir: Path, runner=subprocess.run) -> None:
    """Register (or replace) the logon task for the current user."""
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.exists():
        raise TaskError(f"pythonw.exe nicht gefunden neben {sys.executable}. Aus der venv heraus ausführen.")
    domain = os.environ.get("USERDOMAIN")
    user = f"{domain}\\{getpass.getuser()}" if domain else getpass.getuser()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "task.xml"
        path.write_text(task_xml(pythonw, workdir, user), encoding="utf-16")
        _schtasks(["/Create", "/TN", TASK_NAME, "/XML", str(path), "/F"], "angelegt", runner)


def uninstall_task(runner=subprocess.run) -> None:
    _schtasks(["/Delete", "/TN", TASK_NAME, "/F"], "entfernt", runner)
