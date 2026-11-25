import json
from pathlib import Path

rows = {row["id"]: row["answer"].casefold() for row in json.loads(Path("evals/rag_audit/quality_answers.json").read_text(encoding="utf-8"))["rows"]}
checks = {
    "new_values": ["respect"],
    "new_multi": ["hotel"],
    "new_annexure_keep": ["annexure"],
    "new_store_medical": ["plan a", "plan b", "plan c", "plan d", "plan e"],
    "med_panel": ["panel", "non-panel"],
    "med_maternity": ["annexure"],
    "med_health": ["annexure"],
    "coc_values": ["respect", "trust"],
    "new_old_travel": ["2025", "april"],
    "fu_yes": ["fuel"],
}
for key, words in checks.items():
    text = rows.get(key, "")
    print(key, "present" if text else "ABSENT", {word: word in text for word in words})
