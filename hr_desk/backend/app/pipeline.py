"""One question, from the guardrails through a saved answer or a fresh one."""

from __future__ import annotations

import logging
import json
import re
import time
import uuid
from datetime import date
from dataclasses import replace

from .aliases import expand_query
from .access import (
    AUDIENCES,
    access_key,
    grade_band,
    allowed_audiences,
    asks_for_hidden,
    has_medical_plan,
    visible_passage,
    named_grades,
    names_other_grade,
    policy_row_roles,
    role_reason,
    role_label,
    shares_audience,
)
from .clauses import asked_clause, asked_page, contains_clause, focus_clauses
from .benefits import (asks_for_benefit_overview, asks_for_medical_overview, asks_for_policy_overview,
                       benefit_overview, medical_overview, policy_overview)
from .config import (
    EVIDENCE_MAX_BYTES,
    MAX_PASSAGES,
    embed_model,
    model_nano,
    model_mini,
    UNAVAILABLE,
    answer_max_tokens,
)
from .guardrails import check_question, contains_named_pay
from .llm import IncompleteCompletion
from .models import AskResult, Citation, Employee, TraceRecord
from .prompting import model_context, prompt_hash, prompt_source, system_prompt
from .replies import RATE_MISSING, REFUSAL, TOPIC_MISSING, UNKNOWN
from .retrieve import search_passages, select_passages
from .rewrite import contextual_query, question_recall, recent_history, rewrite_question, topic_words
from .router import choose_model
from .sanitize import exposes_prompt, fixed_reply, forbidden_output, sanitize, soften_labels
from .source_page import source_page_url
from .textutil import token_set
from .store_memory import utc_now
from .topics import TopicEvidence, expand_abbreviations, model_topics, needs_topic_split, split_topics, topic_kind

logger = logging.getLogger("hr_desk")

_POLICY_LOCATOR = re.compile(r"\b(?:in|of|under|from)\s+(?:the\s+)?", re.I)
_APPROVAL_REQUEST = re.compile(r"\bwho\b.*\b(?:approve\w*|authori[sz]\w*|sign\w*)\b", re.I)
_APPROVAL_FIELD = re.compile(r"(?:^|[.\n]\s*)(?:competent authority|approving authority|approved by)\s*:", re.I)
_ONE_SENTENCE = re.compile(r"\b(?:one|1|a single)\s+sentence\b", re.I)
_SUMMARY_PREFIX = re.compile(
    r"^(?:(?:in\s+(?:one|1|a single|two|2)\s+sentences?[,\s]+)|"
    r"(?:(?:please\s+)?(?:briefly\s+)?summari[sz]e\s+(?:the\s+)?))+", re.I,
)
_MEDICAL_SCHEDULE = "medical benefit schedule"
_PLAN_ID = re.compile(r"\bplan\s+([a-e])\b", re.I)
_MEDICAL_FIELDS = (
    (r"\b(?:room(?:\s*(?:and|&)\s*board)?(?:\s+(?:rent|limit|charges?))?|board limit)\b", "room & board limit"),
    (r"\b(?:normal|natural)\s+(?:delivery|birth)\b", "normal delivery"),
    (r"\b(?:caesarean|cesarean|c.section|complicated delivery)\b", "complicated /caesarean delivery"),
    (r"\b(?:hospitali[sz]ation|inpatient hospitalization)\b", "hospitalization benefit"),
)
_FOLLOW_FIELD = re.compile(r"^(?:(?:and|also)\b|(?:what|how)\s+about\b)", re.I)
_SELF_ELIGIBILITY = re.compile(r"\b(?:i|my|me)\b.*\b(?:eligible|covered|qualify)\b|\b(?:eligible|covered|qualify)\b.*\b(?:i|my|me)\b", re.I)
_RETAIL_MEDICAL_ROLES = {"senior_store_manager", "store_manager", "assistant_store_manager", "branch_accountant"}
_PERSONAL = re.compile(r"\b(?:my|for me|for myself|am i|do i|can i|should i|as an?|mujhe)\b", re.I)
_VEHICLE_CC = re.compile(r"\b(\d{2,4})\s*cc\b", re.I)
_FUEL_SCHEDULE = "fuel entitlement list"
_TRAVEL_APPROVAL = "authorization of travel"
_ENTITLEMENT = re.compile(
    r"\b(?:fuel|mileage|petrol|entitle\w*|allowances?|benefits?|coverage|cover(?:ed)?|plans?|reimburse\w*|"
    r"hotels?|stay|accommodation|lodging|DA|TA|tour|delivery|maternity|hospitali[sz]ation|room|limits?|rates?|"
    r"eligib\w*|air|flights?|vehicle)\b",
    re.I,
)
_TRAVEL_DEADLINE = re.compile(r"\b(?:when|how (?:early|far in advance|many days before)|deadline)\b.*\btravel request form\b", re.I)
_HOTEL_SCHEDULE = "annexure c"
_RULE_REQUEST = re.compile(r"\b(?:policy|rules?)\b|\bhow\b[^?.!]{0,55}\b(?:work|works|apply|applies)\b", re.I)
_TA = re.compile(r"\bTA\b", re.I)
_TA_OFFER = "If you mean travel allowance or mileage, I can show the rates for your designation."
_TA_FOLLOW_UP = re.compile(
    r"(?:yes(?:[, ]+please)?|show me(?:[, ]+please)?|(?:travel )?allowance(?: rates?)?|mileage(?: rates?)?)[?.!]*",
    re.I,
)
_FORM_FIELDS_REQUEST = re.compile(
    r"\b(?:fields?|details?|what (?:goes|belongs) in|what (?:information|data)|what .*?"
    r"(?:contains?|includ(?:e[sd]?|ing)|fill|enter|write)|"
    r"(?:list|show) .*?template)\b", re.I,
)
_CLOSING_OFFER = re.compile(
    r"(?:if (?:you(?:'d|’d| would)? (?:want|need|like)|helpful|useful|needed|desired)\b|"
    r"(?:i|we) (?:can|could|would be happy to) (?:also )?"
    r"(?:provide|format|show|share|give|help|send|create|prepare|generate|offer|supply|make|draft|download|"
    r"export|email|attach|upload|print|fill|open|write)\b|"
    r"(?:please )?let me know\b|would you like\b|next steps?\s*:)", re.I,
)
_POLICY_LIST = re.compile(r"\bpolicies\b", re.I)
_POLICY_LIST_WORDS = {"all", "list", "show", "give", "available", "there", "see", "open", "access", "have",
                      "which", "apply", "hr", "company", "every", "names"}
_COMPARISON = re.compile(r"\b(?:compar\w*|versus|vs\.?|difference between|side.by.side)\b", re.I)
_DA_CONDITION = re.compile(
    r"originating from the permanent place of work, applicable for journeys totaling "
    r"([\d,]+)\s*(?:km|kilometers?) or more \(round trip\), including transit time", re.I,
)


def _missing_requested_quantity(question: str, answer: str) -> bool:
    """Recognize an explicit missing quantity without discarding related information."""
    if re.search(r"\d|\b(?:one|two|three|four|five|six|seven|eight|nine|ten|twenty|thirty|sixty|ninety)\s+(?:days?|weeks?|months?|years?|percent)\b", answer, re.I):
        return False
    if re.search(r"\bnotice period\b", question, re.I):
        field = r"notice period"
    elif re.search(r"\bmaternity\b", question, re.I) and re.search(r"\b(?:days?|how long)\b", question, re.I):
        field = r"(?:paid\s+days|maternity\s+leave|leave\s+days|number of (?:paid )?days)"
    elif (re.search(r"\b(?:provident fund|pf)\b", question, re.I)
          and re.search(r"\b(?:deduct\w*|contribut\w*)\b", question, re.I)
          and re.search(r"\b(?:percent(?:age)?|rate)\b", question, re.I)):
        field = r"(?:percentage|deduction rate|contribution rate)"
    else:
        return False
    negative = r"(?:does not|doesn't|is not|isn't|aren't|not|couldn't|cannot|can't|unavailable)"
    text = answer.replace("’", "'")
    return bool(re.search(rf"{negative}[^.\n]{{0,90}}\b{field}\b|\b{field}\b[^.\n]{{0,90}}{negative}", text, re.I))


