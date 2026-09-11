"""Dynamic-ledger regressions: every model request carries exactly one
authoritative investigation ledger, refreshed from the current kernel state.

Only public, case-neutral surfaces are exercised here; no private scenario data
and no real model provider is involved.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from data_incident_gym.diagnosis import DiagnosisStatus, DiagnosticStrategy
from data_incident_gym.diagnostic_agent import DiagnosisRunner, ModelIdentity
from data_incident_gym.evidence import (
    DbtRunResultsFact,
    EvidenceRecord,
    EvidenceSource,
    EvidenceType,
)
from data_incident_gym.run_context import IncidentBrief

RUN_ID = "c" * 32
MODEL_BASE_URL = "http://127.0.0.1:11434/v1"
LEDGER_HEADER = "CURRENT INVESTIGATION LEDGER"


def _write_run_context(project_root: Path, run_id: str) -> None:
    run_root = project_root / ".dig" / "lab" / "runs" / run_id
    run_root.mkdir(parents=True, exist_ok=True)
    runtime = {
        "schema_version": "p1.runtime.v1",
        "run_id": run_id,
        "dbt_exit_code": 0,
        "artifacts": {
            "manifest": "dbt/target/manifest.json",
            "run_results": "dbt/target/run_results.json",
            "dbt_log": "dbt/logs/dbt.log",
            "schema": "schema.json",
            "profile_snapshot": "profile_snapshot.json",
            "incident_brief": "incident_brief.json",
        },
        "observable_relations": {
            "schema": ["raw_payments"],
            "profile": ["raw_payments"],
            "history": ["raw_payments"],
        },
        "profile_spec_sha256": "b" * 64,
    }
    (run_root / "runtime.json").write_text(json.dumps(runtime), encoding="utf-8")
    brief = IncidentBrief(
        schema_version="incident_brief.v1",
        signal_code="DBT_BUILD_FAILED",
        summary="A pipeline evidence review is requested.",
        subjects=("seed.jaffle_shop.raw_payments", "raw_payments"),
        logical_observed_at=datetime(2026, 8, 30, tzinfo=UTC),
        observations=(),
    )
    (run_root / "incident_brief.json").write_text(brief.model_dump_json(), encoding="utf-8")


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        model_base_url=MODEL_BASE_URL,
        model_name="synthetic-model",
        model_api_key=SimpleNamespace(get_secret_value=lambda: "synthetic-key"),
    )


def _runner_for(
    run_id: str,
    project_root: Path,
    model: FunctionModel,
    tools: object,
    strategy: DiagnosticStrategy = DiagnosticStrategy.DIAGNOSTIC_KERNEL,
) -> DiagnosisRunner:
    return DiagnosisRunner.for_run(
        run_id,
        _settings(),
        strategy,
        project_root,
        model=model,
        tools=tools,  # type: ignore[arg-type]
        model_identity=ModelIdentity("synthetic", "synthetic-model"),
    )


class _KernelTools:
    """Only the run-results read succeeds; every other relation is blocked."""

    def __init__(self, run_id: str) -> None:
        self._run_id = run_id
        self.calls: list[str] = []

    def lineage_node_candidates(self, subjects: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted(subject for subject in subjects if subject.startswith("seed.")))

    def get_dbt_run_results(self, _run_id: str):
        self.calls.append("get_dbt_run_results")
        return (
            EvidenceRecord.create(
                run_id=self._run_id,
                evidence_type=EvidenceType.DBT_RUN_RESULTS,
                source=EvidenceSource.DBT_RUN_RESULTS,
                subject=self._run_id,
                observed_at=datetime(2026, 8, 30, tzinfo=UTC),
                content=DbtRunResultsFact(
                    kind="DBT_RUN_RESULTS",
                    run_id=self._run_id,
                    run_status="FAILED",
                    dbt_exit_code=1,
                    failed_nodes=("model.jaffle_shop.stg_payments",),
                    skipped_nodes=(),
                ),
            ),
        )

    def get_relation_data_profile(self, _relation_name: str):
        self.calls.append("get_relation_data_profile")
        raise AssertionError("blocked relations must never reach the tool layer")

    def get_relation_schema(self, _relation_name: str):
        self.calls.append("get_relation_schema")
        raise AssertionError("blocked relations must never reach the tool layer")


def _ledger_payload(text: str) -> dict[str, object]:
    start = text.index(LEDGER_HEADER)
    return json.loads(text[start:].splitlines()[1])


def _request_surfaces(agent_info: AgentInfo) -> dict[str, object]:
    instructions = agent_info.instructions or ""
    return {
        "instructions": instructions,
        "copies": instructions.count(LEDGER_HEADER),
        "tool_copies": [
            tool.name
            for tool in (*agent_info.function_tools, *agent_info.output_tools)
            if LEDGER_HEADER in (tool.description or "")
        ],
        "tool_names": [tool.name for tool in agent_info.function_tools],
    }


def _four_round_scripted(
    *,
    run_id: str,
    captured: list[dict[str, object]],
) -> FunctionModel:
    """Initial read, one blocked probe, a rejected finalize and a corrected one."""

    binding = {
        "kernel_hypothesis_ids": ["h_loss", "h_decline"],
        "kernel_new_hypotheses": [
            {
                "hypothesis_id": "h_loss",
                "root_cause_code": "SOURCE_PAYMENT_INGESTION_LOSS",
            },
            {
                "hypothesis_id": "h_decline",
                "root_cause_code": "NORMAL_BUSINESS_PAYMENT_DECLINE",
            },
        ],
    }

    def decision(unresolved: list[dict[str, str]]) -> dict[str, object]:
        return {
            "schema_version": "p1.kernel_decision.v1",
            "status": "INSUFFICIENT_EVIDENCE",
            "run_id": run_id,
            "selected_hypothesis_id": None,
            "assessments": [],
            "claims": [],
            "unresolved_evidence": unresolved,
            "summary": "The payment profile is not observable in this run.",
            "recommended_actions": [],
            "confidence": 0.2,
        }

    def scripted(
        messages: list[ModelMessage],
        agent_info: AgentInfo,
    ) -> ModelResponse:
        sent = {
            part.tool_call_id
            for message in messages
            for part in message.parts
            if isinstance(part, ToolCallPart)
        }
        captured.append(_request_surfaces(agent_info))
        if "call-0" not in sent:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "get_dbt_run_results",
                        {"run_id": run_id, **binding},
                        tool_call_id="call-0",
                    )
                ]
            )
        if "call-1" not in sent:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "get_relation_data_profile",
                        {
                            "relation_name": "raw_customers",
                            "kernel_hypothesis_ids": ["h_loss", "h_decline"],
                        },
                        tool_call_id="call-1",
                    )
                ]
            )
        if "final-1" not in sent:
            # Unbound declaration: rejected, so the next request is an output retry.
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        agent_info.output_tools[0].name,
                        decision(
                            [
                                {
                                    "evidence_kind": "RELATION_DATA_PROFILE",
                                    "subject": "raw_customers",
                                    "reason_code": "NOT_OBSERVABLE",
                                }
                            ]
                        ),
                        tool_call_id="final-1",
                    )
                ]
            )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    agent_info.output_tools[0].name,
                    decision(
                        [
                            {
                                "evidence_kind": "RELATION_DATA_PROFILE",
                                "subject": "raw_customers",
                                "reason_code": "RELATION_NOT_ALLOWED",
                            }
                        ]
                    ),
                    tool_call_id="final-2",
                )
            ]
        )

    return FunctionModel(scripted)


@pytest.mark.asyncio
async def test_every_request_carries_one_refreshed_ledger(tmp_path: Path) -> None:
    """Initial, post-tool, post-probe and post-rejection requests each carry
    exactly one ledger that matches the kernel state at that moment."""

    _write_run_context(tmp_path, RUN_ID)
    tools = _KernelTools(RUN_ID)
    captured: list[dict[str, object]] = []
    model = _four_round_scripted(run_id=RUN_ID, captured=captured)
    result = await _runner_for(RUN_ID, tmp_path, model, tools).diagnose()

    assert result.diagnosis.status is DiagnosisStatus.INSUFFICIENT_EVIDENCE
    assert len(captured) == 4, captured
    for surfaces in captured:
        assert surfaces["copies"] == 1, surfaces
        assert surfaces["tool_copies"] == [], surfaces

    budgets = [
        _ledger_payload(str(surfaces["instructions"]))["model_requests_remaining"]
        for surfaces in captured
    ]
    assert budgets == [8, 7, 6, 5], budgets

    initial = _ledger_payload(str(captured[0]["instructions"]))
    assert initial["evidence"] == []
    assert initial["gaps"] == []
    assert initial["hypotheses"] == []

    after_run = _ledger_payload(str(captured[1]["instructions"]))
    assert [item["hypothesis_id"] for item in after_run["hypotheses"]] == [
        "h_loss",
        "h_decline",
    ]
    assert len(after_run["evidence"]) == 1
    assert after_run["evidence"][0]["evidence_type"] == "DBT_RUN_RESULTS"

    after_probe = _ledger_payload(str(captured[2]["instructions"]))
    blocked = next(gap for gap in after_probe["gaps"] if gap["status"] == "BLOCKED")
    assert blocked["error_code"] == "RELATION_NOT_ALLOWED"
    assert blocked["subject"] == "raw_customers"

    # The rejected finalize does not change kernel state, so the retry request
    # sees the same accepted facts and a strictly smaller budget.
    after_rejection = _ledger_payload(str(captured[3]["instructions"]))
    assert after_rejection["evidence"] == after_probe["evidence"]
    assert after_rejection["gaps"] == after_probe["gaps"]
    assert after_rejection["model_requests_remaining"] == 5


@pytest.mark.asyncio
async def test_ledger_is_not_reused_across_runs(tmp_path: Path) -> None:
    """Two runs in the same project root never share ledger state."""

    first_id, second_id = "1" * 32, "2" * 32
    _write_run_context(tmp_path, first_id)
    _write_run_context(tmp_path, second_id)

    captured: list[dict[str, object]] = []
    first_model = _four_round_scripted(run_id=first_id, captured=captured)
    first_result = await _runner_for(
        first_id, tmp_path, first_model, _KernelTools(first_id)
    ).diagnose()
    first_ledgers = [_ledger_payload(str(item["instructions"])) for item in captured]

    captured.clear()
    second_model = _four_round_scripted(run_id=second_id, captured=captured)
    second_result = await _runner_for(
        second_id, tmp_path, second_model, _KernelTools(second_id)
    ).diagnose()
    second_ledgers = [_ledger_payload(str(item["instructions"])) for item in captured]

    assert first_result.diagnosis.status is DiagnosisStatus.INSUFFICIENT_EVIDENCE
    assert second_result.diagnosis.status is DiagnosisStatus.INSUFFICIENT_EVIDENCE
    assert first_ledgers[0]["evidence"] == []
    assert second_ledgers[0]["evidence"] == []
    first_run_evidence = {item["evidence_id"] for item in first_ledgers[1]["evidence"]}
    second_run_evidence = {item["evidence_id"] for item in second_ledgers[1]["evidence"]}
    assert first_run_evidence and second_run_evidence
    assert not first_run_evidence & second_run_evidence


def _ablation_scripted(
    *,
    run_id: str,
    captured: list[dict[str, object]],
) -> FunctionModel:
    """Register two hypotheses through a real read, then finalize insufficient."""

    binding = {
        "kernel_hypothesis_ids": ["h_loss", "h_decline"],
        "kernel_new_hypotheses": [
            {
                "hypothesis_id": "h_loss",
                "root_cause_code": "SOURCE_PAYMENT_INGESTION_LOSS",
            },
            {
                "hypothesis_id": "h_decline",
                "root_cause_code": "NORMAL_BUSINESS_PAYMENT_DECLINE",
            },
        ],
    }

    def scripted(
        messages: list[ModelMessage],
        agent_info: AgentInfo,
    ) -> ModelResponse:
        sent = {
            part.tool_call_id
            for message in messages
            for part in message.parts
            if isinstance(part, ToolCallPart)
        }
        captured.append(_request_surfaces(agent_info))
        if "call-0" not in sent:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "get_dbt_run_results",
                        {"run_id": run_id, **binding},
                        tool_call_id="call-0",
                    )
                ]
            )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    agent_info.output_tools[0].name,
                    {
                        "schema_version": "p1.kernel_decision.v1",
                        "status": "INSUFFICIENT_EVIDENCE",
                        "run_id": run_id,
                        "selected_hypothesis_id": None,
                        "assessments": [],
                        "claims": [],
                        "unresolved_evidence": [
                            {
                                "evidence_kind": "INGESTION_WATERMARK",
                                "subject": "raw_payments",
                                "reason_code": "NOT_OBSERVABLE",
                            }
                        ],
                        "summary": "No decisive evidence was collected.",
                        "recommended_actions": [],
                        "confidence": 0.2,
                    },
                    tool_call_id="final",
                )
            ]
        )

    return FunctionModel(scripted)


@pytest.mark.parametrize(
    ("strategy", "missing_tool"),
    (
        (DiagnosticStrategy.KERNEL_NO_LINEAGE, "get_dbt_lineage"),
        (DiagnosticStrategy.KERNEL_NO_SCHEMA, "get_relation_schema"),
    ),
)
@pytest.mark.asyncio
async def test_ablations_expose_one_ledger_limited_to_enabled_tools(
    tmp_path: Path,
    strategy: DiagnosticStrategy,
    missing_tool: str,
) -> None:
    _write_run_context(tmp_path, RUN_ID)
    captured: list[dict[str, object]] = []
    model = _ablation_scripted(run_id=RUN_ID, captured=captured)
    result = await _runner_for(RUN_ID, tmp_path, model, _KernelTools(RUN_ID), strategy).diagnose()

    assert result.diagnosis.status is DiagnosisStatus.INSUFFICIENT_EVIDENCE
    assert len(captured) == 2
    for surfaces in captured:
        assert surfaces["copies"] == 1
        assert surfaces["tool_copies"] == []
        assert missing_tool not in surfaces["tool_names"]
        ledger = _ledger_payload(str(surfaces["instructions"]))
        # The disabled tool keeps no provable relation in the ledger either.
        assert ledger["provable_relations"].get(missing_tool, []) == []


@pytest.mark.parametrize(
    ("strategy", "expected"),
    (
        (
            DiagnosticStrategy.KERNEL_NO_LINEAGE,
            {
                "get_relation_schema": ["raw_payments"],
                "get_relation_data_profile": ["raw_payments"],
                "get_relation_history": ["raw_payments"],
            },
        ),
        (
            DiagnosticStrategy.KERNEL_NO_SCHEMA,
            {
                "get_relation_schema": [],
                "get_relation_data_profile": ["raw_payments"],
                "get_relation_history": ["raw_payments"],
            },
        ),
    ),
)
@pytest.mark.asyncio
async def test_uncollected_relations_at_runner_level_keeps_other_tools(
    tmp_path: Path,
    strategy: DiagnosticStrategy,
    expected: dict[str, list[str]],
) -> None:
    """The runner wiring decides the projection, so it is asserted here rather
    than only against a hand-built kernel: KERNEL_NO_SCHEMA empties only the
    schema entry, and KERNEL_NO_LINEAGE must leave every relation entry in place
    because it removes lineage tooling, not relation permissions."""

    _write_run_context(tmp_path, RUN_ID)
    captured: list[dict[str, object]] = []
    model = _ablation_scripted(run_id=RUN_ID, captured=captured)
    result = await _runner_for(RUN_ID, tmp_path, model, _KernelTools(RUN_ID), strategy).diagnose()

    assert result.diagnosis.status is DiagnosisStatus.INSUFFICIENT_EVIDENCE
    ledger = _ledger_payload(str(captured[-1]["instructions"]))
    assert ledger["uncollected_relations"] == expected


def test_relation_evidence_type_mapping_covers_every_relation_tool() -> None:
    """The projection maps each relation tool to its evidence type by key, so a
    new relation tool must arrive with its mapping instead of a runtime error."""

    from data_incident_gym.diagnostic_agent import _RELATION_EVIDENCE_TYPES

    kernel = _projection_kernel()

    assert set(_RELATION_EVIDENCE_TYPES) == set(kernel.provable_relations_by_tool())


def test_ledger_explanation_limits_what_blocks_a_listed_relation() -> None:
    """Being listed states a permission, not a guarantee of callability, and the
    explanation may only name the constraints that really block a call. A request
    rejected while being prepared records no fingerprint (only the success path
    and the blocked-relation path do), so the text must not claim that any earlier
    rejection makes a relation unusable."""

    from data_incident_gym.diagnostic_agent import _kernel_state_summary

    kernel = _projection_kernel()
    summary = _kernel_state_summary(kernel, kernel.snapshot(model_requests_used=0))

    assert "not what is callable" in summary
    assert "a spent tool budget" in summary
    assert "already-recorded fingerprint for the same tool and arguments" in summary
    assert "rejected " in summary and "does not by" in summary
    # The over-broad claim the audit flagged must stay gone, and the preparation
    # case must be stated as correctable rather than fatal.
    assert "earlier rejected attempt" not in summary
    assert "does not by itself make the relation unusable" in summary


def _static_scripted(
    *,
    run_id: str,
    captured: list[dict[str, object]],
) -> FunctionModel:
    """One read, then a static INSUFFICIENT diagnosis over the public contract."""

    def scripted(
        messages: list[ModelMessage],
        agent_info: AgentInfo,
    ) -> ModelResponse:
        sent = {
            part.tool_call_id
            for message in messages
            for part in message.parts
            if isinstance(part, ToolCallPart)
        }
        captured.append(_request_surfaces(agent_info))
        if "call-0" not in sent:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "get_dbt_run_results",
                        {"run_id": run_id},
                        tool_call_id="call-0",
                    )
                ]
            )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    agent_info.output_tools[0].name,
                    {
                        "schema_version": "p1.diagnosis.v1",
                        "status": "INSUFFICIENT_EVIDENCE",
                        "run_id": run_id,
                        "root_cause_code": None,
                        "summary": "No decisive evidence was collected.",
                        "affected_assets": [],
                        "evidence_ids": [],
                        "claims": [],
                        "unresolved_evidence": [
                            {
                                "evidence_kind": "INGESTION_WATERMARK",
                                "subject": "raw_payments",
                                "reason_code": "NOT_OBSERVABLE",
                            }
                        ],
                        "recommended_actions": [],
                        "confidence": 0.2,
                    },
                    tool_call_id="final",
                )
            ]
        )

    return FunctionModel(scripted)


@pytest.mark.asyncio
async def test_static_skill_never_receives_a_ledger(tmp_path: Path) -> None:
    _write_run_context(tmp_path, RUN_ID)
    captured: list[dict[str, object]] = []
    result = await _runner_for(
        RUN_ID,
        tmp_path,
        _static_scripted(run_id=RUN_ID, captured=captured),
        _KernelTools(RUN_ID),
        DiagnosticStrategy.STATIC_SKILL,
    ).diagnose()

    assert result.diagnosis.status is DiagnosisStatus.INSUFFICIENT_EVIDENCE
    assert len(captured) == 2
    for surfaces in captured:
        assert surfaces["copies"] == 0
        assert surfaces["tool_copies"] == []


def _projection_kernel():
    from data_incident_gym.diagnostic_kernel import DiagnosticKernel

    return DiagnosticKernel.start(
        run_id=RUN_ID,
        allowed_root_cause_codes=(
            "SOURCE_REQUIRED_FIELD_NULL",
            "TRANSFORMATION_REQUIRED_FIELD_NULL",
        ),
        model_request_limit=8,
        tool_call_limit=8,
        observable_schema_relations=("raw_orders", "raw_payments"),
        observable_profile_relations=("raw_orders", "raw_payments"),
        observable_history_relations=("raw_orders", "raw_payments"),
    )


def _profile_record(relation_name: str):
    from data_incident_gym.evidence import (
        EvidenceRecord,
        EvidenceSource,
        EvidenceType,
        RelationDataProfileFact,
    )
    from data_incident_gym.profiles import RelationProfileSnapshot

    return EvidenceRecord.create(
        run_id=RUN_ID,
        evidence_type=EvidenceType.RELATION_DATA_PROFILE,
        source=EvidenceSource.POSTGRES_PROFILE_SNAPSHOT,
        subject=relation_name,
        observed_at=datetime(2026, 8, 30, tzinfo=UTC),
        content=RelationDataProfileFact(
            kind="RELATION_DATA_PROFILE",
            run_id=RUN_ID,
            relation_name=relation_name,
            profile_spec_version="profile_spec.v1",
            profile_spec_sha256="b" * 64,
            snapshot=RelationProfileSnapshot(
                relation_name=relation_name,
                row_count=0,
                columns=(),
            ),
        ),
    )


def _close_profile_gap(kernel, relation_name: str) -> None:
    from data_incident_gym.diagnostic_kernel import EvidenceGapKind, InvestigationIntent

    prepared = kernel.prepare_tool(
        intent=InvestigationIntent(gap_id="g_profile", gap_kind=EvidenceGapKind.PROFILE_RELATION),
        tool_name="get_relation_data_profile",
        arguments={"relation_name": relation_name},
    )
    kernel.record_tool_result(prepared, (_profile_record(relation_name),))


def _uncollected(kernel) -> dict[str, list[str]]:
    from data_incident_gym.diagnostic_agent import _kernel_state_summary

    payload = json.loads(
        _kernel_state_summary(kernel, kernel.snapshot(model_requests_used=0)).splitlines()[1]
    )
    return payload["uncollected_relations"]


def test_ledger_reports_allowed_but_uncollected_relations() -> None:
    """A relation is listed as uncollected only while that tool's own evidence
    type has no accepted record for it; a profile never clears a history entry."""

    kernel = _projection_kernel()
    # Nothing collected yet: every allowed relation is uncollected for its tool.
    assert _uncollected(kernel) == {
        "get_relation_schema": ["raw_orders", "raw_payments"],
        "get_relation_data_profile": ["raw_orders", "raw_payments"],
        "get_relation_history": ["raw_orders", "raw_payments"],
    }
    _close_profile_gap(kernel, "raw_orders")

    projection = _uncollected(kernel)
    # The profile of raw_orders is collected; its history and schema stay listed.
    assert projection["get_relation_data_profile"] == ["raw_payments"]
    assert projection["get_relation_history"] == ["raw_orders", "raw_payments"]
    assert projection["get_relation_schema"] == ["raw_orders", "raw_payments"]


def test_uncollected_relations_follows_each_ablation() -> None:
    """KERNEL_NO_SCHEMA clears only the schema entry; KERNEL_NO_LINEAGE clears
    no relation entry, because neither ablation removes relation permissions."""

    from data_incident_gym.diagnostic_kernel import DiagnosticKernel

    # Only schema permissions are removed for KERNEL_NO_SCHEMA; lineage tooling is
    # unrelated to relation permissions, so it clears nothing here.
    no_schema = DiagnosticKernel.start(
        run_id=RUN_ID,
        allowed_root_cause_codes=(
            "SOURCE_REQUIRED_FIELD_NULL",
            "TRANSFORMATION_REQUIRED_FIELD_NULL",
        ),
        model_request_limit=8,
        tool_call_limit=8,
        observable_schema_relations=(),
        observable_profile_relations=("raw_orders", "raw_payments"),
        observable_history_relations=("raw_orders", "raw_payments"),
    )
    projection = _uncollected(no_schema)
    assert projection["get_relation_schema"] == []
    assert projection["get_relation_data_profile"] == ["raw_orders", "raw_payments"]
    assert projection["get_relation_history"] == ["raw_orders", "raw_payments"]

    # KERNEL_NO_LINEAGE keeps every relation permission, so nothing is cleared.
    no_lineage = _projection_kernel()
    projection = _uncollected(no_lineage)
    assert projection["get_relation_schema"] == ["raw_orders", "raw_payments"]
    assert projection["get_relation_history"] == ["raw_orders", "raw_payments"]


def test_uncollected_projection_does_not_mutate_kernel_state() -> None:
    """Projecting the ledger is read-only: hypotheses, gaps, fingerprints and
    budget stay identical, and no tool call is issued."""

    from data_incident_gym.diagnostic_agent import _kernel_state_summary

    kernel = _projection_kernel()
    before = kernel.snapshot(model_requests_used=0)
    _kernel_state_summary(kernel, before)
    _kernel_state_summary(kernel, kernel.snapshot(model_requests_used=0))

    assert kernel.snapshot(model_requests_used=0) == before
