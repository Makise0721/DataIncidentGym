from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from data_incident_gym.config import PROJECT_ROOT
from data_incident_gym.diagnosis import DiagnosisStatus, DiagnosticStrategy
from data_incident_gym.evidence import (
    DbtLineageFact,
    DbtLineageNode,
    DbtNodeErrorFact,
    DbtRunResultsFact,
    EvidenceRecord,
    EvidenceSource,
    EvidenceType,
    RelationDataProfileFact,
    RelationHistoryFact,
    RelationNotAllowedError,
    RelationSchemaColumn,
    RelationSchemaFact,
)
from data_incident_gym.fixed_rule import FIXED_RULE_TOOL_LIMIT, fixed_rule_policy_identity
from data_incident_gym.profiles import (
    ColumnProfileFact,
    DuplicateProfileFact,
    RelationHistorySnapshot,
    RelationProfileSnapshot,
)
from data_incident_gym.reference_solver import (
    REFERENCE_ANALYST_VERSION,
    ReferenceAnalystRunner,
    reference_analyst_policy_identity,
)
from data_incident_gym.run_context import IncidentBrief, ObservableRunContext

RUN_ID = "b" * 32
OBSERVED_AT = datetime(2026, 9, 15, tzinfo=UTC)
FORBIDDEN_PRIVATE_TOKENS = (
    "config/scenarios",
    "load_scenario_spec",
    "ScenarioSpec",
    "answerability",
    "expected_status",
    "variant_role",
    ".dig/lab/private",
)


def _record(content, evidence_type: EvidenceType, subject: str) -> EvidenceRecord:
    source = (
        EvidenceSource.DBT_RUN_RESULTS
        if evidence_type in {EvidenceType.DBT_RUN_RESULTS, EvidenceType.DBT_NODE_ERROR}
        else EvidenceSource.DBT_MANIFEST
        if evidence_type is EvidenceType.DBT_LINEAGE
        else EvidenceSource.POSTGRES_CATALOG
        if evidence_type is EvidenceType.RELATION_SCHEMA
        else EvidenceSource.POSTGRES_PROFILE_SNAPSHOT
    )
    return EvidenceRecord.create(
        run_id=RUN_ID,
        evidence_type=evidence_type,
        source=source,
        subject=subject,
        observed_at=OBSERVED_AT,
        content=content,
    )


def _context(
    tmp_path: Path,
    *,
    signal_code: str,
    summary: str,
    subjects: tuple[str, ...],
    schema_relations: tuple[str, ...] = (),
    profile_relations: tuple[str, ...] = (),
) -> ObservableRunContext:
    return ObservableRunContext(
        run_id=RUN_ID,
        artifact_dir=tmp_path,
        runtime={
            "observable_relations": {
                "schema": list(schema_relations),
                "profile": list(profile_relations),
                "history": [],
            }
        },
        incident_brief=IncidentBrief(
            schema_version="incident_brief.v1",
            signal_code=signal_code,
            summary=summary,
            subjects=subjects,
            logical_observed_at=OBSERVED_AT,
            observations=(),
        ),
    )


def _run(failed_nodes: tuple[str, ...]) -> EvidenceRecord:
    return _record(
        DbtRunResultsFact(
            kind="DBT_RUN_RESULTS",
            run_id=RUN_ID,
            run_status="FAILED" if failed_nodes else "SUCCEEDED",
            dbt_exit_code=1 if failed_nodes else 0,
            failed_nodes=failed_nodes,
            skipped_nodes=(),
        ),
        EvidenceType.DBT_RUN_RESULTS,
        RUN_ID,
    )


def _node_error(node_id: str, message: str, resource_type: str) -> EvidenceRecord:
    return _record(
        DbtNodeErrorFact(
            kind="DBT_NODE_ERROR",
            run_id=RUN_ID,
            node_id=node_id,
            resource_type=resource_type,
            status="fail",
            message=message,
        ),
        EvidenceType.DBT_NODE_ERROR,
        node_id,
    )