class Pipeline:
    def __init__(self, store, embedder, llm, today=None) -> None:
        self.store = store
        self.embedder = embedder
        self.llm = llm
        self.today = today or date.today #trace

    def ask(self, employee: Employee, question: str, history: list[dict] | None = None,
            *, request_id: str | None = None) -> AskResult:
        with model_context(self.today(), role_label(employee.designation)):
            return self._ask(employee, question, history, request_id=request_id)

    def _ask(self, employee: Employee, question: str, history: list[dict] | None = None,
             *, request_id: str | None = None) -> AskResult:
        started = time.perf_counter()
        source = prompt_source()
        state = {
            "id": request_id or uuid.uuid4().hex,
            "prompt_source": source,
            "prompt_hash": prompt_hash(source),
            "topic_count": 1,
            "finish_reason": None,
            "evidence_ids": [],
            "guardrail": "pass",
            "guardrail_reason": "ok",
            "classifier_called": False,
            "cache": "skipped",
            "rewritten_question": "",
            "history_note": "",
            "search": "skipped",
            "sections": [],
            "withheld": False,
            "scores": [],
            "model": None,
            "tokens_in": 0,
            "tokens_out": 0,
            "outcome": "error",
            "answer": UNAVAILABLE,
            "citations": [],
            "valid_until": None,
        }
        try:
            decision = check_question(question)
            if decision.action in {"block", "pay"}:
                state["guardrail"] = "blocked"
                state["guardrail_reason"] = decision.reason
                state["outcome"] = "refused" if decision.action == "pay" else "blocked"
                state["answer"] = decision.answer
                return self._finish(employee, question, state, started)
            history = history or []
            ordinal = question_recall(question)
            if ordinal is not None:
                answer, passage_ids = _recall_question(ordinal, history)
                state.update(outcome="answered", answer=answer, history_note="question-recall", evidence_ids=passage_ids)
                return self._finish(employee, question, state, started)
            ta_follow_up = bool(
                len(history) >= 2 and history[-2].get("role") == "user" and _TA.search(history[-2].get("text", ""))
                and history[-1].get("role") == "assistant" and _TA_OFFER in history[-1].get("text", "")
                and _TA_FOLLOW_UP.fullmatch(question.strip())
            )
            topics, remaining_topics = split_topics(question)
            if (len(topics) == 1 and decision.action == "allow" and needs_topic_split(question)
                    and hasattr(self.llm, "split_topic_queries")):
                try:
                    split, overflow = model_topics(self.llm.split_topic_queries(question), question)
                    if len(split) > 1:
                        topics, remaining_topics = split, overflow
                except IncompleteCompletion:
                    raise
                except Exception as error:
                    _log_error(error, state["id"], 200)
            if len(topics) > 1:
                return self._ask_topics(employee, question, topics, remaining_topics, state, started, decision.action)
            expanded_question = expand_abbreviations(question)
            grade = grade_band(question)
            grade_question = grade not in {None, "all"} and re.search(
                r"\b(fuel|entitlements?|allowances?|benefits?|coverage|plan|plans|cover|reimbursement)\b",
                question,
                re.IGNORECASE,
            )
            fuel_question = re.search(r"\b(fuel|mileage)\b", question, re.I)
            requested_roles = None
            if fuel_question and named_grades(question):
                requested_roles = policy_row_roles("Fuel Entitlement", f"Grade: {question}")
            requested_plan = _medical_plan(question)
            medical_fields = _medical_fields(question)
            if requested_plan:
                requested_roles = policy_row_roles("Medical Benefit Schedule", requested_plan[0])
            # "And for a General Manager?" carries its entitlement topic in the question before it.
            asked_before = next((turn.get("text") or "" for turn in reversed(history or [])
                                 if turn.get("role") == "user"), "")
            entitlement_topic = _ENTITLEMENT.search(question) or _ENTITLEMENT.search(asked_before)
            all_grades = (_COMPARISON.search(question) and _ENTITLEMENT.search(question)
                          and re.search(r"\b(?:every|all|other)\s+grades?\b", question, re.I))
            whole_grade_table = (re.search(r"\bannexure\s+c\b", question, re.I)
                                 and re.search(r"\btravel\b", question, re.I)
                                 and re.search(r"\b(?:word for word|verbatim|entire|full)\b", question, re.I))
            if ((employee.designation != "hr" and (all_grades or whole_grade_table))
                    or requested_roles and employee.designation not in requested_roles | {"hr"}
                    or (entitlement_topic and names_other_grade(employee.designation, question))):
                state["outcome"] = "refused"
                state["answer"] = "I can't share an entitlement for a different grade. Ask HR if you need help."
                state["withheld"] = True
                return self._finish(employee, question, state, started)
            if decision.action == "unsure":
                state["classifier_called"] = True
                verdict = self.llm.classify(question)
                if verdict != "allow":
                    state["guardrail"] = "blocked"
                    state["guardrail_reason"] = "classifier"
                    state["outcome"] = "blocked"
                    state["answer"] = REFUSAL
                    return self._finish(employee, question, state, started)
            today = self.today()
            turns = recent_history(history)
            reference = contextual_query(question)
            medical_follow_up = False
            medical_reference = bool(not requested_plan and medical_fields and _FOLLOW_FIELD.match(question.strip()) and not re.search(
                r"\b(?:travel|hotel|accommodation|provident|salary)\b", question, re.I
            ))
            latest_answer = history[-1] if history and history[-1].get("role") == "assistant" else {}
            unresolved = latest_answer.get("outcome") in {"refused", "clarification"}
            # "And the normal delivery for that plan?" after a refusal or a question back: the asker's own plan.
            own_plan_follow_up = medical_reference and unresolved
            if not requested_plan and reference is not None and not medical_reference and unresolved:
                if latest_answer.get("outcome") == "refused":
                    state.update(outcome="refused", answer=latest_answer.get("text") or REFUSAL, withheld=True,
                                 history_note="repeat-boundary")
                else:
                    state.update(outcome="clarification", answer="Which policy or plan do you mean?",
                                 history_note="follow-up-after-unresolved-turn")
                return self._finish(employee, question, state, started)
            if medical_reference and not own_plan_follow_up:
                previous, labels = _policy_context(history)
                if labels and all(_MEDICAL_SCHEDULE in label.casefold() for label in labels):
                    medical_follow_up = True
                    requested_plan = _medical_plan(previous)
                    reference = reference or question.strip()
            cacheable = not turns and reference is None and decision.action == "allow"
            question_key = question.strip()
            version = (f"{self.store.policy_version()}:{state['prompt_hash']}:exact-v14:"
                       f"{today.isoformat()}:{embed_model()}:{model_nano()}:{model_mini()}")
            key = access_key(employee.designation)
            cached = self.store.cache_lookup(question_key, key, version) if cacheable else None
            if cacheable:
                state["cache"] = "miss"
            if cached:
                items = cached["citations"] or []
                ids = {item.get("passage_id") for item in items}
                if items and None not in ids:
                    sources = self.store.passages_by_id(list(ids))
                    audiences = set(allowed_audiences(employee.designation))
                    if len(sources) == len(ids) and all(
                        _visible(employee.designation, passage)
                        and shares_audience(passage.audience, audiences)
                        and passage.effective_from <= today
                        and (passage.effective_to is None or passage.effective_to >= today)
                        for passage in sources
                    ):
                        state["valid_until"] = min((p.effective_to for p in sources if p.effective_to), default=None)
                        state["cache"] = "hit"
                        state["outcome"] = cached["outcome"]
                        state["answer"] = cached["answer"]
                        state["evidence_ids"] = [item["passage_id"] for item in items]
                        state["citations"] = [
                            Citation(item["label"], item["text"], item.get("image") or "",
                                     item["passage_id"], item.get("title") or "")
                            for item in items
                        ]
                        state["sections"] = [item.label for item in state["citations"]]
                        return self._finish(employee, question, state, started)

            if self.llm.moderate(question):
                state["guardrail"] = "blocked"
                state["guardrail_reason"] = "moderation"
                state["outcome"] = "blocked"
                state["answer"] = REFUSAL
                return self._finish(employee, question, state, started)
            if (employee.designation in {"intern", "trainee"} and re.search(r"\btravel policy\b", question, re.I)
                    and re.search(r"\b(?:interns?|internship|trainees?)\b", question, re.I)
                    and re.search(r"\b(?:appl(?:y|ies)|eligib\w*|cover\w*)\b", question, re.I)):
                audiences = set(allowed_audiences(employee.designation))
                policies = [p for p in self.store.list_policies() if p.title.casefold() == "travel policy"
                            and not shares_audience(p.audience, audiences)]
                if policies:
                    state.update(outcome="refused", answer=role_reason(employee.designation, policies), withheld=True)
                    return self._finish(employee, question, state, started)
            exact = self._source_fact(employee, question, today)
            prior_reply = fixed_reply(latest_answer.get("text", ""))
            if (not exact and latest_answer and not unresolved
                    and (not prior_reply or (prior_reply == UNKNOWN and re.search(r"\bdental\b", question, re.I)))):
                prior_question = next((turn.get("text", "") for turn in reversed(history) if turn.get("role") == "user"), "")
                if (re.fullmatch(r"where (?:can i |to )?find (?:it|this|that)[?.!]*", question.strip(), re.I)
                        and re.search(r"\bfuel\b", prior_question, re.I)):
                    outcome, _, sources = self._personal_travel(employee, prior_question, "fuel", today)
                    if outcome == "answered" and sources:
                        exact = "You can find this in the Travel Policy under Fuel Entitlement.", sources
                elif re.fullmatch(r"(?:and\s+)?(?:how many|what (?:are|about))\s+installments?[?.!]*", question.strip(), re.I):
                    exact = self._source_fact(employee, f"{prior_question} {question}", today)
                elif (re.search(r"\bdental\b", question, re.I) and re.search(r"\b(?:it|that|this)\b", question, re.I)
                      and re.search(r"\b(?:medical|health|takaful)\b", prior_question, re.I)):
                    exact = self._source_fact(employee, f"{prior_question} {question}", today)
                elif (employee.designation == "hr" and re.match(r"^\s*(?:and|what about)\b", question, re.I)
                      and re.search(r"\b(?:daily allowance|per diem|DA)\b", prior_question, re.I)
                      and (requested := named_grades(question))):
                    audiences = set(allowed_audiences(employee.designation))
                    rows = [p for p in self.store.heading_hits(["daily allowance"], audiences, today)
                            if "travel" in p.title.casefold() and p.content_type == "table"
                            and self._topic_visible(employee.designation, p, audiences)
                            and requested <= (policy_row_roles(p.heading, p.body, p.title) or set())]
                    if len(rows) == 1:
                        exact = friendly_table(rows, question), rows
                elif (re.fullmatch(r"who\s+(?:approves|authori[sz]es|signs)\s+(?:it|that|this)[?.!]*", question.strip(), re.I)
                      and re.search(r"\btravel request form\b", prior_question, re.I)):
                    outcome, answer, sources = self._personal_travel(employee, prior_question, "approval", today)
                    if outcome == "answered":
                        exact = answer, sources
            if exact:
                answer, sources = exact
                state.update(outcome="answered", answer=answer, search="heading-lookup",
                             rewritten_question=question.strip(),
                             sections=list(dict.fromkeys(p.section_label for p in sources)),
                             citations=[_citation(p) for p in sources],
                             valid_until=min((p.effective_to for p in sources if p.effective_to), default=None))
                return self._finish(employee, question, state, started)
            if _TRAVEL_DEADLINE.search(question):
                audiences = set(allowed_audiences(employee.designation))
                sources = [p for p in self.store.heading_hits([_TRAVEL_APPROVAL], audiences, today)
                           if self._topic_visible(employee.designation, p, audiences) and p.content_type == "section"]
                answer = _travel_authorization_summary(question, sources)
                if answer:
                    state.update(outcome="answered", answer=answer, search="heading-lookup",
                                 rewritten_question=question.strip(), sections=[p.section_label for p in sources],
                                 citations=[_citation(p) for p in sources],
                                 valid_until=min((p.effective_to for p in sources if p.effective_to), default=None))
                    return self._finish(employee, question, state, started)

            if asks_for_benefit_overview(question):
                answer, sources = benefit_overview(self.store, employee, today)
                state.update(outcome="answered" if sources else "dont_know", answer=answer, search="done",
                             history_note="benefit-overview", rewritten_question=question.strip(),
                             sections=list(dict.fromkeys(p.section_label for p in sources)),
                             citations=[_citation(p) for p in sources],
                             valid_until=min((p.effective_to for p in sources if p.effective_to), default=None))
                return self._finish(employee, question, state, started)

            overview = medical_overview(self.store, employee, today) if asks_for_medical_overview(question) else None
            if overview:
                answer, sources = overview
                state.update(outcome="answered", answer=answer, search="heading-lookup",
                             history_note="medical-overview", rewritten_question=question.strip(),
                             sections=list(dict.fromkeys(p.section_label for p in sources)),
                             citations=[_citation(p) for p in sources],
                             valid_until=min((p.effective_to for p in sources if p.effective_to), default=None))
                return self._finish(employee, question, state, started)

            overview_topic = asks_for_policy_overview(question)
            overview = policy_overview(self.store, employee, today, overview_topic) if overview_topic else None
            if overview:
                answer, sources = overview
                state.update(outcome="answered", answer=answer, search="heading-lookup",
                             history_note="policy-overview", rewritten_question=question.strip(),
                             sections=list(dict.fromkeys(p.section_label for p in sources)),
                             citations=[_citation(p) for p in sources],
                             valid_until=min((p.effective_to for p in sources if p.effective_to), default=None))
                return self._finish(employee, question, state, started)

            catalog = _policy_catalog(question, self.store, employee.designation, today)
            if catalog:
                state.update(outcome="answered", search="done", history_note="policy-list",
                             rewritten_question=question.strip(),
                             answer="You can ask about these policies:\n" + "\n".join(f"- {title}" for title in catalog))
                return self._finish(employee, question, state, started)

            form_link = self._travel_form_link(employee, question, today)
            if form_link:
                outcome, answer, sources = form_link
                state.update(outcome=outcome, answer=answer, search="done", history_note="form-link",
                             withheld=outcome == "refused", sections=[p.section_label for p in sources],
                             citations=[_citation(p) for p in sources],
                             valid_until=min((p.effective_to for p in sources if p.effective_to), default=None))
                return self._finish(employee, question, state, started)

            named = _named_policy_scope(question, self.store)
            personal = _personal_travel_intent(expanded_question, fuel_question, requested_plan)
            hotel_follow = rewrite_question(expanded_question, turns)
            hotel_follow_up = False
            if not personal and hotel_follow.note == "follow-up":
                followed = _personal_travel_intent(hotel_follow.search_query, fuel_question, requested_plan)
                if followed in {"hotel", "fuel", "daily", "tour"}:
                    personal = followed
                    hotel_follow_up = True
            if ta_follow_up:
                personal = "fuel"
            elif re.fullmatch(r"TA[?.!]*", question.strip(), re.I):
                personal = "approval"
            personal_medical = own_plan_follow_up or bool(
                not requested_plan and not reference and _PERSONAL.search(question)
                and (medical_fields or re.search(r"\b(?:medical|health insurance|medical cover|hospitali[sz]ation|hospital|takaful|maternity|delivery)\b", question, re.I))
                # The plan schedule holds amounts. Claims, ages, dependents and leave are answered elsewhere.
                and not re.search(r"\b(?:claim|panel|form|submit|reimburse|age|child(?:ren)?|dependents?|spouse"
                                  r"|parents?|leave)\b", question, re.I)
            )
            personal_search = ""
            if (not personal and not personal_medical and not named and not reference and not requested_plan
                    and not medical_follow_up and not asked_clause(question) and _PERSONAL.search(question)
                    and hasattr(self.llm, "personal_query")):
                try:
                    intent, personal_search = self.llm.personal_query(question)
                    if intent in _TRAVEL_EVIDENCE and _TRAVEL_EVIDENCE[intent].search(_travel_spelling(expanded_question)):
                        personal = intent
                except IncompleteCompletion:
                    raise
                except Exception as error:
                    _log_error(error, state["id"], 200)
            if personal and (not requested_roles or employee.designation in requested_roles):
                # Don't replace a question explicitly scoped to another document.
                if not named or any(p.title.casefold() == "travel policy" and p.id in named[1]
                                    for p in self.store.list_policies()):
                    outcome, answer, sources = self._personal_travel(employee, question, personal, today)
                    state.update(outcome=outcome, answer=answer, search="designation-lookup",
                                 history_note=("ta-follow-up" if ta_follow_up else "hotel-follow-up" if hotel_follow_up else "employee-designation"),
                                 rewritten_question=("fuel allowance for me" if ta_follow_up else hotel_follow.search_query if hotel_follow_up else question.strip()),
                                 withheld=outcome == "refused", sections=[p.section_label for p in sources],
                                 citations=[_citation(p) for p in sources] if outcome == "answered" else [],
                                 valid_until=min((p.effective_to for p in sources if p.effective_to), default=None))
                    return self._finish(employee, question, state, started)

            rewrite = rewrite_question(expanded_question, turns)
            earlier = _previous_boundary(turns)
            if earlier and not topic_words(question):
                state["history_note"] = "repeat-boundary"
                state["rewritten_question"] = question.strip()
                state["outcome"] = "refused"
                state["answer"] = earlier
                return self._finish(employee, question, state, started)
            policy_ids = None
            search_text = ""
            approval_rows = []
            answer_question = expanded_question.strip()
            if named:
                subject, policy_ids = named
                subject = _SUMMARY_PREFIX.sub("", subject).strip() or subject
                state.update(rewritten_question=subject, history_note="named-policy")
                # "Who counts as a dependent" alone is too vague to embed. The title gives it the document's
                # context; aliases of the title, such as hospitalization for medical insurance, would not.
                title = next((p.title for p in self.store.list_policies() if p.id in policy_ids), "")
                search_text = f"{expand_query(subject)} {title}".strip()
                background = ""
            elif (requested_plan and not medical_follow_up) or personal_medical:
                state.update(rewritten_question=question.strip(), history_note="explicit-medical-plan")
                background = ""
            elif reference is not None:
                previous, labels = _policy_context(history)
                policies = self.store.list_policies() if labels else []
                titles = {policy.title for policy in policies if any(
                    label == policy.title or label.startswith((f"{policy.title}, ", f"{policy.title}, p. "))
                    for label in labels
                )}
                if len(titles) != 1:
                    state.update(outcome="clarification", answer="Which policy or topic do you mean?",
                                 history_note="missing-policy-context")
                    return self._finish(employee, question, state, started)
                title = titles.pop()
                policy_ids = [policy.id for policy in policies if policy.title == title]
                state["rewritten_question"] = f"{reference or question.strip()} {title} {previous}".strip()
                # The search is already limited to this document and the earlier question gives the context.
                # Its title in the text would favour headings that repeat it, such as Corrective-Action.
                search_text = expand_query(f"{reference or question.strip()} {previous}".strip())
                state["history_note"] = "policy-follow-up"
                background = previous
                answer_question = f"{reference or question.strip()} under the {title}."
                if _APPROVAL_REQUEST.search(reference):
                    for label in dict.fromkeys(labels):
                        if not label.startswith(f"{title}, "):
                            continue
                        heading = re.sub(r", p\. \d+$", "", label[len(title) + 2:])
                        for policy_id in policy_ids:
                            approval_rows.extend(
                                row for row in self.store.heading_rows(
                                    policy_id, heading, set(allowed_audiences(employee.designation)), today
                                ) if row.content_type == "table" and _APPROVAL_FIELD.search(row.body)
                                and _visible(employee.designation, row)
                            )
            elif personal_search:
                # The normalization can improve retrieval but cannot replace the user's request.
                state.update(rewritten_question=f"{question.strip()} {personal_search}", history_note="personal-query")
                background = ""
            elif rewrite.needs_model:
                rewritten = self.llm.rewrite(question, turns) or question.strip()
                asked = topic_words(question)
                if asked and not asked <= token_set(rewritten):
                    rewritten = f"{rewrite.background} {question}".strip()
                    state["history_note"] = "kept-question"
                else:
                    state["history_note"] = "model"
                state["rewritten_question"] = rewritten
                background = ""
            else:
                state["rewritten_question"] = _SUMMARY_PREFIX.sub("", rewrite.search_query).strip() or rewrite.search_query
                state["history_note"] = rewrite.note
                background = rewrite.background

            audiences = set(allowed_audiences(employee.designation))
            chosen = []
            scope = {} if policy_ids is None else {"policy_ids": policy_ids}
            embedding = None
            medical_lookup = bool(requested_plan or medical_follow_up or personal_medical)
            report_rows = self._report_rows(answer_question, employee.designation, audiences, today, scope)
            summary_fields_requested = _asks_summary_fields(answer_question)
            if summary_fields_requested and not _summary_report_source_fields(report_rows):
                state.update(outcome="dont_know", answer=UNKNOWN, search="heading-lookup")
                return self._finish(employee, question, state, started)
            if report_rows:
                passages = report_rows
                answer_question = re.sub(r"\bsummart report\b", "summary report", answer_question, flags=re.I)
            elif medical_lookup:
                passages = [
                    row for row in self.store.heading_hits([_MEDICAL_SCHEDULE], audiences, today, **scope)
                    if row.content_type == "table" and "medical" in row.title.casefold()
                    and _visible(employee.designation, row)
                    and (not requested_plan or _row_plan(row.body) == requested_plan[1].lower())
                    and (not personal_medical or employee.designation in
                         (policy_row_roles(row.heading, row.body, row.title) or set()))
                ]
                if (personal_medical and not passages and not has_medical_plan(employee.designation)
                        and any("medical" in policy.title.casefold() and shares_audience(policy.audience, audiences)
                                for policy in self.store.list_policies())):
                    state.update(outcome="dont_know", search="heading-lookup", answer=(
                        "The medical policy's benefit schedule doesn't list a plan for the "
                        f"{role_label(employee.designation)} designation. Ask HR which plan applies to you."))
                    return self._finish(employee, question, state, started)
            elif approval_rows:
                passages = list({row.id: row for row in approval_rows}.values())
            else:
                search_text = search_text or expand_query(state["rewritten_question"])
                embedding = self.embedder.embed([search_text])[0]
                passages = search_passages(self.store, embedding, search_text, audiences, today, **scope)
            passages = [p for p in passages if not contains_named_pay(f"{p.title}\n{p.body}")]
            if requested_roles and not medical_lookup:
                passages = [
                    passage for passage in passages if passage.content_type == "table"
                    and "fuel entitlement" in passage.heading.casefold()
                    and (roles := policy_row_roles(passage.heading, passage.body, passage.title))
                    and roles != policy_row_roles("Fuel Entitlement", "Grade: All Levels")
                    and requested_roles <= roles
                ]
            elif grade_question and employee.designation in {"employee", "hr"}:
                # A named grade picks its own row. Bands put "Senior Manager" with "AM & M", not "AGM & SM".
                named = named_grades(question)
                matching_rows = [
                    passage for passage in passages if passage.content_type == "table" and (
                        bool(named & (policy_row_roles(passage.heading, passage.body, passage.title) or set()))
                        if named else grade_band(f"{passage.heading} {passage.body}") == grade
                    )
                ]
                if matching_rows:
                    passages = matching_rows
            state["search"] = "done"
            distance = _distance_rule_answer(question, passages, self.store, audiences, today)
            if distance:
                answer, sources = distance
                state.update(answer=answer, outcome="answered", answer_kind="distance-rule", model="deterministic",
                             evidence_ids=[p.id for p in sources], sections=[p.section_label for p in sources],
                             citations=[_citation(p) for p in sources])
                return self._finish(employee, question, state, started)
            everywhere = []
            if policy_ids is None and embedding is not None and employee.designation != "hr":
                # Refuse on what the person asked. The model's search wording can name a policy they never did.
                asked_text, asked_embedding = search_text, embedding
                if personal_search:
                    asked_text = expand_query(question)
                    asked_embedding = self.embedder.embed([asked_text])[0]
                everywhere = [p for p in search_passages(self.store, asked_embedding, asked_text, set(AUDIENCES), today)
                              if not contains_named_pay(f"{p.title}\n{p.body}")]
            # A weaker passage from an open policy does not answer what a closed policy covers.
            closed = everywhere[:1] if (everywhere and not shares_audience(everywhere[0].audience, audiences)
                                        and asks_for_hidden(question, everywhere[:1])) else []
            if passages and not closed:
                chosen = select_passages(passages)
                probe = state["rewritten_question"] if policy_ids or personal_search or not topic_words(question) else question
                if state["history_note"] in {"model", "kept-question"} and not grade_band(question):
                    # "And how many installments?" only names its subject through the rewrite.
                    probe = state["rewritten_question"]
                if named:
                    # The named document is part of what the person asked, such as page 5 of the Travel Policy.
                    probe = question
                number = asked_clause(question)
                matched = chosen if medical_lookup or report_rows else [
                    p for p in chosen
                    if asks_for_hidden(probe, [p]) or contains_clause(f"{p.heading}\n{p.body}", number)
                ]
                if not matched and not names_other_grade(employee.designation, question):
                    state["outcome"] = "dont_know"
                    state["answer"] = UNKNOWN
                    state["citations"] = []
                    chosen = []
                else:
                    chosen = matched or chosen
                visible = [passage for passage in chosen if _visible(employee.designation, passage)]
                if chosen and not visible:
                    visible_matches = [
                        passage
                        for passage in passages
                        if _visible(employee.designation, passage) and asks_for_hidden(probe, [passage])
                    ]
                    if visible_matches and not names_other_grade(employee.designation, question):
                        chosen = select_passages(visible_matches)
                    elif asks_for_hidden(probe, chosen) or names_other_grade(employee.designation, question):
                        state["withheld"] = True
                        state["outcome"] = "refused"
                        state["answer"] = role_reason(employee.designation, chosen)
                        state["sections"] = [passage.section_label for passage in chosen]
                        state["citations"] = []
                        chosen = []
                    else:
                        state["outcome"] = "dont_know"
                        state["answer"] = UNKNOWN
                        state["citations"] = []
                        chosen = []
                else:
                    chosen = visible
                if state["outcome"] != "refused":
                    focused = focus_clauses(chosen, question)
                    if focused is not None:
                        chosen = focused
                        titles = list(dict.fromkeys(passage.title for passage in chosen))
                        if len(titles) > 1:
                            options = ", ".join(titles)
                            state["outcome"] = "clarification"
                            state["answer"] = f"Which policy do you mean for point {asked_clause(question)}? I found it in {options}."
                            return self._finish(employee, question, state, started)
                        if not chosen:
                            state["outcome"] = "dont_know"
                            state["answer"] = UNKNOWN
                    state["sections"] = [passage.section_label for passage in chosen]
                state["scores"] = [
                    {"label": passage.section_label, "score": round(passage.score, 3)}
                    for passage in chosen
                ]
                if chosen and all(passage.content_type == "table" for passage in chosen):
                    state["outcome"] = "answered"
                    state["answer"] = _dress_code_rule(chosen, answer_question) or friendly_table(chosen, question)
                    if all(_FUEL_SCHEDULE in p.heading.casefold() for p in chosen):
                        applicable = [p for p in chosen if "all levels" in p.body.casefold()
                                      or employee.designation in (policy_row_roles(p.heading, p.body, p.title) or set())]
                        state["answer"] = _fuel_rates(applicable, question) or UNKNOWN
                        if not applicable:
                            state["outcome"] = "dont_know"
                    if medical_lookup and medical_fields:
                        state["answer"] = _medical_field_answer(chosen, medical_fields, question)
                        if state["answer"] == UNKNOWN:
                            state["outcome"] = "dont_know"
                    if fuel_question and len(chosen) > 1 and all(
                        "fuel entitlement" in passage.heading.casefold() for passage in chosen
                    ) and sum(
                        employee.designation in (policy_row_roles(passage.heading, passage.body, passage.title) or set())
                        and "all levels" not in passage.body.casefold() for passage in chosen
                    ) > 1:
                        state["answer"] = "The policy lists more than one row for your designation. Ask HR which one applies.\n\n" + state["answer"]
                    if len(chosen) == 1 and approval_rows:
                        fields = dict(_fields(chosen[0].body))
                        approver = next((value for name, value in fields.items() if _APPROVAL_FIELD.search(f"{name}:")), "")
                        if approver:
                            state["answer"] = f"{approver} must approve the request."
                    elif approval_rows:
                        state["answer"] = "The approver depends on the designation:\n\n" + state["answer"]
                    if state["outcome"] == "answered":
                        state["citations"] = [_citation(passage, employee.designation == "hr") for passage in chosen]
                elif chosen:
                    direct_answer = (
                        _summary_report_fields(chosen, question)
                        or _retail_eligibility(employee.designation, question, chosen)
                        or _travel_authorization_summary(question, chosen)
                    )
                    if direct_answer:
                        state.update(outcome="answered", answer=_short_answer(direct_answer, question),
                                     citations=[_citation(passage) for passage in chosen])
                        state["valid_until"] = min((p.effective_to for p in chosen if p.effective_to), default=None)
                        return self._finish(employee, question, state, started)
                    evidence = []
                    remaining = EVIDENCE_MAX_BYTES
                    for passage in chosen:
                        size = len(f"{passage.section_label}\n{passage.body}".encode("utf-8")) + 80
                        if size > remaining:
                            break
                        remaining -= size
                        evidence.append(passage)
                    if not evidence:
                        raise RuntimeError("Policy evidence exceeds the request budget")
                    chosen = evidence
                    model = choose_model(question, chosen)
                    state.update(model=model, evidence_ids=[p.id for p in chosen])
                    completion = self.llm.answer(
                        system=system_prompt(today, role_label(employee.designation), source=state["prompt_source"]),
                        question=answer_question,
                        background=background,
                        passages=chosen,
                        model=model,
                        max_tokens=answer_max_tokens(model),
                    )
                    state["finish_reason"] = completion.finish_reason
                    state["tokens_in"] = completion.tokens_in
                    state["tokens_out"] = completion.tokens_out
                    allowed_numbers = " ".join(
                        [
                            today.isoformat(),
                            *(passage.effective_from.isoformat() for passage in chosen),
                            *(
                                passage.effective_to.isoformat()
                                for passage in chosen
                                if passage.effective_to
                            ),
                        ]
                    )
                    draft = soften_labels(completion.text)
                    if forbidden_output(draft):
                        state.update(answer=draft)
                        return self._finish(employee, question, state, started)
                    answer = sanitize(
                        _strip_closing_offers(draft),
                        [passage.body for passage in chosen],
                        allowed_numbers,
                    )
                    if answer == UNKNOWN and named and _ONE_SENTENCE.search(question) and _SUMMARY_PREFIX.match(question):
                        answer = _named_summary_excerpt(state["rewritten_question"], chosen)
                    answer = _short_answer(answer, question)
                    if answer == REFUSAL:
                        state["outcome"] = "refused"
                        state["answer"] = REFUSAL
                    elif answer == UNKNOWN:
                        state["outcome"] = "dont_know"
                        state["answer"] = UNKNOWN
                    else:
                        state["outcome"] = "answered"
                        state["answer"] = answer
                        state["citations"] = [_citation(passage, employee.designation == "hr") for passage in chosen]
            elif everywhere:
                hidden = closed or [p for p in everywhere if not shares_audience(p.audience, audiences)
                                    or not _visible(employee.designation, p)]
                if hidden and asks_for_hidden(question, hidden):
                    state["withheld"] = True
                    state["outcome"] = "refused"
                    state["answer"] = role_reason(employee.designation, hidden)
                else:
                    state["outcome"] = "dont_know"
                    state["answer"] = UNKNOWN
            else:
                state.update(outcome="dont_know", answer=UNKNOWN)

            if state["outcome"] == "answered":
                if re.search(r"\b(link|url|form|submit|apply|file|claim)\b", question, re.I):
                    state["answer"] = with_passage_links(state["answer"], chosen)
                state["valid_until"] = min((p.effective_to for p in chosen if p.effective_to), default=None)
            if cacheable and state["outcome"] == "answered" and state["citations"]:
                try:
                    self.store.cache_store(
                        {
                            "id": uuid.uuid4().hex,
                            "access_key": key,
                            "policy_version": version,
                            "question": question_key,
                            "answer": state["answer"],
                            "citations": [
                                {"label": item.label, "text": item.text, "image": item.image,
                                 "passage_id": item.passage_id, "title": item.title}
                                for item in state["citations"]
                            ],
                            "outcome": state["outcome"],
                        }
                    )
                except Exception as error:
                    _log_error(error, state["id"], 200)
        except IncompleteCompletion as error:
            _log_error(error, state["id"], 200)
            state.update(outcome="dont_know", answer=UNKNOWN, citations=[], finish_reason=error.reason,
                         model=error.model or state["model"])
        except Exception as error:
            _log_error(error, state["id"], 503)
            state["outcome"] = "error"
            state["answer"] = UNAVAILABLE
            state["citations"] = []
        return self._finish(employee, question, state, started)

    def _travel_form_link(self, employee: Employee, question: str, today: date) -> tuple | None:
        claim = re.search(r"\btravel\s+(?:claim|expense)\s+form\b", question, re.I)
        request = re.search(r"\btravel\s+request\s+form\b", question, re.I)
        if bool(claim) == bool(request):
            return None
        explicit_link = re.search(r"\b(?:link|url|address)\b", question, re.I)
        if not explicit_link and (not claim or not (
            re.fullmatch(r"(?:the\s+)?travel\s+(?:claim|expense)\s+form(?:\s+for me)?[?.!]*", question.strip(), re.I)
            or _FORM_FIELDS_REQUEST.search(question)
        )):
            return None
        heading = "travel expense form" if claim else "travel request form"
        audiences = set(allowed_audiences(employee.designation))
        sources = [p for p in self.store.heading_hits([heading], audiences, today)
                   if p.title.casefold() == "travel policy" and p.content_type == "form"
                   and shares_audience(p.audience, audiences) and _visible(employee.designation, p)]
        urls = {url.rstrip(".,)") for p in sources for url in _LINK.findall(_joined_links(p.body))}
        if len(urls) != 1:
            if not sources and employee.designation in {"intern", "trainee"}:
                return "refused", "I can't share the travel form for your designation. Ask HR if you need help.", []
            return "dont_know", UNKNOWN, []
        url = urls.pop()
        if explicit_link and re.search(r"\b(?:only|just)\b", question, re.I):
            return "answered", url, sources
        if claim:
            fields = _claim_form_fields(sources)
            if fields:
                return "answered", f"{_field_list('Travel Claim Form', fields)}\n\nForm link: {url}", sources
        return "answered", f"{'Travel Claim Form' if claim else 'Travel Request Form'}: {url}", sources

    def _ask_topics(self, employee, question, topics, remaining_topics, state, started, guard_action):
        today = self.today()
        role = employee.designation
        audiences = set(allowed_audiences(role))
        state["classifier_called"] = guard_action == "unsure"
        state["topic_count"] = len(topics)
        if ((guard_action == "unsure" and self.llm.classify(question) != "allow")
                or self.llm.moderate(question)):
            state.update(outcome="blocked", guardrail="blocked", guardrail_reason="multi-topic-input", answer=REFUSAL)
            return self._finish(employee, question, state, started)
        answers = {}
        accepted = {}
        pending = {}
        searches = []
        failed = False
        for index, topic in enumerate(topics, 1):
            query = expand_abbreviations(topic.query)
            if check_question(query).action in {"block", "pay"}:
                state.update(outcome="refused", guardrail="blocked", guardrail_reason="topic-input", answer=REFUSAL)
                return self._finish(employee, question, state, started)
            kind = topic_kind(query)
            personal = bool(_PERSONAL.search(query))
            explicit = _medical_plan(query)
            named = _named_policy_scope(query, self.store)
            scope = {} if not named else {"policy_ids": named[1]}
            requested = (policy_row_roles("Medical Benefit Schedule", explicit[0]) if explicit else
                         policy_row_roles("Fuel Entitlement", f"Grade: {query}")
                         if kind == "fuel" and named_grades(query) else None)
            if requested and role not in requested | {"hr"}:
                state["withheld"] = True
                continue

            exact = None
            if kind == "form" and re.search(r"\btravel\s+(?:claim|expense|request)\s+form\b", query, re.I) and not named:
                exact = self._travel_form_link(employee, query, today)
            elif personal and not named:
                travel = _personal_travel_intent(query, kind == "fuel", explicit)
                if travel:
                    exact = self._personal_travel(employee, query, travel, today)
            if exact:
                outcome, text, sources = exact
                if outcome == "answered" and (kind not in {"fuel", "hotel"}
                                               or any(p.content_type == "table" for p in sources)):
                    answers[index] = text
                    accepted.update((p.id, p) for p in sources)
                if outcome == "refused":
                    state["withheld"] = True
                continue

            rows = self._report_rows(query, role, audiences, today, scope)
            if _asks_summary_fields(query) and not _summary_report_source_fields(rows):
                continue
            if rows:
                if field_answer := _summary_report_fields(rows, query):
                    answers[index] = field_answer
                    accepted.update((p.id, p) for p in rows)
                else:
                    pending[index] = TopicEvidence(index, query, rows[:MAX_PASSAGES])
                continue

            if re.fullmatch(r"(?:the\s+)?code of conduct(?:\s+for me)?[?.!]*", query.strip(), re.I):
                rows = [p for p in self.store.heading_hits(["responsibilities", "business integrity", "our values"],
                                                           audiences, today, **scope)
                        if "code of conduct" in p.title.casefold() and p.content_type == "section"
                        and self._topic_visible(role, p, audiences)]
                if rows:
                    pending[index] = TopicEvidence(index, query, rows[:MAX_PASSAGES])
                    continue

            if kind == "medical" and (explicit or re.fullmatch(
                r"(?:health insurance|medical (?:insurance|coverage|takaful|benefits)|medical)(?:\s+for me)?[?.!]*",
                query.strip(), re.I,
            )):
                headings = [_MEDICAL_SCHEDULE] if explicit else ["eligibility", "coverage", _MEDICAL_SCHEDULE]
                rows = [p for p in self.store.heading_hits(headings, audiences, today, **scope)
                        if "medical" in p.title.casefold() and self._topic_visible(role, p, audiences)]
                if explicit:
                    rows = [p for p in rows if _row_plan(p.body) == explicit[1].lower()]
                elif personal:
                    rows = [p for p in rows if p.content_type != "table"
                            or role in (policy_row_roles(p.heading, p.body, p.title) or set())]
                fields = _medical_fields(query)
                if fields and rows:
                    amount = _medical_field_answer(rows, fields, query)
                    if amount != UNKNOWN:
                        answers[index] = amount
                        accepted.update((p.id, p) for p in rows)
                        continue
                if personal and not explicit and rows:
                    summary = _personal_medical_summary(rows)
                    if summary:
                        answers[index] = summary
                        accepted.update((p.id, p) for p in rows)
                        continue
                if rows:
                    # Full eligibility and coverage precede the narrower maternity and termination headings.
                    rows.sort(key=lambda p: (not bool(re.match(r"(?:2\. Eligibility|3\. Coverage)$", p.heading, re.I)),
                                             p.content_type != "table", "termination" in p.heading.casefold()))
                    pending[index] = TopicEvidence(index, query, rows[:MAX_PASSAGES])
                    continue
            searches.append((index, query, expand_query(named[0] if named else query), scope, requested if kind == "fuel" else None))

        if searches:
            try:
                embeddings = self.embedder.embed([
                    f"{search} — employee designation: {role_label(role)}"
                    if _PERSONAL.search(query) else search
                    for _, query, search, _, _ in searches
                ])
                for (index, query, search, scope, fuel_roles), embedding in zip(searches, embeddings, strict=True):
                    rows = search_passages(self.store, embedding, search, audiences, today, **scope)
                    rows = [p for p in rows if self._topic_visible(role, p, audiences) and asks_for_hidden(search, [p])]
                    if fuel_roles:
                        rows = [p for p in rows if p.content_type == "table" and _FUEL_SCHEDULE in p.heading.casefold()
                                and "all levels" not in p.body.casefold()
                                and fuel_roles <= (policy_row_roles(p.heading, p.body, p.title) or set())]
                    focused = focus_clauses(rows, query)
                    if focused is not None:
                        rows = focused if len({p.policy_id for p in focused}) <= 1 else []
                    selected = select_passages(rows)[:MAX_PASSAGES]
                    if selected:
                        pending[index] = TopicEvidence(index, query, selected)
            except Exception as error:
                failed = True
                _log_error(error, state["id"], 503)

        grouped = _bounded_topic_evidence(list(pending.values()))
        truncated = False
        if grouped:
            try:
                model = model_mini()
                state.update(model=model, evidence_ids=[p.id for group in grouped for p in group.passages])
                completion = self.llm.answer_topics(
                    system=system_prompt(today, role_label(role), source=state["prompt_source"]),
                    topics=grouped, model=model, max_tokens=answer_max_tokens(model),
                )
                state.update(tokens_in=completion.tokens_in, tokens_out=completion.tokens_out,
                             finish_reason=completion.finish_reason)
                truncated = completion.finish_reason != "stop"
                if forbidden_output(completion.text):
                    state.update(answer=completion.text)
                    return self._finish(employee, question, state, started)
                output = _topic_json(completion.text, partial=truncated)
                for group in grouped:
                    text = output.get(str(group.number))
                    if not isinstance(text, str):
                        continue
                    cleaned = sanitize(_strip_closing_offers(text), [p.body for p in group.passages])
                    if cleaned not in {UNKNOWN, REFUSAL}:
                        answers[group.number] = _short_answer(cleaned, group.question)
                        accepted.update((p.id, p) for p in group.passages)
            except IncompleteCompletion as error:
                _log_error(error, state["id"], 200)
                state.update(outcome="dont_know", answer=UNKNOWN, finish_reason=error.reason)
                return self._finish(employee, question, state, started)
            except Exception as error:
                failed = True
                _log_error(error, state["id"], 503)

        state.update(search="done", history_note="multi-topic", rewritten_question=" | ".join(t.query for t in topics))
        if not answers and (truncated or not state["withheld"]):
            outcome = "error" if failed else "refused" if state["withheld"] else "dont_know"
            state.update(outcome=outcome, answer=UNAVAILABLE if failed else UNKNOWN)
            return self._finish(employee, question, state, started)
        sections = [
            (f"**{_topic_display(topic.label)}**\n\n{_offer_ta(_format_answer(answers[index], topic.query), topic.query)}"
             if index in answers
             else TOPIC_MISSING.format(topic=_topic_display(topic.label)))
            for index, topic in enumerate(topics, 1)
        ]
        if remaining_topics:
            sections.append("I can cover up to eight topics at a time. Please send any remaining topics separately.")
        state.update(outcome="answered" if answers else "refused",
                     answer="\n\n".join(sections),
                     sections=list(dict.fromkeys(p.section_label for p in accepted.values())),
                     citations=[_citation(p) for p in accepted.values()],
                     valid_until=min((p.effective_to for p in accepted.values() if p.effective_to), default=None))
        return self._finish(employee, question, state, started)

    @staticmethod
    def _topic_visible(role, passage, audiences):
        return shares_audience(passage.audience, audiences) and _visible(role, passage)

    def _source_fact(self, employee, question, today):
        audiences = set(allowed_audiences(employee.designation))
        def policy_hits(title, heading):
            return [p for p in self.store.heading_hits([heading], audiences, today)
                    if title in p.title.casefold() and self._topic_visible(employee.designation, p, audiences)]

        if (re.search(r"\btravel\b", question, re.I) and re.search(r"\bnon[- ]panel\b", question, re.I)
                and re.search(r"\bforms?\b", question, re.I) and re.search(r"\bclaims?\b", question, re.I)):
            travel = [p for p in policy_hits("travel", "travel expense form")
                      if re.search(r"\btravel expense form\b", p.body, re.I)]
            medical = [p for p in policy_hits("medical", "claim in case of non-panel hospital")
                       if re.search(r"\bin[- ]patient claim form\b", p.body, re.I)]
            if travel and medical and len({p.policy_id for p in travel}) == len({p.policy_id for p in medical}) == 1:
                return "For a travel expense claim, use the Travel Expense Form. For a non-panel hospital claim, use the In-Patient Claim Form.", [travel[0], medical[0]]

        page = asked_page(question)
        if page and re.search(r"\btravel policy\b", question, re.I):
            rows = [p for p in self.store.printed_page_hits(page, audiences, today)
                    if "travel" in p.title.casefold() and self._topic_visible(employee.designation, p, audiences)]
            rules = [p for p in rows if p.content_type == "section" and _DA_CONDITION.search(p.body)]
            if rules and len({p.policy_id for p in rows}) == 1:
                distance = _DA_CONDITION.search(rules[0].body)[1]
                return (f"The page includes the daily allowance rule for journeys totaling {distance} km or more round trip, including transit time.", [rules[0]])
        if page and re.search(r"\b(?:medical|takaful|health insurance)\b", question, re.I):
            rows = [p for p in policy_hits("medical", _MEDICAL_SCHEDULE)
                    if p.page == page and p.content_type == "table"
                    and (employee.designation == "hr" or employee.designation in
                         (policy_row_roles(p.heading, p.body, p.title) or set()))]
            if rows and len({p.policy_id for p in rows}) == 1:
                return friendly_table(rows, question), rows
        if re.fullmatch(r"(?:tell|show|list)(?:\s+me)?\s+my\s+allowances[?.!]*", question.strip(), re.I):
            sections, sources = [], {}
            for intent, label in (("daily", "Daily allowance"), ("hotel", "Hotel allowance"), ("fuel", "Fuel allowance")):
                outcome, answer, evidence = self._personal_travel(employee, f"{label} for me", intent, today)
                if outcome == "answered" and evidence:
                    sections.append(f"{label}: {answer}")
                    sources.update((p.id, p) for p in evidence)
            if sections:
                return "\n\n".join(sections), list(sources.values())
        if (re.search(r"\b(?:minimum|lowest)\s+age\b", question, re.I)
                and re.search(r"\b(?:hir\w*|employ\w*|recruit\w*)\b", question, re.I)):
            for row in policy_hits("code of conduct", "gifts"):
                match = re.search(r"child labou?r.{0,150}?not employ anyone under the age of\s+(\d+)", row.body, re.I | re.S)
                if match:
                    return f"The Code of Conduct says the company does not employ anyone under {match[1]} years of age.", [row]
        if re.search(r"\bgifts?\b", question, re.I) and re.search(r"\b(?:accept\w*|supplier|hospitality)\b", question, re.I):
            for row in policy_hits("code of conduct", "gifts"):
                amount = re.search(r"not exceeding\s+PKR\s*([\d,]+)", row.body, re.I)
                if amount and all(re.search(term, row.body, re.I) for term in (r"\bmodest\b", r"\binfrequent\b", r"\binfluence\b", r"\bmanager\b")):
                    return (f"Gifts may only be accepted if they are modest (not exceeding PKR {amount[1]}), infrequent, and not intended to influence a business decision. Gifts must be reported to a manager.", [row])
        if re.search(r"\b(?:lawyer|advocate)\b", question, re.I) and re.search(r"\binquir\w*\b", question, re.I):
            for row in policy_hits("corrective action", "inquiry process"):
                match = re.search(r"\bNo advocate can legally be permitted to assist a workman during the inquiry\.", row.body, re.I)
                if match:
                    return f"The policy states: {match[0]} Ask HR how this applies to your situation.", [row]
        if re.search(r"\bdental\b", question, re.I) and re.search(r"\b(?:medical|health|takaful)\b", question, re.I):
            for row in policy_hits("medical", "medical exclusions"):
                match = re.search(r"\bAny Dental Treatment\b[^.]*\baccidental injury\b[^.]*\.", row.body, re.I | re.S)
                if match:
                    clause = re.sub(r"\s+", " ", match[0]).strip()
                    return f"The medical policy excludes: {clause}", [row]
        if (re.search(r"\bnon[- ]panel\b", question, re.I) and re.search(r"\bclaim\b", question, re.I)
                and re.search(r"\b(?:discharge|deadline|days|how long)\b", question, re.I)):
            for row in policy_hits("medical", "in patient claim process"):
                match = re.search(r"non[- ]panel hospital[^.]*within\s+(\d+)\s+days?\s+of\s+discharge", row.body, re.I | re.S)
                if match:
                    return f"Submit the original bills with the claim form within {match[1]} days of discharge.", [row]
        if (re.search(r"\badvance\b", question, re.I) and re.search(r"\bcreate\b", question, re.I)
                and re.search(r"\b(?:manager|approv\w*|submit\w*)\b", question, re.I)):
            for row in policy_hits("advance salary", "enter required details"):
                if (re.search(r"clicking\s*[“\"']?create[”\"']?\s+will save the request as draft", row.body, re.I)
                        and re.search(r"create\s*&\s*submit", row.body, re.I)):
                    return "Clicking Create saves the request as a draft; it is not sent for approval until you select Create & Submit.", [row]
        if (re.search(r"\b(?:female|women|woman)\b", question, re.I) and re.search(r"\bair\b", question, re.I)
                and re.search(r"\b(?:travel|fly|flight)\b", question, re.I)):
            for row in policy_hits("travel", "mode of travel"):
                if row.content_type == "section" and re.search(r"female employees not eligible for air travel.*lowest grade with air travel entitlement", row.body, re.I | re.S):
                    return ("Female employees whose grade does not include air travel follow the policy for the lowest grade with air travel entitlement.", [row])
        if re.search(r"\bsuspen(?:d|ded|sion)\b", question, re.I):
            for row in policy_hits("corrective action", "suspension of service"):
                days = re.search(r"(?:maximum|max(?:imum)? of)\s*(?:of\s*)?(\d+\s+working\s+days)", row.body, re.I)
                half = re.search(r"\bhalf pay\b", row.body, re.I)
                if days or half:
                    details = []
                    if days:
                        details.append(f"The total suspension period must not exceed {days[1]}.")
                    if half:
                        details.append("Suspension days are paid at half pay.")
                    return " ".join(details) + " Ask HR whether the rule applies to the employee's status.", [row]
        if re.search(r"\bsmok\w*\b", question, re.I) and re.search(r"\bstock\s*room\b", question, re.I):
            for row in policy_hits("corrective action", "progressive corrective action"):
                if re.search(r"\bsmok\w*\b.*\bstock\s*room\b", row.body, re.I | re.S):
                    return "Smoking in the stock room is listed as a violation subject to progressive corrective action.", [row]
        if re.search(r"\bharass\w*\b", question, re.I) and re.search(r"\bcustomers?\b", question, re.I):
            for row in policy_hits("corrective action", "immediate dismissal"):
                if re.search(r"\bharassment\b.*\bcustomers?\b", row.body, re.I | re.S):
                    return "Harassment of customers is listed as a violation that may be subject to immediate dismissal.", [row]
        if (re.search(r"\b(?:salesman|salesmen)\b", question, re.I)
                and re.search(r"\b(?:who|prepar\w*)\b", question, re.I)
                and re.search(r"\bincident report\b", question, re.I)):
            for row in policy_hits("corrective action", "authority on handling inquiry"):
                if (row.content_type == "table" and re.search(r"\bsalesmen\s+and\s+below\b", row.body, re.I)
                        and re.search(r"\bRSM to prepare Incident Report\b", row.body, re.I)):
                    return "For cases involving salesmen and below, the RSM prepares the incident report.", [row]
        if (re.search(r"\b(?:family|wife|husband|spouse)\b", question, re.I)
                and re.search(r"\b(?:travel|trip)\b", question, re.I)):
            for row in policy_hits("travel", "travel of family members"):
                if re.search(r"\bown expense\b", row.body, re.I) and re.search(r"\bnot be liable\b", row.body, re.I):
                    return ("Family members may travel at the employee's own expense; the company is not liable for their travel and related costs.", [row])
        if (re.search(r"\b(?:daily allowance|per diem|DA)\b", question, re.I)
                and re.search(r"\b(?:same[- ]day|hours?)\b", question, re.I)):
            for row in policy_hits("travel", "daily allowance"):
                if row.content_type != "section":
                    continue
                half = re.search(r"\bHalf DA\b[^.]*\b12 hours or less\b[^.]*\.", row.body, re.I)
                if half:
                    distance = _DA_CONDITION.search(row.body)
                    text = half[0]
                    if distance:
                        text += f" The general daily allowance rule also requires a {distance[1]} km round trip."
                    return text, [row]
        if (_PERSONAL.search(question) and re.search(r"\bair\b", question, re.I)
                and re.search(r"\b(?:travel|trips?|fly|flight)\b", question, re.I)
                and not re.search(r"\b(?:female|women|woman|girl)\b", question, re.I)):
            rows = [p for p in policy_hits("travel", "mode of travel") if p.content_type == "table"
                    and employee.designation in (policy_row_roles(p.heading, p.body, p.title) or set())]
            if len(rows) == 1:
                fields = {key.casefold(): value for key, value in _fields(rows[0].body)}
                air = fields.get("travel by air")
                if air:
                    text = ("No air travel entitlement is listed for your grade." if air == "-"
                            else f"Your grade's listed air travel entitlement is {air}.")
                    for key, label in (("travel by rail", "Rail"), ("travel by road", "Road")):
                        if fields.get(key) and fields[key] != "-":
                            text += f" {label}: {fields[key]}."
                    exceptions = [p for p in policy_hits("travel", "mode of travel") if p.content_type == "section"
                                  and re.search(r"female employees not eligible for air travel", p.body, re.I)]
                    if exceptions and air == "-":
                        text += " A separate rule applies to female employees; ask HR if it applies to you."
                    return text, [*rows, *exceptions]
        if (employee.designation in _RETAIL_MEDICAL_ROLES and _SELF_ELIGIBILITY.search(question)
                and re.search(r"\b(?:medical|health|insurance|takaful)\b", question, re.I)
                and not _medical_plan(question)):
            rows = [p for p in self.store.heading_hits(["eligibility"], audiences, today)
                    if "medical" in p.title.casefold() and p.content_type == "section"
                    and self._topic_visible(employee.designation, p, audiences)]
            for row in rows:
                match = re.search(r"2\.1\.\s*(.*?)(?=\s*2\.2\.|$)", row.body, re.I | re.S)
                if match and "store manager" in match[1].casefold():
                    scope = re.sub(r"\s+", " ", match[1]).strip()
                    return f"{scope} Ask HR to confirm your individual eligibility.", [row]
        if (re.search(r"\bcode of conduct\b", question, re.I)
                and re.search(r"\backnowledg(?:e)?ment\b", question, re.I)
                and re.search(r"\b(?:form|fields|details|fill)\b", question, re.I)):
            rows = [p for p in self.store.heading_hits(["acknowledgment"], audiences, today)
                    if "code of conduct" in p.title.casefold() and p.content_type in {"form", "table"}
                    and self._topic_visible(employee.designation, p, audiences)]
            for row in rows:
                fields = [label for pattern, label in (
                    (r"\bemployee\s+name\s*:", "Employee Name"), (r"\bemployee\s+id\s*:", "Employee ID"),
                    (r"\bsignatures?\s*:", "Signature"), (r"\bdate\s*:", "Date"),
                ) if re.search(pattern, row.body, re.I)]
                if len(fields) >= 2:
                    return _field_list("Code of Conduct acknowledgment form", fields), [row]
        if not (re.search(r"\b(?:pf|provident fund)\b", question, re.I) and re.search(r"\b(?:loan|borrow\w*)\b", question, re.I)):
            return None
        tenure = re.search(r"\b(\d{1,2})\s*(?:years?|yrs?)\b", question, re.I)
        if not tenure and re.search(r"\bhow much\b", question, re.I):
            rows = [p for p in self.store.heading_hits(["approval authority matrix"], audiences, today)
                    if "provident fund" in p.title.casefold() and p.content_type == "table"
                    and self._topic_visible(employee.designation, p, audiences)]
            bands = []
            for row in rows:
                period = re.search(r"Service Period:\s*([^.]*)", row.body, re.I)
                percentage = re.search(r"PF Loan Eligibility:\s*([\d.]+%)", row.body, re.I)
                if period and percentage:
                    bands.append((period[1].strip(), percentage[1], row))
            if bands and len({row.policy_id for _, _, row in bands}) == 1:
                summary = "; ".join(f"{period}: {percentage}" for period, percentage, _ in bands)
                return f"The PF loan limit depends on your service period: {summary}. Ask HR which band applies to you.", [row for _, _, row in bands]
        if tenure and re.search(r"\b(?:percent(?:age)?|how much|balance|installments?|approv\w*)\b", question, re.I):
            years = int(tenure[1])
            matches = []
            for row in self.store.heading_hits(["approval authority matrix"], audiences, today):
                if "provident fund" not in row.title.casefold() or row.content_type != "table" or not self._topic_visible(employee.designation, row, audiences):
                    continue
                period = re.search(r"Service Period:\s*([^.]*)", row.body, re.I)
                if not period:
                    continue
                text = period[1]
                lower = re.search(r"more than\s+(\d+)\s+years?", text, re.I)
                upper = re.search(r"up to\s+(\d+)\s+years?", text, re.I)
                minimum = re.search(r"minimum\s+(\d+)\s+months?", text, re.I)
                if ((lower and years > int(lower[1]) or minimum and years * 12 >= int(minimum[1]))
                        and (not upper or years <= int(upper[1]))):
                    matches.append(row)
            if len(matches) == 1:
                row = matches[0]
                percentage = re.search(r"PF Loan Eligibility:\s*([\d.]+%)", row.body, re.I)
                installments = re.search(r"Loan Installments Up to:\s*(\d+)\s+Months?", row.body, re.I)
                if percentage:
                    text = f"For {years} years of service, the PF loan limit is {percentage[1]} of the PF balance."
                    if installments:
                        text += f" Repayment is up to {installments[1]} months."
                    authority = re.search(r"Approving Authority:\s*([^.]*)", row.body, re.I)
                    if authority and re.search(r"\bapprov\w*\b", question, re.I):
                        text += f" Approving authority: {authority[1].strip()}."
                    return text, [row]
        if not tenure and re.search(r"\b(?:eligible|eligibility|how (?:many|much) (?:months?|service))\b", question, re.I) and not re.search(r"\b(?:percent(?:age)?|balance|installments?)\b", question, re.I):
            rows = [p for p in self.store.heading_hits(["eligibility criteria"], audiences, today)
                    if "provident fund" in p.title.casefold() and p.content_type == "section"
                    and self._topic_visible(employee.designation, p, audiences)]
            for row in rows:
                match = re.search(r"at least\s+([^.]*?months?\s+of\s+continuous\s+service)", row.body, re.I)
                if match:
                    return f"A PF loan requires at least {match[1]} with the company, subject to the other policy conditions.", [row]
        return None

    def _report_rows(self, query, role, audiences, today, scope):
        report = re.search(r"\b(?:incident report|(?:investigation )?summar[yt] report)\b", query, re.I)
        if not report or re.search(r"\b(?:compar\w*|versus|difference)\b", query, re.I):
            return []
        heading = "incident report" if report[0].casefold() == "incident report" else "summary report"
        return [p for p in self.store.heading_hits([heading], audiences, today, **scope)
                if p.content_type == "form" and heading in p.heading.casefold()
                and self._topic_visible(role, p, audiences)]

    def _personal_travel(self, employee: Employee, question: str, intent: str, today: date) -> tuple:
        role = employee.designation
        audiences = set(allowed_audiences(role))
        if role in {"intern", "trainee"}:
            return "refused", "Travel benefits apply to permanent management employees. Ask HR if you need help.", []

        def rows_for(heading):
            return [p for p in self.store.heading_hits([heading], audiences, today)
                    if p.title.casefold() == "travel policy" and _visible(role, p)
                    and shares_audience(p.audience, audiences)]

        eligibility = [p for p in rows_for("eligibility") if p.content_type == "section"
                       and "permanent management employees" in p.body.casefold()]
        travel_condition = ""
        for passage in eligibility:
            match = re.search(r"within the country, covering a distance of ([\d,]+) kilometers or more round trip",
                              passage.body, re.I)
            if match:
                travel_condition = ("You qualify if you're a permanent management employee traveling for work "
                                    f"within Pakistan at least {match[1]} km round trip.")
                break

        if intent == "hotel":
            rows = [p for heading in (_HOTEL_SCHEDULE, "hotel entitlement", "accommodation")
                    for p in rows_for(heading) if p.content_type == "table"
                    and role in (policy_row_roles(p.heading, p.body, p.title) or set())
                    and re.search(r"\b(?:proposed rates|self-stay)\s*:", p.body, re.I)]
            rows = list({p.id: p for p in rows}.values())
            if not rows:
                rules = [p for p in rows_for("accommodation") if p.content_type == "section"
                         and "eligible for accommodation in company-provided hotels or guest rooms" in p.body.casefold()]
                if rules and travel_condition:
                    return ("answered", f"{travel_condition} Accommodation is in company-provided hotels or guest rooms.\n\n"
                            f"{RATE_MISSING}", [*rules, *eligibility])
                return "dont_know", UNKNOWN, []
            prefix = f"For your designation ({role_label(role)}):"
            if travel_condition:
                prefix = f"{travel_condition}\n\n{prefix}"
            if len(rows) > 1:
                return "answered", f"{prefix}\n\n{friendly_table(rows, question)}", [*rows, *eligibility]
            fields = {key.casefold(): value for key, value in _fields(rows[0].body)}
            details = []
            self_stay_only = re.search(r"\b(?:self[- ]stay|own accommodation|stay on my own)\b", question, re.I)
            amount_only = re.search(r"\b(?:rates?|amount|how much)\b", question, re.I) and not _RULE_REQUEST.search(question)
            if not self_stay_only:
                if fields.get("proposed rates") not in {None, "", "-"}:
                    details.append(f"Hotel rate: {fields['proposed rates']}")
                if fields.get("hotel entitlement") and not amount_only:
                    details.append(f"Hotel options: {fields['hotel entitlement']}")
            if fields.get("self-stay") and (self_stay_only or not amount_only):
                details.append(f"Self-stay: {fields['self-stay']}")
            if amount_only and details:
                return "answered", f"{details[0]}. {travel_condition}".strip(), [*rows, *eligibility]
            return "answered", f"{prefix}\n\n" + "\n".join(
                f"- {value}" for value in details
            ), [*rows, *eligibility]

        if intent == "fuel":
            rows = [p for p in rows_for(_FUEL_SCHEDULE) if p.content_type == "table"
                    and role in (policy_row_roles(p.heading, p.body, p.title) or set())]
            if not rows:
                rules = [p for p in rows_for("company-maintained vehicles") if p.content_type == "section"
                         and "reimbursed for fuel expenses based on the mileage rate" in p.body.casefold()]
                if rules:
                    return ("answered", "Fuel is reimbursed at the mileage rate if you're "
                            "using a company-maintained vehicle for business travel or have a vehicle allowance.\n\n"
                            f"{RATE_MISSING}", [*rules, *eligibility])
                return "dont_know", UNKNOWN, []
            vehicle = _VEHICLE_CC.search(question)
            if vehicle:
                rows = [p for p in rows if vehicle[1] in _vehicle_sizes(p)]
                if not rows:
                    return "dont_know", UNKNOWN, []
            elif len(rows) > 1 and re.search(r"\b(?:one|single|exact)\s+rate\b|\b(?:rate|amount)\s+only\b", question, re.I):
                return "clarification", "Which vehicle engine size should I use?", []
            intro = f"The policy lists these fuel rates for your designation ({role_label(role)}):"
            rates = _fuel_rates(rows, question)
            if len(rows) == 1 and re.search(r"\b(?:rates?|amount|how much)\b", question, re.I) and not _COMPARISON.search(question):
                return "answered", rates.removeprefix("- ").rstrip(".") + ".", [*rows, *eligibility]
            return "answered", f"{intro}\n\n{rates}", [*rows, *eligibility]

        if intent == "daily":
            evidence = rows_for("daily allowance")
            rows = [p for p in evidence if p.content_type == "table"
                    and role in (policy_row_roles(p.heading, p.body, p.title) or set())]
            amounts = {value for p in rows for key, value in _fields(p.body)
                       if key.casefold().startswith("daily allowance") and re.fullmatch(r"[\d,]+(?:\.\d+)?", value)}
            rules = [p for p in evidence if p.content_type == "section" and _DA_CONDITION.search(p.body)]
            distances = {match[1] for p in rules for match in _DA_CONDITION.finditer(p.body)}
            if len(amounts) > 1 or len(distances) > 1:
                return "clarification", "The daily allowance rules conflict. Ask HR to confirm which applies.", []
            condition = ""
            if distances:
                employment = "you're a permanent management employee and " if eligibility else ""
                condition = (f"You qualify if {employment}your business trip is at least "
                             f"{next(iter(distances))} km round trip from your permanent place of work, "
                             "including transit time.")
            if condition and _RULE_REQUEST.search(question):
                narrative = "\n".join(p.body for p in evidence if p.content_type == "section")
                applicable_rules = []
                full = re.search(r"\bOne full DA[^.]*full calendar day or exceeds (\d+) hours", narrative, re.I)
                half = re.search(r"\bHalf DA[^.]*same-day[^.]*(\b\d+) hours or less", narrative, re.I)
                if full:
                    applicable_rules.append(f"Full allowance: a full calendar day or over {full[1]} hours.")
                if half:
                    applicable_rules.append(f"Half allowance: same-day return of {half[1]} hours or less.")
                if re.search(r"\ba daily allowance will be granted for weekends and public holidays", narrative, re.I):
                    applicable_rules.append("Applies on weekends and public holidays during the trip.")
                if re.search(r"for training or sponsored travel[^.]*breakfast and lunch[^.]*half DA", narrative, re.I):
                    applicable_rules.append("Half allowance for training or sponsored trips with breakfast and lunch.")
                if re.search(r"if the travel is fully sponsored, no DA", narrative, re.I):
                    applicable_rules.append("No allowance for fully sponsored trips.")
                if applicable_rules:
                    condition += "\n\n" + "\n".join(f"- {rule}" for rule in applicable_rules)
            if len(amounts) == 1:
                answer = f"Rs. {next(iter(amounts))} per day."
            elif condition:
                return "answered", f"{condition}\n\n{RATE_MISSING}", [*rules, *eligibility]
            else:
                return "dont_know", UNKNOWN, []
            return "answered", f"{answer} {condition}".strip(), [*rows, *rules, *eligibility]

        if intent == "tour":
            evidence = rows_for("tour allowance")
            rows = [p for p in evidence if p.content_type == "table"
                    and role in (policy_row_roles(p.heading, p.body, p.title) or set())]
            if not rows:
                return "dont_know", UNKNOWN, []
            intro = f"For your designation ({role_label(role)}):"
            return "answered", intro + "\n\n" + "\n".join(p.body for p in rows), rows

        evidence = rows_for(_TRAVEL_APPROVAL)
        rows = [p for p in evidence if p.content_type == "table"
                and role in (policy_row_roles(p.heading, p.body, p.title) or set())]
        authorities = list(dict.fromkeys(
            value for p in rows for key, value in _fields(p.body) if _APPROVAL_FIELD.search(f"{key}:")
        ))
        if len(authorities) != 1:
            return "dont_know", UNKNOWN, []
        authority = authorities[0]
        if authority.casefold() == "gc, gm":
            authority = "Group Chief / General Manager"
        detail = f"For your designation ({role_label(role)}), the travel approver is {authority}."
        if not re.search(r"\bwho\b|\bapprover\b", question, re.I):
            summary = _travel_authorization_summary("authorization of business travel", evidence)
            if summary:
                detail += " " + summary.split(" Sales and Field Audit staff", 1)[0]
        if re.search(r"\b(?:link|url)\b", question, re.I):
            detail = with_passage_links(detail, evidence)
        return "answered", detail, [*rows, *(p for p in evidence if p.content_type == "section")]

    def _finish(self, employee: Employee, question: str, state: dict, started: float) -> AskResult:
        state["answer"] = soften_labels(state["answer"])
        if forbidden_output(state["answer"]) or contains_named_pay(state["answer"]):
            logger.warning("answer_guard request_id=%s", state["id"])
            state.update(outcome="refused", guardrail="blocked", guardrail_reason="answer-content",
                         answer=REFUSAL, citations=[], valid_until=None)
        elif state["outcome"] not in {"blocked", "refused"} and (canonical := fixed_reply(state["answer"])):
            state.update(answer=canonical, outcome="refused" if canonical == REFUSAL else "dont_know",
                         citations=[], valid_until=None)
        elif (state["outcome"] == "answered" and state["topic_count"] == 1
              and _missing_requested_quantity(question, state["answer"])):
            state["outcome"] = "dont_know"
        if state["outcome"] == "answered" and state["history_note"] != "question-recall":
            note = _edition_note(question, self.store.list_policies() if re.search(r"\b(?:19|20)\d{2}\b", question) else [])
            if note and note not in state["answer"]:
                state["answer"] = f"{note}\n\n{state['answer']}"
            state["answer"] = _remove_document_references(state["answer"])
            if state["topic_count"] == 1:
                state["answer"] = _offer_ta(_format_answer(state["answer"], question), question)
        path = ("cached" if state["cache"] == "hit" else "recalled" if state["history_note"] == "question-recall"
                else "model" if state["model"] else "deterministic")
        passage_ids = list(dict.fromkeys([
            *state["evidence_ids"], *(citation.passage_id for citation in state["citations"] if citation.passage_id),
        ]))
        logger.info("answer_trace %s", json.dumps({
            "request_id": state["id"], "prompt_hash": state["prompt_hash"], "model": state["model"],
            "finish_reason": state["finish_reason"], "topic_count": state["topic_count"], "path": path,
            "passage_ids": passage_ids,
        }))
        stopped = state["guardrail"] == "blocked"
        trace = TraceRecord(
            id=state["id"],
            created_at=utc_now(),
            email=employee.email,
            name=employee.name,
            designation=employee.designation,
            question=(question or "").strip()[:1000] if not stopped else "",
            guardrail=state["guardrail"],
            guardrail_reason=state["guardrail_reason"],
            classifier_called=state["classifier_called"],
            cache=state["cache"],
            rewritten_question=state["rewritten_question"],
            history_note=state["history_note"],
            search=state["search"],
            sections=list(state["sections"]),
            withheld=state["withheld"],
            scores=list(state["scores"]),
            model=state["model"],
            tokens_in=state["tokens_in"],
            tokens_out=state["tokens_out"],
            latency_ms=int((time.perf_counter() - started) * 1000),
            outcome=state["outcome"],
            answer=state["answer"],
            citations=list(state["citations"]) if state["outcome"] == "answered" else [],
            steps=plain_steps(
                stopped=stopped,
                cache=state["cache"],
                search=state["search"],
                sections=state["sections"] if state["outcome"] == "answered" else [],
                withheld=state["withheld"],
                model=state["model"],
            ),
            prompt_version=state["prompt_hash"][:12],
            passage_ids=passage_ids,
        )
        if state["outcome"] != "answered":
            trace.citations = []
        try:
            self.store.add_trace(trace)
        except Exception as error:
            _log_error(error, state["id"], 200)
        shown = trace.citations if state["outcome"] == "answered" else []
        return AskResult(trace.answer, state["outcome"], shown, trace.id, state["valid_until"], passage_ids)


