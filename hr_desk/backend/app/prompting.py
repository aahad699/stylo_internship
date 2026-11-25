"""Build the prompts the answer model sees. Internal ids stay out."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date

from .config import BACKEND_ROOT, CANARY
from .models import Passage
from .replies import RATE_MISSING, REFUSAL, TOPIC_MISSING, UNKNOWN
from .topics import TopicEvidence

_PROMPT_PATH = BACKEND_ROOT / "prompts" / "system.md"
logger = logging.getLogger("hr_desk")
_verified_context: ContextVar[tuple[date, str] | None] = ContextVar("verified_prompt_context", default=None)
_CONTROL_LINE = re.compile(
    r"^\s*(?:[#>*-]+\s*)?(?:"
    r"(?:system|developer|assistant|verified employee designation|internal marker)\s*:|"
    r"(?:ignore|disregard|override|forget)\b.*\b(?:instructions|rules|guardrails|prompt)\b|"
    r"</?(?:system|developer|assistant|policy_evidence|conversation_context)\b[^>]*>|"
    r"\[(?:/?INST|/?SYSTEM|/?DEVELOPER)\]|<<\s*/?SYS\s*>>)", re.I,
)


def prompt_source() -> bytes:
    return _PROMPT_PATH.read_bytes()


def prompt_hash(source: bytes) -> str:
    return hashlib.sha256(source).hexdigest()


def system_prompt(today: date, designation: str = "", *, source: bytes | None = None) -> str:
    raw = (prompt_source() if source is None else source).decode("utf-8")
    if CANARY not in raw:
        raise RuntimeError("The system prompt is missing its leak marker.")
    for key, reply in {
        "unknown_reply": UNKNOWN, "refusal_reply": REFUSAL,
        "topic_missing": TOPIC_MISSING.format(topic="<Topic>"), "rate_missing": RATE_MISSING,
        "fuel_missing": TOPIC_MISSING.format(topic="Fuel allowance"),
    }.items():
        raw = raw.replace("{{" + key + "}}", reply)
    return _render_context(raw, today, designation)


def _render_context(raw: str, today: date, designation: str) -> str:
    if not isinstance(today, date):
        logger.error("Cannot render the HR prompt without the server date.")
        raise RuntimeError("Missing server date")
    role = (designation or "").strip()
    if not role:
        logger.error("Verified employee designation is missing; using unknown in the answer prompt.")
        role = "unknown"
    rendered = raw.replace("{{today}}", today.isoformat()).replace("{{designation}}", role)
    if re.search(r"{{[^{}]+}}", rendered):
        logger.error("An answer prompt placeholder could not be rendered.")
        raise RuntimeError("Unresolved answer prompt placeholder")
    return rendered


@contextmanager
def model_context(today: date, designation: str):
    token = _verified_context.set((today, designation))
    try:
        yield
    finally:
        _verified_context.reset(token)


def chat_system_prompt(prompt: str) -> str:
    context = _verified_context.get()
    if context is None:
        logger.error("Chat call has no verified employee context; using server date and unknown designation.")
        context = (date.today(), "unknown")
    if not (prompt.startswith("You are Stylo's HR assistant.") and "Verified employee designation:" in prompt):
        prompt = "Today's date: {{today}}\nVerified employee designation: {{designation}}\n\n" + prompt
    return _render_context(prompt, *context)


def user_message(
    question: str,
    background: str,
    passages: list[Passage],
) -> str:
    return json.dumps({
        "question": question.strip(),
        "conversation_context": model_data(background),
        "policy_evidence": [_evidence(passage) for passage in passages],
    }, ensure_ascii=False)


def grouped_user_message(topics: list[TopicEvidence]) -> str:
    pool: dict[str, tuple[int, Passage]] = {}
    groups = []
    for topic in topics:
        references = [
            pool.setdefault(p.id, (len(pool) + 1, p))[0]
            for p in topic.passages
        ]
        groups.append({"number": topic.number, "question": topic.question, "allowed_evidence": references})
    return json.dumps({
        "topics": groups,
        "policy_evidence": {number: _evidence(passage) for number, passage in pool.values()},
    }, ensure_ascii=False)


def _evidence(passage: Passage) -> dict:
    return {
        "policy_section": re.sub(r"\bannexure\s+[a-z](?:\s*-\s*\d+)?\s*[-:]?\s*", "",
                                 model_data(passage.section_label), flags=re.I),
        "effective_from": passage.effective_from.isoformat(),
        "effective_to": passage.effective_to.isoformat() if passage.effective_to else None,
        "text": model_data(passage.body),
    }


def model_data(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not _CONTROL_LINE.match(line)).strip()
