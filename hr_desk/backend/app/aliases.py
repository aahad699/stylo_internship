"""Query and index aliases loaded from the policy data file, not from topics.py."""

from __future__ import annotations

import json
import re
from functools import lru_cache

from .config import BACKEND_ROOT

_ALIAS_PATH = BACKEND_ROOT / "data" / "retrieval_aliases.json"


@lru_cache(maxsize=1)
def alias_groups() -> tuple[dict, ...]:
    payload = json.loads(_ALIAS_PATH.read_text(encoding="utf-8"))
    return tuple(payload["groups"])


def _contains(text: str, phrase: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(phrase.casefold())}(?![a-z0-9])", text) is not None


def replacements() -> dict[str, str]:
    found = {}
    for group in alias_groups():
        for short, long in (group.get("replace") or {}).items():
            found[short.casefold()] = long
    return found


def expand_abbreviations(text: str) -> str:
    """Replace a short form with the one canonical phrase topic detection expects."""
    table = replacements()
    if not table:
        return text
    pattern = re.compile(r"\b(?:" + "|".join(re.escape(key) for key in sorted(table, key=len, reverse=True)) + r")\b", re.I)
    return pattern.sub(lambda match: table[match.group(0).casefold()], text)


def expand_query(text: str) -> str:
    """Keep the original words and append aliases that share a group."""
    folded = text.casefold()
    extra: list[str] = []
    for group in alias_groups():
        names = [group["means"], *(item["phrase"] for item in group["aliases"])]
        if not any(_contains(folded, name) for name in names):
            continue
        for name in names:
            if not _contains(folded, name) and name not in extra:
                extra.append(name)
    if not extra:
        return text
    return f"{text} {' '.join(extra)}"


def index_context(title: str, heading_path: str, heading: str) -> str:
    """Text added to the embedding and keyword index. The displayed body stays separate."""
    blob = f"{title} {heading} {heading_path}".casefold()
    also: list[str] = []
    for group in alias_groups():
        if re.search(group["match"], blob, re.I):
            for item in group["aliases"]:
                if item["phrase"] not in also:
                    also.append(item["phrase"])
    lines = [title.strip(), (heading_path or heading or "").strip()]
    if also:
        lines.append("Also called: " + ", ".join(also))
    if heading:
        lines.append(f"Same section: {heading}")
    if re.search(r"daily allowance|fuel entitlement|hotel entitlement", heading or "", re.I):
        lines.append(
            "Linked rule: daily allowance applies to journeys of 300 km or more round trip, including transit time."
        )
    return "\n".join(line for line in lines if line)


def embed_input(title: str, heading_path: str, heading: str, body: str) -> str:
    return "\n".join(part for part in (index_context(title, heading_path, heading), body.strip()) if part)
