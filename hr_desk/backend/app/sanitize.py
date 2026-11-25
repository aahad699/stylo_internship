"""Remove leaked ids, invented links, and numbers that are not in the passages."""

from __future__ import annotations

import re

from .access import grade_band
from .config import CANARY
from .replies import REFUSAL, UNKNOWN

_CHUNK = re.compile(
    r"\[/?chunk_[^\]]+\]|chunk_\d+_doc_\d+|\[doc_\d+\]|\bdoc_\d+\b",
    re.I,
)
_URL = re.compile(r"https?://[^\s)>\]]+")
_NUMBER = re.compile(r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)*")
_SENTENCE = re.compile(r"(?<!\bRs\.)(?<=[.!?])\s+", re.IGNORECASE)
_MONEY = re.compile(r"\b(?:rs\b|pkr\b|rupees?\b)", re.IGNORECASE)
_ECHO = re.compile(
    r"^(source\b|effective\b|source line\b|if you want\b|if the passage\b|next,\s|we would add\b)",
    re.I,
)
_DETERMINER = r"(?:the|this|that|these|those|your)\s+"
_DET = rf"(?:{_DETERMINER})?"
_SUPPLIED = r"(?:provided|supplied|shared)"
_YOU_GAVE = rf"you(?:'ve|\s+have)?\s+(?:{_SUPPLIED}|given|gave|sent)(?:\s+(?:me|us))?"
_EVIDENCE_NOUN = (r"policy\s+(?:text|data|excerpts?|evidence|passages?|information|details|documents?|sections?)"
                  r"|polic(?:y|ies)|text|excerpts?|passages?|rates")
_EVIDENCE_REFERENCE = re.compile(
    rf"\b(?:{_DET}(?:{_EVIDENCE_NOUN})\s+(?:that\s+)?{_YOU_GAVE}"
    rf"|{_DETERMINER}{_SUPPLIED}\s+(?:{_EVIDENCE_NOUN})"
    rf"|{_DET}(?:policy\s+(?:text|data|excerpts?|evidence|passages?)|excerpts?|passages?)\s+{_SUPPLIED}"
    rf"|what\s+{_YOU_GAVE}"
    rf"|{_DET}(?:policy\s+)?(?:excerpts?|passages?)|{_DET}policy\s+evidence|your\s+policy\s+(?:text|data))\b"
    r"(?P<verb>\s+(?:say|state|show|mention|specify|list|include|cover|contain|do|don't|are|aren't|have)\b)?",
    re.I,
)
_SINGULAR_VERB = {"say": "says", "state": "states", "show": "shows", "mention": "mentions", "specify": "specifies",
                  "list": "lists", "include": "includes", "cover": "covers", "contain": "contains", "do": "does",
                  "don't": "doesn't", "are": "is", "aren't": "isn't", "have": "has"}
_INTERNAL_WORDING = re.compile(
    r"\b(?:(?:provide|confirm|share)(?:\s+or\s+(?:confirm|share))?\s+(?:the\s+)?(?:policy text|rates?|amounts?)|"
    r"system prompt|verified employee designation)\b", re.I,
)
_FILE_PATH = re.compile(
    r"(?i)(?<![\w/])(?:file:\s*//|[a-z]:[\\/]|\\\\[\w.-]+\\|"
    r"/[\w.-]+(?:/[\w.-]+)+|(?:\.\.?[\\/])?[\w.-]+[\\/][\w.-]+\.(?:pdf|docx?|xlsx?|md|py|json|txt)\b)"
)


def _allowed_urls(passages: list[str]) -> set[str]:
    found = set()
    for text in passages:
        for match in _URL.findall(text):
            found.add(match.rstrip(".,"))
    return found


def _sentences(text: str) -> list[str]:
    parts = _SENTENCE.split(text.strip())
    return [part.strip() for part in parts if part.strip()]


def _numbers(text: str) -> set[str]:
    return {token.replace(",", "") for token in _NUMBER.findall(text)}


def exposes_prompt(text: str) -> bool:
    folded = text.casefold()
    return CANARY.casefold() in folded or "internal marker" in folded or bool(re.search(r"{{[^{}]+}}", text))


_ANNEXURE_LABEL = re.compile(r"\bannexures?\s+[a-z0-9]+\b", re.I)
_ANNEXURE_WORD = re.compile(r"\bannexures?\b", re.I)


def _as_the_policy(match: re.Match[str]) -> str:
    verb = (match.group("verb") or "").strip()
    reference = match.group(0)[: len(match.group(0)) - len(match.group("verb") or "")]
    phrase = "The policy" if reference[0].isupper() else "the policy"
    if re.search(r"(?:excerpts|passages|rates|documents|details|sections|policies|these|those)\b", reference, re.I):
        verb = _SINGULAR_VERB.get(verb.casefold(), verb)
    return f"{phrase} {verb}".rstrip()


