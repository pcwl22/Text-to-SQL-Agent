"""P1B fail-closed USD soft cap and single-use authorization; no credentials."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import re

from .adaptive_validation_config import read_json, ROOT
from .experiment_sets import file_sha256
from .model import ModelFailure
from .request_audit import AuditError, write_new_json

RECORD_ONLY = "UNLIMITED_RECORD_ONLY"
CONTENT_FAILURES = {"model_output_truncated", "empty_response", "model_empty_choices", "model_reasoning_only",
                    "model_invalid_response", "model_unsupported_content"}


def amount(value):
    if isinstance(value, bool) or value is None:
        raise ValueError("Finite non-negative monetary amount required")
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise ValueError("Invalid monetary amount") from None
    if not result.is_finite() or result < 0:
        raise ValueError("Invalid monetary amount")
    return result


def verify_live_authorization(path, config, frozen_dir):
    """Human prepares this file only after separate explicit Live authorization.

    A local file is an operational attestation, not a cryptographic proof of
    consent. Its identity is consumed once, globally across output directories.
    """
    auth = read_json(path)
    allowed = {"allow_live", "one_batch_only", "model", "provider", "base_url", "config_sha256", "manifest_sha256",
               "authorization_id", "authorized_by", "max_attempts", "max_usd", "expires_at",
               "server_hard_cap_verified", "server_cap_evidence", "server_cap_remaining_usd", "accept_local_soft_cap_risk",
               "no_monetary_cap", "cost_policy", "max_total_http_reservations", "catalog_allowance"}
    if not isinstance(auth, dict) or set(auth) - allowed:
        raise ValueError("Unexpected authorization fields; never place credentials in this file")
    c, p = config["common"], config["pricing"]
    if (auth.get("allow_live") is not True or auth.get("one_batch_only") is not True
            or auth.get("model") != c["model"] or auth.get("provider") != c["provider"]
            or auth.get("base_url") != c["base_url"]
            or auth.get("config_sha256") != file_sha256(Path(frozen_dir) / "config.json")
            or auth.get("manifest_sha256") != config["manifest_sha256"]
            or not re.fullmatch(r"[a-f0-9]{32}", str(auth.get("authorization_id", "")))
            or not isinstance(auth.get("authorized_by"), str) or not auth["authorized_by"].strip()
            or type(auth.get("max_attempts")) is not int or not 1 <= auth["max_attempts"] <= config["max_attempts"]):
        raise ValueError("Incomplete or mismatched one-time authorization")
    if auth.get("no_monetary_cap") is True:
        if auth.get("max_usd") is not None:
            raise ValueError("Use either an explicit USD cap or explicit no-cap attestation")
    elif amount(auth.get("max_usd")) <= 0:
        raise ValueError("USD cap required unless explicitly waived by the user")
    now = datetime.now(timezone.utc)
    def current(value):
        parsed = datetime.fromisoformat(value)
        if parsed.utcoffset() is None:
            raise ValueError("Timezone required")
        return parsed
    if not now < current(auth["expires_at"]):
        raise ValueError("Expired authorization")
    if auth.get("cost_policy") == RECORD_ONLY:
        if (auth.get("no_monetary_cap") is not True or auth.get("max_usd") is not None
                or type(auth.get("catalog_allowance")) is not int or auth["catalog_allowance"] != 1
                or type(auth.get("max_total_http_reservations")) is not int or auth["max_total_http_reservations"] != 40
                or auth["max_attempts"] + auth["catalog_allowance"] > 40):
            raise ValueError("Unlimited-cost authorization must retain the combined HTTP hard cap")
        return auth  # Explicit user policy: prices, bills and monetary bounds do not gate execution.
    if auth.get("cost_policy") is not None:
        raise ValueError("Unsupported cost policy")
    if (p.get("status") != "VERIFIED" or p.get("currency") != "USD"
            or p.get("model") != c["model"] or p.get("provider") != c["provider"]
            or p.get("base_url") != c["base_url"] or p.get("billing_mode") not in ("per_token", "included_subscription")
            or p.get("additional_charges") is not False or p.get("usage_supported") is not True
            or not str(p.get("source_url", "")).startswith("https://")
            or not 0 <= (now - current(p["verified_at"])).total_seconds() <= 86400):
        raise ValueError("Current provider-specific billing/usage/bounds not verified")
    if p["billing_mode"] == "per_token":
        for key in ("input_per_million", "output_per_million", "cached_input_per_million"):
            amount(p.get(key))
        if (p.get("byte_token_bound_verified") is not True or not str(p.get("bound_source_url", "")).startswith("https://")
                or type(p.get("chat_overhead_token_bound")) is not int or p["chat_overhead_token_bound"] < 0):
            raise ValueError("Verified tokenizer/envelope bound required")
    elif (not p.get("account_plan_evidence") or p.get("included_model_verified") is not True
          or p.get("no_incremental_inference_charge_verified") is not True
          or any(p.get(k) is not None for k in ("input_per_million", "output_per_million", "cached_input_per_million"))):
        raise ValueError("Subscription coverage needs account-specific proof; token rates remain not applicable")
    hard_cap = auth.get("server_hard_cap_verified") is True
    if hard_cap:
        if (not auth.get("server_cap_evidence") or (auth.get("max_usd") is not None
                and amount(auth.get("server_cap_remaining_usd")) > amount(auth["max_usd"]))):
            raise ValueError("Server cap evidence/remaining amount required")
    elif auth.get("accept_local_soft_cap_risk") is not True:
        raise ValueError("No reliable server cap: explicit acceptance required")
    return auth


def consume_authorization(auth, run_id):
    folder = ROOT / "outputs" / "p1b_authorization_consumption"
    folder.mkdir(parents=True, exist_ok=True)
    # Exclusive file create + fsync: a crash never grants another batch.
    write_new_json(folder / (auth["authorization_id"] + ".json"),
                   {"authorization_id": auth["authorization_id"], "run_id": run_id,
                    "automatic_resume_allowed": False})


class BudgetLedger:
    """One underlying durable RequestLog shared across B/E and every node."""
    def __init__(self, batch, *, pricing=None, max_usd=None, cost_path=None, record_only=False):
        self.batch, self.pricing = batch, pricing
        self.record_only = record_only
        self.last_content_failure = None
        self.max_usd = None if max_usd is None else amount(max_usd)
        self.spent, self.committed = Decimal(0), Decimal(0)
        self.stop_reason, self.quote_cost = None, None
        self.quotes = {}
        self.group_counts = {"B": 0, "E": 0}
        self.case_counts = {}
        self.context = None
        self.cost_stream = Path(cost_path).open("x", encoding="utf-8") if cost_path else None
        if self.cost_stream:
            self.record_cost({"event": "COST_GUARD_CREATED", "max_usd": str(self.max_usd),
                              "pricing": pricing, "local_soft_cap_only": not record_only,
                              "cost_policy": RECORD_ONLY if record_only else "VERIFIED_COST_GUARD"})

    def record_cost(self, value):
        if self.cost_stream:
            try:
                text = json.dumps(value) + "\n"
                if self.cost_stream.write(text) != len(text):
                    raise OSError("Incomplete cost reservation")
                self.cost_stream.flush()
                os.fsync(self.cost_stream.fileno())
            except BaseException:
                self.stop_reason = "model_audit_error"
                raise AuditError() from None

    def close(self):
        if self.cost_stream:
            self.cost_stream.close()

    @property
    def model_id(self):
        return self.batch.model_id

    @property
    def max_attempts(self):
        return self.batch.max_attempts

    def quote(self, system, user, max_output):
        if self.stop_reason:
            raise ModelFailure(self.stop_reason)
        if self.record_only or self.pricing is None:
            self.quote_cost = Decimal(0)
            return
        p = self.pricing
        if p.get("billing_mode") == "included_subscription":
            # Verified incremental inference bound, not allocation of subscription fees.
            self.quote_cost = Decimal(0)
            return
        # Conservative only under the separately verified provider/tokenizer bound.
        input_bound = len((system + user).encode("utf-8")) + p["chat_overhead_token_bound"]
        rate = max(amount(p["input_per_million"]), amount(p["cached_input_per_million"]))
        self.quote_cost = (input_bound * rate + max_output * amount(p["output_per_million"])) / 1000000
        if self.max_usd is not None and self.spent + self.committed + self.quote_cost > self.max_usd:
            self.stop_reason = "model_cost_budget_exceeded"
            raise ModelFailure(self.stop_reason)

    def prepare(self, case_id):
        if self.stop_reason or self.quote_cost is None or self.context is None:
            raise AuditError(self.stop_reason or "model_audit_context_missing")
        group, identity, node = self.context
        if (str(case_id) != str(identity) or self.batch.reserved >= self.max_attempts or self.group_counts[group] >= 20
                or self.case_counts.get((group, identity), 0) >= 2):
            self.stop_reason = "model_budget_exceeded"
            raise AuditError(self.stop_reason)
        self.group_counts[group] += 1
        self.case_counts[group, identity] = self.case_counts.get((group, identity), 0) + 1
        quote, self.quote_cost = self.quote_cost, None
        self.committed += quote  # Never refund incomplete/failed persistence or uncertain requests.
        try:
            self.record_cost({"event": "COST_COMMITTED_BEFORE_HTTP", "case": f"{group}:{identity}:{node}",
                              "scope": "incremental_request_bound_not_subscription_allocation",
                              "quoted_usd": str(quote), "spent_usd": str(self.spent),
                              "committed_usd": str(self.committed), "max_usd": str(self.max_usd)})
            ident = self.batch.prepare(f"{group}:{identity}:{node}")
        except BaseException as exc:
            self.stop_reason = getattr(exc, "category", "model_audit_error")
            raise
        self.quotes[ident] = quote
        return ident

    def finish(self, attempt_id, status, **metadata):
        try:
            self.batch.finish(attempt_id, status, **metadata)
        except BaseException:
            self.stop_reason = "model_audit_error"
            raise
        self.last_content_failure = None
        if self.record_only:
            category = metadata.get("error_category")
            http_status = metadata.get("http_status")
            if status != "SUCCEEDED":
                if status == "FAILED" and type(http_status) is int and 200 <= http_status < 300 and category in CONTENT_FAILURES:
                    self.last_content_failure = category  # Completed response; next distinct node/case is not a retry.
                else:
                    self.stop_reason = category or "model_request_failed"
            self.record_cost({"event": "USAGE_RECORDED_COST_NOT_A_STOP_CONDITION", "attempt_id": attempt_id,
                              "prompt_tokens": metadata.get("prompt_tokens"), "completion_tokens": metadata.get("completion_tokens"),
                              "usage_missing": any(type(metadata.get(k)) is not int for k in ("prompt_tokens", "completion_tokens")),
                              "calculated_cost_usd": None, "billed_cost_usd": "UNKNOWN", "stop_reason": self.stop_reason})
            return
        if status != "SUCCEEDED":
            self.stop_reason = metadata.get("error_category") or "model_request_failed"
            self.record_cost({"event": "COST_UNKNOWN_STOP", "attempt_id": attempt_id,
                              "quoted_usd_held": str(self.quotes[attempt_id]), "reason": self.stop_reason})
            return
        if self.pricing is None:
            return
        try:
            if any(type(metadata.get(k)) is not int or metadata[k] < 0 for k in ("prompt_tokens", "completion_tokens")):
                raise ValueError()
            cost = Decimal(0) if self.pricing.get("billing_mode") == "included_subscription" else usage_cost(metadata, self.pricing)
            if cost is None:
                raise ValueError()
            self.spent += cost
            quoted = self.quotes.pop(attempt_id)
            self.committed -= quoted
            if cost > quoted or (self.max_usd is not None and self.spent > self.max_usd):
                self.stop_reason = "model_cost_bound_exceeded"
        except (KeyError, ValueError, TypeError):
            self.stop_reason = "model_usage_or_billing_unknown"
        self.record_cost({"event": "COST_SETTLEMENT", "attempt_id": attempt_id, "spent_usd": str(self.spent),
                          "committed_usd": str(self.committed), "stop_reason": self.stop_reason})


def usage_cost(event, pricing):
    if pricing.get("status") not in (None, "VERIFIED"):
        return None
    if pricing.get("billing_mode") == "included_subscription":
        return None  # No defensible token-based allocation of a subscription bill.
    p, c = event.get("prompt_tokens"), event.get("completion_tokens")
    if type(p) is not int or p < 0 or type(c) is not int or c < 0:
        return None
    try:
        input_rate, cached_rate, output_rate = (amount(pricing[k]) for k in
            ("input_per_million", "cached_input_per_million", "output_per_million"))
    except (ValueError, KeyError):
        return None
    details = event.get("response_metadata", {}).get("prompt_tokens_details")
    cached = details.get("cached_tokens") if isinstance(details, dict) else None
    if cached is None:
        if input_rate != cached_rate:
            return None
        cached = 0
    if type(cached) is not int or not 0 <= cached <= p:
        return None
    extra = event.get("response_metadata", {})
    for name in ("prompt_tokens_details", "completion_tokens_details"):
        data = extra.get(name) or {}
        if (data.get("audio_tokens") or 0) != 0:
            return None
    return ((p - cached) * input_rate + cached * cached_rate + c * output_rate) / 1000000