def _lineage(
    node_id: str,
    direction: str,
    nodes: tuple[tuple[str, str, int], ...],
) -> EvidenceRecord:
    return _record(
        DbtLineageFact(
            kind="DBT_LINEAGE",
            run_id=RUN_ID,
            node_id=node_id,
            direction=direction,
            related_nodes=tuple(
                DbtLineageNode(
                    node_id=name,
                    resource_type=kind,
                    name=name.rsplit(".", 1)[-1],
                    distance=distance,
                )
                for name, kind, distance in nodes
            ),
        ),
        EvidenceType.DBT_LINEAGE,
        node_id,
    )


def _schema(relation: str, columns: tuple[tuple[str, str], ...]) -> EvidenceRecord:
    return _record(
        RelationSchemaFact(
            kind="RELATION_SCHEMA",
            run_id=RUN_ID,
            schema_name="analytics",
            relation_name=relation,
            columns=tuple(
                RelationSchemaColumn(
                    name=name,
                    data_type=data_type,
                    nullable=True,
                    ordinal_position=index,
                )
                for index, (name, data_type) in enumerate(columns, start=1)
            ),
        ),
        EvidenceType.RELATION_SCHEMA,
        f"analytics.{relation}",
    )


def _profile(
    relation: str,
    columns: tuple[tuple[str, int], ...],
    *,
    key_duplicate: int = 0,
    fingerprint_duplicate: int = 0,
) -> EvidenceRecord:
    return _record(
        RelationDataProfileFact(
            kind="RELATION_DATA_PROFILE",
            run_id=RUN_ID,
            relation_name=relation,
            profile_spec_version="profile_spec.v1",
            profile_spec_sha256="a" * 64,
            snapshot=RelationProfileSnapshot(
                relation_name=relation,
                row_count=100,
                columns=tuple(
                    ColumnProfileFact(column_name=name, null_count=nulls, distinct_count=1)
                    for name, nulls in columns
                ),
                business_key_duplicates=(
                    DuplicateProfileFact(name="id", duplicate_count=key_duplicate),
                ),
                business_fingerprint_duplicates=(
                    DuplicateProfileFact(
                        name="order_payment_amount", duplicate_count=fingerprint_duplicate
                    ),
                ),
                relationship_violations=(),
                groups=(),
            ),
        ),
        EvidenceType.RELATION_DATA_PROFILE,
        relation,
    )


def _history(relation: str) -> EvidenceRecord:
    return _record(
        RelationHistoryFact(
            kind="RELATION_HISTORY",
            run_id=RUN_ID,
            relation_name=relation,
            profile_spec_version="profile_spec.v1",
            profile_spec_sha256="a" * 64,
            snapshot=RelationHistorySnapshot(relation_name=relation, histories=()),
        ),
        EvidenceType.RELATION_HISTORY,
        relation,
    )


class _Tools:
    def __init__(
        self,
        records: dict[tuple[str, str | None], tuple[EvidenceRecord, ...]],
        *,
        blocked: tuple[tuple[str, str], ...] = (),
    ) -> None:
        self._records = records
        self._blocked = set(blocked)
        self.calls: list[tuple[str, str | None]] = []

    def _get(
        self,
        tool: str,
        key: str | None,
        relation: str | None = None,
    ) -> tuple[EvidenceRecord, ...]:
        self.calls.append((tool, relation or key))
        if relation is not None and (tool, relation) in self._blocked:
            raise RelationNotAllowedError("Relation is not allowed")
        return self._records.get((tool, key), self._records.get((tool, None), ()))

    def get_dbt_run_results(self, run_id: str) -> tuple[EvidenceRecord, ...]:
        return self._get("get_dbt_run_results", run_id)

    def get_dbt_node_error(self, run_id: str, node_id: str) -> tuple[EvidenceRecord, ...]:
        return self._get("get_dbt_node_error", node_id)

    def get_dbt_lineage(self, node_id: str, direction: str) -> tuple[EvidenceRecord, ...]:
        return self._get("get_dbt_lineage", f"{node_id}|{direction}")

    def get_relation_schema(self, relation_name: str) -> tuple[EvidenceRecord, ...]:
        return self._get("get_relation_schema", relation_name, relation_name)

    def get_relation_data_profile(self, relation_name: str) -> tuple[EvidenceRecord, ...]:
        return self._get("get_relation_data_profile", relation_name, relation_name)

    def get_relation_history(self, relation_name: str) -> tuple[EvidenceRecord, ...]:
        return self._get("get_relation_history", relation_name, relation_name)


