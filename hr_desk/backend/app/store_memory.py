"""In-memory store for checks that stop before search. The live app uses Postgres."""

from __future__ import annotations

import calendar
import hashlib
import json
import re
import secrets
import threading
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

from .access import shares_audience
from .clauses import heading_has_phrase
from .config import SESSION_HOURS, CACHE_MAX_ROWS, CACHE_TTL_MINUTES, CHAT_MAX_MESSAGES
from .operations import BudgetExceeded, digest

from .embeddings import cosine
from .models import ChunkDraft, Employee, Passage, PolicyDraft, TraceRecord
from .textutil import keyword_score


class MemoryStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.employees: dict[str, Employee] = {}
        self.password_hashes: dict[str, str] = {}
        self.sessions: dict[str, tuple[str, datetime]] = {}
        self.policies: dict[str, PolicyDraft] = {}
        self.chunks: list[dict] = []
        self.cache: list[dict] = []
        self.traces: dict[str, TraceRecord] = {}
        self.actions: list[dict] = []
        self.activations = {}
        self.budget = {}
        self.conversations = {}
        self.conversation_meta = {}
        self.drafts = {}
        self.archived = {}
        self.jobs = {}

    def enqueue_policy_job(self, kind, payload, actor, upload=b""):
        with self._lock:
            if kind == "publish":
                for job in self.jobs.values():
                    if job["kind"] == kind and job["status"] in {"queued", "running"} and job["payload"] == payload:
                        return job["id"]
            key = uuid4().hex
            self.jobs[key] = dict(id=key, kind=kind, payload=payload, actor=actor, upload=upload,
                                  title=payload["title"], status="queued", detail="", claim=None)
            return key

    def list_policy_jobs(self):
        return [{key: job[key] for key in ("id", "kind", "title", "status", "detail")}
                for job in reversed(list(self.jobs.values()))]

    def claim_policy_job(self):
        with self._lock:
            job = next((job for job in self.jobs.values() if job["status"] == "queued"), None)
            if job:
                job.update(status="running", claim=uuid4().hex)
                return dict(job)
            return None

    def renew_policy_job(self, key, claim):
        pass

    def fail_policy_job(self, key, claim, detail):
        with self._lock:
            job = self.jobs[key]
            if job["claim"] == claim and job["status"] == "running":
                job.update(status="failed", detail=detail, claim=None)

    def retry_policy_job(self, key, actor):
        with self._lock:
            job = self.jobs.get(key)
            if not job or job["status"] != "failed":
                return False
            job.update(status="queued", detail="", actor=actor)
            return True

    def _complete_policy_job(self, job_claim, detail):
        if job_claim:
            key, claim = job_claim
            job = self.jobs[key]
            if job["claim"] != claim or job["status"] != "running":
                raise ValueError("The job was interrupted and has been picked up again.")
            job.update(status="completed", claim=None, detail=detail, upload=b"")

    def save_draft(self, key, prepared, *, job_claim=None):
        with self._lock:
            self._complete_policy_job(job_claim, "Ready for review.")
            self.drafts[key] = prepared

    def get_draft(self, key):
        return self.drafts.get(key)

    def list_drafts(self):
        return list(reversed(list(self.drafts.items())))

    def discard_draft(self, key):
        return self.drafts.pop(key, None) is not None

    def rollback_policy(self, key, actor, *, as_of=None):
        with self._lock:
            current = self.policies.get(key)
            if not current or not current.previous_id or current.superseded_on:
                return False
            old_id = current.previous_id
            old = self.policies.get(old_id) or self.archived.pop(old_id, None)
            if old is None:
                return False
            self.archived[key] = self.policies.pop(key)
            self.policies[old_id] = old
            self._set_cutoff(old, None)
            self.cache.clear()
            return True

    def issue_activation(self, email, token):
        with self._lock:
            if email not in self.employees or self.password_hashes.get(email):
                return False
            self.activations[email] = (digest(token), datetime.now(timezone.utc) + timedelta(minutes=30))
            return True

    def redeem_activation(self, token, password_hash):
        with self._lock:
            for email, (stored, expiry) in list(self.activations.items()):
                if stored == digest(token) and expiry > datetime.now(timezone.utc):
                    del self.activations[email]
                    if self.set_password(email, password_hash):
                        return self.employee(email)
            return None

    def consume_budget(self, limits, *, lease=False):
        now = int(datetime.now(timezone.utc).timestamp())
        with self._lock:
            keys = [(scope, now // seconds) for scope, _, seconds in limits]
            for key, (_, limit, seconds) in zip(keys, limits):
                if self.budget.get(key, 0) >= limit:
                    raise BudgetExceeded(seconds - now % seconds)
            for key in keys:
                self.budget[key] = self.budget.get(key, 0) + 1
        return uuid4().hex if lease else None

    def release_lease(self, key):
        pass

    def maintain(self):
        self.purge_old_traces()
        return []

    def create_conversation(self, email):
        key = uuid4().hex
        self.conversations[(email, key)] = []
        return key

    def list_conversations(self, email):
        rows = []
        for (owner, key), messages in self.conversations.items():
            if owner != email:
                continue
            meta = self.conversation_meta.get((owner, key), {})
            custom = (meta.get("title") or "").strip()
            if custom:
                title = custom[:70]
            elif messages and messages[0].get("text"):
                title = messages[0]["text"][:70]
            else:
                title = "New chat"
            rows.append({
                "id": key,
                "title": title,
                "updatedAt": meta.get("updatedAt") or utc_now(),
                "pinned": bool(meta.get("pinned")),
                "pinnedAt": meta.get("pinnedAt") or "",
            })
        rows.sort(key=lambda row: (row["pinned"], row["pinnedAt"], row["updatedAt"]), reverse=True)
        pinned = [row for row in rows if row["pinned"]]
        unpinned = [row for row in rows if not row["pinned"]][:20]
        return [{key: row[key] for key in ("id", "title", "updatedAt", "pinned")} for row in [*pinned, *unpinned]]

    def update_conversation(self, email, key, *, title=None, pinned=None):
        if (email, key) not in self.conversations or (title is None and pinned is None):
            return None
        meta = self.conversation_meta.setdefault((email, key), {})
        if title is not None:
            meta["title"] = title
        if pinned is True:
            meta["pinned"] = True
            meta["pinnedAt"] = meta.get("pinnedAt") or utc_now()
        elif pinned is False:
            meta["pinned"] = False
            meta["pinnedAt"] = ""
        found = next((row for row in self.list_conversations(email) if row["id"] == key), None)
        return found

    def conversation_updated_at(self, email, key):
        return utc_now() if (email, key) in self.conversations else None

    def question_times(self, email):
        rows = [trace for trace in self.traces.values() if trace.email == email]
        rows.sort(key=lambda trace: trace.created_at)
        return [(trace.question or "", trace.created_at) for trace in rows]

    def conversation(self, email, key):
        return self.conversations.get((email, key))

    def append_exchange(self, email, key, user, answer):
        with self._lock:
            messages = self.conversations.get((email, key))
            if messages is not None:
                self.conversations[(email, key)] = [*messages, user, answer][-CHAT_MAX_MESSAGES:]

    def delete_conversation(self, email, key):
        self.conversation_meta.pop((email, key), None)
        return self.conversations.pop((email, key), None) is not None


    def ping(self) -> None:
        return None

    def add_employee(self, email: str, name: str, designation: str, is_admin: bool = False) -> None:
        self.employees[email] = Employee(email, name, designation, is_admin)

    def create_employee(
        self, email: str, name: str, designation: str, is_admin: bool = False, *, actor=None,
        employee_type=None, is_permanent=None, medical_plan=None, joined_on=None,
    ) -> bool:
        with self._lock:
            if email in self.employees:
                return False
            self.employees[email] = Employee(
                email, name, designation, is_admin, employee_type, is_permanent, medical_plan, joined_on,
            )
            if actor:
                self.actions.append({"id": uuid4().hex, "created_at": utc_now(), "email": actor,
                                     "action": "added an email", "detail": email})
            return True

    def list_employees(self) -> list[tuple[Employee, bool]]:
        rows = []
        for employee in sorted(self.employees.values(), key=lambda item: (item.name.lower(), item.email)):
            rows.append((employee, bool(self.password_hashes.get(employee.email))))
        return rows

    def employee(self, email: str) -> Employee | None:
        return self.employees.get(email)

    def password_hash(self, email: str) -> str | None:
        return self.password_hashes.get(email) or None

    def set_password(self, email: str, password_hash: str) -> bool:
        if email not in self.employees or self.password_hashes.get(email):
            return False
        self.password_hashes[email] = password_hash
        return True

    def delete_employee(self, email: str, *, actor=None) -> bool:
        with self._lock:
            if email not in self.employees:
                return False
            self.employees.pop(email, None)
            self.password_hashes.pop(email, None)
            self.activations.pop(email, None)
            self.conversations = {key: value for key, value in self.conversations.items() if key[0] != email}
            self.conversation_meta = {key: value for key, value in self.conversation_meta.items() if key[0] != email}
            self.sessions = {
                token: row for token, row in self.sessions.items() if row[0] != email
            }
            if actor:
                self.actions.append({"id": uuid4().hex, "created_at": utc_now(), "email": actor,
                                     "action": "removed an email", "detail": email})
            return True

    def update_employee(
        self, email: str, designation: str, is_admin: bool, *, actor=None,
        employee_type=None, is_permanent=None, medical_plan=None, joined_on=None, joined_on_set=False,
    ) -> bool:
        with self._lock:
            current = self.employees.get(email)
            if current is None:
                return False
            self.employees[email] = Employee(
                email, current.name, designation, is_admin,
                employee_type if employee_type is not None else current.employee_type,
                is_permanent if is_permanent is not None else current.is_permanent,
                medical_plan if medical_plan is not None else current.medical_plan,
                joined_on if joined_on_set else current.joined_on,
            )
            if actor:
                self.actions.append({"id": uuid4().hex, "created_at": utc_now(), "email": actor,
                                     "action": "changed employee access", "detail": email})
            return True

    def delete_session(self, token: str) -> None:
        with self._lock:
            self.sessions.pop(token or "", None)

    def create_session(self, email: str) -> str:
        with self._lock:
            token = secrets.token_urlsafe(32)
            self.sessions[token] = (email, datetime.now(timezone.utc))
            return token

    def employee_for_session(self, token: str) -> Employee | None:
        row = self.sessions.get(token or "")
        if not row:
            return None
        email, created = row
        if datetime.now(timezone.utc) - created > timedelta(hours=SESSION_HOURS):
            self.sessions.pop(token, None)
            return None
        return self.employees.get(email)

    def has_policy(self, policy_id: str) -> bool:
        return policy_id in self.policies

    def update_policy_access(
        self,
        policy_id: str,
        audience: str,
        effective_from: date,
        effective_to: date | None,
        *, actor=None, as_of=None,
    ) -> bool:
        with self._lock:
            policy = self.policies.get(policy_id)
            if policy is None:
                return False
            if policy.previous_id and policy.effective_from != effective_from:
                previous = self.policies.get(policy.previous_id)
                if previous:
                    self._set_cutoff(previous, effective_from)
            policy.audience = audience
            policy.effective_from = effective_from
            policy.effective_to = effective_to
            for chunk in self.chunks:
                if chunk["policy_id"] != policy_id:
                    continue
                chunk["audience"] = audience
                chunk["effective_from"] = effective_from
                chunk["effective_to"] = effective_to
            self.cache.clear()
            return True

    def delete_policy(self, policy_id: str, actor=None, *, as_of=None) -> bool:
        with self._lock:
            policy = self.policies.get(policy_id)
            if not policy or policy.superseded_on:
                return False
            self.archived[policy_id] = self.policies.pop(policy_id)
            previous = self.policies.get(policy.previous_id)
            if previous and policy.effective_from > (as_of or date.today()):
                self._set_cutoff(previous, None)
            self.cache.clear()
            return True

    def insert_policy(
        self,
        policy: PolicyDraft,
        chunks: list[ChunkDraft],
        vectors: list[list[float]],
        *, replaces=None, draft_key=None, actor=None, as_of=None, job_claim=None,
    ) -> None:
        if len(chunks) != len(vectors):
            raise RuntimeError("Each policy section needs an embedding.")
        with self._lock:
            if draft_key and draft_key not in self.drafts:
                raise ValueError("The draft is no longer available.")
            previous = self.policies.get(replaces)
            if replaces and (not previous or previous.superseded_on):
                raise ValueError("The policy being replaced has changed. Prepare a new draft.")
            scheduled = policy.effective_from > (as_of or date.today())
            if previous and scheduled and previous.effective_to and policy.effective_from > previous.effective_to + timedelta(days=1):
                raise ValueError("This start date would leave a gap after the earlier revision expires.")
            self._complete_policy_job(job_claim, "Scheduled." if scheduled else "Published.")
            if draft_key:
                del self.drafts[draft_key]
            if replaces:
                if scheduled:
                    self._set_cutoff(previous, policy.effective_from)
                else:
                    self.archived[replaces] = self.policies.pop(replaces)
                policy.previous_id = replaces
            if actor:
                self.actions.append({"id": uuid4().hex, "created_at": utc_now(), "email": actor,
                                     "action": "published a policy" if not replaces else "published a replacement",
                                     "detail": policy.title})
            self.policies[policy.id] = policy
            for index, (chunk, vector) in enumerate(zip(chunks, vectors)):
                self.chunks.append(
                    {
                        "id": f"{policy.id}:{index}",
                        "policy_id": policy.id,
                        "title": policy.title,
                        "section_label": chunk.section_label,
                        "heading": chunk.heading,
                        "body": chunk.body,
                        "embed_text": chunk.embed_text,
                        "embedding": vector,
                        "heading_path": chunk.heading_path,
                        "content_type": chunk.content_type,
                        "page": chunk.page,
                        "image_path": chunk.image_path,
                        "polarity": chunk.polarity,
                        "audience": policy.audience,
                        "effective_from": policy.effective_from,
                        "effective_to": policy.effective_to,
                    }
                )
            self.cache.clear()

    def _set_cutoff(self, policy, cutoff):
        policy.superseded_on = cutoff
        for chunk in self.chunks:
            if chunk["policy_id"] == policy.id:
                chunk["superseded_on"] = cutoff

    def list_policies(self) -> list[PolicyDraft]:
        return list(self.policies.values())

    def policy_version(self) -> str:
        payload = []
        for policy in sorted(self.policies.values(), key=lambda item: item.id):
            payload.append(
                {
                    "id": policy.id,
                    "audience": policy.audience,
                    "effective_from": policy.effective_from.isoformat(),
                    "effective_to": policy.effective_to.isoformat() if policy.effective_to else "",
                    "superseded_on": policy.superseded_on.isoformat() if policy.superseded_on else "",
                }
            )
        for chunk in self.chunks:
            if chunk["policy_id"] not in self.policies:
                continue
            payload.append({"chunk": chunk["id"], "body": chunk["body"]})
        raw = json.dumps(payload, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def candidates(
        self,
        embedding: list[float],
        query: str,
        audiences: set[str],
        today: date,
        limit: int,
        *, policy_ids: list[str] | None = None,
    ) -> list[Passage]:
        found: list[Passage] = []
        for chunk in self.chunks:
            if chunk["policy_id"] not in self.policies:
                continue
            if policy_ids is not None and chunk["policy_id"] not in policy_ids:
                continue
            if not shares_audience(chunk["audience"], audiences):
                continue
            if chunk["effective_from"] > today:
                continue
            if (end := _last_effective_day(chunk)) and end < today:
                continue
            passage = _passage(chunk, cosine(embedding, chunk["embedding"]))
            passage.kw_score = keyword_score(
                query,
                f"{chunk['section_label']} {chunk.get('heading_path', '')} {chunk['heading']} {chunk['body']}",
            )
            found.append(passage)
        found.sort(key=lambda passage: (0.65 * max(passage.vec_score, 0.0) + 0.35 * passage.kw_score), reverse=True)
        return found[:limit]

    def clause_hits(self, numbers: list[str], audiences: set[str], today: date, *, policy_ids=None) -> list[Passage]:
        from .clauses import contains_clause

        return self._matching(audiences, today, lambda chunk: any(
            contains_clause(f"{chunk['heading']}\n{chunk['body']}", number) for number in numbers
        ), policy_ids=policy_ids)

    def heading_hits(self, phrases: list[str], audiences: set[str], today: date, *, policy_ids=None) -> list[Passage]:
        needles = []
        for phrase in phrases:
            text = " ".join(re.findall(r"[a-z0-9]+", (phrase or "").lower()))
            if len(text) >= 4:
                needles.append(text)
        return self._matching(audiences, today, lambda chunk: any(
            heading_has_phrase(f"{chunk['heading']}\n{chunk.get('heading_path', '')}", needle) for needle in needles
        ), policy_ids=policy_ids)

    def printed_page_hits(self, number: int, audiences: set[str], today: date, *, policy_ids=None) -> list[Passage]:
        if not isinstance(number, int) or number < 1 or number > 40:
            return []
        needle = f"page {number} of"
        marked = {
            (chunk["policy_id"], chunk.get("page"))
            for chunk in self.chunks
            if chunk["heading"].lower().startswith(needle)
        }
        return self._matching(
            audiences,
            today,
            lambda chunk: (chunk["policy_id"], chunk.get("page")) in marked,
            policy_ids=policy_ids,
        )[:16]

    def _matching(self, audiences, today, test, *, policy_ids=None) -> list[Passage]:
        found = []
        for chunk in self.chunks:
            if chunk["policy_id"] not in self.policies:
                continue
            if policy_ids is not None and chunk["policy_id"] not in policy_ids:
                continue
            if not shares_audience(chunk["audience"], audiences):
                continue
            if chunk["effective_from"] > today:
                continue
            if (end := _last_effective_day(chunk)) and end < today:
                continue
            if test(chunk):
                found.append(_passage(chunk, 0.6))
        return found

    def heading_rows(self, policy_id: str, heading: str, audiences: set[str], today: date) -> list[Passage]:
        return self._matching(
            audiences,
            today,
            lambda chunk: chunk["policy_id"] == policy_id and chunk["heading"] == heading,
        )[:16]

    def media_passages(self, policy_id, name):
        wanted = {name, f"{policy_id}/{name}"}
        return [_passage(chunk, 0) for chunk in self.chunks
                if chunk["policy_id"] == policy_id and wanted.intersection(chunk["image_path"].split("|"))]

    def passages_by_id(self, ids):
        return [_passage(chunk, 0) for chunk in self.chunks if chunk["id"] in ids and chunk["policy_id"] in self.policies]

    def cache_lookup(self, question, access, version):
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=CACHE_TTL_MINUTES)
        return next((row for row in reversed(self.cache)
                     if row.get("access_key") == access and row.get("policy_version") == version
                     and row.get("question") == question and row.get("created_at", cutoff) > cutoff), None)

    def cache_lookup_similar(self, embedding, access, version, question):
        from .config import CACHE_SIMILARITY
        from .embeddings import cosine
        from .rewrite import same_subject

        cutoff = datetime.now(timezone.utc) - timedelta(minutes=CACHE_TTL_MINUTES)
        best = None
        best_score = 0.0
        for row in self.cache:
            vector = row.get("embedding")
            if (row.get("access_key") != access or row.get("policy_version") != version
                    or not vector or row.get("created_at", cutoff) <= cutoff):
                continue
            score = cosine(embedding, vector)
            if score >= CACHE_SIMILARITY and score > best_score and same_subject(question, row.get("question", "")):
                best = row
                best_score = score
        return best

    def cache_store(self, row):
        with self._lock:
            self.cache = [item for item in self.cache if any(
                item.get(field) != row[field] for field in ("access_key", "policy_version", "question")
            )]
            self.cache.append({**row, "created_at": datetime.now(timezone.utc)})
            del self.cache[:-CACHE_MAX_ROWS]

    def record_action(self, email: str, action: str, detail: str) -> None:
        with self._lock:
            self.actions.append(
                {
                    "id": uuid4().hex,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "email": email,
                    "action": action,
                    "detail": detail,
                }
            )

    def list_actions(self, limit: int = 50) -> list[dict]:
        return list(reversed(self.actions))[:limit]

    def add_trace(self, trace: TraceRecord) -> None:
        with self._lock:
            self.traces[trace.id] = trace

    def purge_old_traces(self) -> None:
        cutoff = _three_months_ago(datetime.now(timezone.utc))
        with self._lock:
            kept: dict[str, TraceRecord] = {}
            for trace_id, trace in self.traces.items():
                created = _trace_time(trace.created_at)
                if created is None or created >= cutoff:
                    kept[trace_id] = trace
            self.traces = kept

    def list_traces(self, limit: int = 50) -> list[TraceRecord]:
        self.purge_old_traces()
        rows = sorted(self.traces.values(), key=lambda trace: trace.created_at, reverse=True)
        return rows[:limit]

    def delete_trace(self, trace_id: str) -> bool:
        with self._lock:
            return self.traces.pop(trace_id, None) is not None

    def get_trace(self, trace_id: str) -> TraceRecord | None:
        return self.traces.get(trace_id)


def _last_effective_day(chunk):
    end = chunk["effective_to"]
    cutoff = chunk.get("superseded_on")
    if cutoff:
        before = cutoff - timedelta(days=1)
        return min(end, before) if end else before
    return end


def _passage(chunk: dict, vec_score: float) -> Passage:
    return Passage(
        id=chunk["id"],
        policy_id=chunk["policy_id"],
        title=chunk["title"],
        section_label=chunk["section_label"],
        heading=chunk["heading"],
        body=chunk["body"],
        audience=chunk["audience"],
        effective_from=chunk["effective_from"],
        effective_to=_last_effective_day(chunk),
        vec_score=vec_score,
        heading_path=chunk.get("heading_path", ""),
        content_type=chunk.get("content_type", "section"),
        page=chunk.get("page"),
        image_path=chunk.get("image_path", ""),
        polarity=chunk.get("polarity", ""),
    )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _three_months_ago(moment: datetime) -> datetime:
    month = moment.month - 3
    year = moment.year
    if month <= 0:
        month += 12
        year -= 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)


def _trace_time(value: str) -> datetime | None:
    try:
        created = datetime.fromisoformat(value)
    except ValueError:
        return None
    if created.tzinfo is None:
        return created.replace(tzinfo=timezone.utc)
    return created
