"""Everyday words reach the clause written in the policy's own words.

"lawyer" never appears in the clause that bars an advocate, so the closest meaning must survive the word filter.
"unpaid" ties several loan sections on meaning, so the alias to "outstanding" picks the clause that settles it.
The candidates are the real passages and vector scores the database returned for each question.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from pathlib import Path

from app.aliases import expand_query
from app.models import Passage
from app.retrieve import search_passages, select_passages

TODAY = date(2026, 11, 25)
CANDIDATES = json.loads((Path(__file__).parent / "fixtures" / "synonym_candidates.json").read_text(encoding="utf-8"))
LAWYER = "Can I bring a lawyer to my inquiry?"
UNPAID = "Can I take a new PF loan while my old one is unpaid?"


class _Store:
    def __init__(self, question: str) -> None:
        self._candidates = [
            Passage(item["id"], item["id"].split(":")[0], "Policy", f"Policy, {item['heading']}", item["heading"],
                    item["body"], "everyone", date(2026, 1, 1), None, vec_score=item["vec"],
                    heading_path=item["path"], content_type=item["type"])
            for item in CANDIDATES[question]
        ]

    def candidates(self, embedding, query, audiences, today, limit, **scope):
        return [replace(p) for p in self._candidates]

    def heading_hits(self, phrases, audiences, today, **scope):
        return []


def _selected_headings(question: str) -> list[str]:
    passages = search_passages(_Store(question), [0.0], expand_query(question), {"everyone"}, TODAY)
    return [p.heading for p in select_passages(passages)]


def test_lawyer_finds_the_inquiry_clause_that_bars_an_advocate():
    assert "6.6. Inquiry Process" in _selected_headings(LAWYER)


def test_unpaid_loan_finds_the_clause_on_outstanding_loans():
    assert "4. Eligibility Criteria:" in _selected_headings(UNPAID)


def test_a_tie_on_meaning_does_not_let_an_unrelated_passage_past_the_word_filter():
    # "What are the official office timings?" Nothing answers it. The nearest passage only shares "office"
    # and leads the next one by 0.001, so it is no closer in meaning than the rest.
    near = [("pf:12", "12. Effective Date:", "This policy shall be effective immediately and applies to all permanent Head Office (RSC) employees of the company.", 0.340),
            ("coc:3", "Guidelines for All Employees", "All employees are expected to follow these guidelines at all times while at work and when representing the brand.", 0.339)]
    store = _Store(LAWYER)
    store._candidates = [Passage(pid, pid.split(":")[0], "Policy", f"Policy, {heading}", heading, body, "everyone",
                                 date(2026, 1, 1), None, vec_score=vec, heading_path=heading)
                         for pid, heading, body, vec in near]
    assert search_passages(store, [0.0], "What are the official office timings?", {"everyone"}, TODAY) == []
