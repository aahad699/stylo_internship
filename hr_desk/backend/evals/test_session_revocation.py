import os
from uuid import uuid4

import pytest
from psycopg.conninfo import conninfo_to_dict

from app.store_pg import PostgresStore


def test_postgres_sign_out_revokes_session():
    dsn = os.environ.get("HR_DESK_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Set HR_DESK_TEST_DATABASE_URL to run PostgreSQL integration checks.")
    assert conninfo_to_dict(dsn).get("dbname", "").endswith("_test")
    first, second = PostgresStore(dsn, migrate=True), PostgresStore(dsn)
    email = f"signout-{uuid4().hex[:16]}@stylo.example"
    try:
        assert first.create_employee(email, "Signer", "manager")
        session = first.create_session(email)
        assert second.employee_for_session(session).email == email
        first.delete_session(session)
        assert second.employee_for_session(session) is None, "session still valid after sign-out"
    finally:
        first.delete_employee(email)
        first.conn.close()
        second.conn.close()
