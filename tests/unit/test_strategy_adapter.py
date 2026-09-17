"""T09 unified strategy access protocol tests.

The session is driven through a synthetic backend so every rule is checkable
without a database: allowlist, budget, registration, refusal receipts, final
submission, cancellation, self-reported usage and the built-in facade
equivalence.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from data_incident_gym.diagnosis import DiagnosisStatus, ToolTraceEvent
from data_incident_gym.evidence import EvidenceRecord, RelationNotAllowedError
from data_incident_gym.fixed_rule import FIXED_RULE_TOOL_LIMIT, FixedRuleRunner
from data_incident_gym.run_context import IncidentBrief, ObservableRunContext
from data_incident_gym.strategy_adapter import (
    PROTOCOL_TOOL_ALLOWLIST,
    STRATEGY_PROTOCOL_VERSION,
    FinalSubmission,
    ProtocolBudget,
    ProtocolTools,
    StrategyDeclaration,
    StrategyProtocolError,
    StrategySession,
    ToolRequest,
    same_condition,
)

RUN_ID = "c" * 32
OBSERVED_AT = datetime(2026, 9, 16, tzinfo=UTC)


def _declaration(**overrides: object) -> StrategyDeclaration:
    payload: dict[str, object] = {
        "framework": "pytest",
        "framework_version": "1.0",
        "model_provider": "test",
        "model_name": "deterministic-script",
        "deterministic": True,
        "visible_context": ("incident_brief", "relation_whitelist"),
    }
    payload.update(overrides)
    return StrategyDeclaration.model_validate(payload)


class _StaticTools:
    """Deterministic backend: one record per tool, optional refusal."""

    def __init__(self, record: EvidenceRecord, *, refuse_profile_for: str | None = None) -> None:
        self.record = record
        self.refuse_profile_for = refuse_profile_for
        self.calls: list[str] = []

    def _records(self, name: str) -> tuple[EvidenceRecord, ...]:
        self.calls.append(name)
        return (self.record,)

    def get_dbt_run_results(self, run_id: str) -> tuple[EvidenceRecord, ...]:
        return self._records("get_dbt_run_results")

    def get_dbt_node_error(self, run_id: str, node_id: str) -> tuple[EvidenceRecord, ...]:
        return self._records("get_dbt_node_error")

    def get_dbt_lineage(self, node_id: str, direction: str) -> tuple[EvidenceRecord, ...]:
        return self._records("get_dbt_lineage")

    def get_relation_schema(self, relation_name: str) -> tuple[EvidenceRecord, ...]:
        return self._records("get_relation_schema")

    def get_relation_data_profile(self, relation_name: str) -> tuple[EvidenceRecord, ...]:
        if self.refuse_profile_for is not None and relation_name == self.refuse_profile_for:
            raise RelationNotAllowedError(relation_name)
        return self._records("get_relation_data_profile")

    def get_relation_history(self, relation_name: str) -> tuple[EvidenceRecord, ...]:
        return self._records("get_relation_history")


def _run_record() -> EvidenceRecord:
    from data_incident_gym.evidence import DbtRunResultsFact, EvidenceSource, EvidenceType

    return EvidenceRecord.create(
        run_id=RUN_ID,
        evidence_type=EvidenceType.DBT_RUN_RESULTS,
        source=EvidenceSource.DBT_RUN_RESULTS,
        subject=RUN_ID,
        observed_at=OBSERVED_AT,
        content=DbtRunResultsFact(
            kind="DBT_RUN_RESULTS",
            run_id=RUN_ID,
            run_status="FAILED",
            dbt_exit_code=1,
            failed_nodes=("test.jaffle_shop.orders",),
            skipped_nodes=(),
        ),
    )


def _context() -> ObservableRunContext:
    return ObservableRunContext(
        run_id=RUN_ID,
        artifact_dir=Path("."),
        runtime={"observable_relations": {"profile": ["raw_orders"]}},
        incident_brief=IncidentBrief(
            schema_version="incident_brief.v1",
            signal_code="DBT_TEST_FAILED",
            summary="A dbt test failed.",
            subjects=("test.jaffle_shop.orders",),
            logical_observed_at=OBSERVED_AT,
            observations=(),
        ),
    )


def _session(
    tools: object | None = None,
    *,
    declaration: StrategyDeclaration | None = None,
    allowlist: frozenset[str] = PROTOCOL_TOOL_ALLOWLIST,
) -> StrategySession:
    return StrategySession(
        run_id=RUN_ID,
        tools=tools or _StaticTools(_run_record()),
        context=_context(),
        declaration=declaration or _declaration(),
        allowlist=allowlist,
    )


def _request(tool_name: str, **arguments: str) -> ToolRequest:
    return ToolRequest(request_id=f"req-{tool_name}", tool_name=tool_name, arguments=arguments)


def test_task_context_is_public_only() -> None:
    context = _session().task_context()

    assert context.protocol_version == STRATEGY_PROTOCOL_VERSION
    assert context.run_id == RUN_ID
    assert context.incident_brief.signal_code == "DBT_TEST_FAILED"
    assert context.observable_relations == {"profile": ("raw_orders",)}
    assert set(context.tool_allowlist) == set(PROTOCOL_TOOL_ALLOWLIST)
    assert context.budget == ProtocolBudget()
    assert context.declaration.framework == "pytest"
    # No private scenario fact ever appears in the task context.
    assert "incident_case_id" not in context.model_dump()
    assert "expected" not in context.model_dump()


def test_declaration_of_ungranted_tools_is_rejected() -> None:
    with pytest.raises(StrategyProtocolError, match="DECLARATION_INVALID"):
        _session(declaration=_declaration(extra_tools=("read_private_contract",)))


def test_tool_gate_unknown_and_not_allowlisted() -> None:
    session = _session()

    unknown = session.call_tool(_request("shell_exec", command="ls"))
    assert unknown.accepted is False
    assert unknown.error.code == "UNKNOWN_TOOL"
    assert unknown.tool_calls_used == 0  # rejected at the door, no budget spent

    narrowed = _session(allowlist=frozenset({"get_dbt_run_results"}))
    refused = narrowed.call_tool(_request("get_relation_schema", relation_name="raw_orders"))
    assert refused.accepted is False
    assert refused.error.code == "TOOL_NOT_ALLOWLISTED"
    assert refused.tool_calls_used == 0


def test_tool_arguments_and_run_scope_are_checked() -> None:
    session = _session()

    missing = session.call_tool(_request("get_dbt_lineage", node_id="n"))
    assert missing.accepted is False
    assert missing.error.code == "TOOL_ARGUMENT_INVALID"

    foreign = session.call_tool(_request("get_dbt_run_results", run_id="d" * 32))
    assert foreign.accepted is False
    assert foreign.error.code == "TOOL_ARGUMENT_INVALID"


def test_backend_refusal_keeps_its_real_code() -> None:
    tools = _StaticTools(_run_record(), refuse_profile_for="raw_customers")
    session = _session(tools)

    receipt = session.call_tool(
        _request("get_relation_data_profile", relation_name="raw_customers")
    )

    assert receipt.accepted is False
    assert receipt.error.code == "RELATION_NOT_ALLOWED"
    assert receipt.tool_calls_used == 1
    assert session.registered_evidence_ids() == ()


def test_budget_is_counted_by_the_harness() -> None:
    session = _session()

    receipts = [
        session.call_tool(_request("get_dbt_run_results", run_id=RUN_ID))
        for _ in range(FIXED_RULE_TOOL_LIMIT + 1)
    ]

    assert all(receipt.accepted for receipt in receipts[:FIXED_RULE_TOOL_LIMIT])
    exhausted = receipts[-1]
    assert exhausted.accepted is False
    assert exhausted.error.code == "TOOL_BUDGET_EXHAUSTED"
    assert exhausted.tool_calls_used == FIXED_RULE_TOOL_LIMIT + 1
    # Every accepted call registered the same record; duplicates stay visible.
    assert session.registered_evidence_ids() == (receipts[0].evidence_ids[0],)
    assert all(receipt.duplicate for receipt in receipts[1:FIXED_RULE_TOOL_LIMIT])


def test_submit_cannot_cite_unregistered_evidence() -> None:
    session = _session()
    session.call_tool(_request("get_dbt_run_results", run_id=RUN_ID))

    submission = FinalSubmission(
        status=DiagnosisStatus.CONFIRMED,
        summary="confirmed with an invented citation",
        root_cause_code="SOURCE_REQUIRED_FIELD_NULL",
        confidence=0.9,
        evidence_ids=("ev_" + "f" * 64,),
    )

    receipt = session.submit(submission)
    assert receipt.accepted is False
    assert receipt.error.code == "EVIDENCE_NOT_REGISTERED"
    assert "ev_" in (receipt.error.detail or "")


def test_submit_valid_confirmed_answer_once() -> None:
    from data_incident_gym.diagnosis import AffectedAssetClaim, RootCauseClaim

    session = _session()
    run_receipt = session.call_tool(_request("get_dbt_run_results", run_id=RUN_ID))
    evidence_id = run_receipt.evidence_ids[0]

    submission = FinalSubmission(
        status=DiagnosisStatus.CONFIRMED,
        summary="the run failed",
        root_cause_code="SOURCE_REQUIRED_FIELD_NULL",
        affected_assets=("model.jaffle_shop.orders",),
        confidence=0.8,
        evidence_ids=(evidence_id,),
        claims=(
            RootCauseClaim(
                kind="ROOT_CAUSE",
                root_cause_code="SOURCE_REQUIRED_FIELD_NULL",
                evidence_ids=(evidence_id,),
            ),
            AffectedAssetClaim(
                kind="AFFECTED_ASSET",
                asset="model.jaffle_shop.orders",
                evidence_ids=(evidence_id,),
            ),
        ),
    )

    first = session.submit(submission)
    assert first.accepted is True
    assert first.diagnosis is not None
    assert first.diagnosis.run_id == RUN_ID  # the harness owns the run id
    assert first.diagnosis.status is DiagnosisStatus.CONFIRMED

    again = session.submit(submission)
    assert again.accepted is False
    assert again.error.code == "SUBMISSION_ALREADY_FINAL"

    closed = session.call_tool(_request("get_dbt_run_results", run_id=RUN_ID))
    assert closed.accepted is False
    assert closed.error.code == "SESSION_CLOSED"


def test_cancel_closes_the_session() -> None:
    session = _session()

    receipt = session.cancel("STRATEGY_CANCELLED")

    assert receipt.accepted is True
    assert session.cancellation == "STRATEGY_CANCELLED"
    refused = session.call_tool(_request("get_dbt_run_results", run_id=RUN_ID))
    assert refused.accepted is False
    assert refused.error.code == "SESSION_CLOSED"
    submission = session.submit(
        FinalSubmission(status=DiagnosisStatus.NO_INCIDENT, summary="late", confidence=0.5)
    )
    assert submission.accepted is False
    assert submission.error.code == "SESSION_CLOSED"


def test_self_reported_usage_is_auxiliary_only() -> None:
    session = _session()
    session.call_tool(_request("get_dbt_run_results", run_id=RUN_ID))

    session.report_usage(model_requests=99, input_tokens=1_000_000)
    snapshot = session.snapshot()

    assert snapshot["tool_call_attempts"] == 1  # the harness count
    assert snapshot["self_reported_usage"] == {"model_requests": 99, "input_tokens": 1_000_000}
    assert "auxiliary" in snapshot["note"]


def test_same_condition_requires_identical_capabilities() -> None:
    base = _session()
    identical = _session(declaration=_declaration())
    different_model = _session(declaration=_declaration(model_name="other-model"))
    different_context = _session(
        declaration=_declaration(visible_context=("incident_brief",))
    )
    narrowed = _session(allowlist=frozenset({"get_dbt_run_results"}))

    assert same_condition(base, identical) is True
    assert same_condition(base, different_model) is False
    assert same_condition(base, different_context) is False
    assert same_condition(base, narrowed) is False


def test_builtin_facade_is_equivalent_to_direct_tools(tmp_path: Path) -> None:
    """T09 acceptance: a built-in runner behind the protocol facade produces the
    same diagnosis, evidence, counters and trace shapes as direct execution."""

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from unit.test_fixed_rule import _context as runner_context
    from unit.test_fixed_rule import _records, _Tools  # noqa: E402

    from data_incident_gym.diagnosis import DiagnosisRunResult  # noqa: E402

    direct_tools = _Tools(_records())
    session = StrategySession(
        run_id="a" * 32,
        tools=_Tools(_records()),
        context=runner_context(tmp_path, profile_relations=("raw_payments",)),
        declaration=_declaration(),
    )

    async def run(tools: object) -> DiagnosisRunResult:
        runner = FixedRuleRunner(
            run_id="a" * 32,
            settings=SimpleNamespace(),
            project_root=tmp_path,
            tools=tools,  # type: ignore[arg-type]
            context=runner_context(tmp_path, profile_relations=("raw_payments",)),
        )
        return await runner.diagnose()

    import asyncio  # noqa: E402

    direct = asyncio.run(run(direct_tools))
    adapted = asyncio.run(run(session.tools_facade()))

    assert adapted.diagnosis == direct.diagnosis
    assert adapted.evidence_records == direct.evidence_records

    def shape(result: DiagnosisRunResult) -> list[object]:
        shaped = []
        for event in result.trace:
            if isinstance(event, ToolTraceEvent):
                shaped.append(
                    (event.tool_name, event.arguments, event.fingerprint, event.evidence_ids,
                     event.error_code)
                )
            else:
                shaped.append(event.event_type)
        return shaped

    assert shape(adapted) == shape(direct)
    direct_metrics = direct.metrics.model_dump(exclude={"elapsed_ms"})
    adapted_metrics = adapted.metrics.model_dump(exclude={"elapsed_ms"})
    assert adapted_metrics == direct_metrics

    # Identical inputs must yield identical checks: run the deterministic
    # evaluator over both results with the same scenario and verification.
    from unit.test_evaluation import _m9_verification  # noqa: E402

    from data_incident_gym.evaluation import DeterministicEvaluator  # noqa: E402
    from data_incident_gym.scenarios import load_scenario_spec  # noqa: E402

    scenario = load_scenario_spec("duplicate_payment_coupon_a")
    verification = _m9_verification("duplicate_payment_coupon_a")
    direct_evaluation = DeterministicEvaluator.evaluate(
        scenario, verification, direct, recovery_succeeded=True
    )
    adapted_evaluation = DeterministicEvaluator.evaluate(
        scenario, verification, adapted, recovery_succeeded=True
    )
    assert adapted_evaluation.status is direct_evaluation.status
    assert adapted_evaluation.failed_check_codes == direct_evaluation.failed_check_codes
    assert adapted_evaluation.checks == direct_evaluation.checks

    # The facade exercised the protocol on every call the runner made.
    snapshot = session.snapshot()
    assert snapshot["tool_call_attempts"] == direct.metrics.tool_call_attempts
    assert snapshot["registered_evidence"] == len(direct.evidence_records)
    assert snapshot["refusals_by_code"] == {}


def test_builtin_facade_preserves_backend_refusals(tmp_path: Path) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from unit.test_fixed_rule import _context as runner_context
    from unit.test_fixed_rule import _records, _Tools  # noqa: E402

    session = StrategySession(
        run_id="a" * 32,
        tools=_Tools(_records(), block_profile=True),
        context=runner_context(tmp_path, profile_relations=("raw_payments",)),
        declaration=_declaration(),
    )
    facade = session.tools_facade()

    with pytest.raises(RelationNotAllowedError):
        facade.get_relation_data_profile("raw_payments")

    snapshot = session.snapshot()
    assert snapshot["refusals_by_code"] == {"RELATION_NOT_ALLOWED": 1}
    assert snapshot["registered_evidence"] == 0


def test_receipt_carries_the_public_evidence_content() -> None:
    session = _session()

    receipt = session.call_tool(_request("get_dbt_run_results", run_id=RUN_ID))

    assert receipt.accepted is True
    assert len(receipt.evidence) == 1
    assert receipt.evidence[0].evidence_id == receipt.evidence_ids[0]
    assert receipt.evidence[0].content.run_status == "FAILED"
    assert receipt.evidence[0].content.failed_nodes == ("test.jaffle_shop.orders",)


def test_invalid_submissions_consume_the_retry_budget() -> None:
    """Audit regression: rejected submissions used to be free, so five invalid
    ones could still be followed by a successful submission."""

    session = _session()
    bad = FinalSubmission(
        status=DiagnosisStatus.CONFIRMED,
        summary="claims without a root claim do not validate",
        root_cause_code="SOURCE_REQUIRED_FIELD_NULL",
        confidence=0.5,
    )

    first = session.submit(bad)
    second = session.submit(bad)
    assert first.error.code == "SUBMISSION_INVALID"
    assert second.error.code == "SUBMISSION_INVALID"

    exhausted = session.submit(bad)
    assert exhausted.accepted is False
    assert exhausted.error.code == "OUTPUT_RETRY_EXHAUSTED"
    assert session.snapshot()["output_retries_used"] == 2


def test_model_request_budget_is_enforced_at_the_gate() -> None:
    session = _session()

    grants = [session.acquire_model_request() for _ in range(8)]
    assert all(error is None for error in grants)

    refused = session.acquire_model_request()
    assert refused is not None
    assert refused.code == "MODEL_REQUEST_BUDGET_EXHAUSTED"
    assert session.snapshot()["model_requests_granted"] == 8

    # Self-reported usage beyond the granted slots is flagged, not trusted.
    session.report_usage(model_requests=9)
    assert session.snapshot()["usage_violation"] is True


def test_deadline_is_enforced_with_the_harness_clock() -> None:
    now = {"t": 0.0}
    session = StrategySession(
        run_id=RUN_ID,
        tools=_StaticTools(_run_record()),
        context=_context(),
        declaration=_declaration(),
        clock=lambda: now["t"],
    )
    assert session.call_tool(_request("get_dbt_run_results", run_id=RUN_ID)).accepted

    now["t"] = 300.5
    late = session.call_tool(_request("get_dbt_run_results", run_id=RUN_ID))
    assert late.accepted is False
    assert late.error.code == "DEADLINE_EXCEEDED"
    refused = session.acquire_model_request()
    assert refused is not None
    assert refused.code == "DEADLINE_EXCEEDED"
    snapshot = session.snapshot()
    assert snapshot["deadline_seconds"] == 300
    assert snapshot["elapsed_seconds"] == 300.5


def test_production_for_run_routes_tools_and_submission_through_the_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Audit regression: the production factory must attach the session itself;
    wrapping it by hand in a test proved nothing about the normal entry point."""

    import asyncio

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from unit.test_fixed_rule import (
        _context as runner_context,
    )
    from unit.test_fixed_rule import (  # noqa: E402
        _records,
        _Tools,
    )

    from data_incident_gym import fixed_rule as fixed_rule_module  # noqa: E402

    monkeypatch.setattr(
        fixed_rule_module,
        "resolve_run_context",
        lambda run_id, project_root=None: runner_context(
            tmp_path, profile_relations=("raw_payments",)
        ),
    )
    monkeypatch.setattr(
        fixed_rule_module.EvidenceTools,
        "for_run",
        classmethod(lambda cls, *args, **kwargs: _Tools(_records())),
    )

    runner = fixed_rule_module.FixedRuleRunner.for_run("a" * 32, SimpleNamespace(), tmp_path)

    assert isinstance(runner._tools, ProtocolTools)
    assert runner.session is not None
    assert runner.session.declaration.deterministic is True

    result = asyncio.run(runner.diagnose())

    assert result.diagnosis.status is DiagnosisStatus.CONFIRMED
    assert runner.session.final_diagnosis == result.diagnosis
    snapshot = runner.session.snapshot()
    assert snapshot["tool_call_attempts"] == result.metrics.tool_call_attempts
    assert snapshot["registered_evidence"] == len(result.evidence_records)
    assert snapshot["submitted"] is True