def _runner(
    tmp_path: Path,
    records: dict[tuple[str, str | None], tuple[EvidenceRecord, ...]],
    context: ObservableRunContext,
    *,
    blocked: tuple[tuple[str, str], ...] = (),
) -> tuple[ReferenceAnalystRunner, _Tools]:
    tools = _Tools(records, blocked=blocked)
    runner = ReferenceAnalystRunner(
        run_id=RUN_ID,
        settings=SimpleNamespace(),
        project_root=tmp_path,
        tools=tools,
        context=context,
    )
    return runner, tools


def test_reference_identity_is_separate_from_fixed_rule(tmp_path: Path) -> None:
    reference = reference_analyst_policy_identity()
    fixed = fixed_rule_policy_identity()

    assert reference.strategy is DiagnosticStrategy.REFERENCE_ANALYST
    assert reference.strategy_prompt_version == REFERENCE_ANALYST_VERSION
    assert reference.controller_protocol_sha256 != fixed.controller_protocol_sha256
    runner, _ = _runner(
        tmp_path,
        {},
        _context(
            tmp_path,
            signal_code="DBT_BUILD_FAILED",
            summary="A model failed to build.",
            subjects=("model.jaffle_shop.stg_payments",),
        ),
    )
    assert runner.strategy is DiagnosticStrategy.REFERENCE_ANALYST


@pytest.mark.asyncio
async def test_rename_rule_confirms_missing_source_column(tmp_path: Path) -> None:
    failed = "model.jaffle_shop.stg_payments"
    message = (
        "Database Error in model stg_payments (models/staging/stg_payments.sql)\n"
        'column "amount" does not exist\n'
        "LINE 20:         amount / 100 as amount"
    )
    records = {
        ("get_dbt_run_results", None): (_run((failed,)),),
        ("get_dbt_node_error", failed): (_node_error(failed, message, "model"),),
        ("get_dbt_lineage", f"{failed}|upstream"): (
            _lineage(failed, "upstream", (("seed.jaffle_shop.raw_payments", "seed", 1),)),
        ),
        ("get_dbt_lineage", f"{failed}|downstream"): (
            _lineage(
                failed,
                "downstream",
                (
                    ("model.jaffle_shop.orders", "model", 1),
                    ("model.jaffle_shop.customers", "model", 1),
                ),
            ),
        ),
        ("get_relation_schema", "raw_payments"): (
            _schema(
                "raw_payments",
                (
                    ("id", "integer"),
                    ("order_id", "integer"),
                    ("payment_method", "text"),
                    ("total_amount", "integer"),
                ),
            ),
        ),
    }
    runner, tools = _runner(
        tmp_path,
        records,
        _context(
            tmp_path,
            signal_code="DBT_BUILD_FAILED",
            summary="A model failed to build in the payment pipeline.",
            subjects=(failed,),
            schema_relations=("raw_payments",),
        ),
    )

    result = await runner.diagnose()

    diagnosis = result.diagnosis
    assert diagnosis.status is DiagnosisStatus.CONFIRMED
    assert diagnosis.root_cause_code == "SOURCE_SCHEMA_COLUMN_RENAMED"
    assert set(diagnosis.affected_assets) == {
        "model.jaffle_shop.stg_payments",
        "model.jaffle_shop.orders",
        "model.jaffle_shop.customers",
    }
    root_claim = diagnosis.claims[0]
    cited_kinds = {
        record.content.kind
        for record in result.evidence_records
        if record.evidence_id in root_claim.evidence_ids
    }
    assert {"DBT_NODE_ERROR", "RELATION_SCHEMA", "DBT_RUN_RESULTS"} <= cited_kinds
    assert len(tools.calls) <= FIXED_RULE_TOOL_LIMIT


