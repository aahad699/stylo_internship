"""Shared request budgets and employee-owned conversations."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from uuid import uuid4
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import Employee

from psycopg.types.json import Jsonb

from .config import CHAT_MAX_MESSAGES, CHAT_RETENTION_DAYS, TEAM_CONCURRENCY

SCHEMA = """
DO $$
BEGIN
  IF to_regclass('public.demo_initialized') IS NOT NULL
     AND to_regclass('public.reference_library_initialized') IS NULL THEN
    ALTER TABLE public.demo_initialized RENAME TO reference_library_initialized;
  END IF;
END;
$$;
ALTER TABLE policies ADD COLUMN IF NOT EXISTS published BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE policies ADD COLUMN IF NOT EXISTS previous_id TEXT;
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'policies' AND column_name = 'superseded_on'
  ) THEN
    ALTER TABLE policies ADD COLUMN superseded_on DATE;
    UPDATE policies previous
    SET published = TRUE, superseded_on = replacement.effective_from
    FROM policies replacement
    WHERE replacement.previous_id = previous.id AND replacement.published
      AND NOT previous.published
      AND replacement.effective_from > (now() AT TIME ZONE 'Asia/Karachi')::date;
  END IF;
END;
$$;
CREATE TABLE IF NOT EXISTS policy_drafts (
  id TEXT PRIMARY KEY,
  prepared JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS policy_jobs (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('prepare', 'publish')),
  status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'completed', 'failed')),
  payload JSONB NOT NULL,
  upload BYTEA NOT NULL DEFAULT ''::bytea,
  actor TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT '',
  attempts SMALLINT NOT NULL DEFAULT 0,
  claim TEXT,
  lease_until TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS policy_jobs_dispatch ON policy_jobs(status, created_at);
