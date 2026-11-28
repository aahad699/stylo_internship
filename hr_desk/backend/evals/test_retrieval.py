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
             content_type: str = "section", audience: str = "everyone") -> Passage:
    return Passage(pid, pid.split(":")[0], title, f"{title}, {heading}", heading, body, audience,
                   date(2026, 1, 1), None, vec_score=vec, heading_path=path or heading, content_type=content_type)


class _Policy:
    def __init__(self, policy_id: str, title: str) -> None:
        self.id = policy_id
        self.title = title


class _Store:
    def __init__(self, candidates: list[Passage], headings: list[Passage] = (), clauses: list[Passage] = (),
                 policies: list[_Policy] = (), rows: list[Passage] = ()) -> None:
        self._candidates = candidates
        self._headings = list(headings)
        self._clauses = list(clauses)
        self._policies = list(policies)
        self._rows = list(rows)

    def candidates(self, embedding, query, audiences, today, limit, **scope):
        return [replace(p) for p in self._candidates]

    def heading_hits(self, phrases, audiences, today, **scope):
        return [replace(p) for p in self._headings]

    def clause_hits(self, numbers, audiences, today, **scope):
        return [replace(p) for p in self._clauses]

    def heading_rows(self, policy_id, heading, audiences, today):
        return [replace(p) for p in self._rows if p.policy_id == policy_id and p.heading == heading]

    def list_policies(self):
        return list(self._policies)


def _ids(store: _Store, query: str, designation: str | None = None) -> list[str]:
    return [p.id for p in search_passages(store, [0.0], query, {"everyone"}, TODAY, designation=designation)]


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


def test_an_annexure_pointer_stays_inside_the_same_policy():
    section = _passage(
        "travel:4", "4.4. Company vehicles",
        "Fuel is reimbursed at the mileage rate outlined in the Annexure D.",
        0.8, title="Travel Policy",
    )
    same = _passage(
        "travel:d", "Annexure D Fuel Entitlement",
        "Grade: Officer. Mileage: 10 km per liter.",
        0.2, title="Travel Policy", content_type="table",
    )
    other = _passage(
        "medical:d", "Annexure D Claim Form",
        "Attach the bills.",
        0.2, title="Medical Insurance Policy", content_type="table",
    )
    store = _Store([section], headings=[same, other], policies=[_Policy("travel", "Travel Policy"),
                                                                _Policy("medical", "Medical Insurance Policy")])
    found = _ids(store, "What mileage rate applies when I use a company vehicle?")
    assert "travel:d" in found, found
    assert "medical:d" not in found, found


def test_a_named_other_policy_is_the_annexure_that_is_opened():
    section = _passage(
        "med:5", "5. Claims",
        "Submit the form described in Annexure B of the Travel Policy.",
        0.8, title="Medical Insurance Policy",
    )
    travel = _passage(
        "travel:b", "Annexure B Travel Expense Form",
        "Fields: dates, amount, and grade.",
        0.2, title="Travel Policy", content_type="form",
    )
    medical = _passage(
        "med:b", "Annexure B Benefit Schedule",
        "Plan A room limit 12000.",
        0.2, title="Medical Insurance Policy", content_type="table",
    )
    store = _Store([section], headings=[travel, medical], policies=[_Policy("travel", "Travel Policy"),
                                                                    _Policy("med", "Medical Insurance Policy")])
    found = _ids(store, "How do I claim a refund?", designation="general_manager")
    assert "travel:b" in found, found
    assert "med:b" not in found, found


def test_a_point_pointer_opens_that_point_in_the_same_policy():
    section = _passage(
        "travel:45", "4.5. Personal vehicle",
        "Employees who drive their own car are reimbursed at the mileage rate for the category mentioned in point 4.4.",
        0.8, title="Travel Policy",
    )
    point = _passage(
        "travel:44", "4.4. Company vehicles",
        "4.4. Company vehicles are reimbursed from the mileage table.",
        0.2, title="Travel Policy",
    )
    other = _passage(
        "medical:44", "4.4. Other cover",
        "4.4. This point belongs to a different policy.",
        0.2, title="Medical Insurance Policy",
    )
    store = _Store([section], clauses=[point, other])
    found = _ids(store, "What mileage rate is used when I drive my own car?")
    assert "travel:44" in found, found
    assert "medical:44" not in found, found


