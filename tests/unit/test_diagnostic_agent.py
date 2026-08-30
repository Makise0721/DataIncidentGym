from __future__ import annotations

import inspect
import json
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.models.function import FunctionModel

from data_incident_gym.diagnosis import DiagnosticStrategy
from data_incident_gym.diagnostic_agent import (
    BASE_PROMPT,
    KERNEL_PROMPT,
    STATIC_PROMPT,
    TOOL_NAMES,
    DiagnosisBudget,
    DiagnosisRunner,
    ModelIdentity,
    load_strategy_prompt,
)
from data_incident_gym.run_context import IncidentBrief

RUN_ID = "a" * 32


def _write_public_run(project_root: Path) -> None:
    run_root = project_root / ".dig" / "lab" / "runs" / RUN_ID
    run_root.mkdir(parents=True)
    (run_root / "runtime.json").write_text(
        json.dumps(
            {
                "schema_version": "p1.runtime.v1",
                "run_id": RUN_ID,
                "dbt_exit_code": 1,
                "artifacts": {
                    "manifest": "dbt/target/manifest.json",
                    "run_results": "dbt/target/run_results.json",
                    "dbt_log": "dbt/logs/dbt.log",
                    "schema": "schema.json",
                    "profile_snapshot": "profile_snapshot.json",
                    "incident_brief": "incident_brief.json",
                },
                "observable_relations": {"schema": [], "profile": [], "history": []},
                "profile_spec_sha256": "b" * 64,
            }
        ),
        encoding="utf-8",
    )
    (run_root / "incident_brief.json").write_text(
        IncidentBrief(
            schema_version="incident_brief.v1",
            signal_code="DBT_BUILD_FAILED",
            summary="A build failed.",
            subjects=("model.jaffle_shop.stg_payments",),
            logical_observed_at=datetime(2026, 8, 30, tzinfo=UTC),
            observations=(),
        ).model_dump_json(),
        encoding="utf-8",
    )


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        model_base_url="http://127.0.0.1:11434/v1",
        model_name="synthetic-model",
        model_api_key=SimpleNamespace(get_secret_value=lambda: "synthetic-key"),
    )


def test_shared_tool_surface_and_budget_are_policy_neutral(tmp_path: Path) -> None:
    _write_public_run(tmp_path)
    model = FunctionModel(lambda _messages, _info: None)
    static = DiagnosisRunner.for_run(
        RUN_ID,
        _settings(),
        DiagnosticStrategy.STATIC_SKILL,
        tmp_path,
        model=model,
        tools=SimpleNamespace(),
        model_identity=ModelIdentity("synthetic", "synthetic-model"),
    )
    kernel = DiagnosisRunner.for_run(
        RUN_ID,
        _settings(),
        DiagnosticStrategy.DIAGNOSTIC_KERNEL,
        tmp_path,
        model=model,
        tools=SimpleNamespace(),
        model_identity=ModelIdentity("synthetic", "synthetic-model"),
    )

    assert TOOL_NAMES == (
        "get_dbt_run_results",
        "get_dbt_node_error",
        "get_relation_schema",
        "get_dbt_lineage",
        "get_relation_data_profile",
        "get_relation_history",
    )
    assert static.tool_schema_sha256 == kernel.tool_schema_sha256
    assert static.budget == kernel.budget == DiagnosisBudget(8, 8, 2, 300)
    assert static.policy_identity.strategy_prompt_sha256 != (
        kernel.policy_identity.strategy_prompt_sha256
    )
    assert tuple(inspect.signature(static.diagnose).parameters) == ()


def test_static_prompt_is_generic_and_kernel_intent_is_not_a_business_argument() -> None:
    assert load_strategy_prompt(DiagnosticStrategy.STATIC_SKILL) == STATIC_PROMPT
    assert load_strategy_prompt(DiagnosticStrategy.DIAGNOSTIC_KERNEL) == KERNEL_PROMPT
    assert "InvestigationState" not in STATIC_PROMPT
    assert "EvidenceGap" not in STATIC_PROMPT
    assert "schema_type_change" not in STATIC_PROMPT
    assert "incident_case_id" not in STATIC_PROMPT
    assert "gap_id" not in json.dumps(
        {name: {"run_id": "x"} for name in TOOL_NAMES},
        sort_keys=True,
    )
    assert BASE_PROMPT.strip()


def test_kernel_prompt_exposes_the_exact_intent_transport_contract() -> None:
    assert '"schema_version":"p1.kernel_intent.v1"' in KERNEL_PROMPT
    assert '"new_hypotheses":[]' in KERNEL_PROMPT
    assert "LOCATE_FAILURE -> get_dbt_run_results" in KERNEL_PROMPT
    assert "EXPLAIN_FAILURE -> get_dbt_node_error" in KERNEL_PROMPT
    assert "DISCOVER_SOURCE_RELATION -> get_dbt_lineage upstream" in KERNEL_PROMPT
    assert "DISCRIMINATE_SCHEMA -> get_relation_schema" in KERNEL_PROMPT
    assert "MAP_IMPACT -> get_dbt_lineage downstream" in KERNEL_PROMPT
    assert "PROFILE_RELATION -> get_relation_data_profile" in KERNEL_PROMPT
    assert "COMPARE_HISTORY -> get_relation_history" in KERNEL_PROMPT
    assert '"root_cause_code":"SOURCE_SCHEMA_COLUMN_TYPE_CHANGED"' in KERNEL_PROMPT


class _FailingAgent:
    def __init__(self, error: Exception) -> None:
        self._error = error

    @contextmanager
    def parallel_tool_call_execution_mode(self, _: str):
        yield

    async def run(self, *_: object, **__: object) -> None:
        raise self._error


def _static_runner(tmp_path: Path) -> DiagnosisRunner:
    _write_public_run(tmp_path)
    return DiagnosisRunner.for_run(
        RUN_ID,
        _settings(),
        DiagnosticStrategy.STATIC_SKILL,
        tmp_path,
        model=FunctionModel(lambda _messages, _info: None),
        tools=SimpleNamespace(),
        model_identity=ModelIdentity("synthetic", "synthetic-model"),
    )


@pytest.mark.asyncio
async def test_timeout_maps_to_safe_terminal_model_error(tmp_path: Path, monkeypatch) -> None:
    runner = _static_runner(tmp_path)
    monkeypatch.setattr(runner, "_agent", lambda _: _FailingAgent(TimeoutError("secret=never")))

    result = await runner.diagnose()

    assert result.diagnosis.status.value == "MODEL_ERROR"
    assert result.diagnosis.summary == "MODEL_TIMEOUT"
    assert result.trace[-1].event_type == "DIAGNOSIS_TERMINAL"
    assert "secret=never" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_protocol_failure_maps_to_safe_terminal_model_error(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner = _static_runner(tmp_path)
    monkeypatch.setattr(
        runner,
        "_agent",
        lambda _: _FailingAgent(ValueError("provider body secret=never")),
    )

    result = await runner.diagnose()

    assert result.diagnosis.summary == "MODEL_PROTOCOL_ERROR"
    assert result.trace[-1].event_type == "DIAGNOSIS_TERMINAL"
    assert "provider body secret=never" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_tool_budget_exhaustion_is_not_reported_as_request_limit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner = _static_runner(tmp_path)
    monkeypatch.setattr(
        runner,
        "_agent",
        lambda _: _FailingAgent(
            UsageLimitExceeded("The next tool call would exceed the tool_calls_limit of 8")
        ),
    )

    result = await runner.diagnose()

    assert result.diagnosis.summary == "MODEL_TOOL_CALL_LIMIT"