CREATE TABLE IF NOT EXISTS employee_activation (
  email TEXT PRIMARY KEY REFERENCES employees(email) ON DELETE CASCADE,
  token_hash TEXT UNIQUE NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS request_budget (
  scope TEXT NOT NULL,
  bucket BIGINT NOT NULL,
  used INTEGER NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (scope, bucket)
);
CREATE INDEX IF NOT EXISTS request_budget_expiry ON request_budget(expires_at);
CREATE TABLE IF NOT EXISTS ask_leases (
  id TEXT PRIMARY KEY,
  expires_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS conversations (
  id TEXT PRIMARY KEY,
  email TEXT NOT NULL REFERENCES employees(email) ON DELETE CASCADE,
  messages JSONB NOT NULL DEFAULT '[]',
  title TEXT,
  pinned BOOLEAN NOT NULL DEFAULT FALSE,
  pinned_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS conversation_owner ON conversations(email, updated_at DESC);
CREATE INDEX IF NOT EXISTS conversation_expiry ON conversations(updated_at);
CREATE TABLE IF NOT EXISTS library_revision (
  singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
  revision BIGINT NOT NULL DEFAULT 0
);
INSERT INTO library_revision(singleton) VALUES (TRUE) ON CONFLICT DO NOTHING;
CREATE OR REPLACE FUNCTION advance_library_revision() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  UPDATE library_revision SET revision = revision + 1 WHERE singleton;
  RETURN NULL;
END;
$$;
CREATE OR REPLACE TRIGGER policy_library_revision AFTER INSERT OR UPDATE OR DELETE ON policies
FOR EACH STATEMENT EXECUTE FUNCTION advance_library_revision();
CREATE INDEX IF NOT EXISTS answer_cache_lookup ON answer_cache(access_key, policy_version, question, created_at DESC);
CREATE INDEX IF NOT EXISTS traces_expiry ON traces(created_at);
CREATE INDEX IF NOT EXISTS sessions_expiry ON sessions(created_at);
"""


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class BudgetExceeded(Exception):
    def __init__(self, retry_after: int) -> None:
        self.retry_after = retry_after


def complete_policy_job(cur, job_claim, detail) -> None:
    key, claim = job_claim
    cur.execute(
        """
        SELECT employee.email FROM policy_jobs job JOIN employees employee ON employee.email = job.actor
        WHERE job.id = %s AND job.claim = %s AND job.status = 'running' AND employee.is_admin
        FOR SHARE OF employee
        """, (key, claim),
    )
    if cur.fetchone() is None:
        raise ValueError("The job is no longer owned by an authorized worker. Ask HR to review and retry.")
    cur.execute(
        """
        UPDATE policy_jobs SET status = 'completed', detail = %s, claim = NULL,
            upload = ''::bytea, updated_at = now()
        WHERE id = %s AND claim = %s AND status = 'running'
        """, (detail, key, claim),
    )
    if cur.rowcount != 1:
        raise ValueError("The job was interrupted and has been picked up again.")


class PostgresOperations:
    def enqueue_policy_job(self, kind, payload, actor, upload=b"") -> str:
        with self._lock, self.conn.cursor() as cur, self.conn.transaction():
            cur.execute("SELECT pg_advisory_xact_lock(490622361)")
            if kind == "publish":
                cur.execute(
                    """SELECT id FROM policy_jobs WHERE kind = 'publish'
                       AND status IN ('queued', 'running') AND payload->>'draft_key' = %s""",
                    (payload["draft_key"],),
                )
                existing = cur.fetchone()
                if existing:
                    return existing[0]
            cur.execute("SELECT count(*) FROM policy_jobs WHERE status IN ('queued', 'running')")
            if cur.fetchone()[0] >= 12:
                raise ValueError("The policy queue is full. Wait for a job to finish.")
            key = uuid4().hex
            cur.execute(
                "INSERT INTO policy_jobs(id, kind, status, payload, actor, upload) VALUES (%s, %s, 'queued', %s, %s, %s)",
                (key, kind, Jsonb(payload), actor, upload),
            )
            return key

    def list_policy_jobs(self) -> list[dict]:
        with self._lock, self.conn.cursor() as cur:
            cur.execute(
                """SELECT id, kind, status, payload->>'title', detail, payload->>'draft_key'
                   FROM policy_jobs ORDER BY (status IN ('queued', 'running')) DESC, updated_at DESC, id LIMIT 30"""
            )
            return [dict(id=key, kind=kind, status=status, title=title, detail=detail, draftId=draft)
                    for key, kind, status, title, detail, draft in cur.fetchall()]

    def claim_policy_job(self) -> dict | None:
        claim = uuid4().hex
        with self._lock, self.conn.cursor() as cur, self.conn.transaction():
            cur.execute(
                """UPDATE policy_jobs SET status = 'failed', detail = 'Processing was interrupted. Retry when ready.',
                          claim = NULL, updated_at = now()
                   WHERE status = 'running' AND lease_until <= now() AND attempts >= 3"""
            )
            cur.execute(
                """
                WITH next_job AS (
                  SELECT id FROM policy_jobs
                  WHERE status = 'queued' OR (status = 'running' AND lease_until <= now())
                  ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED
                )
                UPDATE policy_jobs j SET status = 'running', attempts = j.attempts + 1,
                    claim = %s, lease_until = now() + interval '2 minutes', updated_at = now()
                FROM next_job WHERE j.id = next_job.id
                RETURNING j.id, j.kind, j.payload, j.actor, j.upload
                """, (claim,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            key, kind, payload, actor, upload = row
            return dict(id=key, claim=claim, kind=kind, payload=payload, actor=actor, upload=bytes(upload))

    def renew_policy_job(self, key, claim) -> None:
        with self._lock, self.conn.cursor() as cur:
            cur.execute(
                """UPDATE policy_jobs SET lease_until = now() + interval '2 minutes'
                   WHERE id = %s AND claim = %s AND status = 'running'""",
                (key, claim),
            )

    def fail_policy_job(self, key, claim, detail) -> None:
        with self._lock, self.conn.cursor() as cur:
            cur.execute(
                """UPDATE policy_jobs SET status = 'failed', claim = NULL, detail = %s, updated_at = now()
                   WHERE id = %s AND claim = %s AND status = 'running'""",
                (detail, key, claim),
            )

    def retry_policy_job(self, key, actor) -> bool:
        with self._lock, self.conn.cursor() as cur, self.conn.transaction():
            cur.execute("SELECT pg_advisory_xact_lock(490622361)")
            cur.execute("SELECT count(*) FROM policy_jobs WHERE status IN ('queued', 'running')")
            if cur.fetchone()[0] >= 12:
                raise ValueError("The policy queue is full. Wait for a job to finish.")
            cur.execute(
                """
                UPDATE policy_jobs job SET status = 'queued', attempts = 0, detail = '', actor = %s, updated_at = now()
                WHERE id = %s AND status = 'failed' AND (kind = 'prepare' OR EXISTS (
                  SELECT 1 FROM policy_drafts draft WHERE draft.id = job.payload->>'draft_key'
                )) AND NOT EXISTS (
                  SELECT 1 FROM policy_jobs other
                  WHERE job.kind = 'publish' AND other.kind = job.kind AND other.status IN ('queued', 'running')
                    AND other.payload->>'draft_key' = job.payload->>'draft_key'
                )
                """, (actor, key),
            )
            return cur.rowcount == 1

    def save_draft(self, key: str, prepared: dict, *, job_claim=None) -> None:
        with self._lock, self.conn.cursor() as cur, self.conn.transaction():
            if job_claim:
                complete_policy_job(cur, job_claim, "Ready for review.")
            cur.execute("SELECT pg_advisory_xact_lock(490622359)")
            cur.execute("SELECT count(*) FROM policy_drafts")
            if cur.fetchone()[0] >= 12:
                raise RuntimeError("Policy draft limit reached")
            cur.execute("INSERT INTO policy_drafts(id, prepared) VALUES (%s, %s)", (key, Jsonb(prepared)))

    def get_draft(self, key: str):
        with self._lock, self.conn.cursor() as cur:
            cur.execute("SELECT prepared FROM policy_drafts WHERE id = %s", (key,))
            row = cur.fetchone()
        return row[0] if row else None

    def list_drafts(self):
        with self._lock, self.conn.cursor() as cur:
            cur.execute("SELECT id, prepared FROM policy_drafts ORDER BY created_at DESC")
            return cur.fetchall()

    def discard_draft(self, key: str) -> bool:
        with self._lock, self.conn.cursor() as cur:
            cur.execute("DELETE FROM policy_drafts WHERE id = %s", (key,))
            return cur.rowcount == 1

    def issue_activation(self, email: str, token: str) -> bool:
        with self._lock, self.conn.cursor() as cur, self.conn.transaction():
            cur.execute(
                """
                INSERT INTO employee_activation(email, token_hash, expires_at)
                SELECT email, %s, now() + interval '30 minutes'
                FROM employees WHERE email = %s AND coalesce(password_hash, '') = ''
                ON CONFLICT (email) DO UPDATE SET token_hash = excluded.token_hash,
                    expires_at = excluded.expires_at
                WHERE employee_activation.expires_at < now() + interval '29 minutes'
                """,
                (digest(token), email),
            )
            return cur.rowcount == 1

    def redeem_activation(self, token: str, password_hash: str) -> Employee | None:
        from .models import Employee

        with self._lock, self.conn.cursor() as cur, self.conn.transaction():
            cur.execute(
                "DELETE FROM employee_activation WHERE token_hash = %s AND expires_at > now() RETURNING email",
                (digest(token),),
            )
            row = cur.fetchone()
            if row is None:
                return None
            cur.execute(
                """
                UPDATE employees SET password_hash = %s
                WHERE email = %s AND coalesce(password_hash, '') = ''
                RETURNING email, name, designation, is_admin
                """,
                (password_hash, row[0]),
            )
            row = cur.fetchone()
            return Employee(*row) if row else None

    def consume_budget(self, limits, *, lease=False):
        now = int(datetime.now(timezone.utc).timestamp())
        with self._lock, self.conn.cursor() as cur, self.conn.transaction():
            for scope, limit, seconds in limits:
                bucket = now // seconds
                expires = (bucket + 1) * seconds
                cur.execute(
                    """
                    INSERT INTO request_budget(scope, bucket, used, expires_at)
                    VALUES (%s, %s, 1, to_timestamp(%s))
                    ON CONFLICT (scope, bucket) DO UPDATE SET used = request_budget.used + 1
                    WHERE request_budget.used < %s RETURNING used
                    """,
                    (scope, bucket, expires, limit),
                )
                if cur.fetchone() is None:
                    raise BudgetExceeded(max(1, expires - now))
            if lease:
                cur.execute("SELECT pg_advisory_xact_lock(490622357)")
                cur.execute("DELETE FROM ask_leases WHERE expires_at <= now()")
                cur.execute("SELECT count(*) FROM ask_leases")
                if cur.fetchone()[0] >= TEAM_CONCURRENCY:
                    raise BudgetExceeded(5)
                key = uuid4().hex
                cur.execute("INSERT INTO ask_leases VALUES (%s, now() + interval '2 minutes')", (key,))
                return key
            return None

    def release_lease(self, key: str) -> None:
        with self._lock, self.conn.cursor() as cur:
            cur.execute("DELETE FROM ask_leases WHERE id = %s", (key,))

    def maintain(self) -> list[str]:
        with self._lock, self.conn.cursor() as cur, self.conn.transaction():
            cur.execute(
                """
                DELETE FROM policy_drafts draft WHERE created_at < now() - interval '7 days'
                AND NOT EXISTS (
                  SELECT 1 FROM policy_jobs job WHERE job.kind = 'publish' AND job.status IN ('queued', 'running')
                  AND job.payload->>'draft_key' = draft.id
                )
                RETURNING prepared->>'id'
                """
            )
            staged = [row[0] for row in cur.fetchall() if row[0]]
            cur.execute("DELETE FROM policy_jobs WHERE status IN ('completed', 'failed') AND updated_at < now() - interval '7 days'")
            cur.execute("DELETE FROM answer_cache WHERE created_at <= now() - interval '1 hour'")
            cur.execute("DELETE FROM request_budget WHERE expires_at <= now()")
            cur.execute("DELETE FROM employee_activation WHERE expires_at <= now()")
            cur.execute("DELETE FROM sessions WHERE created_at < now() - interval '12 hours'")
            cur.execute("DELETE FROM conversations WHERE updated_at < now() - make_interval(days => %s)", (CHAT_RETENTION_DAYS,))
            cur.execute("DELETE FROM traces WHERE created_at < now() - interval '3 months'")
        return staged

    def create_conversation(self, email: str) -> str:
        key = uuid4().hex
        with self._lock, self.conn.cursor() as cur, self.conn.transaction():
            cur.execute("SELECT email FROM employees WHERE email = %s FOR UPDATE", (email,))
            cur.execute("INSERT INTO conversations(id, email) VALUES (%s, %s)", (key, email))
            cur.execute(
                """
                DELETE FROM conversations WHERE id IN (
                    SELECT id FROM conversations
                    WHERE email = %s AND NOT pinned
                    ORDER BY updated_at DESC, id
                    OFFSET 20
                )
                """,
                (email,),
            )
        return key

    def list_conversations(self, email: str):
        with self._lock, self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT id,
                       coalesce(nullif(btrim(title), ''), left(coalesce(messages->0->>'text', 'New chat'), 70)),
                       updated_at, pinned
                FROM conversations WHERE email = %s
                  AND updated_at >= now() - make_interval(days => %s)
                  AND (
                    pinned OR id IN (
                      SELECT id FROM conversations
                      WHERE email = %s AND NOT pinned
                        AND updated_at >= now() - make_interval(days => %s)
                      ORDER BY updated_at DESC, id
                      LIMIT 20
                    )
                  )
                ORDER BY pinned DESC, pinned_at DESC NULLS LAST, updated_at DESC, id
                """,
                (email, CHAT_RETENTION_DAYS, email, CHAT_RETENTION_DAYS),
            )
            return [self._chat_summary(key, title, updated, pinned) for key, title, updated, pinned in cur.fetchall()]

    def update_conversation(self, email: str, key: str, *, title: str | None = None, pinned: bool | None = None):
        if title is None and pinned is None:
            return None
        assignments = []
        params: list = []
        if title is not None:
            assignments.append("title = %s")
            params.append(title)
        if pinned is not None:
            assignments.append("pinned = %s")
            params.append(pinned)
            assignments.append("pinned_at = CASE WHEN %s THEN COALESCE(pinned_at, now()) ELSE NULL END")
            params.append(pinned)
        params.extend((key, email, CHAT_RETENTION_DAYS))
        with self._lock, self.conn.cursor() as cur, self.conn.transaction():
            cur.execute(
                f"""
                UPDATE conversations SET {", ".join(assignments)}
                WHERE id = %s AND email = %s
                  AND updated_at >= now() - make_interval(days => %s)
                """,
                params,
            )
            if cur.rowcount != 1:
                return None
            cur.execute(
                """
                SELECT id,
                       coalesce(nullif(btrim(title), ''), left(coalesce(messages->0->>'text', 'New chat'), 70)),
                       updated_at, pinned
                FROM conversations WHERE id = %s AND email = %s
                """,
                (key, email),
            )
            row = cur.fetchone()
        if not row:
            return None
        return self._chat_summary(*row)

    @staticmethod
    def _chat_summary(key, title, updated, pinned):
        shown = (title or "").strip() or "New chat"
        when = updated.isoformat() if hasattr(updated, "isoformat") else str(updated)
        return {"id": key, "title": shown[:70], "updatedAt": when, "pinned": bool(pinned)}

    def conversation_updated_at(self, email: str, key: str) -> str | None:
        with self._lock, self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT updated_at FROM conversations WHERE id = %s AND email = %s
                    AND updated_at >= now() - make_interval(days => %s)
                """,
                (key, email, CHAT_RETENTION_DAYS),
            )
            row = cur.fetchone()
        if not row:
            return None
        updated = row[0]
        return updated.isoformat() if hasattr(updated, "isoformat") else str(updated)

    def question_times(self, email: str) -> list[tuple[str, str]]:
        with self._lock, self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT question, created_at FROM traces
                WHERE email = %s AND created_at >= now() - interval '3 months'
                ORDER BY created_at, id
                """,
                (email,),
            )
            rows = []
            for question, created in cur.fetchall():
                when = created.isoformat() if hasattr(created, "isoformat") else str(created)
                rows.append((question or "", when))
            return rows

    def conversation(self, email: str, key: str):
        with self._lock, self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT messages FROM conversations WHERE id = %s AND email = %s
                    AND updated_at >= now() - make_interval(days => %s)
                """,
                (key, email, CHAT_RETENTION_DAYS),
            )
            row = cur.fetchone()
        return row[0] if row else None

    def append_exchange(self, email: str, key: str, user: dict, answer: dict) -> None:
        with self._lock, self.conn.cursor() as cur, self.conn.transaction():
            cur.execute("SELECT messages FROM conversations WHERE id = %s AND email = %s FOR UPDATE", (key, email))
            row = cur.fetchone()
            if row is not None:
                messages = [*row[0], user, answer][-CHAT_MAX_MESSAGES:]
                cur.execute("UPDATE conversations SET messages = %s, updated_at = now() WHERE id = %s",
                            (Jsonb(messages), key))

    def delete_conversation(self, email: str, key: str) -> bool:
        with self._lock, self.conn.cursor() as cur:
            cur.execute("DELETE FROM conversations WHERE id = %s AND email = %s", (key, email))
            return cur.rowcount == 1
