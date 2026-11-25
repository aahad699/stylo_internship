"""Postgres keyword candidates must retrieve partial matches in relevance order."""

from datetime import date
from threading import Lock
from unittest.mock import MagicMock

from app.store_pg import PostgresStore


def test_keyword_query_uses_ranked_or_matching():
    store = PostgresStore.__new__(PostgresStore)
    store._lock = Lock()
    cursor = MagicMock()
    store._conn = MagicMock()
    store._conn.closed = False
    store._conn.broken = False
    store._conn.cursor.return_value.__enter__.return_value = cursor
    cursor.fetchall.return_value = []

    store.candidates([0.0], "unpaid provident fund loan", {"hr"}, date(2026, 11, 25), 20)

    sql, params = cursor.execute.call_args_list[1].args
    assert "ts_rank" in sql.lower()
    assert " | " in sql or "'||'" in sql or "'|'" in sql
    assert "order by" in sql.lower()
    assert "unpaid provident fund loan" in params
