import json
import sqlite3
import pytest
from bird_starter.data import load_cases, find_db
from bird_starter.tools import check_read_only_sql, schema_catalog, execute_select
from bird_starter.evaluation import result_match_proxy

@pytest.fixture
def fixture_db(tmp_path):
    root = tmp_path / "databases"
    folder = root / "demo"
    folder.mkdir(parents=True)
    db = folder / "demo.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, amount INTEGER)")
        conn.executemany("INSERT INTO orders(amount) VALUES (?)", [(3,), (5,)])
    return root, db

def test_catalog_and_ro(fixture_db):
    root, db = fixture_db
    assert find_db(root, "demo") == db
    assert "orders" in schema_catalog(db)
    assert execute_select(db, "SELECT SUM(amount) FROM orders")["rows"] == [(8,)]
    assert result_match_proxy(str(db), "SELECT amount FROM orders", "SELECT amount FROM orders ORDER BY amount DESC") is True
    with pytest.raises(ValueError):
        check_read_only_sql("DROP TABLE orders")
    with pytest.raises(ValueError):
        check_read_only_sql("SELECT 1; DELETE FROM orders")

def test_loader(tmp_path):
    # Synthetic case records used only in this unit test, not benchmark data.
    cases = [{"question_id": 1, "db_id": "demo", "question": "total?", "SQL": "SELECT 1", "evidence": "", "difficulty": "challenging"},
             {"question_id": 2, "db_id": "demo", "question": "count?", "SQL": "SELECT 2", "evidence": "", "difficulty": "simple"}]
    p = tmp_path / "mini_dev_sqlite.json"
    p.write_text(json.dumps(cases))
    assert len(load_cases(p, difficulty="challenging")) == 1
