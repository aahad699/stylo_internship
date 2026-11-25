"""Which path answers a personal question: benefit overview, travel lookup, or policy search."""

import unittest
from datetime import date
from unittest.mock import Mock

from app.benefits import asks_for_benefit_overview
from app.models import Employee
from app.pipeline import Pipeline
from app.store_memory import MemoryStore

TODAY = date(2026, 11, 24)
GM = Employee("gm@stylo.example", "Test employee", "general_manager")
TRAVEL_ANSWER = "Travel lookup answer."
INTENTS = ("hotel", "fuel", "daily", "approval")


def routed(question: str, classifier_intent: str) -> tuple[str | None, Mock]:
    """Ask with the classifier stubbed. Return the travel intent used, or None when search answered."""
    llm = Mock(spec=["moderate", "answer", "answer_topics", "personal_query", "rewrite", "split_topic_queries"])
    llm.moderate.return_value = False
    llm.personal_query.return_value = (classifier_intent, "normalized search")
    llm.rewrite.return_value = question
    llm.split_topic_queries.return_value = [question]
    embedder = Mock()
    embedder.embed.side_effect = lambda queries: [[1.0] for _ in queries]
    pipeline = Pipeline(MemoryStore(), embedder, llm, today=lambda: TODAY)
    pipeline._personal_travel = Mock(return_value=("answered", TRAVEL_ANSWER, []))
    result = pipeline.ask(GM, question)
    if not pipeline._personal_travel.called:
        return None, llm
    assert TRAVEL_ANSWER in result.answer
    return pipeline._personal_travel.call_args.args[2], llm


class BenefitOverviewTriggerTests(unittest.TestCase):
    def test_broad_asks_get_the_overview(self):
        for question in ("list all facilities that i can avail", "what benefits am I eligible for?",
                         "What are all the perks I have?", "What benefits do I qualify for?"):
            with self.subTest(question=question):
                self.assertTrue(asks_for_benefit_overview(question))

    def test_a_named_benefit_is_not_an_overview(self):
        for question in ("What is my daily allowance?", "What daily allowance am I entitled to?",
                         "What is my hospitalization benefit?", "What is my self-stay allowance?",
                         "What is my room and board limit?", "What is my medical benefit?",
                         "What benefits am I eligible for except medical?"):
            with self.subTest(question=question):
                self.assertFalse(asks_for_benefit_overview(question))


class PersonalTravelRoutingTests(unittest.TestCase):
    def test_classifier_alone_cannot_send_a_non_travel_question_to_the_travel_lookup(self):
        for question in ("How do I apply for an advance salary?", "How much provident fund loan can I take?",
                         "Can I borrow money against my retirement savings?", "Can I work from home?"):
            for intent in INTENTS:
                with self.subTest(question=question, intent=intent):
                    self.assertIsNone(routed(question, intent)[0])

    def test_travel_questions_reach_the_travel_lookup_whatever_the_classifier_says(self):
        cases = (("What is my daily allowance on tour?", "daily"), ("what is my hotel entitlement?", "hotel"),
                 ("fuel allowance for me", "fuel"), ("Who approves my business travel?", "approval"),
                 ("TA?", "approval"), ("What is my daily allowance?", "daily"),
                 ("What daily allowance am I entitled to?", "daily"), ("What is my self-stay allowance?", "hotel"))
        for question, expected in cases:
            for intent in (*INTENTS, "other", ""):
                with self.subTest(question=question, intent=intent):
                    self.assertEqual(routed(question, intent)[0], expected)

    def test_classifier_still_resolves_travel_wording_the_rules_miss(self):
        for question, intent in (("wat is my dailly alowance", "daily"), ("travle allowance for me", "daily"),
                                 ("How much money do I get per day when I go to Karachi for office work?", "daily")):
            with self.subTest(question=question):
                self.assertEqual(routed(question, intent)[0], intent)
                self.assertIsNone(routed(question, "other")[0])

    def test_classifier_intent_must_match_the_travel_evidence(self):
        self.assertIsNone(routed("Can I accept free travel tickets from a supplier as a gift?", "approval")[0])
        self.assertIsNone(routed("wat is my dailly alowance", "hotel")[0])

    def test_medical_plan_fields_skip_the_travel_classifier(self):
        for question in ("What is my room and board limit?", "What is my hospitalization benefit?"):
            for intent in INTENTS:
                with self.subTest(question=question, intent=intent):
                    travel, llm = routed(question, intent)
                    self.assertIsNone(travel)
                    llm.personal_query.assert_not_called()


if __name__ == "__main__":
    unittest.main()
