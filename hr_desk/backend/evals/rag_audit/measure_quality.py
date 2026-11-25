"""Retrieval metrics for the frozen tuning and holdout split.

Retrieval mode does not call the answer model. Answer mode does.
Writes do not go to the answer cache or the trace table.
"""

from __future__ import annotations

import json
import os
import statistics
import time
from collections import defaultdict
from pathlib import Path

import yaml

os.environ.setdefault("HR_DESK_PUBLIC_ORIGIN", "http://localhost:3000")
os.environ.setdefault("PYTHONUNBUFFERED", "1")

from app.access import policy_row_roles
from app.factory import build_pipeline
from app.models import Completion, Employee
from app.retrieve import search_passages

HERE = Path(__file__).resolve().parent
QUESTIONS = HERE / "questions.yaml"
AUTHORED = HERE / "questions_authored.yaml"
SPLIT = HERE / "split.json"

VERIFIED = {
    "general_manager": ("hina.gm@stylo.example", "Hina"),
    "intern": ("sara.intern@stylo.example", "Sara"),
    "hr": ("insrah.hr@stylo.example", "Insrah"),
}


class Probe:
    def __init__(self, real, allow_answer: bool):
        self.real = real
        self.allow_answer = allow_answer

    def answer(self, **kwargs):
        if self.allow_answer:
            return self.real.answer(**kwargs)
        return Completion(text="I couldn't find this. Ask HR.", finish_reason="stop")

    def __getattr__(self, name):
        return getattr(self.real, name)


def employee_for(designation: str) -> Employee:
    email, name = VERIFIED.get(designation, (f"{designation}@stylo.example", designation))
    return Employee(email, name, designation, is_admin=designation == "hr")


def load_questions() -> list[dict]:
    rows = yaml.safe_load(QUESTIONS.read_text(encoding="utf-8"))
    if AUTHORED.exists() and os.environ.get("RAG_AUDIT_AUTHORED") == "1":
        rows = [*rows, *yaml.safe_load(AUTHORED.read_text(encoding="utf-8"))]
    return rows


def silence(store) -> None:
    store.cache_lookup = lambda *args, **kwargs: None
    store.cache_store = lambda *args, **kwargs: None
    store.add_trace = lambda *args, **kwargs: None
    store.consume_budget = lambda *args, **kwargs: None
    store.release_lease = lambda *args, **kwargs: None


def titles(store) -> dict[str, str]:
    with store.conn.cursor() as cur:
        cur.execute("SELECT c.id, p.title FROM policy_chunks c JOIN policies p ON p.id = c.policy_id")
        return {row[0]: row[1] for row in cur.fetchall()}


def best_ranks(searches: list[dict], passage_ids: list[str], designation: str) -> dict[str, int]:
    ranks = {}
    for search in searches:
        if search["hr"] and designation != "hr":
            continue
        for index, passage_id in enumerate(search["ids"], start=1):
            ranks[passage_id] = min(ranks.get(passage_id, 99), index)
    for index, passage_id in enumerate(passage_ids, start=1):
        ranks[passage_id] = min(ranks.get(passage_id, 99), index)
    return ranks


def hit(expected: list[str], ranks: dict[str, int], cutoff: int, match: str) -> bool:
    if not expected:
        return False
    found = [ranks.get(item, 99) <= cutoff for item in expected]
    return any(found) if match == "any" else all(found)


def reciprocal(expected: list[str], ranks: dict[str, int], match: str) -> float:
    if not expected:
        return 0.0
    positions = [ranks.get(item, 0) for item in expected]
    positions = [item for item in positions if item]
    if match == "any":
        return 1 / min(positions) if positions else 0.0
    if len(positions) != len(expected):
        return 0.0
    return 1 / max(positions)


def false_positive(question: dict, ranks: dict[str, int], names: dict[str, str]) -> bool:
    expected = set(question.get("expected") or [])
    top = [passage_id for passage_id, rank in ranks.items() if rank <= 4 and passage_id not in expected]
    if not top:
        return False
    if not expected:
        return True
    document = (question.get("document") or "").casefold()
    if not document:
        return False
    return any(document not in names.get(passage_id, "").casefold() for passage_id in top)


def wrong_grade(question: dict, ranks: dict[str, int], store) -> bool:
    designation = question["designation"]
    if designation in {"hr", "intern"}:
        return False
    top = [passage_id for passage_id, rank in ranks.items() if rank <= 4]
    if not top or not hasattr(store, "passages_by_id"):
        return False
    return False


