"""The scripted planner director is public-evidence-driven, not case-configured.

These are the offline regressions for that claim: the same director used by the
integration chain test is driven with synthetic, hand-built tool returns (real
fact and record models, no database). Changing one public fact — the null count,
the column type, the refusal code — must change the script's conclusion, and the
director must never receive a case id or an expected answer.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from pydantic_ai.messages import ModelRequest, ToolReturnPart, UserPromptPart

from data_incident_gym.evidence import (
    DbtLineageFact,
    DbtLineageNode,
    DbtNodeErrorFact,
    DbtRunResultsFact,
    EvidenceRecord,
    EvidenceSource,
    EvidenceType,
    RelationDataProfileFact,
    RelationSchemaColumn,
    RelationSchemaFact,
)
from data_incident_gym.evidence_planner import obligation_id_for
from data_incident_gym.profiles import ColumnProfileFact, RelationProfileSnapshot

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from integration.test_planner_real_evidence import _director  # noqa: E402

RUN_ID = "a" * 32
SOURCE = "raw_payments"
STG_PAYMENTS = "model.jaffle_shop.stg_payments"
TEST_NODE = "test.jaffle_shop.not_null_stg_payments_payment_id.c19cc50075"
NOW = datetime(2018, 4, 9, 12, 0, tzinfo=UTC)

_INFO = SimpleNamespace(output_tools=[SimpleNamespace(name="submit_diagnosis")])


_SOURCE_BY_TYPE = {
    EvidenceType.DBT_RUN_RESULTS: EvidenceSource.DBT_RUN_RESULTS,
    EvidenceType.DBT_NODE_ERROR: EvidenceSource.DBT_RUN_RESULTS,
    EvidenceType.DBT_LINEAGE: EvidenceSource.DBT_MANIFEST,
    EvidenceType.RELATION_SCHEMA: EvidenceSource.POSTGRES_CATALOG,
    EvidenceType.RELATION_DATA_PROFILE: EvidenceSource.POSTGRES_PROFILE_SNAPSHOT,
}


def _record(evidence_type: EvidenceType, subject: str, content) -> dict:
    record = EvidenceRecord.create(
        run_id=RUN_ID,
        evidence_type=evidence_type,
        source=_SOURCE_BY_TYPE[evidence_type],
        subject=subject,
        observed_at=NOW,
        content=content,
    )
    return record.model_dump(mode="json")


def _run_results(node: str) -> dict:
    return _record(
        EvidenceType.DBT_RUN_RESULTS,
        node,
        DbtRunResultsFact(
            kind="DBT_RUN_RESULTS",
            run_id=RUN_ID,
            run_status="FAILED",
            dbt_exit_code=1,
            failed_nodes=(node,),
            skipped_nodes=(),
        ),
    )


def _node_error(node: str, *, resource_type: str, message: str) -> dict:
    return _record(
        EvidenceType.DBT_NODE_ERROR,
        node,
        DbtNodeErrorFact(
            kind="DBT_NODE_ERROR",
            run_id=RUN_ID,
            node_id=node,
            resource_type=resource_type,
            status="error",
            message=message,
        ),
    )


def _upstream_lineage(node: str, related: tuple[DbtLineageNode, ...]) -> dict:
    return _record(
        EvidenceType.DBT_LINEAGE,
        f"{node}:upstream",
        DbtLineageFact(
            kind="DBT_LINEAGE",
            run_id=RUN_ID,
            node_id=node,
            direction="upstream",
            related_nodes=related,
        ),
    )


def _downstream_lineage(node: str, related: tuple[DbtLineageNode, ...]) -> dict:
    return _record(
        EvidenceType.DBT_LINEAGE,
        f"{node}:downstream",
        DbtLineageFact(
            kind="DBT_LINEAGE",
            run_id=RUN_ID,
            node_id=node,
            direction="downstream",
            related_nodes=related,
        ),
    )


def _profile(null_count: int) -> dict:
    return _record(
        EvidenceType.RELATION_DATA_PROFILE,
        SOURCE,
        RelationDataProfileFact(
            kind="RELATION_DATA_PROFILE",
            run_id=RUN_ID,
            relation_name=SOURCE,
            profile_spec_version="profile_spec.v1",
            profile_spec_sha256="b" * 64,
            snapshot=RelationProfileSnapshot(
                relation_name=SOURCE,
                row_count=100,
                columns=(
                    ColumnProfileFact(
                        column_name="id", null_count=null_count, distinct_count=100
                    ),
                ),
            ),
        ),
    )


def _schema(amount_type: str) -> dict:
    return _record(
        EvidenceType.RELATION_SCHEMA,
        SOURCE,
        RelationSchemaFact(
            kind="RELATION_SCHEMA",
            run_id=RUN_ID,
            schema_name="public",
            relation_name=SOURCE,
            columns=(
                RelationSchemaColumn(
                    name="id", data_type="integer", nullable=False, ordinal_position=1
                ),
                RelationSchemaColumn(
                    name="amount",
                    data_type=amount_type,
                    nullable=False,
                    ordinal_position=2,
                ),
            ),
        ),
    )


def _plan_payload(
    tool_name: str,
    arguments: dict[str, object],
    records: tuple[dict, ...] = (),
    *,
    refusal_code: str | None = None,
) -> dict:
    return {
        "tool_name": tool_name,
        "obligation_id": obligation_id_for(
            tool_name, {key: str(value) for key, value in arguments.items()}
        ),
        "accepted": refusal_code is None,
        "verdict_code": None,
        "detail": None,
        "tool_calls_used": 1,
        "plan_refusals_used": 0,
        "plan_refusal_limit": 2,
        "evidence_ids": [record["evidence_id"] for record in records],
        "refusal_code": refusal_code,
        "evidence": list(records),
    }


def _prompt(observable: dict[str, list[str]]) -> ModelRequest:
    payload = {
        "run_id": RUN_ID,
        "incident_brief": {"summary": "a required-field dbt test failed"},
        "observable_relations": observable,
    }
    return ModelRequest(
        parts=[UserPromptPart(content="Investigate the run.\n" + json.dumps(payload))]
    )


def _drive(history: list, responder) -> tuple[list, dict]:
    """Run the director until it submits; the responder synthesizes returns."""

    messages = list(history)
    calls = []
    for _ in range(12):
        response = _director(messages, _INFO, run_id=RUN_ID)
        part = response.parts[0]
        calls.append(part)
        if part.tool_name == "submit_diagnosis":
            return calls, part.args
        if part.tool_name == "close_obligation":
            payload = {
                "accepted": True,
                "obligation_id": part.args["obligation_id"],
                "requested_outcome": part.args["outcome"],
            }
        else:
            payload = responder(part)
        messages.append(
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name=part.tool_name, content=json.dumps(payload)
                    )
                ]
            )
        )
    raise AssertionError("director did not terminate")


def _test_failure_history(profile: dict, schema: dict) -> tuple[list, object]:
    """A failing-test investigation whose facts the caller supplies."""

    related = (
        DbtLineageNode(
            node_id=STG_PAYMENTS, resource_type="model", name="stg_payments", distance=1
        ),
        DbtLineageNode(
            node_id="seed.jaffle_shop.raw_payments",
            resource_type="seed",
            name=SOURCE,
            distance=2,
        ),
    )

    def responder(part) -> dict:
        inner, args = part.args["tool_name"], part.args["arguments"]
        if inner == "get_dbt_run_results":
            return _plan_payload(inner, args, (_run_results(TEST_NODE),))
        if inner == "get_dbt_node_error":
            return _plan_payload(
                inner,
                args,
                (_node_error(TEST_NODE, resource_type="test", message="FAIL 1 rows"),),
            )
        if inner == "get_dbt_lineage":
            return _plan_payload(inner, args, (_upstream_lineage(TEST_NODE, related),))
        if inner == "get_relation_data_profile":
            return _plan_payload(inner, args, (profile,))
        if inner == "get_relation_schema":
            return _plan_payload(inner, args, (schema,))
        raise AssertionError(f"unexpected tool {inner}")

    history = [
        _prompt({"schema": [SOURCE], "profile": [SOURCE], "history": []}),
    ]
    return history, responder


def _build_failure_history(schema: dict) -> tuple[list, object]:
    related = (
        DbtLineageNode(
            node_id="seed.jaffle_shop.raw_payments",
            resource_type="seed",
            name=SOURCE,
            distance=1,
        ),
    )
    downstream = (
        DbtLineageNode(
            node_id="model.jaffle_shop.orders",
            resource_type="model",
            name="orders",
            distance=1,
        ),
    )

    def responder(part) -> dict:
        inner, args = part.args["tool_name"], part.args["arguments"]
        if inner == "get_dbt_run_results":
            return _plan_payload(inner, args, (_run_results(STG_PAYMENTS),))
        if inner == "get_dbt_node_error":
            return _plan_payload(
                inner,
                args,
                (
                    _node_error(
                        STG_PAYMENTS,
                        resource_type="model",
                        message='invalid input syntax for type integer: "3.50" in column amount',
                    ),
                ),
            )
        if inner == "get_dbt_lineage":
            if args["direction"] == "upstream":
                return _plan_payload(
                    inner, args, (_upstream_lineage(STG_PAYMENTS, related),)
                )
            return _plan_payload(
                inner, args, (_downstream_lineage(STG_PAYMENTS, downstream),)
            )
        if inner == "get_relation_schema":
            return _plan_payload(inner, args, (schema,))
        raise AssertionError(f"unexpected tool {inner}")

    history = [
        _prompt({"schema": [SOURCE], "profile": [SOURCE], "history": []}),
    ]
    return history, responder


def test_the_null_fact_confirms_the_null_cause() -> None:
    history, responder = _test_failure_history(_profile(null_count=1), _schema("integer"))

    calls, submission = _drive(history, responder)

    assert [call.tool_name for call in calls][:5] == [
        "plan_step",
        "plan_step",
        "plan_step",
        "plan_step",
        "plan_step",
    ]
    assert [call.args["tool_name"] for call in calls if call.tool_name == "plan_step"][-2:] == [
        "get_relation_data_profile",
        "get_relation_schema",
    ]
    assert submission["status"] == "CONFIRMED"
    assert submission["root_cause_code"] == "SOURCE_REQUIRED_FIELD_NULL"
    assert submission["affected_assets"] == [STG_PAYMENTS]


def test_without_the_null_fact_the_same_history_does_not_confirm() -> None:
    history, responder = _test_failure_history(_profile(null_count=0), _schema("integer"))

    calls, submission = _drive(history, responder)

    assert submission["status"] == "INSUFFICIENT_EVIDENCE"
    assert "root_cause_code" not in submission
    # The script did probe the decisive relation; the fact simply was not there.
    assert "get_relation_data_profile" in [
        call.args["tool_name"] for call in calls if call.tool_name == "plan_step"
    ]


def test_the_type_mismatch_fact_confirms_the_type_change() -> None:
    history, responder = _build_failure_history(_schema("text"))

    calls, submission = _drive(history, responder)

    steps = [call.args for call in calls if call.tool_name == "plan_step"]
    assert [item["tool_name"] for item in steps] == [
        "get_dbt_run_results",
        "get_dbt_node_error",
        "get_dbt_lineage",
        "get_relation_schema",
        "get_dbt_lineage",
    ]
    assert submission["status"] == "CONFIRMED"
    assert submission["root_cause_code"] == "SOURCE_SCHEMA_COLUMN_TYPE_CHANGED"
    assert submission["affected_assets"] == [
        STG_PAYMENTS,
        "model.jaffle_shop.orders",
    ]


def test_an_integer_schema_does_not_confirm_the_type_change() -> None:
    history, responder = _build_failure_history(_schema("integer"))

    _, submission = _drive(history, responder)

    assert submission["status"] == "INSUFFICIENT_EVIDENCE"
    assert "root_cause_code" not in submission


def test_the_abstention_gaps_follow_the_real_refusal_receipt() -> None:
    related = (
        DbtLineageNode(
            node_id=STG_PAYMENTS, resource_type="model", name="stg_payments", distance=1
        ),
        DbtLineageNode(
            node_id="seed.jaffle_shop.raw_payments",
            resource_type="seed",
            name=SOURCE,
            distance=2,
        ),
    )
    refusal = {"code": "RELATION_NOT_ALLOWED"}

    def responder(part) -> dict:
        inner, args = part.args["tool_name"], part.args["arguments"]
        if inner == "get_dbt_run_results":
            return _plan_payload(inner, args, (_run_results(TEST_NODE),))
        if inner == "get_dbt_node_error":
            return _plan_payload(
                inner,
                args,
                (_node_error(TEST_NODE, resource_type="test", message="FAIL 1 rows"),),
            )
        if inner == "get_dbt_lineage":
            return _plan_payload(inner, args, (_upstream_lineage(TEST_NODE, related),))
        if inner == "get_relation_data_profile":
            # The first probe is refused; the completion probe succeeds.
            if args["relation_name"] == SOURCE:
                return _plan_payload(inner, args, refusal_code=refusal["code"])
            return _plan_payload(inner, args, (_profile(null_count=0),))
        if inner == "get_relation_schema":
            return _plan_payload(inner, args, (_schema("integer"),))
        raise AssertionError(f"unexpected tool {inner}")

    history = [
        _prompt({"schema": [SOURCE], "profile": ["raw_customers"], "history": []}),
    ]

    _, submission = _drive(history, responder)

    assert submission["status"] == "INSUFFICIENT_EVIDENCE"
    assert submission["unresolved_evidence"] == [
        {
            "evidence_kind": "RELATION_DATA_PROFILE",
            "subject": SOURCE,
            "reason_code": "RELATION_NOT_ALLOWED",
        },
        {
            "evidence_kind": "TRANSFORMATION_DEFINITION",
            "subject": STG_PAYMENTS,
            "reason_code": "NOT_OBSERVABLE",
        },
    ]

    # Change only the public fact: the declared gap must follow the receipt.
    refusal["code"] = "TOOL_BUDGET_EXHAUSTED"
    _, changed = _drive(
        [*history], responder
    )

    assert changed["unresolved_evidence"][0]["reason_code"] == "TOOL_BUDGET_EXHAUSTED"
