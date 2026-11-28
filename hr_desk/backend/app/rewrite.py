"""Turn a follow-up into one search question. A previous turn is sent to the rewrite."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from .config import CHAT_CONTEXT_BYTES, HISTORY_TURNS
from .textutil import token_set

_FOLLOW = re.compile(r"(?i)^(?:(?:and|also)\s+)?(?:what|how) about\s+(.+?)\s*\??$")
_FOLLOW_UP_WORDS = {"not", "again", "more", "else", "same", "also"}
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


def context_digest(turns: list[dict]) -> str:
    """Short id of the recent turns a follow-up was rewritten with. Empty when there are none."""
    if not turns:
        return ""
    packed = "\n".join(f"{turn.get('role', '')}:{turn.get('text', '')}" for turn in turns)
    return hashlib.sha256(packed.encode()).hexdigest()[:16]


def same_subject(left: str, right: str) -> bool:
    """The two questions name the same subject. Gift does not match mileage."""
    from .textutil import _stem, token_set

    left_stems = {_stem(token) for token in token_set(left)}
    right_stems = {_stem(token) for token in token_set(right)}
    return bool(left_stems) and left_stems == right_stems


def cache_question(question: str, turns: list[dict] | None = None) -> str:
    """The cache key: folded wording, plus the recent-turn id when there is one.

    "What's" and "What is" share a key. A shorter question, such as "gift limit", does not.
    """
    text = (question or "").strip().replace("\u2019", "'").replace("\u2018", "'")
    text = re.sub(r"(?i)\bwhat(?:'s|s)\b", "what is", text)
    text = re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()
    digest = context_digest(list(turns or []))
    return f"{text}\n{digest}" if digest else text


def conversation_text(turns: list[dict]) -> str:
    """The recent exchanges the answer may use to understand a follow-up."""
    lines = []
    for turn in turns or []:
        role = turn.get("role")
        text = (turn.get("text") or "").strip()
        if role == "user" and text:
            lines.append(f"Employee: {text}")
        elif role == "assistant" and text:
            # "Assistant:" is removed as a control line before the model sees the message.
            lines.append(f"Answer: {text}")
    return "\n".join(lines)


def _last_user_question(history: list[dict]) -> str:
    for turn in reversed(history):
        if turn["role"] == "user":
            return turn["text"]
    return ""


_BARE_YES = re.compile(r"(?i)^(?:yes|yeah|yep|sure|ok|okay)(?:\s*,?\s*please)?[?.!]*$")
_REFERRAL_FOLLOW = re.compile(
    r"(?i)^(?:(?:yes|yeah|yep|sure|ok|okay)\b\s*[,:-]?\s*)?(?:please\s+)?"
    r"(?:(?:can|could)\s+you\s+)?"
    r"(?:tell\s+me(?:\s+about)?|what\s+about|what\s+does|explain|describe|show\s+me)\s+"
    r"(?:(?:this|that|the|it)\s+)"
    r"(?P<kind>annexures?|sections?|points?|clauses?)"
    r"(?:\s+say)?\s*[?.!]*$"
)
_POINTER_ANNEXURE = re.compile(
    r"(?i)\b(?:please\s+refer\s+to|refer\s+to|see|defined\s+in|outlined\s+in|specified\s+in|"
    r"set\s+out\s+in|listed\s+in|shown\s+in|mentioned\s+in|as\s+per)\s+(?:the\s+)?annexure\s+([a-z])\b"
    r"|:\s*annexure\s+([a-z])\b"
)
_POINTER_SECTION = re.compile(
    r"(?i)\b(?:mentioned|described|specified|defined|set\s+out|covered|stated)\s+in\s+"
    r"(?:point|clause|section)\s+(\d+(?:\.\d+)*)"
    r"|\b(?:please\s+)?refer\s+to\s+(?:point|clause|section)\s+(\d+(?:\.\d+)*)"
    r"|\bsee\s+(?:point|clause|section)\s+(\d+(?:\.\d+)*)"
)


@dataclass(frozen=True)
class ReferralTarget:
    kind: str
    name: str
    policy_title: str = ""


def referral_request(question: str) -> str | None:
    """A follow-up that means the section or annexure the previous answer pointed to.

    "annexure" and "section" name which kind. "any" is a bare yes. None is a new question.
    """
    text = (question or "").strip()
    match = _REFERRAL_FOLLOW.fullmatch(text)
    if match:
        return "annexure" if match.group("kind").casefold().startswith("annexure") else "section"
    if _BARE_YES.fullmatch(text):
        return "any"
    return None


def pointers_in(text: str, titles: list[str], default_title: str = "") -> list[ReferralTarget]:
    """Annexure letters and section numbers a passage tells the reader to open."""
    found: list[ReferralTarget] = []
    seen: set[tuple[str, str, str]] = set()
    for pattern, kind in ((_POINTER_ANNEXURE, "annexure"), (_POINTER_SECTION, "section")):
        for match in pattern.finditer(text or ""):
            name = next(group for group in match.groups() if group)
            title = _policy_after(text, match.end(), titles) or default_title
            item = ReferralTarget(kind, name.upper() if kind == "annexure" else name, title)
            key = (item.kind, item.name.casefold(), item.policy_title.casefold())
            if key not in seen:
                seen.add(key)
                found.append(item)
    return found


def choose_pointers(targets: list[ReferralTarget], kind: str) -> list[ReferralTarget]:
    """Prefer the kind the employee named. A bare yes keeps every pointer."""
    if kind == "annexure":
        preferred = [target for target in targets if target.kind == "annexure"]
    elif kind == "section":
        preferred = [target for target in targets if target.kind == "section"]
    else:
        preferred = targets
    return preferred or list(targets)


def _policy_after(text: str, end: int, titles: list[str]) -> str:
    tail = (text or "")[end:end + 90]
    for title in sorted(titles, key=len, reverse=True):
        if len(title) < 4:
            continue
        if re.match(rf"(?i)\s*(?:of|in)\s+(?:the\s+)?{re.escape(title)}\b", tail):
            return title
    return ""


def topic_words(text: str) -> set[str]:
    """Words that name a subject. Follow-up fillers such as "why not" do not."""
    return {word for word in token_set(text) if word not in _FOLLOW_UP_WORDS}


def rewrite_question(question: str, history: list[dict]) -> Rewrite:
    """A previous turn goes to the cheap rewrite. The first question in a chat does not."""
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
        # "What about the allowance?" names a word already in the previous question.
        if token_set(topic) & token_set(prior):
            return Rewrite(topic, prior, "follow-up")
    if not prior:
        return Rewrite(text, "", "no-history")
    return Rewrite(text, prior, "model", needs_model=True)
