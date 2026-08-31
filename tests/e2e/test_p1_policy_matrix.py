from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from data_incident_gym.artifacts import ARTIFACT_FILENAMES, ArtifactWriter
from data_incident_gym.config import Settings
from data_incident_gym.diagnosis import DiagnosisStatus, DiagnosticStrategy
from data_incident_gym.diagnostic_agent import DiagnosisRunner, ModelIdentity
from data_incident_gym.diagnostic_config import DiagnosticSettings
from data_incident_gym.evaluation import DeterministicEvaluator, EvaluationStatus
from data_incident_gym.evaluation_runner import EvaluationRunner
from data_incident_gym.evidence import (
    DbtRunResultsFact,
    EvidenceRecord,
    RelationHistoryFact,
    RelationSchemaFact,
)
from data_incident_gym.lab import IncidentLab
from data_incident_gym.run_context import resolve_run_context
from data_incident_gym.scenarios import (
    P1_M7_SCENARIO_IDS,
    P1_M8_SCENARIO_IDS,
    load_scenario_spec,
)

MATRIX_CASES = P1_M7_SCENARIO_IDS + P1_M8_SCENARIO_IDS
MATRIX_STRATEGIES = (
    DiagnosticStrategy.STATIC_SKILL,
    DiagnosticStrategy.DIAGNOSTIC_KERNEL,
)
assert len(MATRIX_CASES) == 7
assert len(MATRIX_CASES) * len(MATRIX_STRATEGIES) == 14


def _returned_records(
    messages: Iterable[ModelMessage],
    tool_name: str,
) -> tuple[EvidenceRecord, ...]:
    records: list[EvidenceRecord] = []
    for message in messages:
        for part in message.parts:
            if (
                isinstance(part, ToolReturnPart)
                and part.tool_name == tool_name
                and part.outcome == "success"
                and isinstance(part.content, tuple)
            ):
                records.extend(part.content)
    return tuple(records)


def _tool_attempts(messages: Iterable[ModelMessage], tool_name: str) -> int:
    return sum(
        isinstance(part, ToolCallPart) and part.tool_name == tool_name
        for message in messages
        for part in message.parts
    )


def _tool_call(name: str, arguments: dict[str, object], call_id: str) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(name, arguments, tool_call_id=call_id)])


def _intent(gap_id: str, gap_kind: str, **values: object) -> str:
    return json.dumps(
        {
            "schema_version": "p1.kernel_intent.v1",
            "gap_id": gap_id,
            "gap_kind": gap_kind,
            "hypothesis_ids": [],
            "new_hypotheses": [],
            **values,
        }
    )


def _source_relations(records: tuple[EvidenceRecord, ...]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            node.name
            for record in records
            for node in getattr(record.content, "related_nodes", ())
            if node.resource_type == "seed"
        )
    )


def _transform_subject(records: tuple[EvidenceRecord, ...]) -> str:
    for record in records:
        for node in getattr(record.content, "related_nodes", ()):
            if node.node_id.endswith(".stg_orders"):
                return node.node_id
    return "model.jaffle_shop.stg_orders"


def _health_point(record: EvidenceRecord) -> tuple[str, int]:
    assert isinstance(record.content, RelationHistoryFact)
    series = next(
        item for item in record.content.snapshot.histories if item.name == "order_count_by_day"
    )
    point = next(item for item in series.points if item.bucket == "2018-04-02")
    return point.bucket, int(point.value)


