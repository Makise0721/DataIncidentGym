"""The receipt-candidate projection: a public fact, never a decision.

The ledger lists, per enabled relation tool, the public relations that tool may
not read and for which no permission receipt is recorded. It is derived only
from public inputs — the tools' own whitelists and the recorded calls — so the
distractors travel with it: nothing here filters by a case's private expectation,
and whether a candidate is decisive stays the model's judgement.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from data_incident_gym.diagnosis import DiagnosticStrategy
from data_incident_gym.diagnostic_agent import (
    DiagnosisRunner,
    ModelIdentity,
    _kernel_state_summary,
    _unreceipted_relation_candidates,
)
from data_incident_gym.diagnostic_contracts import (
    EvidenceGapKind,
    Hypothesis,
    InvestigationIntent,
    KernelError,
)
from data_incident_gym.diagnostic_kernel import DiagnosticKernel
from data_incident_gym.evidence import (
    DbtRunResultsFact,
    EvidenceRecord,
    EvidenceSource,
    EvidenceType,
)
from data_incident_gym.run_context import IncidentBrief

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "evidence_planning"
RUN_ID = "e" * 32
MODEL_BASE_URL = "http://127.0.0.1:11434/v1"
RELATION_TOOLS = (
    "get_relation_schema",
    "get_relation_data_profile",
    "get_relation_history",
)
_ENABLED = frozenset(RELATION_TOOLS)

_HYPOTHESES = (
    Hypothesis(hypothesis_id="h_loss", root_cause_code="SOURCE_PAYMENT_INGESTION_LOSS"),
    Hypothesis(hypothesis_id="h_decline", root_cause_code="NORMAL_BUSINESS_PAYMENT_DECLINE"),
)

# The public difference sets these two cells produce, distractors included.
_EXPECTED = {
    19: [
        {"tool_name": "get_relation_history", "relation_name": "raw_orders"},
        {"tool_name": "get_relation_history", "relation_name": "raw_payments"},
        {"tool_name": "get_relation_schema", "relation_name": "raw_orders"},
    ],
    44: [
        {"tool_name": "get_relation_history", "relation_name": "raw_customers"},
        {"tool_name": "get_relation_history", "relation_name": "raw_payments"},
        {"tool_name": "get_relation_schema", "relation_name": "raw_orders"},
    ],
}


def _inputs(seq: int) -> dict:
    return json.loads((FIXTURES / f"seq{seq}_inputs.json").read_text(encoding="utf-8"))


def _kernel_from_inputs(inputs: dict, **overrides: object) -> DiagnosticKernel:
    relations = inputs["observable_relations"]
    return DiagnosticKernel.start(
        run_id=inputs["source_run_id"],
        allowed_root_cause_codes=(
            "SOURCE_PAYMENT_INGESTION_LOSS",
            "NORMAL_BUSINESS_PAYMENT_DECLINE",
        ),
        model_request_limit=8,
        tool_call_limit=8,
        observable_schema_relations=tuple(relations["schema"]),
        observable_profile_relations=tuple(relations["profile"]),
        observable_history_relations=tuple(relations["history"]),
        incident_subjects=tuple(inputs["incident_subjects"]),
        lineage_node_candidates=tuple(inputs["lineage_node_candidates"]),
        **overrides,  # type: ignore[arg-type]
    )


def _candidates(kernel: DiagnosticKernel, enabled: frozenset[str] = _ENABLED):
    return _unreceipted_relation_candidates(
        kernel,
        kernel.snapshot(model_requests_used=0),
        enabled_tools=enabled,
    )


def _probe(kernel: DiagnosticKernel, tool_name: str, relation: str) -> str:
    """Take one boundary probe and return the receipt it recorded."""

    try:
        kernel.prepare_tool(
            intent=InvestigationIntent(
                gap_id=f"g_{tool_name}_{relation}",
                gap_kind={
                    "get_relation_schema": EvidenceGapKind.DISCRIMINATE_SCHEMA,
                    "get_relation_data_profile": EvidenceGapKind.PROFILE_RELATION,
                    "get_relation_history": EvidenceGapKind.COMPARE_HISTORY,
                }[tool_name],
                new_hypotheses=_HYPOTHESES,
            ),
            tool_name=tool_name,
            arguments={"relation_name": relation},
        )
    except KernelError as error:
        return error.code
    raise AssertionError("the probe was expected to be refused")


# ------------------------------------------------------------------ projection


@pytest.mark.parametrize("seq", sorted(_EXPECTED))
def test_the_public_difference_set_is_projected_with_its_distractors(seq: int) -> None:
    """Both cells produce their own public difference set; the distractors stay
    in the list rather than being filtered by what the case needs."""

    kernel = _kernel_from_inputs(_inputs(seq))

    candidates, omitted = _candidates(kernel)

    assert candidates == _EXPECTED[seq]
    assert omitted == 0
    # A candidate names a tool and a public relation, nothing else.
    assert {key for item in candidates for key in item} == {"tool_name", "relation_name"}


def test_the_projection_follows_public_permissions_not_the_case_for() -> None:
    """Changing only the public whitelists changes the projection: the same cell
    identifier cannot decide it."""

    inputs = _inputs(19)
    inputs["observable_relations"] = {
        "schema": ["raw_orders"],
        "profile": ["raw_payments"],
        "history": ["raw_orders"],
    }
    kernel = _kernel_from_inputs(inputs)

    candidates, omitted = _candidates(kernel)

    assert candidates == [
        {"tool_name": "get_relation_data_profile", "relation_name": "raw_orders"},
        {"tool_name": "get_relation_history", "relation_name": "raw_payments"},
        {"tool_name": "get_relation_schema", "relation_name": "raw_payments"},
    ]
    assert omitted == 0


def test_a_recorded_receipt_leaves_the_candidates_for_that_pair_only() -> None:
    kernel = _kernel_from_inputs(_inputs(19))
    before, _ = _candidates(kernel)

    assert _probe(kernel, "get_relation_history", "raw_orders") == "RELATION_NOT_ALLOWED"
    after, _ = _candidates(kernel)

    assert {"tool_name": "get_relation_history", "relation_name": "raw_orders"} not in after
    # The other history relation and the schema pair are untouched: receipts do
    # not carry across relations or across tools.
    assert after == [
        {"tool_name": "get_relation_history", "relation_name": "raw_payments"},
        {"tool_name": "get_relation_schema", "relation_name": "raw_orders"},
    ]
    assert len(before) == len(after) + 1


def test_a_disabled_tool_contributes_no_candidates() -> None:
    kernel = _kernel_from_inputs(_inputs(19))

    candidates, _ = _candidates(kernel, _ENABLED - {"get_relation_schema"})

    assert all(item["tool_name"] != "get_relation_schema" for item in candidates)
    assert candidates == _EXPECTED[19][:2]


def test_the_candidate_list_is_sorted_capped_and_counted() -> None:
    inputs = _inputs(19)
    # Seven public relations across three tools: 14 unreadable pairs, so the
    # listed twelve are a truncation and the count says how many were left out.
    inputs["observable_relations"] = {
        "schema": ["raw_a", "raw_b", "raw_g"],
        "profile": ["raw_c", "raw_d"],
        "history": ["raw_e", "raw_f"],
    }
    inputs["incident_subjects"] = [f"raw_{name}" for name in "abcdefg"]
    kernel = _kernel_from_inputs(inputs)

    candidates, omitted = _candidates(kernel)

    assert len(candidates) == 12
    assert omitted == 2
    assert candidates == sorted(
        candidates, key=lambda item: (item["tool_name"], item["relation_name"])
    )
    # The truncation keeps the sorted prefix rather than sampling the set.
    assert candidates[0] == {"tool_name": "get_relation_data_profile", "relation_name": "raw_a"}


def test_the_projection_is_read_only() -> None:
    kernel = _kernel_from_inputs(_inputs(44))
    before = kernel.snapshot(model_requests_used=0)

    payload = json.loads(
        _kernel_state_summary(
            kernel,
            before,
            enabled_tools=_ENABLED,
        ).splitlines()[1]
    )

    assert payload["unreceipted_relation_candidates"] == _EXPECTED[44]
    assert kernel.snapshot(model_requests_used=0) == before
    assert kernel.evidence_records == ()


# --------------------------------------------------------------------- runner


def _write_public_run(project_root: Path) -> None:
    run_root = project_root / ".dig" / "lab" / "runs" / RUN_ID
    run_root.mkdir(parents=True, exist_ok=True)
    (run_root / "runtime.json").write_text(
        json.dumps(
            {
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
                    "profile": ["raw_orders"],
                    "history": [],
                },
                "profile_spec_sha256": "b" * 64,
            }
        ),
        encoding="utf-8",
    )
    (run_root / "incident_brief.json").write_text(
        IncidentBrief(
            schema_version="incident_brief.v1",
            signal_code="DBT_BUILD_FAILED",
            summary="A pipeline evidence review is requested.",
            subjects=("raw_payments", "raw_orders"),
            logical_observed_at=datetime(2026, 9, 2, tzinfo=UTC),
            observations=(),
        ).model_dump_json(),
        encoding="utf-8",
    )


class _Tools:
    def get_dbt_run_results(self, run_id: str):
        return (
            EvidenceRecord.create(
                run_id=run_id,
                evidence_type=EvidenceType.DBT_RUN_RESULTS,
                source=EvidenceSource.DBT_RUN_RESULTS,
                subject=run_id,
                observed_at=datetime(2026, 9, 2, tzinfo=UTC),
                content=DbtRunResultsFact(
                    kind="DBT_RUN_RESULTS",
                    run_id=run_id,
                    run_status="FAILED",
                    dbt_exit_code=1,
                    failed_nodes=("model.jaffle_shop.stg_payments",),
                    skipped_nodes=(),
                ),
            ),
        )


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        model_base_url=MODEL_BASE_URL,
        model_name="synthetic-model",
        model_api_key="synthetic-key",
    )


def _ledger_payload(instructions: str) -> dict:
    return json.loads(instructions.splitlines()[1])


@pytest.mark.asyncio
async def test_the_runner_refreshes_the_candidates_once_per_request(tmp_path: Path) -> None:
    """The projection arrives through the real per-request instructions: once per
    request, sorted, and refreshed after a receipt is recorded."""

    _write_public_run(tmp_path)
    captured: list[str] = []

    def scripted(messages: list[ModelMessage], agent_info: AgentInfo) -> ModelResponse:
        captured.append(str(agent_info.instructions))
        sent = {
            part.tool_call_id
            for message in messages
            for part in message.parts
            if isinstance(part, ToolCallPart)
        }
        if "call-0" not in sent:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "get_dbt_run_results",
                        {
                            "run_id": RUN_ID,
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
                        },
                        tool_call_id="call-0",
                    )
                ]
            )
        if "call-probe" not in sent:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "get_relation_schema",
                        {"relation_name": "raw_orders"},
                        tool_call_id="call-probe",
                    )
                ]
            )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    agent_info.output_tools[0].name,
                    {
                        "run_id": RUN_ID,
                        "assessments": [],
                        "unresolved_evidence": [
                            {"evidence_kind": "INGESTION_WATERMARK", "subject": "raw_payments"}
                        ],
                        "summary": "One receipt was recorded.",
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
    await runner.diagnose()

    initial = _ledger_payload(captured[1])
    assert initial["unreceipted_relation_candidates"] == [
        {"tool_name": "get_relation_data_profile", "relation_name": "raw_payments"},
        {"tool_name": "get_relation_history", "relation_name": "raw_orders"},
        {"tool_name": "get_relation_history", "relation_name": "raw_payments"},
        {"tool_name": "get_relation_schema", "relation_name": "raw_orders"},
    ]
    assert initial["unreceipted_relation_candidates_omitted"] == 0
    # One projection per request, and the ledger arrives exactly once.
    assert sum("unreceipted_relation_candidates" in text for text in captured) == len(captured)
    assert all(text.count("CURRENT INVESTIGATION LEDGER") == 1 for text in captured)

    after_receipt = _ledger_payload(captured[2])
    assert after_receipt["unreceipted_relation_candidates"] == [
        {"tool_name": "get_relation_data_profile", "relation_name": "raw_payments"},
        {"tool_name": "get_relation_history", "relation_name": "raw_orders"},
        {"tool_name": "get_relation_history", "relation_name": "raw_payments"},
    ]