def summarize(rows: list[dict], label: str) -> dict:
    scored = [row for row in rows if row["expected"]]
    def rate(key):
        return round(sum(1 for row in scored if row[key]) / len(scored), 3) if scored else 0
    groups = {}
    for field in ("category", "designation"):
        buckets = defaultdict(list)
        for row in scored:
            buckets[row[field]].append(row)
        groups[field] = {
            name: {
                "n": len(items),
                "recall@1": round(sum(item["hit1"] for item in items) / len(items), 3),
                "recall@4": round(sum(item["hit4"] for item in items) / len(items), 3),
                "recall@10": round(sum(item["hit10"] for item in items) / len(items), 3),
                "mrr": round(sum(item["rr"] for item in items) / len(items), 3),
            }
            for name, items in sorted(buckets.items())
        }
    latencies = [row["ms"] for row in rows]
    return {
        "label": label,
        "n": len(rows),
        "scored": len(scored),
        "recall@1": rate("hit1"),
        "recall@4": rate("hit4"),
        "recall@10": rate("hit10"),
        "mrr": round(sum(row["rr"] for row in scored) / len(scored), 3) if scored else 0,
        "false_positives": sum(1 for row in rows if row["false_positive"]),
        "confident_empty": sum(1 for row in rows if row["confident_empty"]),
        "latency_p50": int(statistics.median(latencies)) if latencies else 0,
        "latency_max": max(latencies) if latencies else 0,
        "by": groups,
    }


def main() -> None:
    split = json.loads(SPLIT.read_text(encoding="utf-8"))
    which = os.environ.get("RAG_AUDIT_SPLIT", "all")
    pipeline = build_pipeline()
    silence(pipeline.store)
    allow_answer = os.environ.get("RAG_AUDIT_ANSWERS") == "1"
    pipeline.llm = Probe(pipeline.llm, allow_answer)
    names = titles(pipeline.store)
    searches: list[dict] = []

    def wrapped(store, embedding, query, audiences, today, **scope):
        rows = search_passages(store, embedding, query, audiences, today, **scope)
        searches.append({
            "hr": "hr" in set(audiences),
            "ids": [row.id for row in rows],
            "top_score": rows[0].score if rows else 0.0,
        })
        return rows

    import app.pipeline as pipeline_module
    pipeline_module.search_passages = wrapped
    questions = load_questions()
    if allow_answer:
        questions = [item for item in questions if item.get("answer") or str(item["id"]).startswith("new_")]
    chosen = set(split["tuning"] if which == "tuning" else split["holdout"] if which == "holdout" else split["tuning"] + split["holdout"])
    if os.environ.get("RAG_AUDIT_AUTHORED") == "1":
        chosen.update(item["id"] for item in questions if item["id"].startswith("new_"))
    results = []
    for item in questions:
        if item["id"] not in chosen:
            continue
        searches.clear()
        started = time.perf_counter()
        history = item.get("history") or []
        try:
            result = pipeline.ask(employee_for(item["designation"]), item["question"], history=history)
            error = ""
        except Exception as exc:
            result = None
            error = type(exc).__name__
        elapsed = int((time.perf_counter() - started) * 1000)
        expected = list(item.get("expected") or [])
        match = item.get("match") or "all"
        ranks = best_ranks(searches, result.passage_ids if result else [], item["designation"])
        top_score = max((search["top_score"] for search in searches if not (search["hr"] and item["designation"] != "hr")), default=0.0)
        row = {
            "id": item["id"],
            "category": item["category"],
            "designation": item["designation"],
            "split": "holdout" if item["id"] in split["holdout"] else "tuning" if item["id"] in split["tuning"] else "authored",
            "expected": expected,
            "hit1": hit(expected, ranks, 1, match),
            "hit4": hit(expected, ranks, 4, match),
            "hit10": hit(expected, ranks, 10, match),
            "rr": reciprocal(expected, ranks, match),
            "false_positive": false_positive(item, ranks, names) if not expected else false_positive(item, ranks, names),
            "confident_empty": (not expected) and item["category"] in {"no_answer", "superseded", "intern_blocked"} and bool(ranks),
            "top_score": round(top_score, 4),
            "outcome": result.outcome if result else "error",
            "ms": elapsed,
            "error": error,
            "answer": (result.answer if result else "")[:400],
        }
        results.append(row)
        print(f"{row['id']} hit4={row['hit4']} {row['outcome']} {elapsed}ms", flush=True)
    payload = {
        "tuning": summarize([row for row in results if row["split"] == "tuning"], "tuning"),
        "holdout": summarize([row for row in results if row["split"] == "holdout"], "holdout"),
        "rows": results,
    }
    out = Path(os.environ.get("RAG_AUDIT_OUT", HERE / "quality.json"))
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps({"tuning": payload["tuning"], "holdout": payload["holdout"]}, indent=2))


if __name__ == "__main__":
    main()
