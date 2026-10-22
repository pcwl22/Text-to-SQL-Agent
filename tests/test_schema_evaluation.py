"""Gold access occurs only inside the offline dependency evaluator."""
import json
from contextlib import closing
import sqlite3

import pytest

from bird_starter.schema_evaluation import gold_dependencies, evaluate_selection, summarize_schema
from bird_starter.schema_linking import read_schema, full_selection


@pytest.fixture
def snapshot(tmp_path):
    path = tmp_path / "evaluation-fixture.sqlite"
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript('CREATE TABLE orders(id INT,customer_id INT,amount INT);'
                           'CREATE TABLE customers(id INT,name TEXT);')
    return read_schema(path)


@pytest.mark.parametrize("sql,expected", [
    ("SELECT amount FROM orders", {("orders", "amount")}),
    ("SELECT o.amount,c.name FROM orders o,customers c WHERE o.customer_id=c.id",
     {("orders", "amount"), ("orders", "customer_id"), ("customers", "id"), ("customers", "name")}),
    ("WITH x AS (SELECT customer_id,SUM(amount) AS s FROM orders GROUP BY customer_id) "
     "SELECT c.name,x.s FROM x JOIN customers c ON c.id=x.customer_id",
     {("orders", "amount"), ("orders", "customer_id"), ("customers", "id"), ("customers", "name")}),
    ("SELECT COUNT(*) FROM orders o WHERE EXISTS(SELECT 1 FROM customers c WHERE c.id=o.customer_id)",
     {("orders", "customer_id"), ("customers", "id")}),
    ("SELECT COUNT(*) FROM orders JOIN customers USING(id)", {("orders", "id"), ("customers", "id")}),
    ("SELECT COUNT(*) FROM orders NATURAL JOIN customers", {("orders", "id"), ("customers", "id")}),
    ("SELECT * FROM orders", {("orders", "id"), ("orders", "customer_id"), ("orders", "amount")}),
    ("SELECT amount AS total FROM orders ORDER BY total", {("orders", "amount")}),
    ("SELECT amount FROM main.orders UNION ALL SELECT amount FROM orders", {("orders", "amount")}),
])
def test_ast_scope_dependencies(snapshot, sql, expected):
    result = gold_dependencies(sql, snapshot)
    assert result["status"] == "RESOLVED"
    assert {(c["table"], c["column"]) for c in result["columns"]} == expected
    assert sql not in json.dumps(result)


@pytest.mark.parametrize("sql", ["SELECT missing FROM orders", "SELECT id FROM orders JOIN customers ON orders.id=customers.id",
                                "SELECT * FROM unknown_table", "SELECT ( FROM", "DROP TABLE orders",
                                "WITH RECURSIVE x(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM x WHERE n<2) SELECT * FROM x"])
def test_unknown_is_not_perfect_recall(snapshot, sql):
    result = evaluate_selection(sql, snapshot, full_selection(snapshot))
    assert result["dependency_status"] == "UNKNOWN"
    assert result["table_recall"] is None and result["column_recall"] is None
    assert sql not in json.dumps(result)


def test_missing_tables_columns_and_fallback_recall(snapshot):
    sql = "SELECT c.name,SUM(o.amount) FROM customers c JOIN orders o ON o.customer_id=c.id GROUP BY c.name"
    sparse = {"tables": {"orders": ["id"]}}
    result = evaluate_selection(sql, snapshot, sparse)
    assert result["table_recall"] == .5 and result["column_recall"] == 0
    assert result["missing_tables"] == ["customers"]
    assert {"table": "orders", "column": "amount"} in result["missing_columns"]
    full = evaluate_selection(sql, snapshot, full_selection(snapshot))
    assert full["table_recall"] == full["column_recall"] == 1
    rows = [{"case_id": 1, "schema_evaluation": result, "schema_linking": {"selection_valid": True, "model_calls": 1}},
            {"case_id": 2, "schema_evaluation": full, "schema_linking": {"selection_valid": False, "fallback": True, "model_calls": 1}}]
    summary = summarize_schema(rows)
    assert summary["selector_valid_rate"] == .5 and summary["effective_selection_legal_rate"] == 1
    assert summary["fallback_cases"] == 1 and summary["schema_linking_model_calls"] == 2
    assert summary["usage_missing_calls"] == 2 and summary["prompt_tokens"] is None
    assert len(summary["missing_dependency_cases"]) == 1
