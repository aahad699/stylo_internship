"""Place of work, permanence, medical plan, and joining date."""

from __future__ import annotations

from datetime import date

from app.models import Employee, PolicyDraft
from app.pipeline import Pipeline
from app.store_memory import MemoryStore
from unittest.mock import Mock

TODAY = date(2026, 11, 24)


def _pipeline(store: MemoryStore | None = None) -> Pipeline:
    llm = Mock()
    llm.moderate.return_value = False
    llm.classify.return_value = "allow"
    llm.personal_query.return_value = ("", "")
    llm.split_topic_queries.return_value = []
    return Pipeline(store or MemoryStore(), Mock(), llm, today=lambda: TODAY)


def _policy(store: MemoryStore, policy_id: str, title: str) -> None:
    store.insert_policy(
        PolicyDraft(policy_id, title, "general_manager,hr", TODAY, None, title),
        [],
        [],
        as_of=TODAY,
    )


def test_people_create_and_update_keep_the_saved_choice():
    store = MemoryStore()
    assert store.create_employee(
        "a@stylo.example", "Amina", "general_manager",
        employee_type="retail", is_permanent=True, medical_plan="b", joined_on=date(2020, 1, 15),
    )
    person = store.employee("a@stylo.example")
    assert person.employee_type == "retail"
    assert person.is_permanent is True
    assert person.medical_plan == "b"
    assert person.joined_on == date(2020, 1, 15)
    assert store.update_employee("a@stylo.example", "manager", False)
    kept = store.employee("a@stylo.example")
    assert kept.designation == "manager"
    assert kept.employee_type == "retail"
    assert kept.is_permanent is True
    assert store.update_employee(
        "a@stylo.example", "manager", False,
        employee_type="head_office", is_permanent=False, medical_plan="a",
        joined_on=date(2024, 6, 1), joined_on_set=True,
    )
    changed = store.employee("a@stylo.example")
    assert (changed.employee_type, changed.is_permanent, changed.medical_plan, changed.joined_on) == (
        "head_office", False, "a", date(2024, 6, 1),
    )


def test_a_non_permanent_general_manager_is_refused_travel():
    store = MemoryStore()
    _policy(store, "travel", "Travel Policy")
    person = Employee("gm@stylo.example", "Hina", "general_manager", is_permanent=False)
    result = _pipeline(store).ask(person, "What is the travel policy hotel allowance?")
    assert result.outcome == "refused", result.answer
    assert "permanent" in result.answer.casefold()


def test_a_permanent_retail_employee_is_refused_provident():
    store = MemoryStore()
    _policy(store, "pf", "Provident Fund Policy")
    person = Employee("gm@stylo.example", "Hina", "general_manager", employee_type="retail", is_permanent=True)
    result = _pipeline(store).ask(person, "What is the provident fund?")
    assert result.outcome == "refused", result.answer
    assert "Head Office" in result.answer


def test_a_permanent_head_office_employee_can_still_reach_provident():
    store = MemoryStore()
    _policy(store, "pf", "Provident Fund Policy")
    person = Employee("gm@stylo.example", "Hina", "general_manager", employee_type="head_office", is_permanent=True)
    result = _pipeline(store).ask(person, "What is the provident fund?")
    assert result.outcome != "refused"


def test_a_recent_joining_date_says_the_nine_months_are_not_complete():
    store = MemoryStore()
    _policy(store, "pf", "Provident Fund Policy")
    person = Employee(
        "gm@stylo.example", "Hina", "general_manager",
        employee_type="head_office", is_permanent=True, joined_on=date(2026, 8, 1),
    )
    result = _pipeline(store).ask(person, "Can I apply for the provident fund?")
    assert result.outcome == "answered", result.answer
    assert "not complete" in result.answer.casefold()


def test_an_empty_joining_date_does_not_guess():
    store = MemoryStore()
    _policy(store, "pf", "Provident Fund Policy")
    person = Employee("gm@stylo.example", "Hina", "general_manager", employee_type="head_office", is_permanent=True)
    pipeline = _pipeline(store)
    pipeline.embedder.embed.side_effect = lambda queries: [[1.0, 0.0] for _ in queries]
    pipeline.llm.answer.return_value = None
    result = pipeline.ask(person, "Can I apply for the provident fund?")
    assert "not complete" not in result.answer.casefold()
