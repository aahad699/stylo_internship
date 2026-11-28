"""Follow-ups, word matching, and the policy list, checked without a database or a model."""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import Mock, patch

from app.access import asks_for_hidden
from app.models import ChunkDraft, Completion, Employee, Passage, PolicyDraft
from app.pipeline import Pipeline
from app.prompting import user_message
from app.rewrite import cache_question, context_digest, contextual_query, recent_history, referral_request, rewrite_question
from app.store_memory import MemoryStore
from app.textutil import keyword_score

TODAY = date(2026, 11, 24)
INTERN = Employee("intern@stylo.example", "Test intern", "intern")
GM = Employee("gm@stylo.example", "Test manager", "general_manager")
STORE_MANAGER = Employee("store@stylo.example", "Test store manager", "store_manager")
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


def test_a_previous_turn_is_sent_to_the_rewrite():
    mileage = [{"role": "user", "text": "What is my mileage rate?"},
               {"role": "assistant", "text": "The repayment is deducted from the next salary."}]
    for question in ("How do I pay it back?", "What is the gift limit?"):
        rewrite = rewrite_question(question, mileage)
        assert rewrite.needs_model and rewrite.background == mileage[0]["text"]
    dress = rewrite_question("What about the dress code?", [
        {"role": "user", "text": "Who approves business travel?"},
        {"role": "assistant", "text": "Contact the approver."},
    ])
    assert dress.needs_model and dress.background == "Who approves business travel?"
    alone = rewrite_question("What is the gift limit?", [])
    assert alone.background == "" and not alone.needs_model


def test_a_follow_up_cache_key_includes_the_recent_turns():
    pipeline = _pipeline()
    pipeline.llm.rewrite.return_value = "What is the gift limit?"
    pipeline.embedder.embed.return_value = [[1.0, 0.0]]
    seen = []

    def capture(question, access, version):
        seen.append(question)
        return None

    pipeline.store.cache_lookup = capture
    history = [{"role": "user", "text": "What is my mileage rate?"},
               {"role": "assistant", "text": "Rs. 1,800 per day."}]
    pipeline.ask(GM, "What is the gift limit?", history)
    turns = recent_history(history)
    assert seen[0] == cache_question("What is the gift limit?", turns)
    other = [{"role": "user", "text": "What is my mileage rate?"},
             {"role": "assistant", "text": "The rate is paid another way."}]
    assert context_digest(recent_history(other)) != context_digest(turns)
    seen.clear()
    pipeline.ask(GM, "What is the gift limit?", [])
    assert seen[0] == cache_question("What is the gift limit?")
    seen.clear()
    pipeline.ask(GM, "How do I pay it back?", history)
    assert seen[0] == cache_question("How do I pay it back?", turns)


def test_cache_keys_fold_wording_but_not_a_shorter_question():
    same = cache_question("What is the gift limit?")
    assert cache_question("What's the gift limit?") == same
    assert cache_question("Whats the gift limit?") == same
    assert cache_question("Gift limit") != same


def _section(passage_id: str, title: str, heading: str, body: str, audience: str = "everyone") -> Passage:
    return Passage(passage_id, passage_id.split(":")[0], title, f"{title}, {heading}", heading, body,
                   audience, TODAY, None, score=1.0)


def test_a_payback_follow_up_shows_the_mileage_exchange_to_the_answer():
    pipeline = _pipeline()
    pipeline.llm.rewrite.return_value = "How do I pay the mileage repayment back?"
    pipeline.embedder.embed.side_effect = lambda queries: [[1.0, 0.0] for _ in queries]
    repayment = _section("pay:1", "Advance Salary Policy", "Repayment",
                         "Repayment is deducted from the next salary.")
    seen = {}

    def answer(**kwargs):
        seen["context"] = json.loads(user_message(kwargs["question"], kwargs["background"], kwargs["passages"]))[
            "conversation_context"]
        return Completion(repayment.body, 5, 5)

    pipeline.llm.answer.side_effect = answer
    history = [{"role": "user", "text": "What is my mileage rate?"},
               {"role": "assistant", "text": "Rs. 1,800 per day."}]
    with patch("app.pipeline.search_passages", return_value=[repayment]):
        result = pipeline.ask(GM, "How do I pay it back?", history)
    assert result.outcome == "answered", result.answer
    assert "Employee: What is my mileage rate?" in seen["context"]
    assert "Answer: Rs. 1,800 per day." in seen["context"]


