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
from .access import named_grades, policy_row_roles, policy_visible_to, visible_passage
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


def _rule_phrases(query: str) -> list[str]:
    """Headings to fetch when the question is about a condition, not a procedure."""
    text = query or ""
    phrases: list[str] = []
    if re.search(
        r"\b(?:who is covered|who can use|who can apply|apply to|applies to|eligib|"
        r"how long|before i can|the most|maximum)\b",
        text, re.I,
    ) or re.search(r"\bdoes\b.{0,40}\bapply\b", text, re.I):
        phrases.extend(["eligibility", "scope", "applicability"])
    if re.search(r"\bthe most\b|\bmaximum\b", text, re.I) and re.search(r"\badvance\b", text, re.I):
        phrases.append("eligibility")
    if re.search(r"\bwhen does\b.{0,40}\b(?:stop|end|cease)\b", text, re.I):
        phrases.append("termination of coverage")
    unique: list[str] = []
    for phrase in phrases:
        if phrase not in unique:
            unique.append(phrase)
    return unique


def _topic_tokens(text: str) -> set[str]:
    return {word for word in re.findall(r"[a-z]{5,}", (text or "").casefold()) if word not in _TOPIC_SKIP}


def _shares_topic(query: str, passage: Passage) -> bool:
    return bool(_topic_tokens(query) & _topic_tokens(f"{passage.title} {passage.heading} {passage.body}"))


def _placeholder_row(passage: Passage) -> bool:
    """A repeated column-title row from a scanned table, with no rule in it."""
    return (passage.body or "").casefold().count("steps/what happens") >= 2


_NEGATIVE = {"not", "no", "excluded", "cease", "without", "longer"}
_ANNEXURE = re.compile(r"(?i)\bannexure\s+([a-z])\b")
# A sentence that sends the reader to an annexure for the fact, not a passing "in accordance with".
_FACT_ANNEXURE = re.compile(
    r"(?i)\b(?:please\s+refer\s+to|refer\s+to|see|defined\s+in|outlined\s+in|specified\s+in|"
    r"set\s+out\s+in|listed\s+in|shown\s+in|mentioned\s+in|as\s+per)\s+(?:the\s+)?annexure\s+([a-z])\b"
    r"|:\s*annexure\s+([a-z])\b"
)
_PLEASE_REFER = re.compile(r"(?i)\b(?:please\s+)?refer\s+to\s+(?:the\s+)?annexure\s+([a-z])\b")
_CLAUSE_POINTER = re.compile(
    r"(?i)\b(?:mentioned|described|specified|defined|set\s+out|covered|stated)\s+in\s+"
    r"(?:point|clause|section)\s+(\d+(?:\.\d+)*)"
    r"|\b(?:please\s+)?refer\s+to\s+(?:point|clause|section)\s+(\d+(?:\.\d+)*)"
    r"|\bsee\s+(?:point|clause|section)\s+(\d+(?:\.\d+)*)"
)
_FOLLOWING_TABLE = re.compile(
    r"(?i)\b(?:please\s+)?refer to the following\b|\bfollowing\s+entitlements?\s+table\b|\btable below\b"
)
_ASKS_FORM = re.compile(r"\b(?:form|template|fields)\b", re.I)
_RULE_HEADING = re.compile(r"eligib|scope|applicab|termination of coverage", re.I)
_TOPIC_SKIP = {
    "about", "policy", "employee", "employees", "company", "which", "where", "would",
    "could", "should", "apply", "applies", "applied", "before", "after", "their",
}
_BROAD = _TOPIC_SKIP | {
    "salary", "request", "travel", "medical", "people", "working", "during", "without",
    "manager", "advance", "please", "there", "other",
}


