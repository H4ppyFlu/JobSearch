"""The only module that calls the AI: Claude Code headless (claude -p) on the subscription.

Every call gets a JSON schema, which the CLI enforces, so the answer arrives as
parsed JSON in `structured_output`. The caller can add its own check on top. An
answer that fails either check is asked for once more, then the call gives up.

Flags keep each call small and inert: no tools, no MCP servers, no skills, a
short system prompt instead of the coding one, no saved session, and a neutral
working directory so no project CLAUDE.md is loaded. --bare is not usable, since
it ignores the subscription login and accepts only an API key.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass

log = logging.getLogger(__name__)

TIMEOUT_SECONDS = 900

_QUOTA = re.compile(r"usage limit|rate limit|limit reached|quota|too many requests|\b429\b", re.IGNORECASE)
_LOGIN = re.compile(r"log ?in|logged out|authenticat|unauthori[sz]ed|\b401\b|invalid api key", re.IGNORECASE)


class AiError(Exception):
    """The AI step failed. The message is German and meant for the user."""


class InvalidAnswer(ValueError):
    """Raised by a caller's check when an answer is well-formed JSON but unusable."""


@dataclass(frozen=True)
class Answer:
    data: dict
    model: str  # the model that actually answered, e.g. claude-sonnet-5-5


Runner = Callable[..., subprocess.CompletedProcess]


def command(model: str, schema: dict, system: str) -> list[str]:
    exe = shutil.which("claude")
    if not exe:
        raise AiError("Der Befehl 'claude' wurde nicht gefunden. Ist Claude Code installiert und im PATH?")
    return [
        exe, "-p",
        "--model", model,
        "--output-format", "json",
        "--json-schema", json.dumps(schema),
        "--system-prompt", system,
        "--tools", "",
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--no-session-persistence",
    ]


def _failure(text: str) -> AiError:
    text = text.strip()[:500]
    if _QUOTA.search(text):
        return AiError(f"Claude-Kontingent erschöpft, die Jobs warten auf den nächsten Lauf. ({text})")
    if _LOGIN.search(text):
        return AiError(f"Claude ist nicht angemeldet. Einmal 'claude' im Terminal starten und anmelden. ({text})")
    return AiError(f"Claude-Aufruf fehlgeschlagen: {text or 'keine Ausgabe'}")


def _parse(proc: subprocess.CompletedProcess) -> tuple[dict, str]:
    """The structured answer and the model name, or AiError for failures that a retry won't fix."""
    try:
        envelope = json.loads(proc.stdout)
    except (json.JSONDecodeError, TypeError):
        raise _failure(proc.stderr or proc.stdout or f"Exit-Code {proc.returncode}") from None
    if proc.returncode != 0 or envelope.get("is_error"):
        raise _failure(str(envelope.get("result") or proc.stderr or envelope.get("subtype") or ""))
    models = list((envelope.get("modelUsage") or {}).keys())
    return envelope.get("structured_output"), (models[0] if models else "")


def ask(
    prompt: str,
    *,
    model: str,
    schema: dict,
    system: str,
    check: Callable[[dict], None] | None = None,
    runner: Runner = subprocess.run,
) -> Answer:
    """Send the prompt on stdin and return the checked answer. Raises AiError."""
    cmd = command(model, schema, system)
    problem = ""
    for attempt in (1, 2):
        try:
            proc = runner(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                          timeout=TIMEOUT_SECONDS, cwd=tempfile.gettempdir())
        except subprocess.TimeoutExpired:
            raise AiError(f"Claude hat nach {TIMEOUT_SECONDS // 60} Minuten nicht geantwortet.") from None
        data, used_model = _parse(proc)
        try:
            if not isinstance(data, dict):
                raise InvalidAnswer("keine strukturierte Antwort")
            if check:
                check(data)
            return Answer(data, used_model or model)
        except InvalidAnswer as e:
            problem = str(e)
            log.warning("Invalid AI answer (attempt %d): %s", attempt, problem)
    raise AiError(f"Claude hat zweimal keine brauchbare Antwort geliefert: {problem}")
