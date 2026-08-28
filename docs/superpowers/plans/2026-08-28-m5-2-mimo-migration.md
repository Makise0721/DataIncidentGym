# MiMo V2.5 Model Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current diagnostic model default with Xiaomi MiMo `mimo-v2.5`, using its OpenAI-compatible endpoint and the user-owned `MIMO_API_KEY` environment variable without changing diagnostic budgets, tools, gates, scoring, or prompt.

**Architecture:** Keep the existing `OpenAIChatModel` and `OpenAIProvider` integration. `DiagnosticSettings` receives a narrowly scoped alias so an explicit `DIG_DIAGNOSTIC_MODEL_API_KEY` override remains supported while `MIMO_API_KEY` supplies the normal production credential. Update every current-model surface from local Ollama vocabulary to provider-neutral OpenAI-compatible vocabulary; do not add model-specific parsing, retry, or fallback behavior.

**Tech Stack:** Python 3.12, Pydantic Settings 2.15, PydanticAI OpenAI provider, pytest, Ruff.

---

### Task 1: Migrate effective diagnostic configuration and unit contract

**Files:**
- Modify: `src/data_incident_gym/diagnostic_config.py:5-30`
- Modify: `.env.diagnostic.example:7-9`
- Modify: ignored `.env.diagnostic:7-9`
- Modify: `tests/unit/test_diagnostic_config.py:29-62`

- [ ] **Step 1: Write the failing settings tests**

```python
def test_diagnostic_model_defaults_use_mimo() -> None:
    settings = DiagnosticSettings(_env_file=None)
    assert settings.model_base_url == "https://api.xiaomimimo.com/v1"
    assert settings.model_name == "mimo-v2.5"


def test_mimo_api_key_is_used_when_no_diagnostic_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DIG_DIAGNOSTIC_MODEL_API_KEY", raising=False)
    monkeypatch.setenv("MIMO_API_KEY", "TEST_REDACTED_VALUE")
    assert DiagnosticSettings(_env_file=None).model_api_key.get_secret_value() == "TEST_REDACTED_VALUE"
```

- [ ] **Step 2: Run the focused settings tests and confirm the new assertions fail**

Run: `uv run pytest tests/unit/test_diagnostic_config.py -q`

Expected: failure because the current defaults still identify the local Ollama endpoint and no `MIMO_API_KEY` alias exists.

- [ ] **Step 3: Implement the minimal configuration update**

```python
from pydantic import AliasChoices, Field, SecretStr, StrictStr, field_validator

model_base_url: StrictStr = "https://api.xiaomimimo.com/v1"
model_name: StrictStr = "mimo-v2.5"
model_api_key: SecretStr = Field(
    default=SecretStr("mimo-api-key-required"),
    validation_alias=AliasChoices("DIG_DIAGNOSTIC_MODEL_API_KEY", "MIMO_API_KEY"),
)
```

Set the example and ignored effective configuration to the MiMo Base URL and model ID. Remove any `DIG_DIAGNOSTIC_MODEL_API_KEY` value from the ignored file so it cannot mask the user-owned `MIMO_API_KEY`; document the key variable without writing its value.

- [ ] **Step 4: Run the focused settings tests and confirm they pass**

Run: `uv run pytest tests/unit/test_diagnostic_config.py -q`

Expected: all tests pass and no assertion or representation exposes `TEST_REDACTED_VALUE`.

### Task 2: Migrate provider-facing status surfaces and their tests

**Files:**
- Modify: `src/data_incident_gym/doctor.py:57-83, 361-401`
- Modify: `src/data_incident_gym/cli.py:31-36`
- Modify: `pyproject.toml:37-41`
- Modify: `tests/unit/test_doctor.py`
- Modify: `tests/unit/test_cli.py`
- Modify: `tests/e2e/test_ollama_diagnosis.py`
- Modify: `tests/e2e/test_ollama_evaluation.py`

- [ ] **Step 1: Write failing tests for provider-neutral doctor codes and MiMo recommendations**

```python
assert DoctorCheckCode.MODEL_ENDPOINT.value == "MODEL_ENDPOINT"
assert RECOMMENDATION_BY_CHECK[DoctorCheckCode.MODEL_ENDPOINT] == "CHECK_MODEL_ENDPOINT"
assert RECOMMENDATION_BY_CHECK[DoctorCheckCode.MODEL_PRESENT] == "CHECK_MIMO_MODEL_ACCESS"
```

