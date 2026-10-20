"""Guardrails that enforce rules CLAUDE.md only writes down.

`python -m jobsearch.guard profile` is a Claude Code PreToolUse hook: it blocks any
file write in the Profile's folder, and, as a best-effort string check, any shell
command that names that folder. The check is a heuristic for shell commands, since
a command can build a path in ways a string search misses.

`python -m jobsearch.guard staged` (git pre-commit) and `... message <file>` (git
commit-msg) refuse a commit whose added lines, file names or message contain a
personal value from .env, private/ or the derived Search terms.
"""

from __future__ import annotations

import csv
import json
import os
import re
import sqlite3
import subprocess
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, TextIO

if TYPE_CHECKING:
    from .config import Config

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
    # The folder name must end there, so a sibling such as "<folder>2" does not count
    return any(re.search(re.escape(spelling) + r"(?![\w.-])", text) for spelling in _spellings(profile.resolve().parent))


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


# Personal values in commits

ENV_KEYS = ("PROFILE_PATH", "HOME_LOCATION", "HOME_LAT", "HOME_LON", "IMAP_HOST", "IMAP_USER", "IMAP_PASSWORD")
SECRET_KEYS = {"IMAP_PASSWORD"}
MIN_LENGTH = 4
MESSAGE = "Commit-Nachricht"
SCISSORS = "# ------------------------ >8 ------------------------"


@dataclass(frozen=True)
class Value:
    """One personal value and where it comes from. Secret values never appear in a report."""
    text: str
    kind: str
    secret: bool = False


@dataclass(frozen=True)
class Hit:
    file: str
    line: int  # 0 means the file name itself
    value: Value


def _word(text: str) -> re.Pattern:
    return re.compile(r"(?<!\w)" + re.escape(text) + r"(?!\w)", re.IGNORECASE)


def _company_values(path: Path) -> list[str]:
    texts = []
    # utf-8-sig: a list saved from a spreadsheet often starts with a BOM, which would hide the "name" column
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            texts.append(row.get("name") or "")
            texts += (row.get("aliases") or "").split("|")
            texts += re.split(r"[/|,]", row.get("locations") or "")
            texts.append(row.get("career_url") or "")
    return texts


def _derived_terms(db_path: Path) -> list[str]:
    from .search_terms import derived_terms

    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return derived_terms(conn)
    finally:
        conn.close()


def _example_text(root: Path) -> str:
    """The data lines of private.example/, without comments, which are prose and not examples."""
    from .config import read_list_file

    folder = root / "private.example"
    if not folder.is_dir():
        return ""
    return "\n".join(line for f in sorted(folder.iterdir()) if f.is_file() for line in read_list_file(f))


def _git_bash_form(path: str) -> str | None:
    """C:/x/y as Git Bash writes it: /c/x/y."""
    return f"/{path[0].lower()}{path[2:]}" if len(path) > 1 and path[1] == ":" else None


def collect_values(cfg: Config, env: Mapping[str, str], notes: list[str]) -> list[Value]:
    """Every personal value worth looking for, read now from env, private/ and the database.
    A missing source adds a note and is skipped. Values shorter than MIN_LENGTH, values that
    private.example/ uses too, and values in private/allowed_terms.txt are left out."""
    from .config import ConfigError, read_list_file
    from .search_terms import switched_off

    raw: list[Value] = []
    for key in ENV_KEYS:
        text = (env.get(key) or "").strip()
        if not text:
            notes.append(f".env {key}")
            continue
        raw.append(Value(text, f".env {key}", key in SECRET_KEYS))
        if key == "PROFILE_PATH":
            posix = text.replace("\\", "/")
            folder = posix.rsplit("/", 1)[0]
            spellings = [posix, folder, folder.replace("/", "\\"), _git_bash_form(posix), _git_bash_form(folder)]
            raw += [Value(t, ".env PROFILE_PATH") for t in spellings if t]
    for path in (cfg.exclusions_path, cfg.search_terms_path):
        try:
            lines = read_list_file(path)
        except ConfigError:
            notes.append(f"private/{path.name}")
            continue
        if path == cfg.search_terms_path:
            lines = [line for line in lines if not line.startswith("-")] + switched_off(lines)
        raw += [Value(line, f"private/{path.name}") for line in lines]
    if cfg.companies_path.exists():
        raw += [Value(t, "private/companies.csv") for t in _company_values(cfg.companies_path)]
    else:
        notes.append("private/companies.csv")
    try:
        if not cfg.db_path.exists():
            raise sqlite3.Error("missing")
        raw += [Value(t, "abgeleiteter Suchbegriff") for t in _derived_terms(cfg.db_path)]
    except sqlite3.Error:
        notes.append("Datenbank (abgeleitete Suchbegriffe)")

    allowed_path = cfg.private_dir / "allowed_terms.txt"
    allowed = {a.lower() for a in read_list_file(allowed_path)} if allowed_path.exists() else set()
    examples = _example_text(cfg.root)
    values: dict[str, Value] = {}
    for value in raw:
        text = value.text.strip()
        key = text.lower()
        if len(text) < MIN_LENGTH or key in values or key in allowed:
            continue
        if not value.secret and _word(text).search(examples):
            continue
        values[key] = Value(text, value.kind, value.secret)
    return list(values.values())