def test_the_following_table_is_brought_from_the_same_heading():
    section = _passage(
        "travel:mode", "4. Mode of Travel",
        "Please refer to the following entitlements table for travelling within the country.",
        0.8, title="Travel Policy",
    )
    row = _passage(
        "travel:air", "4. Mode of Travel",
        "Grade: Officer. Travel by air: Economy.",
        0.1, title="Travel Policy", content_type="table",
    )
    store = _Store([section], rows=[row])
    found = _ids(store, "How can I travel for work?")
    assert found[:2] == ["travel:mode", "travel:air"], found


def test_a_closed_policy_target_is_not_attached():
    section = _passage(
        "coc:1", "Gifts",
        "The limit is the one in Annexure B of the Travel Policy.",
        0.8, title="Code of Conduct",
    )
    hidden = _passage(
        "travel:b", "Annexure B Rates",
        "The hidden rate table.",
        0.2, title="Travel Policy", content_type="table", audience="assistant_manager,manager,hr",
    )
    store = _Store([section], headings=[hidden], policies=[_Policy("travel", "Travel Policy"),
                                                          _Policy("coc", "Code of Conduct")])
    found = _ids(store, "What is the gift limit?", designation="store_manager")
    assert found == ["coc:1"], found


def test_the_navigation_step_is_not_dropped_for_being_short():
    step = _passage(
        "adv:4", "Step 1: Navigate to Advance Request",
        "From the left navigation menu: Payroll → Employee Advances & Loans",
        0.5, title="User Guidelines - Advance Salary (HO - RSC)",
    )
    later = _passage(
        "adv:6", "Step 3: Enter Required Details",
        "Advance Type Code is Advance Salary. Create saves a draft. Create and Submit sends the request.",
        0.62, title="User Guidelines - Advance Salary (HO - RSC)",
    )
    found = _ids(_Store([later, step]), "Where in ESS do I start an advance request?")
    assert found[0] == "adv:4", found


def test_who_can_use_a_policy_fetches_its_scope():
    procedure = _passage(
        "pf:28", "14. Provident Fund Advance Request Form:",
        "The provident fund loan request form is on the portal.",
        0.7,
    )
    scope = _passage(
        "pf:10", "2. Scope:",
        "This policy applies to all permanent Head Office employees and provident fund contributions.",
        0.3,
    )
    found = _ids(_Store([procedure], headings=[scope]), "Who can use the provident fund loan policy?")
    assert "pf:10" in found[:4], found


def test_the_named_grade_approval_row_is_attached():
    section = _passage(
        "travel:7", "3.2. Authorization of Travel",
        "All business travel must be pre-approved by the competent authority as stated in the table below.",
        0.7, title="Travel Policy",
    )
    manager = _passage(
        "travel:10", "3.2. Authorization of Travel",
        "Grade: Manager, Assistant Manager, Senior Executive, Executive, Officer and below. Competent Authority: Head of Department.",
        0.2, title="Travel Policy", content_type="table",
    )
    manager.allowed_roles = ("manager", "hr")
    general = _passage(
        "travel:8", "3.2. Authorization of Travel",
        "Grade: General Manager, Deputy General Manager. Competent Authority: Director.",
        0.2, title="Travel Policy", content_type="table",
    )
    general.allowed_roles = ("general_manager", "hr")
    found = _ids(
        _Store([section], rows=[general, manager]),
        "Who approves travel for a manager?",
        designation="manager",
    )
    assert "travel:10" in found, found
    assert "travel:8" not in found, found


def test_a_repeated_table_header_does_not_outrank_the_scope():
    blank = _passage(
        "cap:113", "Annexure D- 4 SOP for handling Corrective-Action Process for Store Staff",
        "Steps/What Happens?: Steps/What Happens?. Time Frame: Time Frame.",
        0.8, title="Corrective Action Policy", content_type="table",
    )
    scope = _passage(
        "cap:4", "1. Scope",
        "This policy applies to permanent employees, contractual employees, and interns or trainees.",
        0.35, title="Corrective Action Policy",
    )
    found = _ids(
        _Store([blank], headings=[scope]),
        "Does the corrective action policy apply to interns?",
    )
    assert found.index("cap:4") < found.index("cap:113"), found


def test_a_section_named_by_its_title_still_wins():
    cash = _passage("coc:20", "Cash Handling:", "All cash must be counted at POS and tills reconciled daily.", 0.41,
                    title=COC, path="Part 4 – Retail-Specific Standards & Acknowledgment > Cash Handling:")
    other = _passage("coc:21", "Inventory Integrity:", "Cash and stock counts must match the records.", 0.52,
                     title=COC, path="Part 4 – Retail-Specific Standards & Acknowledgment > Inventory Integrity:")
    assert _ids(_Store([other, cash], headings=[cash]), "What does the Cash Handling section say?") == ["coc:20"]
