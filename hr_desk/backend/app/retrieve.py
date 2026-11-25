"""Hybrid search: vector similarity plus the words the employee actually used."""

from __future__ import annotations

import re
from datetime import date

from .config import (
    MAX_PASSAGES,
    MIN_VECTOR_SCORE,
    PASSAGE_SCORE_WINDOW,
    RERANK_FLOOR,
    RETRIEVE_CANDIDATES,
    RETRIEVE_TOP_K,
    RRF_K,
    VECTOR_LEAD,
)
from .clauses import asked_clause, asked_page, contains_clause, heading_naming, heading_phrases
from .models import Passage
from .textutil import keyword_score, token_list, token_set


def _keep(passage: Passage) -> bool:
    if passage.kw_score >= 0.67:
        return True
    if passage.vec_score >= 0.55:
        return True
    if passage.vec_score >= MIN_VECTOR_SCORE and passage.kw_score >= 0.34:
        return True
    # A one-letter slip shares few embedding dimensions, so the word match has to be enough.
    return bool(getattr(passage, "typo_match", False)) and passage.kw_score >= 0.34


_NEGATIVE = {"not", "no", "excluded", "cease", "without", "longer"}
_ANNEXURE = re.compile(r"(?i)\bannexure\s+([a-z])\b")


def search_passages(
    store, embedding: list[float], query: str, audiences: set[str], today: date,
    *, policy_ids: list[str] | None = None,
) -> list[Passage]:
    scope = {} if policy_ids is None else {"policy_ids": policy_ids}
    raw = store.candidates(embedding, query, audiences, today, RETRIEVE_CANDIDATES, **scope)
    number = asked_clause(query)
    phrases = heading_phrases(query)
    page_number = asked_page(query)
    seen = {passage.id for passage in raw}
    if number and hasattr(store, "clause_hits"):
        for extra in store.clause_hits([number], audiences, today, **scope):
            if extra.id not in seen:
                raw.append(extra)
                seen.add(extra.id)
    if phrases and hasattr(store, "heading_hits"):
        for extra in store.heading_hits(phrases, audiences, today, **scope):
            # The heading lookup matches word pairs. Only a heading the question names joins the candidates.
            if extra.id not in seen and _names_this(extra, query, page_number):
                raw.append(extra)
                seen.add(extra.id)
    on_asked_page: list[str] = []
    if page_number and hasattr(store, "printed_page_hits"):
        for extra in store.printed_page_hits(page_number, audiences, today, **scope):
            # A passage on the asked page is asked for, even when it was already a weak vector candidate.
            on_asked_page.append(extra.id)
            if extra.id not in seen:
                raw.append(extra)
                seen.add(extra.id)
    words = token_set(query)
    negative = bool(words & _NEGATIVE)
    asks_for_form = bool(words & {"template", "form", "details"})
    heading_order = [passage.id for passage in raw if _names_this(passage, query, page_number)]
    heading_order.extend(passage_id for passage_id in on_asked_page if passage_id not in heading_order)
    if number:
        heading_order.extend(
            passage.id for passage in raw
            if contains_clause(f"{passage.heading}\n{passage.body}", number) and passage.id not in heading_order
        )
    for passage in raw:
        # The document title sits in section_label. Score the heading path and the body apart from it.
        passage.heading_kw = keyword_score(query, f"{passage.heading_path} {passage.heading}")
        passage.body_kw = keyword_score(query, passage.body)
        passage.kw_score = max(passage.heading_kw, passage.body_kw)
        passage.typo_match = _typo_match(query, f"{passage.heading_path} {passage.heading} {passage.body}")
    by_vector = sorted(raw, key=lambda passage: passage.vec_score, reverse=True)
    by_keyword = sorted(raw, key=lambda passage: passage.kw_score, reverse=True)
    vector_order = [passage.id for passage in by_vector if passage.vec_score > 0]
    keyword_order = [passage.id for passage in by_keyword if passage.kw_score > 0]
    rrf: dict[str, float] = {}
    for order in (vector_order, keyword_order, heading_order):
        for rank, passage_id in enumerate(order, start=1):
            rrf[passage_id] = rrf.get(passage_id, 0.0) + 1.0 / (RRF_K + rank)
    explicit = set(heading_order)
    pool_ids = {passage.id for passage in sorted(raw, key=lambda item: rrf.get(item.id, 0.0), reverse=True)[:20]}
    pool_ids.update(explicit)
    # The word gate must not drop a clear closest meaning. "lawyer" is never in the clause that says "advocate".
    by_meaning = sorted((passage.vec_score for passage in raw), reverse=True)
    best_vector = by_meaning[0] if by_meaning else 0.0
    clear_lead = best_vector > 0 and best_vector - (by_meaning[1] if len(by_meaning) > 1 else 0.0) >= VECTOR_LEAD
    kept: list[Passage] = []
    for passage in raw:
        if passage.id not in pool_ids:
            continue
        passage.score = _rerank(passage, rrf.get(passage.id, 0.0))
        if negative and passage.polarity == "exclusion":
            passage.score += 0.02
        if asks_for_form and passage.content_type == "form":
            passage.score += 0.02
        if passage.id in explicit or _keep(passage) or (clear_lead and passage.vec_score >= best_vector):
            kept.append(passage)
    kept.sort(key=lambda passage: passage.score, reverse=True)
    # Only a heading named in full replaces the ranked list. Part of a title only lifts it in the fusion above.
    naming = {passage.id: _naming(passage, query) for passage in kept}
    named = [passage for passage in kept if naming[passage.id][0]]
    if named:
        best = max(naming[passage.id] for passage in named)
        named = [passage for passage in named if naming[passage.id] == best]
    on_page = [passage for passage in kept if _on_printed_page(passage, page_number, kept)]
    # The printed "Page 5 of 10" footer finds the page. It has no policy text, so the page's sections answer.
    on_page = [passage for passage in on_page if not _is_page_footer(passage)] or on_page
    if named or on_page:
        merged = []
        seen_ids = set()
        for passage in named + on_page:
            if passage.id in seen_ids:
                continue
            seen_ids.add(passage.id)
            merged.append(passage)
        if merged:
            top = max(passage.score for passage in merged)
            for passage in merged:
                passage.score = top
        return _with_distance_rule(store, query, _with_referenced_table(store, merged[:16], audiences, today),
                                   audiences, today)
    if kept and kept[0].content_type == "table" and hasattr(store, "heading_rows"):
        top = kept[0]
        # The sibling lookup stops at 16 rows, so the ranked rows of this table come first.
        rows = [passage for passage in kept if (passage.policy_id, passage.heading) == (top.policy_id, top.heading)]
        ranked = {passage.id for passage in rows}
        rows += [row for row in store.heading_rows(top.policy_id, top.heading, audiences, today) if row.id not in ranked]
        if len(rows) > 1:
            for row in rows[len(ranked):]:
                row.score = 0.0
            kept = rows
    if kept and kept[0].score < RERANK_FLOOR and not (named or on_page):
        return []
    limit = 16 if kept and kept[0].content_type == "table" and len(kept) > RETRIEVE_TOP_K else RETRIEVE_TOP_K
    return _with_distance_rule(store, query, _with_referenced_table(store, kept[:limit], audiences, today),
                               audiences, today)