def test_a_gift_question_after_mileage_is_searched_with_both():
    pipeline = _pipeline()
    pipeline.llm.rewrite.return_value = "What is the mileage limit?"
    pipeline.embedder.embed.side_effect = lambda queries: [[1.0, 0.0] for _ in queries]
    gift = _section("coc:1", "Code of Conduct", "Gifts", "The gift limit is PKR 25,000.")
    mileage = _section("travel:1", "Travel Policy", "Mileage", "Mileage is Rs. 1,800 per day.")
    queries = []

    def search(_store, _embedding, query, _audiences, _today, designation=None, **_kwargs):
        queries.append(query)
        folded = query.casefold()
        found = []
        if "gift" in folded:
            found.append(gift)
        if "mileage" in folded or "1,800" in folded:
            found.append(mileage)
        return found

    pipeline.llm.answer.return_value = Completion(gift.body, 5, 5)
    history = [{"role": "user", "text": "What is my mileage rate?"},
               {"role": "assistant", "text": "Rs. 1,800 per day."}]
    with patch("app.pipeline.search_passages", side_effect=search):
        result = pipeline.ask(GM, "What is the gift limit?", history)
    combined = [query for query in queries if "Rs. 1,800 per day." in query]
    assert len(combined) == 1
    assert "gift" in combined[0].casefold()
    assert result.outcome == "answered", result.answer
    assert "25,000" in result.answer


def test_a_show_cause_question_survives_a_rewrite_that_mentions_travel():
    pipeline = _pipeline()
    pipeline.llm.rewrite.return_value = "What is the travel authorization deadline?"
    pipeline.embedder.embed.side_effect = lambda queries: [[0.2, 1.0] if "travel" in query.casefold() else [1.0, 0.2]
                                                          for query in queries]
    corrective = _section(
        "cap:1", "Corrective Action Policy", "Initiation of Due Process",
        "The employee shall reply to the Show Cause Letter within four days of receiving it.",
    )
    travel = _section(
        "travel:1", "Travel Policy", "Authorization of Travel",
        "A travel request must reach Administration at least 7 days before travel.",
        "general_manager,hr",
    )

    def search(_store, _embedding, query, _audiences, _today, designation=None, **_kwargs):
        if "show" in query.casefold() and "cause" in query.casefold():
            return [corrective]
        if "travel" in query.casefold():
            return [travel]
        return []

    def answer(**kwargs):
        text = next(passage.body for passage in kwargs["passages"] if "four days" in passage.body)
        return Completion(text, 5, 5)

    pipeline.llm.answer.side_effect = answer
    history = [{"role": "user", "text": "What is the hotel limit?"},
               {"role": "assistant", "text": "Ask your manager."}]
    with patch("app.pipeline.search_passages", side_effect=search):
        result = pipeline.ask(STORE_MANAGER, "How many days do I have to reply to a show cause notice?", history)
    assert result.outcome == "answered", result.answer
    assert "four days" in result.answer
    assert "7 days" not in result.answer


