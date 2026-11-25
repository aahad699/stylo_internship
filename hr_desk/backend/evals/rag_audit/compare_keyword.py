"""Compare keyword candidate recall locally, without embeddings or model calls."""

import json
import os
from datetime import date
from pathlib import Path

import psycopg
import yaml

from app.access import allowed_audiences
from app.textutil import keyword_score

HERE = Path(__file__).resolve().parent
SQL = """
WITH q AS (SELECT {query} AS terms)
SELECT c.id, c.heading, c.heading_path, c.body FROM policy_chunks c JOIN policies p ON p.id = c.policy_id AND p.published
CROSS JOIN q
WHERE string_to_array(c.audience, ',') && %s::text[] AND %s = ANY(c.allowed_roles)
  AND c.effective_from <= %s
  AND (LEAST(c.effective_to, p.superseded_on - 1) IS NULL
       OR LEAST(c.effective_to, p.superseded_on - 1) >= %s)
  AND c.search_text @@ q.terms
ORDER BY ts_rank(c.search_text, q.terms) DESC, c.id
LIMIT 20
"""


def main():
    questions = yaml.safe_load((HERE / "questions.yaml").read_text(encoding="utf-8"))
    results = {name: {"scored": 0, "hit": 0, "empty_questions": 0, "empty_with_candidates": 0,
                      "keyword_only_hit": 0, "empty_after_keyword_gate": 0}
               for name in ("and", "or")}
    expressions = {"and": "plainto_tsquery('english', %s)",
                   "or": "replace(plainto_tsquery('english', %s)::text, ' & ', ' | ')::tsquery"}
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        conn.execute("SET default_transaction_read_only = on")
        with conn.cursor() as cur:
            for item in questions:
                role = item["designation"]
                expected = item.get("expected") or []
                for name, expression in expressions.items():
                    cur.execute(SQL.format(query=expression),
                                (item["question"], list(allowed_audiences(role)), role, date(2026, 11, 25), date(2026, 11, 25)))
                    rows = cur.fetchall()
                    found = {row[0] for row in rows}
                    gated = {pid for pid, heading, path, body in rows
                             if max(keyword_score(item["question"], f"{path or ''} {heading}"),
                                    keyword_score(item["question"], body)) >= 0.67}
                    bucket = results[name]
                    if expected:
                        bucket["scored"] += 1
                        matches = [entry in found for entry in expected]
                        bucket["hit"] += int(any(matches) if item.get("match") == "any" else all(matches))
                        matches = [entry in gated for entry in expected]
                        bucket["keyword_only_hit"] += int(any(matches) if item.get("match") == "any" else all(matches))
                    elif item["category"] in {"no_answer", "superseded", "intern_blocked"}:
                        bucket["empty_questions"] += 1
                        bucket["empty_with_candidates"] += int(bool(found))
                        bucket["empty_after_keyword_gate"] += int(bool(gated))
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
