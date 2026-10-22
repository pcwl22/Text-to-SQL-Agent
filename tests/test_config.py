"""Offline dotenv fixtures only: never inspect the user's actual .env or API key."""
import json
import os
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from bird_starter import config
from bird_starter.model import Model, ModelFailure
import run_eval


def clear_env(monkeypatch, *keys):
    # Register restoration even when a variable was initially absent.
    for key in keys:
        monkeypatch.setenv(key, "offline-placeholder")
        monkeypatch.delenv(key)


def test_project_env_is_independent_of_cwd_and_preserves_environment(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("BIRD_DOTENV_FIXTURE", raising=False)
    monkeypatch.setenv("BIRD_EXISTING_FIXTURE", "shell-value")
    (tmp_path / ".env").write_text(
        "BIRD_DOTENV_FIXTURE=project-value\nBIRD_EXISTING_FIXTURE=file-value\n", encoding="utf-8-sig")
    other = tmp_path / "other"
    other.mkdir()
    (other / ".env").write_text("BIRD_DOTENV_FIXTURE=wrong-cwd\n", encoding="utf-8")
    monkeypatch.chdir(other)
    config.load_project_env()
    assert os.environ["BIRD_DOTENV_FIXTURE"] == "project-value"
    assert os.environ["BIRD_EXISTING_FIXTURE"] == "shell-value"
    assert capsys.readouterr().out == ""
    monkeypatch.delenv("BIRD_DOTENV_FIXTURE")


def test_missing_project_env_is_optional(tmp_path):
    config.load_project_env()
    assert not (tmp_path / ".env").exists()


@pytest.mark.parametrize("canonical_exists", [False, True])
def test_existing_provider_names_are_supported_without_overriding_canonical(tmp_path, monkeypatch,
                                                                          canonical_exists, capsys):
    mapping = {"OPENAI_API_KEY": ("DEEPSEEK_API_KEY", "fixture-alias-secret"),
               "OPENAI_MODEL": ("DEEPSEEK_MODEL", "fixture-alias-model"),
               "OPENAI_BASE_URL": ("DEEPSEEK_API_URL", "https://example.invalid/v1")}
    clear_env(monkeypatch, *mapping, *(source for source, _ in mapping.values()))
    (tmp_path / ".env").write_text("\n".join(f"{source}={value}" for source,value in mapping.values()),
                                    encoding="utf-8")
    if canonical_exists:
        for target in mapping:
            monkeypatch.setenv(target, "existing-shell-value")
    config.load_project_env()
    for target, (_, value) in mapping.items():
        assert os.environ[target] == ("existing-shell-value" if canonical_exists else value)
    assert capsys.readouterr().out == ""


def test_model_directly_loads_project_env(tmp_path, monkeypatch):
    clear_env(monkeypatch, "OPENAI_MODEL", "OPENAI_BASE_URL")
    (tmp_path / ".env").write_text(
        "OPENAI_MODEL=dotenv-fixture\nOPENAI_BASE_URL=https://example.invalid/v1\n", encoding="utf-8")
    model = Model(client=object())
    assert model.model == "dotenv-fixture"
    assert model.base_url == "https://example.invalid/v1"


def test_cli_loads_dotenv_before_paths_and_never_records_key(tmp_path, tiny_db, monkeypatch, capsys):
    clear_env(monkeypatch, "OPENAI_MODEL", "OPENAI_BASE_URL", "OPENAI_API_KEY",
              "BIRD_CASES_PATH", "BIRD_DB_ROOT")
    cases = tmp_path / "fixture.json"
    cases.write_text(json.dumps([{"question_id": 1, "db_id": "demo", "question": "fixture question",
                                 "evidence": "", "SQL": "FIXTURE_GOLD_ONLY"}]), encoding="utf-8")
    secret = "fixture-dotenv-secret-never-real"
    (tmp_path / ".env").write_text(
        f"OPENAI_MODEL=dotenv-fixture\nOPENAI_BASE_URL=https://example.invalid/v1\n"
        f"OPENAI_API_KEY={secret}\nBIRD_CASES_PATH='{cases.as_posix()}'\n"
        f"BIRD_DB_ROOT='{tiny_db[0].as_posix()}'\n", encoding="utf-8")
    def forbidden(**kwargs):
        raise AssertionError("Dry run must not construct any model client")
    monkeypatch.setattr(run_eval, "Model", forbidden)
    out = tmp_path / "preflight.jsonl"
    result = run_eval.main(["--mode", "baseline", "--dry-run", "--out", str(out)])
    assert result["preflight_successes"] == 1 and result["model_calls"] == 0
    cfg = json.loads(out.with_suffix(".config.json").read_text(encoding="utf-8"))
    assert cfg["model"] == "dotenv-fixture" and cfg["base_url"] == "https://example.invalid/v1"
    assert Path(cfg["db_root"]) == tiny_db[0]
    assert os.environ["OPENAI_API_KEY"] == secret
    text = "".join(p.read_text(encoding="utf-8") for p in
                   [out, out.with_suffix(".config.json"), out.with_suffix(".summary.json")])
    assert secret not in text + capsys.readouterr().out


def test_env_is_gitignored():
    rules = (Path(run_eval.__file__).parent / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in rules and ".env.*" in rules


@pytest.mark.parametrize("file_models,shell_models,cli_model,expected", [
    ({"DEEPSEEK_MODEL": "deepseek-4.1-flash"}, {}, None, "deepseek-4.1-flash"),
    ({"DEEPSEEK_MODEL": "file-alias"}, {"DEEPSEEK_MODEL": "shell-alias"}, None, "shell-alias"),
    ({"OPENAI_MODEL": "file-canonical"}, {"DEEPSEEK_MODEL": "shell-alias"}, None, "file-canonical"),
    ({"DEEPSEEK_MODEL": "file-alias"}, {"OPENAI_MODEL": "shell-canonical"}, None, "shell-canonical"),
    ({"OPENAI_MODEL": "file-canonical", "DEEPSEEK_MODEL": "file-alias"}, {}, None, "file-canonical"),
    ({"OPENAI_MODEL": "file-canonical"}, {"OPENAI_MODEL": "shell-canonical"}, None, "shell-canonical"),
    ({"DEEPSEEK_MODEL": "file-alias"}, {"OPENAI_MODEL": "shell-canonical", "DEEPSEEK_MODEL": "shell-alias"},
     None, "shell-canonical"),
    ({"OPENAI_MODEL": "deepseek-4.1-flash-background", "DEEPSEEK_MODEL": "deepseek-4.1-flash-background"},
     {"OPENAI_MODEL": "stale-shell-model", "DEEPSEEK_MODEL": "another-stale-shell-model"},
     "deepseek-4.1-flash", "deepseek-4.1-flash"),
    ({"OPENAI_MODEL": "", "DEEPSEEK_MODEL": "file-alias"}, {}, "deepseek-4.1-flash", "deepseek-4.1-flash"),
])
def test_model_precedence_manifest_instance_and_payload(tmp_path, tiny_db, monkeypatch,
                                                      file_models, shell_models, cli_model, expected):
    """Only temporary dotenv/SQLite fixtures and an injected SDK; never load real credentials."""
    clear_env(monkeypatch, "OPENAI_MODEL", "DEEPSEEK_MODEL")
    (tmp_path / ".env").write_text("\n".join(f"{k}={v}" for k, v in file_models.items()), encoding="utf-8")
    for key, value in shell_models.items():
        monkeypatch.setenv(key, value)
    cases = tmp_path / "fixture-cases.json"
    cases.write_text(json.dumps([{"question_id": 1, "db_id": "demo", "question": "fixture question",
                                 "evidence": "fixture clue", "SQL": "FIXTURE_EVALUATOR_ONLY"}]), encoding="utf-8")
    payloads, instances = [], []
    def create(**kwargs):
        payloads.append(kwargs)
        return NS(choices=[NS(message=NS(content="SELECT 1"), finish_reason="stop")])
    def factory(**kwargs):
        instance = Model(**kwargs, client=NS(chat=NS(completions=NS(create=create))))
        instances.append(instance.configuration())
        return instance
    monkeypatch.setattr(run_eval, "Model", factory)
    out = tmp_path / "mock.jsonl"
    args = ["--mode", "baseline", "--limit", "1", "--cases", str(cases), "--db-root", str(tiny_db[0]),
            "--request-retries", "0", "--max-model-calls", "10", "--max-output-tokens", "2048",
            "--token-parameter", "max_completion_tokens", "--case-error-policy", "isolate",
            "--allow-api", "--no-proxy", "--out", str(out)]
    if cli_model is not None:
        args += ["--model", cli_model]
    summary = run_eval.main(args)
    manifest = json.loads(out.with_suffix(".config.json").read_text())
    row = json.loads(out.read_text())
    assert manifest["model"] == instances[0]["model"] == payloads[0]["model"] == row["model"] == expected
    assert summary["model_calls"] == len(payloads) == 1
    assert payloads[0]["max_completion_tokens"] == 2048 and "max_tokens" not in payloads[0]
    assert manifest["request_retries"] == manifest["sdk_retries"] == 0
    assert manifest["max_model_calls"] == 10 and manifest["concurrency"] == 1
    assert manifest["case_error_policy"] == "isolate"


def test_empty_canonical_model_does_not_silently_fall_back(tmp_path, monkeypatch):
    clear_env(monkeypatch, "OPENAI_MODEL", "DEEPSEEK_MODEL")
    (tmp_path / ".env").write_text("OPENAI_MODEL=\nDEEPSEEK_MODEL=deepseek-4.1-flash\n", encoding="utf-8")
    with pytest.raises(ModelFailure, match="model_configuration_error"):
        Model(client=object())
