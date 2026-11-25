"""Split independent policy requests without splitting a single comparison or field."""

from __future__ import annotations

from dataclasses import dataclass
import re

from .aliases import expand_abbreviations
from .models import Passage

MAX_TOPICS = 8

_BOUNDARY = re.compile(r"(?<!\d),(?!\d)|;|\n|(?<=\?)\s+")
_AND = re.compile(r"\s+(?:and|also)\s+", re.I)
_UNSPLIT = re.compile(r"\b(?:compare|versus|difference between|but not|rather than|instead of)\b", re.I)
_LIST_PREFIX = re.compile(r"^(?:please\s+)?(?:(?:give|tell|show)\s+me(?:\s+about)?|list|explain)\s+", re.I)
_NEW_REQUEST = re.compile(r"^(?:what|who|where|when|why|how|can|do|is|are|give|show|tell)\b", re.I)
_PERSONAL_END = re.compile(r"\s+for\s+(?:me|myself)[?.!]*$", re.I)
_TOPIC_PATTERNS = {
    "tour": r"\bdaily tour allowance\b|\btour allowance\b",
    "travel": r"\b(?:business travel|travel policy|travel approval|authorization of travel)\b",
    "fuel": r"\b(?:fuel|mileage)\b",
    "medical": r"\b(?:medical|health insurance|takaful|hospital|maternity|plan [a-e])\b",
    "hotel": r"\b(?:hotel|lodging|accommodation|self.stay)\b",
    "form": r"\b(?:travel (?:claim|expense|request) form|incident report|summary report)\b",
    "daily": r"\b(?:daily allowance|per diem|DA)\b",
    "provident": r"\bprovident fund\b",
    "salary": r"\b(?:advance salary|salary advance)\b",
    "conduct": r"\b(?:code of conduct|dress code|ceo message|message from the ceo)\b",
    "corrective": r"\b(?:corrective action|inquiry process|show cause|first investigation report)\b",
    "leave": r"\b(?:leave|holidays?)\b",
}


@dataclass(frozen=True)
class Topic:
    label: str
    query: str


@dataclass
class TopicEvidence:
    number: int
    question: str
    passages: list[Passage]


def topic_kind(text: str) -> str:
    text = expand_abbreviations(text)
    for kind, pattern in _TOPIC_PATTERNS.items():
        if re.search(pattern, text, re.I):
            return kind
    return ""


def split_topics(question: str) -> tuple[list[Topic], bool]:
    parts = [part.strip(" \t,;.-") for part in _BOUNDARY.split(question) if part.strip(" \t,;.-")]
    if len(parts) == 2 and re.fullmatch(r"(?:under|in|for)\s+(?:the\s+)?(?:medical\s+)?plan\s+[a-e]", parts[0], re.I):
        return [Topic(question, question)], False
    if len(parts) == 1 and not _UNSPLIT.search(question):
        segments = _AND.split(parts[0])
        kinds = [topic_kind(part) for part in segments]
        if len(segments) > 1 and (all(kinds) and len(set(kinds)) == len(kinds)
                                  or all(_NEW_REQUEST.match(part) for part in segments)):
            parts = segments
    if len(parts) < 2 or (_UNSPLIT.search(question) and not all(topic_kind(part) for part in parts)):
        return [Topic(question, question)], False
    if not (all(topic_kind(part) or _NEW_REQUEST.match(part) for part in parts)
            or (len(parts) >= 3 and all(1 <= len(part.split()) <= 9 for part in parts))):
        return [Topic(question, question)], False
    personal_list = bool(_PERSONAL_END.search(question))
    result = []
    seen = set()
    for index, part in enumerate(parts):
        query = re.sub(r"^(?:and|also)\s+", "", part, flags=re.I).strip()
        if index == 0:
            query = _LIST_PREFIX.sub("", query).strip()
        if not query:
            continue
        label = _PERSONAL_END.sub("", query).strip(" ?.!")
        key = label.casefold()
        if not label or key in seen:
            continue
        seen.add(key)
        result.append(Topic(label, f"{label} for me" if personal_list else query))
    if len(result) < 2:
        return [Topic(question, question)], False
    return result[:MAX_TOPICS], len(result) > MAX_TOPICS


def needs_topic_split(question: str) -> bool:
    """Only spend a model call when there is a clear cue for separate requests."""
    return not _UNSPLIT.search(question) and bool(re.search(
        r"\b(?:separately|also|and then|in addition)\b.*\b(?:can you|could you|what|how|tell|show|give|explain)\b"
        r"|\?\s+\S.*\?", question, re.I | re.S,
    ))


def model_topics(queries: list[str], question: str) -> tuple[list[Topic], bool]:
    if not isinstance(queries, list) or not all(isinstance(item, str) and 0 < len(item.strip()) <= 300 for item in queries):
        return [], False
    # Reuse the original personal scope and deduplication after the model suggests boundaries.
    values = list(dict.fromkeys(item.strip(" ,;.\n\t") for item in queries if item.strip()))
    personal = bool(_PERSONAL_END.search(question))
    result = [Topic(_PERSONAL_END.sub("", value).strip(" ?.!"), value if not personal or _PERSONAL_END.search(value)
                    else value.rstrip(" ?.!") + " for me") for value in values]
    return result[:MAX_TOPICS], len(result) > MAX_TOPICS
