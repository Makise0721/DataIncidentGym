"""T12 planner on real DB/dbt evidence chains (integration).

A scripted model drives the planner's two action tools and the terminal output
tool through a real lab pipeline (reset, inject, build, diagnose, evaluate,
recover). The script only reacts to what the tools actually returned — the
failed node, the upstream source relation, the evidence ids of each receipt —
so the run proves the plan layer executes against real artifacts, not fixtures.

What this answers: the planner can run reliably on real products (diagnosis,
evaluation, artifacts, scoring inputs, recovery, offline rescoring all work).
What it does not answer: whether the planner is smarter — that needs a real
model and a new frozen identity.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from data_incident_gym.artifacts import ARTIFACT_FILENAMES, ArtifactWriter
from data_incident_gym.config import PROJECT_ROOT, Settings
from data_incident_gym.diagnosis import DiagnosticStrategy
from data_incident_gym.diagnostic_agent import ModelIdentity
from data_incident_gym.diagnostic_config import DiagnosticSettings
from data_incident_gym.evaluation import DeterministicEvaluator, EvaluationStatus
from data_incident_gym.evaluation_inputs import (
    ArtifactInputStatus,
    classify_scoring_inputs,
    load_evaluation_input_bundle,
)
from data_incident_gym.evaluation_rescore import score_run_offline
from data_incident_gym.evaluation_runner import EvaluationRunner
from data_incident_gym.lab import IncidentLab
from data_incident_gym.planner_agent import EvidencePlannerRunner
from data_incident_gym.scenarios import load_scenario_spec

# One entry per T12 dev-extension variant: which relation probe is decisive,
# what the refusal must look like when the variant withholds it, and the final
# answer the contract expects. Everything else (failed node, source relation,
# evidence ids) is read back from the real receipts at run time.
CASES = {
    "required_null_payment_id_distractor_a": {
        "probe": ("get_relation_data_profile", "raw_payments"),
        "expected_steps": 5,
        "answer": "CONFIRMED",
    },
    "required_null_payment_id_distractor_b": {
        "probe": ("get_relation_data_profile", "raw_payments"),
        "refusal": "RELATION_NOT_ALLOWED",
        "gap_kind": "RELATION_DATA_PROFILE",
        # 4 decisive steps plus 2 completion probes on still-observable
        # relations (the evaluator requires the contract's evidence types to
        # be present in the inventory even for a qualified abstention).
        "expected_steps": 6,
        "answer": "INSUFFICIENT_EVIDENCE",
    },
    "type_change_payment_amount_drift_a": {
        "probe": ("get_relation_schema", "raw_payments"),
        "expected_steps": 5,
        "answer": "CONFIRMED",
    },
    "type_change_payment_amount_drift_b": {
        "probe": ("get_relation_schema", "raw_payments"),
        "refusal": "RELATION_NOT_ALLOWED",
        "gap_kind": "RELATION_SCHEMA",
        "expected_steps": 6,
        "answer": "INSUFFICIENT_EVIDENCE",
    },
}

STG_PAYMENTS = "model.jaffle_shop.stg_payments"


def _plan_turns(messages: Iterable[ModelMessage]) -> list[dict[str, object]]:
    """The planner action-tool answers the model received, in order."""

    turns: list[dict[str, object]] = []
    for message in messages:
        for part in message.parts:
            if (
                isinstance(part, ToolReturnPart)
                and part.tool_name in ("plan_step", "close_obligation")
                and isinstance(part.content, str)
            ):
                payload = json.loads(part.content)
                payload["tool"] = part.tool_name
                turns.append(payload)
    return turns


def _records(turns: Iterable[dict[str, object]]) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for turn in turns:
        if turn.get("tool") == "plan_step" and turn.get("evidence"):
            records.extend(turn["evidence"])
    return records


def _records_of_kind(
    turns: Iterable[dict[str, object]], kind: str
) -> list[dict[str, object]]:
    return [record for record in _records(turns) if record.get("evidence_type") == kind]


def _failed_node(turns: list[dict[str, object]]) -> str:
    results = _records_of_kind(turns, "DBT_RUN_RESULTS")
    return results[-1]["content"]["failed_nodes"][0]


def _source_relation(turns: list[dict[str, object]]) -> str:
    # The upstream lineage carries the seed/source relations; a later
    # downstream fetch must not shadow it.
    lineage = next(
        record["content"]
        for record in _records_of_kind(turns, "DBT_LINEAGE")
        if record["content"]["direction"] == "upstream"
    )
    seeds = [
        node
        for node in lineage["related_nodes"]
        if node["resource_type"] in ("seed", "source")
    ]
    return min(seeds, key=lambda node: (node["distance"], node["name"]))["name"]


def _observable_relations(messages: list[ModelMessage]) -> dict[str, list[str]]:
    """The public per-tool relation whitelists from the run's user prompt."""

    from pydantic_ai.messages import UserPromptPart

    for message in messages:
        for part in message.parts:
            if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                payload = json.loads(part.content.split("\n", 1)[1])
                return {
                    str(key): [str(item) for item in value]
                    for key, value in payload["observable_relations"].items()
                }
    return {}


