"""HTTP API for the policy assistant. The screen talks only to these routes."""

from __future__ import annotations

import asyncio
import logging
import secrets
import re
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from threading import Event

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from .activation import accepts_employee_email, allows_local_password_setup, public_origin, send_activation
from .operations import BudgetExceeded, digest
from .access import (
    AUDIENCE_LABELS,
    AUDIENCES,
    ROLE_LABELS,
    allowed_audiences,
    passage_grades,
    visible_passage,
    pack_audiences,
    role_label,
    shares_audience,
)
from .config import (
    ASK_LIMIT,
    CHAT_MAX_MESSAGES,
    CHAT_RETENTION_DAYS,
    ASK_DEADLINE_S,
    ASK_WINDOW_S,
    MAX_USER_CHARS,
    TEAM_ASK_LIMIT,
    TEAM_ASK_WINDOW_S,
    TEAM_DAILY_ASK_LIMIT,
    DATA_ROOT,
    LOGIN_LIMIT,
    LOGIN_WINDOW_S,
    MAX_UPLOAD_BYTES,
    UNAVAILABLE,
)
from .passwords import burn_password_check, hash_password, verify_password
from .factory import build_pipeline
from .ingest import (
    POLICY_DIR,
    find_policy_file,
    folder_filenames,
    remove_policy_files,
)
from .llm import provider_deadline
from .models import Employee, PolicyDraft
from .policy_worker import run_policy_worker
from .pipeline import Pipeline
from .guardrails import contains_named_pay

logger = logging.getLogger("hr_desk")
logging.basicConfig(level=logging.INFO)
_POLICY_ID = re.compile(r"^(?:doc|upload)-[a-f0-9]{16}$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_MEDIA_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")
_TRACE_ID = re.compile(r"^[a-f0-9]{32}$")
_ACTIVATION_TOKEN = re.compile(r"^[A-Za-z0-9_-]{43}$")


class SessionIn(BaseModel):
    email: str = Field(default="", max_length=254)
    password: str = Field(default="", max_length=256)


class SignUpIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(default="", max_length=256)


class ActivationIn(BaseModel):
    token: str = Field(max_length=64)
    password: str = Field(max_length=256)


class AskIn(BaseModel):
    question: str = Field(default="", max_length=MAX_USER_CHARS)
    conversation_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")


class EmployeeIn(BaseModel):
    email: str = ""
    name: str = ""
    designation: str = ""
    is_admin: bool = False


class RoleIn(BaseModel):
    designation: str = ""
    is_admin: bool = False


class PublishIn(BaseModel):
    reviewed: bool = False


class PolicyAccessIn(BaseModel):
    audience: str = ""
    audiences: list[str] = []
    starts_on: str = ""
    ends_on: str = ""