def test_model_runner_factory_attaches_the_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The model-backed runner's tool calls also pass through the protocol."""

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from pydantic_ai.models.function import FunctionModel
    from unit.test_diagnostic_agent import _settings, _write_public_run
    from unit.test_fixed_rule import _records, _Tools

    from data_incident_gym.diagnostic_agent import (
        DiagnosisRunner,
        DiagnosticStrategy,
        ModelIdentity,
    )
    from data_incident_gym.evidence_tools import EvidenceTools

    _write_public_run(tmp_path)
    monkeypatch.setattr(
        EvidenceTools,
        "for_run",
        classmethod(lambda cls, *args, **kwargs: _Tools(_records())),
    )

    runner = DiagnosisRunner.for_run(
        "a" * 32,
        _settings(),
        DiagnosticStrategy.STATIC_SKILL,
        tmp_path,
        model=FunctionModel(lambda _messages, _info: None),
        model_identity=ModelIdentity("synthetic", "synthetic-model"),
    )

    assert isinstance(runner._tools, ProtocolTools)
    assert runner.session is not None
    assert runner.session.declaration.model_name == "synthetic-model"
    assert runner.session.declaration.model_provider == "synthetic"
    assert runner.session.declaration.deterministic is False


def _model_runner(
    tmp_path: Path,
    script,
    strategy_name: str,
    monkeypatch,
    *,
    run_id: str = "a" * 32,
    kernel_context: bool = False,
):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from pydantic_ai.models.function import FunctionModel
    from unit.test_diagnostic_agent import _settings, _write_public_run
    from unit.test_kernel_interface_joint import _Tools as KernelTools
    from unit.test_kernel_interface_joint import _write_context

    from data_incident_gym.diagnostic_agent import (
        DiagnosisRunner,
        DiagnosticStrategy,
        ModelIdentity,
    )
    from data_incident_gym.evidence_tools import EvidenceTools

    if kernel_context:
        _write_context(tmp_path)
    else:
        _write_public_run(tmp_path)
    monkeypatch.setattr(
        EvidenceTools,
        "for_run",
        classmethod(lambda cls, *args, **kwargs: KernelTools()),
    )
    return DiagnosisRunner.for_run(
        run_id,
        _settings(),
        DiagnosticStrategy(strategy_name),
        tmp_path,
        model=FunctionModel(script),
        model_identity=ModelIdentity("synthetic", "synthetic-model"),
    )


