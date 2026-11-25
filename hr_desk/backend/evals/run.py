"""Ship gate. Deterministic checks always run. Live model checks run when a key is set."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml

from app.config import openai_api_key
from app.factory import build_pipeline
from app.pipeline import Pipeline
from app.replies import REFUSAL
from app.sanitize import sanitize
from app.store_memory import MemoryStore

CASES_PATH = Path(__file__).resolve().parent / "cases.yaml"
LAST_RUN_PATH = Path(__file__).resolve().parent / "last_run.json"
_NUMBER = re.compile(r"\d+")


@dataclass
class Check:
    name: str
    ok: bool
    primary: bool = False
    developer_detail: str = ""
    case_id: str = ""
    answer: str = ""
    word_count: int | None = None


def load_cases() -> list[dict]:
    data = yaml.safe_load(CASES_PATH.read_text(encoding="utf-8"))
    return list(data)


def _offline_pipeline() -> Pipeline:
    """Guard checks stop before search, so they do not need a model or embeddings."""
    store = MemoryStore()
    store.add_employee("sara.intern@stylo.example", "Sara", "intern", False)
    return Pipeline(store, None, None)


def run_deterministic() -> list[Check]:
    pipeline = _offline_pipeline()
    checks: list[Check] = []
    for case in load_cases():
        if case.get("live_role"):
            continue
        if case.get("kind") == "sanitize":
            checks.append(_sanitize_case(case))
            continue
        checks.append(_ask_case(pipeline, case))
    return checks


def live_cases() -> list[dict]:
    email = os.environ.get("HR_DESK_EVAL_EMPLOYEE_EMAIL", "").strip().lower()
    if not email:
        raise RuntimeError("Set HR_DESK_EVAL_EMPLOYEE_EMAIL to an account with access to the summary report.")
    case = {
        "id": "live_summary",
        "name": "A live answer about the summary report stays inside the policy",
        "primary": True,
        "show_answer": True,
        "email": email,
        "question": "give me details about summart report",
        "history": [{"role": "user", "text": "How do I get travel approval?"}],
        "expect": {
            "outcome": "answered",
            "passage_must_contain": ["Inquiry officer findings"],
            "answer_must_not_contain": ["warning letter", "if you want", "if you need", "i can",
                                        "next step", "let me know", "download", "template link"],
            "exact_summary_fields": True,
            "model_called": False,
        },
    }
    cases = [case]
    for extra in load_cases():
        role = extra.get("live_role")
        if role:
            account = os.environ.get(f"HR_DESK_EVAL_{role.upper()}_EMAIL", "").strip().lower()
            if account:
                cases.append({**extra, "email": account})
    return cases


def run_live() -> list[Check]:
    pipeline = build_pipeline()
    return [_ask_case(pipeline, case) for case in live_cases()]


def run_screen_checks() -> dict:
    """Checks the page can run without calling the live model or reading an old file."""
    checks = run_deterministic()
    failed = [check for check in checks if not check.ok]
    if failed:
        status = "needs_fix"
        summary = "Needs a fix. Do not rely on these answers until the check passes."
    else:
        status = "safe"
        summary = "These checks passed just now."
    return {
        "status": status,
        "summary": summary,
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "checks": [
            {
                "name": check.name,
                "ok": check.ok,
                "primary": check.primary,
                "developer_detail": check.developer_detail,
            }
            for check in checks
        ],
    }


def write_last_run(checks: list[Check], *, live_skipped: bool) -> dict:
    failed = [check for check in checks if not check.ok]
    if failed:
        status = "needs_fix"
        summary = "Needs a fix. Do not rely on these answers until the check passes."
    elif live_skipped:
        status = "incomplete"
        summary = "Checks are incomplete. The live check has not run."
    else:
        status = "safe"
        summary = "Safe to use. The last check passed."
    payload = {
        "status": status,
        "summary": summary,
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "checks": [
            {
                "name": check.name,
                "ok": check.ok,
                "primary": check.primary,
                "developer_detail": check.developer_detail,
            }
            for check in checks
        ],
    }
    LAST_RUN_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def main() -> int:
    checks = run_deterministic()
    live_skipped = not openai_api_key()
    if not live_skipped:
        try:
            checks.extend(run_live())
        except Exception as exc:
            checks.append(
                Check(
                    name="A live answer about the summary report stays inside the policy",
                    ok=False,
                    primary=True,
                    developer_detail=type(exc).__name__,
                    case_id="live_summary",
                )
            )
    payload = write_last_run(checks, live_skipped=live_skipped)
    for check in checks:
        mark = "ok" if check.ok else "FAILED"
        line = f"[{mark}] {check.name}"
        if check.developer_detail and not check.ok:
            line += f" — {check.developer_detail}"
        print(line)
        if check.word_count is not None:
            print(f"  {check.word_count} words (target 40, over by {max(0, check.word_count - 40)})")
        if check.answer:
            print(check.answer)
    print(payload["summary"])
    if any(not check.ok for check in checks):
        return 1
    if live_skipped:
        print("Set OPENAI_API_KEY and run python -m evals again before shipping.")
        return 2
    return 0


def _employee(pipeline, email: str):
    person = pipeline.store.employee(email)
    if person is None:
        raise RuntimeError(f"This email is not on the employee list: {email}")
    return person


def _ask_case(pipeline, case: dict) -> Check:
    result, trace, detail = _run_question(pipeline, case)
    if detail:
        check = _failed(case, detail)
    else:
        check = _passed(case)
    if case.get("live_role") or case.get("show_answer"):
        check.answer = result.answer
    if case.get("measure_length"):
        check.word_count = len(result.answer.split())
    if detail:
        return check
    if case.get("then_repeat"):
        before = _answer_calls(pipeline)
        repeated, repeated_trace = _ask(pipeline, case)
        if repeated.answer != result.answer:
            return _failed(case, "The repeated question did not reuse the same answer.")
        if repeated_trace.cache != "hit":
            return _failed(case, f"Expected a saved answer, cache was {repeated_trace.cache}.")
        if _answer_calls(pipeline) != before:
            return _failed(case, "The repeated question called the model again.")
    return check


def _run_question(pipeline, case: dict):
    result, trace = _ask(pipeline, case)
    detail = _expect_detail(case.get("expect") or {}, result, trace)
    hidden = case.get("expect", {}).get("answer_must_not_contain") or []
    if trace.withheld or case.get("expect", {}).get("withheld"):
        blob = json.dumps(
            {
                "answer": trace.answer,
                "sections": trace.sections,
                "scores": trace.scores,
                "citations": [{"label": item.label, "text": item.text} for item in trace.citations],
            }
        )
        for banned in hidden:
            if str(banned) in blob:
                detail = detail or f"Restricted text leaked into the trace: {banned}"
    return result, trace, detail


def _ask(pipeline, case: dict):
    person = _employee(pipeline, case["email"])
    history = case.get("history") or []
    if first_question := case.get("after_question"):
        first = pipeline.ask(person, first_question, history)
        history = [
            *history, {"role": "user", "text": first_question},
            {"role": "assistant", "text": first.answer, "passage_ids": first.passage_ids},
        ]
    result = pipeline.ask(person, case["question"], history)
    trace = pipeline.store.get_trace(result.trace_id)
    return result, trace


def _sanitize_case(case: dict) -> Check:
    cleaned = sanitize(case["draft"], [case["passage"]])
    detail = _text_rules(cleaned, case)
    if "http" in case.get("answer_must_not_contain", []) and "http" in cleaned:
        detail = detail or "An invented link was still in the answer."
    return _failed(case, detail) if detail else _passed(case)


def _expect_detail(expect: dict, result, trace) -> str:
    if "outcome" in expect and result.outcome != expect["outcome"]:
        return f"Outcome was {result.outcome}, expected {expect['outcome']}."
    if "answer_is" in expect and result.answer != expect["answer_is"]:
        return f"Answer was: {result.answer}"
    if expect.get("fixed_refusal") and result.answer != REFUSAL:
        return "The response did not match the fixed refusal."
    if "max_words" in expect and len(result.answer.split()) > expect["max_words"]:
        return f"Answer exceeded {expect['max_words']} words."
    if expect.get("exact_summary_fields"):
        fields = summary_source_fields(result.citations)
        expected_answer = "Investigation Summary Report fields\n\n" + "\n".join(f"- {field}" for field in fields)
        if not fields or result.answer != expected_answer:
            return "Summary Report fields were missing, changed, out of order, or followed by extra text."
    if expect.get("gm_travel_units"):
        sections = dict(re.findall(r"\*\*([^*]+)\*\*\s*\n\s*(.*?)(?=\n\n\*\*|\Z)", result.answer, re.S))
        fuel = sections.get("Fuel allowance", "")
        sizes = re.findall(r"\b(\d+)cc\b", fuel)
        if sizes != ["70", "125", "1300", "1500"]:
            return f"Unexpected GM fuel sizes or order: {sizes}."
        if re.findall(r"Rs\. ([\d.]+) per km", fuel) != ["2.0", "2.5", "4.0", "5.0"]:
            return "Fuel rates did not preserve the source amounts and units."
        if re.findall(r"Rs\. ([\d,]+)", sections.get("Hotel allowance", "")) != ["24,000", "28,000", "15,500"]:
            return "Hotel rates did not preserve the source amounts and currency."
        if "Rs. 1,800 per day" not in sections.get("Daily allowance", ""):
            return "Daily allowance did not include the source currency and period."
        medical = sections.get("Health insurance", "")
        if "Rs." in medical or not all(term in medical for term in ("500,000", "12,000 per day")):
            return "Medical limits must preserve the printed values and per-day label without inferring a currency."
    index = 0
    for phrase in expect.get("in_order") or []:
        found = result.answer.find(phrase, index)
        if found < 0:
            return f"Expected in order: {phrase}"
        index = found + len(phrase)
    if expect.get("searched") is False and trace.search != "skipped":
        return "Search ran when it should not have."
    if expect.get("searched") is True and trace.search != "done":
        return "Search did not run."
    if expect.get("model_called") is False and trace.model:
        return f"The answer model ran ({trace.model})."
    if expect.get("model_called") is True and not trace.model:
        return "The answer model did not run."
    if expect.get("classifier_called") is False and trace.classifier_called:
        return "The classifier ran before the block."
    if "cache" in expect and trace.cache != expect["cache"]:
        return f"Saved-answer state was {trace.cache}, expected {expect['cache']}."
    if expect.get("withheld") and not trace.withheld:
        return "A hidden policy was not withheld."
    if "rewritten_equals" in expect and trace.rewritten_question != expect["rewritten_equals"]:
        return f"Search question was: {trace.rewritten_question}"
    if "rewritten_contains" in expect and expect["rewritten_contains"].lower() not in trace.rewritten_question.lower():
        return f"Search question was: {trace.rewritten_question}"
    sections = " ".join(trace.sections)
    if "section_contains" in expect and expect["section_contains"] not in sections:
        return f"Policies read: {sections or 'none'}"
    if "section_must_not_contain" in expect and expect["section_must_not_contain"] in sections:
        return f"The old topic was still in the policies read: {sections}"
    if "citation_has" in expect:
        labels = " ".join(item.label for item in result.citations)
        if expect["citation_has"] not in labels:
            return f"Sources were: {labels or 'none'}"
    passage_text = "\n".join(item.text for item in result.citations)
    for needed in expect.get("passage_must_contain") or []:
        if str(needed).lower() not in passage_text.lower():
            return f"The policy text did not include {needed}."
    for banned in expect.get("passage_must_not_contain") or []:
        if str(banned).lower() in passage_text.lower():
            return f"The policy text included {banned}."
    text_detail = _text_rules(result.answer, {"answer_must_contain": expect.get("answer_must_contain"), "answer_must_not_contain": expect.get("answer_must_not_contain")})
    if text_detail:
        return text_detail
    if expect.get("numbers_grounded"):
        allowed = set(_NUMBER.findall(" ".join(item.text for item in result.citations)))
        extra = [number for number in _NUMBER.findall(result.answer) if number not in allowed]
        if extra:
            return f"The answer used numbers that are not in the policy: {', '.join(extra)}"
    return ""


def _text_rules(answer: str, case: dict) -> str:
    for needed in case.get("answer_must_contain") or []:
        if str(needed) not in answer:
            return f"The answer did not include {needed}. Answer: {answer}"
    for banned in case.get("answer_must_not_contain") or []:
        if str(banned).casefold() in answer.casefold():
            return f"The answer included {banned}."
    return ""


def summary_source_fields(citations) -> list[str]:
    prefix = "Investigation Summary Report template. "
    return [field for item in citations if item.text.startswith(prefix)
            for field in item.text.removeprefix(prefix).rstrip(".").split(". ")]


def _answer_calls(pipeline) -> int:
    calls = getattr(pipeline.llm, "calls", None)
    if not calls:
        return 0
    return int(calls.get("answer", 0))


def _passed(case: dict) -> Check:
    return Check(
        name=case["name"],
        ok=True,
        primary=bool(case.get("primary")),
        case_id=case.get("id", ""),
    )


def _failed(case: dict, detail: str) -> Check:
    return Check(
        name=case.get("name", case.get("id", "check")),
        ok=False,
        primary=bool(case.get("primary")),
        developer_detail=detail,
        case_id=case.get("id", ""),
    )


if __name__ == "__main__":
    raise SystemExit(main())