def create_app(pipeline: Pipeline | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if pipeline is None:
            built = build_pipeline()
        else:
            built = pipeline
        app.state.pipeline = built
        app.state.store = built.store
        async def maintain():
            while True:
                try:
                    expired = await asyncio.to_thread(built.store.maintain)
                    for policy_id in expired:
                        await asyncio.to_thread(remove_policy_files, policy_id)
                except Exception as error:
                    logger.error("error_class=%s request_id=- status=500", type(error).__name__)
                await asyncio.sleep(300)

        maintenance = asyncio.create_task(maintain())
        worker = asyncio.create_task(run_policy_worker(built))
        try:
            yield
        finally:
            maintenance.cancel()
            worker.cancel()
            await asyncio.gather(maintenance, worker, return_exceptions=True)

    app = FastAPI(title="Stylo HR desk", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    if pipeline is not None:
        app.state.pipeline = pipeline
        app.state.store = pipeline.store
    register(app)
    return app


def register(app: FastAPI) -> None:
    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        request.state.request_id = secrets.token_hex(16)
        content_length = request.headers.get("content-length")
        if content_length and content_length.isdecimal():
            is_upload = request.headers.get("content-type", "").lower().startswith("multipart/form-data")
            limit = MAX_UPLOAD_BYTES + 1024 * 1024 if is_upload else 1024 * 1024
            if int(content_length) > limit:
                return JSONResponse(status_code=413, content={"message": "This request is too large."})
        origin = request.headers.get("origin")
        expected = public_origin()
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and origin and expected and origin != expected:
            return JSONResponse(status_code=403, content={"message": "Open HR Desk from its company address."})
        try:
            response = await call_next(request)
        except Exception as error:
            _log_request_error(request, error, 500)
            response = JSONResponse(status_code=500, content={"message": UNAVAILABLE, "answer": UNAVAILABLE})
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(HTTPException)
    async def http_error(_request: Request, exc: HTTPException):
        message = exc.detail if isinstance(exc.detail, str) else "Something went wrong. Try again."
        return JSONResponse(status_code=exc.status_code, content={"message": message}, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, _exc: RequestValidationError):
        return JSONResponse(
            status_code=400,
            content={"message": "Check the form and try again."},
        )

    @app.get("/api/health")
    def health(request: Request):
        try:
            request.app.state.store.ping()
        except Exception as exc:
            _log_request_error(request, exc, 503)
            raise HTTPException(503, "The database is not available.") from exc
        return {"ok": True}

    @app.get("/api/bootstrap")
    def bootstrap(request: Request):
        me = _current(request)
        return {"me": _person(me) if me else None, "localPasswordSetup": allows_local_password_setup()}

    @app.post("/api/sign-up", status_code=202)
    def sign_up(request: Request, payload: SignUpIn):
        email = payload.email.strip().lower()
        if not _EMAIL.fullmatch(email):
            raise HTTPException(400, "Enter your work email.")
        store = request.app.state.store
        _budget(store, [("activate:team", 80, 900), (f"activate:{_client_key(request)}", 12, 900)])
        if allows_local_password_setup():
            if not _password_ok(payload.password):
                raise HTTPException(400, "Choose a password of at least 12 characters.")
            employee = store.employee(email) if accepts_employee_email(email) else None
            if employee is None:
                raise HTTPException(400, "This work email is not on the employee list.")
            if not store.set_password(email, hash_password(payload.password)):
                raise HTTPException(409, "This account already has a password. Sign in to continue.")
            return _session_response(store, employee)
        token = secrets.token_urlsafe(32)
        if accepts_employee_email(email) and store.issue_activation(email, token):
            try:
                send_activation(email, token)
            except Exception:
                logger.error("Activation email delivery unavailable")
        return {"message": "If this address is eligible, a setup link will arrive in its mailbox."}

    @app.post("/api/activate")
    def activate(request: Request, payload: ActivationIn):
        if allows_local_password_setup():
            raise HTTPException(404, "Set your password on the sign-up page.")
        store = request.app.state.store
        _budget(store, [("redeem:team", 120, 900), (f"redeem:{_client_key(request)}", 16, 900)])
        if not _ACTIVATION_TOKEN.fullmatch(payload.token):
            raise HTTPException(400, "This setup link is invalid or expired. Request a new link.")
        if not _password_ok(payload.password):
            raise HTTPException(400, "Choose a password of at least 12 characters.")
        employee = store.redeem_activation(payload.token, hash_password(payload.password))
        if not employee:
            raise HTTPException(400, "This setup link is invalid or expired. Request a new link.")
        return _session_response(store, employee)

    @app.post("/api/session")
    def sign_in(request: Request, payload: SessionIn):
        email = payload.email.strip().lower()
        if not _EMAIL.fullmatch(email) or not payload.password:
            raise HTTPException(400, "Enter your email and password.")
        store = request.app.state.store
        _budget(store, [("login:team", 160, LOGIN_WINDOW_S),
                        (f"login:ip:{_client_key(request)}", 24, LOGIN_WINDOW_S),
                        (f"login:account:{digest(email)}", LOGIN_LIMIT, LOGIN_WINDOW_S)])
        employee = store.employee(email) if accepts_employee_email(email) else None
        stored = store.password_hash(email) if employee else None
        if not employee or not stored:
            burn_password_check(payload.password)
            raise HTTPException(400, "The email or password does not match.")
        if not verify_password(payload.password, stored):
            raise HTTPException(400, "The email or password does not match.")
        return _session_response(store, employee)

    @app.post("/api/sign-out")
    def sign_out(request: Request):
        token = request.cookies.get("hr_session", "")
        if token:
            request.app.state.store.delete_session(token)
        response = JSONResponse({"ok": True})
        response.delete_cookie("hr_session", path="/", samesite="lax")
        return response

    @app.get("/api/media/{policy_id}/{name}")
    def media(request: Request, policy_id: str, name: str):
        employee = _require(request)
        if not _POLICY_ID.fullmatch(policy_id) or not _MEDIA_NAME.fullmatch(name) or ".." in name:
            raise HTTPException(404, "That picture is not available.")
        store = request.app.state.store
        policy = next((item for item in store.list_policies() if item.id == policy_id), None)
        today = _today(request)
        if (policy is None or not _effective(policy, today)
                or not shares_audience(policy.audience, set(allowed_audiences(employee.designation)))):
            raise HTTPException(404, "That picture is not available.")
        page_picture = re.fullmatch(r"page-[1-9][0-9]*\.png", name)
        if page_picture:
            if employee.designation != "hr":
                raise HTTPException(404, "That picture is not available.")
        else:
            passages = store.media_passages(policy_id, name)
            if not passages or not all(_can_view(employee, passage, today) for passage in passages):
                raise HTTPException(404, "That picture is not available.")
        root = (DATA_ROOT / "policy_media").resolve()
        folder = (root / policy_id).resolve()
        path = (folder / name).resolve()
        if root not in folder.parents or folder not in path.parents or not path.is_file():
            raise HTTPException(404, "That picture is not available.")
        if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
            raise HTTPException(404, "That picture is not available.")
        return FileResponse(path)

    @app.get("/api/conversations")
    def conversations(request: Request):
        employee = _require(request)
        return {"conversations": request.app.state.store.list_conversations(employee.email),
                "messageLimit": CHAT_MAX_MESSAGES, "retentionDays": CHAT_RETENTION_DAYS,
                "questionLimit": MAX_USER_CHARS}

    @app.post("/api/conversations", status_code=201)
    def new_conversation(request: Request):
        employee = _require(request)
        store = request.app.state.store
        _budget(store, [(f"new-chat:{digest(employee.email)}", 30, 900)])
        return {"id": store.create_conversation(employee.email)}

    @app.get("/api/conversations/{key}")
    def get_conversation(request: Request, key: str):
        employee = _require(request)
        messages, hidden = _conversation(request, employee, key, _conversation_scope(request, employee))
        return {"messages": [{"role": item["role"], "text": item["text"]} for item in messages],
                "hiddenMessages": hidden}

    @app.delete("/api/conversations/{key}")
    def delete_conversation(request: Request, key: str):
        employee = _require(request)
        if not _TRACE_ID.fullmatch(key) or not request.app.state.store.delete_conversation(employee.email, key):
            raise HTTPException(404, "That chat is no longer available.")
        return {"ok": True}

    @app.post("/api/ask")
    async def ask(request: Request, payload: AskIn):
        cancelled = Event()
        task = asyncio.create_task(asyncio.to_thread(_ask_question, request, payload, cancelled))
        try:
            while not task.done():
                await asyncio.wait([task], timeout=0.25)
                if await request.is_disconnected():
                    cancelled.set()
                    break
            return await asyncio.shield(task)
        finally:
            cancelled.set()


    @app.get("/api/policies")
    def policies(request: Request):
        _require_admin(request)
        names = folder_filenames()
        rows = [_policy_row(policy, _today(request), names) for policy in request.app.state.store.list_policies()]
        rows.sort(key=lambda item: (item["title"], item["starts_on"]))
        return {"policies": rows}

    @app.post("/api/policies", status_code=202)
    @app.post("/api/policies/{policy_id}", status_code=202)
    async def prepare_policy(
        request: Request,
        policy_id: str | None = None,
        title: str = Form(""),
        audience: str = Form(""),
        starts_on: str = Form(""),
        ends_on: str = Form(""),
        text: str = Form(""),
        file: UploadFile | None = File(None),
    ):
        admin = _require_admin(request)
        store = request.app.state.store
        if policy_id and (not _POLICY_ID.fullmatch(policy_id) or not store.has_policy(policy_id)):
            raise HTTPException(404, "That policy is no longer in the list.")
        _budget(store, [("prepare:team", 24, 86400), (f"prepare:{digest(admin.email)}", 8, 900)])
        name, roles, start, end, body, filename, upload = await _policy_submission(
            title, audience, starts_on, ends_on, text, file,
        )
        payload = dict(title=name, audience=roles, starts_on=start.isoformat(),
                       ends_on=end.isoformat() if end else None, text=body, filename=filename,
                       replaces=policy_id)
        job_id = await asyncio.to_thread(_enqueue_job, store, "prepare", payload, admin.email, upload)
        return {"jobId": job_id, "message": "Document queued for preparation."}

    @app.get("/api/policy-drafts")
    def drafts(request: Request):
        _require_admin(request)
        return {"drafts": [_review_draft(key, draft) for key, draft in request.app.state.store.list_drafts()]}

    @app.get("/api/policy-drafts/{key}/source")
    def draft_source(request: Request, key: str):
        _require_admin(request)
        record = _draft(request.app.state.store, key)
        source = find_policy_file(record["id"])
        if source is None or not source.resolve().is_relative_to(POLICY_DIR.resolve()):
            raise HTTPException(404, "The source file is unavailable.")
        return FileResponse(source, filename=Path(record["source_name"]).name)

    @app.delete("/api/policy-drafts/{key}")
    def discard_draft(request: Request, key: str):
        _require_admin(request)
        store = request.app.state.store
        draft = _draft(store, key)
        if not store.discard_draft(key):
            raise HTTPException(404, "That draft is no longer available.")
        remove_policy_files(draft["id"])
        return {"message": "Draft discarded."}

    @app.get("/api/policy-jobs")
    def policy_jobs(request: Request):
        _require_admin(request)
        return {"jobs": request.app.state.store.list_policy_jobs()}

    @app.post("/api/policy-jobs/{key}/retry", status_code=202)
    def retry_policy_job(request: Request, key: str):
        admin = _require_admin(request)
        store = request.app.state.store
        _budget(store, [("policy-retry:team", 20, 86400)])
        try:
            if not _TRACE_ID.fullmatch(key) or not store.retry_policy_job(key, admin.email):
                raise HTTPException(409, "This job is no longer available for retry.")
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"jobId": key, "message": "Job queued again."}

    @app.post("/api/policy-drafts/{key}/publish", status_code=202)
    def publish_draft(request: Request, key: str, payload: PublishIn):
        admin = _require_admin(request)
        store = request.app.state.store
        _budget(store, [("publish:team", 12, 86400)])
        record = _draft(store, key)
        if not payload.reviewed:
            raise HTTPException(400, "Review the extraction notes before publishing.")
        job_id = _enqueue_job(store, "publish", dict(draft_key=key, title=record["title"]), admin.email)
        return {"jobId": job_id, "message": "Publication queued."}

    @app.delete("/api/policies/{policy_id}")
    def delete_policy(policy_id: str, request: Request):
        admin = _require_admin(request)
        store = request.app.state.store
        if not _POLICY_ID.fullmatch(policy_id) or not store.delete_policy(policy_id, admin.email, as_of=_today(request)):
            raise HTTPException(404, "That policy is no longer in the list.")
        return {"message": "Removed that policy. People can no longer ask about it."}

    @app.post("/api/policies/{policy_id}/rollback")
    def rollback_policy(policy_id: str, request: Request):
        admin = _require_admin(request)
        try:
            if not _POLICY_ID.fullmatch(policy_id) or not request.app.state.store.rollback_policy(
                policy_id, admin.email, as_of=_today(request),
            ):
                raise HTTPException(404, "There is no earlier revision to restore.")
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"message": "Restored the earlier policy and its original access settings."}

    @app.post("/api/policies/{policy_id}/access")
    def update_policy_access(policy_id: str, request: Request, payload: PolicyAccessIn):
        admin = _require_admin(request)
        store = request.app.state.store
        if not _POLICY_ID.fullmatch(policy_id) or not store.has_policy(policy_id):
            raise HTTPException(404, "That policy is no longer in the list.")
        stored_audience = pack_audiences(payload.audiences or ([payload.audience] if payload.audience else []))
        if not stored_audience:
            raise HTTPException(400, "Choose who can see this policy.")
        try:
            effective_from = date.fromisoformat(payload.starts_on)
        except ValueError as exc:
            raise HTTPException(400, "Choose the date this policy starts.") from exc
        effective_to = _end_date(effective_from, payload.ends_on)
        try:
            if not store.update_policy_access(policy_id, stored_audience, effective_from, effective_to,
                                              actor=admin.email, as_of=_today(request)):
                raise HTTPException(404, "That policy is no longer in the list.")
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        fresh = next(item for item in store.list_policies() if item.id == policy_id)
        return {
            "message": f"Updated {fresh.title}. The passages are unchanged.",
            "policy": _policy_row(fresh, _today(request), folder_filenames()),
        }

    @app.get("/api/employees")
    def employees(request: Request):
        _require_admin(request)
        return {"employees": [_employee_row(person, has_password) for person, has_password in request.app.state.store.list_employees()]}

    @app.post("/api/employees")
    def add_employee(request: Request, payload: EmployeeIn):
        admin = _require_admin(request)
        email = payload.email.strip().lower()
        name = " ".join(payload.name.split())
        designation = payload.designation.strip().lower()
        if not _EMAIL.fullmatch(email) or not accepts_employee_email(email):
            raise HTTPException(400, "Enter a valid email.")
        if not name or len(name) > 80:
            raise HTTPException(400, "Enter this person's name.")
        if designation not in ROLE_LABELS:
            raise HTTPException(400, "Choose a role.")
        store = request.app.state.store
        if not store.create_employee(email, name, designation, payload.is_admin, actor=admin.email):
            raise HTTPException(400, "This email is already on the employee list.")
        employee = store.employee(email)
        return {
            "message": f"Added {email}. They can sign up and choose a password.",
            "employee": _employee_row(employee, False),
        }

    @app.delete("/api/employees/{email}")
    def remove_employee(email: str, request: Request):
        admin = _require_admin(request)
        target = email.strip().lower()
        if not _EMAIL.fullmatch(target):
            raise HTTPException(400, "Enter a valid email.")
        store = request.app.state.store
        people = store.list_employees()
        person = next((item for item, _has_password in people if item.email == target), None)
        if person is None:
            raise HTTPException(404, "That email is not on the list.")
        admins = [item for item, _has_password in people if item.is_admin]
        if person.is_admin and len(admins) <= 1:
            raise HTTPException(400, "Keep at least one admin.")
        if person.email == admin.email:
            raise HTTPException(400, "You cannot remove your own email.")
        if not _change_employee(store.delete_employee, target, actor=admin.email):
            raise HTTPException(404, "That email is not on the list.")
        return {"message": f"Removed {target}. They can no longer sign in."}

    @app.patch("/api/employees/{email}")
    def change_role(email: str, request: Request, payload: RoleIn):
        admin = _require_admin(request)
        target = email.strip().lower()
        designation = payload.designation.strip().lower()
        if not _EMAIL.fullmatch(target):
            raise HTTPException(400, "Enter a valid email.")
        if designation not in ROLE_LABELS:
            raise HTTPException(400, "Choose a role.")
        store = request.app.state.store
        people = store.list_employees()
        person = next((item for item, _has_password in people if item.email == target), None)
        if person is None:
            raise HTTPException(404, "That email is not on the list.")
        admins = [item for item, _has_password in people if item.is_admin]
        if person.is_admin and not payload.is_admin and len(admins) <= 1:
            raise HTTPException(400, "Keep at least one admin.")
        if not _change_employee(store.update_employee, target, designation, payload.is_admin, actor=admin.email):
            raise HTTPException(404, "That email is not on the list.")
        fresh = store.employee(target)
        has_password = next(flag for item, flag in store.list_employees() if item.email == target)
        return {
            "message": f"Saved {fresh.name}.",
            "employee": _employee_row(fresh, has_password),
        }

    @app.get("/api/questions")
    def questions(request: Request):
        _require_admin(request)
        store = request.app.state.store
        return {
            "questions": [_question_row(trace) for trace in store.list_traces()],
            "actions": [_action_row(store, row) for row in store.list_actions()],
        }

    @app.get("/api/questions/{trace_id}/passages")
    def question_passages(trace_id: str, request: Request):
        employee = _require_admin(request)
        store = request.app.state.store
        trace = store.get_trace(trace_id) if _TRACE_ID.fullmatch(trace_id) else None
        if not trace:
            raise HTTPException(404, "Question not found.")
        ids = list(dict.fromkeys(c.passage_id for c in trace.citations if c.passage_id))
        audiences = set(allowed_audiences(employee.designation))
        today = _today(request)
        passages = store.passages_by_id(ids) if ids else []
        visible = {
            p.id: p for p in passages
            if shares_audience(p.audience, audiences) and visible_passage(employee.designation, p)
            and p.effective_from <= today and (p.effective_to is None or p.effective_to >= today)
            and not contains_named_pay(f"{p.title}\n{p.body}")
        }
        return {"passages": [{"title": visible[id].title, "label": visible[id].section_label,
                              "text": visible[id].body} for id in ids if id in visible]}

    @app.delete("/api/questions/{trace_id}")
    def remove_question(trace_id: str, request: Request):
        admin = _require_admin(request)
        if not _TRACE_ID.fullmatch(trace_id):
            raise HTTPException(404, "That question is no longer in the list.")
        store = request.app.state.store
        if not store.delete_trace(trace_id):
            raise HTTPException(404, "That question is no longer in the list.")
        _record(store, admin, "removed a question", "One saved question")
        return {"message": "Removed that question."}

    @app.get("/api/checks")
    def checks(_request: Request):
        _require_admin(_request)
        try:
            from evals.run import run_screen_checks

            return run_screen_checks()
        except Exception as exc:
            _log_request_error(request, exc, 500)
            raise HTTPException(500, UNAVAILABLE) from exc