def _model_response(
    messages: list[ModelMessage],
    agent_info: AgentInfo,
    *,
    run_id: str,
    strategy: DiagnosticStrategy,
    schema_relations: tuple[str, ...],
    profile_relations: tuple[str, ...],
) -> ModelResponse:
    run_records = _returned_records(messages, "get_dbt_run_results")
    node_errors = _returned_records(messages, "get_dbt_node_error")
    lineages = _returned_records(messages, "get_dbt_lineage")
    schemas = _returned_records(messages, "get_relation_schema")
    profiles = _returned_records(messages, "get_relation_data_profile")
    histories = _returned_records(messages, "get_relation_history")

    if not run_records:
        tool = _tool_call("get_dbt_run_results", {"run_id": run_id}, "run-results")
        return _with_intent(tool, strategy, _intent("g_locate", "LOCATE_FAILURE"))

    run_fact = run_records[-1].content
    if isinstance(run_fact, DbtRunResultsFact) and run_fact.run_status == "SUCCEEDED":
        if not profiles:
            tool = _tool_call(
                "get_relation_data_profile",
                {"relation_name": "raw_orders"},
                "profile",
            )
            return _with_intent(tool, strategy, _intent("g_profile", "PROFILE_RELATION"))
        if not histories:
            tool = _tool_call(
                "get_relation_history",
                {"relation_name": "raw_orders"},
                "history",
            )
            return _with_intent(tool, strategy, _intent("g_history", "COMPARE_HISTORY"))
        bucket, value = _health_point(histories[-1])
        evidence_ids = [
            run_records[-1].evidence_id,
            profiles[-1].evidence_id,
            histories[-1].evidence_id,
        ]
        if strategy is DiagnosticStrategy.STATIC_SKILL:
            payload = {
                "status": "NO_INCIDENT",
                "run_id": run_id,
                "summary": "The observed order volume is within the healthy history range.",
                "affected_assets": [],
                "evidence_ids": evidence_ids,
                "claims": [
                    {
                        "kind": "HEALTH_STATE",
                        "relation_name": "raw_orders",
                        "history_name": "order_count_by_day",
                        "bucket": bucket,
                        "current_value": value,
                        "evidence_ids": evidence_ids,
                    }
                ],
                "unresolved_evidence": [],
                "recommended_actions": ["Continue observing the order-volume history."],
                "confidence": 0.95,
            }
        else:
            payload = {
                "status": "NO_INCIDENT",
                "run_id": run_id,
                "selected_hypothesis_id": None,
                "assessments": [],
                "claims": [
                    {
                        "kind": "HEALTH_STATE",
                        "value": "raw_orders",
                        "relation_name": "raw_orders",
                        "history_name": "order_count_by_day",
                        "bucket": bucket,
                        "current_value": value,
                        "evidence_ids": evidence_ids,
                    }
                ],
                "unresolved_evidence": [],
                "summary": "The observed order volume is within the healthy history range.",
                "recommended_actions": ["Continue observing the order-volume history."],
                "confidence": 0.95,
            }
        return _tool_call(agent_info.output_tools[0].name, payload, "diagnosis")

    if not node_errors:
        node_id = run_fact.failed_nodes[0]  # type: ignore[union-attr]
        tool = _tool_call(
            "get_dbt_node_error",
            {"run_id": run_id, "node_id": node_id},
            "node-error",
        )
        return _with_intent(tool, strategy, _intent("g_explain", "EXPLAIN_FAILURE"))

    failure_node = node_errors[-1].content.node_id
    upstream = tuple(
        record
        for record in lineages
        if getattr(record.content, "direction", None) == "upstream"
    )
    downstream = tuple(
        record
        for record in lineages
        if getattr(record.content, "direction", None) == "downstream"
    )
    lineage_attempts = _tool_attempts(messages, "get_dbt_lineage")
    if not upstream and lineage_attempts == 0:
        tool = _tool_call(
            "get_dbt_lineage",
            {"node_id": failure_node, "direction": "upstream"},
            "upstream",
        )
        return _with_intent(tool, strategy, _intent("g_source", "DISCOVER_SOURCE_RELATION"))

    source_relations = _source_relations(upstream)
    schema_attempts = _tool_attempts(messages, "get_relation_schema")

    if getattr(node_errors[-1].content, "resource_type", None) == "test":
        source_relation = next(
            relation for relation in source_relations if relation in schema_relations
        )
        affected_model = next(
            node.node_id
            for record in upstream
            for node in record.content.related_nodes
            if node.resource_type == "model" and node.distance == 1
        )
        if schema_attempts == 0:
            new_hypotheses = [
                {
                    "hypothesis_id": "h_source_null",
                    "root_cause_code": "SOURCE_REQUIRED_FIELD_NULL",
                },
                {
                    "hypothesis_id": "h_transform_null",
                    "root_cause_code": "TRANSFORMATION_REQUIRED_FIELD_NULL",
                },
            ]
            tool = _tool_call(
                "get_relation_schema",
                {"relation_name": source_relation},
                "schema",
            )
            return _with_intent(
                tool,
                strategy,
                _intent(
                    "g_schema_null",
                    "DISCRIMINATE_SCHEMA",
                    new_hypotheses=new_hypotheses,
                ),
            )

        profile_by_relation = {
            record.content.relation_name: record
            for record in profiles
        }
        distractor_relation = next(
            (
                relation
                for relation in profile_relations
                if relation not in source_relations
            ),
            None,
        )
        if distractor_relation is not None and distractor_relation not in profile_by_relation:
            tool = _tool_call(
                "get_relation_data_profile",
                {"relation_name": distractor_relation},
                "distractor-profile",
            )
            return _with_intent(
                tool,
                strategy,
                _intent(
                    "g_distractor_profile",
                    "PROFILE_RELATION",
                    hypothesis_ids=["h_source_null", "h_transform_null"],
                ),
            )

        source_profile = profile_by_relation.get(source_relation)
        profile_attempts = _tool_attempts(messages, "get_relation_data_profile")
        if source_profile is None and profile_attempts < 2:
            tool = _tool_call(
                "get_relation_data_profile",
                {"relation_name": source_relation},
                "source-profile",
            )
            return _with_intent(
                tool,
                strategy,
                _intent(
                    "g_source_profile",
                    "PROFILE_RELATION",
                    hypothesis_ids=["h_source_null", "h_transform_null"],
                ),
            )

        evidence_ids = [
            record.evidence_id
            for record in (*run_records, *node_errors, *lineages, *schemas, *profiles)
        ]
        node_error_id = node_errors[-1].evidence_id
        lineage_id = next(
            record.evidence_id
            for record in upstream
            if any(
                node.node_id == affected_model and node.distance == 1
                for node in record.content.related_nodes
            )
        )
        if source_profile is None:
            unresolved = [
                {
                    "evidence_kind": "RELATION_DATA_PROFILE",
                    "subject": source_relation,
                    "reason_code": "RELATION_NOT_ALLOWED",
                },
                {
                    "evidence_kind": "TRANSFORMATION_DEFINITION",
                    "subject": _transform_subject(upstream),
                    "reason_code": "NOT_OBSERVABLE",
                },
            ]
            if strategy is DiagnosticStrategy.STATIC_SKILL:
                payload = {
                    "status": "INSUFFICIENT_EVIDENCE",
                    "run_id": run_id,
                    "summary": (
                        "Two compatible causes remain because decisive evidence is unavailable."
                    ),
                    "affected_assets": [],
                    "evidence_ids": evidence_ids,
                    "claims": [],
                    "unresolved_evidence": unresolved,
                    "recommended_actions": [
                        "Collect the unavailable source and transformation facts."
                    ],
                    "confidence": 0.3,
                }
            else:
                payload = {
                    "status": "INSUFFICIENT_EVIDENCE",
                    "run_id": run_id,
                    "selected_hypothesis_id": None,
                    "assessments": [],
                    "claims": [],
                    "unresolved_evidence": unresolved,
                    "summary": (
                        "Two compatible causes remain because decisive evidence is unavailable."
                    ),
                    "recommended_actions": [
                        "Collect the unavailable source and transformation facts."
                    ],
                    "confidence": 0.3,
                }
            return _tool_call(agent_info.output_tools[0].name, payload, "diagnosis")

        source_profile_id = source_profile.evidence_id
        root_evidence_ids = [node_error_id, source_profile_id]
        assets = (affected_model,)
        if strategy is DiagnosticStrategy.STATIC_SKILL:
            payload = {
                "status": "CONFIRMED",
                "run_id": run_id,
                "root_cause_code": "SOURCE_REQUIRED_FIELD_NULL",
                "summary": "The required source field is null.",
                "affected_assets": assets,
                "evidence_ids": evidence_ids,
                "claims": [
                    {
                        "kind": "ROOT_CAUSE",
                        "root_cause_code": "SOURCE_REQUIRED_FIELD_NULL",
                        "evidence_ids": root_evidence_ids,
                    },
                    {
                        "kind": "AFFECTED_ASSET",
                        "asset": affected_model,
                        "evidence_ids": [lineage_id],
                    },
                ],
                "unresolved_evidence": [],
                "recommended_actions": ["Restore the required source field."],
                "confidence": 0.9,
            }
        else:
            payload = {
                "status": "CONFIRMED",
                "run_id": run_id,
                "selected_hypothesis_id": "h_source_null",
                "assessments": [
                    {
                        "hypothesis_id": "h_source_null",
                        "verdict": "SUPPORTED",
                        "evidence_ids": root_evidence_ids,
                    },
                    {
                        "hypothesis_id": "h_transform_null",
                        "verdict": "REFUTED",
                        "evidence_ids": [node_error_id, source_profile_id],
                    },
                ],
                "claims": [
                    {
                        "kind": "ROOT_CAUSE",
                        "value": "SOURCE_REQUIRED_FIELD_NULL",
                        "evidence_ids": root_evidence_ids,
                    },
                    {
                        "kind": "AFFECTED_ASSET",
                        "value": affected_model,
                        "evidence_ids": [lineage_id],
                    },
                ],
                "unresolved_evidence": [],
                "summary": "The required source field is null.",
                "recommended_actions": ["Restore the required source field."],
                "confidence": 0.9,
            }
        return _tool_call(agent_info.output_tools[0].name, payload, "diagnosis")

    if schema_attempts == 0:
        new_hypotheses = [
            {
                "hypothesis_id": "h_rename",
                "root_cause_code": "SOURCE_SCHEMA_COLUMN_RENAMED",
            },
            {
                "hypothesis_id": "h_type",
                "root_cause_code": "SOURCE_SCHEMA_COLUMN_TYPE_CHANGED",
            },
            {
                "hypothesis_id": "h_cast",
                "root_cause_code": "TRANSFORMATION_COLUMN_CAST_CHANGED",
            },
        ]
        tool = _tool_call(
            "get_relation_schema",
            {
                "relation_name": (
                    "raw_orders"
                    if "raw_orders" in source_relations and "raw_orders" not in schema_relations
                    else next(
                        relation
                        for relation in schema_relations
                        if relation in source_relations
                    )
                )
            },
            "schema",
        )
        return _with_intent(
            tool,
            strategy,
            _intent(
                f"g_schema_{schema_attempts + 1}",
                "DISCRIMINATE_SCHEMA",
                **(
                    {"new_hypotheses": new_hypotheses}
                    if schema_attempts == 0
                    else {"hypothesis_ids": ["h_rename", "h_type", "h_cast"]}
                ),
            ),
        )

    blocked_relations = tuple(
        relation for relation in source_relations if relation not in schema_relations
    )
    if blocked_relations:
        blocked_relation = blocked_relations[0]
        if not profiles:
            tool = _tool_call(
                "get_relation_data_profile",
                {"relation_name": "raw_orders"},
                "profile",
            )
            return _with_intent(
                tool,
                strategy,
                _intent(
                    "g_profile",
                    "PROFILE_RELATION",
                    hypothesis_ids=["h_rename", "h_type", "h_cast"],
                ),
            )
        if not histories:
            tool = _tool_call(
                "get_relation_history",
                {"relation_name": "raw_orders"},
                "history",
            )
            return _with_intent(
                tool,
                strategy,
                _intent(
                    "g_history",
                    "COMPARE_HISTORY",
                    hypothesis_ids=["h_rename", "h_type", "h_cast"],
                ),
            )
        evidence_ids = [
            record.evidence_id
            for record in (*run_records, *node_errors, *lineages, *profiles, *histories)
        ]
        unresolved = [
            {
                "evidence_kind": "RELATION_SCHEMA",
                "subject": blocked_relation,
                "reason_code": "RELATION_NOT_ALLOWED",
            },
            {
                "evidence_kind": "TRANSFORMATION_DEFINITION",
                "subject": _transform_subject(upstream),
                "reason_code": "NOT_OBSERVABLE",
            },
        ]
        if strategy is DiagnosticStrategy.STATIC_SKILL:
            payload = {
                "status": "INSUFFICIENT_EVIDENCE",
                "run_id": run_id,
                "summary": "Two compatible causes remain because decisive evidence is unavailable.",
                "affected_assets": [],
                "evidence_ids": evidence_ids,
                "claims": [],
                "unresolved_evidence": unresolved,
                "recommended_actions": ["Collect the unavailable source and transformation facts."],
                "confidence": 0.3,
            }
        else:
            payload = {
                "status": "INSUFFICIENT_EVIDENCE",
                "run_id": run_id,
                "selected_hypothesis_id": None,
                "assessments": [],
                "claims": [],
                "unresolved_evidence": unresolved,
                "summary": "Two compatible causes remain because decisive evidence is unavailable.",
                "recommended_actions": ["Collect the unavailable source and transformation facts."],
                "confidence": 0.3,
            }
        return _tool_call(agent_info.output_tools[0].name, payload, "diagnosis")

    if lineage_attempts < 2:
        tool = _tool_call(
            "get_dbt_lineage",
            {"node_id": failure_node, "direction": "downstream"},
            "downstream",
        )
        return _with_intent(
            tool,
            strategy,
            _intent(
                "g_impact",
                "MAP_IMPACT",
                hypothesis_ids=["h_rename", "h_type", "h_cast"],
            ),
        )

    changed_schema = next(
        record.content
        for record in schemas
        if isinstance(record.content, RelationSchemaFact)
        and (
            any(
                column.name == "total_amount" or column.name == "amount"
                and column.data_type == "text"
                for column in record.content.columns
            )
            or any(
                column.name == "user_id" and column.data_type == "text"
                for column in record.content.columns
            )
        )
    )
    schema = changed_schema
    node_error_id = node_errors[-1].evidence_id
    run_id_evidence = run_records[-1].evidence_id
    schema_id = next(
        record.evidence_id
        for record in schemas
        if record.content is changed_schema
    )
    lineage_id = downstream[-1].evidence_id
    if any(column.name == "total_amount" for column in schema.columns):
        selected_id = "h_rename"
        root_cause = "SOURCE_SCHEMA_COLUMN_RENAMED"
    else:
        selected_id = "h_type"
        root_cause = "SOURCE_SCHEMA_COLUMN_TYPE_CHANGED"
    assets = (
        failure_node,
        *(
            node.node_id
            for node in downstream[-1].content.related_nodes
            if node.resource_type == "model"
        ),
    )
    if strategy is DiagnosticStrategy.STATIC_SKILL:
        payload = {
            "status": "CONFIRMED",
            "run_id": run_id,
            "root_cause_code": root_cause,
            "summary": "The source schema change explains the failed model.",
            "affected_assets": assets,
            "evidence_ids": [run_id_evidence, node_error_id, schema_id, lineage_id],
            "claims": [
            {
                "kind": "ROOT_CAUSE",
                "root_cause_code": root_cause,
                "evidence_ids": [run_id_evidence, node_error_id, schema_id, lineage_id],
                },
                *(
                    {
                        "kind": "AFFECTED_ASSET",
                        "asset": asset,
                        "evidence_ids": [node_error_id if asset == failure_node else lineage_id],
                    }
                    for asset in assets
                ),
            ],
            "unresolved_evidence": [],
            "recommended_actions": ["Restore the source schema contract."],
            "confidence": 0.9,
        }
    else:
        assessments = [
            {
                "hypothesis_id": hypothesis_id,
                "verdict": "SUPPORTED" if hypothesis_id == selected_id else "REFUTED",
                "evidence_ids": [run_id_evidence, node_error_id, schema_id],
            }
            for hypothesis_id in ("h_rename", "h_type", "h_cast")
        ]
        payload = {
            "status": "CONFIRMED",
            "run_id": run_id,
            "selected_hypothesis_id": selected_id,
            "assessments": assessments,
            "claims": [
                {
                    "kind": "ROOT_CAUSE",
                    "value": root_cause,
                    "evidence_ids": [run_id_evidence, node_error_id, schema_id, lineage_id],
                },
                *(
                    {
                        "kind": "AFFECTED_ASSET",
                        "value": asset,
                        "evidence_ids": [node_error_id if asset == failure_node else lineage_id],
                    }
                    for asset in assets
                ),
            ],
            "unresolved_evidence": [],
            "summary": "The source schema change explains the failed model.",
            "recommended_actions": ["Restore the source schema contract."],
            "confidence": 0.9,
        }
    return _tool_call(agent_info.output_tools[0].name, payload, "diagnosis")


