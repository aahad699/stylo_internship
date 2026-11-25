"""Stop unsafe questions before search or the answer model."""

from __future__ import annotations

import re #regular expressions
from dataclasses import dataclass

from .config import EMPTY_QUESTION, MAX_USER_CHARS, TOO_LONG
from .replies import REFUSAL
from .textutil import normalize_for_rules

_JAILBREAKS = (
    re.compile(r"\bignore (?:all |any |the |previous |your )*(?:instructions|rules|guardrails|prompt)\b"),
    re.compile(
        r"\b(?:disregard|forget|override) (?:all |any |the |your )*(?:instructions|rules|guardrails|prompt)\b"
    ),
    re.compile(
        r"\b(?:reveal|print|show|repeat|leak) (?:me )?(?:your |the )?(?:system prompt|hidden instructions|instructions|hidden prompt|prompt)\b"
    ),
    re.compile(r"\byou are now\b"),
    re.compile(r"\bdeveloper mode\b"),
    re.compile(r"\bjailbreak\b"),
    re.compile(r"\bdo anything now\b"),
    re.compile(r"\bdan mode\b"),
)

_PAY = (
    re.compile(r"\bceo (salary|pay|compensation|package)\b"),
    re.compile(r"\bchief executive (officer )?(salary|pay|compensation|package)\b"),
    re.compile(r"\b(salary|pay|compensation|package) of the (ceo|chief executive)\b"),
    re.compile(r"\bhow much (does|is) the (ceo|chief executive)\b"),
    re.compile(r"\bwhat does the (ceo|chief executive) (earn|make|get paid)\b"),
    re.compile(r"\b(founder|owner) (salary|pay|compensation|package)\b"),
)

_NAMED_PAY = (
    re.compile(r"\b[A-Z][a-z]{2,}'s (salary|pay|compensation)\b(?!\s+(?:advance|policy|bands?))"),
    re.compile(r"\b(?i:how much does) [A-Z][a-z]{2,} (?i:earn|make|get paid)\b"),
)
_CLAIMED_ACCESS = re.compile(
    r"\b(?:i(?: am|['’]m)|my designation is|act as|pretend (?:i am|to be))\s+(?:the\s+|an?\s+)?"
    r"(?:ceo|chief executive(?: officer)?|admin|administrator)\b", re.I,
)
_PAY_RECORD = re.compile(
    r"\b(?:name|employee name|employee)\s*:\s*[^\n|;]+?\s*"
    r"(?:[|;\n]\s*|\.\s+|\s+)(?:base\s+)?(?:salary|pay|compensation)\s*:\s*(?:Rs\.?\s*|PKR\s*)?\d",
    re.I,
)
_NAMED_EARNINGS = re.compile(
    r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3}\s+"
    r"(?:earns|is paid|receives (?:a salary of|pay of))\s+(?:(?i:Rs\.?|PKR)\s*|[$£€]\s*)?\d"
)

_UNSURE = (
    re.compile(r"\bpretend\b"),
    re.compile(r"\bact as\b"),
    re.compile(r"\brole ?play\b"),
    re.compile(r"\bsystem prompt\b"),
    re.compile(r"\bhidden prompt\b"),
)

_POLICY_QUESTION = re.compile(
    r"\b(policy|policies|leave|rule|dress|hours|attendance|conduct)\b",
    re.I,
)
_SOFT_MODERATION = {"harassment", "harassment_threatening", "hate", "hate_threatening"}


@dataclass(frozen=True)
class GuardDecision:
    action: str  # allow, block, pay, unsure
    answer: str = ""
    reason: str = "ok"


def check_question(question: str) -> GuardDecision:
    text = (question or "").strip()
    if not text:
        return GuardDecision("block", EMPTY_QUESTION, "empty")
    if len(text) > MAX_USER_CHARS:
        return GuardDecision("block", TOO_LONG, "too_long")
    folded = normalize_for_rules(text)
    if any(pattern.search(folded) for pattern in _JAILBREAKS) or _CLAIMED_ACCESS.search(text):
        return GuardDecision("block", REFUSAL, "jailbreak")
    if contains_named_pay(text):
        return GuardDecision("pay", REFUSAL, "pay")
    if any(pattern.search(folded) for pattern in _UNSURE):
        return GuardDecision("unsure", "", "unsure")
    return GuardDecision("allow", "", "ok")


def contains_named_pay(text: str) -> bool:
    normalized = text.replace("’", "'")
    folded = normalize_for_rules(normalized)
    return (any(pattern.search(folded) for pattern in _PAY)
            or any(pattern.search(normalized) for pattern in _NAMED_PAY)
            or bool(_PAY_RECORD.search(normalized) or _NAMED_EARNINGS.search(normalized)))


def moderation_blocks(categories: dict, flagged: bool, text: str) -> bool:
    """Free moderation is a backstop. Ordinary policy wording should still pass."""
    if not flagged:
        return False
    on = {name for name, value in categories.items() if value}
    if on and on <= _SOFT_MODERATION and _POLICY_QUESTION.search(text):
        return False
    return True