@pytest.mark.asyncio
async def test_type_rule_uses_named_string_column(tmp_path: Path) -> None:
    failed = "model.jaffle_shop.stg_payments"
    message = (
        "Database Error in model stg_payments (models/staging/stg_payments.sql)\n"
        "operator does not exist: text / integer\n"
        "LINE 20:         amount / 100 as amount"
    )
    records = {
        ("get_dbt_run_results", None): (_run((failed,)),),
        ("get_dbt_node_error", failed): (_node_error(failed, message, "model"),),
        ("get_dbt_lineage", f"{failed}|upstream"): (
            _lineage(failed, "upstream", (("seed.jaffle_shop.raw_payments", "seed", 1),)),
        ),
        ("get_dbt_lineage", f"{failed}|downstream"): (
            _lineage(failed, "downstream", (("model.jaffle_shop.orders", "model", 1),)),
        ),
        ("get_relation_schema", "raw_payments"): (
            _schema(
                "raw_payments",
                (
                    ("id", "integer"),
                    ("payment_method", "text"),
                    ("amount", "text"),
                    ("source_batch_note", "text"),
                ),
            ),
        ),
    }
    runner, _ = _runner(
        tmp_path,
        records,
        _context(
            tmp_path,
            signal_code="DBT_BUILD_FAILED",
            summary="A model failed to build in the payment pipeline.",
            subjects=(failed,),
            schema_relations=("raw_payments",),
        ),
    )

    result = await runner.diagnose()

    assert result.diagnosis.status is DiagnosisStatus.CONFIRMED
    assert result.diagnosis.root_cause_code == "SOURCE_SCHEMA_COLUMN_TYPE_CHANGED"
    root_claim = result.diagnosis.claims[0]
    cited_relations = {
        record.content.relation_name
        for record in result.evidence_records
        if record.evidence_id in root_claim.evidence_ids
        and record.content.kind == "RELATION_SCHEMA"
    }
    assert cited_relations == {"raw_payments"}


@pytest.mark.asyncio
async def test_type_rule_uses_key_column_for_join_mismatch(tmp_path: Path) -> None:
    failed = "model.jaffle_shop.customers"
    message = (
        "Database Error in model customers (models/customers.sql)\n"
        "operator does not exist: integer = text\n"
        "LINE 73:         on customers.customer_id = customer_orders.customer_id"
    )
    records = {
        ("get_dbt_run_results", None): (_run((failed,)),),
        ("get_dbt_node_error", failed): (_node_error(failed, message, "model"),),
        ("get_dbt_lineage", f"{failed}|upstream"): (
            _lineage(
                failed,
                "upstream",
                (
                    ("seed.jaffle_shop.raw_customers", "seed", 2),
                    ("seed.jaffle_shop.raw_orders", "seed", 2),
                    ("seed.jaffle_shop.raw_payments", "seed", 2),
                ),
            ),
        ),
        ("get_dbt_lineage", f"{failed}|downstream"): (
            _lineage(failed, "downstream", ()),
        ),
        ("get_relation_schema", "raw_customers"): (
            _schema(
                "raw_customers",
                (("id", "integer"), ("first_name", "text"), ("last_name", "text")),
            ),
        ),
        ("get_relation_schema", "raw_orders"): (
            _schema(
                "raw_orders",
                (
                    ("id", "integer"),
                    ("user_id", "text"),
                    ("order_date", "date"),
                    ("status", "text"),
                ),
            ),
        ),
        ("get_relation_schema", "raw_payments"): (
            _schema("raw_payments", (("id", "integer"), ("order_id", "integer"))),
        ),
    }
    runner, _ = _runner(
        tmp_path,
        records,
        _context(
            tmp_path,
            signal_code="DBT_BUILD_FAILED",
            summary="A model failed to build in the order pipeline.",
            subjects=(failed,),
            schema_relations=("raw_customers", "raw_orders", "raw_payments"),
        ),
    )

    result = await runner.diagnose()

    assert result.diagnosis.status is DiagnosisStatus.CONFIRMED
    assert result.diagnosis.root_cause_code == "SOURCE_SCHEMA_COLUMN_TYPE_CHANGED"
    root_claim = result.diagnosis.claims[0]
    cited_relations = {
        record.content.relation_name
        for record in result.evidence_records
        if record.evidence_id in root_claim.evidence_ids
        and record.content.kind == "RELATION_SCHEMA"
    }
    # The decisive relation is the only upstream seed whose key column is
    # string-typed; the claim cites every collected record, as the fixed-rule
    # engine does, and the evaluator checks the mutated relation among them.
    assert "raw_orders" in cited_relations
    assert result.metrics.tool_call_attempts <= FIXED_RULE_TOOL_LIMIT


