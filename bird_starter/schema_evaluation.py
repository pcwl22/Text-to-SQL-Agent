"""OFFLINE EVALUATOR ONLY. Gold dependencies must never be imported by generation nodes."""
from collections import Counter

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import traverse_scope, Scope

from .tools import check_read_only_sql


def gold_dependencies(gold_sql, snapshot):
    """AST/scope resolution, including correlated references and implicit USING joins.

    Reports syntactic dependencies, conservatively including referenced CTE bodies.
    Unsupported/ambiguous cases are UNKNOWN, never assumed to have perfect recall.
    No SQL, literal, question or parser message is returned.
    """
    tables = snapshot["tables"]
    names = {name.casefold(): name for name in tables}
    columns = {name: {c["name"].casefold(): c["name"] for c in table["columns"]}
               for name, table in tables.items()}
    try:
        tree = sqlglot.parse_one(check_read_only_sql(gold_sql), read="sqlite")
        if any(node.args.get("recursive") for node in tree.find_all(exp.With)):
            raise ValueError("unsupported recursive CTE")
        for table in tree.find_all(exp.Table):
            if table.db.lower() == "main":
                table.set("db", None)
        schema = {name: {c["name"]: "UNKNOWN" for c in item["columns"]} for name, item in tables.items()}
        tree = qualify(tree, dialect="sqlite", schema=schema, infer_schema=False,
                       validate_qualify_columns=True, allow_partial_qualification=False)
        needed_tables, needed_columns = set(), set()
        for scope in traverse_scope(tree):
            for _, source in scope.selected_sources.values():
                if isinstance(source, exp.Table):
                    name = names.get(source.name.casefold())
                    if name is None:
                        raise ValueError("unknown base table")
                    needed_tables.add(name)
                elif not isinstance(source, Scope):
                    raise ValueError("unsupported source")
            for column in scope.columns:
                owner = scope
                while owner is not None and column.table not in owner.sources:
                    owner = owner.parent
                if owner is None:
                    raise ValueError("unresolved column scope")
                source = owner.sources[column.table]
                if isinstance(source, exp.Table):
                    table = names.get(source.name.casefold())
                    field = columns.get(table, {}).get(column.name.casefold())
                    if field is None:
                        raise ValueError("unresolved column")
                    needed_columns.add((table, field))
                elif not isinstance(source, Scope):
                    raise ValueError("unsupported column source")
            # NATURAL JOIN implies columns even when SELECT COUNT(*) names none.
            if isinstance(scope.expression, exp.Select):
                from_clause = scope.expression.args.get("from_")
                left = [from_clause.this.alias_or_name] if from_clause else []
                for join in scope.expression.args.get("joins", []):
                    right = join.this.alias_or_name
                    if join.method.upper() == "NATURAL":
                        sources = [scope.sources[a] for a in left + [right]]
                        if not all(isinstance(source, exp.Table) for source in sources):
                            raise ValueError("natural derived source not supported")
                        right_table = names[sources[-1].name.casefold()]
                        for source in sources[:-1]:
                            left_table = names[source.name.casefold()]
                            for shared in set(columns[left_table]) & set(columns[right_table]):
                                needed_columns.add((left_table, columns[left_table][shared]))
                                needed_columns.add((right_table, columns[right_table][shared]))
                    left.append(right)
        return {"status": "RESOLVED", "tables": sorted(needed_tables),
                "columns": [{"table": t, "column": c} for t, c in sorted(needed_columns)],
                "definition": "conservative_AST_scope_syntactic_dependencies"}
    except Exception:
        return {"status": "UNKNOWN", "tables": None, "columns": None,
                "reason": "unsupported_or_unresolved_dependencies"}