def _log_request_error(request: Request, error: Exception, status: int) -> None:
    logger.error("error_class=%s request_id=%s status=%d", type(error).__name__, request.state.request_id, status)


def _ask_question(request: Request, payload: AskIn, cancelled: Event):
    employee = _require(request)
    store = request.app.state.store
    version = _conversation_scope(request, employee)
    history, _ = _conversation(request, employee, payload.conversation_id, version) if payload.conversation_id else ([], 0)
    today = _today(request)
    lease = _budget(store, [("ask:team", TEAM_ASK_LIMIT, TEAM_ASK_WINDOW_S),
                            ("ask:daily", TEAM_DAILY_ASK_LIMIT, 86400),
                            (f"ask:{digest(employee.email)}", ASK_LIMIT, ASK_WINDOW_S)], lease=True)
    try:
        with provider_deadline(ASK_DEADLINE_S, cancelled):
            result = request.app.state.pipeline.ask(employee, payload.question, history, request_id=request.state.request_id)
    finally:
        store.release_lease(lease)
    if cancelled.is_set():
        raise HTTPException(499, "You stopped this question.")
    if result.outcome == "error":
        raise HTTPException(503, UNAVAILABLE)
    current = _require(request)
    if _conversation_scope(request, current) != version or _today(request) != today:
        raise HTTPException(409, "Policy access changed while we were answering. Ask again for the current policy.")
    citations = ([{"label": label} for label in dict.fromkeys(item.label for item in result.citations)]
                 if result.outcome == "answered" else [])
    if payload.conversation_id and result.outcome in {"answered", "dont_know", "refused", "clarification"}:
        store.append_exchange(employee.email, payload.conversation_id,
                              {"role": "user", "text": payload.question.strip()},
                              {"role": "assistant", "text": result.answer, "citations": citations, "scope": version,
                               "outcome": result.outcome,
                               "passage_ids": result.passage_ids,
                               "expiresOn": result.valid_until.isoformat() if result.valid_until else None})
    return {"answer": result.answer, "outcome": result.outcome, "citations": []}