def _call(name: str, arguments: dict[str, object], call_id: str) -> ToolCallPart:
    return ToolCallPart(name, arguments, tool_call_id=call_id)


def _closes(turns: list[dict[str, object]]) -> list[ToolCallPart]:
    """One close per planned obligation: satisfied with its own evidence,
    revoked with the real refusal code when the backend refused."""

    parts: list[ToolCallPart] = []
    for index, turn in enumerate(
        [item for item in turns if item.get("tool") == "plan_step"]
    ):
        obligation_id = turn["obligation_id"]
        if turn["evidence_ids"]:
            parts.append(
                _call(
                    "close_obligation",
                    {
                        "obligation_id": obligation_id,
                        "outcome": "SATISFIED",
                        "evidence_ids": turn["evidence_ids"],
                    },
                    f"close-{index}",
                )
            )
        else:
            refusal = turn.get("refusal_code") or "no evidence"
            parts.append(
                _call(
                    "close_obligation",
                    {
                        "obligation_id": obligation_id,
                        "outcome": "REVOKED",
                        "reason": f"backend refused this call: {refusal}",
                    },
                    f"close-{index}",
                )
            )
    return parts


def _director(
    messages: list[ModelMessage],
    agent_info: AgentInfo,
    *,
    run_id: str,
    case: str,
) -> ModelResponse:
    from data_incident_gym.evidence_planner import obligation_id_for

    config = CASES[case]
    turns = _plan_turns(messages)
    steps = [item for item in turns if item.get("tool") == "plan_step"]
    # The plan answer echoes the derived obligation id (<tool>:<canonical
    # arguments>), which is exactly the dedupe key for "already stepped".
    stepped = {item["obligation_id"] for item in steps}

    def step(tool: str, arguments: dict[str, object], call_id: str) -> ModelResponse:
        return ModelResponse(parts=[_call("plan_step", {
            "tool_name": tool, "arguments": arguments, "intent": call_id
        }, call_id)])

    def already_stepped(tool: str, arguments: dict[str, object]) -> bool:
        return obligation_id_for(tool, {k: str(v) for k, v in arguments.items()}) in stepped

    if not _records_of_kind(turns, "DBT_RUN_RESULTS"):
        return step("get_dbt_run_results", {"run_id": run_id}, "run-results")
    node = _failed_node(turns)
    if not _records_of_kind(turns, "DBT_NODE_ERROR"):
        return step(
            "get_dbt_node_error", {"run_id": run_id, "node_id": node}, "node-error"
        )
    if not any(
        record["content"]["direction"] == "upstream"
        for record in _records_of_kind(turns, "DBT_LINEAGE")
    ):
        return step(
            "get_dbt_lineage", {"node_id": node, "direction": "upstream"}, "upstream"
        )
    relation = _source_relation(turns)
    probe_tool = config["probe"][0]
    probed = any(
        item["tool_name"] == probe_tool for item in steps
    )
    if not probed:
        return step(probe_tool, {"relation_name": relation}, "probe")
    if config["answer"] == "CONFIRMED":
        if case.startswith("required_null"):
            # Confirm only on the decisive fact: the profile shows nulls.
            columns = _records_of_kind(turns, "RELATION_DATA_PROFILE")[-1]["content"][
                "snapshot"
            ]["columns"]
            assert any(column["null_count"] > 0 for column in columns)
            extra = [
                ("get_relation_schema", {"relation_name": relation}, "schema"),
            ]
        else:
            # Confirm only on the decisive fact: the amount column is now text.
            schema_columns = _records_of_kind(turns, "RELATION_SCHEMA")[-1]["content"][
                "columns"
            ]
            amount = next(c for c in schema_columns if c["name"] == "amount")
            assert amount["data_type"] == "text"
            extra = [
                (
                    "get_dbt_lineage",
                    {"node_id": node, "direction": "downstream"},
                    "downstream",
                ),
            ]
        for tool, arguments, call_id in extra:
            if not already_stepped(tool, arguments):
                return step(tool, arguments, call_id)
    else:
        # Qualified abstention still completes what IS observable: for each
        # relation-evidence kind that has no successful record yet, probe its
        # first whitelisted relation that was not attempted (the refused
        # combination is never whitelisted). One probe per kind keeps the run
        # inside the eight-request budget.
        whitelists = _observable_relations(messages)
        for kind, tool, fact in (
            ("schema", "get_relation_schema", "RELATION_SCHEMA"),
            ("profile", "get_relation_data_profile", "RELATION_DATA_PROFILE"),
            ("history", "get_relation_history", "RELATION_HISTORY"),
        ):
            if _records_of_kind(turns, fact):
                continue
            candidate = next(
                (
                    name
                    for name in whitelists.get(kind, [])
                    if not already_stepped(tool, {"relation_name": name})
                ),
                None,
            )
            if candidate is not None:
                return step(tool, {"relation_name": candidate}, f"complete-{kind}")
    if not any(item.get("tool") == "close_obligation" for item in turns):
        return ModelResponse(parts=_closes(steps))

    # Terminal: the answer the contract expects, citing only real receipts.
    collected = [record["evidence_id"] for record in _records(turns)]
    if config["answer"] == "CONFIRMED":
        if case.startswith("required_null"):
            lineage_id = [
                record["evidence_id"]
                for record in _records_of_kind(turns, "DBT_LINEAGE")
                if record["content"]["direction"] == "upstream"
            ][-1]
            assets, asset_evidence = (STG_PAYMENTS,), (lineage_id,)
            root = "SOURCE_REQUIRED_FIELD_NULL"
        else:
            node_error_id = _records_of_kind(turns, "DBT_NODE_ERROR")[-1][
                "evidence_id"
            ]
            downstream = [
                record
                for record in _records_of_kind(turns, "DBT_LINEAGE")
                if record["content"]["direction"] == "downstream"
            ][-1]
            lineage_id = downstream["evidence_id"]
            assets = (
                STG_PAYMENTS,
                *(
                    item["node_id"]
                    for item in downstream["content"]["related_nodes"]
                    if item["resource_type"] == "model"
                ),
            )
            asset_evidence = tuple(
                node_error_id if asset == STG_PAYMENTS else lineage_id
                for asset in assets
            )
            root = "SOURCE_SCHEMA_COLUMN_TYPE_CHANGED"
        payload = {
            "status": "CONFIRMED",
            "summary": "The decisive public evidence confirms the source-side fault.",
            "root_cause_code": root,
            "affected_assets": list(assets),
            "evidence_ids": collected,
            "claims": [
                {
                    "kind": "ROOT_CAUSE",
                    "root_cause_code": root,
                    "evidence_ids": collected,
                },
                *(
                    {
                        "kind": "AFFECTED_ASSET",
                        "asset": asset,
                        "evidence_ids": [evidence],
                    }
                    for asset, evidence in zip(assets, asset_evidence, strict=True)
                ),
            ],
            "confidence": 0.9,
        }
    else:
        payload = {
            "status": "INSUFFICIENT_EVIDENCE",
            "summary": "The decisive relation evidence is not observable; abstaining.",
            "unresolved_evidence": [
                {
                    "evidence_kind": config["gap_kind"],
                    "subject": config["probe"][1],
                    "reason_code": config["refusal"],
                },
                {
                    "evidence_kind": "TRANSFORMATION_DEFINITION",
                    "subject": STG_PAYMENTS,
                    "reason_code": "NOT_OBSERVABLE",
                },
            ],
            "evidence_ids": collected,
            "confidence": 0.2,
        }
    return ModelResponse(
        parts=[_call(agent_info.output_tools[0].name, payload, "submit")]
    )


