"""Extract rent amounts and match location keywords from English housing posts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# Common English rent patterns: $1200, 1200/mo, Rs 25,000, ₹25000, 1.2k, etc.
# Auto-approve smoke test: comment-only change (no logic edits).
RENT_PATTERNS = [
    re.compile(
        r"(?:rs\.?|inr|₹)\s*([0-9]{1,3}(?:,[0-9]{2,3})+|[0-9]+(?:\.\d+)?)\s*(k|l|lac|lakh)?",
        re.IGNORECASE,
    ),
    re.compile(
        r"\$\s*([0-9]{1,3}(?:,[0-9]{3})+|[0-9]+(?:\.\d+)?)\s*(k)?",
        re.IGNORECASE,
    ),
    re.compile(
        r"([0-9]{1,3}(?:,[0-9]{2,3})+|[0-9]+(?:\.\d+)?)\s*(k|l|lac|lakh)?\s*"
        r"(?:/|\s)?\s*(?:mo(?:nth)?|pm|per\s*month|pcm|rent)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:rent|budget|price)\s*(?:is|:|-|=)?\s*(?:rs\.?|inr|₹|\$)?\s*"
        r"([0-9]{1,3}(?:,[0-9]{2,3})+|[0-9]+(?:\.\d+)?)\s*(k|l|lac|lakh)?",
        re.IGNORECASE,
    ),
]


@dataclass
class ExtractedRent:
    amount: float
    raw: str


def _to_amount(number: str, suffix: Optional[str]) -> float:
    value = float(number.replace(",", ""))
    if not suffix:
        return value
    s = suffix.lower()
    if s == "k":
        return value * 1_000
    if s in ("l", "lac", "lakh"):
        return value * 100_000
    return value


def extract_rent(text: str) -> Optional[ExtractedRent]:
    if not text:
        return None
    for pattern in RENT_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        number = match.group(1)
        suffix = match.group(2) if match.lastindex and match.lastindex >= 2 else None
        try:
            amount = _to_amount(number, suffix)
        except ValueError:
            continue
        if amount <= 0:
            continue
        return ExtractedRent(amount=amount, raw=match.group(0).strip())
    return None


def find_locations(text: str, locations: list[str]) -> list[str]:
    if not text or not locations:
        return []
    lower = text.lower()
    found = []
    for loc in locations:
        loc = loc.strip()
        if not loc:
            continue
        if loc.lower() in lower:
            found.append(loc)
    return found


def matches_keywords(text: str, keywords: list[str], require_all: bool = False) -> bool:
    if not keywords:
        return True
    if not text:
        return False
    lower = text.lower()
    hits = [kw.strip().lower() in lower for kw in keywords if kw.strip()]
    if not hits:
        return True
    return all(hits) if require_all else any(hits)


def matches_sender(
    sender_name: str | None,
    sender_username: str | None,
    query: str,
) -> bool:
    """Case-insensitive substring match against display name or @username."""
    q = (query or "").strip().lower()
    if not q:
        return True
    name = (sender_name or "").lower()
    username = (sender_username or "").lower()
    return q in name or q in username


def passes_rent_filter(
    rent: Optional[ExtractedRent],
    min_rent: Optional[float],
    max_rent: Optional[float],
    require_rent: bool,
) -> bool:
    if rent is None:
        return not require_rent
    if min_rent is not None and rent.amount < min_rent:
        return False
    if max_rent is not None and rent.amount > max_rent:
        return False
    return True
