"""Regressions for broad policy questions and duplicated passage links."""

from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.config import model_mini, model_nano
from app.benefits import asks_for_policy_overview, policy_overview
from app.models import ChunkDraft, Completion, Employee, PolicyDraft
from app.pipeline import Pipeline, with_passage_links
from app.router import choose_model
from app.store_memory import MemoryStore

TODAY = date(2026, 11, 25)


@pytest.mark.parametrize("question", ["tell me about the inquiry process", "how does the inquiry process work?"])
def test_broad_questions_use_the_stronger_model(question):
    assert choose_model(question, []) == model_mini()


def test_direct_questions_keep_the_small_model():
    assert choose_model("Can I bring a lawyer to my inquiry?", []) == model_nano()


def test_duplicate_adjacent_links_are_not_concatenated():
    url = "https://example.org/hospitals.pdf"
    passage = SimpleNamespace(body=f"Panel hospitals: {url}\n{url}")
    answer = with_passage_links("Claim with the health card.", [passage])
    assert answer.count(url) == 1
    assert url + url not in answer


def test_wrapped_link_is_still_reassembled():
    passage = SimpleNamespace(body="Panel hospitals: https://example.org/\nhospitals.pdf")
    assert "https://example.org/hospitals.pdf" in with_passage_links("Claim.", [passage])


@pytest.mark.parametrize("question", ["travel claim form", "what is my travel allowance",
                                      "how many installments for a provident fund loan?"])
def test_specific_questions_do_not_trigger_an_overview(question):
    assert not asks_for_policy_overview(question)


def test_overview_does_not_open_an_unavailable_policy():
    store = MemoryStore()
    body = "Permanent management employees qualify for business travel."
    chunk = ChunkDraft("Travel Policy, Eligibility", "Eligibility", body, body)
    store.insert_policy(PolicyDraft("travel", "Travel Policy", "general_manager", TODAY, None, body),
                        [chunk], [[1.0]], as_of=TODAY)
    assert policy_overview(store, Employee("intern@example.com", "Intern", "intern"), TODAY, "travel") is None


def test_travel_overview_does_not_apply_field_rules_to_office_staff():
    store = MemoryStore()
    chunks = [ChunkDraft("Travel, Eligibility", "Eligibility", "Permanent managers may travel.", "Permanent managers may travel."),
              ChunkDraft("Travel, Daily Tour Allowance", "Daily Tour Allowance for Field-Based Employees",
                         "Field staff qualify at 25 kilometers.", "Field staff qualify at 25 kilometers.")]
    store.insert_policy(PolicyDraft("travel", "Travel Policy", "general_manager", TODAY, None, ""),
                        chunks, [[1.0], [1.0]], as_of=TODAY)
    answer, _ = policy_overview(store, Employee("gm@example.com", "GM", "general_manager"), TODAY, "travel")
    assert "25 kilometers" not in answer


def test_overview_does_not_leave_broken_annexure_references():
    store = MemoryStore()
    body = "The loan is interest-free. Limits are defined in Annexure A."
    chunk = ChunkDraft("Provident Fund, Entitlement", "Entitlement", body, body)
    store.insert_policy(PolicyDraft("pf", "Provident Fund Policy", "general_manager", TODAY, None, body),
                        [chunk], [[1.0]], as_of=TODAY)
    answer, _ = policy_overview(store, Employee("gm@example.com", "GM", "general_manager"), TODAY, "provident")
    from app.sanitize import soften_labels
    assert "defined in ." not in soften_labels(answer)


@pytest.mark.parametrize("title,question,body,expected", [
    ("Travel Policy", "travel policy", "Permanent management employees qualify for business travel.", "Permanent management"),
    ("Provident Fund Loan Policy", "provident fund policy", "Permanent head office employees may apply after nine months.", "nine months"),
])
def test_bare_policy_overview_uses_authorized_evidence(title, question, body, expected):
    store = MemoryStore()
    chunk = ChunkDraft(f"{title}, Eligibility", "Eligibility", body, body)
    store.insert_policy(PolicyDraft("policy", title, "general_manager", TODAY, None, body), [chunk], [[1.0]], as_of=TODAY)
    llm = Mock()
    llm.moderate.return_value = False
    llm.answer.return_value = Completion("I couldn't find this. Ask HR.")
    llm.rewrite.return_value = question
    llm.personal_query.return_value = ("", "")
    llm.split_topic_queries.return_value = [question]
    embedder = Mock()
    embedder.embed.return_value = [[1.0]]
    result = Pipeline(store, embedder, llm, today=lambda: TODAY).ask(
        Employee("gm@stylo.example", "GM", "general_manager"), question)
    assert result.outcome == "answered"
    assert expected.casefold() in result.answer.casefold()
    llm.answer.assert_not_called()
