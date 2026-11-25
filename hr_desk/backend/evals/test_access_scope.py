"""Who each policy and medical plan row is open to, as the policy text states it."""

import unittest
from datetime import date
from unittest.mock import Mock

from app.access import AUDIENCES, grade_band, passage_grades, reference_audience, role_reason
from app.models import ChunkDraft, Employee, Passage, PolicyDraft
from app.pipeline import Pipeline
from app.store_memory import MemoryStore

TODAY = date(2026, 11, 24)
PF = "Provident Fund Loan Policy – Head Office"
ADVANCE = "User Guidelines - Advance Salary (HO - RSC)"
MEDICAL = "Medical Insurance Policy"
HEAD_OFFICE = {"officer", "senior_executive", "executive", "assistant_manager", "manager", "senior_manager",
               "assistant_general_manager", "deputy_general_manager", "general_manager", "group_chief",
               "coo_business", "ceo", "cfo", "director", "head_hr", "hr", "employee"}
OUTSIDE_HEAD_OFFICE = {"intern", "trainee", "salesman", "store_manager", "senior_store_manager",
                       "assistant_store_manager", "branch_accountant", "regional_sales_manager", "area_sales_manager"}
PLAN_ROWS = {
    "a": "Plan: Plan A. Grades: COO, Director, CEO, CFO, HEAD, AGM, GM, Higher Management. "
         "Hospitalization Benefit (Employee, Spouse and Children): 500,000. Room & Board Limit (per day): 12,000.",
    "b": "Plan: Plan B. Grades: Manager, Assistant, Sr. Manager, Middle Management. "
         "Hospitalization Benefit (Employee, Spouse and Children): 400,000. Room & Board Limit (per day): 10,000.",
    "c": "Plan: Plan C. Grades: Executive, Designer, Lower Management. "
         "Hospitalization Benefit (Employee, Spouse and Children): 300,000. Room & Board Limit (per day): 7,000.",
    "d": "Plan: Plan D. Grades: Officers, Supervisor, Support Staff. "
         "Hospitalization Benefit (Employee, Spouse and Children): 250,000. Room & Board Limit (per day): 5,000.",
    "e": "Plan: Plan E. Grades: Other Staff. "
         "Hospitalization Benefit (Employee, Spouse and Children): 200,000. Room & Board Limit (per day): 5,000.",
}
SCHEDULE = "Annexure B: In-Patient Medical Benefit Schedule"


def roles(audience: str) -> set[str]:
    return set(audience.split(","))


def plan_rows(designation: str) -> set[str]:
    return {plan for plan, body in PLAN_ROWS.items()
            if designation in passage_grades(SCHEDULE, body, "table", MEDICAL)}


def medical_pipeline():
    llm = Mock(spec=["moderate", "answer", "answer_topics"])
    llm.moderate.return_value = False
    embedder = Mock()
    embedder.embed.side_effect = lambda queries: [[1.0] for _ in queries]
    pipeline = Pipeline(MemoryStore(), embedder, llm, today=lambda: TODAY)
    draft = PolicyDraft("medical", MEDICAL, reference_audience(MEDICAL), TODAY, None, "")
    chunks = [ChunkDraft(f"{MEDICAL}, {SCHEDULE}", SCHEDULE, body, body, content_type="table")
              for body in PLAN_ROWS.values()]
    pipeline.store.insert_policy(draft, chunks, [[1.0] for _ in chunks], as_of=TODAY)
    return pipeline


class HeadOfficeAudienceTests(unittest.TestCase):
    def test_acknowledgment_is_not_a_dgm_grade_marker(self):
        self.assertIsNone(grade_band("Acknowledgment Form: Employee Name: Employee ID: Signatures:"))
        self.assertIn("intern", passage_grades("Acknowledgment Form", "Employee ID: Signature:", "table", "Code of Conduct"))

    def test_head_office_policies_open_to_every_head_office_grade(self):
        for title in (PF, ADVANCE):
            with self.subTest(title=title):
                audience = roles(reference_audience(title))
                self.assertLessEqual(HEAD_OFFICE, audience)
                self.assertFalse(audience & OUTSIDE_HEAD_OFFICE)

    def test_refusal_outside_the_audience_names_the_policy_scope(self):
        passage = Passage("pf:4", "pf", PF, f"{PF}, 4. Eligibility", "4. Eligibility Criteria",
                          "4.1. An employee becomes eligible after Nine (09) months.", reference_audience(PF),
                          TODAY, None)
        answer = role_reason("store_manager", [passage])
        self.assertIn("Head Office", answer)
        self.assertIn("Store Manager", answer)
        self.assertNotIn("different grade", answer)


class MedicalPlanTests(unittest.TestCase):
    def test_each_designation_reads_the_plan_its_grade_label_names(self):
        expected = {
            "coo_business": {"a"}, "director": {"a"}, "ceo": {"a"}, "cfo": {"a"}, "head_hr": {"a"},
            "assistant_general_manager": {"a"}, "deputy_general_manager": {"a"}, "general_manager": {"a"},
            "group_chief": {"a"},
            "manager": {"b"}, "assistant_manager": {"b"}, "senior_manager": {"b"},
            "executive": {"c"}, "senior_executive": {"c"}, "designer": {"c"},
            "officer": {"d"}, "supervisor": {"d"}, "support_staff": {"d"},
            "other_staff": {"e"},
            "store_manager": set(), "senior_store_manager": set(), "assistant_store_manager": set(),
            "branch_accountant": set(), "regional_sales_manager": set(), "area_sales_manager": set(),
            "employee": set(), "hr": set(PLAN_ROWS),
        }
        for designation, plans in expected.items():
            with self.subTest(designation=designation):
                self.assertEqual(plan_rows(designation), plans)

    def test_hr_reads_every_row(self):
        for body in PLAN_ROWS.values():
            self.assertIn("hr", passage_grades(SCHEDULE, body, "table", MEDICAL))
        self.assertIn("hr", roles(reference_audience(MEDICAL)) & set(AUDIENCES))

    def test_dgm_gets_plan_a_room_limit(self):
        result = medical_pipeline().ask(Employee("dgm@stylo.example", "DGM", "deputy_general_manager"),
                                        "What is my medical room and board limit per day?")
        self.assertEqual(result.outcome, "answered")
        self.assertIn("12,000", result.answer)

    def test_designation_without_a_plan_is_told_to_ask_hr(self):
        result = medical_pipeline().ask(Employee("sm@stylo.example", "SM", "store_manager"),
                                        "Which Medical Takaful plan am I on as a Store Manager?")
        self.assertEqual(result.outcome, "dont_know")
        self.assertIn("Store Manager", result.answer)
        self.assertIn("Ask HR", result.answer)
        for plan in "ABCDE":
            self.assertNotIn(f"Plan {plan}", result.answer)
        for figure in ("500,000", "400,000", "300,000", "250,000", "200,000"):
            self.assertNotIn(figure, result.answer)


if __name__ == "__main__":
    unittest.main()
