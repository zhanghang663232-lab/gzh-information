"""Prioritize new-looking cards without using titles as article identities."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol


class TitledCard(Protocol):
    title: str


@dataclass(frozen=True, slots=True)
class CandidateQueue:
    # Every input index remains present exactly once, including repeated titles.
    order: tuple[int, ...]
    # These candidates still need their actual URLs checked. They are not done.
    deferred_duplicate: frozenset[int]


def _title_hint(value: object) -> str:
    # OCR whitespace may change between observations; punctuation and wording
    # remain significant. This hint only affects scheduling, never deduplication.
    return "".join(value.split()) if isinstance(value, str) else ""


def prioritize_candidates(
    cards: Sequence[TitledCard],
    completed_rows: Iterable[Mapping[str, object]],
    *,
    preferred_order: Sequence[int] | None = None,
) -> CandidateQueue:
    """Put unseen titles first, preserving preferred order within both groups.

    Engagement counts intentionally do not participate: reading a saved article
    can change its counts. A same-title repost is retained for later URL checking.
    Callers must rebuild the queue after observing a changed list viewport.
    """
    count = len(cards)
    order = tuple(range(count)) if preferred_order is None else tuple(preferred_order)
    if (len(order) != count or any(type(index) is not int for index in order)
            or set(order) != set(range(count))):
        raise ValueError("候选顺序必须完整包含当前页面的每个整数索引，且不能重复")

    known_titles = {
        hint for row in completed_rows
        if (hint := _title_hint(row.get("title")))
    }
    deferred = frozenset(
        index for index, card in enumerate(cards)
        if (hint := _title_hint(card.title)) and hint in known_titles
    )
    return CandidateQueue(
        order=tuple(index for index in order if index not in deferred)
        + tuple(index for index in order if index in deferred),
        deferred_duplicate=deferred,
    )
