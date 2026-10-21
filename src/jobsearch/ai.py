"""The only module that calls the AI: Claude Code headless (claude -p) on the subscription.

Every call gets a JSON schema, which the CLI enforces, so the answer arrives as
parsed JSON in `structured_output`. The caller can add its own check on top. An
answer that fails either check is asked for once more, then the call gives up.

Flags keep each call small and inert: no tools (except web search for the one-time
career page setup), no MCP servers, no skills, a
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
from datetime import datetime

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


# What each call is for, so the AI usage can be told apart per purpose.
PURPOSES = frozenset({"scoring", "career_extract", "career_setup", "search_terms", "check_injection",
                      "weekly_report", "skill_groups"})


@dataclass(frozen=True)
class CallUsage:
    """One claude -p process. Token and cost fields are None when no answer envelope arrived."""
    purpose: str
    model: str
    attempt: int
    outcome: str  # ok | invalid | error | timeout
    started_at: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_creation_tokens: int | None = None
    thinking_tokens: int | None = None
    web_search_requests: int | None = None
    cost_usd: float | None = None  # list price through the API, nothing is billed
    duration_ms: int | None = None
    num_turns: int | None = None


UsageRecorder = Callable[[CallUsage], None]
Runner = Callable[..., subprocess.CompletedProcess]
# Started from pythonw by Task Scheduler, a console program would otherwise open a window.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def command(model: str, schema: dict, system: str, tools: tuple[str, ...] = ()) -> list[str]:
    exe = shutil.which("claude")
    if not exe:
        raise AiError("Der Befehl 'claude' wurde nicht gefunden. Ist Claude Code installiert und im PATH?")
    return [
        exe, "-p",
        "--model", model,
        "--output-format", "json",
        "--json-schema", json.dumps(schema),
        "--system-prompt", system,
        "--tools", ",".join(tools),
        *(["--allowedTools", ",".join(tools)] if tools else []),
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


def _envelope(proc: subprocess.CompletedProcess) -> dict | None:
    try:
        envelope = json.loads(proc.stdout)
    except (json.JSONDecodeError, TypeError):
        return None
    return envelope if isinstance(envelope, dict) else None


def _parse(proc: subprocess.CompletedProcess, envelope: dict | None) -> tuple[dict, str]:
    """The structured answer and the model name, or AiError for failures that a retry won't fix."""
    if envelope is None:
        raise _failure(proc.stderr or proc.stdout or f"Exit-Code {proc.returncode}")
    if proc.returncode != 0 or envelope.get("is_error"):
        raise _failure(str(envelope.get("result") or proc.stderr or envelope.get("subtype") or ""))
    models = list((envelope.get("modelUsage") or {}).keys())
    return envelope.get("structured_output"), (models[0] if models else "")


def _usage(envelope: dict | None, *, purpose: str, model: str, attempt: int, outcome: str,
           started_at: str) -> CallUsage:
    """Usage from modelUsage, summed over the models that answered."""
    base = CallUsage(purpose, model, attempt, outcome, started_at)
    if envelope is None:
        return base
    per_model = envelope.get("modelUsage") or {}

    def total(key: str):
        values = [m[key] for m in per_model.values() if isinstance(m, dict) and m.get(key) is not None]
        return sum(values) if values else None

    return CallUsage(
        purpose, ", ".join(per_model) or model, attempt, outcome, started_at,
        input_tokens=total("inputTokens"), output_tokens=total("outputTokens"),
        cache_read_tokens=total("cacheReadInputTokens"), cache_creation_tokens=total("cacheCreationInputTokens"),
        thinking_tokens=total("thinkingTokens"), web_search_requests=total("webSearchRequests"),
        cost_usd=total("costUSD"), duration_ms=envelope.get("duration_ms"), num_turns=envelope.get("num_turns"),
    )


def _record(recorder: UsageRecorder | None, usage: CallUsage) -> None:
    if recorder is None:
        return
    try:
        recorder(usage)
    except Exception:  # measuring must never cost the answer
        log.warning("Could not record AI usage", exc_info=True)


def ask(
    prompt: str,
    *,
    purpose: str,
    model: str,
    schema: dict,
    system: str,
    check: Callable[[dict], None] | None = None,
    tools: tuple[str, ...] = (),
    timeout: int = TIMEOUT_SECONDS,
    runner: Runner = subprocess.run,
    usage_recorder: UsageRecorder | None = None,
) -> Answer:
    """Send the prompt on stdin and return the checked answer. Raises AiError.

    `usage_recorder` gets the AI usage of every attempt, whatever its outcome."""
    if purpose not in PURPOSES:
        raise ValueError(f"unknown purpose {purpose!r}")
    cmd = command(model, schema, system, tools)
    problem = ""
    for attempt in (1, 2):
        started_at = datetime.now().isoformat(timespec="seconds")
        usage = dict(purpose=purpose, model=model, attempt=attempt, started_at=started_at)
        try:
            proc = runner(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                          timeout=timeout, cwd=tempfile.gettempdir(), creationflags=NO_WINDOW)
        except subprocess.TimeoutExpired:
            _record(usage_recorder, _usage(None, outcome="timeout", **usage))
            raise AiError(f"Claude hat nach {timeout // 60} Minuten nicht geantwortet.") from None
        envelope = _envelope(proc)
        if envelope is not None:  # the raw numbers next to the stored row, for checking by hand
            log.info("AI usage (%s, attempt %d): %s", purpose, attempt, json.dumps(
                {k: envelope.get(k) for k in ("modelUsage", "total_cost_usd", "duration_ms", "num_turns")}))
        try:
            data, used_model = _parse(proc, envelope)
        except AiError:
            _record(usage_recorder, _usage(envelope, outcome="error", **usage))
            raise
        try:
            if not isinstance(data, dict):
                raise InvalidAnswer("keine strukturierte Antwort")
            if check:
                check(data)
        except InvalidAnswer as e:
            _record(usage_recorder, _usage(envelope, outcome="invalid", **usage))
            problem = str(e)
            log.warning("Invalid AI answer (attempt %d): %s", attempt, problem)
        else:
            _record(usage_recorder, _usage(envelope, outcome="ok", **usage))
            return Answer(data, used_model or model)
    raise AiError(f"Claude hat zweimal keine brauchbare Antwort geliefert: {problem}")
