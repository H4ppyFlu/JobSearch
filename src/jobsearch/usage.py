"""AI usage: what one claude -p call used, and how calls add up.

`ai.ask()` reports a CallUsage per attempt, `db.usage_recorder()` stores it in `ai_calls`, and
the eval, the Digest and `status` sum the stored rows with Usage. The fields are named here once:
a new one needs a CallUsage field, a column in the migration, its place in the envelope and, if
it is shown, a label.

cost_usd is the API equivalent: the list price the call would cost through the API. The AI runs
on the subscription, so it is neither a real cost nor a share of the quota.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

# What each call is for, with its German label.
PURPOSES = {
    "scoring": "Bewertung",
    "rescoring": "Neubewertung",
    "career_extract": "Karriereseiten auslesen",
    "career_setup": "Karriereseiten suchen",
    "search_terms": "Suchbegriffe ableiten",
    "check_injection": "Injection-Check",
    "weekly_report": "Wochenbericht",
    "skill_groups": "Skill-Gruppen",
}
OUTCOMES = ("ok", "invalid", "error", "timeout")


@dataclass(frozen=True)
class CallUsage:
    """One claude -p process. Token and cost fields are None when the envelope did not report them."""
    purpose: str  # a key of PURPOSES
    model: str
    attempt: int
    outcome: str  # one of OUTCOMES
    started_at: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_creation_tokens: int | None = None
    thinking_tokens: int | None = None
    web_search_requests: int | None = None
    cost_usd: float | None = None
    duration_ms: int | None = None
    num_turns: int | None = None


# Where each counted field is in the CLI's answer envelope: its key per model in modelUsage, and
# its path in the call-wide part, used when modelUsage is missing.
ENVELOPE = {
    "input_tokens": ("inputTokens", ("usage", "input_tokens")),
    "output_tokens": ("outputTokens", ("usage", "output_tokens")),
    "cache_read_tokens": ("cacheReadInputTokens", ("usage", "cache_read_input_tokens")),
    "cache_creation_tokens": ("cacheCreationInputTokens", ("usage", "cache_creation_input_tokens")),
    "thinking_tokens": ("thinkingTokens", ("usage", "output_tokens_details", "thinking_tokens")),
    "web_search_requests": ("webSearchRequests", ("usage", "server_tool_use", "web_search_requests")),
    "cost_usd": ("costUSD", ("total_cost_usd",)),
}


def _path(envelope: dict, path: tuple[str, ...]):
    value = envelope
    for key in path:
        value = value.get(key) if isinstance(value, dict) else None
    return value


def from_envelope(envelope: dict | None, *, purpose: str, model: str, attempt: int, outcome: str,
                  started_at: str) -> CallUsage:
    """Usage from modelUsage, summed over the models that answered, else from the call-wide fields."""
    if envelope is None:
        return CallUsage(purpose, model, attempt, outcome, started_at)
    per_model = [m for m in (envelope.get("modelUsage") or {}).values() if isinstance(m, dict)]
    counted = {}
    for name, (key, path) in ENVELOPE.items():
        values = [m[key] for m in per_model if m.get(key) is not None]
        counted[name] = sum(values) if values else (None if per_model else _path(envelope, path))
    return CallUsage(purpose, ", ".join(envelope.get("modelUsage") or {}) or model, attempt, outcome, started_at,
                     duration_ms=envelope.get("duration_ms"), num_turns=envelope.get("num_turns"), **counted)


# What the reports show, in this order. Token kinds weigh very differently, so they are never summed.
SHOWN = {"input_tokens": "Eingabe", "output_tokens": "Ausgabe", "cache_read_tokens": "Cache gelesen",
         "cache_creation_tokens": "Cache geschrieben", "cost_usd": "API-Gegenwert"}
HEADER = ["Aufrufe", *SHOWN.values()]


@dataclass
class Usage:
    """Stored calls added up."""
    calls: int = 0
    totals: dict[str, float] = field(default_factory=lambda: dict.fromkeys(SHOWN, 0))

    def add(self, row: sqlite3.Row) -> None:
        self.calls += 1
        for name in SHOWN:
            self.totals[name] += row[name] or 0

    def __getitem__(self, name: str) -> float:
        return self.totals[name]

    @property
    def cost_usd(self) -> float:
        return self.totals["cost_usd"]


def tokens(n: float) -> str:
    return f"{round(n):,}".replace(",", ".")


def money(x: float | None) -> str:
    return "-" if x is None else f"{x:.3f} $"


def values(u: Usage) -> list[str]:
    """The cells of one row under HEADER."""
    return [str(u.calls)] + [money(u[name]) if name == "cost_usd" else tokens(u[name]) for name in SHOWN]


def summary(u: Usage) -> str:
    """One line for the Digest and status."""
    counts = ", ".join(f"{label} {tokens(u[name])}" for name, label in SHOWN.items() if name != "cost_usd")
    return f"{u.calls} Aufrufe, API-Gegenwert {money(u.cost_usd)} (Tokens: {counts})"
