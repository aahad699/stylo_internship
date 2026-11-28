"""Hello and goodbye are answered locally, before search or the model."""

from datetime import date
from unittest.mock import Mock

from app.models import Employee
from app.pipeline import Pipeline
from app.replies import courtesy_reply
from app.store_memory import MemoryStore

TODAY = date(2026, 11, 27)
SARA = Employee("sara.intern@stylo.example", "Sara Intern", "intern")


def _pipeline():
    return Pipeline(MemoryStore(), Mock(), Mock(), today=lambda: TODAY)


def test_a_greeting_is_answered_without_calling_the_model():
    pipeline = _pipeline()
    result = pipeline.ask(SARA, "Hello!")
    assert result.outcome == "answered"
    assert result.answer == "Hello, Sara. Ask me about leave, travel, or another policy."
    assert pipeline.llm.method_calls == []
    assert pipeline.embedder.method_calls == []


def test_a_goodbye_uses_the_first_name():
    result = _pipeline().ask(SARA, "good bye")
    assert result.answer == "Goodbye, Sara. See you next time."


def test_a_policy_question_that_starts_politely_is_not_a_greeting():
    assert courtesy_reply("hello, what is the leave policy?", "Sara") is None
    assert courtesy_reply("bye, what is my travel allowance?", "Sara") is None
