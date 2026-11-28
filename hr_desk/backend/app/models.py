"""Records that move through the ask pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class Employee:
    email: str
    name: str
    designation: str
    is_admin: bool = False
    employee_type: str | None = None
    is_permanent: bool | None = None
    medical_plan: str | None = None
    joined_on: date | None = None

    def __post_init__(self) -> None:
        from .access import default_employee_type, default_is_permanent, default_medical_plan

        if not self.employee_type:
            object.__setattr__(self, "employee_type", default_employee_type(self.designation))
        if self.is_permanent is None:
            object.__setattr__(self, "is_permanent", default_is_permanent(self.designation))
        if self.medical_plan is None:
            object.__setattr__(self, "medical_plan", default_medical_plan(self.designation))


@dataclass
class PolicyDraft:
    id: str
    title: str
    audience: str
    effective_from: date
    effective_to: date | None
    body: str
    chunks: list["ChunkDraft"] | None = None
    source_name: str = ""
    warnings: list[str] = field(default_factory=list)
    previous_id: str | None = None
    superseded_on: date | None = None


@dataclass
class ChunkDraft:
    section_label: str
    heading: str
    body: str
    embed_text: str
    heading_path: str = ""
    content_type: str = "section"
    page: int | None = None
    image_path: str = ""
    polarity: str = ""


@dataclass
class Passage:
    id: str
    policy_id: str
    title: str
    section_label: str
    heading: str
    body: str
    audience: str
    effective_from: date
    effective_to: date | None
    vec_score: float = 0.0
    kw_score: float = 0.0
    score: float = 0.0
    heading_path: str = ""
    content_type: str = "section"
    page: int | None = None
    image_path: str = ""
    polarity: str = ""
    allowed_roles: tuple[str, ...] | None = None


@dataclass(frozen=True)
class Citation:
    label: str
    text: str
    image: str = ""
    passage_id: str = ""
    title: str = ""


@dataclass
class Completion:
    text: str
    tokens_in: int = 0
    tokens_out: int = 0
    finish_reason: str = "stop"


@dataclass
class TraceRecord:
    id: str
    created_at: str
    email: str
    name: str
    designation: str
    question: str
    guardrail: str
    guardrail_reason: str
    classifier_called: bool
    cache: str
    rewritten_question: str
    history_note: str
    search: str
    sections: list[str] = field(default_factory=list)
    withheld: bool = False
    scores: list[dict] = field(default_factory=list)
    model: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    outcome: str = ""
    answer: str = ""
    citations: list[Citation] = field(default_factory=list)
    steps: list[dict] = field(default_factory=list)
    prompt_version: str = ""
    passage_ids: list[str] = field(default_factory=list)


@dataclass
class AskResult:
    answer: str
    outcome: str
    citations: list[Citation]
    trace_id: str
    valid_until: date | None = None
    passage_ids: list[str] = field(default_factory=list)
