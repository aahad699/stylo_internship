"""Freeze a 70/30 tuning and holdout split. Stratified by category and designation.

The file is written once. Later runs refuse to replace it, so tuning cannot move the holdout.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
QUESTIONS = HERE / "questions.yaml"
SPLIT = HERE / "split.json"
SEED = "retrieval-split-2026-11-24"


def build() -> dict:
    questions = yaml.safe_load(QUESTIONS.read_text(encoding="utf-8"))
    groups = defaultdict(list)
    for item in questions:
        groups[(item["category"], item["designation"])].append(item["id"])
    tuning, holdout = [], []
    for key in sorted(groups):
        ids = sorted(groups[key], key=lambda item: hashlib.sha256(f"{SEED}:{item}".encode()).hexdigest())
        if len(ids) == 1:
            bucket = int(hashlib.sha256(f"{SEED}:cell:{ids[0]}".encode()).hexdigest()[:2], 16) / 255
            (holdout if bucket < 0.30 else tuning).append(ids[0])
            continue
        take = int(round(len(ids) * 0.70))
        take = min(max(take, 1), len(ids) - 1)
        tuning.extend(ids[:take])
        holdout.extend(ids[take:])
    return {
        "seed": SEED,
        "stratified_by": ["category", "designation"],
        "tuning": sorted(tuning),
        "holdout": sorted(holdout),
    }


def main() -> None:
    if SPLIT.exists():
        raise SystemExit(f"{SPLIT.name} already exists and is not rewritten.")
    payload = build()
    SPLIT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"tuning {len(payload['tuning'])} holdout {len(payload['holdout'])}")


if __name__ == "__main__":
    main()
