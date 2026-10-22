"""Offline import/CLI smoke checks; no Model is instantiated or API called."""
import importlib
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("module", [
    "bird_starter.data",
    "bird_starter.model",
    "bird_starter.tools",
    "bird_starter.workflow",
    "bird_starter.evaluation",
    "bird_starter.mcp_server",
    "run_eval",
])
def test_module_import(module):
    importlib.import_module(module)


def test_cli_help():
    result = subprocess.run(
        [sys.executable, "run_eval.py", "--help"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=30,
        env={**os.environ, "PYTHON_DOTENV_DISABLED": "1"},
    )
    assert result.returncode == 0, result.stderr
    assert "--mode {baseline,graph,schema_only,repair_only,full}" in result.stdout
