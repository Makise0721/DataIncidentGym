"""Joint regression for the model-interface change: a single FunctionModel run
that retransmits an identical hypothesis declaration on a second independent
query, then confirms the ledger stays single and the registry stays unique."""

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
    RelationDataProfileFact,
)
from data_incident_gym.profiles import (
    DuplicateProfileFact,
    RelationProfileSnapshot,
)
from data_incident_gym.run_context import IncidentBrief

RUN_ID = "7" * 32

MODEL_BASE_URL = "http://127.0.0.1:11434/v1"
LEDGER_HEADER = "CURRENT INVESTIGATION LEDGER"

HYPOTHESES = [
    {"hypothesis_id": "h_exact_dup", "root_cause_code": "SOURCE_EXACT_PAYMENT_DUPLICATE"},
    {"hypothesis_id": "h_legit", "root_cause_code": "LEGITIMATE_SPLIT_PAYMENT"},
]


def _write_context(project_root: Path) -> None:
    run_root = project_root / ".dig" / "lab" / "runs" / RUN_ID
    run_root.mkdir(parents=True, exist_ok=True)
    runtime = {
        "schema_version": "p1.runtime.v1",
        "run_id": RUN_ID,
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
        signal_code="DBT_BUILD_SUCCEEDED_WITH_ALERT",
        summary="A duplicate payment alert is under review.",
        subjects=("raw_payments",),
        logical_observed_at=datetime(2026, 8, 30, tzinfo=UTC),
        observations=(
            {
                "kind": "PAYMENT_DUPLICATE_ALERT",
                "subject": "raw_payments",
                "value": "24",
            },
        ),
    )
    (run_root / "incident_brief.json").write_text(brief.model_dump_json(), encoding="utf-8")


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        model_base_url=MODEL_BASE_URL,
        model_name="synthetic-model",
        model_api_key=SimpleNamespace(get_secret_value=lambda: "synthetic-key"),
    )


class _Tools:
    def lineage_node_candidates(self, _subjects: tuple[str, ...]) -> tuple[str, ...]:
        return ("seed.jaffle_shop.raw_payments",)

    def get_dbt_run_results(self, run_id: str):
        return (
            EvidenceRecord.create(
                run_id=run_id,
                evidence_type=EvidenceType.DBT_RUN_RESULTS,
                source=EvidenceSource.DBT_RUN_RESULTS,
                subject=run_id,
                observed_at=datetime(2026, 8, 30, tzinfo=UTC),
                content=DbtRunResultsFact(
                    kind="DBT_RUN_RESULTS",
                    run_id=run_id,
                    run_status="SUCCEEDED",
                    dbt_exit_code=0,
                    failed_nodes=(),
                    skipped_nodes=(),
                ),
            ),
        )

    def get_relation_data_profile(self, relation_name: str):
        return (
            EvidenceRecord.create(
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
                        row_count=116,
                        columns=(),
                        business_key_duplicates=(
                            DuplicateProfileFact(name="id", duplicate_count=2),
                        ),
                    ),
                ),
            ),
        )


def _ledger_payload(text: str) -> dict[str, object]:
    start = text.index(LEDGER_HEADER)
    return json.loads(text[start:].splitlines()[1])


@pytest.mark.asyncio
async def test_identical_redeclaration_runs_two_queries_with_one_ledger(
    tmp_path: Path,
) -> None:
    """One response batches two independent calls that both re-send the same
    declarations; the second query still runs, the registry stays unique and
    every request carries a single ledger."""

    _write_context(tmp_path)
    captured: list[dict[str, object]] = []
    binding = {
        "kernel_hypothesis_ids": ["h_exact_dup", "h_legit"],
        "kernel_new_hypotheses": HYPOTHESES,
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
        captured.append(
            {
                "instructions": agent_info.instructions or "",
                "tool_copies": [
                    tool.name
                    for tool in (*agent_info.function_tools, *agent_info.output_tools)
                    if LEDGER_HEADER in (tool.description or "")
                ],
            }
        )
        if "batch-0" not in sent:
            # Both calls in this response re-send the same declarations.
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "get_dbt_run_results",
                        {"run_id": RUN_ID, **binding},
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
                        "status": "INSUFFICIENT_EVIDENCE",
                        "run_id": RUN_ID,
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
                        "summary": "Synthetic joint-interface terminal.",
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
        model=FunctionModel(scripted),
        tools=_Tools(),  # type: ignore[arg-type]
        model_identity=ModelIdentity("synthetic", "synthetic-model"),
    )
    result = await runner.diagnose()

    # The second call ran: the duplicate declaration did not block it. The final
    # decision is rejected for the placeholder citation ids, which is the point
    # here: the interface test asserts registry and ledger shape, not the answer.
    assert result.metrics.successful_tool_calls == 2
    assert result.metrics.tool_call_attempts == 2
    assert result.diagnosis.status is DiagnosisStatus.INSUFFICIENT_EVIDENCE
    assert [gap.gap_kind.value for gap in result.kernel_state.gaps] == [
        "LOCATE_FAILURE",
        "PROFILE_RELATION",
    ]
    hypothesis_ids = [item.hypothesis_id for item in result.kernel_state.hypotheses]
    assert hypothesis_ids == ["h_exact_dup", "h_legit"]

    assert len(captured) == 2
    for surfaces in captured:
        instructions = str(surfaces["instructions"])
        assert instructions.count(LEDGER_HEADER) == 1
        assert surfaces["tool_copies"] == []
    second = _ledger_payload(str(captured[1]["instructions"]))
    assert [item["hypothesis_id"] for item in second["hypotheses"]] == [
        "h_exact_dup",
        "h_legit",
    ]
    assert len(second["evidence"]) == 2
