"""Another grade's entitlement stays closed after a follow-up, and HR gets the row for the grade it names."""

from __future__ import annotations

from datetime import date
from unittest.mock import Mock

from app.models import ChunkDraft, Employee, PolicyDraft
from app.pipeline import Pipeline
from app.store_memory import MemoryStore

TODAY = date(2026, 11, 24)
OFFICER = Employee("officer@stylo.example", "Test officer", "officer")
HR = Employee("hr@stylo.example", "Test HR", "hr")
GRADE_REFUSAL = "I can't share an entitlement for a different grade. Ask HR if you need help."
HOTEL_ROWS = (
    ("Assistant General Manager (AGM) & Senior Manager (SM)", "Chalet Hotel, Hotel One", "Rs. 20,000 – Rs. 24,000"),
    ("Assistant Manager (AM) & Manager (M)", "Faran Hotel, Asaish Hotel", "Rs. 12,000 – Rs. 15,000"),
)


def _pipeline() -> Pipeline:
    llm = Mock()
    llm.moderate.return_value = False
    llm.classify.return_value = "allow"
    llm.personal_query.return_value = ("", "")
    llm.split_topic_queries.return_value = []
    llm.rewrite.return_value = ""
    embedder = Mock()
    embedder.embed.side_effect = lambda queries: [[1.0] for _ in queries]
    return Pipeline(MemoryStore(), embedder, llm, today=lambda: TODAY)


def _add_hotel_schedule(pipeline: Pipeline) -> None:
    chunks = [
        ChunkDraft("Travel Policy, Annexure C", "Annexure C",
                   f"Annexure C. Grade: {grade}. Hotel Entitlement: {hotels}. Proposed Rates: {rates}.",
                   f"Grade: {grade}. Hotel Entitlement: {hotels}. Proposed Rates: {rates}.", content_type="table")
        for grade, hotels, rates in HOTEL_ROWS
    ]
    draft = PolicyDraft("travel", "Travel Policy", "everyone", TODAY, None, "\n".join(c.body for c in chunks))
    pipeline.store.insert_policy(draft, chunks, [[1.0] for _ in chunks], as_of=TODAY)


def test_a_grade_follow_up_after_an_entitlement_question_is_refused():
    history = [{"role": "user", "text": "What is my daily allowance on tour?"},
               {"role": "assistant", "text": "Rs. 1,200 per day."}]
    for follow_up in ("And for a General Manager?", "What about senior managers?"):
        result = _pipeline().ask(OFFICER, follow_up, history)
        assert (result.outcome, result.answer) == ("refused", GRADE_REFUSAL), follow_up


def test_naming_a_grade_without_an_entitlement_topic_is_not_refused():
    history = [{"role": "user", "text": "What does the Code of Conduct say about gifts?"},
               {"role": "assistant", "text": "Gifts must be modest."}]
    result = _pipeline().ask(OFFICER, "Does that apply to a General Manager too?", history)
    assert result.answer != GRADE_REFUSAL


def test_hr_gets_the_hotel_row_for_the_grade_it_names():
    pipeline = _pipeline()
    _add_hotel_schedule(pipeline)
    result = pipeline.ask(HR, "What is the hotel entitlement for a Senior Manager?")
    assert "Chalet Hotel" in result.answer, result.answer
    assert "Faran Hotel" not in result.answer, result.answer


def test_hr_daily_allowance_follow_up_uses_new_named_grade_row():
    pipeline = _pipeline()
    rows = ["Grade: Senior Executive & below. Daily Allowance: 1,200.",
            "Grade: DGM to GM. Daily Allowance: 1,800."]
    chunks = [ChunkDraft("Travel Policy, Daily Allowance", "6. Daily Allowance", body, body, content_type="table")
              for body in rows]
    pipeline.store.insert_policy(PolicyDraft("travel", "Travel Policy", "everyone", TODAY, None, ""),
                                 chunks, [[1.0], [1.0]], as_of=TODAY)
    history = [{"role": "user", "text": "What is the daily allowance for Senior Executive & below?"},
               {"role": "assistant", "text": "The daily allowance is 1,200."}]
    result = pipeline.ask(HR, "And for DGM to GM?", history)
    assert result.outcome == "answered", result.answer
    assert "1,800" in result.answer
    assert "1,200" not in result.answer