def _edition_note(question: str, policies) -> str:
    years = {int(year) for year in re.findall(r"\b(?:19|20)\d{2}\b", question)}
    if not years or not policies:
        return ""
    folded = question.casefold()
    named = [policy for policy in policies if any(len(word) > 4 and word in folded for word in policy.title.casefold().split())]
    if not named or any(policy.effective_from and policy.effective_from.year in years for policy in named):
        return ""
    dates = sorted({policy.effective_from.isoformat() for policy in named if policy.effective_from})
    if not dates:
        return ""
    listed = " or ".join(str(year) for year in sorted(years))
    return f"There is no {listed} edition in the library. The edition on file is effective from {dates[0]}."


def _distance_rule_answer(question: str, passages, store, audiences, today):
    if not re.search(r"\b300\b", question) or not re.search(r"\b(?:km|kilometers?|rule|distance)\b", question, re.I):
        return None
    rule = next((passage for passage in passages if _DA_CONDITION.search(passage.body)), None)
    if rule is None and hasattr(store, "heading_hits"):
        for passage in store.heading_hits(["daily allowance"], audiences, today):
            if _DA_CONDITION.search(passage.body):
                rule = passage
                break
    if rule is None:
        return None
    match = _DA_CONDITION.search(rule.body)
    sentence = (
        f"The daily allowance applies to a business trip of at least {match[1]} km round trip "
        "from the permanent place of work, including transit time."
    )
    return sentence, [rule]


