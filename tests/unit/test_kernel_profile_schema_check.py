"""Kernel profile-then-schema corroboration slice (2026-09-27 design, section 5.1).

The prompt-only strategy change asks the model to check the same relation's
schema before confirming an anomaly from an accepted profile. These tests
pin the mechanism and identity boundaries: the version bump, the unchanged
non-kernel surfaces, the ledger semantics that make the playbook executable,
and one runner-level scripted pass over the real kernel path.

FunctionModel scripts prove only that the agreed path is executable with
correct permissions, counts and bookkeeping; they never prove a real model
would make these choices.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from data_incident_gym.diagnosis import (
    DiagnosisStatus,
    DiagnosticStrategy,
    EvidenceGateTraceEventV2,
)
from data_incident_gym.diagnostic_agent import (
    KERNEL_PROMPT,
    STATIC_PROMPT,
    DiagnosisRunner,
    ModelIdentity,
    _uncollected_relations,
    policy_surface_for_strategy,
)
from data_incident_gym.diagnostic_contracts import KernelError
from data_incident_gym.diagnostic_kernel import (
    DiagnosticKernel,
    EvidenceGapKind,
    EvidenceGapStatus,
    InvestigationIntent,
)
from data_incident_gym.evaluation import EvaluationStatus
from data_incident_gym.evidence import EvidenceRecord
from data_incident_gym.run_context import IncidentBrief

PROMPTS_DIR = (
    Path(__file__).resolve().parents[2] / "src" / "data_incident_gym" / "prompts"
)
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "kernel_rerun"
RUN_ID = "d" * 32
SILENT_RUN_ID = "870de53cf8e67a39cbcc97c0d42884e7"
MODEL_BASE_URL = "http://127.0.0.1:11434/v1"
LEDGER_HEADER = "CURRENT INVESTIGATION LEDGER"
CODES = (
    "SOURCE_PAYMENT_INGESTION_LOSS",
    "NORMAL_BUSINESS_PAYMENT_DECLINE",
)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ----------------------------------------------------------- identity surface


@pytest.mark.parametrize(
    "strategy",
    (
        DiagnosticStrategy.DIAGNOSTIC_KERNEL,
        DiagnosticStrategy.KERNEL_NO_LINEAGE,
        DiagnosticStrategy.KERNEL_NO_SCHEMA,
    ),
)
def test_kernel_family_shares_the_v19_prompt_and_its_file_digest(
    strategy: DiagnosticStrategy,
) -> None:
    surface = policy_surface_for_strategy(strategy)
    assert surface.strategy_prompt_version == "p1.kernel.v19"
    assert surface.strategy_prompt_sha256 == _sha256(
        (PROMPTS_DIR / "diagnostic_kernel.md").read_text(encoding="utf-8")
    )
    assert surface.policy_identity.base_prompt_sha256 == _sha256(
        (PROMPTS_DIR / "base_safety.md").read_text(encoding="utf-8")
    )
    assert surface.policy_identity.controller_protocol_version == "p1.controller.v22"


@pytest.mark.parametrize(
    "strategy,filename,version",
    (
        (DiagnosticStrategy.STATIC_SKILL, "static_skill.md", "p1.static.v5"),
        (DiagnosticStrategy.NO_TOOL, "no_tool.md", "p1.no-tool.v1"),
    ),
)
def test_non_kernel_strategy_prompt_identities_unchanged(
    strategy: DiagnosticStrategy,
    filename: str,
    version: str,
) -> None:
    surface = policy_surface_for_strategy(strategy)
    assert surface.strategy_prompt_version == version
    assert surface.strategy_prompt_sha256 == _sha256(
        (PROMPTS_DIR / filename).read_text(encoding="utf-8")
    )
    assert surface.policy_identity.controller_protocol_version == "p1.controller.v22"


def test_schema_check_paragraph_precedes_the_final_decision_paragraph() -> None:
    # The prompt file hard-wraps its paragraphs; compare on normalized text.
    flat = " ".join(KERNEL_PROMPT.split())
    marker = "Before confirming an anomaly from an accepted relation profile"
    assert marker in flat
    assert flat.index(marker) < flat.index(
        "Before a final decision, check whether decisive, queryable evidence"
    )
    for fragment in (
        "Reuse an accepted schema.",
        "Do not spend the last model request on this corroboration.",
        "Do not sweep relations or displace decisive profile, history or lineage checks.",
        "A disabled or unavailable schema is not, by itself, a reason to probe or abstain.",
        "preserving each claim's citation constraints",
    ):
        assert fragment in flat
    # Static guidance must not leak the kernel-only rule.
    assert marker not in " ".join(STATIC_PROMPT.split())


# ------------------------------------------------------------ ledger mechanics


def _kernel(
    *,
    schema: tuple[str, ...],
    profile: tuple[str, ...],
    history: tuple[str, ...] = (),
    tool_call_limit: int = 8,
) -> DiagnosticKernel:
    return DiagnosticKernel.start(
        run_id=RUN_ID,
        allowed_root_cause_codes=CODES,
        model_request_limit=8,
        tool_call_limit=tool_call_limit,
        observable_schema_relations=schema,
        observable_profile_relations=profile,
        observable_history_relations=history,
    )


def _fixture_record(kind: str, relation: str | None, source_run: int) -> EvidenceRecord:
    payload = json.loads(
        (FIXTURES / f"seq{source_run}_evidence.json").read_text(encoding="utf-8")
    )
    for item in payload["records"]:
        content = item["content"]
        if content.get("kind") != kind:
            continue
        if relation is not None and content.get("relation_name") != relation:
            continue
        base = EvidenceRecord.model_validate(item)
        return EvidenceRecord.create(
            run_id=RUN_ID,
            evidence_type=base.evidence_type,
            source=base.source,
            subject=base.subject,
            observed_at=base.observed_at,
            content=base.content.model_copy(update={"run_id": RUN_ID}),
        )
    raise AssertionError(f"no fixture record for {kind}/{relation}/seq{source_run}")


def _collect(
    kernel: DiagnosticKernel,
    *,
    gap_id: str,
    gap_kind: EvidenceGapKind,
    tool_name: str,
    relation: str,
    record: EvidenceRecord,
) -> None:
    prepared = kernel.prepare_tool(
        intent=InvestigationIntent(
            gap_id=gap_id,
            gap_kind=gap_kind,
            hypothesis_ids=("h_loss", "h_decline"),
        ),
        tool_name=tool_name,
        arguments={"relation_name": relation},
    )
    kernel.record_tool_result(prepared, (record,))


def _register_hypotheses(kernel: DiagnosticKernel) -> None:
    kernel.prepare_tool(
        intent=InvestigationIntent(
            gap_id="g_locate",
            gap_kind=EvidenceGapKind.LOCATE_FAILURE,
            new_hypotheses=(  # type: ignore[arg-type]
                {"hypothesis_id": "h_loss", "root_cause_code": CODES[0]},  # type: ignore[list-item]
                {"hypothesis_id": "h_decline", "root_cause_code": CODES[1]},  # type: ignore[list-item]
            ),
        ),
        tool_name="get_dbt_run_results",
        arguments={"run_id": RUN_ID},
    )


def test_profile_or_other_relation_does_not_clear_the_schema_opportunity() -> None:
    kernel = _kernel(
        schema=("raw_payments", "raw_customers"),
        profile=("raw_payments",),
    )
    _register_hypotheses(kernel)
    _collect(
        kernel,
        gap_id="g_profile",
        gap_kind=EvidenceGapKind.PROFILE_RELATION,
        tool_name="get_relation_data_profile",
        relation="raw_payments",
        record=_fixture_record("RELATION_DATA_PROFILE", "raw_payments", 50),
    )
    uncollected = _uncollected_relations(kernel)
    # A profile of the same relation must not present its schema as collected.
    assert "raw_payments" in uncollected["get_relation_schema"]
    assert "raw_payments" not in uncollected["get_relation_data_profile"]
    _collect(
        kernel,
        gap_id="g_schema_other",
        gap_kind=EvidenceGapKind.DISCRIMINATE_SCHEMA,
        tool_name="get_relation_schema",
        relation="raw_customers",
        record=_fixture_record("RELATION_SCHEMA", "raw_customers", 59),
    )
    uncollected = _uncollected_relations(kernel)
    # Another relation's schema must not stand in for the target relation.
    assert "raw_payments" in uncollected["get_relation_schema"]
    assert "raw_customers" not in uncollected["get_relation_schema"]


def test_recorded_schema_refusal_is_reused_and_never_reprobed() -> None:
    kernel = _kernel(schema=("raw_payments",), profile=("raw_payments",))
    _register_hypotheses(kernel)
    prepared = kernel.prepare_tool(
        intent=InvestigationIntent(
            gap_id="g_schema",
            gap_kind=EvidenceGapKind.DISCRIMINATE_SCHEMA,
            hypothesis_ids=("h_loss", "h_decline"),
        ),
        tool_name="get_relation_schema",
        arguments={"relation_name": "raw_payments"},
    )
    kernel.record_tool_failure(prepared, "RELATION_NOT_ALLOWED")
    # The real receipt stays on record; no fabricated success exists.
    assert kernel.evidence_records == ()
    gaps = kernel.snapshot(model_requests_used=0).gaps
    assert any(
        gap.status is EvidenceGapStatus.BLOCKED
        and gap.error_code == "RELATION_NOT_ALLOWED"
        for gap in gaps
    )
    with pytest.raises(KernelError) as error:
        kernel.prepare_tool(
            intent=InvestigationIntent(
                gap_id="g_schema_retry",
                gap_kind=EvidenceGapKind.DISCRIMINATE_SCHEMA,
                hypothesis_ids=("h_loss", "h_decline"),
            ),
            tool_name="get_relation_schema",
            arguments={"relation_name": "raw_payments"},
        )
    assert error.value.code == "DUPLICATE_TOOL_CALL"


def test_spent_tool_budget_keeps_its_existing_failure_code() -> None:
    kernel = _kernel(
        schema=("raw_payments",),
        profile=("raw_payments",),
        tool_call_limit=2,
    )
    _register_hypotheses(kernel)
    _collect(
        kernel,
        gap_id="g_profile",
        gap_kind=EvidenceGapKind.PROFILE_RELATION,
        tool_name="get_relation_data_profile",
        relation="raw_payments",
        record=_fixture_record("RELATION_DATA_PROFILE", "raw_payments", 50),
    )
    with pytest.raises(KernelError) as error:
        kernel.prepare_tool(
            intent=InvestigationIntent(
                gap_id="g_schema",
                gap_kind=EvidenceGapKind.DISCRIMINATE_SCHEMA,
                hypothesis_ids=("h_loss", "h_decline"),
            ),
            tool_name="get_relation_schema",
            arguments={"relation_name": "raw_payments"},
        )
    assert error.value.code == "TOOL_CALL_LIMIT"
    spent = kernel.snapshot(model_requests_used=8)
    assert spent.tool_calls_remaining == 0
    assert spent.model_requests_remaining == 0


# ------------------------------------------------- runner-level scripted pass


def _silent_record(kind: str, relation: str | None = None) -> EvidenceRecord:
    payload = json.loads((FIXTURES / "seq50_evidence.json").read_text(encoding="utf-8"))
    for item in payload["records"]:
        content = item["content"]
        if content.get("kind") != kind:
            continue
        if relation is not None and content.get("relation_name") != relation:
            continue
        return EvidenceRecord.model_validate(item)
    raise AssertionError(f"seq50 fixture lacks {kind}/{relation}")


class _SilentTools:
    def get_dbt_run_results(self, _run_id: str):
        return (_silent_record("DBT_RUN_RESULTS"),)

    def get_relation_data_profile(self, relation_name: str):
        return (_silent_record("RELATION_DATA_PROFILE", relation_name),)

    def get_relation_schema(self, relation_name: str):
        return (_silent_record("RELATION_SCHEMA", relation_name),)

    def get_relation_history(self, relation_name: str):
        return (_silent_record("RELATION_HISTORY", relation_name),)

    def get_dbt_lineage(self, _node_id: str, direction: str):
        return (_silent_record("DBT_LINEAGE"),)

    def lineage_node_candidates(self, subjects: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted(subject for subject in subjects if subject.startswith("seed.")))


def _write_silent_context(
    project_root: Path,
    *,
    schema_relations: tuple[str, ...] = ("raw_payments",),
) -> None:
    run_root = project_root / ".dig" / "lab" / "runs" / SILENT_RUN_ID
    run_root.mkdir(parents=True, exist_ok=True)
    brief = json.loads((FIXTURES / "seq50_brief.json").read_text(encoding="utf-8"))
    incident = IncidentBrief(
        schema_version="incident_brief.v1",
        signal_code="DBT_BUILD_FAILED",
        summary="A pipeline evidence review is requested.",
        subjects=tuple(brief["subjects"]),
        logical_observed_at=datetime(2026, 8, 30, tzinfo=UTC),
        observations=tuple(
            {"kind": item["kind"], "subject": item["subject"], "value": item["value"]}
            for item in brief["observations"]
        ),
    )
    runtime = {
        "schema_version": "p1.runtime.v1",
        "run_id": SILENT_RUN_ID,
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
            "schema": list(schema_relations),
            "profile": ["raw_payments", "raw_orders"],
            "history": ["raw_payments", "raw_orders"],
        },
        "profile_spec_sha256": "b" * 64,
    }
    (run_root / "runtime.json").write_text(json.dumps(runtime), encoding="utf-8")
    (run_root / "incident_brief.json").write_text(incident.model_dump_json(), encoding="utf-8")


def _ledger_from_instructions(agent_info: AgentInfo) -> dict[str, object]:
    instructions = agent_info.instructions or ""
    start = instructions.index(LEDGER_HEADER)
    return json.loads(instructions[start:].splitlines()[1])


def _confirmed_payload() -> dict[str, object]:
    """The silent-family CONFIRMED decision over the five decisive records."""

    lineage = _silent_record("DBT_LINEAGE")
    assets = tuple(
        sorted(
            node.node_id
            for node in lineage.content.related_nodes  # type: ignore[union-attr]
            if node.resource_type == "model"
        )
    )
    root_evidence = [
        record.evidence_id
        for record in (
            _silent_record("DBT_RUN_RESULTS"),
            _silent_record("RELATION_DATA_PROFILE", "raw_payments"),
            _silent_record("RELATION_DATA_PROFILE", "raw_orders"),
            _silent_record("RELATION_HISTORY", "raw_payments"),
            _silent_record("RELATION_HISTORY", "raw_orders"),
        )
    ]
    return {
        "schema_version": "p1.kernel_decision.v1",
        "run_id": SILENT_RUN_ID,
        "selected_hypothesis_id": "h_loss",
        "assessments": [
            {
                "hypothesis_id": "h_loss",
                "verdict": "SUPPORTED",
                "evidence_ids": root_evidence,
            },
            {
                "hypothesis_id": "h_decline",
                "verdict": "REFUTED",
                "evidence_ids": [lineage.evidence_id],
            },
        ],
        "claims": [
            {
                "kind": "ROOT_CAUSE",
                "value": CODES[0],
                "evidence_ids": root_evidence,
            },
            *(
                {
                    "kind": "AFFECTED_ASSET",
                    "value": asset,
                    "evidence_ids": [lineage.evidence_id],
                }
                for asset in assets
            ),
        ],
        "summary": "SYNTHETIC: settled payment volume is missing source events.",
        "recommended_actions": [],
        "confidence": 0.9,
    }


def _playbook_script(captured: list[dict[str, object]]) -> FunctionModel:
    def call(name: str, arguments: dict[str, object], call_id: str) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart(name, arguments, tool_call_id=call_id)])

    def scripted(messages: list[ModelMessage], agent_info: AgentInfo) -> ModelResponse:
        sent = {
            part.tool_call_id
            for message in messages
            for part in message.parts
            if isinstance(part, ToolCallPart)
        }
        ledger = _ledger_from_instructions(agent_info)
        captured.append(
            {
                "schema_uncollected": ledger["uncollected_relations"][
                    "get_relation_schema"
                ],
                "model_requests_remaining": ledger["model_requests_remaining"],
            }
        )
        registration = {
            "kernel_hypothesis_ids": [],
            "kernel_new_hypotheses": [
                {"hypothesis_id": "h_loss", "root_cause_code": CODES[0]},
                {"hypothesis_id": "h_decline", "root_cause_code": CODES[1]},
            ],
        }
        serving = {"kernel_hypothesis_ids": ["h_loss", "h_decline"]}
        if "c1" not in sent:
            return call(
                "get_dbt_run_results", {"run_id": SILENT_RUN_ID, **registration}, "c1"
            )
        if "c2" not in sent:
            return call(
                "get_relation_data_profile",
                {"relation_name": "raw_payments", **serving},
                "c2",
            )
        # The new-rule step: corroborate the profiled relation with its own
        # schema while the ledger still lists it as uncollected.
        if "c3" not in sent:
            assert captured[-1]["schema_uncollected"] == ["raw_payments"]
            return call(
                "get_relation_schema", {"relation_name": "raw_payments", **serving}, "c3"
            )
        if "c4" not in sent:
            return call(
                "get_relation_data_profile",
                {"relation_name": "raw_orders", **serving},
                "c4",
            )
        if "c5" not in sent:
            return call(
                "get_relation_history", {"relation_name": "raw_payments", **serving}, "c5"
            )
        if "c6" not in sent:
            return call(
                "get_relation_history", {"relation_name": "raw_orders", **serving}, "c6"
            )
        if "c7" not in sent:
            return call(
                "get_dbt_lineage",
                {
                    "node_id": "seed.jaffle_shop.raw_payments",
                    "direction": "downstream",
                    **serving,
                },
                "c7",
            )
        # The decision turn: the schema opportunity has been consumed, and the
        # last model request is reserved for the decision itself — no
        # corroboration batch rides it.
        assert captured[-1]["schema_uncollected"] == []
        assert captured[-1]["model_requests_remaining"] == 1
        payload = _confirmed_payload()
        return ModelResponse(
            parts=[ToolCallPart(agent_info.output_tools[1].name, payload, tool_call_id="d1")]
        )

    return FunctionModel(scripted)


@pytest.mark.asyncio
async def test_kernel_profile_schema_playbook_runs_over_the_real_runner(
    tmp_path: Path,
) -> None:
    _write_silent_context(tmp_path)
    captured: list[dict[str, object]] = []
    runner = DiagnosisRunner.for_run(
        SILENT_RUN_ID,
        SimpleNamespace(
            model_base_url=MODEL_BASE_URL,
            model_name="synthetic-model",
            model_api_key=SimpleNamespace(get_secret_value=lambda: "synthetic-key"),
        ),
        DiagnosticStrategy.DIAGNOSTIC_KERNEL,
        tmp_path,
        model=_playbook_script(captured),
        tools=_SilentTools(),
        model_identity=ModelIdentity("synthetic", "synthetic-model"),
    )
    result = await runner.diagnose()

    assert result.diagnosis.status is DiagnosisStatus.CONFIRMED
    assert result.diagnosis.root_cause_code == CODES[0]
    schema_events = [
        event
        for event in result.trace
        if getattr(event, "tool_name", None) == "get_relation_schema"
    ]
    assert len(schema_events) == 1
    assert schema_events[0].error_code is None
    profile_index = next(
        index
        for index, event in enumerate(result.trace)
        if getattr(event, "tool_name", None) == "get_relation_data_profile"
    )
    assert result.trace.index(schema_events[0]) > profile_index
    assert result.metrics.model_requests == 8
    assert result.metrics.tool_call_attempts == 7
    assert any(
        record.content.kind == "RELATION_SCHEMA" for record in result.evidence_records
    )
    # The opportunity existed from the first investigation turn and was
    # consumed by the same session's schema call, exactly once.
    assert captured[0]["schema_uncollected"] == ["raw_payments"]
    assert captured[-1]["schema_uncollected"] == []
    # Collection completeness is not citation completeness: the corroboration
    # record exists but no claim stuffs it in, and the ROOT_CAUSE claim keeps
    # its exact citation set.
    schema_record = next(
        record
        for record in result.evidence_records
        if record.content.kind == "RELATION_SCHEMA"
    )
    assert all(
        schema_record.evidence_id not in claim.evidence_ids
        for claim in result.diagnosis.claims
    )


# ----------------------- scripted boundary regressions (design rows 3, 5, 6, 7)


def _kernel_runner(
    run_id: str,
    project_root: Path,
    model: FunctionModel,
    tools: object,
    strategy: DiagnosticStrategy = DiagnosticStrategy.DIAGNOSTIC_KERNEL,
) -> DiagnosisRunner:
    return DiagnosisRunner.for_run(
        run_id,
        SimpleNamespace(
            model_base_url=MODEL_BASE_URL,
            model_name="synthetic-model",
            model_api_key=SimpleNamespace(get_secret_value=lambda: "synthetic-key"),
        ),
        strategy,
        project_root,
        model=model,
        tools=tools,  # type: ignore[arg-type]
        model_identity=ModelIdentity("synthetic", "synthetic-model"),
    )


def _silent_script_without_schema(
    captured: list[dict[str, object]],
) -> FunctionModel:
    """The silent playbook with the corroboration step unavailable: the script
    never calls schema, never fabricates a substitute, and still reaches the
    unchanged CONFIRMED terminal over the decisive evidence."""

    def call(name: str, arguments: dict[str, object], call_id: str) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart(name, arguments, tool_call_id=call_id)])

    def scripted(messages: list[ModelMessage], agent_info: AgentInfo) -> ModelResponse:
        sent = {
            part.tool_call_id
            for message in messages
            for part in message.parts
            if isinstance(part, ToolCallPart)
        }
        ledger = _ledger_from_instructions(agent_info)
        captured.append(ledger)
        registration = {
            "kernel_hypothesis_ids": [],
            "kernel_new_hypotheses": [
                {"hypothesis_id": "h_loss", "root_cause_code": CODES[0]},
                {"hypothesis_id": "h_decline", "root_cause_code": CODES[1]},
            ],
        }
        serving = {"kernel_hypothesis_ids": ["h_loss", "h_decline"]}
        if "c1" not in sent:
            return call(
                "get_dbt_run_results", {"run_id": SILENT_RUN_ID, **registration}, "c1"
            )
        if "c2" not in sent:
            return call(
                "get_relation_data_profile",
                {"relation_name": "raw_payments", **serving},
                "c2",
            )
        if "c3" not in sent:
            return call(
                "get_relation_data_profile",
                {"relation_name": "raw_orders", **serving},
                "c3",
            )
        if "c4" not in sent:
            return call(
                "get_relation_history",
                {"relation_name": "raw_payments", **serving},
                "c4",
            )
        if "c5" not in sent:
            return call(
                "get_relation_history", {"relation_name": "raw_orders", **serving}, "c5"
            )
        if "c6" not in sent:
            return call(
                "get_dbt_lineage",
                {
                    "node_id": "seed.jaffle_shop.raw_payments",
                    "direction": "downstream",
                    **serving,
                },
                "c6",
            )
        # The decision turn: the target schema was never collected (disabled
        # tool) and the corroboration skip did not create or force anything.
        assert captured[-1]["model_requests_remaining"] == 2
        payload = _confirmed_payload()
        return ModelResponse(
            parts=[ToolCallPart(agent_info.output_tools[1].name, payload, tool_call_id="d1")]
        )

    return FunctionModel(scripted)


@pytest.mark.asyncio
async def test_no_schema_ablation_skips_corroboration_without_new_gaps(
    tmp_path: Path,
) -> None:
    _write_silent_context(tmp_path)
    captured: list[dict[str, object]] = []
    result = await _kernel_runner(
        SILENT_RUN_ID,
        tmp_path,
        _silent_script_without_schema(captured),
        _SilentTools(),
        DiagnosticStrategy.KERNEL_NO_SCHEMA,
    ).diagnose()

    # Existing terminal rules unchanged: the disabled schema is not a reason
    # to abstain, and the confirmation path over decisive evidence still works.
    assert result.diagnosis.status is DiagnosisStatus.CONFIRMED
    assert result.diagnosis.root_cause_code == CODES[0]
    # No schema call and no auto-created schema gap anywhere in the run.
    assert not [
        event
        for event in result.trace
        if getattr(event, "tool_name", None) == "get_relation_schema"
    ]
    assert all(
        gap.gap_kind is not EvidenceGapKind.DISCRIMINATE_SCHEMA
        for gap in result.kernel_state.gaps
    )
    assert result.metrics.tool_call_attempts == 6


def _seq59_abstaining_script(
    captured: list[dict[str, object]],
    run_id: str,
) -> FunctionModel:
    """Schema-change family (seq59, coupon b): the target relation raw_orders
    is not readable by the schema tool and other relations' schemas are
    available — the script collects none of them, adds no probe, and abstains
    with only the independent-fact declaration."""

    def call(name: str, arguments: dict[str, object], call_id: str) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart(name, arguments, tool_call_id=call_id)])

    def scripted(messages: list[ModelMessage], agent_info: AgentInfo) -> ModelResponse:
        sent = {
            part.tool_call_id
            for message in messages
            for part in message.parts
            if isinstance(part, ToolCallPart)
        }
        ledger = _ledger_from_instructions(agent_info)
        captured.append(
            {
                "provable_schema": ledger["provable_relations"]["get_relation_schema"],
                "schema_uncollected": ledger["uncollected_relations"][
                    "get_relation_schema"
                ],
            }
        )
        registration = {
            "kernel_hypothesis_ids": [],
            "kernel_new_hypotheses": [
                {
                    "hypothesis_id": "h_schema_col_type",
                    "root_cause_code": "SOURCE_SCHEMA_COLUMN_TYPE_CHANGED",
                },
                {
                    "hypothesis_id": "h_transform_cast",
                    "root_cause_code": "TRANSFORMATION_COLUMN_CAST_CHANGED",
                },
            ],
        }
        serving = {"kernel_hypothesis_ids": ["h_schema_col_type", "h_transform_cast"]}
        if "c1" not in sent:
            return call(
                "get_dbt_run_results", {"run_id": run_id, **registration}, "c1"
            )
        if "c2" not in sent:
            return call(
                "get_dbt_node_error",
                {
                    "run_id": run_id,
                    "node_id": "model.jaffle_shop.customers",
                    **serving,
                },
                "c2",
            )
        if "c3" not in sent:
            return call(
                "get_dbt_lineage",
                {
                    "node_id": "model.jaffle_shop.customers",
                    "direction": "upstream",
                    **serving,
                },
                "c3",
            )
        if "c4" not in sent:
            # The corroboration target is not on the schema tool's whitelist;
            # other relations are, but their schemas must not be swept as
            # substitutes.
            assert sorted(captured[-1]["provable_schema"]) == [
                "raw_customers",
                "raw_payments",
            ]
            assert "raw_orders" not in captured[-1]["provable_schema"]
            return call(
                "get_relation_data_profile",
                {"relation_name": "raw_orders", **serving},
                "c4",
            )
        if "c5" not in sent:
            return call(
                "get_relation_history",
                {"relation_name": "raw_orders", **serving},
                "c5",
            )
        if "c6" not in sent:
            # The target is unreadable: under the EXISTING boundary rule the
            # single permitted probe records the real refusal receipt that the
            # abstention contract needs. The new corroboration rule neither
            # removes nor replaces this probe.
            return call(
                "get_relation_schema",
                {"relation_name": "raw_orders", **serving},
                "c6",
            )
        # The decision turn: the probe receipt is reused, the observed
        # OTHER-relation schemas are not a health baseline or a proof of
        # change, and no substitute corroboration call was made.
        assert sorted(captured[-1]["provable_schema"]) == [
            "raw_customers",
            "raw_payments",
        ]
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
                                "evidence_kind": "TRANSFORMATION_DEFINITION",
                                "subject": "model.jaffle_shop.stg_orders",
                            },
                        ],
                        "summary": (
                            "The raw_orders schema and the transformation "
                            "definition are unavailable."
                        ),
                        "recommended_actions": [],
                        "confidence": 0.2,
                    },
                    tool_call_id="final",
                )
            ]
        )

    return FunctionModel(scripted)


@pytest.mark.asyncio
async def test_unreadable_target_is_not_probed_and_other_schemas_not_substituted(
    tmp_path: Path,
) -> None:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import test_kernel_rerun_repair as seq59

    seq59._write_context(tmp_path, 59)
    captured: list[dict[str, object]] = []
    result = await _kernel_runner(
        seq59.RUN_IDS[59],
        tmp_path,
        _seq59_abstaining_script(captured, seq59.RUN_IDS[59]),
        seq59._SchemaBTools(include_history=True),
    ).diagnose()

    # Exactly one schema interaction: the boundary probe for the unreadable
    # target, refused with a real receipt. No substitute corroboration call
    # for the readable other relations.
    schema_events = [
        event
        for event in result.trace
        if getattr(event, "tool_name", None) == "get_relation_schema"
    ]
    assert len(schema_events) == 1
    assert schema_events[0].arguments == {"relation_name": "raw_orders"}
    assert schema_events[0].error_code == "RELATION_NOT_ALLOWED"
    # The only schema-typed gap is the real probe receipt, not an auto gap.
    schema_gaps = [
        gap
        for gap in result.kernel_state.gaps
        if gap.gap_kind is EvidenceGapKind.DISCRIMINATE_SCHEMA
    ]
    assert len(schema_gaps) == 1
    assert schema_gaps[0].status is EvidenceGapStatus.BLOCKED
    assert schema_gaps[0].error_code == "RELATION_NOT_ALLOWED"
    # The terminal stays abstention with only the legitimate declaration; no
    # confirmation was forced or derived from the observed other-relation
    # schemas.
    gates = [
        event for event in result.trace if isinstance(event, EvidenceGateTraceEventV2)
    ]
    assert [event.reason_code for event in gates if not event.accepted] == []
    assert "CONFIRMED" not in {event.reason_code for event in gates}
    assert result.diagnosis.status is DiagnosisStatus.INSUFFICIENT_EVIDENCE
    evaluation = seq59._evaluate(59, result)
    assert evaluation.status is EvaluationStatus.PASSED


def _distractor_script(captured: list[dict[str, object]]) -> FunctionModel:
    """Both relations carry profiles; the schema check targets only the
    root-cause relation and never sweeps the distractor."""

    def call(name: str, arguments: dict[str, object], call_id: str) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart(name, arguments, tool_call_id=call_id)])

    def scripted(messages: list[ModelMessage], agent_info: AgentInfo) -> ModelResponse:
        sent = {
            part.tool_call_id
            for message in messages
            for part in message.parts
            if isinstance(part, ToolCallPart)
        }
        ledger = _ledger_from_instructions(agent_info)
        captured.append(
            {
                "schema_uncollected": ledger["uncollected_relations"][
                    "get_relation_schema"
                ]
            }
        )
        registration = {
            "kernel_hypothesis_ids": [],
            "kernel_new_hypotheses": [
                {"hypothesis_id": "h_loss", "root_cause_code": CODES[0]},
                {"hypothesis_id": "h_decline", "root_cause_code": CODES[1]},
            ],
        }
        serving = {"kernel_hypothesis_ids": ["h_loss", "h_decline"]}
        if "c1" not in sent:
            return call(
                "get_dbt_run_results", {"run_id": SILENT_RUN_ID, **registration}, "c1"
            )
        if "c2" not in sent:
            return call(
                "get_relation_data_profile",
                {"relation_name": "raw_payments", **serving},
                "c2",
            )
        if "c3" not in sent:
            return call(
                "get_relation_data_profile",
                {"relation_name": "raw_orders", **serving},
                "c3",
            )
        if "c4" not in sent:
            # Both relations are readable and uncollected; only the root-cause
            # relation is corroborated.
            assert sorted(captured[-1]["schema_uncollected"]) == [
                "raw_orders",
                "raw_payments",
            ]
            return call(
                "get_relation_schema", {"relation_name": "raw_payments", **serving}, "c4"
            )
        if "c5" not in sent:
            return call(
                "get_relation_history",
                {"relation_name": "raw_payments", **serving},
                "c5",
            )
        if "c6" not in sent:
            return call(
                "get_relation_history", {"relation_name": "raw_orders", **serving}, "c6"
            )
        if "c7" not in sent:
            return call(
                "get_dbt_lineage",
                {
                    "node_id": "seed.jaffle_shop.raw_payments",
                    "direction": "downstream",
                    **serving,
                },
                "c7",
            )
        # The decision turn: the distractor relation was never collected and
        # stays listed; the target was consumed.
        assert captured[-1]["schema_uncollected"] == ["raw_orders"]
        payload = _confirmed_payload()
        return ModelResponse(
            parts=[ToolCallPart(agent_info.output_tools[1].name, payload, tool_call_id="d1")]
        )

    return FunctionModel(scripted)


@pytest.mark.asyncio
async def test_schema_check_targets_only_the_root_cause_relation(
    tmp_path: Path,
) -> None:
    _write_silent_context(tmp_path, schema_relations=("raw_payments", "raw_orders"))
    captured: list[dict[str, object]] = []
    result = await _kernel_runner(
        SILENT_RUN_ID,
        tmp_path,
        _distractor_script(captured),
        _SilentTools(),
    ).diagnose()

    assert result.diagnosis.status is DiagnosisStatus.CONFIRMED
    schema_events = [
        event
        for event in result.trace
        if getattr(event, "tool_name", None) == "get_relation_schema"
    ]
    assert len(schema_events) == 1
    assert schema_events[0].arguments == {"relation_name": "raw_payments"}
    assert schema_events[0].error_code is None
    assert sorted(captured[0]["schema_uncollected"]) == ["raw_orders", "raw_payments"]
    assert captured[-1]["schema_uncollected"] == ["raw_orders"]
