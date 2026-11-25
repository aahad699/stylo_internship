"""Read-only RAG measurement. Cache, traces, and budgets are not written."""

from __future__ import annotations

import json
import os
import re
import time
from collections import Counter, defaultdict
from pathlib import Path

import yaml

os.environ.setdefault("HR_DESK_PUBLIC_ORIGIN", "http://localhost:3000")

import app.pipeline as pipe  # noqa: E402
from app.access import policy_row_roles  # noqa: E402
from app.config import EVIDENCE_MAX_BYTES, RETRIEVE_TOP_K  # noqa: E402
from app.factory import build_pipeline  # noqa: E402
from app.guardrails import contains_named_pay  # noqa: E402
from app.models import Completion, Employee  # noqa: E402
from app.pipeline import Pipeline  # noqa: E402
from app.sanitize import exposes_prompt  # noqa: E402
from app.store_pg import _vector  # noqa: E402
from app.access import query_role  # noqa: E402

ROOT = Path(__file__).resolve().parent
QUESTIONS = ROOT / "questions.yaml"
RESULTS = ROOT / "results.json"
ACCOUNTS = {
    "general_manager": "hina.gm@stylo.example",
    "intern": "sara.intern@stylo.example",
    "hr": "insrah.hr@stylo.example",
}
_INJECT = re.compile(
    r"ignore (?:all |any |previous |prior )?instructions|you are now|system prompt|</?\s*system\s*>|\[INST\]",
    re.I,
)
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_BANNED = ("stylo-prompt-do-not-repeat", "ignore previous", "as an ai", "let me know", "next step")
HOLDER = {"box": {}}


def silence(store) -> None:
    store.cache_lookup = lambda *args, **kwargs: None
    store.cache_store = lambda *args, **kwargs: None
    store.add_trace = lambda *args, **kwargs: None
    store.consume_budget = lambda *args, **kwargs: None
    store.release_lease = lambda *args, **kwargs: None