@pytest.mark.asyncio
async def test_refusal_names_blocked_relation_and_transformation(tmp_path: Path) -> None:
    failed = "model.jaffle_shop.customers"
    message = (
        "Database Error in model customers (models/customers.sql)\n"
        "operator does not exist: integer = text\n"
        "LINE 73:         on customers.customer_id = customer_orders.customer_id"
    )
    records = {
        ("get_dbt_run_results", None): (_run((failed,)),),
        ("get_dbt_node_error", failed): (_node_error(failed, message, "model"),),
        ("get_dbt_lineage", f"{failed}|upstream"): (
            _lineage(
                failed,
                "upstream",
                (
                    ("model.jaffle_shop.stg_customers", "model", 1),
                    ("model.jaffle_shop.stg_orders", "model", 1),
                    ("seed.jaffle_shop.raw_customers", "seed", 2),
                    ("seed.jaffle_shop.raw_orders", "seed", 2),
                    ("seed.jaffle_shop.raw_payments", "seed", 2),
                ),
            ),
        ),
        ("get_relation_schema", "raw_customers"): (
            _schema(
                "raw_customers",
                (("id", "integer"), ("first_name", "text"), ("last_name", "text")),
            ),
        ),
        ("get_relation_schema", "raw_payments"): (
            _schema("raw_payments", (("id", "integer"), ("order_id", "integer"))),
        ),
        ("get_relation_data_profile", "raw_orders"): (
            _profile("raw_orders", (("id", 0), ("user_id", 0))),
        ),
        ("get_relation_history", "raw_orders"): (_history("raw_orders"),),
    }
    runner, _ = _runner(
        tmp_path,
        records,
        _context(
            tmp_path,
            signal_code="DBT_BUILD_FAILED",
            summary="A model failed to build in the order pipeline.",
            subjects=(failed,),
            schema_relations=("raw_customers", "raw_payments"),
        ),
        blocked=(("get_relation_schema", "raw_orders"),),
    )

    result = await runner.diagnose()

    assert result.diagnosis.status is DiagnosisStatus.INSUFFICIENT_EVIDENCE
    gaps = {
        (item.evidence_kind, item.subject, item.reason_code)
        for item in result.diagnosis.unresolved_evidence
    }
    assert gaps == {
        ("RELATION_SCHEMA", "raw_orders", "RELATION_NOT_ALLOWED"),
        ("TRANSFORMATION_DEFINITION", "model.jaffle_shop.stg_orders", "NOT_OBSERVABLE"),
    }
    blocked_events = [
        event
        for event in result.trace
        if getattr(event, "error_code", None) == "RELATION_NOT_ALLOWED"
    ]
    assert len(blocked_events) == 1


