import pytest

from bird_starter.reporting import estimated_cost, latency_stats, summarize


def test_latency_percentiles():
    assert latency_stats([10, 20, 30])["average"] == 20
    assert latency_stats([10, 20, 30])["p50"] == 20
    assert latency_stats([10, 20, 30])["p95"] == pytest.approx(29)
    assert latency_stats([])["p95"] is None


def test_cost_requires_complete_usage_and_explicit_prices():
    record = {"model_calls": 1, "usage_reported_calls": 1, "prompt_tokens": 1000, "completion_tokens": 500}
    assert estimated_cost(record, None, None) is None
    assert estimated_cost(record, 2, 4) == .004
    record["model_calls"] = 2
    assert estimated_cost(record, 2, 4) is None  # unknown timeout attempt may have been billed


def test_summary_denominators_unknown_usage_and_errors():
    base = {"dry_run": False, "generation_success": True, "execution_ok": False,
            "latency_ms": 10, "model_latency_ms": 5, "model_calls": 1, "usage_reported_calls": 0,
            "prompt_tokens": None, "completion_tokens": None, "estimated_cost": None,
            "local_result_match_proxy": None}
    summary = summarize([{**base, "error_type": "sql_syntax_error"},
                         {**base, "error_type": "sql_timeout"},
                         {**base, "error_type": None, "execution_ok": True}])
    assert summary["sql_syntax_error_rate"] == pytest.approx(1 / 3)
    assert summary["sql_execution_timeout_rate"] == pytest.approx(1 / 3)
    assert summary["sql_execution_success_rate"] == pytest.approx(1 / 3)
    assert summary["average_model_calls"] == 1
    assert summary["usage_unknown_calls"] == 3 and summary["prompt_tokens"] is None
    assert summary["estimated_cost"] is None
    assert summary["local_result_match_proxy"]["official"] is False