def channels(store, embedding, query, audiences, today):
    role = query_role(set(audiences))
    vector = _vector(embedding)
    audience_list = list(audiences)
    with store.conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.id FROM policy_chunks c
            JOIN policies p ON p.id = c.policy_id AND p.published
            WHERE string_to_array(c.audience, ',') && %s::text[] AND %s = ANY(c.allowed_roles)
              AND c.effective_from <= %s
              AND (LEAST(c.effective_to, p.superseded_on - 1) IS NULL
                   OR LEAST(c.effective_to, p.superseded_on - 1) >= %s)
            ORDER BY c.embedding <=> %s LIMIT 20
            """,
            (audience_list, role, today, today, vector),
        )
        vector_ids = [row[0] for row in cur.fetchall()]
        cur.execute(
            """
            SELECT c.id FROM policy_chunks c
            JOIN policies p ON p.id = c.policy_id AND p.published
            WHERE string_to_array(c.audience, ',') && %s::text[] AND %s = ANY(c.allowed_roles)
              AND c.effective_from <= %s
              AND (LEAST(c.effective_to, p.superseded_on - 1) IS NULL
                   OR LEAST(c.effective_to, p.superseded_on - 1) >= %s)
              AND c.search_text @@ plainto_tsquery('english', %s)
            LIMIT 20
            """,
            (audience_list, role, today, today, query),
        )
        keyword_ids = [row[0] for row in cur.fetchall()]
    return vector_ids, keyword_ids


class Probe:
    def __init__(self, real, allow: bool) -> None:
        self.real = real
        self.allow = allow

    def embed(self, texts):
        return self.real.embed(texts)

    def rewrite(self, *args, **kwargs):
        return self.real.rewrite(*args, **kwargs)

    def classify(self, *args, **kwargs):
        return self.real.classify(*args, **kwargs)

    def split_topic_queries(self, *args, **kwargs):
        return self.real.split_topic_queries(*args, **kwargs)

    def personal_query(self, *args, **kwargs):
        return self.real.personal_query(*args, **kwargs)

    def answer(self, **kwargs):
        if self.allow:
            return self.real.answer(**kwargs)
        return Completion(text="", finish_reason="probe")

    def answer_topics(self, **kwargs):
        if self.allow:
            return self.real.answer_topics(**kwargs)
        return Completion(text="{}", finish_reason="probe")

    def __getattr__(self, name):
        return getattr(self.real, name)


def install(pipeline, real_search, real_select, real_heading, real_finish):
    def search_wrap(store, embedding, query, audiences, today, **kwargs):
        started = time.perf_counter()
        rows = real_search(store, embedding, query, audiences, today, **kwargs)
        vector_ids, keyword_ids = channels(store, embedding, query, audiences, today)
        HOLDER["box"].setdefault("searches", []).append(
            {
                "query": query,
                "ids": [row.id for row in rows],
                "ms": int((time.perf_counter() - started) * 1000),
                "vector_ids": vector_ids,
                "keyword_ids": keyword_ids,
                "includes_hr": "hr" in set(audiences),
            }
        )
        return rows

    def select_wrap(rows):
        chosen = real_select(rows)
        HOLDER["box"].setdefault("selects", []).append([row.id for row in chosen])
        return chosen

    def heading_wrap(*args, **kwargs):
        rows = real_heading(*args, **kwargs)
        HOLDER["box"].setdefault("headings", []).append({"ids": [row.id for row in rows]})
        return rows

    def finish_wrap(self, employee, question, state, started):
        HOLDER["box"]["state"] = {
            "outcome": state.get("outcome"),
            "history_note": state.get("history_note"),
            "search": state.get("search"),
            "rewritten": state.get("rewritten_question") or "",
            "model": state.get("model"),
            "finish_reason": state.get("finish_reason"),
            "evidence_ids": list(state.get("evidence_ids") or []),
            "citation_ids": [item.passage_id for item in state.get("citations") or [] if item.passage_id],
            "answer": (state.get("answer") or "")[:900],
            "withheld": bool(state.get("withheld")),
            "topic_count": state.get("topic_count"),
            "probed": isinstance(self.llm, Probe) and not self.llm.allow,
        }
        return real_finish(self, employee, question, state, started)

    pipe.search_passages = search_wrap
    pipe.select_passages = select_wrap
    pipeline.store.heading_hits = heading_wrap
    Pipeline._finish = finish_wrap


def employee_for(store, designation: str) -> Employee:
    email = ACCOUNTS.get(designation)
    if email:
        found = store.employee(email)
        if found is None or found.designation != designation:
            raise RuntimeError(f"Verified account missing for {designation}")
        return found
    return Employee(f"{designation}@audit.local", "Audit", designation, False)


def best_ranks(box: dict, designation: str) -> dict[str, int]:
    best: dict[str, int] = {}
    searches = box.get("searches") or []
    if designation != "hr":
        searches = [search for search in searches if not search.get("includes_hr")]
    if searches:
        for search in searches:
            for index, passage_id in enumerate(search["ids"], 1):
                best[passage_id] = min(best.get(passage_id, 99), index)
        return best
    state = box.get("state") or {}
    ordered = list(dict.fromkeys([*(state.get("citation_ids") or []), *(state.get("evidence_ids") or [])]))
    for index, passage_id in enumerate(ordered, 1):
        best[passage_id] = index
    if not best:
        for hit in box.get("headings") or []:
            for index, passage_id in enumerate(hit["ids"], 1):
                best[passage_id] = min(best.get(passage_id, 99), index)
    return best


def hit(expected, ranks, k, match) -> bool:
    found = [ranks.get(passage_id, 99) <= k for passage_id in expected]
    return all(found) if match == "all" else any(found)


def cause(case, ranks, box, index) -> str:
    expected = case["expected"]
    if any(passage_id not in index for passage_id in expected):
        return "data missing"
    state = box.get("state") or {}
    note = state.get("history_note") or ""
    searches = box.get("searches") or []
    if case["designation"] != "hr":
        searches = [item for item in searches if not item.get("includes_hr")]
    if case["category"] == "follow_up" and note in {"model", "repeat-boundary", "kept-question", "topic-change"}:
        return "rewrite step"
    if case["category"] == "multi":
        return "topic splitting or recognition"
    heading_ids = {passage_id for item in box.get("headings") or [] for passage_id in item["ids"]}
    if any(passage_id in heading_ids and passage_id not in ranks for passage_id in expected):
        return "filter"
    selected = [passage_id for group in box.get("selects") or [] for passage_id in group]
    if any(passage_id in selected for passage_id in expected) and state.get("model"):
        evidence = set(state.get("evidence_ids") or [])
        if not any(passage_id in evidence for passage_id in expected):
            return "evidence budget"
    if any(ranks.get(passage_id, 99) <= 16 for passage_id in expected):
        return "ranking"
    if not searches and (state.get("withheld") or note in {"employee-designation", "ta-follow-up"}):
        return "filter"
    vector_ids = {passage_id for search in searches for passage_id in search["vector_ids"]}
    keyword_ids = {passage_id for search in searches for passage_id in search["keyword_ids"]}
    if any(passage_id in vector_ids or passage_id in keyword_ids for passage_id in expected):
        return "ranking"
    top = [passage_id for passage_id, rank in sorted(ranks.items(), key=lambda item: item[1]) if rank <= 4]
    headings = {index[passage_id]["heading"] for passage_id in expected if passage_id in index}
    if any(index.get(passage_id, {}).get("heading") in headings for passage_id in top):
        return "chunking"
    if case["category"] in {"misspelling", "alias", "keyword"}:
        return "vocabulary mismatch"
    return "vocabulary mismatch" if searches or heading_ids else "filter"


def false_positives(case, ranks, index) -> list[str]:
    expected = set(case["expected"])
    bad = []
    for passage_id, rank in ranks.items():
        if rank > 4 or passage_id in expected:
            continue
        row = index.get(passage_id)
        if not row:
            continue
        if case.get("document") and row["title"] != case["document"]:
            bad.append(passage_id)
            continue
        if case["category"] in {"personal", "alias"} and row["content_type"] == "table":
            roles = policy_row_roles(row["heading"], row["start"], row["title"]) or set()
            if roles and case["designation"] not in roles | {"hr"} and "all levels" not in row["start"].casefold():
                bad.append(passage_id)
    return bad


def chunk_audit(store) -> dict:
    with store.conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.id, p.title, c.heading, c.content_type, c.body, octet_length(c.body), c.image_path <> ''
            FROM policy_chunks c JOIN policies p ON p.id = c.policy_id
            ORDER BY p.title, c.id
            """
        )
        rows = cur.fetchall()
    end = re.compile(r"[.!?…][\"')\]]*$")
    flags = Counter()
    sizes = []
    overlap_pairs = 0
    overlap_max = 0
    previous = None
    samples = []
    pinned = {
        "doc-c3ff72f11b544fd9:26", "doc-c3ff72f11b544fd9:28", "doc-c3ff72f11b544fd9:53",
        "doc-c3ff72f11b544fd9:56", "doc-c3ff72f11b544fd9:57", "doc-c3ff72f11b544fd9:45",
        "doc-c3ff72f11b544fd9:34", "doc-922ff7991e7a9615:21", "doc-922ff7991e7a9615:18",
        "doc-922ff7991e7a9615:19", "doc-6a4e4417299da190:33", "doc-6a4e4417299da190:108",
        "doc-6a4e4417299da190:102", "doc-e50809e1dd67ce54:0", "doc-e50809e1dd67ce54:5",
        "doc-14908bcf9bf82ed2:16", "doc-18997a1a72f05567:3", "doc-c3ff72f11b544fd9:48",
    }
    for passage_id, title, heading, kind, body, size, image in rows:
        text = body or ""
        sizes.append(size or 0)
        start_mid = bool(text[:1].islower()) if text else False
        end_mid = bool(text) and not end.search(text.strip())
        flags["start_mid"] += int(start_mid)
        flags["end_mid"] += int(end_mid)
        if kind == "table" and "grade" not in text.casefold() and "plan:" not in text.casefold():
            flags["table_without_grade_label"] += 1
        if previous and previous[0] == title and previous[1] == heading:
            shared = 0
            limit = min(500, len(previous[2]), len(text))
            for count in range(limit, 30, -1):
                if previous[2][-count:] == text[:count]:
                    shared = count
                    break
            if shared:
                overlap_pairs += 1
                overlap_max = max(overlap_max, shared)
                flags["overlap_80"] += int(shared >= 80)
        previous = (title, heading, text)
        if passage_id in pinned or (kind in {"table", "form"} and len(samples) < 34):
            lowered = text.casefold()
            samples.append(
                {
                    "id": passage_id,
                    "title": title,
                    "heading": heading,
                    "type": kind,
                    "bytes": size,
                    "image": bool(image),
                    "start_mid": start_mid,
                    "end_mid": end_mid,
                    "has_grade": "grade" in lowered or lowered.startswith("plan"),
                    "currency": "rs." in lowered or "rupee" in lowered,
                    "per_day": "per day" in lowered,
                    "per_km": "per km" in lowered or "/km" in lowered,
                    "has_300km": "300 km" in lowered,
                    "start": text[:80].replace("\n", " "),
                    "end": text[-60:].replace("\n", " "),
                }
            )
    sizes.sort()
    return {
        "passages": len(rows),
        "size_min": sizes[0],
        "size_p50": sizes[len(sizes) // 2],
        "size_p90": sizes[int(len(sizes) * 0.9)],
        "size_max": sizes[-1],
        "flags": dict(flags),
        "overlap_pairs": overlap_pairs,
        "overlap_max": overlap_max,
        "samples": [item for item in samples if item["id"] in pinned] + [item for item in samples if item["id"] not in pinned][:12],
        "evidence_budget": EVIDENCE_MAX_BYTES,
        "production_top_k": RETRIEVE_TOP_K,
    }


def security(store) -> dict:
    with store.conn.cursor() as cur:
        cur.execute(
            "SELECT c.id, p.title, c.heading, c.body FROM policy_chunks c JOIN policies p ON p.id = c.policy_id"
        )
        rows = cur.fetchall()
    pay, injected = [], []
    for passage_id, title, heading, body in rows:
        if contains_named_pay(f"{title}\n{heading}\n{body}"):
            pay.append({"id": passage_id, "title": title, "heading": heading})
        if _INJECT.search(body or "") or exposes_prompt(body or ""):
            injected.append({"id": passage_id, "title": title, "heading": heading})
    return {"named_pay": pay, "injected": injected}


def light_index(store) -> dict:
    with store.conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.id, p.title, c.heading, c.content_type, left(c.body, 240)
            FROM policy_chunks c JOIN policies p ON p.id = c.policy_id
            """
        )
        return {
            row[0]: {"title": row[1], "heading": row[2], "content_type": row[3], "start": row[4] or ""}
            for row in cur.fetchall()
        }


def judge(case, state, body: str) -> str:
    answer = state.get("answer") or ""
    folded = answer.casefold()
    if any(phrase in folded for phrase in _BANNED) or exposes_prompt(answer):
        return "leaked"
    outcome = state.get("outcome")
    closed = case["category"] in {"no_answer", "superseded", "intern_blocked"} or (
        case["category"] == "follow_up" and not case.get("expected")
    )
    if closed:
        if outcome == "answered":
            return "leaked" if case["category"] == "intern_blocked" else "wrong"
        return "correct"
    if outcome != "answered":
        return "wrong"
    required = [item.casefold() for item in case.get("must_contain") or []]
    if required and not all(item in folded for item in required):
        return "incomplete" if any(item in folded for item in required) else "wrong"
    extras = [
        number for number in _NUMBER.findall(answer)
        if number not in body and number.replace(",", "") not in body.replace(",", "")
    ]
    if extras:
        return "improvised"
    return "correct"


def summarize(rows: list[dict]) -> dict:
    scored = [row for row in rows if row["expected"]]

    def rate(name):
        return round(sum(1 for row in scored if row[name]) / len(scored), 3) if scored else 0

    total = 0
    for row in scored:
        found = [rank for rank in row["ranks"].values() if rank]
        total += (1 / min(found)) if found else 0
    grouped = {}
    for field in ("category", "designation"):
        buckets = defaultdict(list)
        for row in scored:
            buckets[row[field]].append(row)
        grouped[field] = {
            name: {
                "n": len(items),
                "recall@1": round(sum(item["hit1"] for item in items) / len(items), 3),
                "recall@4": round(sum(item["hit4"] for item in items) / len(items), 3),
                "recall@10": round(sum(item["hit10"] for item in items) / len(items), 3),
            }
            for name, items in sorted(buckets.items())
        }
    channels = Counter(row["channel"] for row in scored)
    return {
        "questions": len(rows),
        "scored": len(scored),
        "recall@1": rate("hit1"),
        "recall@4": rate("hit4"),
        "recall@10": rate("hit10"),
        "mrr": round(total / len(scored), 3) if scored else 0,
        "by_category": grouped["category"],
        "by_designation": grouped["designation"],
        "channels": dict(channels),
        "budget_drops": sum(1 for row in scored if row["budget_drop"]),
        "retrieved_not_kept": sum(1 for row in scored if row["retrieved_not_kept"]),
        "false_positive_questions": sum(1 for row in rows if row["false_positives"]),
        "confident_empty": sum(1 for row in rows if row["confident_empty"]),
        "causes": dict(Counter(row["cause"] for row in rows if row.get("cause"))),
        "latency_p50": sorted(row["latency_ms"] for row in rows)[len(rows) // 2],
        "latency_max": max(row["latency_ms"] for row in rows),
    }


def ask(pipeline, case, answer: bool) -> dict:
    HOLDER["box"] = {}
    real = pipeline.llm.real if isinstance(pipeline.llm, Probe) else pipeline.llm
    pipeline.llm = Probe(real, answer)
    employee = employee_for(pipeline.store, case["designation"])
    started = time.perf_counter()
    pipeline.ask(employee, case["question"], case.get("history"))
    box = HOLDER["box"]
    box["latency_ms"] = int((time.perf_counter() - started) * 1000)
    return box


def score(case, box, index) -> dict:
    ranks = best_ranks(box, case["designation"])
    expected = list(case.get("expected") or [])
    match = case.get("match") or "all"
    state = box.get("state") or {}
    searches = box.get("searches") or []
    if case["designation"] != "hr":
        searches = [item for item in searches if not item.get("includes_hr")]
    selected = [passage_id for group in box.get("selects") or [] for passage_id in group]
    evidence = state.get("evidence_ids") or []
    citations = state.get("citation_ids") or []
    kept = set(selected + evidence + citations)
    vector_hit = any(passage_id in search["vector_ids"] for search in searches for passage_id in expected)
    keyword_hit = any(passage_id in search["keyword_ids"] for search in searches for passage_id in expected)
    if searches and vector_hit and keyword_hit:
        channel = "both"
    elif searches and vector_hit:
        channel = "vector"
    elif searches and keyword_hit:
        channel = "keyword"
    elif any(passage_id in {item for hit in box.get("headings") or [] for item in hit["ids"]} or passage_id in citations for passage_id in expected):
        channel = "heading"
    else:
        channel = "none"
    probed = bool(state.get("probed"))
    row = {
        "id": case["id"],
        "question": case["question"],
        "designation": case["designation"],
        "category": case["category"],
        "document": case.get("document") or "",
        "expected": expected,
        "match": match,
        "hit1": bool(expected) and hit(expected, ranks, 1, match),
        "hit4": bool(expected) and hit(expected, ranks, 4, match),
        "hit10": bool(expected) and hit(expected, ranks, 10, match),
        "ranks": {passage_id: ranks[passage_id] for passage_id in expected if passage_id in ranks},
        "top": [passage_id for passage_id, rank in sorted(ranks.items(), key=lambda item: item[1]) if rank <= 4][:8],
        "channel": channel,
        "retrieved_not_kept": bool(expected) and any(ranks.get(passage_id, 99) <= 10 for passage_id in expected) and not any(
            passage_id in kept for passage_id in expected
        ),
        "budget_drop": bool(expected) and any(passage_id in selected for passage_id in expected) and not any(
            passage_id in evidence or passage_id in citations for passage_id in expected
        ) and bool(state.get("model")),
        "false_positives": false_positives(case, ranks, index),
        "latency_ms": box.get("latency_ms") or 0,
        "outcome": None if probed else state.get("outcome"),
        "history_note": state.get("history_note"),
        "rewritten": state.get("rewritten"),
        "search": state.get("search"),
        "withheld": state.get("withheld"),
        "topic_count": state.get("topic_count"),
        "confident_empty": (not expected) and bool(ranks) and case["category"] in {"no_answer", "superseded", "intern_blocked"},
    }
    if expected and not row["hit4"]:
        row["cause"] = cause(case, ranks, box, index)
    if box.get("error"):
        row["error"] = box["error"]
    return row


def bodies_for(store, ids: list[str]) -> str:
    if not ids:
        return ""
    with store.conn.cursor() as cur:
        cur.execute("SELECT body FROM policy_chunks WHERE id = ANY(%s)", (ids,))
        return "\n".join(row[0] or "" for row in cur.fetchall())


def main() -> None:
    cases = yaml.safe_load(QUESTIONS.read_text(encoding="utf-8"))
    if len(cases) < 80:
        raise RuntimeError(f"Need at least 80 questions, found {len(cases)}")
    pipeline = build_pipeline()
    silence(pipeline.store)
    index = light_index(pipeline.store)
    real_search = pipe.search_passages
    real_select = pipe.select_passages
    real_heading = pipeline.store.heading_hits
    real_finish = Pipeline._finish
    install(pipeline, real_search, real_select, real_heading, real_finish)
    previous_answers = []
    if os.environ.get("RAG_AUDIT_RETRIEVAL_ONLY") and RESULTS.exists():
        previous_answers = json.loads(RESULTS.read_text(encoding="utf-8")).get("answers") or []

    def remember(error, request_id, status):
        HOLDER["box"]["error"] = f"{type(error).__name__}: {error}"

    pipe._log_error = remember
    try:
        measured = []
        for case in cases:
            try:
                box = ask(pipeline, case, False)
                row = score(case, box, index)
            except Exception as error:
                row = {
                    "id": case["id"], "question": case["question"], "designation": case["designation"],
                    "category": case["category"], "document": case.get("document") or "",
                    "expected": list(case.get("expected") or []), "match": case.get("match") or "all",
                    "hit1": False, "hit4": False, "hit10": False, "ranks": {}, "top": [], "channel": "none",
                    "retrieved_not_kept": False, "budget_drop": False, "false_positives": [], "latency_ms": 0,
                    "outcome": "error", "history_note": "", "rewritten": "", "search": "", "withheld": False,
                    "topic_count": 1, "confident_empty": False, "cause": "other", "returned": [],
                    "error": type(error).__name__,
                }
            measured.append(row)
            print(f"{row['id']:22} hit4={int(row['hit4'])} {row.get('cause','')} {row['latency_ms']}ms", flush=True)
        if os.environ.get("RAG_AUDIT_RETRIEVAL_ONLY"):
            answers = previous_answers
        else:
            answers = []
            for case in cases:
                if not case.get("answer"):
                    continue
                if case["designation"] not in ACCOUNTS:
                    continue
                box = ask(pipeline, case, True)
                state = box.get("state") or {}
                cited = list(dict.fromkeys([*(state.get("citation_ids") or []), *(state.get("evidence_ids") or [])]))
                label = judge(case, state, bodies_for(pipeline.store, cited))
                path = (
                    "recalled" if state.get("history_note") == "question-recall" else
                    "model" if state.get("model") else "deterministic"
                )
                answers.append(
                    {
                        "id": case["id"],
                        "designation": case["designation"],
                        "question": case["question"],
                        "label": label,
                        "path": path,
                        "outcome": state.get("outcome"),
                        "finish_reason": state.get("finish_reason"),
                        "history_note": state.get("history_note"),
                        "rewritten": state.get("rewritten"),
                        "model": state.get("model"),
                        "citation_ids": cited,
                        "answer": state.get("answer") or "",
                        "latency_ms": box.get("latency_ms"),
                    }
                )
                print(f"answer {case['id']:22} {label} {path} {state.get('outcome')}", flush=True)
        payload = {
            "summary": summarize(measured),
            "chunking": chunk_audit(pipeline.store),
            "security": security(pipeline.store),
            "rows": measured,
            "answers": answers,
            "failures": [row for row in measured if row.get("cause")],
        }
        RESULTS.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(json.dumps(payload["summary"], indent=2))
    finally:
        pipe.search_passages = real_search
        pipe.select_passages = real_select
        pipeline.store.heading_hits = real_heading
        Pipeline._finish = real_finish


if __name__ == "__main__":
    main()
