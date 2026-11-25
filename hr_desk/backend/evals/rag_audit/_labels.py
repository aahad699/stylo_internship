import json
import re
from pathlib import Path

import yaml

rows = {row["id"]: row for row in json.loads(Path("evals/rag_audit/quality_answers.json").read_text(encoding="utf-8"))["rows"]}
questions = []
for name in ("questions.yaml", "questions_authored.yaml"):
    questions.extend(yaml.safe_load((Path("evals/rag_audit") / name).read_text(encoding="utf-8")))
wanted = [item for item in questions if item.get("answer") or str(item["id"]).startswith("new_")]
print("answers", len(rows), "checked", len(wanted))
for item in wanted:
    row = rows.get(item["id"])
    if row is None:
        print("MISSING", item["id"])
        continue
    text = row["answer"]
    need = item.get("must_contain") or []
    ok = all(part.casefold() in text.casefold() for part in need)
    redacted = re.sub(r"\d[\d,]*", "#", text).replace("\n", " ")[:140]
    print(f"{item['id']}\t{row['outcome']}\tneed={ok}\t{item['category']}\t{redacted}")