@pytest.mark.asyncio
async def test_test_branch_detects_exact_and_semantic_duplicates(tmp_path: Path) -> None:
    failed = "test.jaffle_shop.unique_stg_payments_payment_id.3744510712"
    message = "Got 1 result, configured to fail if != 0"
    base_records = {
        ("get_dbt_run_results", None): (_run((failed,)),),
        ("get_dbt_node_error", failed): (_node_error(failed, message, "test"),),
        ("get_dbt_lineage", f"{failed}|upstream"): (
            _lineage(
                failed,
                "upstream",
                (
                    ("model.jaffle_shop.stg_payments", "model", 1),
                    ("seed.jaffle_shop.raw_payments", "seed", 2),
                ),
            ),
        ),
    }
    context = _context(
        tmp_path,
        signal_code="DBT_TEST_FAILED",
        summary="A required-field dbt test failed in the payment pipeline.",
        subjects=(failed, "raw_payments"),
        profile_relations=("raw_payments",),
    )

    exact_runner, _ = _runner(
        # exact duplicate: positive id duplicates
        tmp_path,
        {
            **base_records,
            ("get_relation_data_profile", "raw_payments"): (
                _profile("raw_payments", (("id", 0),), key_duplicate=1),
            ),
        },
        context,
    )
    exact = await exact_runner.diagnose()
    assert exact.diagnosis.status is DiagnosisStatus.CONFIRMED
    assert exact.diagnosis.root_cause_code == "SOURCE_EXACT_PAYMENT_DUPLICATE"

    semantic_runner, _ = _runner(
        tmp_path,
        {
            **base_records,
            ("get_relation_data_profile", "raw_payments"): (
                _profile("raw_payments", (("id", 0),), fingerprint_duplicate=1),
            ),
        },
        context,
    )
    semantic = await semantic_runner.diagnose()
    assert semantic.diagnosis.status is DiagnosisStatus.CONFIRMED
    assert semantic.diagnosis.root_cause_code == "SOURCE_SEMANTIC_PAYMENT_DUPLICATE"


@pytest.mark.asyncio
async def test_rules_ignore_scenario_wording_and_distractors(tmp_path: Path) -> None:
    """The same public evidence must decide the same way under renamed wording."""

    failed = "model.jaffle_shop.stg_payments"
    message = (
        "Database Error in model stg_payments (models/staging/stg_payments.sql)\n"
        'column "amount" does not exist\n'
        "LINE 20:         amount / 100 as amount"
    )
    records = {
        ("get_dbt_run_results", None): (_run((failed,)),),
        ("get_dbt_node_error", failed): (_node_error(failed, message, "model"),),
        ("get_dbt_lineage", f"{failed}|upstream"): (
            _lineage(failed, "upstream", (("seed.jaffle_shop.raw_payments", "seed", 1),)),
        ),
        ("get_dbt_lineage", f"{failed}|downstream"): (
            _lineage(failed, "downstream", (("model.jaffle_shop.orders", "model", 1),)),
        ),
        ("get_relation_schema", "raw_payments"): (
            _schema(
                "raw_payments",
                (
                    ("id", "integer"),
                    ("payment_method", "text"),
                    ("source_batch_note", "text"),
                    ("total_amount", "integer"),
                ),
            ),
        ),
    }
    renamed_context = _context(
        tmp_path,
        signal_code="DBT_BUILD_FAILED",
        summary="A renamed regression alias failed during the nightly refresh.",
        subjects=("model.renamed.stg_payments",),
        schema_relations=("raw_payments",),
    )
    runner, _ = _runner(tmp_path, records, renamed_context)

    result = await runner.diagnose()

    assert result.diagnosis.status is DiagnosisStatus.CONFIRMED
    assert result.diagnosis.root_cause_code == "SOURCE_SCHEMA_COLUMN_RENAMED"


