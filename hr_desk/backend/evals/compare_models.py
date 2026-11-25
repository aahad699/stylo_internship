"""Compare answer generation on frozen, authorized evidence without changing the app router."""

from collections import Counter
import re

from app.access import role_label
from app.config import answer_max_tokens, model_mini, model_nano
from app.factory import build_pipeline
from app.pipeline import _strip_closing_offers
from app.prompting import model_context, system_prompt
from app.sanitize import forbidden_output
from evals.run import _ask, _employee, live_cases, summary_source_fields

_LIMITS = {"live_summary": None, "hr_ceo_message": 1, "hr_topic_shift": 3, "hr_travel_approver": 2}


def main() -> None:
    pipeline = build_pipeline()
    today = pipeline.today()
    cases = {case["id"]: case for case in live_cases()}
    evidence = {}
    for case_id in _LIMITS:
        case = cases[case_id]
        result, _ = _ask(pipeline, case)
        passages = {p.id: p for p in pipeline.store.passages_by_id(result.passage_ids)}
        evidence[case_id] = (result, [passages[id] for id in result.passage_ids if id in passages])
        if not evidence[case_id][1]:
            raise RuntimeError(f"No authorized comparison evidence for {case_id}.")

    counts: dict[tuple[str, str], Counter] = {}
    for model in (model_nano(), model_mini()):
        for case_id, limit in _LIMITS.items():
            case = cases[case_id]
            employee = _employee(pipeline, case["email"])
            result, passages = evidence[case_id]
            fields = summary_source_fields(result.citations) if case_id == "live_summary" else []
            row = counts.setdefault((model, case_id), Counter())
            question = case["question"].replace("summart report", "summary report")
            rendered = system_prompt(today, role_label(employee.designation))
            for run in range(1, 4):
                with model_context(today, role_label(employee.designation)):
                    completion = pipeline.llm.answer(system=rendered, question=question, background="", passages=passages,
                                                     model=model, max_tokens=answer_max_tokens(model))
                draft = completion.text
                offer = _strip_closing_offers(draft) != draft
                merged = bool(fields and any(sum(field.casefold() in line.casefold() for field in fields) > 1
                                             for line in draft.splitlines()))
                changed = bool(fields and not all(any(
                    item.lstrip("-* ").rstrip(".") == field for item in draft.splitlines()
                ) for field in fields))
                extra = bool(limit is not None and (
                    len(re.split(r"(?<!\bRs)(?<=[.!?])\s+(?=[A-Z])", draft.strip())) > limit
                ))
                guarded = forbidden_output(draft)
                row.update(offer=offer, merged=merged, changed=changed, extra=extra, guarded=guarded)
                print(f"[{model} {case_id} {run}/3] offers={offer} merged={merged} "
                      f"field_changes={changed} extra_sentences={extra} output_guard_match={guarded}", flush=True)
                print(draft if not guarded else "[Draft withheld by the unchanged output guard.]", flush=True)
                print(flush=True)
    print("=== COMPARISON COUNTS (each out of 3) ===")
    for (model, case_id), row in counts.items():
        print(f"{model},{case_id},{row['offer']},{row['merged']},{row['changed']},{row['extra']},{row['guarded']}")


if __name__ == "__main__":
    main()