def evaluate_selection(gold_sql, snapshot, selection):
    dependency = gold_dependencies(gold_sql, snapshot)
    chosen = selection.get("tables", {}) if isinstance(selection, dict) else {}
    legal = isinstance(chosen, dict) and bool(chosen)
    available = {name: {c["name"] for c in item["columns"]} for name, item in snapshot["tables"].items()}
    selected_columns = set()
    for table, fields in chosen.items() if isinstance(chosen, dict) else []:
        if (table not in available or not isinstance(fields, list) or not fields
                or not all(isinstance(c, str) and c in available[table] for c in fields)):
            legal = False
        else:
            selected_columns.update((table, c) for c in fields)
    result = {"dependency_status": dependency["status"], "effective_selection_legal": legal,
              "table_recall": None, "column_recall": None,
              "required_table_count": 0, "recalled_table_count": 0,
              "required_column_count": 0, "recalled_column_count": 0,
              "missing_tables": [], "missing_columns": []}
    if dependency["status"] == "RESOLVED":
        required_tables = set(dependency["tables"])
        required_columns = {(c["table"], c["column"]) for c in dependency["columns"]}
        matched_tables = required_tables & set(chosen) if isinstance(chosen, dict) else set()
        matched_columns = required_columns & selected_columns
        result.update(required_table_count=len(required_tables), recalled_table_count=len(matched_tables),
                      required_column_count=len(required_columns), recalled_column_count=len(matched_columns),
                      table_recall=len(matched_tables) / len(required_tables) if required_tables else None,
                      column_recall=len(matched_columns) / len(required_columns) if required_columns else None,
                      missing_tables=sorted(required_tables - matched_tables),
                      missing_columns=[{"table": t, "column": c} for t, c in sorted(required_columns - matched_columns)])
    return result


def summarize_schema(records):
    """Raw selector legality is separate from legal full-schema fallback output."""
    selection = [r.get("schema_linking", {}) for r in records]
    raw = [s["selection_valid"] for s in selection if type(s.get("selection_valid")) is bool]
    evaluated = [r["schema_evaluation"] for r in records if "schema_evaluation" in r]
    required_tables = sum(r["required_table_count"] for r in evaluated)
    required_columns = sum(r["required_column_count"] for r in evaluated)
    full_chars = sum(s.get("full_schema_chars", 0) for s in selection)
    chosen_chars = sum(s.get("selected_schema_chars", 0) for s in selection)
    calls = sum(s.get("model_calls", 0) for s in selection)
    reported = sum(s.get("usage_reported_calls", 0) for s in selection)
    tokens = lambda field: sum(s[field] for s in selection if s.get(field) is not None) if any(
        s.get(field) is not None for s in selection) else None
    return {"records": len(records), "selector_valid_cases": sum(raw), "selector_evaluated_cases": len(raw),
            "selector_valid_rate": sum(raw) / len(raw) if raw else None,
            "effective_selection_legal_rate": sum(r["effective_selection_legal"] for r in evaluated) / len(evaluated) if evaluated else None,
            "fallback_cases": sum(s.get("fallback", False) for s in selection),
            "dependency_status_counts": dict(Counter(r["dependency_status"] for r in evaluated)),
            "table_recall_micro": sum(r["recalled_table_count"] for r in evaluated) / required_tables if required_tables else None,
            "column_recall_micro": sum(r["recalled_column_count"] for r in evaluated) / required_columns if required_columns else None,
            "required_table_count": required_tables, "required_column_count": required_columns,
            "schema_compression_ratio": 1 - chosen_chars / full_chars if full_chars else None,
            "schema_linking_model_calls": calls, "prompt_tokens": tokens("prompt_tokens"),
            "completion_tokens": tokens("completion_tokens"), "usage_missing_calls": calls - reported,
            "schema_linking_latency_ms": sum(s.get("latency_ms", 0) for s in selection),
            "missing_dependency_cases": [{"case_id": r.get("case_id"),
                    "missing_tables": r["schema_evaluation"]["missing_tables"],
                    "missing_columns": r["schema_evaluation"]["missing_columns"]} for r in records
                    if r.get("schema_evaluation", {}).get("missing_tables") or r.get("schema_evaluation", {}).get("missing_columns")],
            "official_ex": False, "notice": "Offline schema dependencies only; not model accuracy or official BIRD EX."}
