from contextlib import closing
import sqlite3

import pytest

from bird_starter import tools
from bird_starter.evaluation import result_match_proxy


@pytest.mark.parametrize("sql", [
    "SELECT 1", "SELECT '; DELETE FROM orders;'", "SELECT * FROM main.orders",
    "WITH x AS (SELECT 1 AS n) SELECT n FROM x", "SELECT 1 UNION SELECT 2",
    "SELECT 1 INTERSECT SELECT 1", "SELECT 1 EXCEPT SELECT 2",
])
def test_allowed_queries(tiny_db, sql):
    result = tools.execute_select(tiny_db[1], sql)
    assert result["complete"] is True


@pytest.mark.parametrize("sql", [
    "INSERT INTO orders VALUES (9, 9)", "UPDATE orders SET amount=0", "DELETE FROM orders",
    "DROP TABLE orders", "ALTER TABLE orders ADD x", "CREATE TABLE evil(x)",
    "PRAGMA query_only=OFF", "ATTACH ':memory:' AS other", "DETACH main", "VACUUM",
    "SELECT 1; SELECT 2", "SELECT 1; DELETE FROM orders", "SELECT load_extension('evil')",
    "SELECT readfile('secret')", "SELECT writefile('out', 'x')", "SELECT eval('DROP TABLE orders')",
    "SELECT * FROM pragma_table_info('orders')", "SELECT * FROM other.orders",
    "SELECT * FROM temp.orders", "WITH x AS (SELECT 1) DELETE FROM orders",
])
def test_unsafe_queries_rejected_without_changes(tiny_db, sql):
    db = tiny_db[1]
    before = db.read_bytes()
    with pytest.raises((ValueError, sqlite3.Error)):
        tools.execute_select(db, sql)
    assert db.read_bytes() == before


@pytest.mark.parametrize("sql", ["UPDATE orders SET amount=0", "SELECT load_extension('evil')",
                                  "PRAGMA query_only=OFF", "ATTACH ':memory:' AS other"])
def test_authorizer_blocks_guard_bypass(tiny_db, monkeypatch, sql):
    monkeypatch.setattr(tools, "check_read_only_sql", lambda text: text)
    before = tiny_db[1].read_bytes()
    with pytest.raises(sqlite3.Error):
        tools.execute_select(tiny_db[1], sql)
    assert tiny_db[1].read_bytes() == before


def test_connection_and_schema_resource_lifetime(tiny_db, monkeypatch):
    with closing(tools.ro_connect(tiny_db[1])) as conn:
        assert conn.execute("PRAGMA query_only").fetchone() == (1,)
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("DELETE FROM orders")
    connections = []
    original = tools.ro_connect
    def connect(path):
        conn = original(path)
        connections.append(conn)
        return conn
    monkeypatch.setattr(tools, "ro_connect", connect)
    assert set(tools.schema_catalog(tiny_db[1])) == {"orders", "unrelated"}
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connections[0].execute("SELECT 1")


def test_row_and_byte_caps_and_proxy_unknown(tiny_db):
    assert tools.execute_select(tiny_db[1], "SELECT * FROM orders", max_rows=1)["complete"] is False
    result = tools.execute_select(tiny_db[1], "SELECT hex(zeroblob(50))", max_result_bytes=20)
    assert result["complete"] is False and result["rows"] == []
    sql = "WITH RECURSIVE x(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM x WHERE n<10001) SELECT n FROM x"
    assert result_match_proxy(str(tiny_db[1]), sql, sql) is None


def test_timeout_and_cell_limit(tiny_db):
    sql = "WITH RECURSIVE x(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM x) SELECT SUM(n) FROM x"
    with pytest.raises(sqlite3.OperationalError, match="interrupted"):
        tools.execute_select(tiny_db[1], sql, timeout_seconds=.001)
    with pytest.raises(sqlite3.DataError):
        tools.execute_select(tiny_db[1], "SELECT randomblob(3000000)")


@pytest.mark.parametrize("kwargs", [{"max_rows": 0}, {"max_rows": -1}, {"max_rows": True},
                                    {"timeout_seconds": 0}, {"timeout_seconds": float("nan")},
                                    {"max_result_bytes": 0}])
def test_invalid_resource_parameters(tiny_db, kwargs):
    with pytest.raises(ValueError):
        tools.execute_select(tiny_db[1], "SELECT 1", **kwargs)
