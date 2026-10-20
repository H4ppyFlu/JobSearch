"""Guardrails that enforce rules CLAUDE.md only writes down.

`python -m jobsearch.guard profile` is a Claude Code PreToolUse hook: it blocks any
file write in the Profile's folder, and, as a best-effort string check, any shell
command that names that folder. The check is a heuristic for shell commands, since
a command can build a path in ways a string search misses.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import TextIO

CASE_INSENSITIVE = os.name == "nt"

WRITE_TOOLS = {"Edit": "file_path", "Write": "file_path", "MultiEdit": "file_path", "NotebookEdit": "notebook_path"}
SHELL_TOOLS = {"Bash", "PowerShell"}

BLOCKED = 2


def _key(path: Path) -> str:
    text = path.as_posix()
    return text.lower() if CASE_INSENSITIVE else text


def touches_profile(file_path: str, profile: Path | None) -> bool:
    """True when the path is the Profile, its folder, or anything inside that folder."""
    if profile is None:
        return False
    folder = _key(profile.resolve().parent)
    target = _key(Path(file_path).resolve())
    return target == folder or target.startswith(folder.rstrip("/") + "/")


def _spellings(folder: Path) -> set[str]:
    """The ways a shell command can spell the folder, after normalizing slashes."""
    posix = _key(folder)
    spellings = {posix}
    if len(posix) > 1 and posix[1] == ":":
        drive, rest = posix[0].lower(), posix[2:]
        spellings |= {f"/{drive}{rest}", f"/mnt/{drive}{rest}", f"/cygdrive/{drive}{rest}"}
    return spellings


def _expand(command: str) -> str:
    """Fill in the variables a command may use to reach the folder: $X, ${X}, %X% (Windows),
    PowerShell's $env:X and a leading ~ in any word."""
    command = re.sub(r"\$env:(\w+)", lambda m: os.environ.get(m.group(1), m.group(0)), command, flags=re.I)
    command = re.sub(r"(?<![\w/\\])~(?=[/\\]|$|\s)", lambda m: str(Path.home()), command)
    return os.path.expandvars(command)


def command_names_profile(command: str, profile: Path | None) -> bool:
    """Best effort: True when the command text contains the Profile's folder."""
    if profile is None:
        return False
    text = _expand(command).replace("\\", "/")
    while "//" in text:
        text = text.replace("//", "/")
    if CASE_INSENSITIVE:
        text = text.lower()
    return any(spelling in text for spelling in _spellings(profile.resolve().parent))


def _profile_path() -> Path | None:
    from . import config

    return config.load().profile_path


def profile_hook(stdin: TextIO, stderr: TextIO) -> int:
    """Exit code for one PreToolUse call: 0 allows it, 2 blocks it with the reason on stderr.
    Fails closed: if the check itself breaks, the call is blocked rather than let through. Shell
    commands are the exception, since their check is only best effort: they get exit 1, which Claude
    Code shows as an error without blocking."""
    tool = ""
    try:
        call = json.loads(stdin.read())
        tool = call.get("tool_name", "")
        tool_input = call.get("tool_input") or {}
        profile = _profile_path()
        if tool in WRITE_TOOLS:
            path = tool_input.get(WRITE_TOOLS[tool])
            if not path:
                raise ValueError(f"{tool} ohne Dateipfad")
            if touches_profile(path, profile):
                stderr.write(f"Blockiert: {path} liegt im Ordner des Profils. Das Profil gehört zu einem anderen "
                             "Projekt und wird hier nur gelesen.\n")
                return BLOCKED
        elif tool in SHELL_TOOLS and command_names_profile(tool_input.get("command", ""), profile):
            stderr.write("Blockiert: Der Befehl nennt den Ordner des Profils. Das Profil wird hier nur gelesen; "
                         "zum Lesen das Read-Tool nutzen.\n")
            return BLOCKED
        return 0
    except Exception as exc:  # noqa: BLE001 - a broken guard must block, never allow silently
        if tool in SHELL_TOOLS:
            stderr.write(f"Profil-Schutz fehlgeschlagen ({exc}). Der Befehl wurde nicht geprüft.\n")
            return 1
        stderr.write(f"Blockiert: Der Profil-Schutz ist fehlgeschlagen ({exc}). Ohne funktionierenden Schutz "
                     "wird nicht geschrieben.\n")
        return BLOCKED


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    # Claude Code reads hook output as UTF-8; the Windows console code page would garble umlauts
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    if args == ["profile"]:
        return profile_hook(sys.stdin, sys.stderr)
    sys.stderr.write("Aufruf: python -m jobsearch.guard profile\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
