import os
import traceback

os.environ.setdefault("HR_DESK_PUBLIC_ORIGIN", "http://localhost:3000")

import app.pipeline as pipeline_module
from app.factory import build_pipeline
from app.models import Employee

pipeline_module._log_error = lambda error, request_id, status: print("ERR", type(error).__name__, error, "".join(traceback.format_exception(error)))

pipeline = build_pipeline()
pipeline.store.cache_lookup = lambda *a, **k: None
pipeline.store.cache_store = lambda *a, **k: None
pipeline.store.add_trace = lambda *a, **k: None
pipeline.store.consume_budget = lambda *a, **k: None
pipeline.store.release_lease = lambda *a, **k: None
checks = [
    (Employee("asm@stylo.example", "ASM", "area_sales_manager"), "What is my tour allowance?"),
    (Employee("hina.gm@stylo.example", "Hina", "general_manager"), "health insurance for me"),
    (Employee("insrah.hr@stylo.example", "Insrah", "hr", True), "What does takaful cover for me?"),
]
for person, question in checks:
    result = pipeline.ask(person, question)
    print(person.designation, result.outcome, result.passage_ids[:6])