def soften_labels(text: str) -> str:
    """Drop an annexure label and keep the sentence, and call the evidence "the policy".

    The canary and other bans stay."""
    cleaned = _ANNEXURE_WORD.sub("", _ANNEXURE_LABEL.sub("", text or ""))
    cleaned = _EVIDENCE_REFERENCE.sub(_as_the_policy, cleaned)
    return re.sub(r"[ \t]{2,}", " ", cleaned).strip()


def forbidden_output(text: str) -> bool:
    return exposes_prompt(text) or bool(_FILE_PATH.search(_URL.sub("", text)))


def fixed_reply(answer: str) -> str:
    plain = answer.strip(" \r\n\t.!*").replace("’", "'").casefold()
    if plain.startswith("i can't answer") and REFUSAL.casefold().startswith(plain):
        return REFUSAL
    unknown_text = re.sub(r"^[^:\n]+:\s+", "", plain)
    if re.fullmatch(
            r"you need to refer to your employment terms\.\s*"
            r"the policy does not specify a general notice period for resignations\.\s*"
            r"please confirm your current contract or local statutory requirements, and i can outline the exact notice period and any exceptions",
            unknown_text):
        return UNKNOWN
    if ((unknown_text.startswith("i couldn't find this") and UNKNOWN.casefold().startswith(unknown_text))
            or re.fullmatch(r"(?:[^:\n]+:\s+)?i couldn't find this(?:\.\s+ask(?: hr)?)?", plain)):
        return UNKNOWN
    if (re.match(r"i couldn't find (?:this|the) (?:(?:exact|specific) )?percentage\b", unknown_text)
            and not re.search(r"\d|\b(?:however|nevertheless|instead)\b|\bbut\b(?!\s+not\b)", unknown_text)
            and not re.search(r"\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|twenty|thirty|forty|fifty)\s+(?:per\s*cent|percent)\b", unknown_text)):
        return UNKNOWN
    return ""


def sanitize(answer: str, passage_texts: list[str], allowed_numbers_from: str = "") -> str:
    answer = soften_labels(answer)
    if not answer or not answer.strip():
        return UNKNOWN
    if forbidden_output(answer):
        return REFUSAL
    if fixed := fixed_reply(answer):
        return fixed
    allowed_urls = _allowed_urls(passage_texts)

    def replace_url(match: re.Match[str]) -> str:
        url = match.group(0).rstrip(".,")
        if url in allowed_urls:
            return match.group(0)
        return ""

    cleaned = _URL.sub(replace_url, answer)
    cleaned = _CHUNK.sub("", cleaned)
    evidence = [(grade_band(passage), _numbers(passage)) for passage in passage_texts]
    allowed_numbers = _numbers(allowed_numbers_from).union(*(numbers for _, numbers in evidence))
    kept_lines: list[str] = []
    blank = False
    for raw_line in cleaned.replace("\r\n", "\n").split("\n"):
        stripped = raw_line.strip()
        if canonical := fixed_reply(stripped):
            if canonical == REFUSAL:
                return REFUSAL
            continue
        if not stripped:
            blank = True
            continue
        prefix = ""
        body = stripped
        bullet = re.match(r"^[-*•]\s+(.*)$", stripped)
        if bullet:
            prefix = "- "
            body = bullet.group(1).strip()
        kept = []
        for sentence in _sentences(body):
            if any(symbol in sentence and not any(symbol in source for source in passage_texts) for symbol in ("₹", "€", "£", "$")):
                continue
            numbers = _numbers(sentence)
            if not numbers <= allowed_numbers:
                continue
            if (numbers and re.search(r"\bannual leave\b", sentence, re.I)
                    and not any(re.search(r"\bannual leave\b", source_sentence, re.I)
                                and numbers <= _numbers(source_sentence)
                                for source in passage_texts for source_sentence in _sentences(source))):
                return UNKNOWN
            if numbers and _MONEY.search(sentence):
                grade = grade_band(sentence)
                if not any(numbers <= values and (grade is None or grade == band) for band, values in evidence):
                    continue
            letters = re.sub(r"[^A-Za-z0-9]", "", sentence)
            if len(letters) < 8 or _ECHO.search(sentence) or _INTERNAL_WORDING.search(sentence):
                continue
            kept.append(sentence)
        if not kept:
            continue
        if blank and kept_lines:
            kept_lines.append("")
        blank = False
        kept_lines.append(prefix + " ".join(kept))
    text = "\n".join(kept_lines)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\s+([.,!?])", r"\1", text).strip()
    return fixed_reply(text) or text or UNKNOWN