def _password_ok(password: str) -> bool:
    return 12 <= len(password) <= 256 and bool(password.strip())


def _limited(payload: bytes) -> bytes:
    if len(payload) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "That file is larger than 64 MB. Choose a smaller file.")
    return payload


def _change_employee(action, *args, actor: str):
    try:
        return action(*args, actor=actor)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def _client_key(request: Request) -> str:
    return digest(request.client.host if request.client else "unknown")


def _budget(store, limits, *, lease=False):
    try:
        return store.consume_budget(limits, lease=lease)
    except BudgetExceeded as exc:
        raise HTTPException(429, "Too many requests. Wait a few minutes and try again.",
                            headers={"Retry-After": str(exc.retry_after)}) from exc


def _today(request: Request) -> date:
    return request.app.state.pipeline.today()


def _effective(record, today: date) -> bool:
    return (record.effective_from <= today and (record.effective_to is None or record.effective_to >= today)
            and (getattr(record, "superseded_on", None) is None or record.superseded_on > today))


def _can_view(employee: Employee, passage, today: date) -> bool:
    return (_effective(passage, today)
            and shares_audience(passage.audience, set(allowed_audiences(employee.designation)))
            and visible_passage(employee.designation, passage))


def _conversation_scope(request: Request, employee: Employee) -> str:
    return f"{employee.designation}:policy-roles-v1:{request.app.state.store.policy_version()}"


