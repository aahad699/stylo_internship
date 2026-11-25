"""Postgres + pgvector store. One database for policies, saved answers, and traces."""

from __future__ import annotations

import re
import secrets
import threading
from datetime import date, datetime

from psycopg.types.json import Jsonb

from .access import passage_grades, query_role, reference_audience
from .config import SESSION_HOURS, CACHE_MAX_ROWS, CACHE_TTL_MINUTES
from .operations import PostgresOperations, SCHEMA as OPERATIONS_SCHEMA, complete_policy_job, digest
from .models import ChunkDraft, Employee, Passage, PolicyDraft, TraceRecord

_REFERENCE_SOURCES = (
    "Travel Policy - Stylo May-2025.pdf",
    "Medical Takaful Policy 051124.pdf",
    "Provident Fund Loan Policy – Head Office.docx",
    "User Guidelines - Advance Salary (HO - RSC).docx",
)

_SCHEMA = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS employees (
  email TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  designation TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
  token TEXT PRIMARY KEY,
  email TEXT NOT NULL REFERENCES employees(email),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS policies (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  audience TEXT NOT NULL,
  effective_from DATE NOT NULL,
  effective_to DATE,
  body TEXT NOT NULL,
  source_name TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS policy_chunks (
  id TEXT PRIMARY KEY,
  policy_id TEXT NOT NULL REFERENCES policies(id) ON DELETE CASCADE,
  section_label TEXT NOT NULL,
  heading TEXT NOT NULL,
  body TEXT NOT NULL,
  embed_text TEXT NOT NULL,
  embedding vector(1536) NOT NULL,
  search_text tsvector GENERATED ALWAYS AS (
    to_tsvector('english', coalesce(section_label, '') || ' ' || coalesce(body, ''))
  ) STORED,
  audience TEXT NOT NULL,
  effective_from DATE NOT NULL,
  effective_to DATE
);

CREATE TABLE IF NOT EXISTS answer_cache (
  id TEXT PRIMARY KEY,
  access_key TEXT NOT NULL,
  policy_version TEXT NOT NULL,
  question TEXT NOT NULL,
  answer TEXT NOT NULL,
  citations JSONB NOT NULL,
  outcome TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS traces (
  id TEXT PRIMARY KEY,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  email TEXT NOT NULL,
  question TEXT NOT NULL,
  outcome TEXT NOT NULL,
  payload JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS admin_actions (
  id TEXT PRIMARY KEY,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  email TEXT NOT NULL,
  action TEXT NOT NULL,
  detail TEXT NOT NULL
);
"""


def _vector(values: list[float]):
    from pgvector import Vector

    # A bare Python list is sent as a float array, and <=> has no match for that.
    return Vector(values)


class PostgresStore(PostgresOperations):
    def __init__(self, dsn: str, *, migrate: bool = False) -> None:
        self._lock = threading.Lock()
        self._dsn = dsn
        self._conn = self._connect(bootstrap=migrate)
        if migrate:
            self._migrate()

    def _connect(self, *, bootstrap=False):
        import psycopg
        from pgvector.psycopg import register_vector

        try:
            connection = psycopg.connect(self._dsn, connect_timeout=5, autocommit=True)
        except psycopg.Error as exc:
            raise RuntimeError("Could not connect to PostgreSQL.") from exc
        try:
            with connection.cursor() as cur:
                cur.execute("SET statement_timeout = '8s'")
                cur.execute("SET lock_timeout = '4s'")
                if bootstrap:
                    cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
            register_vector(connection)
        except Exception:
            connection.close()
            raise
        return connection

    @property
    def conn(self):
        if self._conn.closed or self._conn.broken:
            self._conn = self._connect()
        return self._conn

    def _migrate(self) -> None:
        with self._lock, self.conn.cursor() as cur, self.conn.transaction():
            cur.execute(_SCHEMA)
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS policy_chunks_embedding_idx
                ON policy_chunks USING hnsw (embedding vector_cosine_ops)
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS policy_chunks_search_idx
                ON policy_chunks USING gin (search_text)
                """
            )
            cur.execute("ALTER TABLE employees ADD COLUMN IF NOT EXISTS password_hash TEXT")
            cur.execute(
                "ALTER TABLE employees ADD COLUMN IF NOT EXISTS is_admin BOOLEAN NOT NULL DEFAULT FALSE"
            )
            cur.execute(
                "ALTER TABLE policy_chunks ADD COLUMN IF NOT EXISTS heading_path TEXT NOT NULL DEFAULT ''"
            )
            cur.execute(
                "ALTER TABLE policy_chunks ADD COLUMN IF NOT EXISTS content_type TEXT NOT NULL DEFAULT 'section'"
            )
            cur.execute("ALTER TABLE policy_chunks ADD COLUMN IF NOT EXISTS page INTEGER")
            cur.execute(
                "ALTER TABLE policy_chunks ADD COLUMN IF NOT EXISTS image_path TEXT NOT NULL DEFAULT ''"
            )
            cur.execute(
                "ALTER TABLE policy_chunks ADD COLUMN IF NOT EXISTS polarity TEXT NOT NULL DEFAULT ''"
            )
            cur.execute(
                "ALTER TABLE policies ADD COLUMN IF NOT EXISTS source_name TEXT NOT NULL DEFAULT ''"
            )
            cur.execute(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'policies' AND column_name = 'sample'
                """
            )
            if cur.fetchone():
                cur.execute("DELETE FROM policies WHERE sample = TRUE OR id LIKE 'sample-%%'")
                cur.execute("DELETE FROM answer_cache")
                cur.execute("ALTER TABLE policies DROP COLUMN sample")
            cur.execute(OPERATIONS_SCHEMA)
            cur.execute("ALTER TABLE policy_chunks ADD COLUMN IF NOT EXISTS allowed_roles TEXT[]")
            cur.execute("""SELECT c.id, c.heading, c.body, c.content_type, p.title, c.allowed_roles
                           FROM policy_chunks c JOIN policies p ON p.id = c.policy_id""")
            missing = cur.fetchall()
            for chunk_id, heading, body, content_type, title, previous in missing:
                roles = passage_grades(heading, body, content_type, title)
                if roles != previous:
                    cur.execute("UPDATE policy_chunks SET allowed_roles = %s WHERE id = %s", (roles, chunk_id))
            if any(row[-1] is None for row in missing):
                cur.execute(
                    """
                    UPDATE policies SET audience = 'employee,manager,hr,executive'
                    WHERE audience = 'everyone' AND lower(title) ~ '(travel policy|medical|provident|advance salary)'
                    RETURNING id
                    """
                )
                changed_ids = [row[0] for row in cur.fetchall()]
                cur.execute("UPDATE policy_chunks SET audience = 'employee,manager,hr,executive' WHERE policy_id = ANY(%s)",
                            (changed_ids,))
                cur.execute("DELETE FROM answer_cache")
            # Keep the original library on the audiences its policy text names. An audience
            # HR chose is in the audit log, and that choice stays.
            cur.execute("""SELECT p.id, p.title, p.audience FROM policies p
                           WHERE p.source_name = ANY(%s) AND p.previous_id IS NULL
                             AND NOT EXISTS (SELECT 1 FROM admin_actions a
                                             WHERE a.action = 'changed policy access'
                                               AND left(a.detail, length(p.title) + 1) = p.title || ':')""",
                        (list(_REFERENCE_SOURCES),))
            converged = False
            for policy_id, title, current in cur.fetchall():
                audience = reference_audience(title)
                if audience != current:
                    cur.execute("UPDATE policies SET audience = %s WHERE id = %s", (audience, policy_id))
                    cur.execute("UPDATE policy_chunks SET audience = %s WHERE policy_id = %s", (audience, policy_id))
                    converged = True
            if converged:
                cur.execute("DELETE FROM answer_cache")

    def ping(self) -> None:
        with self._lock, self.conn.cursor() as cur:
            cur.execute("SELECT revision FROM library_revision WHERE singleton")
            if cur.fetchone() is None:
                raise RuntimeError("The database has not been initialized.")

    def employee(self, email: str) -> Employee | None:
        with self._lock, self.conn.cursor() as cur:
            cur.execute(
                "SELECT email, name, designation, is_admin FROM employees WHERE email = %s",
                (email,),
            )
            row = cur.fetchone()
        if not row:
            return None
        return Employee(*row)

    def password_hash(self, email: str) -> str | None:
        with self._lock, self.conn.cursor() as cur:
            cur.execute("SELECT password_hash FROM employees WHERE email = %s", (email,))
            row = cur.fetchone()
        if not row:
            return None
        return row[0] or None

    def list_employees(self) -> list[tuple[Employee, bool]]:
        with self._lock, self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT email, name, designation, is_admin,
                       password_hash IS NOT NULL AND password_hash <> ''
                FROM employees
                ORDER BY name, email
                """
            )
            return [
                (Employee(row[0], row[1], row[2], bool(row[3])), bool(row[4]))
                for row in cur.fetchall()
            ]

    def create_employee(self, email: str, name: str, designation: str, is_admin: bool = False, *, actor: str | None = None) -> bool:
        with self._lock, self.conn.cursor() as cur, self.conn.transaction():
            cur.execute(
                """
                INSERT INTO employees (email, name, designation, is_admin)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (email) DO NOTHING
                """,
                (email, name, designation, is_admin),
            )
            changed = cur.rowcount == 1
            if changed and actor:
                _audit(cur, actor, "added an email", email)
            return changed

    def delete_employee(self, email: str, *, actor=None) -> bool:
        with self._lock, self.conn.cursor() as cur, self.conn.transaction():
            if actor:
                _assert_admin_change(cur, actor, email, False)
            cur.execute("DELETE FROM sessions WHERE email = %s", (email,))
            cur.execute("DELETE FROM employees WHERE email = %s", (email,))
            changed = cur.rowcount == 1
            if changed and actor:
                _audit(cur, actor, "removed an email", email)
            return changed

    def update_employee(self, email: str, designation: str, is_admin: bool, *, actor=None) -> bool:
        with self._lock, self.conn.cursor() as cur, self.conn.transaction():
            if actor:
                _assert_admin_change(cur, actor, email, is_admin)
            cur.execute(
                """
                UPDATE employees
                SET designation = %s, is_admin = %s
                WHERE email = %s
                """,
                (designation, is_admin, email),
            )
            changed = cur.rowcount == 1
            if changed and actor:
                _audit(cur, actor, "changed employee access", f"{email}: {designation}, admin={is_admin}")
            return changed

    def set_password(self, email: str, password_hash: str) -> bool:
        with self._lock, self.conn.cursor() as cur, self.conn.transaction():
            cur.execute(
                """
                UPDATE employees
                SET password_hash = %s
                WHERE email = %s AND (password_hash IS NULL OR password_hash = '')
                """,
                (password_hash, email),
            )
            return cur.rowcount == 1

    def delete_session(self, token: str) -> None:
        with self._lock, self.conn.cursor() as cur, self.conn.transaction():
            cur.execute("DELETE FROM sessions WHERE token = %s", (digest(token),))

    def create_session(self, email: str) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock, self.conn.cursor() as cur, self.conn.transaction():
            cur.execute(
                "INSERT INTO sessions (token, email) VALUES (%s, %s)",
                (digest(token), email),
            )
        return token

    def employee_for_session(self, token: str) -> Employee | None:
        if not token:
            return None
        with self._lock, self.conn.cursor() as cur, self.conn.transaction():
            cur.execute(
                """
                DELETE FROM sessions
                WHERE token = %s AND created_at < now() - make_interval(hours => %s)
                """,
                (digest(token), SESSION_HOURS),
            )
            cur.execute(
                """
                SELECT e.email, e.name, e.designation, e.is_admin
                FROM sessions s
                JOIN employees e ON e.email = s.email
                WHERE s.token = %s
                """,
                (digest(token),),
            )
            row = cur.fetchone()
        if not row:
            return None
        return Employee(*row)

    def update_policy_access(
        self,
        policy_id: str,
        audience: str,
        effective_from: date,
        effective_to: date | None,
        *, actor: str | None = None, as_of: date | None = None,
    ) -> bool:
        today = as_of or date.today()
        with self._lock, self.conn.cursor() as cur, self.conn.transaction():
            cur.execute(
                """
                SELECT effective_from, previous_id, superseded_on
                FROM policies WHERE id = %s AND published FOR UPDATE
                """, (policy_id,),
            )
            previous_state = cur.fetchone()
            if previous_state is None:
                return False
            starts, predecessor, cutoff = previous_state
            if cutoff and cutoff <= today:
                raise ValueError("This revision has already been replaced.")
            if cutoff and (effective_from >= cutoff or (effective_to and effective_to.toordinal() < cutoff.toordinal() - 1)):
                raise ValueError("Keep this revision active until its scheduled replacement starts.")
            if predecessor and starts != effective_from:
                if starts <= today or effective_from <= today:
                    raise ValueError("Keep the replacement start date after today when rescheduling.")
                cur.execute(
                    "SELECT effective_from, effective_to FROM policies WHERE id = %s AND published FOR UPDATE",
                    (predecessor,),
                )
                prior = cur.fetchone()
                if prior:
                    _check_replacement_date(prior[0], prior[1], effective_from)
                    cur.execute("UPDATE policies SET superseded_on = %s WHERE id = %s", (effective_from, predecessor))
            cur.execute(
                """
                UPDATE policies
                SET audience = %s, effective_from = %s, effective_to = %s
                WHERE id = %s AND published RETURNING title
                """,
                (audience, effective_from, effective_to, policy_id),
            )
            row = cur.fetchone()
            if row is None:
                return False
            cur.execute(
                """
                UPDATE policy_chunks
                SET audience = %s, effective_from = %s, effective_to = %s
                WHERE policy_id = %s
                """,
                (audience, effective_from, effective_to, policy_id),
            )
            cur.execute("DELETE FROM answer_cache")
            if actor:
                _audit(cur, actor, "changed policy access", f"{row[0]}: {audience}, {effective_from} to {effective_to or 'open'}")
            return True

    def has_policy(self, policy_id: str) -> bool:
        with self._lock, self.conn.cursor() as cur:
            cur.execute("SELECT 1 FROM policies WHERE id = %s AND published", (policy_id,))
            return cur.fetchone() is not None

    def delete_policy(self, policy_id: str, actor: str | None = None, *, as_of: date | None = None) -> bool:
        today = as_of or date.today()
        with self._lock, self.conn.cursor() as cur, self.conn.transaction():
            cur.execute(
                """
                UPDATE policies SET published = FALSE
                WHERE id = %s AND published AND superseded_on IS NULL
                RETURNING title, previous_id, effective_from
                """, (policy_id,),
            )
            row = cur.fetchone()
            if row:
                title, predecessor, starts = row
                if predecessor and starts > today:
                    cur.execute("UPDATE policies SET superseded_on = NULL WHERE id = %s AND published", (predecessor,))
                cur.execute("DELETE FROM answer_cache")
                if actor:
                    _audit(cur, actor, "removed a policy from publication", title)
            return row is not None

    def rollback_policy(self, policy_id: str, actor: str, *, as_of: date | None = None) -> bool:
        today = as_of or date.today()
        with self._lock, self.conn.cursor() as cur, self.conn.transaction():
            cur.execute(
                """
                SELECT previous_id, title FROM policies
                WHERE id = %s AND published AND superseded_on IS NULL FOR UPDATE
                """, (policy_id,),
            )
            row = cur.fetchone()
            if row is None or not row[0]:
                return False
            cur.execute(
                """
                UPDATE policies SET published = TRUE, superseded_on = NULL
                WHERE id = %s AND effective_from <= %s AND (effective_to IS NULL OR effective_to >= %s)
                """, (row[0], today, today),
            )
            if cur.rowcount != 1:
                raise ValueError("The earlier revision is outside its effective dates. Update or publish a usable revision.")
            cur.execute("UPDATE policies SET published = FALSE WHERE id = %s", (policy_id,))
            cur.execute("DELETE FROM answer_cache")
            _audit(cur, actor, "restored a policy revision", row[1])
            return True

    def insert_policy(
        self,
        policy: PolicyDraft,
        chunks: list[ChunkDraft],
        vectors: list[list[float]],
        *,
        replaces: str | None = None,
        draft_key: str | None = None,
        actor: str | None = None,
        as_of: date | None = None,
        job_claim: tuple[str, str] | None = None,
    ) -> None:
        if len(chunks) != len(vectors):
            raise RuntimeError("Each policy section needs an embedding.")
        for vector in vectors:
            if len(vector) != 1536:
                raise RuntimeError("Embeddings must have 1536 numbers.")
        with self._lock, self.conn.cursor() as cur, self.conn.transaction():
            if draft_key:
                cur.execute("DELETE FROM policy_drafts WHERE id = %s RETURNING id", (draft_key,))
                if cur.fetchone() is None:
                    raise ValueError("The draft is no longer available.")
            if job_claim:
                detail = f"Scheduled for {policy.effective_from.isoformat()}." if policy.effective_from > (as_of or date.today()) else "Published."
                complete_policy_job(cur, job_claim, detail)
            if replaces:
                today = as_of or date.today()
                cur.execute(
                    """
                    SELECT effective_from, effective_to FROM policies
                    WHERE id = %s AND published AND superseded_on IS NULL FOR UPDATE
                    """, (replaces,),
                )
                previous = cur.fetchone()
                if previous is None:
                    raise ValueError("This policy has a replacement already. Review the published library.")
                if previous[0] > today:
                    raise ValueError("Reschedule or cancel the existing revision before replacing it.")
                if policy.effective_from > today:
                    _check_replacement_date(previous[0], previous[1], policy.effective_from)
                    cur.execute("UPDATE policies SET superseded_on = %s WHERE id = %s", (policy.effective_from, replaces))
                else:
                    cur.execute("UPDATE policies SET published = FALSE WHERE id = %s", (replaces,))
            cur.execute(
                """
                INSERT INTO policies
                  (id, title, audience, effective_from, effective_to, body, source_name, previous_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    policy.id,
                    policy.title,
                    policy.audience,
                    policy.effective_from,
                    policy.effective_to,
                    policy.body,
                    policy.source_name,
                    replaces,
                ),
            )
            for index, (chunk, vector) in enumerate(zip(chunks, vectors)):
                cur.execute(
                    """
                    INSERT INTO policy_chunks
                      (id, policy_id, section_label, heading, body, embed_text, embedding,
                       audience, effective_from, effective_to,
                       heading_path, content_type, page, image_path, polarity, allowed_roles)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        f"{policy.id}:{index}",
                        policy.id,
                        chunk.section_label,
                        chunk.heading,
                        chunk.body,
                        chunk.embed_text,
                        _vector(vector),
                        policy.audience,
                        policy.effective_from,
                        policy.effective_to,
                        chunk.heading_path,
                        chunk.content_type,
                        chunk.page,
                        chunk.image_path,
                        chunk.polarity,
                        passage_grades(chunk.heading, chunk.body, chunk.content_type, policy.title),
                    ),
                )
            cur.execute("DELETE FROM answer_cache")
            if actor:
                _audit(cur, actor, "published a replacement" if replaces else "published a policy", policy.title)

    def list_policies(self) -> list[PolicyDraft]:
        with self._lock, self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, title, audience, effective_from, effective_to, body, source_name, previous_id, superseded_on
                FROM policies WHERE published
                ORDER BY title, effective_from
                """
            )
            return [
                PolicyDraft(
                    id=row[0],
                    title=row[1],
                    audience=row[2],
                    effective_from=row[3],
                    effective_to=row[4],
                    body=row[5],
                    source_name=row[6] or "",
                    previous_id=row[7],
                    superseded_on=row[8],
                )
                for row in cur.fetchall()
            ]

    def policy_version(self) -> str:
        with self._lock, self.conn.cursor() as cur:
            cur.execute("SELECT revision FROM library_revision WHERE singleton")
            return str(cur.fetchone()[0])

    def media_passages(self, policy_id: str, name: str) -> list[Passage]:
        with self._lock, self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.id, c.policy_id, p.title, c.section_label, c.heading, c.body,
                       c.audience, c.effective_from, LEAST(c.effective_to, p.superseded_on - 1), 0,
                       c.heading_path, c.content_type, c.page, c.image_path, c.polarity, c.allowed_roles
                FROM policy_chunks c JOIN policies p ON p.id = c.policy_id AND p.published
                WHERE c.policy_id = %s AND (string_to_array(c.image_path, '|') && %s::text[])
                """,
                (policy_id, [name, f"{policy_id}/{name}"]),
            )
            return [_row_passage(row) for row in cur.fetchall()]

    def passages_by_id(self, ids: list[str]) -> list[Passage]:
        with self._lock, self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.id, c.policy_id, p.title, c.section_label, c.heading, c.body,
                       c.audience, c.effective_from, LEAST(c.effective_to, p.superseded_on - 1), 0,
                       c.heading_path, c.content_type, c.page, c.image_path, c.polarity, c.allowed_roles
                FROM policy_chunks c JOIN policies p ON p.id = c.policy_id AND p.published WHERE c.id = ANY(%s)
                """,
                (ids,),
            )
            return [_row_passage(row) for row in cur.fetchall()]

    def candidates(
        self,
        embedding: list[float],
        query: str,
        audiences: set[str],
        today: date,
        limit: int,
        *, policy_ids: list[str] | None = None,
    ) -> list[Passage]:
        audience_list = list(audiences)
        vector = _vector(embedding)
        with self._lock, self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.id, c.policy_id, p.title, c.section_label, c.heading, c.body,
                       c.audience, c.effective_from, LEAST(c.effective_to, p.superseded_on - 1),
                       1 - (c.embedding <=> %s) AS vec_score,
                       c.heading_path, c.content_type, c.page, c.image_path, c.polarity, c.allowed_roles
                FROM policy_chunks c
                JOIN policies p ON p.id = c.policy_id AND p.published
                WHERE string_to_array(c.audience, ',') && %s::text[] AND %s = ANY(c.allowed_roles)
                  AND (%s::text[] IS NULL OR c.policy_id = ANY(%s::text[]))
                  AND c.effective_from <= %s
                  AND (LEAST(c.effective_to, p.superseded_on - 1) IS NULL OR LEAST(c.effective_to, p.superseded_on - 1) >= %s)
                ORDER BY c.embedding <=> %s
                LIMIT %s
                """,
                (vector, audience_list, query_role(audiences), policy_ids, policy_ids, today, today, vector, limit),
            )
            rows = {row[0]: row for row in cur.fetchall()}
            if query.strip():
                cur.execute(
                    """
                    SELECT c.id, c.policy_id, p.title, c.section_label, c.heading, c.body,
                           c.audience, c.effective_from, LEAST(c.effective_to, p.superseded_on - 1),
                           1 - (c.embedding <=> %s) AS vec_score,
                           c.heading_path, c.content_type, c.page, c.image_path, c.polarity, c.allowed_roles
                    FROM policy_chunks c
                    JOIN policies p ON p.id = c.policy_id AND p.published
                    CROSS JOIN (SELECT replace(plainto_tsquery('english', %s)::text, ' & ', ' | ')::tsquery AS terms) q
                    WHERE string_to_array(c.audience, ',') && %s::text[] AND %s = ANY(c.allowed_roles)
                      AND c.effective_from <= %s
                      AND (LEAST(c.effective_to, p.superseded_on - 1) IS NULL OR LEAST(c.effective_to, p.superseded_on - 1) >= %s)
                      AND c.search_text @@ q.terms
                      AND (%s::text[] IS NULL OR c.policy_id = ANY(%s::text[]))
                    ORDER BY ts_rank(c.search_text, q.terms) DESC, c.id
                    LIMIT %s
                    """,
                    (vector, query, audience_list, query_role(audiences), today, today, policy_ids, policy_ids, limit),
                )
                for row in cur.fetchall():
                    rows[row[0]] = row
        return [_row_passage(row) for row in rows.values()]

    def clause_hits(self, numbers: list[str], audiences: set[str], today: date, *, policy_ids=None) -> list[Passage]:
        from .clauses import clause_regex

        if not numbers:
            return []
        checks = []
        params: list = [list(audiences), query_role(audiences), today, today]
        for number in numbers:
            pattern = clause_regex(number)
            checks.append("(c.heading ~ %s OR c.body ~ %s OR c.heading_path ~ %s)")
            params.extend([pattern, pattern, pattern])
        params.extend([policy_ids, policy_ids, 12])
        with self._lock, self.conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT c.id, c.policy_id, p.title, c.section_label, c.heading, c.body,
                       c.audience, c.effective_from, LEAST(c.effective_to, p.superseded_on - 1),
                       0.6 AS vec_score,
                       c.heading_path, c.content_type, c.page, c.image_path, c.polarity, c.allowed_roles
                FROM policy_chunks c
                JOIN policies p ON p.id = c.policy_id AND p.published
                WHERE string_to_array(c.audience, ',') && %s::text[] AND %s = ANY(c.allowed_roles)
                  AND c.effective_from <= %s
                  AND (LEAST(c.effective_to, p.superseded_on - 1) IS NULL OR LEAST(c.effective_to, p.superseded_on - 1) >= %s)
                  AND ({" OR ".join(checks)})
                  AND (%s::text[] IS NULL OR c.policy_id = ANY(%s::text[]))
                LIMIT %s
                """,
                params,
            )
            return [_row_passage(row) for row in cur.fetchall()]

    def heading_hits(self, phrases: list[str], audiences: set[str], today: date, *, policy_ids=None) -> list[Passage]:
        from .clauses import heading_sql_pattern

        clean = []
        for phrase in phrases:
            pattern = heading_sql_pattern(phrase)
            if pattern and pattern not in clean:
                clean.append(pattern)
        if not clean:
            return []
        checks = []
        params: list = [list(audiences), query_role(audiences), today, today]
        for phrase in clean[:6]:
            checks.append("(lower(c.heading) ~ %s OR lower(c.heading_path) ~ %s)")
            params.extend([phrase, phrase])
        params.extend([policy_ids, policy_ids, 40])
        with self._lock, self.conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT c.id, c.policy_id, p.title, c.section_label, c.heading, c.body,
                       c.audience, c.effective_from, LEAST(c.effective_to, p.superseded_on - 1),
                       0.6 AS vec_score,
                       c.heading_path, c.content_type, c.page, c.image_path, c.polarity, c.allowed_roles
                FROM policy_chunks c
                JOIN policies p ON p.id = c.policy_id AND p.published
                WHERE string_to_array(c.audience, ',') && %s::text[] AND %s = ANY(c.allowed_roles)
                  AND c.effective_from <= %s
                  AND (LEAST(c.effective_to, p.superseded_on - 1) IS NULL OR LEAST(c.effective_to, p.superseded_on - 1) >= %s)
                  AND ({" OR ".join(checks)})
                  AND (%s::text[] IS NULL OR c.policy_id = ANY(%s::text[]))
                LIMIT %s
                """,
                params,
            )
            return [_row_passage(row) for row in cur.fetchall()]

    def printed_page_hits(self, number: int, audiences: set[str], today: date, *, policy_ids=None) -> list[Passage]:
        if not isinstance(number, int) or number < 1 or number > 40:
            return []
        needle = f"page {number} of %"
        with self._lock, self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.id, c.policy_id, p.title, c.section_label, c.heading, c.body,
                       c.audience, c.effective_from, LEAST(c.effective_to, p.superseded_on - 1),
                       0.6 AS vec_score,
                       c.heading_path, c.content_type, c.page, c.image_path, c.polarity, c.allowed_roles
                FROM policy_chunks c
                JOIN policies p ON p.id = c.policy_id AND p.published
                WHERE string_to_array(c.audience, ',') && %s::text[] AND %s = ANY(c.allowed_roles)
                  AND c.effective_from <= %s
                  AND (LEAST(c.effective_to, p.superseded_on - 1) IS NULL OR LEAST(c.effective_to, p.superseded_on - 1) >= %s)
                  AND (c.policy_id, c.page) IN (
                    SELECT marked.policy_id, marked.page
                    FROM policy_chunks marked
                    WHERE lower(marked.heading) LIKE %s
                  )
                  AND (%s::text[] IS NULL OR c.policy_id = ANY(%s::text[]))
                LIMIT 16
                """,
                (list(audiences), query_role(audiences), today, today, needle, policy_ids, policy_ids),
            )
            return [_row_passage(row) for row in cur.fetchall()]

    def heading_rows(self, policy_id: str, heading: str, audiences: set[str], today: date) -> list[Passage]:
        with self._lock, self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.id, c.policy_id, p.title, c.section_label, c.heading, c.body,
                       c.audience, c.effective_from, LEAST(c.effective_to, p.superseded_on - 1),
                       0.6 AS vec_score,
                       c.heading_path, c.content_type, c.page, c.image_path, c.polarity, c.allowed_roles
                FROM policy_chunks c
                JOIN policies p ON p.id = c.policy_id AND p.published
                WHERE c.policy_id = %s
                  AND c.heading = %s
                  AND string_to_array(c.audience, ',') && %s::text[] AND %s = ANY(c.allowed_roles)
                  AND c.effective_from <= %s
                  AND (LEAST(c.effective_to, p.superseded_on - 1) IS NULL OR LEAST(c.effective_to, p.superseded_on - 1) >= %s)
                ORDER BY c.id
                LIMIT 16
                """,
                (policy_id, heading, list(audiences), query_role(audiences), today, today),
            )
            return [_row_passage(row) for row in cur.fetchall()]

    def cache_lookup(self, question: str, access: str, version: str) -> dict | None:
        with self._lock, self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT answer, citations, outcome FROM answer_cache
                WHERE access_key = %s AND policy_version = %s AND question = %s
                  AND created_at > now() - make_interval(mins => %s)
                ORDER BY created_at DESC LIMIT 1
                """,
                (access, version, question, CACHE_TTL_MINUTES),
            )
            row = cur.fetchone()
        return {"answer": row[0], "citations": row[1], "outcome": row[2]} if row else None

    def cache_store(self, row: dict) -> None:
        with self._lock, self.conn.cursor() as cur, self.conn.transaction():
            cur.execute("SELECT pg_advisory_xact_lock(490622358)")
            cur.execute(
                """
                DELETE FROM answer_cache WHERE created_at <= now() - make_interval(mins => %s)
                   OR (access_key = %s AND policy_version = %s AND question = %s)
                """,
                (CACHE_TTL_MINUTES, row["access_key"], row["policy_version"], row["question"]),
            )
            cur.execute(
                """
                INSERT INTO answer_cache
                  (id, access_key, policy_version, question, answer, citations, outcome)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (row["id"], row["access_key"], row["policy_version"],
                 row["question"], row["answer"], Jsonb(row["citations"]), row["outcome"]),
            )
            cur.execute(
                "DELETE FROM answer_cache WHERE id IN (SELECT id FROM answer_cache ORDER BY created_at DESC, id OFFSET %s)",
                (CACHE_MAX_ROWS,),
            )

    def add_trace(self, trace: TraceRecord) -> None:
        payload = _trace_payload(trace)
        with self._lock, self.conn.cursor() as cur, self.conn.transaction():
            cur.execute(
                """
                INSERT INTO traces (id, created_at, email, question, outcome, payload)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    trace.id,
                    trace.created_at,
                    trace.email,
                    trace.question,
                    trace.outcome,
                    Jsonb(payload),
                ),
            )

    def record_action(self, email: str, action: str, detail: str) -> None:
        with self._lock, self.conn.cursor() as cur, self.conn.transaction():
            cur.execute(
                """
                INSERT INTO admin_actions (id, email, action, detail)
                VALUES (%s, %s, %s, %s)
                """,
                (secrets.token_hex(16), email, action, detail),
            )

    def list_actions(self, limit: int = 50) -> list[dict]:
        with self._lock, self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, created_at, email, action, detail
                FROM admin_actions
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            rows = cur.fetchall()
        found = []
        for row in rows:
            created = row[1]
            found.append(
                {
                    "id": row[0],
                    "created_at": created.isoformat() if hasattr(created, "isoformat") else str(created),
                    "email": row[2],
                    "action": row[3],
                    "detail": row[4],
                }
            )
        return found

    def purge_old_traces(self) -> None:
        with self._lock, self.conn.cursor() as cur, self.conn.transaction():
            cur.execute("DELETE FROM traces WHERE created_at < now() - interval '3 months'")

    def list_traces(self, limit: int = 50) -> list[TraceRecord]:
        self.purge_old_traces()
        with self._lock, self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT payload
                FROM traces
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            return [_trace_from_payload(row[0]) for row in cur.fetchall()]

    def delete_trace(self, trace_id: str) -> bool:
        with self._lock, self.conn.cursor() as cur, self.conn.transaction():
            cur.execute("DELETE FROM traces WHERE id = %s", (trace_id,))
            return cur.rowcount == 1

    def get_trace(self, trace_id: str) -> TraceRecord | None:
        with self._lock, self.conn.cursor() as cur:
            cur.execute("SELECT payload FROM traces WHERE id = %s AND created_at >= now() - interval '3 months'",
                        (trace_id,))
            row = cur.fetchone()
        if not row:
            return None
        return _trace_from_payload(row[0])


def _check_replacement_date(starts: date, ends: date | None, replacement_starts: date) -> None:
    if replacement_starts <= starts:
        raise ValueError("A scheduled replacement must start after the earlier revision starts.")
    if ends and replacement_starts.toordinal() > ends.toordinal() + 1:
        raise ValueError("This start date would leave a gap after the earlier revision expires.")


def _assert_admin_change(cur, actor: str, target: str, remains_admin: bool) -> None:
    cur.execute("SELECT pg_advisory_xact_lock(490622360)")
    cur.execute("SELECT email FROM employees WHERE is_admin")
    admins = {row[0] for row in cur.fetchall()}
    if actor not in admins:
        raise PermissionError("Your access changed. Sign in again.")
    if not remains_admin and admins == {target}:
        raise ValueError("Keep at least one admin.")


def _audit(cur, email: str, action: str, detail: str) -> None:
    cur.execute("INSERT INTO admin_actions(id, email, action, detail) VALUES (%s, %s, %s, %s)",
                (secrets.token_hex(16), email, action, detail))


def _row_passage(row: tuple) -> Passage:
    effective_to = row[8]
    if isinstance(effective_to, datetime):
        effective_to = effective_to.date()
    return Passage(
        id=row[0],
        policy_id=row[1],
        title=row[2],
        section_label=row[3],
        heading=row[4],
        body=row[5],
        audience=row[6],
        effective_from=row[7],
        effective_to=effective_to,
        vec_score=float(row[9] or 0),
        heading_path=row[10] or "",
        content_type=row[11] or "section",
        page=row[12],
        image_path=row[13] or "",
        polarity=row[14] or "",
        allowed_roles=tuple(row[15]) if row[15] is not None else None,
    )


def _trace_payload(trace: TraceRecord) -> dict:
    return {
        "id": trace.id,
        "created_at": trace.created_at,
        "email": trace.email,
        "name": trace.name,
        "designation": trace.designation,
        "question": trace.question,
        "guardrail": trace.guardrail,
        "guardrail_reason": trace.guardrail_reason,
        "classifier_called": trace.classifier_called,
        "cache": trace.cache,
        "rewritten_question": trace.rewritten_question,
        "history_note": trace.history_note,
        "search": trace.search,
        "sections": trace.sections,
        "withheld": trace.withheld,
        "scores": trace.scores,
        "model": trace.model,
        "tokens_in": trace.tokens_in,
        "tokens_out": trace.tokens_out,
        "latency_ms": trace.latency_ms,
        "outcome": trace.outcome,
        "answer": trace.answer,
        "citations": [
            {"label": item.label, "passage_id": item.passage_id, "title": item.title} for item in trace.citations
        ],
        "steps": trace.steps,
        "prompt_version": trace.prompt_version,
        "passage_ids": trace.passage_ids,
    }


def _trace_from_payload(payload: dict) -> TraceRecord:
    from .models import Citation

    return TraceRecord(
        id=payload["id"],
        created_at=payload["created_at"],
        email=payload["email"],
        name=payload["name"],
        designation=payload["designation"],
        question=payload["question"],
        guardrail=payload["guardrail"],
        guardrail_reason=payload["guardrail_reason"],
        classifier_called=payload["classifier_called"],
        cache=payload["cache"],
        rewritten_question=payload["rewritten_question"],
        history_note=payload["history_note"],
        search=payload["search"],
        sections=payload.get("sections") or [],
        withheld=payload.get("withheld", False),
        scores=payload.get("scores") or [],
        model=payload.get("model"),
        tokens_in=payload.get("tokens_in") or 0,
        tokens_out=payload.get("tokens_out") or 0,
        latency_ms=payload.get("latency_ms") or 0,
        outcome=payload.get("outcome") or "",
        answer=payload.get("answer") or "",
        citations=[
            Citation(item["label"], "", passage_id=item.get("passage_id") or "", title=item.get("title") or "")
            for item in payload.get("citations") or []
        ],
        steps=payload.get("steps") or [],
        prompt_version=payload.get("prompt_version") or "",
        passage_ids=payload.get("passage_ids") or [],
    )
