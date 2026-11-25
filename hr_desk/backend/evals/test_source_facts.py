"""Focused policy questions must be answered from the matching authorized clause."""
from datetime import date
from unittest.mock import Mock

import pytest

from app.models import ChunkDraft, Completion, Employee, PolicyDraft
from app.pipeline import Pipeline
from app.replies import UNKNOWN
from app.store_memory import MemoryStore
from app.topics import split_topics

TODAY = date(2026, 11, 25)


def test_related_pronoun_question_is_not_split_into_independent_topics():
    assert len(split_topics("Can a trainee be suspended, and is it paid?")[0]) == 1


def test_source_lookup_does_not_bypass_policy_audience():
    store = MemoryStore()
    body = "An employee who wishes to travel with family members may do so at their own expense. The company will not be liable."
    chunk = ChunkDraft("Travel Policy, Family", "Travel of Family Members", body, body)
    store.insert_policy(PolicyDraft("travel", "Travel Policy", "general_manager", TODAY, None, ""),
                        [chunk], [[1.0]], as_of=TODAY)
    pipeline = Pipeline(store, Mock(), Mock(), today=lambda: TODAY)
    assert pipeline._source_fact(Employee("intern@example.com", "Intern", "intern"),
                                 "Will the company pay for my wife on a business trip?", TODAY) is None


@pytest.mark.parametrize("prior_answer", ["The policy has medical exclusions.", UNKNOWN])
def test_dental_follow_up_keeps_the_prior_medical_policy(prior_answer):
    store = MemoryStore()
    body = "5. Any Dental Treatment unless necessitated due to accidental injury occurring and up to the extent of pain relief."
    chunk = ChunkDraft("Medical Insurance Policy, Exclusions", "In-Patient Medical Exclusions", body, body)
    store.insert_policy(PolicyDraft("med", "Medical Insurance Policy", "everyone", TODAY, None, ""), [chunk], [[1.0]], as_of=TODAY)
    llm, embedder = Mock(), Mock()
    llm.moderate.return_value = False
    llm.answer.return_value = Completion(UNKNOWN)
    embedder.embed.return_value = [[1.0]]
    history = [{"role": "user", "text": "What does the medical insurance policy exclude?"},
               {"role": "assistant", "text": prior_answer}]
    result = Pipeline(store, embedder, llm, today=lambda: TODAY).ask(Employee("officer@example.com", "Officer", "officer"),
                                                                      "Does it cover dental treatment?", history)
    assert result.outcome == "answered", result.answer
    assert "accidental" in result.answer.casefold()


@pytest.mark.parametrize("title,heading,body,kind,question,expected", [
    ("Corrective Action Policy", "6.1.9. Suspension of Service",
     "The total period of such suspension shall not exceed maximum of 28 working days. Suspension days’ salary will be with half pay.",
     "section", "What is the maximum total suspension period during an inquiry?", "28 working days"),
    ("Corrective Action Policy", "6.1.9. Suspension of Service",
     "The total period of such suspension shall not exceed maximum of 28 working days. Suspension days’ salary will be with half pay.",
     "section", "Can a trainee be suspended, and is it paid?", "half pay"),
    ("Corrective Action Policy", "Violations subject to Progressive Corrective-Action Action",
     "22: Smoking in the office area, shop sales floor and in the stock room.",
     "table", "Is smoking in the stock room a dismissal offence?", "progressive"),
    ("Corrective Action Policy", "Violations that may be subject to Immediate Dismissal",
     "4: Sexual, racial or other harassment of fellow employees, customers or visitors to the company.",
     "table", "What happens if a salesman sexually harasses a customer?", "dismissal"),
    ("Travel Policy", "9. Travel of Family Members",
     "9.1. An employee who wishes to travel with family members may do so at their own expense. The company will not be liable to bear travel and other related expenses.",
     "section", "Will the company pay for my wife to come with me on a business trip?", "own expense"),
    ("Code of Conduct", "Gifts", "Child Labour We do not employ anyone under the age of 18 in any role.",
     "section", "What is the minimum age to hire someone?", "18"),
    ("Code of Conduct", "Gifts", "Gifts may only be accepted if they are modest (not exceeding PKR 25,000 in value), infrequent, and not intended to influence a business decision. All gifts must be reported to a manager.",
     "section", "Can I accept free travel tickets from a supplier as a gift?", "25,000"),
    ("Corrective Action Policy", "6.6. Inquiry Process", "No advocate can legally be permitted to assist a workman during the inquiry.",
     "section", "Can I bring a lawyer to my inquiry?", "advocate"),
    ("Travel Policy", "6. Daily Allowance", "Half DA is granted for same-day return trips if the work-related travel is 12 hours or less.",
     "section", "Do I get DA for a same-day trip of 10 hours?", "half da"),
    ("Corrective Action Policy", "6.4. Authority on handling Inquiry cases and recommend Corrective-Action",
     "Company: Stylo Retail Operations. Level: For all Cases of Assistant Store Manager/ Salesmen and below. Preparation of Incident Report and Inquiry Officer: RSM to prepare Incident Report. People Executive to conduct Inquiry.",
     "table", "Who prepares the incident report for a salesman's case?", "rsm"),
    ("Medical Insurance Policy", "Annexure C: In-Patient Medical Exclusions",
     "5. Any Dental Treatment, X-rays, extractions or fillings unless necessitated due to accidental injury occurring and up to the extent of pain relief.",
     "section", "Does medical insurance cover dental treatment?", "accidental"),
    ("Medical Insurance Policy", "Annexure E: In-Patient Claim Process",
     "Emergency hospitalization at a non-panel hospital: pay cash, submit original bills with the claim form within 30 days of discharge.",
     "form", "How long do I have to submit a claim after discharge from a non-panel hospital?", "30 days"),
    ("User Guidelines - Advance Salary (HO - RSC)", "Step 3: Enter Required Details",
     "Click Create & Submit. Clicking Create will save the request as Draft only. The request will not be sent for approval unless Create & Submit is selected.",
     "section", "I clicked Create but my manager hasn't received my advance request. Why?", "create & submit"),
    ("Travel Policy", "4. Mode of Travel",
     "Female employees not eligible for air travel based on their grade will follow the travel policy applicable to the lowest grade with air travel entitlement.",
     "section", "I'm a female officer, am I eligible for air travel?", "lowest grade with air travel"),
])
def test_answer_is_derived_from_matching_clause(title, heading, body, kind, question, expected):
    store = MemoryStore()
    chunk = ChunkDraft(f"{title}, {heading}", heading, body, body, content_type=kind)
    store.insert_policy(PolicyDraft("policy", title, "everyone", TODAY, None, ""), [chunk], [[1.0]], as_of=TODAY)
    if "incident report" in question.casefold():
        form = "Incident Report Guidelines. Dated. Prepared by. Designation."
        store.insert_policy(PolicyDraft("form", title, "everyone", TODAY, None, ""),
                            [ChunkDraft(f"{title}, Incident Report", "Incident Report", form, form, content_type="form")],
                            [[1.0]], as_of=TODAY)
    llm = Mock()
    llm.moderate.return_value = False
    llm.answer.return_value = Completion("The store manager prepares it." if "incident report" in question.casefold() else UNKNOWN)
    llm.personal_query.return_value = ("", "")
    llm.split_topic_queries.return_value = []
    embedder = Mock()
    embedder.embed.return_value = [[1.0]]
    employee = Employee("officer@example.com", "Officer", "officer")
    result = Pipeline(store, embedder, llm, today=lambda: TODAY).ask(employee, question)
    assert result.outcome == "answered", result.answer
    assert expected in result.answer.casefold()
