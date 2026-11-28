"""Shared settings. One place for the cost limits."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BACKEND_ROOT = Path(__file__).resolve().parents[1]
DESK_ROOT = BACKEND_ROOT.parent
load_dotenv(DESK_ROOT / ".env")
DATA_ROOT = Path(os.environ.get("HR_DESK_DATA_ROOT", BACKEND_ROOT / "data")).resolve()

MAX_USER_CHARS = 1000
SESSION_HOURS = 12
MAX_UPLOAD_BYTES = 64 * 1024 * 1024 #64MB
LOGIN_LIMIT = 8
LOGIN_WINDOW_S = 15 * 60
ASK_LIMIT = 20
ASK_WINDOW_S = 15 * 60
CLASSIFIER_MAX_TOKENS = 30
REWRITE_MAX_TOKENS = 120
ANSWER_MAX_TOKENS_NANO = 400
ANSWER_MAX_TOKENS_MINI = 700
TEMPERATURE_ANSWER = 0.1
TEMPERATURE_CLASSIFIER = 0.0
TIMEOUT_CLASSIFIER_S = 8.0
TIMEOUT_ANSWER_S = 20.0
TIMEOUT_MODERATION_S = 8.0
CACHE_TTL_MINUTES = 60
CACHE_SIMILARITY = 0.94
CACHE_MAX_ROWS = 1000
CHAT_RETENTION_DAYS = 30
CHAT_MAX_MESSAGES = 40
CHAT_CONTEXT_BYTES = 1000
ASK_DEADLINE_S = 35
TEAM_ASK_LIMIT = 120
TEAM_ASK_WINDOW_S = 15 * 60
TEAM_DAILY_ASK_LIMIT = 600
TEAM_CONCURRENCY = 4
CHUNK_CHARS = 3200
CHUNK_OVERLAP = 400
EVIDENCE_MAX_BYTES = 12000
RETRIEVE_TOP_K = 6
RETRIEVE_CANDIDATES = 20
RRF_K = 60
# Floor on the reranker score. 0 until the tuning set chooses it.
RERANK_FLOOR = 0.0
MIN_VECTOR_SCORE = 0.25
# The closest meaning skips the word filter only when it leads the next passage by this much.
# Measured: 0.083 for the clause that answers "Can I bring a lawyer?", at most 0.029 for unanswerable questions.
VECTOR_LEAD = 0.05
HISTORY_TURNS = 6
PASSAGE_SCORE_WINDOW = 0.12
MAX_PASSAGES = 4

CANARY = "STYLO-PROMPT-DO-NOT-REPEAT"

TOO_LONG = "Please ask a shorter question."
EMPTY_QUESTION = "Type a question first."
UNAVAILABLE = "The assistant is not available right now. Try again in a moment."


def model_nano() -> str:
    return os.environ.get("HR_DESK_MODEL_NANO", "gpt-5-nano").strip() or "gpt-5-nano"


def model_mini() -> str:
    return os.environ.get("HR_DESK_MODEL_MINI", "gpt-6-luna").strip() or "gpt-6-luna"


def embed_model() -> str:
    return os.environ.get("HR_DESK_EMBED_MODEL", "text-embedding-3-small").strip() or "text-embedding-3-small"


def openai_api_key() -> str:
    return (os.environ.get("OPENAI_API_KEY") or "").strip()


def answer_max_tokens(model: str) -> int:
    if model == model_mini():
        return ANSWER_MAX_TOKENS_MINI
    return ANSWER_MAX_TOKENS_NANO
