"""Shared lightweight text matching for templates and review checks."""

import re
from difflib import SequenceMatcher

_STOPWORDS = {
    "a",
    "an",
    "and",
    "for",
    "in",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
}


def normalize_title(text: str) -> str:
    """Normalize punctuation, case, and whitespace for matching."""
    lowered = text.lower().replace("&", " and ")
    words = re.findall(r"[a-z0-9]+", lowered)
    return " ".join(word for word in words if word not in _STOPWORDS)


def title_tokens(text: str) -> set[str]:
    """Return meaningful normalized tokens."""
    return set(normalize_title(text).split())


def titles_match(first: str, second: str) -> bool:
    """Return True when two section titles likely describe the same section."""
    left = normalize_title(first)
    right = normalize_title(second)
    if not left or not right:
        return False
    if left == right or left in right or right in left:
        return True
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    overlap = len(left_tokens & right_tokens)
    smaller = max(1, min(len(left_tokens), len(right_tokens)))
    if overlap / smaller >= 0.6:
        return True
    return SequenceMatcher(None, left, right).ratio() >= 0.72


def mentions_any(text: str, terms: set[str]) -> bool:
    """Return whether normalized text contains any of the given terms."""
    normalized = f" {normalize_title(text)} "
    return any(f" {term} " in normalized for term in terms)