def test_a_rewrite_follow_up_is_not_cached_and_a_first_question_is():
    pipeline = _pipeline()
    pipeline.llm.rewrite.return_value = "What is the gift limit?"
    pipeline.embedder.embed.side_effect = lambda queries: [[1.0, 0.0] for _ in queries]
    gift = _section("coc:1", "Code of Conduct", "Gifts", "The gift limit is PKR 25,000.")
    stored = []
    pipeline.store.cache_store = lambda row: stored.append(row["question"])
    pipeline.llm.answer.return_value = Completion(gift.body, 5, 5)
    history = [{"role": "user", "text": "What is my mileage rate?"},
               {"role": "assistant", "text": "Rs. 1,800 per day."}]
    with patch("app.pipeline.search_passages", return_value=[gift]):
        followed = pipeline.ask(GM, "What is the gift limit?", history)
        first = pipeline.ask(GM, "What is the gift limit?", [])
    assert followed.outcome == "answered", followed.answer
    assert first.outcome == "answered", first.answer
    assert stored == [cache_question("What is the gift limit?")]


def test_a_two_part_question_uses_a_stored_part_and_calls_the_model_for_the_miss():
    pipeline = _pipeline()
    pipeline.embedder.embed.side_effect = lambda queries: [[1.0, 0.0] for _ in queries]
    gift = _section("coc:1", "Code of Conduct", "Gifts", "The gift limit is PKR 25,000.")
    dress = _section(
        "coc:2", "Code of Conduct", "Dress code",
        "The dress code requires formal business attire during office hours.",
    )
    gift_key = cache_question("What is the gift limit?")
    dress_key = cache_question("What is the dress code?")
    saved = {
        gift_key: {
            "answer": "The gift limit is PKR 25,000.",
            "outcome": "answered",
            "citations": [{"passage_id": gift.id, "label": gift.section_label, "text": gift.body,
                           "title": gift.title}],
        }
    }
    stored = []
    pipeline.store.cache_lookup = lambda question, access, version: saved.get(question)
    pipeline.store.cache_store = lambda row: stored.append(row["question"])
    pipeline.store.passages_by_id = lambda ids: [gift] if gift.id in ids else []
    seen = {}

    def search(_store, _embedding, query, _audiences, _today, designation=None, **_kwargs):
        return [dress] if "dress" in query.casefold() else []

    def answer_topics(**kwargs):
        seen["questions"] = [topic.question for topic in kwargs["topics"]]
        seen["numbers"] = [topic.number for topic in kwargs["topics"]]
        return Completion('{"2":"The dress code requires formal business attire."}', 5, 5)

    pipeline.llm.answer_topics.side_effect = answer_topics
    question = "What is the gift limit? What is the dress code?"
    with patch("app.pipeline.search_passages", side_effect=search):
        result = pipeline.ask(GM, question)
    assert result.outcome == "answered", result.answer
    assert "25,000" in result.answer
    assert "formal business attire" in result.answer
    assert seen["questions"] == ["What is the dress code?"]
    assert seen["numbers"] == [2]
    assert dress_key in stored
    assert gift_key not in stored

    saved[dress_key] = {
        "answer": "The dress code requires formal business attire.",
        "outcome": "answered",
        "citations": [{"passage_id": dress.id, "label": dress.section_label, "text": dress.body,
                       "title": dress.title}],
    }
    pipeline.store.passages_by_id = lambda ids: [passage for passage in (gift, dress) if passage.id in ids]
    pipeline.llm.answer_topics.side_effect = AssertionError("both parts are already stored")
    with patch("app.pipeline.search_passages", side_effect=AssertionError("both parts are already stored")):
        again = pipeline.ask(GM, question)
    assert again.outcome == "answered", again.answer
    assert "25,000" in again.answer and "formal business attire" in again.answer