def test_model_runner_normal_terminal_goes_through_the_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Audit regression: the static terminal used to bypass the protocol, so the
    session stayed open and further tool calls were still accepted."""

    import asyncio

    from pydantic_ai.messages import ModelResponse, ToolCallPart

    run_id = "a" * 32

    def scripted(_messages, agent_info):
        return ModelResponse(
            parts=[
                ToolCallPart(
                    agent_info.output_tools[0].name,
                    {
                        "status": "INSUFFICIENT_EVIDENCE",
                        "run_id": run_id,
                        "root_cause_code": None,
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
                        "recommended_actions": [],
                        "summary": "The decisive fact is not observable.",
                        "confidence": 0.2,
                    },
                    tool_call_id="final",
                )
            ]
        )

    runner = _model_runner(tmp_path, scripted, "STATIC_SKILL", monkeypatch)
    result = asyncio.run(runner.diagnose())

    assert result.diagnosis.status is DiagnosisStatus.INSUFFICIENT_EVIDENCE
    assert runner.session is not None
    assert runner.session.final_diagnosis == result.diagnosis
    assert runner.session.cancellation is None
    closed = runner.session.call_tool(
        _request("get_dbt_run_results", run_id=run_id)
    )
    assert closed.accepted is False
    assert closed.error.code == "SESSION_CLOSED"


def test_kernel_normal_terminal_goes_through_the_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from unit.test_kernel_interface_joint import HYPOTHESES
    from unit.test_kernel_interface_joint import RUN_ID as KERNEL_RUN_ID

    run_id = KERNEL_RUN_ID
    binding = {
        "kernel_hypothesis_ids": ["h_exact_dup", "h_legit"],
        "kernel_new_hypotheses": HYPOTHESES,
    }

    def scripted(messages: list[ModelMessage], agent_info) -> ModelResponse:
        sent = {
            part.tool_call_id
            for message in messages
            for part in message.parts
            if isinstance(part, ToolCallPart)
        }
        if "batch-0" not in sent:
            # The kernel needs the decisive profile attempt before a final
            # abstention is accepted; this mirrors the joint-interface exchange.
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "get_dbt_run_results",
                        {"run_id": run_id, **binding},
                        tool_call_id="batch-0",
                    ),
                    ToolCallPart(
                        "get_relation_data_profile",
                        {"relation_name": "raw_payments", **binding},
                        tool_call_id="batch-1",
                    ),
                ]
            )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    agent_info.output_tools[0].name,
                    {
                        "schema_version": "p1.kernel_decision.v1",
                        "run_id": run_id,
                        "assessments": [],
                        "unresolved_evidence": [
                            {
                                "evidence_kind": "INGESTION_WATERMARK",
                                "subject": "raw_payments",
                            }
                        ],
                        "summary": "Kernel abstains on an unobservable boundary.",
                        "recommended_actions": [],
                        "confidence": 0.2,
                    },
                    tool_call_id="final",
                )
            ]
        )

    runner = _model_runner(
        tmp_path,
        scripted,
        "DIAGNOSTIC_KERNEL",
        monkeypatch,
        run_id=KERNEL_RUN_ID,
        kernel_context=True,
    )
    result = asyncio.run(runner.diagnose())

    assert result.diagnosis.status is DiagnosisStatus.INSUFFICIENT_EVIDENCE
    assert runner.session is not None
    assert runner.session.final_diagnosis == result.diagnosis
    closed = runner.session.call_tool(_request("get_dbt_run_results", run_id=run_id))
    assert closed.accepted is False
    assert closed.error.code == "SESSION_CLOSED"


def test_model_runner_failure_terminals_close_the_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Timeouts and runtime failures end the run without an answer, and the
    session must not stay open afterwards."""

    import asyncio

    from pydantic_ai.exceptions import UsageLimitExceeded
    from pydantic_ai.messages import ModelResponse, ToolCallPart

    run_id = "a" * 32

    def limited(_messages, _agent_info) -> ModelResponse:
        raise UsageLimitExceeded("budget spent")

    for strategy_name, expected in (
        ("STATIC_SKILL", "RUN_FAILED"),
        ("DIAGNOSTIC_KERNEL", "RUN_FAILED"),
    ):
        runner = _model_runner(
            tmp_path / strategy_name, limited, strategy_name, monkeypatch
        )
        result = asyncio.run(runner.diagnose())

        assert result.diagnosis.status is DiagnosisStatus.MODEL_ERROR
        assert runner.session is not None
        assert runner.session.cancellation == expected
        assert runner.session.final_diagnosis is None
        closed = runner.session.call_tool(_request("get_dbt_run_results", run_id=run_id))
        assert closed.accepted is False
        assert closed.error.code == "SESSION_CLOSED"

    def broken(_messages, agent_info) -> ModelResponse:
        return ModelResponse(
            parts=[
                ToolCallPart(
                    agent_info.output_tools[0].name,
                    {"schema_version": "p1.kernel_decision.v1", "run_id": run_id},
                    tool_call_id="final",
                )
            ]
        )

    runner = _model_runner(tmp_path, broken, "STATIC_SKILL", monkeypatch)
    result = asyncio.run(runner.diagnose())
    assert result.diagnosis.status is DiagnosisStatus.MODEL_ERROR
    assert runner.session is not None
    assert runner.session.cancellation == "RUN_FAILED"
