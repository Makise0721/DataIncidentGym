from __future__ import annotations

import asyncio
import inspect
import json
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic_ai.exceptions import ModelHTTPError, UsageLimitExceeded
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from data_incident_gym.diagnosis import (
    KERNEL_STRATEGIES,
    MODEL_STRATEGIES,
    Diagnosis,
    DiagnosisStatus,
    DiagnosticStrategy,
)
from data_incident_gym.diagnostic_agent import (
    BASE_PROMPT,
    CONTROLLER_PROTOCOL_VERSION,
    KERNEL_PROMPT,
    KERNEL_PROMPT_VERSION,
    NO_TOOL_PROMPT,
    NO_TOOL_PROMPT_VERSION,
    P1_ROOT_CAUSE_CODES,
    STATIC_PROMPT,
    STATIC_PROMPT_VERSION,
    TOOL_NAMES,
    DiagnosisBudget,
    DiagnosisRunner,
    ModelIdentity,
    _kernel_retry_message,
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
    binding_fields = {"kernel_hypothesis_ids", "kernel_new_hypotheses"}
    auto_fields = {"kernel_gap_id", "kernel_gap_kind"}
    static_schemas = {
        item["name"]: set(item["parameters"].get("properties", {}))
        for item in static._tool_schema_payload
    }
    kernel_schemas = {
        item["name"]: set(item["parameters"].get("properties", {}))
        for item in kernel._tool_schema_payload
    }
    assert static.tool_schema_sha256 != kernel.tool_schema_sha256
    assert all(
        not (binding_fields | auto_fields) & fields for fields in static_schemas.values()
    )
    assert all(
        binding_fields <= fields and not (auto_fields & fields)
        for fields in kernel_schemas.values()
    )
    assert static.budget == kernel.budget == DiagnosisBudget(8, 8, 2, 300)
    assert static.policy_identity.strategy_prompt_sha256 != (
        kernel.policy_identity.strategy_prompt_sha256
    )
    assert tuple(inspect.signature(static.diagnose).parameters) == ()


def test_auxiliary_model_strategies_use_only_their_frozen_surfaces(tmp_path: Path) -> None:
    _write_public_run(tmp_path)
    model = FunctionModel(lambda _messages, _info: None)
    runners = {
        strategy: DiagnosisRunner.for_run(
            RUN_ID,
            _settings(),
            strategy,
            tmp_path,
            model=model,
            tools=SimpleNamespace(),
            model_identity=ModelIdentity("synthetic", "synthetic-model"),
        )
        for strategy in MODEL_STRATEGIES
        # The planner deliberately runs through EvidencePlannerRunner; the
        # kernel/static loop would execute a different contract.
        if strategy is not DiagnosticStrategy.EVIDENCE_PLANNER
    }

    assert tuple(runners) == tuple(
        strategy
        for strategy in MODEL_STRATEGIES
        if strategy is not DiagnosticStrategy.EVIDENCE_PLANNER
    )
    with pytest.raises(ValueError, match="EvidencePlannerRunner"):
        DiagnosisRunner.for_run(
            RUN_ID,
            _settings(),
            DiagnosticStrategy.EVIDENCE_PLANNER,
            tmp_path,
            model=model,
            tools=SimpleNamespace(),
            model_identity=ModelIdentity("synthetic", "synthetic-model"),
        )
    assert (
        tuple(item["name"] for item in runners[DiagnosticStrategy.NO_TOOL]._tool_schema_payload)
        == ()
    )
    assert tuple(
        item["name"] for item in runners[DiagnosticStrategy.KERNEL_NO_LINEAGE]._tool_schema_payload
    ) == tuple(name for name in TOOL_NAMES if name != "get_dbt_lineage")
    assert tuple(
        item["name"] for item in runners[DiagnosticStrategy.KERNEL_NO_SCHEMA]._tool_schema_payload
    ) == tuple(name for name in TOOL_NAMES if name != "get_relation_schema")
    assert all(runners[strategy]._tool_schema_payload for strategy in KERNEL_STRATEGIES)
    assert load_strategy_prompt(DiagnosticStrategy.NO_TOOL) == NO_TOOL_PROMPT
    assert runners[DiagnosticStrategy.NO_TOOL].policy_identity.strategy_prompt_version == (
        NO_TOOL_PROMPT_VERSION
    )


def test_static_prompt_is_generic_and_kernel_binding_is_typed_arguments() -> None:
    assert load_strategy_prompt(DiagnosticStrategy.STATIC_SKILL) == STATIC_PROMPT
    assert load_strategy_prompt(DiagnosticStrategy.DIAGNOSTIC_KERNEL) == KERNEL_PROMPT
    assert "InvestigationState" not in STATIC_PROMPT
    assert "EvidenceGap" not in STATIC_PROMPT
    assert "schema_type_change" not in STATIC_PROMPT
    assert "incident_case_id" not in STATIC_PROMPT
    assert "p1.kernel_intent.v1" not in KERNEL_PROMPT
    assert "kernel_gap_id" not in KERNEL_PROMPT
    assert "kernel_gap_kind" not in KERNEL_PROMPT
    assert "never invent gap fields" in KERNEL_PROMPT
    assert "kernel_gap_id" not in STATIC_PROMPT
    assert "kernel_gap_kind" not in STATIC_PROMPT
    assert BASE_PROMPT.strip()


def test_static_prompt_exposes_the_shared_m7_claim_contract() -> None:
    for root_cause_code in (
        "SOURCE_SCHEMA_COLUMN_RENAMED",
        "SOURCE_SCHEMA_COLUMN_TYPE_CHANGED",
        "TRANSFORMATION_COLUMN_CAST_CHANGED",
    ):
        assert root_cause_code in STATIC_PROMPT
    assert "direct failed node" in STATIC_PROMPT
    assert "downstream model assets" in STATIC_PROMPT
    assert (
        "upstream source relations are causal inputs, not affected assets" in STATIC_PROMPT.lower()
    )


def test_both_prompts_expose_the_shared_m11_ontology_and_test_claim_rule() -> None:
    expected = (
        "SOURCE_SCHEMA_COLUMN_RENAMED",
        "SOURCE_SCHEMA_COLUMN_TYPE_CHANGED",
        "TRANSFORMATION_COLUMN_CAST_CHANGED",
        "SOURCE_REQUIRED_FIELD_NULL",
        "TRANSFORMATION_REQUIRED_FIELD_NULL",
        "SOURCE_EXACT_PAYMENT_DUPLICATE",
        "SOURCE_SEMANTIC_PAYMENT_DUPLICATE",
        "LEGITIMATE_SPLIT_PAYMENT",
        "SOURCE_PERMANENT_ORPHAN_PAYMENT",
        "NORMAL_LATE_ARRIVING_ORDER",
        "SOURCE_PAYMENT_INGESTION_LOSS",
        "NORMAL_BUSINESS_PAYMENT_DECLINE",
    )

    assert expected == P1_ROOT_CAUSE_CODES
    assert (KERNEL_PROMPT_VERSION, STATIC_PROMPT_VERSION, CONTROLLER_PROTOCOL_VERSION) == (
        "p1.kernel.v18",
        "p1.static.v5",
        "p1.controller.v19",
    )
    for prompt in (STATIC_PROMPT, KERNEL_PROMPT):
        assert all(code in prompt for code in expected)
        assert "distance-1 upstream model" in prompt
        assert "source profile" in prompt.lower()


def test_both_prompts_expose_successful_payment_anomaly_semantics() -> None:
    for prompt in (STATIC_PROMPT, KERNEL_PROMPT):
        assert "successful dbt run" in prompt
        assert "SOURCE_EXACT_PAYMENT_DUPLICATE" in prompt
        assert "SOURCE_SEMANTIC_PAYMENT_DUPLICATE" in prompt
        assert "LEGITIMATE_SPLIT_PAYMENT" in prompt
        assert "PAYMENT_EVENT_IDENTITY" in prompt
        assert "SOURCE_PERMANENT_ORPHAN_PAYMENT" in prompt
        assert "NORMAL_LATE_ARRIVING_ORDER" in prompt
        assert "orphan_payment_record" not in prompt
        assert "orphan_payment_coupon_a" not in prompt
        assert "orphan_payment_coupon_b" not in prompt


def test_both_prompts_require_history_boundary_for_permanent_orphans() -> None:
    required = (
        "A current payment-to-order relationship violation proves an orphan state",
        "Confirm a permanent orphan only when order history and its watermark",
        "normal-late-arrival alternatives",
    )
    for prompt in (STATIC_PROMPT, KERNEL_PROMPT):
        assert all(fragment in prompt for fragment in required)


def test_kernel_prompt_exposes_the_exact_binding_transport_contract() -> None:
    assert "kernel_hypothesis_ids" in KERNEL_PROMPT
    assert "kernel_new_hypotheses" in KERNEL_PROMPT
    assert "CURRENT INVESTIGATION LEDGER" in KERNEL_PROMPT
    assert "provable_relations" in KERNEL_PROMPT
    assert "The controller allocates" in KERNEL_PROMPT
    assert "never invent gap fields" in KERNEL_PROMPT
    assert "Every opened evidence gap must close" in KERNEL_PROMPT
    assert '"root_cause_code":"SOURCE_SCHEMA_COLUMN_TYPE_CHANGED"' in KERNEL_PROMPT


def test_kernel_prompt_states_confirmed_asset_completeness() -> None:
    """Asset completeness is scoped to CONFIRMED, is checked against the
    accepted evidence rather than the current citations, and keeps the three
    branches apart.

    The branch difference is load-bearing: the project's impact-scope convention
    takes a failed model together with its downstream models, but a failed test
    only by its distance-1 upstream models, and the evaluator compares the asset
    set exactly, so widening either branch produces a failing submission.
    """

    assert "CONFIRMED" in KERNEL_PROMPT
    assert "affected assets" in KERNEL_PROMPT
    # Scoped: an abstention or a healthy verdict is not asked for assets.
    assert "When the decision is CONFIRMED" in KERNEL_PROMPT
    # Checked against accepted evidence, not against whatever the claims cite.
    assert "check the affected assets against the accepted evidence" in KERNEL_PROMPT
    assert "citing fewer\nrecords does not shrink" in KERNEL_PROMPT
    # Failed model: itself plus its downstream models.
    assert "a failed model is an affected asset together with every model its accepted" in (
        KERNEL_PROMPT
    )
    assert "keep it as well" in KERNEL_PROMPT
    # Failed test: distance-1 upstream models only, with exclusions and the
    # binding duty kept inside this branch instead of floating after the list.
    assert "the distance-1 upstream models are affected; bind those model claims to" in (
        KERNEL_PROMPT
    )
    assert "The failed test node and the upstream seed relations are not affected assets" in (
        KERNEL_PROMPT
    )
    assert "do not extend the set" in KERNEL_PROMPT
    # The binding sentence must not survive as its own unqualified paragraph,
    # where it would read as applying to every branch.
    assert "\n\nBind those model claims" not in KERNEL_PROMPT
    # No failed node: sized from the confirmed root cause's source or seed node.
    assert "with no failed node" in KERNEL_PROMPT
    assert "source or seed node the confirmed root cause names" in KERNEL_PROMPT
    # The comparison relation must not become a fault source by accident.
    assert "not every relation named in the incident is a fault source" in KERNEL_PROMPT


def test_kernel_retry_message_carries_provable_relations_for_disallowed_relations() -> None:
    plain = _kernel_retry_message("RELATION_NOT_ALLOWED")
    assert "Currently provable" not in plain

    listed = _kernel_retry_message(
        "RELATION_NOT_ALLOWED",
        provable_relations=("raw_payments", "raw_orders"),
    )
    assert "Currently provable for this tool: raw_payments, raw_orders." in listed

    other = _kernel_retry_message(
        "DUPLICATE_TOOL_CALL",
        provable_relations=("raw_payments",),
    )
    assert "Currently provable" not in other


class _FailingAgent:
    def __init__(self, error: Exception) -> None:
        self._error = error

    @contextmanager
    def parallel_tool_call_execution_mode(self, _: str):
        yield

    async def run(self, *_: object, **__: object) -> None:
        raise self._error


def _static_runner(
    tmp_path: Path,
    model: FunctionModel | None = None,
) -> DiagnosisRunner:
    _write_public_run(tmp_path)
    return DiagnosisRunner.for_run(
        RUN_ID,
        _settings(),
        DiagnosticStrategy.STATIC_SKILL,
        tmp_path,
        model=model or FunctionModel(lambda _messages, _info: None),
        tools=SimpleNamespace(),
        model_identity=ModelIdentity("synthetic", "synthetic-model"),
    )


@pytest.mark.asyncio
async def test_static_model_schema_excludes_controller_generated_error(tmp_path: Path) -> None:
    observed_schema: dict[str, object] = {}

    def capture_schema(_messages: object, agent_info: AgentInfo):
        observed_schema.update(agent_info.output_tools[0].parameters_json_schema)
        raise UsageLimitExceeded("stop after schema capture")

    result = await _static_runner(tmp_path, FunctionModel(capture_schema)).diagnose()

    assert result.diagnosis.summary == "MODEL_REQUEST_LIMIT"
    assert '"MODEL_ERROR"' not in json.dumps(observed_schema, sort_keys=True)


@pytest.mark.asyncio
async def test_kernel_diagnosis_failure_before_run_state_yields_safe_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_public_run(tmp_path)
    runner = DiagnosisRunner.for_run(
        RUN_ID,
        _settings(),
        DiagnosticStrategy.DIAGNOSTIC_KERNEL,
        tmp_path,
        model=FunctionModel(lambda _messages, _info: None),
        tools=SimpleNamespace(),
        model_identity=ModelIdentity("synthetic", "synthetic-model"),
    )

    def broken_kernel(_context: object) -> object:
        raise RuntimeError("construction exploded")

    monkeypatch.setattr(runner, "_kernel", broken_kernel)

    result = await runner.diagnose()

    assert result.diagnosis.status is DiagnosisStatus.MODEL_ERROR
    assert result.diagnosis.summary == "MODEL_RUNTIME_ERROR"
    assert result.metrics.model_requests == 0
    assert result.trace[-1].event_type == "DIAGNOSIS_TERMINAL"


@pytest.mark.asyncio
async def test_diagnose_swallows_owned_client_close_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_public_run(tmp_path)
    runner = DiagnosisRunner.for_run(
        RUN_ID,
        _settings(),
        DiagnosticStrategy.STATIC_SKILL,
        tmp_path,
        model=FunctionModel(lambda _messages, _info: None),
        tools=SimpleNamespace(),
        model_identity=ModelIdentity("synthetic", "synthetic-model"),
    )

    class _BrokenClose:
        async def close(self) -> None:
            raise RuntimeError("teardown exploded")

    runner._owned_model_client = _BrokenClose()  # type: ignore[assignment]

    async def failing_once() -> object:
        raise RuntimeError("once exploded")

    monkeypatch.setattr(runner, "_diagnose_once", failing_once)

    result = await runner.diagnose()

    assert result.diagnosis.status is DiagnosisStatus.MODEL_ERROR
    assert result.diagnosis.summary == "MODEL_RUNTIME_ERROR"


@pytest.mark.asyncio
async def test_static_decision_is_projected_to_public_diagnosis(tmp_path: Path) -> None:
    def return_decision(_messages: object, agent_info: AgentInfo) -> ModelResponse:
        return ModelResponse(
            parts=[
                ToolCallPart(
                    agent_info.output_tools[0].name,
                    {
                        "status": "INSUFFICIENT_EVIDENCE",
                        "run_id": RUN_ID,
                        "root_cause_code": None,
                        "summary": "More evidence is required.",
                        "affected_assets": [],
                        "evidence_ids": [],
                        "claims": [],
                        "unresolved_evidence": [
                            {
                                "evidence_kind": "RELATION_SCHEMA",
                                "subject": "raw_orders",
                                "reason_code": "NOT_OBSERVABLE",
                            }
                        ],
                        "recommended_actions": ["Collect relation schema evidence."],
                        "confidence": 0.2,
                    },
                    tool_call_id="final",
                )
            ]
        )

    result = await _static_runner(tmp_path, FunctionModel(return_decision)).diagnose()

    persisted = Diagnosis.model_validate(result.diagnosis.model_dump(mode="json"))
    assert persisted == result.diagnosis


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

def _invalid_static_payload(missing_summary: bool = True) -> dict[str, object]:
    payload: dict[str, object] = {
        "status": "INSUFFICIENT_EVIDENCE",
        "run_id": RUN_ID,
        "root_cause_code": None,
        "affected_assets": [],
        "evidence_ids": [],
        "claims": [],
        "unresolved_evidence": [],
        "recommended_actions": [],
        "confidence": 0.2,
    }
    if not missing_summary:
        payload["summary"] = "More evidence is required."
    return payload


def _protocol_event(result) -> object:
    return next(
        (event for event in result.trace if getattr(event, "event_type", None) == "MODEL_PROTOCOL"),
        None,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_factory, expected_transport",
    [
        (lambda: ModelHTTPError(429, "DO_NOT_LEAK", {"secret": "x"}), "transport=HTTP_429"),
        (lambda: ModelHTTPError(500, "DO_NOT_LEAK", None), "transport=HTTP_500"),
    ],
)
async def test_provider_failure_records_sanitized_transport_diagnostic(
    tmp_path: Path,
    error_factory,
    expected_transport: str,
) -> None:
    def fail(_messages: object, _info: AgentInfo) -> ModelResponse:
        raise error_factory()

    result = await _static_runner(tmp_path, FunctionModel(fail)).diagnose()

    assert result.diagnosis.status is DiagnosisStatus.MODEL_ERROR
    event = _protocol_event(result)
    assert event is not None
    assert event.category == "PROVIDER_PROTOCOL_FAILURE"
    assert event.stage == "PROVIDER_RESPONSE"
    assert event.error_type == "MODEL_API_ERROR"
    assert event.transport_diagnostic == expected_transport
    dumped = result.model_dump_json()
    assert "DO_NOT_LEAK" not in dumped
    assert "secret" not in dumped


@pytest.mark.asyncio
async def test_connection_error_through_real_sdk_records_transport_diagnostic(
    tmp_path: Path,
) -> None:
    """A real transport failure propagates as ModelAPIError and classifies.

    This exercises the production propagation path: pydantic-ai's OpenAI model
    layer wraps connection errors into ModelAPIError, which the agent graph
    re-raises as the terminating exception (the same shape the v29 archives
    recorded as MODEL_API_ERROR)."""

    import httpx2
    from openai import AsyncOpenAI
    from pydantic_ai.models import override_allow_model_requests
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    def handler(request):
        raise httpx2.ConnectError("DO_NOT_LEAK", request=request)

    async def exercise():
        _write_public_run(tmp_path)
        async with (
            httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http,
            AsyncOpenAI(
                api_key="DO_NOT_LEAK",
                base_url="https://example.invalid/v1",
                http_client=http,
                max_retries=0,
            ) as client,
        ):
            runner = DiagnosisRunner.for_run(
                RUN_ID,
                _settings(),
                DiagnosticStrategy.STATIC_SKILL,
                tmp_path,
                model=OpenAIChatModel(
                    "test", provider=OpenAIProvider(openai_client=client)
                ),
                tools=SimpleNamespace(),
                model_identity=ModelIdentity("openai-compatible", "test"),
            )
            with override_allow_model_requests(True):
                return await runner.diagnose()

    result = await exercise()

    assert result.diagnosis.status is DiagnosisStatus.MODEL_ERROR
    event = _protocol_event(result)
    assert event is not None
    assert event.category == "PROVIDER_PROTOCOL_FAILURE"
    assert event.error_type == "MODEL_API_ERROR"
    assert event.transport_diagnostic == "transport=CONNECTION_ERROR"
    assert "DO_NOT_LEAK" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_output_validation_failure_carries_no_transport_diagnostic(
    tmp_path: Path,
) -> None:
    def return_bad(_messages: object, agent_info: AgentInfo) -> ModelResponse:
        return ModelResponse(
            parts=[
                ToolCallPart(
                    agent_info.output_tools[0].name,
                    _invalid_static_payload(missing_summary=True),
                    tool_call_id="final",
                )
            ]
        )

    result = await _static_runner(tmp_path, FunctionModel(return_bad)).diagnose()

    event = _protocol_event(result)
    assert event is not None
    assert event.category == "OUTPUT_SCHEMA_REJECTED"
    assert event.transport_diagnostic is None


def test_protocol_trace_event_reads_legacy_payloads_without_transport() -> None:
    from data_incident_gym.diagnosis import ModelProtocolTraceEvent

    legacy = {
        "event_type": "MODEL_PROTOCOL",
        "stage": "PROVIDER_RESPONSE",
        "tool_name": None,
        "category": "PROVIDER_PROTOCOL_FAILURE",
        "error_type": "MODEL_API_ERROR",
        "error_origin": "PROVIDER",
    }
    event = ModelProtocolTraceEvent.model_validate(legacy)
    assert event.transport_diagnostic is None

    with_transport = ModelProtocolTraceEvent.model_validate(
        {**legacy, "transport_diagnostic": "transport=HTTP_429"}
    )
    assert with_transport.transport_diagnostic == "transport=HTTP_429"
    assert "transport_diagnostic" in with_transport.model_dump_json()


@pytest.mark.asyncio
async def test_output_schema_rejection_records_safe_field_path(tmp_path: Path) -> None:
    """A missing field is classified and located without leaking values."""

    def return_bad(_messages: object, agent_info: AgentInfo) -> ModelResponse:
        return ModelResponse(
            parts=[
                ToolCallPart(
                    agent_info.output_tools[0].name,
                    _invalid_static_payload(missing_summary=True),
                    tool_call_id="final",
                )
            ]
        )

    result = await _static_runner(tmp_path, FunctionModel(return_bad)).diagnose()

    assert result.diagnosis.status is DiagnosisStatus.MODEL_ERROR
    event = _protocol_event(result)
    assert event is not None
    assert event.category == "OUTPUT_SCHEMA_REJECTED"
    assert event.stage == "OUTPUT_SCHEMA_VALIDATION"
    assert event.error_loc == ("summary",)
    assert event.error_kind == ("missing",)
    dumped = result.model_dump_json()
    assert "Field required" not in dumped
    assert '"input"' not in dumped
    assert result.metrics.model_requests == 3


@pytest.mark.asyncio
async def test_kernel_output_enum_rejection_is_classified_and_located(
    tmp_path: Path,
) -> None:
    """A submitted status surfaces the field path and error kind.

    The terminal status is chosen by the output tool, so a payload that still
    carries one is rejected on the field; the location names it."""

    _write_public_run(tmp_path)

    def return_bad(_messages: object, agent_info: AgentInfo) -> ModelResponse:
        return ModelResponse(
            parts=[
                ToolCallPart(
                    agent_info.output_tools[0].name,
                    {
                        "schema_version": "p1.kernel_decision.v1",
                        "status": "MAYBE",
                        "run_id": RUN_ID,
                        "summary": "Invalid status.",
                        "recommended_actions": [],
                        "confidence": 0.2,
                    },
                    tool_call_id="final",
                )
            ]
        )

    runner = DiagnosisRunner.for_run(
        RUN_ID,
        _settings(),
        DiagnosticStrategy.DIAGNOSTIC_KERNEL,
        tmp_path,
        model=FunctionModel(return_bad),
        tools=SimpleNamespace(),
        model_identity=ModelIdentity("synthetic", "synthetic-model"),
    )
    result = await runner.diagnose()

    assert result.diagnosis.status is DiagnosisStatus.MODEL_ERROR
    event = _protocol_event(result)
    assert event is not None
    assert event.category == "OUTPUT_SCHEMA_REJECTED"
    assert event.error_loc == ("status",)
    assert event.error_kind == ("extra_forbidden",)
    assert event.error_reason == ("UNEXPECTED_DECISION_FIELD",)


@pytest.mark.asyncio
async def test_kernel_cross_field_contract_rejection_records_error_kind(
    tmp_path: Path,
) -> None:
    """A decision that violates a cross-entry validator is classified safely.

    The submission is structurally valid; the duplicated claim pair is only
    visible once the decision is built, so the reason code is what identifies it.
    """

    _write_public_run(tmp_path)

    def return_bad(_messages: object, agent_info: AgentInfo) -> ModelResponse:
        claim = {
            "kind": "ROOT_CAUSE",
            "value": "SOURCE_REQUIRED_FIELD_NULL",
            "evidence_ids": ["ev_" + "0" * 64],
        }
        return ModelResponse(
            parts=[
                ToolCallPart(
                    agent_info.output_tools[1].name,
                    {
                        "schema_version": "p1.kernel_decision.v1",
                        "run_id": RUN_ID,
                        "selected_hypothesis_id": "h_loss",
                        "assessments": [],
                        "claims": [claim, dict(claim)],
                        "summary": "The same claim kind and value twice.",
                        "recommended_actions": [],
                        "confidence": 0.9,
                    },
                    tool_call_id="final",
                )
            ]
        )

    runner = DiagnosisRunner.for_run(
        RUN_ID,
        _settings(),
        DiagnosticStrategy.DIAGNOSTIC_KERNEL,
        tmp_path,
        model=FunctionModel(return_bad),
        tools=SimpleNamespace(),
        model_identity=ModelIdentity("synthetic", "synthetic-model"),
    )
    result = await runner.diagnose()

    assert result.diagnosis.status is DiagnosisStatus.MODEL_ERROR
    event = _protocol_event(result)
    assert event is not None
    assert event.category == "OUTPUT_SCHEMA_REJECTED"
    assert event.error_kind == ("value_error",)
    assert event.error_reason == ("CLAIM_VALUES_DUPLICATED",)


@pytest.mark.asyncio
async def test_output_schema_rejection_recovers_after_retry(tmp_path: Path) -> None:
    """The model corrects the output on the first retry and diagnosis completes."""

    def scripted(messages: list[object], agent_info: AgentInfo) -> ModelResponse:
        invalid_attempts = sum(
            isinstance(part, ToolCallPart) and part.tool_name == agent_info.output_tools[0].name
            for message in messages
            for part in message.parts
        )
        payload = _invalid_static_payload(missing_summary=invalid_attempts == 0)
        if invalid_attempts > 0:
            payload["unresolved_evidence"] = [
                {
                    "evidence_kind": "RELATION_SCHEMA",
                    "subject": "raw_orders",
                    "reason_code": "NOT_OBSERVABLE",
                }
            ]
        return ModelResponse(
            parts=[
                ToolCallPart(
                    agent_info.output_tools[0].name,
                    payload,
                    tool_call_id="final",
                )
            ]
        )

    result = await _static_runner(tmp_path, FunctionModel(scripted)).diagnose()

    assert result.diagnosis.status is DiagnosisStatus.INSUFFICIENT_EVIDENCE
    assert _protocol_event(result) is None
    assert result.metrics.model_requests == 2


def test_schema_prompt_keeps_insufficient_bound_to_indistinguishability() -> None:
    """The transformation-definition absence alone must not force INSUFFICIENT."""

    flat = " ".join(KERNEL_PROMPT.split())
    assert (
        "When the target relation schema is unavailable, or the available public "
        "evidence still cannot distinguish a source change from a transformation cast"
        in flat
    )
    assert "may still be confirmable" in flat
    assert "or the transformation definition is not observable, keep both source-change" not in flat


@pytest.mark.asyncio
async def test_terminal_schema_error_attributes_current_round_not_previous(
    tmp_path: Path,
) -> None:
    """Error A then error B: the terminal trace must report B, not stale A."""

    def scripted(
        messages: list[object],
        agent_info: AgentInfo,
    ) -> ModelResponse:
        attempts = sum(
            isinstance(part, ToolCallPart)
            for message in messages
            for part in message.parts
        )
        payload = _invalid_static_payload(missing_summary=attempts < 2)
        if attempts >= 2:
            payload["status"] = "MAYBE"
        return ModelResponse(
            parts=[
                ToolCallPart(
                    agent_info.output_tools[0].name,
                    payload,
                    tool_call_id="final",
                )
            ]
        )

    result = await _static_runner(tmp_path, FunctionModel(scripted)).diagnose()

    assert result.diagnosis.status is DiagnosisStatus.MODEL_ERROR
    event = _protocol_event(result)
    assert event is not None
    assert event.category == "OUTPUT_SCHEMA_REJECTED"
    assert event.error_loc == ("status",)
    assert event.error_kind == ("literal_error",)
    assert "summary" not in event.error_loc
    assert "missing" not in event.error_kind


@pytest.mark.asyncio
async def test_unknown_output_field_key_never_reaches_persisted_trace(
    tmp_path: Path,
) -> None:
    """extra_forbidden keys supplied by the model are replaced by a marker."""

    secret_key = "PRIVATE_CONTENT_IN_UNEXPECTED_FIELD"

    def return_bad(_messages: object, agent_info: AgentInfo) -> ModelResponse:
        payload = _invalid_static_payload(missing_summary=False)
        payload[secret_key] = "secret-value-must-not-persist"
        return ModelResponse(
            parts=[
                ToolCallPart(
                    agent_info.output_tools[0].name,
                    payload,
                    tool_call_id="final",
                )
            ]
        )

    result = await _static_runner(tmp_path, FunctionModel(return_bad)).diagnose()

    assert result.diagnosis.status is DiagnosisStatus.MODEL_ERROR
    event = _protocol_event(result)
    assert event is not None
    assert event.category == "OUTPUT_SCHEMA_REJECTED"
    assert event.error_kind == ("extra_forbidden",)
    assert event.error_loc == ("<unknown-field>",)
    dumped = result.model_dump_json()
    assert secret_key not in dumped
    assert "secret-value-must-not-persist" not in dumped


def test_safe_validation_details_filters_unknown_names_and_bounds_length() -> None:
    from data_incident_gym.diagnostic_agent import (
        _MAX_LOC_SEGMENT_CHARS,
        _MAX_LOC_TOTAL_CHARS,
        _SAFE_ERROR_KINDS,
        _UNKNOWN_FIELD_MARKER,
        _safe_validation_details,
    )
    from data_incident_gym.diagnostic_contracts import KernelDecision

    long_key = "PRIVATE_" + "k" * 300
    payload = {
        "schema_version": "p1.kernel_decision.v1",
        "status": "CONFIRMED",
        "run_id": RUN_ID,
        "selected_hypothesis_id": "h_type",
        "assessments": [],
        "claims": [
            {
                "kind": "ROOT_CAUSE",
                "value": "SOURCE_SCHEMA_COLUMN_TYPE_CHANGED",
                "evidence_ids": ["ev_" + "a" * 60],
                long_key: "secret-nested",
            }
        ],
        "unresolved_evidence": [],
        "summary": "x",
        "recommended_actions": [],
        "confidence": 0.9,
    }
    with pytest.raises(Exception) as caught:
        KernelDecision.model_validate(payload)
    from pydantic import ValidationError

    assert isinstance(caught.value, ValidationError)
    locs, kinds = _safe_validation_details(caught.value)

    assert long_key not in locs
    assert "secret-nested" not in locs
    assert all(len(segment) <= _MAX_LOC_SEGMENT_CHARS for segment in locs)
    assert all(kind in _SAFE_ERROR_KINDS for kind in kinds)
    assert sum(len(segment) for segment in locs) <= _MAX_LOC_TOTAL_CHARS
    # The nested unknown key collapses to the fixed marker.
    assert _UNKNOWN_FIELD_MARKER in locs
    assert "claims" in locs

@pytest.mark.parametrize('strategy', [
    DiagnosticStrategy.STATIC_SKILL, DiagnosticStrategy.DIAGNOSTIC_KERNEL,
])
def test_settings_model_uses_the_verified_thinking_gateway_profile(tmp_path, strategy):
    from data_incident_gym.diagnostic_config import DiagnosticSettings

    _write_public_run(tmp_path)
    runner = DiagnosisRunner.for_run(
        RUN_ID, DiagnosticSettings(
            _env_file=None, model_name='deepseek/deepseek-v4.1-flash',
            model_base_url='https://api.commandcode.ai/provider/v1',
        ), strategy, tmp_path, tools=SimpleNamespace(),
    )
    try:
        assert runner._model.profile['openai_supports_tool_choice_required'] is False
        assert runner._owned_model_client.max_retries == 0
    finally:
        asyncio.run(runner._owned_model_client.close())