# The personal-query classifier only chooses which travel lookup fits. The question's own words must name
# that topic, so a model guess never sends a salary or loan question to a travel rate.
_TRAVEL_EVIDENCE = {
    "hotel": re.compile(r"\b(?:hotels?|lodging|accommodation|stay|overnight)\b", re.I),
    "fuel": re.compile(r"\b(?:fuel|petrol|diesel|mileage|vehicle|car|km)\b", re.I),
    "daily": re.compile(r"\b(?:allowances?|per diem|per day)\b", re.I),
    "approval": re.compile(r"\b(?:approv\w*|authori[sz]\w*)\b.*\b(?:travel\w*|trip|tour)\b"
                           r"|\b(?:travel\w*|trip|tour)\b.*\b(?:approv\w*|authori[sz]\w*)\b", re.I),
}
_TRAVEL_SPELLINGS = ("travel", "daily", "allowance", "hotel", "lodging", "accommodation", "mileage", "petrol",
                     "diesel", "vehicle")


def _travel_spelling(text: str) -> str:
    """Correct a one-letter slip or swap in a travel word, as in "wat is my dailly alowance"."""
    def fix(match: re.Match) -> str:
        word = match.group(0).casefold()
        if len(word) < 5 or word in _TRAVEL_SPELLINGS:
            return match.group(0)
        return next((target for target in _TRAVEL_SPELLINGS if _one_slip(word, target)), match.group(0))
    return re.sub(r"[A-Za-z]+", fix, text)


