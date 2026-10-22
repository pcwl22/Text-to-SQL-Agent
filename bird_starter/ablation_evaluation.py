"""Evaluator-only Gold access after Agent END. No model calls or feedback path."""
from .evaluation import result_match_proxy
from .schema_evaluation import evaluate_selection


class OfflineEvaluator:
    def __init__(self):
        self.proxy_cache = {}

    def evaluate(self, case, db_path, snapshot, result, selection):
        # Called only on completed Agent outputs. Cache does not cross suites.
        schema = evaluate_selection(case["SQL"], snapshot, selection)
        if not result.get("execution_ok"):
            return {"schema_recall": schema, "local_result_match_proxy": False, "proxy_status": "prediction_failed"}
        if result.get("output_truncated"):
            return {"schema_recall": schema, "local_result_match_proxy": None, "proxy_status": "prediction_truncated"}
        key = (case["case_id"], result["generated_sql"])
        if key not in self.proxy_cache:
            self.proxy_cache[key] = result_match_proxy(str(db_path), result["generated_sql"], case["SQL"])
        match = self.proxy_cache[key]
        return {"schema_recall": schema, "local_result_match_proxy": match,
                "proxy_status": "matched" if match is True else "not_matched_or_evaluator_error" if match is False else
                                "evaluator_truncated_or_unknown"}
