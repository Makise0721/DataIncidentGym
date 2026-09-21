"""T12 planner on real DB/dbt evidence chains (integration).

A scripted model drives the planner's two action tools and the terminal output
tool through a real lab pipeline (reset, inject, build, diagnose, evaluate,
recover). The script carries **no case configuration**: every decision — which
relation to probe, what the receipts mean, whether to confirm or abstain, and
which gaps an abstention must declare — is derived from what the tools actually
returned (run results, node error message, lineage graph, relation facts, the
public relation whitelist in the run prompt). The expected answers live only on
the test side, which asserts against the private scenario contract.

What this answers: a public-evidence-driven script can complete the planner's
plan → evidence → close → submit loop on real products, and the whole pipeline
(diagnosis, evaluation, artifacts, scoring inputs, recovery, offline rescoring)
holds together. What it does not answer: whether a real model would reason this
way, or whether the planner is smarter — both need a real model and a new
frozen identity.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
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
from data_incident_gym.evidence_planner import obligation_id_for
from data_incident_gym.lab import IncidentLab
from data_incident_gym.planner_agent import EvidencePlannerRunner
from data_incident_gym.scenarios import load_scenario_spec

PLANNER_CASES = (
    "required_null_payment_id_distractor_a",
    "required_null_payment_id_distractor_b",
    "type_change_payment_amount_drift_a",
    "type_change_payment_amount_drift_b",
)

#: Executed tool calls per case, derived from the script's policy (not from the
#: answers): decisive path plus completion probes or the downstream fetch.
EXPECTED_TOOL_CALLS = {
    "required_null_payment_id_distractor_a": 5,
    "required_null_payment_id_distractor_b": 6,
    "type_change_payment_amount_drift_a": 5,
    "type_change_payment_amount_drift_b": 6,
}

_STAGING_PREFIX = "stg_"
_MISSING_COLUMN_PATTERN = re.compile(r'(?i)column "([a-z0-9_]+)" does not exist')
_TYPE_MISMATCH_PATTERN = re.compile(
    r"(?i)(operator does not exist|cannot cast|invalid input syntax|does not exist: \w+)"
)
_IDENTIFIER_PATTERN = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*")
_STRING_TYPES = frozenset({"text", "character varying", "character", "varchar", "string"})

_RELATION_TOOLS = (
    ("schema", "get_relation_schema", "RELATION_SCHEMA"),
    ("profile", "get_relation_data_profile", "RELATION_DATA_PROFILE"),
    ("history", "get_relation_history", "RELATION_HISTORY"),
)


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


def _observable_relations(messages: list[ModelMessage]) -> dict[str, list[str]]:
    """The public per-tool relation whitelists from the run's user prompt."""

    for message in messages:
        for part in message.parts:
            if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                payload = json.loads(part.content.split("\n", 1)[1])
                return {
                    str(key): [str(item) for item in value]
                    for key, value in payload["observable_relations"].items()
                }
    return {}


def _nearest_seed_source(upstream: dict[str, object]) -> str | None:
    seeds = [
        node
        for node in upstream["related_nodes"]
        if node["resource_type"] in ("seed", "source")
    ]
    if not seeds:
        return None
    return min(seeds, key=lambda node: (node["distance"], node["name"]))["name"]


def _transformation_subject(
    upstream: dict[str, object], source_relation: str, failure_node: str
) -> str:
    """A public derivation of the unavailable transformation's subject.

    Mirror of the reference analyst's public heuristic: the staging model that
    consumes the source (same trailing token), else the nearest model, else the
    failure node itself. It never reads the private contract.
    """

    models = [
        node
        for node in upstream["related_nodes"]
        if node["resource_type"] == "model"
    ]
    token = source_relation.rsplit("_", 1)[-1].lower()
    staged = [
        node
        for node in models
        if node["name"].startswith(_STAGING_PREFIX) and token in node["name"].lower()
    ]
    if staged:
        return min(staged, key=lambda node: node["distance"])["node_id"]
    if models:
        return min(models, key=lambda node: node["distance"])["node_id"]
    return failure_node