def _with_referenced_table(store, passages: list[Passage], audiences: set[str], today: date) -> list[Passage]:
    """A section that leaves its figures to an annexure table, such as "limits defined in Annexure A" or
    "the below Approval Authority Matrix", brings the rows of that table this role may see. A table that
    already has a ranked row keeps only what the ranking chose."""
    if not passages or passages[0].content_type != "section" or not hasattr(store, "heading_hits"):
        return passages
    top = passages[0]
    body = top.body.casefold()
    letters = {letter.casefold() for letter in _ANNEXURE.findall(top.body)}
    ranked_tables = {(passage.policy_id, passage.heading) for passage in passages if passage.content_type == "table"}
    rows = []
    for row in store.heading_hits(["annexure"], audiences, today, policy_ids=[top.policy_id]):
        label = _ANNEXURE.match(row.heading.strip())
        if row.content_type != "table" or not label or (row.policy_id, row.heading) in ranked_tables:
            continue
        name = row.heading.strip()[label.end():].strip(" :-–").casefold()
        if label.group(1).casefold() in letters or (len(name.split()) >= 2 and name in body):
            rows.append(row)
    if not rows:
        return passages
    attached = {row.id for row in rows}
    for row in rows:
        row.score = top.score
    return [top, *rows, *(passage for passage in passages[1:] if passage.id not in attached)]