def _conversation(request: Request, employee: Employee, key: str, scope: str) -> tuple[list[dict], int]:
    messages = request.app.state.store.conversation(employee.email, key) if _TRACE_ID.fullmatch(key) else None
    if messages is None:
        raise HTTPException(404, "That chat is no longer available.")
    today = _today(request).isoformat()
    visible = []
    for index in range(0, len(messages) - 1, 2):
        question, answer = messages[index:index + 2]
        if answer.get("scope") != scope or (answer.get("expiresOn") or today) < today:
            continue
        visible.append({"role": question["role"], "text": question["text"]})
        visible.append({"role": answer["role"], "text": answer["text"],
                        "citations": answer.get("citations", []), "outcome": answer.get("outcome"),
                        "passage_ids": answer.get("passage_ids", [])})
    return visible, len(messages) - len(visible)


def _current(request: Request) -> Employee | None:
    token = request.cookies.get("hr_session", "")
    return request.app.state.store.employee_for_session(token)


def _require(request: Request) -> Employee:
    employee = _current(request)
    if not employee:
        raise HTTPException(401, "Choose who you are to continue.")
    return employee


def _require_admin(request: Request) -> Employee:
    employee = _require(request)
    if not employee.is_admin:
        raise HTTPException(403, "This page is for an admin.")
    return employee