def _affected_model(upstream: dict[str, object]) -> str | None:
    """The model a failing test belongs to: the nearest upstream model."""

    models = [
        node
        for node in upstream["related_nodes"]
        if node["resource_type"] == "model"
    ]
    if not models:
        return None
    return min(models, key=lambda node: node["distance"])["node_id"]


def _call(name: str, arguments: dict[str, object], call_id: str) -> ToolCallPart:
    return ToolCallPart(name, arguments, tool_call_id=call_id)


def _closes(turns: list[dict[str, object]]) -> list[ToolCallPart]:
    """One close per planned obligation: satisfied with its own evidence,
    revoked with whatever real refusal the backend returned."""

    parts: list[ToolCallPart] = []
    for index, turn in enumerate(
        [item for item in turns if item.get("tool") == "plan_step"]
    ):
        if turn["evidence_ids"]:
            parts.append(
                _call(
                    "close_obligation",
                    {
                        "obligation_id": turn["obligation_id"],
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
                        "obligation_id": turn["obligation_id"],
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
) -> ModelResponse:
    """The scripted policy: everything below branches on public facts only."""

    turns = _plan_turns(messages)
    steps = [item for item in turns if item.get("tool") == "plan_step"]
    stepped = {item["obligation_id"] for item in steps}

    def step(tool: str, arguments: dict[str, object], call_id: str) -> ModelResponse:
        return ModelResponse(
            parts=[
                _call(
                    "plan_step",
                    {"tool_name": tool, "arguments": arguments, "intent": call_id},
                    call_id,
                )
            ]
        )

    def already_stepped(tool: str, arguments: dict[str, object]) -> bool:
        return obligation_id_for(tool, {k: str(v) for k, v in arguments.items()}) in (
            stepped
        )

    results = _records_of_kind(turns, "DBT_RUN_RESULTS")
    if not results:
        return step("get_dbt_run_results", {"run_id": run_id}, "run-results")
    failed_nodes = results[-1]["content"]["failed_nodes"]
    if len(failed_nodes) != 1:
        # More than one failure is beyond this script's public policy.
        return _abstain_without_gap_evidence(agent_info, "ambiguous failures")
    node = failed_nodes[0]

    errors = _records_of_kind(turns, "DBT_NODE_ERROR")
    if not errors:
        return step("get_dbt_node_error", {"run_id": run_id, "node_id": node}, "node-error")
    node_error = errors[-1]["content"]

    upstream_records = [
        record
        for record in _records_of_kind(turns, "DBT_LINEAGE")
        if record["content"]["direction"] == "upstream"
    ]
    if not upstream_records:
        return step(
            "get_dbt_lineage", {"node_id": node, "direction": "upstream"}, "upstream"
        )
    upstream = upstream_records[-1]["content"]
    source = _nearest_seed_source(upstream)
    if source is None:
        return _abstain_without_gap_evidence(agent_info, "no source relation")

    # A failing test points at the source contract through its profile; a
    # failing model points at the source schema through the build error.
    is_test = node_error.get("resource_type") == "test"
    probe_tool = "get_relation_data_profile" if is_test else "get_relation_schema"
    probe_kind = "RELATION_DATA_PROFILE" if is_test else "RELATION_SCHEMA"
    probe = next((item for item in steps if item["tool_name"] == probe_tool), None)
    if probe is None:
        return step(probe_tool, {"relation_name": source}, "probe")

    refused_code = probe.get("refusal_code")
    if refused_code is not None:
        # Qualified abstention: complete what remains observable, then declare
        # the gaps the real receipts prove.
        whitelists = _observable_relations(messages)
        for kind, tool, fact in _RELATION_TOOLS:
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

    collected = [record["evidence_id"] for record in _records(turns)]
    subject = _transformation_subject(upstream, source, node)

    if refused_code is not None:
        if not any(item.get("tool") == "close_obligation" for item in turns):
            return ModelResponse(parts=_closes(steps))
        payload = {
            "status": "INSUFFICIENT_EVIDENCE",
            "summary": "The decisive relation evidence is refused; abstaining.",
            "unresolved_evidence": [
                {
                    "evidence_kind": probe_kind,
                    "subject": source,
                    "reason_code": refused_code,
                },
                {
                    "evidence_kind": "TRANSFORMATION_DEFINITION",
                    "subject": subject,
                    "reason_code": "NOT_OBSERVABLE",
                },
            ],
            "evidence_ids": collected,
            "confidence": 0.2,
        }
        return ModelResponse(parts=[_call(agent_info.output_tools[0].name, payload, "submit")])

    # Read the decisive fact out of the receipt.
    if probe_tool == "get_relation_data_profile":
        columns = probe["evidence"][-1]["content"]["snapshot"]["columns"]
        decisive = any(column["null_count"] > 0 for column in columns)
        root_cause = "SOURCE_REQUIRED_FIELD_NULL"
        if decisive:
            extra = [("get_relation_schema", {"relation_name": source}, "schema")]
    else:
        schema_columns = probe["evidence"][-1]["content"]["columns"]
        message = str(node_error.get("message", ""))
        tokens = {token.lower() for token in _IDENTIFIER_PATTERN.findall(message)}
        named_strings = [
            column
            for column in schema_columns
            if column["data_type"].lower() in _STRING_TYPES
            and column["name"].lower() in tokens
        ]
        missing = _MISSING_COLUMN_PATTERN.search(message)
        if missing is not None and missing.group(1).lower() not in {
            column["name"].lower() for column in schema_columns
        }:
            decisive, root_cause = True, "SOURCE_SCHEMA_COLUMN_RENAMED"
        elif named_strings and _TYPE_MISMATCH_PATTERN.search(message):
            decisive, root_cause = True, "SOURCE_SCHEMA_COLUMN_TYPE_CHANGED"
        else:
            decisive, root_cause = False, None
        if decisive:
            extra = [("get_dbt_lineage", {"node_id": node, "direction": "downstream"},
                      "downstream")]
    if decisive:
        # Complete the confirming evidence before closing anything.
        for tool, arguments, call_id in extra:
            if not already_stepped(tool, arguments):
                return step(tool, arguments, call_id)

    if not any(item.get("tool") == "close_obligation" for item in turns):
        return ModelResponse(parts=_closes(steps))

    if not decisive:
        payload = {
            "status": "INSUFFICIENT_EVIDENCE",
            "summary": "The public evidence does not single out one root cause.",
            "unresolved_evidence": [
                {
                    "evidence_kind": "TRANSFORMATION_DEFINITION",
                    "subject": subject,
                    "reason_code": "NOT_OBSERVABLE",
                },
                {
                    "evidence_kind": "RELATION_DATA_PROFILE",
                    "subject": source,
                    "reason_code": "NOT_OBSERVABLE",
                },
            ],
            "evidence_ids": collected,
            "confidence": 0.3,
        }
        return ModelResponse(parts=[_call(agent_info.output_tools[0].name, payload, "submit")])

    # Confirmed: assets follow the failure's public shape.
    if is_test:
        model = _affected_model(upstream)
        lineage_id = upstream_records[-1]["evidence_id"]
        assets = (model,)
        asset_evidence = (lineage_id,)
    else:
        node_error_id = errors[-1]["evidence_id"]
        downstream = next(
            (
                record
                for record in _records_of_kind(turns, "DBT_LINEAGE")
                if record["content"]["direction"] == "downstream"
            ),
            None,
        )
        assets = (node,)
        asset_evidence = (node_error_id,)
        if downstream is not None:
            assets = (
                node,
                *(
                    item["node_id"]
                    for item in downstream["content"]["related_nodes"]
                    if item["resource_type"] == "model"
                ),
            )
            asset_evidence = (node_error_id,) + (downstream["evidence_id"],) * (
                len(assets) - 1
            )
    payload = {
        "status": "CONFIRMED",
        "summary": "The decisive public evidence confirms the source-side fault.",
        "root_cause_code": root_cause,
        "affected_assets": list(assets),
        "evidence_ids": collected,
        "claims": [
            {
                "kind": "ROOT_CAUSE",
                "root_cause_code": root_cause,
                "evidence_ids": collected,
            },
            *(
                {"kind": "AFFECTED_ASSET", "asset": asset, "evidence_ids": [evidence]}
                for asset, evidence in zip(assets, asset_evidence, strict=True)
            ),
        ],
        "confidence": 0.9,
    }
    return ModelResponse(parts=[_call(agent_info.output_tools[0].name, payload, "submit")])


def _abstain_without_gap_evidence(agent_info: AgentInfo, reason: str) -> ModelResponse:
    """Terminal for histories this policy cannot interpret (never anticipated
    by the four fixtures; kept total so a surprise ends in an answer, not an
    exception)."""

    return ModelResponse(
        parts=[
            _call(
                agent_info.output_tools[0].name,
                {
                    "status": "INSUFFICIENT_EVIDENCE",
                    "summary": f"Public evidence is incomplete: {reason}.",
                    "evidence_ids": [],
                    "confidence": 0.1,
                },
                "submit",
            )
        ]
    )


def _runner(project_root: Path) -> EvaluationRunner:
    settings = Settings(_env_file=None)
    diagnostic_settings = DiagnosticSettings(_env_file=None)

    def diagnosis_factory(
        run_id: str,
        strategy: DiagnosticStrategy,
        submission_policy: object | None = None,
    ):
        assert strategy is DiagnosticStrategy.EVIDENCE_PLANNER
        return EvidencePlannerRunner.for_run(
            run_id,
            diagnostic_settings,
            project_root,
            submission_policy=submission_policy,
            model=FunctionModel(partial(_director, run_id=run_id)),
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
@pytest.mark.parametrize("case", PLANNER_CASES)
async def test_planner_runs_the_real_evidence_chain_end_to_end(case: str) -> None:
    result = await _runner(PROJECT_ROOT).run(case, DiagnosticStrategy.EVIDENCE_PLANNER)

    assert result.status is EvaluationStatus.PASSED
    assert result.evaluation.failed_check_codes == ()
    assert {path.name for path in result.artifact_dir.iterdir()} == set(ARTIFACT_FILENAMES)

    bundle = load_evaluation_input_bundle(PROJECT_ROOT, result.run_id)
    diagnosis_run = bundle.diagnosis_run
    scenario = load_scenario_spec(case)
    diagnosis = diagnosis_run.diagnosis

    assert diagnosis_run.strategy is DiagnosticStrategy.EVIDENCE_PLANNER
    assert diagnosis_run.metrics.tool_call_attempts == EXPECTED_TOOL_CALLS[case]
    assert diagnosis.status.value == scenario.expected_status

    # The expectations live here, not in the script: the contract is the
    # ground truth the evaluator itself scores against.
    expected_gaps = {
        (gap.gap_kind, gap.subject, gap.reason_code)
        for gap in scenario.observable_evidence_contract.unresolved_gaps
    }
    if scenario.expected_status == "CONFIRMED":
        assert diagnosis.root_cause_code in scenario.ground_truth_or_acceptable_root_causes
        assert set(diagnosis.affected_assets) == set(scenario.affected_assets)
    else:
        assert {
            (item.evidence_kind, item.subject, item.reason_code)
            for item in diagnosis.unresolved_evidence
        } == expected_gaps
        refused = sum(
            1
            for event in diagnosis_run.trace
            if event.event_type == "TOOL_CALL" and event.error_code is not None
        )
        assert refused == sum(
            1 for gap in scenario.observable_evidence_contract.unresolved_gaps
            if gap.tool_name is not None
        )

    # The plan ledger rides along: steps, closes and the full state archive.
    kinds = [event.kind for event in diagnosis_run.trace if event.event_type == "PLAN"]
    assert kinds[0] == "STEP"
    assert "CLOSE" in kinds and kinds[-1] == "STATE"
    tool_events = [
        event for event in diagnosis_run.trace if event.event_type == "TOOL_CALL"
    ]
    assert len(tool_events) == EXPECTED_TOOL_CALLS[case]
    # Real backend refusals keep their real code — never a PLAN_* code.
    for event in tool_events:
        assert event.error_code is None or not event.error_code.startswith("PLAN_")
    for gap in scenario.observable_evidence_contract.unresolved_gaps:
        if gap.tool_name is None:
            continue
        matching = [
            event
            for event in tool_events
            if event.tool_name == gap.tool_name
            and event.error_code == gap.reason_code
            and gap.subject in event.arguments.values()
        ]
        assert len(matching) == 1

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