def test_a_one_policy_follow_up_includes_the_previous_reply():
    store = MemoryStore()
    body = "Mileage repayment is paid with the next salary and is not a separate claim."
    store.insert_policy(
        PolicyDraft("travel", "Travel Policy", "general_manager,hr", TODAY, None, ""),
        [ChunkDraft("Travel Policy, Mileage", "Mileage", body, body)],
        [[1.0, 0.0]],
        as_of=TODAY,
    )
    pipeline = _pipeline(store)
    pipeline.embedder.embed.side_effect = lambda queries: [[1.0, 0.0] for _ in queries]
    paid = _section("travel:0", "Travel Policy", "Mileage", body, "general_manager,hr")
    seen = {}
    queries = []

    def answer(**kwargs):
        seen["context"] = json.loads(user_message(kwargs["question"], kwargs["background"], kwargs["passages"]))[
            "conversation_context"]
        return Completion(body, 5, 5)

    def search(_store, _embedding, query, _audiences, _today, designation=None, **kwargs):
        queries.append((query, kwargs.get("policy_ids")))
        return [paid]

    pipeline.llm.answer.side_effect = answer
    history = [
        {"role": "user", "text": "What is my mileage rate?"},
        {"role": "assistant", "text": "Rs. 1,800 per day.", "outcome": "answered",
         "citations": [{"label": "Travel Policy, Mileage"}]},
    ]
    with patch("app.pipeline.search_passages", side_effect=search):
        result = pipeline.ask(GM, "Is it paid?", history)
    assert result.outcome == "answered", result.answer
    assert "Employee: What is my mileage rate?" in seen["context"]
    assert "Answer: Rs. 1,800 per day." in seen["context"]
    assert queries and queries[0][1] == ["travel"]
    assert all("Answer:" not in query for query, _ids in queries)


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


def _travel_store() -> MemoryStore:
    store = MemoryStore()
    section = (
        "4.4. Employees using a company-maintained vehicle for business travel are reimbursed for fuel. "
        "The mileage rate is outlined in the Annexure D."
    )
    annexure = "Grade: General Manager (GM). Mileage: Rs. 2,400. This row is the fuel reimbursement for a company vehicle."
    other = "Annexure B room limit Rs. 12,000 for a plan the employee did not ask to open."
    hidden = "Annexure A hidden rate Rs. 9,999 that a closed policy must not reveal."
    store.insert_policy(
        PolicyDraft("travel", "Travel Policy", "general_manager,hr", TODAY, None, ""),
        [
            ChunkDraft("Travel Policy, 4.4. Company vehicles", "4.4. Company vehicles", section, section),
            ChunkDraft("Travel Policy, Annexure D Fuel Entitlement", "Annexure D Fuel Entitlement", annexure, annexure,
                       content_type="table"),
            ChunkDraft("Travel Policy, Annexure A Rates", "Annexure A Rates", hidden, hidden, content_type="table"),
        ],
        [[0.0, 1.0], [1.0, 0.0], [0.2, 0.2]],
        as_of=TODAY,
    )
    gifts = "Gifts may be accepted only when they are modest and not above PKR 25,000."
    store.insert_policy(
        PolicyDraft("coc", "Code of Conduct", "everyone", TODAY, None, ""),
        [ChunkDraft("Code of Conduct, Gifts", "Gifts", gifts, gifts)],
        [[0.0, 1.0]],
        as_of=TODAY,
    )
    store.insert_policy(
        PolicyDraft("medical", "Medical Insurance Policy", "everyone", TODAY, None, ""),
        [ChunkDraft("Medical Insurance Policy, Annexure B", "Annexure B Benefit Schedule", other, other,
                    content_type="table")],
        [[0.0, 0.0]],
        as_of=TODAY,
    )
    return store


def _referral_pipeline(store: MemoryStore | None = None) -> Pipeline:
    llm = Mock()
    llm.moderate.return_value = False
    llm.classify.return_value = "allow"
    llm.personal_query.return_value = ("", "")
    llm.split_topic_queries.return_value = []
    llm.answer.side_effect = lambda **kwargs: Completion((kwargs["passages"][0].body if kwargs["passages"] else ""))
    llm.rewrite.side_effect = lambda question, turns: question
    embedder = Mock()
    embedder.embed.side_effect = lambda queries: [[1.0, 0.0] if "annexure" in q.lower() else [0.0, 1.0] for q in queries]
    return Pipeline(store or _travel_store(), embedder, llm, today=lambda: TODAY)