def scan(lines: Iterable[tuple[str, int, str]], values: list[Value]) -> list[Hit]:
    """Every whole-word, case-insensitive occurrence of a value, one Hit per line and value."""
    patterns = [(value, _word(value.text)) for value in values]
    return [Hit(file, number, value) for file, number, text in lines
            for value, pattern in patterns if pattern.search(text)]


def added_lines(diff: str) -> list[tuple[str, int, str]]:
    """The added lines of a `git diff -U0`, with their new line numbers."""
    lines: list[tuple[str, int, str]] = []
    file, number, in_header = None, 0, False
    # split("\n"), not splitlines(): that one also breaks on characters such as U+2028 inside a line
    for row in diff.split("\n"):
        if row.startswith("diff --git "):
            file, in_header = None, True
        elif in_header and row.startswith("+++ "):
            target = row[4:]
            file = target[2:] if target.startswith("b/") else None
        elif row.startswith("@@"):
            in_header = False
            match = re.search(r"\+(\d+)", row)
            number = int(match.group(1)) if match else 0
        elif not in_header and file and row.startswith("+"):
            lines.append((file, number, row[1:]))
            number += 1
    return lines


def staged_lines(diff: str, names: list[str]) -> list[tuple[str, int, str]]:
    """Every staged file name (line 0), also of empty and binary files, then the added lines."""
    return [(name, 0, name) for name in names] + added_lines(diff)


def message_lines(text: str) -> list[tuple[str, int, str]]:
    """The lines of a commit message up to the scissors line of `git commit -v`. Lines starting
    with # stay in: `git commit -m` keeps them, and the hook cannot tell how git will clean up."""
    lines = []
    for number, line in enumerate(text.splitlines(), 1):
        if line.startswith(SCISSORS):
            break
        lines.append((MESSAGE, number, line))
    return lines


def format_hits(hits: list[Hit]) -> str:
    rows = ["Commit abgelehnt: persönliche Daten gefunden."]
    for hit in hits:
        where = f"{hit.file} (Dateiname)" if hit.line == 0 else f"{hit.file}:{hit.line}"
        shown = "(Wert verborgen)" if hit.value.secret else f'"{hit.value.text}"'
        rows.append(f"  {where}  {hit.value.kind}  {shown}")
    rows.append("Entfernen oder durch ein erfundenes Beispiel ersetzen. Ein bewusst akzeptierter Begriff kommt in "
                "private/allowed_terms.txt; 'git commit --no-verify' übergeht die Prüfung.")
    return "\n".join(rows)


def _git_diff(*args: str) -> str:
    # No cwd: git starts a hook in the repository being committed, which may be a worktree
    result = subprocess.run(["git", "-c", "core.quotepath=false", "diff", "--cached", "--no-renames", *args],
                            capture_output=True, encoding="utf-8", errors="replace", check=True)
    return result.stdout


def staged_lines_from_git() -> list[tuple[str, int, str]]:
    names = [n for n in _git_diff("--name-only", "--diff-filter=d", "-z").split("\0") if n]
    return staged_lines(_git_diff("-U0", "--no-color", "--no-ext-diff"), names)


def commit_check(message_file: str | None, stderr: TextIO) -> int:
    """Exit code for a git hook: 0 lets the commit through, 1 refuses it. Without a message
    file it checks the staged changes. Fails closed: a broken check refuses the commit."""
    try:
        from . import config

        cfg = config.load()
        notes: list[str] = []
        values = collect_values(cfg, os.environ, notes)
        if message_file is None:
            lines = staged_lines_from_git()
            if notes:
                stderr.write("Hinweis: nicht geprüft, weil nicht vorhanden: " + ", ".join(notes) + "\n")
        else:
            lines = message_lines(Path(message_file).read_text(encoding="utf-8", errors="replace"))
        hits = scan(lines, values)
        if hits:
            stderr.write(format_hits(hits) + "\n")
            return 1
        return 0
    except Exception as exc:  # noqa: BLE001 - a broken check must refuse, never pass silently
        stderr.write(f"Commit abgelehnt: Die Prüfung auf persönliche Daten ist fehlgeschlagen ({exc}). "
                     "'git commit --no-verify' übergeht sie bewusst.\n")
        return 1


def enable_commit_check(root: Path, run=subprocess.run) -> str:
    """Point git at the tracked .githooks/ folder, since git does not version hooks itself.
    Returns a German line for the user."""
    if not (root / ".git").exists():
        return "Kein Git-Repository, Commit-Prüfung nicht eingerichtet."
    try:
        run(["git", "config", "core.hooksPath", ".githooks"], cwd=root, check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        return f"Commit-Prüfung konnte nicht eingerichtet werden ({exc}). Von Hand: git config core.hooksPath .githooks"
    return "Commit-Prüfung auf persönliche Daten ist aktiv (git config core.hooksPath .githooks)."


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    # Claude Code reads hook output as UTF-8; the Windows console code page would garble umlauts
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    if args == ["profile"]:
        return profile_hook(sys.stdin, sys.stderr)
    if args == ["staged"]:
        return commit_check(None, sys.stderr)
    if len(args) == 2 and args[0] == "message":
        return commit_check(args[1], sys.stderr)
    sys.stderr.write("Aufruf: python -m jobsearch.guard profile | staged | message <datei>\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