def _one_slip(word: str, target: str) -> bool:
    if len(word) == len(target):
        diff = [i for i, (a, b) in enumerate(zip(word, target)) if a != b]
        return len(diff) == 1 or (len(diff) == 2 and diff[1] == diff[0] + 1
                                  and word[diff[0]] == target[diff[1]] and word[diff[1]] == target[diff[0]])
    if abs(len(word) - len(target)) != 1:
        return False
    short, long = sorted((word, target), key=len)
    return any(short == long[:i] + long[i + 1:] for i in range(len(long)))


def _personal_travel_intent(question: str, fuel_question, requested_plan) -> str:
    if requested_plan or asked_clause(question) or not _PERSONAL.search(question):
        return ""
    if re.search(r"\b(?:compare|versus|exceptions?|excluded|difference)\b", question, re.I):
        return ""
    if re.search(r"\btour allowance\b", question, re.I):
        return "tour"
    hotel = re.search(r"\b(?:hotel|lodging|accommodation|self[- ]stay|overnight stay|room budget|where can i stay)\b", question, re.I)
    if hotel and not fuel_question and not re.search(
        r"\b(?:how (?:can|should|do) i book|claim form|link|cancel|medical|hospital|takaful|book (?:a|my) hotel)\b", question, re.I,
    ):
        return "hotel"
    if fuel_question and not hotel and not re.search(r"\b(?:form|claim|submit|receipt|link|url|documents?)\b", question, re.I):
        return "fuel"
    if re.search(r"\b(?:daily allowance|per diem|DA)\b", question, re.I) and not re.search(
        r"\b(?:claim|submit|form|link|url|receipt|training|sponsor\w*|half|full|weekends?|holidays?)\b", question, re.I
    ):
        return "daily"
    if re.search(r"\b(?:travel|business trip|work trip|per diem)\b", question, re.I):
        if re.search(r"\b(?:daily allowance|travel allowance|per diem|daily budget)\b", question, re.I) and not hotel:
            return "daily"
        if re.search(r"\b(?:claim|hotel|accommodation|lodging|expenses?|reimbursement|urgent|fuel|mode|air|train|link|form|tour|allowance|except\w*)\b", question, re.I):
            return ""
        if re.search(r"\b(?:travel policy|approv\w*|authori[sz]\w*)\b", question, re.I):
            return "approval"
    return ""