def _with_intent(
    response: ModelResponse,
    strategy: DiagnosticStrategy,
    intent: str,
) -> ModelResponse:
    if strategy is DiagnosticStrategy.STATIC_SKILL:
        return response
    if len(response.parts) != 1 or not isinstance(response.parts[0], ToolCallPart):
        raise AssertionError("business response must contain one tool call")
    return ModelResponse(parts=[TextPart(intent), response.parts[0]])


def _runner(project_root: Path, strategy: DiagnosticStrategy) -> EvaluationRunner:
    settings = Settings(_env_file=None)
    diagnostic_settings = DiagnosticSettings(_env_file=None)
    lab = IncidentLab(settings, project_root)

    def diagnosis_factory(run_id: str, selected_strategy: DiagnosticStrategy) -> DiagnosisRunner:
        assert selected_strategy is strategy
        public_context = resolve_run_context(run_id, project_root=project_root)
        return DiagnosisRunner.for_run(
            run_id,
            diagnostic_settings,
            selected_strategy,
            project_root,
            model=FunctionModel(
                partial(
                    _model_response,
                    run_id=run_id,
                    strategy=selected_strategy,
                    schema_relations=tuple(
                        public_context.runtime["observable_relations"]["schema"]
                    ),
                    profile_relations=tuple(
                        public_context.runtime["observable_relations"]["profile"]
                    ),
                )
            ),
            model_identity=ModelIdentity(
                provider="pydantic-function",
                model="m7-evidence-driven-function-model",
            ),
        )

    return EvaluationRunner(
        lab=lab,
        diagnostic_settings=diagnostic_settings,
        diagnosis_factory=diagnosis_factory,
        private_scenario_loader=lambda case_id: load_scenario_spec(case_id, project_root),
        private_verification_loader=lab.verifier.load_verification,
        evaluator=DeterministicEvaluator.evaluate,
        artifact_writer=ArtifactWriter(project_root),
        clock=lambda: datetime.now(UTC),
    )


