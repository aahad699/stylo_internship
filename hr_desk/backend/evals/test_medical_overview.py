"""A bare medical topic, such as "medical policy" or "tell me about health", gets an overview for the asker's role."""

from __future__ import annotations

from datetime import date
from unittest.mock import Mock, patch

import pytest

from app.aliases import expand_query
from app.benefits import asks_for_medical_overview
from app.models import ChunkDraft, Employee, PolicyDraft
from app.pipeline import Pipeline
from app.store_memory import MemoryStore

TODAY = date(2026, 11, 25)
TITLE = "Medical Insurance Policy"
SCHEDULE = "Annexure B: In-Patient Medical Benefit Schedule"
AUDIENCE = "general_manager,officer,store_manager,hr"
ELIGIBILITY = ("2.1. This policy shall be extended to all permanent management employees of the company including "
               "senior store managers, store managers, assistant store managers and branch accountant. 2.2. The policy "
               "shall cover the employee, spouse and children.")
CLAIMS = ("5.1. In case of hospitalization at a panel hospital, the employee shall present the health card at the "
          "hospital and the insurer settles the bill directly with the hospital.")
PLAN_A = ("Plan: Plan A. Grades: COO, Director, CEO, CFO, HEAD, AGM, GM, Higher Management. Hospitalization Benefit "
          "(Employee, Spouse and Children): 500,000. Room & Board Limit (per day): 12,000. Normal Delivery: 100,000. "
          "Complicated /Caesarean Delivery: 180,000.")
PLAN_D = ("Plan: Plan D. Grades: Officers, Supervisor, Support Staff. Hospitalization Benefit (Employee, Spouse and "
          "Children): 250,000. Room & Board Limit (per day): 5,000. Normal Delivery: 60,000. Complicated /Caesarean "
          "Delivery: 100,200.")


def _pipeline() -> Pipeline:
    llm = Mock()
    llm.moderate.return_value = False
    llm.classify.return_value = "allow"
    llm.personal_query.return_value = ("", "")
    llm.split_topic_queries.return_value = []
    embedder = Mock()
    embedder.embed.side_effect = lambda queries: [[1.0] for _ in queries]
    pipeline = Pipeline(MemoryStore(), embedder, llm, today=lambda: TODAY)
    chunks = [
        ChunkDraft(f"{TITLE}, 2. Eligibility", "2. Eligibility", ELIGIBILITY, ELIGIBILITY),
        ChunkDraft(f"{TITLE}, 5.1. Claims in case of Panel Hospital", "5.1. Claims in case of Panel Hospital", CLAIMS, CLAIMS),
        ChunkDraft(f"{TITLE}, {SCHEDULE}", SCHEDULE, PLAN_A, PLAN_A, content_type="table"),
        ChunkDraft(f"{TITLE}, {SCHEDULE}", SCHEDULE, PLAN_D, PLAN_D, content_type="table"),
    ]
    draft = PolicyDraft("medical", TITLE, AUDIENCE, TODAY, None, "\n".join(c.body for c in chunks))
    pipeline.store.insert_policy(draft, chunks, [[1.0] for _ in chunks], as_of=TODAY)
    return pipeline


def _ask(designation: str, question: str):
    pipeline = _pipeline()
    return pipeline, pipeline.ask(Employee(f"{designation}@stylo.example", "t", designation), question)


@pytest.mark.parametrize("question", [
    "medical", "medical policy", "health policy", "tell me about health", "medical insurance ploicy",
    "tell me about insurance", "Medical Insurance Policy?",
])
def test_a_bare_medical_topic_asks_for_an_overview(question):
    assert asks_for_medical_overview(question)


@pytest.mark.parametrize("question", [
    "What is my room and board limit?", "medical claim form", "Does medical cover dental treatment?",
    "What is the GM's hospitalization benefit?", "tell me about travel",
])
def test_a_specific_medical_question_is_not_an_overview(question):
    assert not asks_for_medical_overview(question)


def test_a_general_manager_gets_plan_a_figures():
    pipeline, result = _ask("general_manager", "medical policy")
    assert result.outcome == "answered", result.answer
    for figure in ("Plan A", "500,000", "12,000"):
        assert figure in result.answer, result.answer
    for other in ("Plan D", "250,000"):
        assert other not in result.answer, result.answer
    assert "permanent management employees" in result.answer
    pipeline.llm.answer.assert_not_called()


def test_an_officer_gets_plan_d_figures():
    _, result = _ask("officer", "health policy")
    assert result.outcome == "answered", result.answer
    assert "Plan D" in result.answer and "250,000" in result.answer
    assert "Plan A" not in result.answer and "500,000" not in result.answer


def test_a_role_without_a_plan_row_is_told_to_ask_hr():
    _, result = _ask("store_manager", "medical")
    assert result.outcome == "answered", result.answer
    assert "doesn't list a plan" in result.answer
    assert "500,000" not in result.answer and "250,000" not in result.answer


def test_store_manager_eligibility_uses_policy_scope_without_inventing_plan():
    _, result = _ask("store_manager", "Am I eligible for medical insurance?")
    assert result.outcome == "answered", result.answer
    assert "store manager" in result.answer.casefold()
    assert "permanent" in result.answer.casefold()
    assert "Plan A" not in result.answer and "Plan D" not in result.answer


def test_medical_page_request_uses_page_metadata_and_only_own_plan():
    pipeline = _pipeline()
    for chunk in pipeline.store.chunks:
        if chunk["content_type"] == "table":
            chunk["page"] = 6
    with patch("app.pipeline.search_passages", return_value=[]):
        result = pipeline.ask(Employee("officer@example.com", "Officer", "officer"),
                              "Show me page 6 of the medical insurance policy.")
    assert result.outcome == "answered", result.answer
    assert "250,000" in result.answer
    assert "500,000" not in result.answer


def test_a_role_outside_the_policy_gets_no_overview():
    _, result = _ask("intern", "medical policy")
    assert result.outcome != "answered", result.answer
    assert "500,000" not in result.answer and "250,000" not in result.answer


@pytest.mark.parametrize("question", ["health policy", "medical", "tell me about health"])
def test_single_words_search_as_medical_insurance(question):
    assert "medical insurance" in expand_query(question).casefold()