@pytest.mark.asyncio
async def test_transformation_subject_falls_back_on_unmatched_staging_name(
    tmp_path: Path,
) -> None:
    """Known assumption, renamed counter-example: a seed whose name matches no
    stg model falls back to the nearest distance-1 model as the subject."""

    failed = "model.jaffle_shop.customers"
    message = (
        "Database Error in model customers (models/customers.sql)\n"
        'syntax error at or near "from"\n'
        "LINE 8: from {{ ref('stg_customers') }}"
    )
    records = {
        ("get_dbt_run_results", None): (_run((failed,)),),
        ("get_dbt_node_error", failed): (_node_error(failed, message, "model"),),
        ("get_dbt_lineage", f"{failed}|upstream"): (
            _lineage(
                failed,
                "upstream",
                (
                    ("model.jaffle_shop.stg_customers", "model", 1),
                    ("seed.jaffle_shop.raw_order_header", "seed", 2),
                ),
            ),
        ),
        ("get_relation_schema", "stg_customers"): (
            _schema("stg_customers", (("customer_id", "integer"),)),
        ),
        ("get_relation_schema", "raw_order_header"): (
            _schema("raw_order_header", (("header_id", "integer"),)),
        ),
    }
    runner, _ = _runner(
        tmp_path,
        records,
        _context(
            tmp_path,
            signal_code="DBT_BUILD_FAILED",
            summary="A model failed to build.",
            subjects=(failed,),
            schema_relations=("stg_customers", "raw_order_header"),
        ),
    )

    result = await runner.diagnose()

    gaps = {
        (item.evidence_kind, item.subject, item.reason_code)
        for item in result.diagnosis.unresolved_evidence
    }
    assert (
        "TRANSFORMATION_DEFINITION",
        "model.jaffle_shop.stg_customers",
        "NOT_OBSERVABLE",
    ) in gaps


@pytest.mark.asyncio
async def test_transformation_subject_is_deterministic_under_ambiguous_names(
    tmp_path: Path,
) -> None:
    """Known assumption, ambiguity counter-example: when several stg models
    match the seed token the first in lineage order wins, deterministically."""

    failed = "model.jaffle_shop.orders"
    message = "Got 1 result, configured to fail if != 0"
    lineage_nodes = (
        ("model.jaffle_shop.orders", "model", 1),
        ("model.jaffle_shop.stg_orders_archive", "model", 2),
        ("model.jaffle_shop.stg_orders", "model", 2),
        ("seed.jaffle_shop.raw_orders", "seed", 3),
    )
    records = {
        ("get_dbt_run_results", None): (_run((failed,)),),
        ("get_dbt_node_error", failed): (_node_error(failed, message, "test"),),
        ("get_dbt_lineage", f"{failed}|upstream"): (
            _lineage(failed, "upstream", lineage_nodes),
        ),
        ("get_relation_data_profile", "raw_orders"): (
            _profile("raw_orders", (("user_id", 0),)),
        ),
    }
    runner_a, _ = _runner(
        tmp_path,
        records,
        _context(
            tmp_path,
            signal_code="DBT_TEST_FAILED",
            summary="A required-field dbt test failed in the order pipeline.",
            subjects=(failed, "raw_orders"),
            profile_relations=("raw_orders",),
        ),
    )
    runner_b, _ = _runner(
        tmp_path,
        records,
        _context(
            tmp_path,
            signal_code="DBT_TEST_FAILED",
            summary="A required-field dbt test failed in the order pipeline.",
            subjects=(failed, "raw_orders"),
            profile_relations=("raw_orders",),
        ),
    )

    first = await runner_a.diagnose()
    second = await runner_b.diagnose()

    assert (
        first.diagnosis.unresolved_evidence == second.diagnosis.unresolved_evidence
    )
    subjects = {
        item.subject for item in first.diagnosis.unresolved_evidence
        if item.evidence_kind == "TRANSFORMATION_DEFINITION"
    }
    assert subjects == {"model.jaffle_shop.stg_orders_archive"}


def test_reference_solver_holds_no_private_access() -> None:
    source = (PROJECT_ROOT / "src" / "data_incident_gym" / "reference_solver.py").read_text(
        encoding="utf-8"
    )

    for token in FORBIDDEN_PRIVATE_TOKENS:
        assert token not in source, token
