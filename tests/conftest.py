"""All automated tests are offline, including explicitly enabled SQLite integration."""
from contextlib import closing
import os
import re
import socket
import sqlite3

import pytest

from bird_starter import config


def pytest_sessionstart(session):
    """Remove inherited real credentials before collection or traceback rendering.

    Tests may install explicit fake credentials with monkeypatch. Never enumerate
    values, load production dotenv, or retain the removed secrets for logging.
    """
    for name in list(os.environ):
        if re.search(r"KEY|TOKEN|SECRET|PASSWORD|AUTH|CREDENTIAL", name, re.I):
            os.environ.pop(name, None)


@pytest.fixture(autouse=True)
def isolated_project_dotenv(monkeypatch, tmp_path):
    """Never load the user's actual key/configuration into unit tests."""
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)


@pytest.fixture(autouse=True)
def no_test_network(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("Automated tests must not open network connections")
    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket.socket, "connect_ex", deny)
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")


@pytest.fixture
def tiny_db(tmp_path):
    """Synthetic orders/extra tables only for unit tests, never benchmark data."""
    root = tmp_path / "databases"
    folder = root / "demo"
    folder.mkdir(parents=True)
    db = folder / "demo.sqlite"
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, amount INTEGER)")
        conn.execute("CREATE TABLE unrelated (name TEXT)")
        conn.executemany("INSERT INTO orders(amount) VALUES (?)", [(3,), (5,)])
        conn.commit()
    return root, db
