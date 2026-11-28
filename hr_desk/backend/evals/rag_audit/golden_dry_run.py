"""Retrieval dry run for the temporary golden set.

Does not call the answer model. Embeds each question, searches, and records
whether the expected passage is in the top 4. Follow-ups are also searched
with the previous question prepended, which is the text a rewrite should use.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from pathlib import Path

import yaml

os.environ.setdefault("HR_DESK_PUBLIC_ORIGIN", "http://localhost:3000")
os.environ.setdefault("DATABASE_URL", "postgresql://hr_desk:hr_desk@127.0.0.1:5433/hr_desk")
os.environ.setdefault("PYTHONUNBUFFERED", "1")

from app.access import allowed_audiences, bind_employee_access, reset_employee_access
from app.aliases import expand_query
from app.factory import build_pipeline
from app.models import Employee
from app.retrieve import search_passages

HERE = Path(__file__).resolve().parent
GOLDEN = HERE.parents[2] / "md_files" / "golden_qa_temp.yaml"
OUT = HERE.parents[2] / ".runtime" / "golden-dry-run.json"


def main() -> None:
    questions = yaml.safe_load(GOLDEN.read_text(encoding="utf-8"))
    pipeline = build_pipeline()
    rows = []
    by_doc = defaultdict(lambda: {"n": 0, "hit4": 0, "hit4_with_context": 0})
    for item in questions:
        employee = Employee(f"{item['designation']}@stylo.example", "Eval", item["designation"])
        audiences = set(allowed_audiences(employee.designation))
        token = bind_employee_access(employee)
        try:
            asked = expand_query(item["question"])
            bare = pipeline.llm.embed([asked])[0]
            found = search_passages(pipeline.store, bare, asked, audiences, pipeline.today(), designation=employee.designation)
        finally:
            reset_employee_access(token)
        ids = [passage.id for passage in found]
        expected = set(item["expected"])
        match = item.get("match") or "all"
        hit = _hit(expected, ids[:4], match)
        context_hit = hit
        context_ids: list[str] = []
        prior = item.get("history_question") or ""
        if prior:
            combined = expand_query(f"{prior} {item['question']}")
            token = bind_employee_access(employee)
            try:
                vector = pipeline.llm.embed([combined])[0]
                context_found = search_passages(
                    pipeline.store, vector, combined, audiences, pipeline.today(), designation=employee.designation,
                )
            finally:
                reset_employee_access(token)
            context_ids = [passage.id for passage in context_found]
            context_hit = _hit(expected, context_ids[:4], match) or hit
        row = {
            "id": item["id"],
            "document": item["document"],
            "follow_up": bool(prior),
            "hit4": hit,
            "hit4_with_context": context_hit,
            "top": ids[:4],
            "context_top": context_ids[:4],
            "expected": item["expected"],
        }
        rows.append(row)
        bucket = by_doc[item["document"]]
        bucket["n"] += 1
        bucket["hit4"] += int(hit)
        bucket["hit4_with_context"] += int(context_hit)
        mark = "ok" if (context_hit if prior else hit) else "MISS"
        print(f"{item['id']} {mark} top={','.join(ids[:3])}", flush=True)
    summary = {
        "questions": len(rows),
        "hit4": sum(row["hit4"] for row in rows),
        "hit4_with_context": sum(row["hit4_with_context"] for row in rows),
        "by_document": dict(by_doc),
        "misses": [row for row in rows if not row["hit4_with_context"]],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"summary": summary, "rows": rows}, indent=2), encoding="utf-8")
    print(json.dumps({"questions": summary["questions"], "hit4": summary["hit4"], "with_context": summary["hit4_with_context"], "by": summary["by_document"]}, indent=2))


def _hit(expected: set[str], top: list[str], match: str) -> bool:
    found = expected.intersection(top)
    return bool(found) if match == "any" else found == expected


if __name__ == "__main__":
    main()