def _runner(project_root: Path, case: str) -> EvaluationRunner:
    settings = Settings(_env_file=None)
    diagnostic_settings = DiagnosticSettings(_env_file=None)

    def diagnosis_factory(run_id: str, strategy: DiagnosticStrategy):
        assert strategy is DiagnosticStrategy.EVIDENCE_PLANNER
        return EvidencePlannerRunner.for_run(
            run_id,
            diagnostic_settings,
            project_root,
            model=FunctionModel(partial(_director, run_id=run_id, case=case)),
            model_identity=ModelIdentity(
                provider="pydantic-function",
                model="scripted-planner-model",
            ),
        )

    lab = IncidentLab(settings, project_root)
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


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("case", sorted(CASES))
async def test_planner_runs_the_real_evidence_chain_end_to_end(case: str) -> None:
    result = await _runner(PROJECT_ROOT, case).run(case, DiagnosticStrategy.EVIDENCE_PLANNER)

    assert result.status is EvaluationStatus.PASSED
    assert result.evaluation.failed_check_codes == ()
    assert {path.name for path in result.artifact_dir.iterdir()} == set(ARTIFACT_FILENAMES)

    bundle = load_evaluation_input_bundle(PROJECT_ROOT, result.run_id)
    diagnosis_run = bundle.diagnosis_run
    config = CASES[case]
    scenario = load_scenario_spec(case)

    assert diagnosis_run.diagnosis.status.value == scenario.expected_status
    assert diagnosis_run.strategy is DiagnosticStrategy.EVIDENCE_PLANNER
    assert diagnosis_run.metrics.tool_call_attempts == config["expected_steps"]
    assert diagnosis_run.metrics.successful_tool_calls == (
        config["expected_steps"] - (1 if "refusal" in config else 0)
    )

    # The plan ledger rides along: steps, closes and the full state archive.
    kinds = [
        event.kind for event in diagnosis_run.trace if event.event_type == "PLAN"
    ]
    assert kinds[0] == "STEP"
    assert "CLOSE" in kinds and kinds[-1] == "STATE"
    tool_events = [
        event for event in diagnosis_run.trace if event.event_type == "TOOL_CALL"
    ]
    assert len(tool_events) == config["expected_steps"]
    if "refusal" in config:
        # A real backend refusal keeps its real code — never a PLAN_* code.
        refused = [
            event
            for event in tool_events
            if event.error_code is not None
        ]
        assert [event.error_code for event in refused] == [config["refusal"]]

    # Recovery closed the loop and the archived inputs re-score identically.
    assert bundle.recovery.recovered is True
    assert bundle.recovery.fingerprint is not None
    assert classify_scoring_inputs(PROJECT_ROOT, result.run_id).status is (
        ArtifactInputStatus.RE_SCORABLE
    )
    rescore = score_run_offline(PROJECT_ROOT, result.run_id)
    assert rescore.created is True
    assert rescore.diff.available is True
    assert rescore.changed_check_codes == ()
    assert rescore.evaluation.model_dump() == result.evaluation.model_dump()