@pytest.mark.e2e
@pytest.mark.parametrize("case_id", MATRIX_CASES, ids=MATRIX_CASES)
@pytest.mark.parametrize("strategy", MATRIX_STRATEGIES, ids=lambda value: value.value)
@pytest.mark.asyncio
async def test_p1_function_model_policy_matrix(
    project_root: Path,
    case_id: str,
    strategy: DiagnosticStrategy,
) -> None:
    result = await _runner(project_root, strategy).run(case_id, strategy)

    assert result.status is EvaluationStatus.PASSED
    assert result.evaluation.failed_check_codes == ()
    assert {path.name for path in result.artifact_dir.iterdir()} == set(ARTIFACT_FILENAMES)
    assert result.run_id == result.evaluation.run_id
    assert result.evaluation.incident_case_id == case_id
    assert result.evaluation.status is EvaluationStatus.PASSED
    assert result.evaluation.variant_role
    assert result.evaluation.answerability
    assert result.evaluation.expected_status
    assert result.artifact_dir.is_dir()

    diagnosis = json.loads(
        (result.artifact_dir / "diagnosis.json").read_text(encoding="utf-8")
    )
    assert diagnosis["status"] in {status.value for status in DiagnosisStatus}

    if case_id in P1_M8_SCENARIO_IDS:
        assert diagnosis["status"] == (
            "INSUFFICIENT_EVIDENCE"
            if case_id == "required_null_order_customer_b"
            else "CONFIRMED"
        )
        if case_id == "required_null_order_customer_b":
            assert diagnosis["root_cause_code"] is None
            assert diagnosis["affected_assets"] == []
            assert {
                (item["evidence_kind"], item["subject"], item["reason_code"])
                for item in diagnosis["unresolved_evidence"]
            } == {
                ("RELATION_DATA_PROFILE", "raw_orders", "RELATION_NOT_ALLOWED"),
                ("TRANSFORMATION_DEFINITION", "model.jaffle_shop.stg_orders", "NOT_OBSERVABLE"),
            }
        else:
            assert diagnosis["root_cause_code"] == "SOURCE_REQUIRED_FIELD_NULL"
            assert diagnosis["affected_assets"] in (
                ["model.jaffle_shop.stg_payments"],
                ["model.jaffle_shop.orders"],
            )

    trace = [
        json.loads(line)["event"]
        for line in (result.artifact_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert all(
        event["tool_name"] in {
            "get_dbt_run_results",
            "get_dbt_node_error",
            "get_relation_schema",
            "get_dbt_lineage",
            "get_relation_data_profile",
            "get_relation_history",
        }
        for event in trace
        if event.get("event_type") == "TOOL_CALL"
    )
