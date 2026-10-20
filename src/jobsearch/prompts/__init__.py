"""Prompt texts, shipped as package data."""

from __future__ import annotations

import hashlib
from importlib import resources


def version(name: str) -> str:
    """A short hash of the prompt file, stored with results to tell prompt versions apart."""
    text = resources.files(__name__).joinpath(name).read_text(encoding="utf-8")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