Update test doubles to use `mimo-v2.5` and assert the endpoint probe sends only the documented API credential header, never serializes the key in `DoctorResult`.

- [ ] **Step 2: Run doctor and CLI unit tests to verify the contract failures**

Run: `uv run pytest tests/unit/test_doctor.py tests/unit/test_cli.py -q`

Expected: failure until the check enum, recommendation mapping, CLI text, and test marker language are migrated.

- [ ] **Step 3: Implement the minimal provider-neutral migration**

Rename the Ollama-only endpoint check and recommendation to `MODEL_ENDPOINT` and `CHECK_MODEL_ENDPOINT`. Build the OpenAI-compatible `/models` request with `urllib.request.Request` and the API key header documented by MiMo; retain the 5-second timeout, 1 MiB bound, strict JSON/model-ID validation, and safe `UNAVAILABLE` failure behavior. Rename the opt-in pytest marker and environment gate from Ollama-specific names to `real_model` / `DIG_RUN_REAL_MODEL_TESTS`; do not change the one-probe or three-sample counts.

- [ ] **Step 4: Run the migrated focused tests**

Run: `uv run pytest tests/unit/test_doctor.py tests/unit/test_cli.py -q`

Expected: all pass without real network or model requests.

### Task 3: Synchronize model metadata fixtures, requirements, and validation

**Files:**
- Modify: `tests/unit/test_artifacts.py`
- Modify: `tests/unit/test_diagnosis.py`
- Modify: `tests/unit/test_diagnostic_agent.py`
- Modify: `tests/unit/test_evaluation.py`
- Modify: `tests/unit/test_evaluation_runner.py`
- Modify: `docs/requirements.md`
- Modify: `docs/superpowers/plans/2026-08-28-m5-1-model-migration.md`

- [ ] **Step 1: Change current-model test fixtures and requirements assertions to `mimo-v2.5`**

Keep historical qwen and Ollama failure records intact. Update only statements that declare the current M5.1 model, the default endpoint, or the current real-model opt-in. Preserve all frozen limits: 8 model requests, 8 tool calls, 2 output retries, 300 seconds.

- [ ] **Step 2: Run the full non-network verification suite**

Run: `PYTHONUTF8=1 uv run pytest tests/unit -q`

Expected: the existing complete unit suite passes with no model request because the real-model opt-in remains unset.

- [ ] **Step 3: Run static and scope checks**

Run:

```powershell
uv run ruff check .
uv lock --check
git diff --check
rg -n 'qwen3\.5:9b|OLLAMA_ENDPOINT|DIG_RUN_OLLAMA_TESTS' src tests pyproject.toml .env.diagnostic.example
```

Expected: checks pass; remaining qwen/Ollama text is only deliberately retained historical documentation or unrelated local environment guidance.

- [ ] **Step 4: Commit only the migration files**

Run:

```powershell
git add -- src/data_incident_gym/diagnostic_config.py src/data_incident_gym/doctor.py src/data_incident_gym/cli.py .env.diagnostic.example pyproject.toml tests docs/requirements.md docs/superpowers/plans/2026-08-28-m5-1-model-migration.md
git commit -m "feat: migrate diagnostics to mimo v2.5"
```

Expected: no root `AGENT.md`, `README.md`, or `mistake.md`; no `third_party/jaffle_shop`; no artifacts; no push.

## Self-review

- Coverage: Task 1 makes `MIMO_API_KEY` effective without storing its value; Task 2 removes Ollama-only runtime assumptions; Task 3 synchronizes current-model contracts and validates frozen boundaries.
- Placeholders: none; every code change and command has an explicit target.
- Type consistency: `DiagnosticSettings` remains the sole provider configuration seam; `OpenAIChatModel` and `OpenAIProvider` remain unchanged; real-model calls retain an explicit opt-in.

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-28-m5-2-mimo-migration.md`.

1. Subagent-Driven — dispatch a fresh subagent per task and review between tasks.
2. Inline Execution — execute the plan in this session with checkpoints.
