"""Conservative checks for text copied from a WeChat article window."""

from __future__ import annotations

import re
from pathlib import Path


def meaningful_body(body: str) -> bool:
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    non_urls = [line for line in lines if not re.fullmatch(r"https?://\S+", line)]
    return len("".join(non_urls)) >= 80


def verified_body_file(root: Path, relative_path: str | None) -> bool:
    """A database `ok` flag alone is not proof that the body was captured."""
    if not relative_path:
        return False
    path = (root / relative_path).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        return False
    try:
        return meaningful_body(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError):
        return False