def _history(answer: str, passage_ids: list[str], label: str = "Travel Policy, 4.4. Company vehicles") -> list[dict]:
    return [
        {"role": "user", "text": "What mileage applies when I use a company vehicle?"},
        {"role": "assistant", "text": answer, "outcome": "answered", "citations": [{"label": label}],
         "passage_ids": passage_ids},
    ]


def test_a_referral_follow_up_is_not_a_new_question():
    assert referral_request("yes, tell me about this section") == "section"
    assert referral_request("tell me about that annexure") == "annexure"
    assert referral_request("what does this annexure say") == "annexure"
    assert referral_request("yes please") == "any"
    assert referral_request("What is the gift limit?") is None
    assert referral_request("yes, what is the gift limit?") is None


def test_a_printed_annexure_is_what_the_follow_up_opens():
    result = _referral_pipeline().ask(
        GM, "yes, tell me about this annexure",
        _history("Fuel is reimbursed. Please refer to Annexure D.", ["travel:0"]),
    )
    assert result.outcome == "answered", result.answer
    assert "2,400" in result.answer
    assert "12,000" not in result.answer
    assert "9,999" not in result.answer


def test_a_stripped_pointer_is_read_from_the_cited_passage():
    result = _referral_pipeline().ask(
        GM, "tell me about this annexure",
        _history("Fuel is reimbursed at the mileage rate.", ["travel:0"]),
    )
    assert result.outcome == "answered", result.answer
    assert "2,400" in result.answer
    assert "9,999" not in result.answer


def test_a_named_other_policy_is_the_annexure_that_is_opened():
    result = _referral_pipeline().ask(
        GM, "yes, tell me about this annexure",
        _history("Please refer to Annexure D of the Travel Policy.", ["medical:0"],
                 "Medical Insurance Policy, Annexure B"),
    )
    assert result.outcome == "answered", result.answer
    assert "2,400" in result.answer
    assert "12,000" not in result.answer


def test_a_section_pointer_opens_that_section():
    result = _referral_pipeline().ask(
        GM, "yes, tell me about this section",
        _history("Please refer to section 4.4.", ["travel:1"]),
    )
    assert result.outcome == "answered", result.answer
    assert "company-maintained vehicle" in result.answer
    assert "2,400" not in result.answer


def test_two_pointers_ask_which_one():
    result = _referral_pipeline().ask(
        GM, "yes",
        _history("Please refer to Annexure D and please refer to section 4.4.", ["travel:0"]),
    )
    assert result.outcome == "clarification", result.answer
    assert "Annexure D" in result.answer and "4.4" in result.answer
    assert "2,400" not in result.answer


def test_an_ordinary_question_does_not_use_the_referral():
    result = _referral_pipeline().ask(GM, "What is the gift limit?", _history("Please refer to Annexure D.", ["travel:0"]))
    assert "25,000" in result.answer
    assert "2,400" not in result.answer


def test_a_closed_policy_referral_is_still_refused():
    result = _referral_pipeline().ask(
        INTERN, "yes, tell me about this annexure",
        _history("Please refer to Annexure A.", ["travel:2"]),
    )
    assert result.outcome == "refused", result.answer
    assert "9,999" not in result.answer
    assert "internship" in result.answer.casefold()


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


def test_a_first_question_with_the_same_subject_reuses_the_stored_answer():
    pipeline = _pipeline()
    pipeline.embedder.embed.side_effect = lambda queries: [[1.0, 0.0] for _ in queries]
    gift = _section("coc:1", "Code of Conduct", "Gifts", "The gift limit is PKR 25,000.")
    pipeline.store.passages_by_id = lambda ids: [gift] if gift.id in ids else []
    pipeline.llm.answer.return_value = Completion(gift.body, 5, 5)
    with patch("app.pipeline.search_passages", return_value=[gift]):
        first = pipeline.ask(GM, "What is the gift limit?")
        pipeline.llm.answer.reset_mock()
        second = pipeline.ask(GM, "What is the limit on gifts?")
        pipeline.llm.answer.assert_not_called()
        mileage = pipeline.ask(GM, "What is the mileage rate?")
    assert first.outcome == "answered"
    assert second.outcome == "answered", second.answer
    assert "25,000" in second.answer
    assert mileage.answer != second.answer or mileage.outcome != "answered"