def _draft(store, key: str) -> dict:
    record = store.get_draft(key) if _TRACE_ID.fullmatch(key) else None
    if record is None:
        raise HTTPException(404, "That draft is no longer available.")
    return record


def _enqueue_job(store, kind, payload, actor, upload=b"") -> str:
    try:
        return store.enqueue_policy_job(kind, payload, actor, upload)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


def _review_draft(key: str, record: dict) -> dict:
    return {"id": key, "title": record["title"], "filename": record["source_name"], "audience": record["audience"],
            "startsOn": record["effective_from"], "endsOn": record["effective_to"],
            "warnings": record["warnings"], "replaces": record["replaces"],
            "hasSourceFile": record["id"].startswith("doc-"),
            "passages": [{"heading": item["heading"], "type": item["content_type"],
                          "page": item["page"], "text": item["body"],
                          "gradeRoles": passage_grades(item["heading"], item["body"], item["content_type"],
                                                       record["title"])} for item in record["chunks"]]}


def _policy_row(policy: PolicyDraft, today: date, filenames: dict[str, str] | None = None) -> dict:
    filename = Path(policy.source_name).name or (filenames or {}).get(policy.id)
    if not filename or filename in {".", ".."}:
        filename = ""
    status = "Active"
    if policy.effective_from > today:
        status = "Scheduled"
    elif policy.superseded_on and policy.superseded_on <= today:
        status = "Replaced"
    elif policy.effective_to and policy.effective_to < today:
        status = "Expired"
    return {
        "id": policy.id,
        "status": status,
        "replacementStartsOn": policy.superseded_on,
        "canRollback": bool(policy.previous_id) and not policy.superseded_on,
        "title": policy.title,
        "audience": policy.audience,
        "who": ", ".join(AUDIENCE_LABELS.get(role, role) for role in policy.audience.split(",") if role),
        "starts_on": policy.effective_from.isoformat(),
        "ends_on": policy.effective_to.isoformat() if policy.effective_to else None,
        "filename": filename,
    }


