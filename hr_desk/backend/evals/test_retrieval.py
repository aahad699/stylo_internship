"""Ranking checks on fixed candidate lists, so no database or embedding call is needed."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from app.models import Passage
from app.retrieve import search_passages

TODAY = date(2026, 11, 24)
PF = "Provident Fund Loan Policy – Head Office"
COC = "Code of Conduct"


def _passage(pid: str, heading: str, body: str, vec: float, *, title: str = PF, path: str = "",
             content_type: str = "section") -> Passage:
    return Passage(pid, pid.split(":")[0], title, f"{title}, {heading}", heading, body, "everyone",
                   date(2026, 1, 1), None, vec_score=vec, heading_path=path or heading, content_type=content_type)


class _Store:
    def __init__(self, candidates: list[Passage], headings: list[Passage] = ()) -> None:
        self._candidates = candidates
        self._headings = list(headings)

    def candidates(self, embedding, query, audiences, today, limit, **scope):
        return [replace(p) for p in self._candidates]

    def heading_hits(self, phrases, audiences, today, **scope):
        return [replace(p) for p in self._headings]


def _ids(store: _Store, query: str) -> list[str]:
    return [p.id for p in search_passages(store, [0.0], query, {"everyone"}, TODAY)]


ENTITLEMENT = _passage("pf:5", "5. PF Loan Entitlement:",
                       "5.1. An employee shall be eligible to apply for a PF Loan based on the Employee and Employer "
                       "contributions, subject to the conditions and limits defined in Annexure A.\n5.2. The maximum PF "
                       "Loan amount shall not exceed the eligible PF balance, as defined in Annexure A, at the time of "
                       "application.\n5.3. PF Loans shall be interest-free, as the loan is extended against the employee "
                       "and employer Provident Fund balance.", 0.738)
MATRIX = _passage("pf:16", "Annexure A Approval Authority Matrix",
                  "Service Period: More than 09 Years. PF Loan Eligibility: 85% of PF. Loan Installments Up to: "
                  "24 Months. Approving Authority: Line Manager HOD Head of HR", 0.682, content_type="table")
FORM = _passage("pf:30", "14. Provident Fund Advance Request Form:",
                "The Provident Fund (PF) Loan Request Form, in accordance with Annexure B (Reference No: HRF/0105/1), "
                "is accessible via the Stylo SharePoint Portal.", 0.658)
TEMPLATE = _passage("pf:31", "Annexure B (Provident Fund Advance Request Form - Template)",
                    "Annexure B (Provident Fund Advance Request Form - Template)", 0.6)


def test_words_shared_with_a_form_heading_do_not_make_it_the_named_section():
    store = _Store([ENTITLEMENT, MATRIX, FORM], headings=[TEMPLATE])
    found = _ids(store, "How much provident fund loan can I take? pf")
    assert found[:1] == ["pf:5"], found
    assert "pf:31" not in found


def test_a_section_that_leaves_its_limits_to_an_annexure_brings_the_annexure_rows():
    store = _Store([ENTITLEMENT, FORM], headings=[TEMPLATE, MATRIX])
    found = _ids(store, "How much provident fund loan can I take? pf")
    assert found[:2] == ["pf:5", "pf:16"], found


def test_a_heading_named_inside_a_longer_question_does_not_hide_the_answer():
    form = _passage("cap:40", "Annexure D- 2 INCIDENT REPORT", "Incident report. Store: ____ Date: ____ Details: ____",
                    0.45, title="Corrective Action Policy", content_type="form")
    authority = _passage("cap:71", "6.4. Authority on handling Inquiry cases and recommend Corrective-Action:",
                         "Level: For all Cases of Assistant Store Manager/ Salesmen and below. Preparation of Incident "
                         "Report and Inquiry Officer: RSM to prepare Incident Report.", 0.5,
                         title="Corrective Action Policy", content_type="table")
    found = _ids(_Store([form, authority], headings=[form]), "Who prepares the incident report for a salesman's case?")
    assert "cap:71" in found, found


def test_a_section_named_by_its_title_still_wins():
    cash = _passage("coc:20", "Cash Handling:", "All cash must be counted at POS and tills reconciled daily.", 0.41,
                    title=COC, path="Part 4 – Retail-Specific Standards & Acknowledgment > Cash Handling:")
    other = _passage("coc:21", "Inventory Integrity:", "Cash and stock counts must match the records.", 0.52,
                     title=COC, path="Part 4 – Retail-Specific Standards & Acknowledgment > Inventory Integrity:")
    assert _ids(_Store([other, cash], headings=[cash]), "What does the Cash Handling section say?") == ["coc:20"]