def _bounded_topic_evidence(groups: list[TopicEvidence]) -> list[TopicEvidence]:
    if not groups:
        return []
    # Each topic keeps its own top passages. The byte budget is the only shared limit.
    pools = [list(group.passages[:MAX_PASSAGES]) for group in groups]
    chosen: list[list] = [[] for _ in groups]
    used = 0
    while used < EVIDENCE_MAX_BYTES:
        added = False
        for index, pool in enumerate(pools):
            if not pool:
                continue
            row = pool[0]
            size = len(row.body.encode("utf-8")) + len(row.section_label.encode("utf-8")) + 40
            if chosen[index] and used + size > EVIDENCE_MAX_BYTES:
                pool.clear()
                continue
            if size > EVIDENCE_MAX_BYTES - used and chosen[index]:
                pool.clear()
                continue
            if size > EVIDENCE_MAX_BYTES:
                raw = row.body.encode("utf-8")[: max(0, EVIDENCE_MAX_BYTES - used - 80)]
                body = raw.decode("utf-8", errors="ignore")
                body = body.rsplit(None, 1)[0] if " " in body else body
                chosen[index].append(replace(row, body=body))
                pool.clear()
                used = EVIDENCE_MAX_BYTES
                added = True
                continue
            chosen[index].append(pool.pop(0))
            used += size
            added = True
        if not added:
            break
    result = [
        TopicEvidence(group.number, group.question, rows)
        for group, rows in zip(groups, chosen) if rows
    ]
    shared = {}
    for group in result:
        for row in group.passages:
            if row.id not in shared or len(row.body) < len(shared[row.id].body):
                shared[row.id] = row
    for group in result:
        group.passages = [shared[row.id] for row in group.passages]
    return result


