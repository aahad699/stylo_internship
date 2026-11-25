"""Token helpers shared by search, embeddings, and number checks."""

from __future__ import annotations

import re

STOP = {
    "a",
    "an",
    "the",
    "is",
    "are",
    "was",
    "were",
    "do",
    "does",
    "did",
    "i",
    "me",
    "my",
    "we",
    "our",
    "you",
    "your",
    "how",
    "many",
    "what",
    "when",
    "where",
    "who",
    "why",
    "can",
    "get",
    "of",
    "to",
    "in",
    "on",
    "for",
    "and",
    "or",
    "please",
    "about",
    "this",
    "that",
    "with",
    "from",
    "each",
    "per",
    "it",
    "be",
    "if",
    "as",
    "at",
    "by",
    "tell",
    "stylo",
    "policy",
    "policies",
}


def token_list(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return [word for word in words if word not in STOP and len(word) > 1]


def token_set(text: str) -> set[str]:
    return set(token_list(text))


def keyword_score(query: str, passage: str) -> float:
    wanted = token_set(query)
    if not wanted:
        return 0.0
    return len(matched_tokens(wanted, passage)) / len(wanted)


def matched_tokens(wanted: set[str], text: str) -> set[str]:
    """The wanted words that the text uses, allowing another word form or a one-letter slip."""
    found = token_set(text)
    stems = {_stem(token) for token in found}
    found_by_length: dict[int, list[str]] = {}
    for token in found:
        found_by_length.setdefault(len(token), []).append(token)
    return {token for token in wanted
            if token in found or _stem(token) in stems or _near_token(token, found_by_length)}


def _stem(word: str) -> str:
    """gifts and gift, cancelled and cancellations, accepted and accept share a stem."""
    for suffix in ("ations", "ation", "ings", "ing", "ions", "ion", "ed", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def _near_token(token: str, found_by_length: dict[int, list[str]]) -> bool:
    """A one-letter slip such as summart or retial, or swapped letters such as feul, still matches the policy word."""
    if len(token) < 4:
        return False
    if len(token) == 4:
        return any(_swapped(token, other) for other in found_by_length.get(4, []))
    nearby: list[str] = []
    for length in (len(token) - 1, len(token), len(token) + 1):
        nearby.extend(found_by_length.get(length, []))
    return any(_edit_distance(token, other) <= 1 or _swapped(token, other) for other in nearby)


def _swapped(left: str, right: str) -> bool:
    if len(left) != len(right) or left == right:
        return False
    diff = [index for index, (a, b) in enumerate(zip(left, right)) if a != b]
    return len(diff) == 2 and diff[1] == diff[0] + 1 and left[diff[0]] == right[diff[1]] and left[diff[1]] == right[diff[0]]


def _edit_distance(left: str, right: str) -> int:
    if abs(len(left) - len(right)) > 1:
        return 2
    previous = list(range(len(right) + 1))
    for i, char in enumerate(left, start=1):
        current = [i]
        for j, other in enumerate(right, start=1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (char != other)))
        previous = current
    return previous[-1]


def normalize_for_rules(text: str) -> str:
    cleaned = text.lower().replace("\u2019", "'").replace("\u2018", "'")
    cleaned = re.sub(r"'s\b", " ", cleaned)
    cleaned = re.sub(r"[^a-z0-9]+", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()