async def _policy_submission(
    title: str,
    audience: str,
    starts_on: str,
    ends_on: str,
    text: str,
    file: UploadFile | None,
) -> tuple[str, str, date, date | None, str, str, bytes]:
    name = title.strip()
    if len(name) > 200:
        raise HTTPException(400, "Use a policy name of 200 characters or fewer.")
    if not name:
        raise HTTPException(400, "Give this policy a name.")
    audience = pack_audiences([part.strip() for part in audience.split(",") if part.strip()])
    if not audience:
        raise HTTPException(400, "Choose who can see this policy.")
    try:
        effective_from = date.fromisoformat(starts_on)
    except ValueError as exc:
        raise HTTPException(400, "Choose the date this policy starts.") from exc
    effective_to = _end_date(effective_from, ends_on)
    filename = ""
    payload = b""
    if file is not None and (file.filename or ""):
        filename = file.filename or ""
        if Path(filename).suffix.lower() not in {".pdf", ".docx", ".txt", ".md"}:
            raise HTTPException(400, "Choose a PDF, Word (.docx), or text file.")
        payload = _limited(await file.read(MAX_UPLOAD_BYTES + 1))
    body = text.strip()
    if len(body) > 200000:
        raise HTTPException(413, "Use a file for policy text longer than 200,000 characters.")
    if not payload and not body:
        raise HTTPException(400, "Add the policy text or choose a file.")
    return name, audience, effective_from, effective_to, body, filename, payload


