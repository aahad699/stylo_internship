"""Turn a follow-up into one search question, and drop history on a topic change."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .config import CHAT_CONTEXT_BYTES, HISTORY_TURNS
from .textutil import token_set

_FOLLOW = re.compile(r"(?i)^(?:(?:and|also)\s+)?(?:what|how) about\s+(.+?)\s*\??$")
_CONTINUE = re.compile(r"(?i)^(and|also|same|again|more|else|that|this|it|its|why|how)\b")
_FOLLOW_UP_WORDS = {"not", "again", "more", "else", "same", "also"}
_ATTRIBUTE_WORDS = {"maximum", "minimum", "max", "min", "amount", "limit", "rate", "percentage", "deadline",
                    "duration", "period", "much", "long", "days", "months", "eligible", "eligibility"}
_RECALL = re.compile(
    r"(?i)^(?:(?:what(?:'s|s|\s+(?:is|was))|(?:show|repeat|tell) me)\s+)?"
    r"(?:my|the)\s+(first|second|third|last|previous|\d{1,3}(?:st|nd|rd|th)?)\s+(?:saved\s+)?question[?.!]*$"
)
_REFERENCE = re.compile(
    r"(?i)\b(?:regarding(?:\s+to)?|related\s+to|about|on|for|to|with)\s+"
    r"(?:this|that)(?:\s+(?:matter|topic|issue|policy|rule|requirement|section|case|process|benefit|plan))?\s*[?.!]*$"
)
_IMPLIED_REFERENCE = re.compile(
    r"(?i)^(?:(?:are there|what are|what about)\s+)?(?:the\s+|any\s+)?"
    r"(?:restrictions?|exceptions?|exclusions?|conditions?|limits?|requirements?)[?.!]*$"
)
_PRONOUN_REFERENCE = re.compile(
    r"(?i)^(?:(?:and|also)\s+)?(?:who|what|when|where|why|how|does|do|can|is|are)\s+"
    r"(?:[\w'-]+\s+){0,5}(?:it|this|that)(?:\s+[\w'-]+)?[?.!]*$"
)


def question_recall(question: str) -> int | None:
    match = _RECALL.fullmatch(question.strip().replace("\u2019", "'"))
    if not match:
        return None
    ordinal = match[1].lower()
    named = {"first": 1, "second": 2, "third": 3, "last": -1, "previous": -1}
    return named[ordinal] if ordinal in named else int(ordinal.rstrip("stndrh"))


def contextual_query(question: str) -> str | None:
    """Keep the new request while removing its reference to the previous subject."""
    text = question.strip()
    match = _REFERENCE.search(text)
    if match:
        return text[:match.start()].strip()
    if _IMPLIED_REFERENCE.fullmatch(text):
        return text.rstrip("?.!")
    if _PRONOUN_REFERENCE.fullmatch(text):
        return re.sub(r"(?i)^(?:and|also)\s+|\b(?:it|this|that)\b", "", text).strip(" ?!.")
    return None


@dataclass(frozen=True)
class Rewrite:
    search_query: str
    background: str
    note: str
    needs_model: bool = False


def recent_history(history: list[dict], limit: int = HISTORY_TURNS) -> list[dict]:
    cleaned = []
    remaining = CHAT_CONTEXT_BYTES
    for turn in reversed((history or [])[-limit:]):
        role = turn.get("role")
        text = (turn.get("text") or "").strip()
        if role in {"user", "assistant"} and text and remaining > 32:
            # Leave space for a recent employee question even after a long answer.
            limit_bytes = min(remaining - 16, 420 if role == "assistant" else 500)
            fragment = text.encode("utf-8")[:limit_bytes]
            cleaned.append({"role": role, "text": fragment.decode("utf-8", errors="ignore")})
            remaining -= len(fragment) + 16
    return list(reversed(cleaned))


def _last_user_question(history: list[dict]) -> str:
    for turn in reversed(history):
        if turn["role"] == "user":
            return turn["text"]
    return ""


def topic_words(text: str) -> set[str]:
    """Words that name a subject. Follow-up fillers such as "why not" do not."""
    return {word for word in token_set(text) if word not in _FOLLOW_UP_WORDS}


def rewrite_question(question: str, history: list[dict]) -> Rewrite:
    """Rules cover the common cases. needs_model means a cheap model should rewrite."""
    text = question.strip()
    turns = recent_history(history)
    prior = _last_user_question(turns)
    follow = _FOLLOW.match(text)
    if follow and prior:
        topic = follow.group(1).strip()
        travel_prior = re.search(
            r"\b(?:daily allowance|fuel|mileage|hotel|lodging|travel allowance|tour allowance|DA|TA)\b",
            prior,
            re.I,
        )
        if travel_prior and re.search(r"\b(?:hotel|lodging|fuel|mileage|allowance)\b", topic, re.I):
            return Rewrite(f"what is my {topic}", prior, "follow-up")
        shared = token_set(topic) & token_set(prior)
        if shared:
            return Rewrite(topic, prior, "follow-up")
        return Rewrite(topic, "", "topic-change")
    words = re.findall(r"[A-Za-z0-9']+", text)
    # "and for managers" and "why not" still need the previous question.
    # "ceo" and "message from ceo" name their own subject, so history stays out.
    if prior and (_CONTINUE.match(text) or topic_words(text) <= _ATTRIBUTE_WORDS):
        # "What is the maximum amount?" asks about a property of the previous subject without naming it.
        return Rewrite(text, prior, "model", needs_model=True)
    if len(words) >= 4 or topic_words(text):
        note = "topic-change" if prior else "no-history"
        return Rewrite(text, "", note)
    if not prior:
        return Rewrite(text, "", "no-history")
    return Rewrite(text, prior, "model", needs_model=True)