def search_passages(
    store, embedding: list[float], query: str, audiences: set[str], today: date,
    *, policy_ids: list[str] | None = None, designation: str | None = None,
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
    rule_phrases = _rule_phrases(query)
    if rule_phrases and hasattr(store, "heading_hits"):
        # A short eligibility or scope section often loses the vector search to the longer procedure.
        for extra in store.heading_hits(rule_phrases, audiences, today, **scope):
            if extra.id in seen or not _RULE_HEADING.search(extra.heading) or not _shares_topic(query, extra):
                continue
            extra.vec_score = min(extra.vec_score, 0.4)
            extra.rule_match = True
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
    pool_ids.update(passage.id for passage in raw if getattr(passage, "rule_match", False))
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
        if rule_phrases and _RULE_HEADING.search(passage.heading) and _shares_topic(query, passage):
            passage.score += 0.02
        if re.match(r"(?i)step\s*1\b", passage.heading) and re.search(r"\b(?:where|start|navigate|menu)\b", query, re.I):
            passage.score += 0.02
        if re.search(r"immediate dismissal", passage.heading, re.I):
            unusual = {word for word in words if len(word) >= 5} - _BROAD
            if unusual & token_set(passage.body):
                passage.score += 0.02
        if passage.id in explicit or getattr(passage, "rule_match", False) or _keep(passage) or (clear_lead and passage.vec_score >= best_vector):
            kept.append(passage)
    kept.sort(key=lambda passage: passage.score, reverse=True)
    if kept and kept[0].score < RERANK_FLOOR and not (named or on_page):
        return []
    for passage in kept:
        # A repeated column-title row has no rule in it. Applied after the floor check so it reorders, not discards.
        if _placeholder_row(passage):
            passage.score -= 0.05
        if re.match(r"(?i)step\s*1\b", passage.heading) and re.search(r"\b(?:where|start|navigate|menu)\b", query, re.I):
            if passage.content_type != "table" and len((passage.body or "").encode("utf-8")) < 80:
                passage.score += 0.04
    kept.sort(key=lambda passage: passage.score, reverse=True)
    rules = [passage for passage in kept if getattr(passage, "rule_match", False)]
    if rules:
        rule_ids = {passage.id for passage in rules}
        kept = rules + [passage for passage in kept if passage.id not in rule_ids]
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
        return _with_named_approval(
            store, query, _with_distance_rule(
                store, query, _with_referenced_table(store, merged[:16], audiences, today, designation, query),
                audiences, today,
            ), audiences, today, designation,
        )
    if kept and kept[0].content_type == "table" and hasattr(store, "heading_rows"):
        top = kept[0]
        # The sibling lookup stops at 16 rows, so the ranked rows of this table come first.
        rows = [passage for passage in kept if (passage.policy_id, passage.heading) == (top.policy_id, top.heading)]
        ranked = {passage.id for passage in rows}
        rows += [row for row in store.heading_rows(top.policy_id, top.heading, audiences, today) if row.id not in ranked]
        if len(rows) > 1:
            for row in rows[len(ranked):]:
                row.score = 0.0
            # The sibling rows answer the table. A different section already ranked above them still has to stay.
            lead = [passage for passage in kept[:RETRIEVE_TOP_K] if passage.id not in {row.id for row in rows}]
            kept = lead + rows
    limit = 16 if kept and kept[0].content_type == "table" and len(kept) > RETRIEVE_TOP_K else RETRIEVE_TOP_K
    return _with_named_approval(
        store, query, _with_distance_rule(
            store, query, _with_referenced_table(store, kept[:limit], audiences, today, designation, query),
            audiences, today,
        ), audiences, today, designation,
    )


def _with_referenced_table(
    store, passages: list[Passage], audiences: set[str], today: date,
    designation: str | None = None, query: str = "",
) -> list[Passage]:
    """Follow a pointer to the annexure, clause, or table it names.

    "Annexure B" stays in the passage's own policy. "Annexure B of the Travel Policy" opens that
    annexure in the named policy. A form template is included only when the sentence says to refer
    to it, or the question asks for the form. A target this role cannot see is left out.
    """
    if not passages or not hasattr(store, "heading_hits"):
        return passages
    titles = _policy_titles(store, passages)
    present = {passage.id: passage for passage in passages}
    ranked_tables = {(passage.policy_id, passage.heading) for passage in passages if passage.content_type == "table"}
    extras: dict[str, list[Passage]] = {}
    asks_form = bool(_ASKS_FORM.search(query or ""))

    for index, source in enumerate(passages):
        if source.content_type != "section":
            continue
        body = source.body or ""
        cross = _cross_annexures(body, source.policy_id, titles)
        cross_letters = {letter for letter, _policy_id in cross}
        letters = _match_groups(_FACT_ANNEXURE, body) - cross_letters
        please = _match_groups(_PLEASE_REFER, body) - cross_letters
        if index == 0:
            letters |= {letter.casefold() for letter in _ANNEXURE.findall(body)} - cross_letters
        for row in _annexure_rows(store, source.policy_id, audiences, today):
            _take_annexure(row, source, letters, please, asks_form, False, designation, present, extras, ranked_tables)
        if index == 0:
            _take_named_table(store, source, audiences, today, designation, present, extras, ranked_tables)
        for letter, policy_id in cross:
            for row in _annexure_rows(store, policy_id, audiences, today):
                _take_annexure(row, source, {letter}, {letter}, True, True, designation, present, extras, ranked_tables)
        if hasattr(store, "clause_hits"):
            for number in _match_groups(_CLAUSE_POINTER, body):
                for row in store.clause_hits([number], audiences, today, policy_ids=[source.policy_id]):
                    if row.policy_id != source.policy_id or not contains_clause(f"{row.heading}\n{row.body}", number):
                        continue
                    _take_row(row, source, designation, present, extras)
        if hasattr(store, "heading_rows") and _FOLLOWING_TABLE.search(body):
            for row in store.heading_rows(source.policy_id, source.heading, audiences, today):
                if row.content_type == "table" and row.policy_id == source.policy_id:
                    _take_row(row, source, designation, present, extras)
    if not extras and not any(getattr(passage, "referenced", False) for passage in passages):
        return passages
    return _insert_referenced(passages, extras, query)


def _match_groups(pattern: re.Pattern, text: str) -> set[str]:
    found = set()
    for match in pattern.finditer(text or ""):
        found.update(group.casefold() for group in match.groups() if group)
    return found


def _policy_titles(store, passages: list[Passage]) -> list[tuple[str, str]]:
    found: dict[str, str] = {}
    if hasattr(store, "list_policies"):
        for policy in store.list_policies():
            title = getattr(policy, "title", "") or ""
            policy_id = getattr(policy, "id", "") or ""
            if policy_id and title:
                found[policy_id] = title
    for passage in passages:
        found.setdefault(passage.policy_id, passage.title)
    return sorted(found.items(), key=lambda item: len(item[1]), reverse=True)


def _cross_annexures(text: str, source_policy_id: str, titles: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Annexure letters this sentence sends to a different named policy."""
    found = []
    seen = set()
    for policy_id, title in titles:
        if policy_id == source_policy_id or len(title) < 4:
            continue
        pattern = re.compile(
            rf"(?i)\bannexure\s+([a-z])\b(?:\s+\([^)]{{0,80}}\))?\s+(?:of|in)\s+(?:the\s+)?{re.escape(title)}\b"
        )
        for match in pattern.finditer(text or ""):
            item = (match.group(1).casefold(), policy_id)
            if item not in seen:
                seen.add(item)
                found.append(item)
    return found


def _annexure_rows(store, policy_id: str, audiences: set[str], today: date) -> list[Passage]:
    return [
        row for row in store.heading_hits(["annexure"], audiences, today, policy_ids=[policy_id])
        if row.policy_id == policy_id
    ]


def _formish(passage: Passage) -> bool:
    if passage.content_type == "form":
        return True
    label = f"{passage.heading} {passage.body}".casefold()
    return "template" in label or (
        passage.content_type != "table" and re.search(r"\bform\b", label) and len(passage.body) < 500
    )


def _open_to(designation: str | None, passage: Passage) -> bool:
    if not designation:
        return True
    return policy_visible_to(designation, passage.title, passage.audience)


def _take_row(row: Passage, source: Passage, designation, present, extras) -> None:
    if row.id == source.id or not _open_to(designation, row):
        return
    if designation and row.allowed_roles is not None and not visible_passage(designation, row):
        return
    row.score = source.score
    existing = present.get(row.id)
    if existing is not None:
        _remember_source(existing, source.id)
        existing.score = max(existing.score, source.score)
        return
    _remember_source(row, source.id)
    present[row.id] = row
    extras.setdefault(source.id, []).append(row)


def _remember_source(passage: Passage, source_id: str) -> None:
    passage.referenced = True
    sources = getattr(passage, "referenced_from", None)
    if sources is None:
        passage.referenced_from = {source_id}
    else:
        sources.add(source_id)


def _take_annexure(row, source, letters, please, asks_form, cross, designation, present, extras, ranked_tables) -> None:
    label = _ANNEXURE.match(row.heading.strip())
    if not label or label.group(1).casefold() not in letters:
        return
    if (row.policy_id, row.heading) in ranked_tables and row.id not in present:
        return
    form = _formish(row)
    if form and not cross and label.group(1).casefold() not in please and not asks_form:
        return
    if not form and row.content_type not in {"table", "section", "form"}:
        return
    if not cross and not form and row.content_type != "table" and label.group(1).casefold() not in please and not asks_form:
        # A loose mention on the opening section brings its table, not every annexure section.
        if label.group(1).casefold() not in _match_groups(_FACT_ANNEXURE, source.body):
            return
    _take_row(row, source, designation, present, extras)


def _take_named_table(store, source, audiences, today, designation, present, extras, ranked_tables) -> None:
    """The opening section can name the table by its heading, such as "Approval Authority Matrix"."""
    body = source.body.casefold()
    for row in _annexure_rows(store, source.policy_id, audiences, today):
        label = _ANNEXURE.match(row.heading.strip())
        if row.content_type != "table" or not label or (row.policy_id, row.heading) in ranked_tables:
            continue
        name = row.heading.strip()[label.end():].strip(" :-–").casefold()
        if len(name.split()) >= 2 and name in body:
            _take_row(row, source, designation, present, extras)


def _insert_referenced(passages: list[Passage], extras: dict[str, list[Passage]], query: str = "") -> list[Passage]:
    merged = []
    seen = set()
    parked: list[Passage] = []
    wanted = named_grades(query)
    for passage in passages:
        if passage.id in seen:
            continue
        seen.add(passage.id)
        merged.append(passage)
        following = [extra for extra in extras.get(passage.id, []) if extra.id not in seen]
        if wanted:
            following.sort(key=lambda row: 0 if wanted <= (policy_row_roles(row.heading, row.body, row.title) or set()) else 1)
        # The row the question names, or the first referenced row, stays beside the pointer.
        # The other rows wait, so a short rule already ranked next is not pushed out.
        for extra in following[:1]:
            seen.add(extra.id)
            merged.append(extra)
        parked.extend(following[1:])
    for extra in parked:
        if extra.id in seen:
            continue
        seen.add(extra.id)
        merged.append(extra)
    return merged


def _rerank(passage: Passage, rrf: float) -> float:
    """Order the fused candidates. A title-only passage loses to a real section. A short table row is a whole fact."""
    short = 0.04 if passage.content_type != "table" and len(passage.body.encode("utf-8")) < 80 else 0.0
    return rrf + 0.025 * passage.heading_kw + 0.01 * passage.body_kw - short


def _with_named_approval(
    store, query: str, passages: list[Passage], audiences: set[str], today: date, designation: str | None,
) -> list[Passage]:
    """The authorization section points at a table. Keep the row for the grade the question names."""
    named = named_grades(query) if re.search(r"\b(?:approves|authori[sz]es)\b", query or "", re.I) else set()
    if not named or not designation or not passages or not hasattr(store, "heading_rows"):
        return passages
    present = {passage.id for passage in passages}
    matched: list[Passage] = []
    for source in passages:
        if "authorization of travel" not in (source.heading or "").casefold():
            continue
        for row in store.heading_rows(source.policy_id, source.heading, audiences, today):
            if row.id in present or row.content_type != "table" or not visible_passage(designation, row):
                continue
            roles = policy_row_roles(row.heading, row.body, row.title) or set()
            if named <= roles:
                row.score = source.score
                matched.append(row)
                present.add(row.id)
    if not matched:
        return passages
    merged: list[Passage] = []
    seen: set[str] = set()
    for passage in passages:
        if passage.id in seen:
            continue
        seen.add(passage.id)
        merged.append(passage)
        if "authorization of travel" in (passage.heading or "").casefold():
            for row in matched:
                if row.id not in seen:
                    seen.add(row.id)
                    merged.append(row)
    return merged


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