def _end_date(starts: date, ends_on: str) -> date | None:
    text = (ends_on or "").strip()
    if not text:
        return None
    try:
        end = date.fromisoformat(text)
    except ValueError as exc:
        raise HTTPException(400, "Choose the date this policy ends.") from exc
    if end < starts:
        raise HTTPException(400, "Choose an end date on or after the start date.")
    return end


def _record(store, employee: Employee, action: str, detail: str) -> None:
    try:
        store.record_action(employee.email, action, detail)
    except Exception as error:
        logger.error("error_class=%s request_id=- status=500", type(error).__name__)


def _action_row(store, row: dict) -> dict:
    person = store.employee(row["email"])
    return {
        "id": row["id"],
        "action": row["action"],
        "detail": row["detail"],
        "who": person.name if person else row["email"],
        "when": row["created_at"],
    }


def _session_response(store, employee: Employee):
    token = store.create_session(employee.email)
    response = JSONResponse({"me": _person(employee)})
    response.set_cookie(
        "hr_session",
        token,
        httponly=True,
        secure=public_origin().startswith("https://"),
        samesite="lax",
        path="/",
        max_age=60 * 60 * 12,
    )
    return response


def _employee_row(employee: Employee, has_password: bool) -> dict:
    return {
        "email": employee.email,
        "name": employee.name,
        "designation": employee.designation,
        "role": role_label(employee.designation),
        "isAdmin": employee.is_admin,
        "hasPassword": has_password,
    }


def _person(employee: Employee) -> dict:
    return {
        "email": employee.email,
        "name": employee.name,
        "designation": employee.designation,
        "role": role_label(employee.designation),
        "isAdmin": employee.is_admin,
    }


def _question_row(trace) -> dict:
    labels = {
        "answered": "Answered",
        "dont_know": "Could not answer",
        "refused": "Not allowed",
        "blocked": "Not allowed",
        "clarification": "Asked for clarification",
        "error": "Could not finish",
    }
    if trace.outcome == "answered" and trace.citations:
        policy = "; ".join(dict.fromkeys(item.label for item in trace.citations))
    elif trace.withheld:
        policy = "Not available for this role"
    elif trace.outcome == "dont_know":
        policy = "No matching policy"
    else:
        policy = "Not answered"
    return {
        "id": trace.id,
        "question": trace.question or "Blocked or empty question",
        "asked_by": f"{trace.name}, {role_label(trace.designation)}",
        "outcome_label": labels.get(trace.outcome, "Could not finish"),
        "policy": policy,
        "when": trace.created_at,
        "steps": trace.steps,
        "has_passages": any(item.passage_id for item in trace.citations),
        "technical": {
            "model": trace.model,
            "tokens_in": trace.tokens_in,
            "tokens_out": trace.tokens_out,
            "latency_ms": trace.latency_ms,
            "cache": trace.cache,
            "rewritten_question": trace.rewritten_question,
            "history_note": trace.history_note,
            "scores": trace.scores,
            "prompt_version": trace.prompt_version,
            "guardrail_reason": trace.guardrail_reason,
        },
    }


app = create_app()
