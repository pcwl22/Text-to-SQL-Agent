"""Mock MCP adapter exposing live-shaped tools backed by local BIRD SQLite files.

Launch with: BIRD_DB_ROOT=... python -m bird_starter.mcp_server
"""
from __future__ import annotations
import os
from mcp.server.fastmcp import FastMCP
from .data import find_db
from .tools import schema_catalog, execute_select

mcp = FastMCP("bird-sql-replay")

def _db(db_id: str):
    root = os.environ.get("BIRD_DB_ROOT")
    if not root:
        raise RuntimeError("Set BIRD_DB_ROOT first")
    return find_db(root, db_id)

@mcp.tool()
def get_schema(db_id: str) -> dict[str, str]:
    """Read SQL CREATE TABLE definitions from a benchmark database."""
    return schema_catalog(_db(db_id))

@mcp.tool()
def query_sqlite(db_id: str, sql: str) -> dict:
    """Execute one read-only SQLite SELECT with a time and result cap."""
    result = execute_select(_db(db_id), sql, max_rows=50)
    return {"columns": result["columns"], "rows": result["rows"],
            "complete": result["complete"], "elapsed_ms": result["elapsed_ms"]}

if __name__ == "__main__":
    mcp.run(transport="stdio")
