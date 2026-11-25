"""Follow-ups, word matching, and the policy list, checked without a database or a model."""

from __future__ import annotations

from datetime import date
from unittest.mock import Mock

from app.access import asks_for_hidden
from app.models import Employee, Passage, PolicyDraft
from app.pipeline import Pipeline
from app.rewrite import contextual_query, rewrite_question
from app.store_memory import MemoryStore
from app.textutil import keyword_score

TODAY = date(2026, 11, 24)
INTERN = Employee("intern@stylo.example", "Test intern", "intern")
GM = Employee("gm@stylo.example", "Test manager", "general_manager")
TRAVEL_REFUSAL = ("I can't share the travel rules. That policy applies to permanent management employees, "
                  "and an internship is outside that group.")


def _pipeline(store: MemoryStore | None = None) -> Pipeline:
    llm = Mock()
    llm.moderate.return_value = False
    llm.classify.return_value = "allow"
    llm.personal_query.return_value = ("", "")
    llm.split_topic_queries.return_value = []
    return Pipeline(store or MemoryStore(), Mock(), llm, today=lambda: TODAY)


def test_a_pronoun_question_with_one_more_word_refers_to_the_last_answer():
    assert contextual_query("Is it paid?") is not None
    assert contextual_query("What is the gift limit?") is None


def test_and_what_about_keeps_the_travel_topic():
    history = [{"role": "user", "text": "What is my daily allowance on tour?"},
               {"role": "assistant", "text": "Rs. 1,800 per day."}]
    rewrite = rewrite_question("and what about hotel?", history)
    assert (rewrite.note, rewrite.search_query) == ("follow-up", "what is my hotel")


def test_a_question_about_a_property_alone_keeps_the_previous_subject():
    history = [{"role": "user", "text": "How do I apply for advance salary?"},
               {"role": "assistant", "text": "Open the ESS portal and click + New Advance."}]
    assert rewrite_question("What is the maximum amount?", history).needs_model
    assert not rewrite_question("What is the gift limit?", history).needs_model


def test_a_follow_up_to_a_refusal_repeats_the_refusal():
    history = [{"role": "user", "text": "What does the travel policy say about authorization?"},
               {"role": "assistant", "text": TRAVEL_REFUSAL, "outcome": "refused", "citations": []}]
    result = _pipeline().ask(INTERN, "What are the restrictions?", history)
    assert (result.outcome, result.answer) == ("refused", TRAVEL_REFUSAL)


def test_all_policies_lists_the_titles_this_role_can_open():
    store = MemoryStore()
    for policy_id, title, audience in (("coc", "Code of Conduct", "everyone"),
                                        ("travel", "Travel Policy", "general_manager"),
                                        ("pf", "Provident Fund Loan Policy", "executive")):
        store.policies[policy_id] = PolicyDraft(policy_id, title, audience, date(2025, 1, 1), None, "")
    result = _pipeline(store).ask(GM, "tell me about all policies")
    assert result.outcome == "answered"
    assert "Code of Conduct" in result.answer and "Travel Policy" in result.answer
    assert "Provident Fund" not in result.answer


def test_word_forms_and_swapped_letters_still_match():
    gifts = ("Gifts or hospitality may only be given or accepted if they are modest "
             "(not exceeding PKR 25,000 in value).")
    assert keyword_score("maximum value of a gift I can accept", gifts) == 0.75
    assert keyword_score("tell me abouit feul", "Annexure D: Fuel Entitlement List") == 0.5
    assert keyword_score("my flight is cancelled", "Cancellations and Missed Flights") == 1.0
    assert keyword_score("form", "from") == 0.0
    heading = Passage("coc:9", "coc", "Code of Conduct", "Code of Conduct, Gifts", "Gifts", gifts, "everyone",
                      TODAY, None)
    assert asks_for_hidden("What is the maximum value of a gift I can accept?", [heading])