def _topic_json(text: str, *, partial: bool = False) -> dict:
    payload = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text, flags=re.I)
    try:
        value = json.loads(payload)
        return value if isinstance(value, dict) else {}
    except ValueError:
        if not partial:
            return {}
    # Preserve only leading JSON members with a complete key and a closed string value.
    payload = payload.lstrip()
    if not payload.startswith("{"):
        return {}
    decoder = json.JSONDecoder()
    completed = {}
    remainder = payload[1:].lstrip()
    while remainder:
        try:
            key, end = decoder.raw_decode(remainder)
            value_start = remainder[end:].lstrip()
            if not isinstance(key, str) or not key.isdecimal() or not value_start.startswith(":"):
                break
            value_start = value_start[1:].lstrip()
            value, end = decoder.raw_decode(value_start)
        except ValueError:
            break
        if not isinstance(value, str) or key in completed:
            break
        remainder = value_start[end:].lstrip()
        if remainder and remainder[0] not in ",}":
            break
        completed[key] = value
        if not remainder.startswith(","):
            break
        remainder = remainder[1:].lstrip()
    return completed


def _topic_display(label: str) -> str:
    safe = re.sub(r"[^\w\s./&'-]", "", label).strip()
    return safe[:1].upper() + safe[1:100] if safe else "Policy question"


def _personal_medical_summary(rows: list[Passage]) -> str:
    narrative = " ".join(p.body for p in rows if p.content_type == "section").casefold()
    if "permanent management employees" not in narrative or not re.search(r"\bin[- ]?patient\b", narrative):
        return ""
    answer = "If you're a permanent management employee, the policy provides inpatient cover."
    if "maternity" in narrative:
        answer += " Maternity has separate eligibility rules."
    plans = {p.id: p for p in rows if p.content_type == "table" and _row_plan(p.body)}
    if len(plans) == 1:
        p = next(iter(plans.values()))
        fields = dict(_fields(p.body))
        plan = _row_plan(p.body).upper()
        room = next((value for label, value in fields.items() if label.casefold().startswith("room & board limit")), "")
        hospital = next((value for label, value in fields.items() if label.casefold().startswith("hospitalization benefit")), "")
        answer += f" Your designation is listed under Plan {plan}."
        if hospital:
            answer += f" Hospitalization limit: {hospital}."
        if room:
            answer += f" Room and board limit: {room} per day."
    return answer


def _vehicle_sizes(passage) -> set[str]:
    vehicle = next((value for key, value in _fields(passage.body) if key.casefold() == "vehicle type"), "")
    match = re.match(r"([\d/ ]+)\s*cc", vehicle, re.I)
    return set(_VEHICLE_CC.findall(vehicle)) | (set(re.findall(r"\d+", match[1])) if match else set())


def _remove_document_references(answer: str) -> str:
    answer = re.sub(r"\s*\(\s*(?:see\s+|refer to\s+)?annexure\s+[a-z](?:\s*-\s*\d+)?\s*\)",
                    "", answer, flags=re.I)
    answer = re.sub(r"\s+\b(?:as per|shown in|set out in|listed in|in|under|see|refer to)\s+(?:the\s+)?"
                    r"(?:annexure\s+[a-z](?:\s*-\s*\d+)?|(?:section|clause)\s+\d+(?:\.\d+)*)\b",
                    "", answer, flags=re.I)
    return re.sub(r"(?im)^\s*(?:annexure\s+[a-z](?:\s*-\s*\d+)?|(?:section|clause)\s+\d+(?:\.\d+)*)\s*[:—–-]\s*", "", answer).strip()


def _medical_plan(question: str) -> re.Match | None:
    match = re.search(
        r"\bplan\s+([a-e])(?=\s*[?.!,]*$|\s+(?:coverage|benefits?|cover|in)\b)", question, re.I,
    )
    if match or re.search(r"\b(medical|takaful|hospital|insurance)\b", question, re.I) or _medical_fields(question):
        return match or _PLAN_ID.search(question)
    return None


def _row_plan(body: str) -> str:
    match = re.match(r"(?:plan:\s*)?plan\s+([a-e])\b", body.strip(), re.I)
    return match[1].lower() if match else ""


def _medical_fields(question: str) -> list[str]:
    fields = [label for pattern, label in _MEDICAL_FIELDS if re.search(pattern, question, re.I)]
    if (re.search(r"\bnormal\s+(?:and|vs\.?|versus|or)\s+(?:complicated|caesarean|cesarean|c.section)\b", question, re.I)
            and "normal delivery" not in fields):
        fields.insert(0, "normal delivery")
    positive = re.split(r"[,;]\s*not\s+|\b(?:but not|rather than|instead of)\s+", question, maxsplit=1, flags=re.I)
    if len(positive) == 2:
        return _medical_fields(positive[0])
    return fields


def _medical_field_answer(passages, requested: list[str], question: str) -> str:
    values = {}
    for passage in passages:
        plan = _row_plan(passage.body)
        for name, value in _fields(passage.body):
            key = name.casefold()
            if any(key.startswith(field) for field in requested):
                values.setdefault((plan, name), set()).add(value)
    if not values:
        return UNKNOWN
    if len(values) == 1:
        (_, name), amounts = next(iter(values.items()))
        if len(amounts) == 1:
            amount = next(iter(amounts))
            if re.search(r"\b(?:just|only)\s+(?:the\s+)?amount\b|\bamount\s+only\b", question, re.I):
                return amount
            return f"{name}: {amount}."
    if any(len(amounts) > 1 for amounts in values.values()):
        return "The policy gives conflicting amounts. Please check with HR."
    records = [
        [f"Plan {plan.upper()}", name, next(iter(amounts))] for (plan, name), amounts in values.items()
    ]
    if _COMPARISON.search(question):
        return _markdown_table(["Plan", "Benefit", "Amount"], records)
    return "\n".join(f"- {plan}, {name}: {amount}" for plan, name, amount in records)


def _retail_eligibility(designation: str, question: str, passages) -> str:
    if (designation not in _RETAIL_MEDICAL_ROLES or not _SELF_ELIGIBILITY.search(question)
            or not re.search(r"\b(?:medical|takaful|health insurance)\b", question, re.I)
            or re.search(r"\b(?:maternity|dependents?|spouse|children|exclusions?|plan)\b", question, re.I)):
        return ""
    role = role_label(designation)
    for passage in passages:
        if ("medical" in passage.title.casefold() and "permanent management employees" in passage.body.casefold()
                and re.search(rf"\b{re.escape(role)}s?\b", passage.body, re.I)):
            return f"Yes, if you're a permanent employee. The policy lists {role} as an eligible designation."
    return ""


def _travel_authorization_summary(question: str, passages) -> str:
    asks_deadline = _TRAVEL_DEADLINE.search(question)
    asks_summary = re.search(r"\b(?:authori[sz]ation of (?:business )?travel|travel authori[sz]ation)\b", question, re.I)
    if not (asks_deadline or asks_summary) or asked_clause(question) or (not asks_deadline and re.search(
        r"\b(?:who|when|where|exceptions?|restrictions?|urgent|sales|audit|form|link|compare|exact)\b", question, re.I,
    )):
        return ""
    text = "\n".join(p.body for p in passages if p.title == "Travel Policy"
                     and p.heading.startswith("3.2.") and p.content_type == "section").casefold()
    deadline = re.search(r"at least (\d+) days prior to the travel date", text)
    if asks_deadline:
        if deadline and all(word in text for word in ("dually signed travel request form", "administration department via hr")):
            return (f"Send the signed Travel Request Form to Administration through HR at least {deadline[1]} days "
                    "before travel. For urgent travel, get approval by email and complete the form when you return."
                    if "seek approval via email" in text and "upon returning" in text
                    else f"Send the signed Travel Request Form to Administration through HR at least {deadline[1]} days before travel.")
        return ""
    required = ("pre-approved by the competent authority", "dually signed travel request form",
                "administration department via hr", "seek approval via email", "upon returning",
                "complete the travel request form", "obtain all necessary approvals")
    if not deadline or not all(phrase in text for phrase in required):
        return ""
    answer = (f"Get pre-approval and send the dually signed Travel Request Form to Administration via HR "
              f"at least {deadline[1]} days before travel. For urgent travel, get email approval and "
              "complete the form and remaining approvals when you return.")
    if all(term in text for term in ("sales staff", "field audit staff", "internal guidelines")):
        answer += " Sales and Field Audit staff follow their own department guidelines."
    return answer


def _named_summary_excerpt(subject: str, passages) -> str:
    """Fallback for an explicitly named public section, when the model yields no answer."""
    terms = lambda text: re.findall(r"\w+", text.casefold())
    sentences = []
    for passage in passages:
        if passage.audience != "everyone" or terms(passage.heading) != terms(subject):
            continue
        for sentence in re.split(r"(?<=[.!?])\s+", " ".join(passage.body.split())):
            if 6 <= len(sentence.split()) <= 42 and sentence.endswith((".", "!", "?")):
                if sanitize(sentence, [passage.body]) == sentence:
                    sentences.append(sentence)
    if not sentences:
        return UNKNOWN
    return min(sentences, key=lambda item: (
        not bool(re.search(r"\b(?:values|purpose|means|requires)\b", item, re.I)),
        bool(re.search(r"\d", item)),
    ))


def _recall_question(ordinal: int, history: list[dict]) -> tuple[str, list[str]]:
    user_indexes = [index for index, turn in enumerate(history) if turn["role"] == "user"]
    if not user_indexes or (ordinal != -1 and not 0 < ordinal <= len(user_indexes)):
        return "I can't find that question in this chat's saved history.", []
    index = user_indexes[-1] if ordinal == -1 else user_indexes[ordinal - 1]
    question = history[index]["text"]
    following = history[index + 1] if index + 1 < len(history) else {}
    ids = following.get("passage_ids", []) if following.get("role") == "assistant" else []
    if ordinal == -1:
        return f'Your previous saved question was: "{question}"', ids
    return f'Question {ordinal} in this chat\'s saved history was: "{question}"', ids


def _strip_closing_offers(answer: str) -> str:
    boundaries = [0, *(match.end() for match in re.finditer(r"(?<=[.!?;])[ \t]+|\n+", answer))]
    for start in boundaries:
        tail = answer[start:].lstrip(" \t*_-")
        if not _CLOSING_OFFER.match(tail):
            continue
        if re.match(r"if\b", tail, re.I) and not re.search(
            r"\b(?:let me know|i (?:can|could|will)|we (?:can|could|will)|download(?:able)? template|template link)\b",
            tail, re.I,
        ):
            continue
        return answer[:start].rstrip(" \t\r\n;")
    return answer