def _rerank(passage: Passage, rrf: float) -> float:
    """Order the fused candidates. A title-only passage loses to a real section. A short table row is a whole fact."""
    short = 0.04 if passage.content_type != "table" and len(passage.body.encode("utf-8")) < 80 else 0.0
    return rrf + 0.025 * passage.heading_kw + 0.01 * passage.body_kw - short


def _with_distance_rule(store, query: str, passages: list[Passage], audiences: set[str], today: date) -> list[Passage]:
    asked = bool(re.search(r"\b300\b", query) and re.search(r"\b(?:km|kilometers?|rule|allowance|distance)\b", query, re.I))
    rate = any(re.search(r"daily allowance|fuel entitlement|hotel entitlement", passage.heading, re.I) for passage in passages)
    if not asked and not rate:
        return passages
    if any(re.search(r"\b300\b", passage.body) and re.search(r"round trip", passage.body, re.I) for passage in passages):
        return passages
    if not hasattr(store, "heading_hits"):
        return passages
    for extra in store.heading_hits(["daily allowance"], audiences, today):
        if re.search(r"\b300\b", extra.body) and re.search(r"round trip", extra.body, re.I):
            if all(passage.id != extra.id for passage in passages):
                return [*passages, extra]
            break
    return passages


def _is_page_footer(passage: Passage) -> bool:
    return bool(re.fullmatch(r"(?i)page\s+\d+\s+of\s+\d+", passage.heading.strip()))


def _on_printed_page(passage: Passage, page_number: int, kept: list[Passage]) -> bool:
    if not page_number:
        return False
    pages = {
        (item.policy_id, item.page)
        for item in kept
        if item.heading.lower().startswith(f"page {page_number} of")
    }
    return (passage.policy_id, passage.page) in pages


def _naming(passage: Passage, query: str) -> tuple[bool, int]:
    return heading_naming(query, passage.heading, passage.heading_path, passage.title)


def _names_this(passage: Passage, query: str, page_number: int) -> bool:
    if _naming(passage, query)[1]:
        return True
    if page_number and passage.heading.lower().startswith(f"page {page_number} of"):
        return True
    return False


def _typo_match(query: str, text: str) -> bool:
    wanted = [token for token in set(token_list(query)) if len(token) >= 5]
    found = token_set(text)
    found_by_length: dict[int, list[str]] = {}
    for token in found:
        found_by_length.setdefault(len(token), []).append(token)
    for token in wanted:
        if token in found:
            continue
        if any(_one_edit(token, other) for other in _similar_length(token, found_by_length)):
            return True
    return False


def _similar_length(token: str, found_by_length: dict[int, list[str]]) -> list[str]:
    nearby: list[str] = []
    for length in (len(token) - 1, len(token), len(token) + 1):
        nearby.extend(found_by_length.get(length, []))
    return nearby


def _one_edit(left: str, right: str) -> bool:
    if abs(len(left) - len(right)) > 1:
        return False
    if len(left) == len(right):
        return sum(a != b for a, b in zip(left, right)) == 1
    if len(left) > len(right):
        left, right = right, left
    for index in range(len(right)):
        if left == right[:index] + right[index + 1 :]:
            return True
    return False


def select_passages(passages: list[Passage]) -> list[Passage]:
    if not passages:
        return []
    best = passages[0].score
    chosen = [passage for passage in passages if passage.score >= best - PASSAGE_SCORE_WINDOW]
    one_policy = len({passage.title for passage in chosen}) == 1
    tied = len({round(passage.score, 2) for passage in chosen}) == 1
    if len(chosen) > MAX_PASSAGES and (tied or (one_policy and len({passage.page for passage in chosen}) <= 2)):
        return chosen[:16]
    return chosen[:MAX_PASSAGES]