def test_a_follow_up_is_not_matched_by_meaning():
    pipeline = _pipeline()
    pipeline.embedder.embed.side_effect = lambda queries: [[1.0, 0.0] for _ in queries]
    gift = _section("coc:1", "Code of Conduct", "Gifts", "The gift limit is PKR 25,000.")
    pipeline.store.passages_by_id = lambda ids: [gift] if gift.id in ids else []
    pipeline.llm.answer.return_value = Completion(gift.body, 5, 5)
    pipeline.llm.rewrite.return_value = "What is the limit on gifts?"
    history = [{"role": "user", "text": "What is my mileage rate?"},
               {"role": "assistant", "text": "Ask your manager."}]
    with patch("app.pipeline.search_passages", return_value=[gift]):
        pipeline.ask(GM, "What is the gift limit?")
        pipeline.llm.answer.reset_mock()
        followed = pipeline.ask(GM, "What is the limit on gifts?", history)
    assert followed.outcome == "answered", followed.answer
    pipeline.llm.answer.assert_called()


def test_a_personal_follow_up_keeps_the_previous_reply_out_of_the_search():
    pipeline = _pipeline()
    pipeline.llm.personal_query.return_value = ("other", "gift limit")
    pipeline.embedder.embed.side_effect = lambda queries: [[1.0, 0.0] for _ in queries]
    gift = _section("coc:1", "Code of Conduct", "Gifts", "The gift limit is PKR 25,000.")
    queries = []

    def answer(**kwargs):
        assert "Ask your manager." in kwargs["background"]
        return Completion(gift.body, 5, 5)

    def search(_store, _embedding, query, _audiences, _today, designation=None, **_kwargs):
        queries.append(query)
        return [gift]

    pipeline.llm.answer.side_effect = answer
    history = [{"role": "user", "text": "What is my mileage rate?"},
               {"role": "assistant", "text": "Ask your manager."}]
    with patch("app.pipeline.search_passages", side_effect=search):
        result = pipeline.ask(GM, "What is my gift limit?", history)
    assert result.outcome == "answered", result.answer
    assert queries
    assert all("Answer:" not in query for query in queries)


def test_a_two_part_question_includes_the_previous_reply_in_the_answer_only():
    pipeline = _pipeline()
    pipeline.embedder.embed.side_effect = lambda queries: [[1.0, 0.0] for _ in queries]
    gift = _section("coc:1", "Code of Conduct", "Gifts", "The gift limit is PKR 25,000.")
    dress = _section("coc:2", "Code of Conduct", "Dress code",
                     "The dress code requires formal business attire during office hours.")
    queries = []
    seen = {}

    def search(_store, _embedding, query, _audiences, _today, designation=None, **_kwargs):
        queries.append(query)
        return [dress] if "dress" in query.casefold() else [gift]

    def answer_topics(**kwargs):
        seen["background"] = kwargs["background"]
        return Completion(
            '{"1":"The gift limit is PKR 25,000.","2":"The dress code requires formal business attire."}',
            5, 5,
        )

    pipeline.llm.answer_topics.side_effect = answer_topics
    history = [{"role": "user", "text": "What is my mileage rate?"},
               {"role": "assistant", "text": "Ask your manager."}]
    with patch("app.pipeline.search_passages", side_effect=search):
        result = pipeline.ask(GM, "What is the gift limit? What is the dress code?", history)
    assert result.outcome == "answered", result.answer
    assert "Ask your manager." in seen["background"]
    assert all("Answer:" not in query for query in queries)