def _field_list(title: str, fields: list[str]) -> str:
    return title + " fields\n\n" + "\n".join(f"- {field}" for field in fields)


def _summary_report_source_fields(passages) -> list[str]:
    prefix = "Investigation Summary Report template. "
    return list(dict.fromkeys(
        part.strip().rstrip(".")
        for passage in passages if passage.content_type == "form" and passage.body.startswith(prefix)
        for part in passage.body[len(prefix):].split(". ") if part.strip()
    ))


def _asks_summary_fields(question: str) -> bool:
    return bool(re.search(r"\bsummar[yt] report\b", question, re.I) and (
        _FORM_FIELDS_REQUEST.search(question) or re.fullmatch(
            r"(?:the\s+)?(?:investigation\s+)?summar[yt] report(?: template)?[?.!]*", question.strip(), re.I)))


def _summary_report_fields(passages, question: str) -> str:
    if not _asks_summary_fields(question):
        return ""
    fields = _summary_report_source_fields(passages)
    if not fields:
        return ""
    return _field_list("Investigation Summary Report", fields)


def _claim_form_fields(passages) -> list[str]:
    fields = []
    for passage in passages:
        start = re.search(r"^Employee Details:\s*", passage.body, re.M)
        if not start:
            continue
        for line in passage.body[start.start():].splitlines():
            group = re.fullmatch(r"(?:Employee|Travel|Expenses) Details:\s*(.+)", line.strip())
            parts = group[1].rstrip(".").split(", ") if group else line.strip().rstrip(".").split(". ")
            fields.extend(part for part in parts if part)
    return list(dict.fromkeys(fields))


def _short_answer(answer: str, question: str) -> str:
    lines = []
    seen = set()
    for line in answer.splitlines():
        key = re.sub(r"[^\w]+", " ", line.casefold()).strip()
        if key and not line.lstrip().startswith("|"):
            if key in seen:
                continue
            seen.add(key)
        lines.append(line)
    text = "\n".join(lines).strip()
    if text in {UNKNOWN, REFUSAL}:
        return text
    if not any(line.lstrip().startswith(("-", "|")) for line in lines):
        text = re.sub(r"(?<=[.!?])\s+(?:If you(?:’|'| a)re (?:seeking|looking for)|For (?:the )?(?:full|exact) (?:text|details)).*\Z",
                      "", text, flags=re.I | re.S)
        if _ONE_SENTENCE.search(question):
            # Keep essential exceptions as clauses instead of dropping later sentences.
            text = re.sub(r"(?<![A-Z])(?<!\b[Rr]s)(?<!\b[Nn]o)(?<!\b[Mm]r)(?<!\b[Mm]s)[.!?]\s+(?=[A-Z])",
                          "; ", text)
    return text


def _named_policy_scope(question: str, store) -> tuple[str, list[str]] | None:
    """Separate a section request from a trailing, explicitly named document."""
    matches = list(_POLICY_LOCATOR.finditer(question))
    if not matches:
        return None
    title_key = lambda text: " ".join(re.findall(r"\w+", text.casefold()))
    policies = store.list_policies()
    for match in reversed(matches):
        subject = question[:match.start()].strip(" ,:\t")
        key = title_key(question[match.end():])
        ids = [policy.id for policy in policies if title_key(policy.title) == key]
        if subject and ids:
            return subject, ids
    return None


def _policy_catalog(question: str, store, designation: str, today: date) -> list[str]:
    """The titles this role can open, when the question asks for the policies themselves and nothing else."""
    if not _POLICY_LIST.search(question) or not token_set(question) <= _POLICY_LIST_WORDS:
        return []
    audiences = set(allowed_audiences(designation))
    return list(dict.fromkeys(
        policy.title for policy in store.list_policies()
        if shares_audience(policy.audience, audiences) and policy.effective_from <= today
        and (policy.effective_to is None or policy.effective_to >= today)
        and (policy.superseded_on is None or policy.superseded_on > today)
    ))


def _policy_context(history: list[dict]) -> tuple[str, list[str]]:
    previous = ""
    labels = []
    for index in range(len(history) - 1, 0, -2):
        asked, answer = history[index - 1:index + 1]
        if asked["role"] != "user" or answer["role"] != "assistant" or question_recall(asked["text"]) is not None:
            continue
        if answer.get("outcome") in {"refused", "clarification"}:
            break
        if not previous:
            # The latest topic must have sources. Never jump across an unanswered topic.
            labels = [item["label"] for item in answer.get("citations", []) if item.get("label")]
            if not labels:
                break
            previous = asked["text"]
        medical_continuation = bool(
            _FOLLOW_FIELD.match(asked["text"]) and _medical_fields(asked["text"]) and not _medical_plan(asked["text"])
            and any(_MEDICAL_SCHEDULE in label.casefold() for label in labels)
        )
        if contextual_query(asked["text"]) is None and not medical_continuation:
            previous = asked["text"]
            break
    return previous, labels


def _previous_boundary(turns: list[dict]) -> str:
    """The last answer, when it was a role boundary rather than a policy answer."""
    for turn in reversed(turns):
        if turn.get("role") != "assistant":
            continue
        text = (turn.get("text") or "").strip()
        if text.startswith("I can't share") or text.startswith("I can't answer"):
            return text
        return ""
    return ""


def _visible(designation: str, passage) -> bool:
    return (visible_passage(designation, passage)
            and not contains_named_pay(f"{passage.title}\n{passage.body}")
            and not exposes_prompt(passage.body))


_LINK = re.compile(r"https?://[^\s)>\]]+")


def _joined_links(text: str) -> str:
    """A web address printed over two lines is one address."""
    lines = (text or "").splitlines()
    joined: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if re.search(r"https?://\S+$", line) and index + 1 < len(lines):
            nxt = lines[index + 1].strip()
            if nxt and " " not in nxt and not re.match(r"^(?:\d|https?://)", nxt, re.I):
                line = line.rstrip() + nxt
                index += 1
        joined.append(line)
        index += 1
    return "\n".join(joined)


def _link_label(line: str, url: str) -> str:
    before = line.split(url, 1)[0]
    before = re.sub(r"^\d+(?:\.\d+)*\.?\s*", "", before.strip())
    before = re.sub(r"\s*please visit\s*$", "", before, flags=re.I)
    before = before.strip(" :.-")
    if ":" in before:
        tail = before.split(":")[-1].strip()
        if tail:
            before = tail
    return before


def with_passage_links(answer: str, passages) -> str:
    """Add each web address from the passages the answer used."""
    text = answer or ""
    found: list[str] = []
    seen = {match.rstrip(".,)") for match in _LINK.findall(text)}
    for passage in passages:
        body = _joined_links(getattr(passage, "body", "") or "")
        for line in body.splitlines():
            for match in _LINK.findall(line):
                url = match.rstrip(".,)")
                if url in seen:
                    continue
                seen.add(url)
                label = _link_label(line, url)
                found.append(f"{label}: {url}" if label else url)
    if not found:
        return text
    return text.rstrip() + "\n\n" + "\n".join(found)


_NUMBER_COLUMN = re.compile(r"(?:#|no\.?|(?:sr|serial|item|row)\.?\s*(?:#|no\.?(?:umber)?|number)?|\d+[.)]?)", re.I)


def _offer_ta(answer: str, question: str) -> str:
    if _TA.search(question) and not re.search(r"\b(?:mileage|fuel|allowance rates?)\b", question, re.I):
        return f"{answer}\n\n{_TA_OFFER}"
    return answer


def _dress_code_rule(passages, question: str) -> str:
    if not re.search(r"\bdress[- ]code\b", question, re.I):
        return ""
    for passage in passages:
        fields = {k.casefold(): v for k, v in _fields(passage.body)}
        if ("dress code" in fields.get("examples (not limited to these)", "").casefold()
                and fields.get("level", "").casefold() == "minor"
                and re.fullmatch(r"Verbal\s*→\s*1st Written Warning", fields.get("steps", ""), re.I)):
            return ("Follow workplace dress-code rules. A breach is treated as minor. "
                    "It starts with a verbal warning, followed by a first written warning.")
    return ""


def _fuel_rates(passages, question: str) -> str:
    records = []
    for passage in sorted(passages, key=lambda p: min(map(int, _vehicle_sizes(p)), default=0)):
        fields = {k.casefold(): v for k, v in _fields(passage.body)}
        vehicle = fields.get("vehicle type")
        if vehicle:
            records.append([
                vehicle, fields.get("grade entitlement", ""), fields.get("mileage (km coverage)", ""),
                fields.get("maintenance and depreciation (new rates)", "").replace("/km", " per km"),
            ])
    if _COMPARISON.search(question) and records:
        return _markdown_table(["Vehicle", "Applies to", "Mileage", "Maintenance and depreciation"], records)
    return "\n".join(
        "- " + ": ".join([vehicle, ", ".join(value for value in (mileage, rate) if value)])
        for vehicle, _, mileage, rate in records
    )


def _format_answer(answer: str, question: str) -> str:
    answer = re.sub(r"\b(?:PKR\.?|Rs\.?|rupees)\s*(?=\d)", "Rs. ", answer, flags=re.I)
    answer = re.sub(r"(?<=\d)/-", "", answer)
    answer = re.sub(r"(?m)^[ \t]*\d{1,3}[.):][ \t]+(?=\S)", "- ", answer)
    answer = re.sub(r"\b(?:Sr\.?\s*#|(?:serial|row|item)\s+(?:number|no\.?))\s*:?\s*\d+\s*[-:.]?\s*",
                    "", answer, flags=re.I)

    def reformat_table(match: re.Match) -> str:
        lines = [[cell.strip() for cell in line.strip().strip("|").split("|")] for line in match[0].splitlines()]
        first, *records = lines
        records = [row for row in records if not all(re.fullmatch(r":?-+:?", cell) for cell in row)]
        indexes = [index for index, field in enumerate(first) if not _NUMBER_COLUMN.fullmatch(field)]
        headers = [first[index] for index in indexes]
        rows = [[row[index] for index in indexes] for row in records if len(row) == len(first)]
        if _COMPARISON.search(question) and len(headers) > 1 and rows:
            return _markdown_table(headers, rows) + "\n"
        bullets = []
        for row in rows:
            fields = [f"{key}: {value}" if key.casefold() not in {"details", "value"} else value
                      for key, value in zip(headers, row) if value]
            if fields:
                bullets.append("- " + " · ".join(fields))
        return "\n".join(bullets) + "\n"

    return re.sub(r"(?m)(?:^[ \t]*\|[^\n]*\|[ \t]*(?:\n|$)){2,}", reformat_table, answer).strip()


def friendly_table(passages, question: str = "") -> str:
    """Keep comparisons tabular and present ordinary policy rows in plain text."""
    records = []
    for passage in passages:
        pairs = [(label, value) for label, value in _fields(passage.body)
                 if not _NUMBER_COLUMN.fullmatch(label) or label.isdecimal()]
        if pairs:
            records.append([("" if label.isdecimal() else label, value) for label, value in pairs])
        elif passage.body.strip():
            records.append([("", passage.body.strip())])
    if not records:
        return ""
    labels = [label for label, _ in records[0]]
    same_shape = all([label for label, _ in record] == labels for record in records)
    if _COMPARISON.search(question) and len(records) > 1 and same_shape and all(labels):
        return _markdown_table(labels, [[value for _, value in record] for record in records])
    lines = [" · ".join(f"{label}: {value}" if label else value for label, value in record) for record in records]
    return lines[0] if len(lines) == 1 else "\n".join(f"- {line}" for line in lines)


def _markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    def cell(value: str) -> str:
        return value.replace("|", "/").replace("\n", " ").strip()

    head = "| " + " | ".join(cell(header) for header in headers) + " |"
    rule = "| " + " | ".join("---" for _ in headers) + " |"
    body = ["| " + " | ".join(cell(value) for value in row) + " |" for row in rows]
    return "\n".join([head, rule, *body])


def _fields(body: str) -> list[tuple[str, str]]:
    pairs = []
    for part in re.split(r"(?<!Rs)(?<!Sr)\.\s+", body.strip()):
        piece = part.strip().strip(".")
        if ":" not in piece:
            continue
        label, value = piece.split(":", 1)
        label = label.strip()
        value = value.strip(" .")
        if label and value:
            pairs.append((label, value))
    return pairs


def _citation(passage, include_full_page=False) -> Citation:
    images = []
    if passage.image_path:
        images.extend(f"/api/media/{path}" for path in passage.image_path.split("|")
                      if path and (include_full_page or not path.rsplit("/", 1)[-1].startswith("page-")))
    source = source_page_url(passage) if include_full_page else ""
    if source and source not in images:
        images.append(source)
    label = f"{passage.section_label}, p. {passage.page}" if passage.page else passage.section_label
    return Citation(label, passage.body, "|".join(images), passage.id, passage.title)


def _log_error(error: Exception, request_id: str, status: int) -> None:
    logger.warning("error_class=%s request_id=%s status=%d", type(error).__name__, request_id, status)


def plain_steps(
    *,
    stopped: bool,
    cache: str,
    search: str,
    sections: list[str],
    withheld: bool,
    model: str | None,
) -> list[dict]:
    if cache == "hit":
        saved = "Used a recent similar answer"
    elif cache == "miss":
        saved = "None saved"
    else:
        saved = "Not checked"
    if sections:
        read = "; ".join(dict.fromkeys(sections))
    elif withheld:
        read = "A matching policy is not available for this role"
    elif search == "skipped":
        read = "Not needed"
    else:
        read = "Nothing matched"
    return [
        {"label": "Checked the question", "detail": "Stopped" if stopped else "Allowed"},
        {"label": "Saved answer", "detail": saved},
        {"label": "Read the policies", "detail": read},
        {"label": "Wrote an answer", "detail": "Done" if model else "Not needed"},
    ]
