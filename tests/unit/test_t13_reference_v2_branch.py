"""T13 slice 4, increment 3: the reference analyst's v2 decision branch.

The offline contract for the T13 A/B pairs, driven against the same byte-exact
compiled SQL and failure message the authorized dry run produced:

- **A variants** confirm ``SOURCE_SCHEMA_COLUMN_TYPE_CHANGED`` only through the
  column-mapping reader over E1/E2 and the identity bridge — once with the
  deviation on the left origin, once mirrored onto the right origin;
- **the B variant** abstains with exactly the two contract gaps, each backed by
  the real per-target refusal receipt in the archived trace;
- **the abstention path is exhaustive**: a missing bridge, a reader UNKNOWN,
  an incomplete definition, a non-unique deviation and a deviation the failing
  expression does not read all land in ``INSUFFICIENT_EVIDENCE``, never in a
  confirmation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from data_incident_gym.diagnosis import (
    DiagnosisStatus,
    DiagnosisV2,
    refusal_witnessed,
)
from data_incident_gym.evidence import (
    BatchTargetsRefusedError,
    DbtLineageFact,
    DbtLineageNode,
    DbtNodeDefinitionFact,
    DbtNodeErrorFact,
    DbtRunResultsFact,
    EvidenceRecord,
    EvidenceSource,
    EvidenceType,
    ExpectedColumn,
    RelationSchemaColumn,
    RelationSchemaExpectationFact,
    RelationSchemaFact,
)
from data_incident_gym.fixed_rule import FIXED_RULE_TOOL_LIMIT
from data_incident_gym.reference_solver import ReferenceAnalystRunner
from data_incident_gym.run_context import (
    RUNTIME_V2_SCHEMA_VERSION,
    IncidentBrief,
    ObservableRunContext,
)
from data_incident_gym.strategy_adapter import (
    EVIDENCE_V2_TOOL_ALLOWLIST,
    FinalSubmissionV2,
    ProtocolTools,
    StrategySession,
    builtin_declaration,
    tool_allowlist_for_context,
)

# The archived compiled texts and the failure message of the authorized dry
# run, byte-exact, shared with the reader regression (single source).
from tests.unit.test_t13_column_mapping import (  # noqa: E402
    REAL_CUSTOMERS,
    REAL_STG_CUSTOMERS,
    REAL_STG_ORDERS,
    REAL_TRUNCATED_MESSAGE,
)

RUN_ID = "c" * 32
OBSERVED_AT = datetime(2026, 9, 18, tzinfo=UTC)
FAILURE_NODE = "model.jaffle_shop.customers"
STG_CUSTOMERS_IDENTITY = "data_incident_gym.analytics.stg_customers"
STG_ORDERS_IDENTITY = "data_incident_gym.analytics.stg_orders"
RAW_CUSTOMERS_IDENTITY = "data_incident_gym.analytics.raw_customers"
RAW_ORDERS_IDENTITY = "data_incident_gym.analytics.raw_orders"
B_GAPS = {
    ("RELATION_SCHEMA_EXPECTATION", "raw_customers", "RELATION_NOT_ALLOWED"),
    ("DBT_NODE_DEFINITION", FAILURE_NODE, "NODE_NOT_ALLOWED"),
}
UPSTREAM_LINEAGE = (
    ("model.jaffle_shop.stg_customers", "model", 1),
    ("model.jaffle_shop.stg_orders", "model", 1),
    ("model.jaffle_shop.stg_payments", "model", 1),
    ("seed.jaffle_shop.raw_customers", "seed", 2),
    ("seed.jaffle_shop.raw_orders", "seed", 2),
    ("seed.jaffle_shop.raw_payments", "seed", 2),
)


# -- fixtures -----------------------------------------------------------------


def _fact_record(content, evidence_type: EvidenceType, subject: str) -> EvidenceRecord:
    source = {
        EvidenceType.DBT_RUN_RESULTS: EvidenceSource.DBT_RUN_RESULTS,
        EvidenceType.DBT_NODE_ERROR: EvidenceSource.DBT_RUN_RESULTS,
        EvidenceType.DBT_LINEAGE: EvidenceSource.DBT_MANIFEST,
        EvidenceType.RELATION_SCHEMA: EvidenceSource.POSTGRES_CATALOG,
        EvidenceType.RELATION_SCHEMA_EXPECTATION: EvidenceSource.RUN_BASELINE,
        EvidenceType.DBT_NODE_DEFINITION: EvidenceSource.DBT_MANIFEST,
    }[evidence_type]
    return EvidenceRecord.create(
        run_id=RUN_ID,
        evidence_type=evidence_type,
        source=source,
        subject=subject,
        observed_at=OBSERVED_AT,
        content=content,
    )


def _v2_context(
    tmp_path: Path,
    *,
    expectation: tuple[str, ...],
    definition: tuple[str, ...],
) -> ObservableRunContext:
    return ObservableRunContext(
        run_id=RUN_ID,
        artifact_dir=tmp_path,
        runtime={
            "schema_version": RUNTIME_V2_SCHEMA_VERSION,
            "observable_relations": {
                "schema": ["raw_customers", "raw_orders"],
                "profile": [],
                "history": [],
                "expectation": list(expectation),
            },
            "observable_nodes": {"definition": list(definition)},
        },
        incident_brief=IncidentBrief(
            schema_version="incident_brief.v1",
            signal_code="DBT_BUILD_FAILED",
            summary="A customer-facing dbt model failed after an upstream schema change.",
            subjects=(FAILURE_NODE,),
            logical_observed_at=OBSERVED_AT,
            observations=(),
        ),
    )


def _lineage_record() -> EvidenceRecord:
    return _fact_record(
        DbtLineageFact(
            kind="DBT_LINEAGE",
            run_id=RUN_ID,
            node_id=FAILURE_NODE,
            direction="upstream",
            related_nodes=tuple(
                DbtLineageNode(
                    node_id=node_id,
                    resource_type=resource_type,
                    name=node_id.rsplit(".", 1)[-1],
                    distance=distance,
                )
                for node_id, resource_type, distance in UPSTREAM_LINEAGE
            ),
        ),
        EvidenceType.DBT_LINEAGE,
        FAILURE_NODE,
    )


def _schema_record(relation: str, columns: tuple[tuple[str, str], ...]) -> EvidenceRecord:
    return _fact_record(
        RelationSchemaFact(
            kind="RELATION_SCHEMA",
            run_id=RUN_ID,
            schema_name="analytics",
            relation_name=relation,
            columns=tuple(
                RelationSchemaColumn(
                    name=name, data_type=data_type, nullable=True, ordinal_position=index
                )
                for index, (name, data_type) in enumerate(columns, start=1)
            ),
        ),
        EvidenceType.RELATION_SCHEMA,
        relation,
    )


def _expectation_fact(
    relation: str,
    identity: str | None,
    resource_type: str | None,
    columns: tuple[tuple[str, str], ...],
) -> RelationSchemaExpectationFact:
    return RelationSchemaExpectationFact(
        kind="RELATION_SCHEMA_EXPECTATION",
        run_id=RUN_ID,
        relation_name=relation,
        baseline_fingerprint="a" * 64,
        known=True,
        columns=tuple(
            ExpectedColumn(
                name=name,
                expected_data_type=data_type,
                expected_nullable=True,
                ordinal_position=index,
            )
            for index, (name, data_type) in enumerate(columns, start=1)
        ),
        relation_identity=identity,
        resource_type=resource_type,
    )


def _definition_fact(
    node_id: str,
    *,
    identity: str | None,
    sql: str | None,
    complete: bool = True,
) -> DbtNodeDefinitionFact:
    return DbtNodeDefinitionFact(
        kind="DBT_NODE_DEFINITION",
        run_id=RUN_ID,
        node_id=node_id,
        known=sql is not None,
        resource_type="model",
        name=node_id.rsplit(".", 1)[-1],
        relation_identity=identity,
        compiled_sql=sql,
        complete=complete,
    )


_RAW_CUSTOMERS_HEALTHY = (("id", "integer"), ("first_name", "text"), ("last_name", "text"))
_RAW_ORDERS_HEALTHY = (
    ("id", "integer"),
    ("user_id", "integer"),
    ("order_date", "date"),
    ("status", "text"),
)


class _Tools:
    """The public surface with the real batch refusal semantics."""

    def __init__(
        self,
        *,
        schemas: dict[str, EvidenceRecord],
        expectations: dict[str, EvidenceRecord],
        definitions: dict[str, EvidenceRecord],
        expectation_allowed: tuple[str, ...],
        definition_allowed: tuple[str, ...],
    ) -> None:
        self._schemas = schemas
        self._expectations = expectations
        self._definitions = definitions
        self._expectation_allowed = frozenset(expectation_allowed)
        self._definition_allowed = frozenset(definition_allowed)
        self.calls: list[tuple[str, str]] = []

    def _batch(
        self,
        tool: str,
        raw: str,
        allowed: frozenset[str],
        facts: dict[str, EvidenceRecord],
        code: str,
    ) -> tuple[EvidenceRecord, ...]:
        self.calls.append((tool, raw))
        targets = [part for part in raw.split(",") if part]
        refusals = tuple((target, code) for target in targets if target not in allowed)
        if refusals:
            raise BatchTargetsRefusedError("not granted", target_refusals=refusals)
        return tuple(facts[target] for target in targets)

    # -- the six v1 tools --------------------------------------------------

    def get_dbt_run_results(self, run_id: str) -> tuple[EvidenceRecord, ...]:
        self.calls.append(("get_dbt_run_results", run_id))
        return (
            _fact_record(
                DbtRunResultsFact(
                    kind="DBT_RUN_RESULTS",
                    run_id=RUN_ID,
                    run_status="FAILED",
                    dbt_exit_code=1,
                    failed_nodes=(FAILURE_NODE,),
                    skipped_nodes=(),
                ),
                EvidenceType.DBT_RUN_RESULTS,
                RUN_ID,
            ),
        )

    def get_dbt_node_error(self, run_id: str, node_id: str) -> tuple[EvidenceRecord, ...]:
        self.calls.append(("get_dbt_node_error", node_id))
        return (
            _fact_record(
                DbtNodeErrorFact(
                    kind="DBT_NODE_ERROR",
                    run_id=RUN_ID,
                    node_id=node_id,
                    resource_type="model",
                    status="error",
                    message=REAL_TRUNCATED_MESSAGE,
                ),
                EvidenceType.DBT_NODE_ERROR,
                node_id,
            ),
        )

    def get_dbt_lineage(self, node_id: str, direction: str) -> tuple[EvidenceRecord, ...]:
        self.calls.append(("get_dbt_lineage", f"{node_id}|{direction}"))
        if direction == "upstream":
            return (_lineage_record(),)
        return (
            _fact_record(
                DbtLineageFact(
                    kind="DBT_LINEAGE",
                    run_id=RUN_ID,
                    node_id=node_id,
                    direction=direction,
                    related_nodes=(),
                ),
                EvidenceType.DBT_LINEAGE,
                node_id,
            ),
        )

    def get_relation_schema(self, relation_name: str) -> tuple[EvidenceRecord, ...]:
        self.calls.append(("get_relation_schema", relation_name))
        return (self._schemas[relation_name],)

    # -- the two v2 batch tools --------------------------------------------

    def get_relation_schema_expectation(self, relation_names: str) -> tuple[EvidenceRecord, ...]:
        return self._batch(
            "get_relation_schema_expectation",
            relation_names,
            self._expectation_allowed,
            self._expectations,
            "RELATION_NOT_ALLOWED",
        )

    def get_dbt_node_definition(self, node_ids: str) -> tuple[EvidenceRecord, ...]:
        return self._batch(
            "get_dbt_node_definition",
            node_ids,
            self._definition_allowed,
            self._definitions,
            "NODE_NOT_ALLOWED",
        )


def _pair_records(*, customer_id_type: str, order_user_id_type: str) -> dict[str, EvidenceRecord]:
    """Observed schemas of one A-pair fixture: exactly one mutated column."""

    return {
        "raw_customers": _schema_record(
            "raw_customers",
            (
                ("id", customer_id_type),
                ("first_name", "text"),
                ("last_name", "text"),
            ),
        ),
        "raw_orders": _schema_record(
            "raw_orders",
            (
                ("id", "integer"),
                ("user_id", order_user_id_type),
                ("order_date", "date"),
                ("status", "text"),
            ),
        ),
    }


def _expectation_records(*, bridged: bool) -> dict[str, EvidenceRecord]:
    identity_customers = RAW_CUSTOMERS_IDENTITY if bridged else None
    identity_orders = RAW_ORDERS_IDENTITY if bridged else None
    resource = "seed" if bridged else None
    return {
        "raw_customers": _fact_record(
            _expectation_fact(
                "raw_customers", identity_customers, resource, _RAW_CUSTOMERS_HEALTHY
            ),
            EvidenceType.RELATION_SCHEMA_EXPECTATION,
            "raw_customers",
        ),
        "raw_orders": _fact_record(
            _expectation_fact(
                "raw_orders", identity_orders, resource, _RAW_ORDERS_HEALTHY
            ),
            EvidenceType.RELATION_SCHEMA_EXPECTATION,
            "raw_orders",
        ),
    }


def _definition_records(*, customers_complete: bool = True) -> dict[str, EvidenceRecord]:
    return {
        FAILURE_NODE: _fact_record(
            _definition_fact(
                FAILURE_NODE,
                identity="data_incident_gym.analytics.customers",
                sql=REAL_CUSTOMERS,
                complete=customers_complete,
            ),
            EvidenceType.DBT_NODE_DEFINITION,
            FAILURE_NODE,
        ),
        "model.jaffle_shop.stg_customers": _fact_record(
            _definition_fact(
                "model.jaffle_shop.stg_customers",
                identity=STG_CUSTOMERS_IDENTITY,
                sql=REAL_STG_CUSTOMERS,
            ),
            EvidenceType.DBT_NODE_DEFINITION,
            "model.jaffle_shop.stg_customers",
        ),
        "model.jaffle_shop.stg_orders": _fact_record(
            _definition_fact(
                "model.jaffle_shop.stg_orders", identity=STG_ORDERS_IDENTITY, sql=REAL_STG_ORDERS
            ),
            EvidenceType.DBT_NODE_DEFINITION,
            "model.jaffle_shop.stg_orders",
        ),
    }


def _runner(tmp_path: Path, tools: _Tools, context: ObservableRunContext) -> ReferenceAnalystRunner:
    return ReferenceAnalystRunner(
        run_id=RUN_ID,
        settings=SimpleNamespace(),
        project_root=tmp_path,
        tools=tools,
        context=context,
    )


def _gaps(diagnosis) -> set[tuple[str, str, str]]:
    return {
        (item.evidence_kind, item.subject, item.reason_code)
        for item in diagnosis.unresolved_evidence
    }


# -- A variants: confirmation through the reader -------------------------------


@pytest.mark.asyncio
async def test_pair1_a_confirms_the_left_origin_deviation(tmp_path: Path) -> None:
    """T1′: the deviation sits on ``raw_customers.id`` — the join's left origin."""

    tools = _Tools(
        schemas=_pair_records(customer_id_type="text", order_user_id_type="integer"),
        expectations=_expectation_records(bridged=True),
        definitions=_definition_records(),
        expectation_allowed=("raw_customers", "raw_orders"),
        definition_allowed=(
            FAILURE_NODE,
            "model.jaffle_shop.stg_customers",
            "model.jaffle_shop.stg_orders",
        ),
    )
    runner = _runner(
        tmp_path,
        tools,
        _v2_context(
            tmp_path,
            expectation=("raw_customers", "raw_orders"),
            definition=(
                FAILURE_NODE,
                "model.jaffle_shop.stg_customers",
                "model.jaffle_shop.stg_orders",
            ),
        ),
    )

    result = await runner.diagnose()

    diagnosis = result.diagnosis
    assert diagnosis.status is DiagnosisStatus.CONFIRMED
    assert diagnosis.root_cause_code == "SOURCE_SCHEMA_COLUMN_TYPE_CHANGED"
    assert set(diagnosis.affected_assets) == {FAILURE_NODE}
    assert diagnosis.unresolved_evidence == ()
    collected = {record.evidence_type.value for record in result.evidence_records}
    assert {"RELATION_SCHEMA_EXPECTATION", "DBT_NODE_DEFINITION"} <= collected
    # §4.1's checklist: run results, node error, upstream lineage, two schemas,
    # one batch call per fact family, downstream lineage for the assets.
    assert result.metrics.tool_call_attempts == 8


@pytest.mark.asyncio
async def test_pair2_a_confirms_the_right_origin_deviation(tmp_path: Path) -> None:
    """T2′ mirror: the deviation sits on ``raw_orders.user_id`` — the right
    origin. A rule that only inspects the first-resolved side fails here."""

    tools = _Tools(
        schemas=_pair_records(customer_id_type="integer", order_user_id_type="text"),
        expectations=_expectation_records(bridged=True),
        definitions=_definition_records(),
        expectation_allowed=("raw_customers", "raw_orders"),
        definition_allowed=(
            FAILURE_NODE,
            "model.jaffle_shop.stg_customers",
            "model.jaffle_shop.stg_orders",
        ),
    )
    runner = _runner(
        tmp_path,
        tools,
        _v2_context(
            tmp_path,
            expectation=("raw_customers", "raw_orders"),
            definition=(
                FAILURE_NODE,
                "model.jaffle_shop.stg_customers",
                "model.jaffle_shop.stg_orders",
            ),
        ),
    )

    result = await runner.diagnose()

    assert result.diagnosis.status is DiagnosisStatus.CONFIRMED
    assert result.diagnosis.root_cause_code == "SOURCE_SCHEMA_COLUMN_TYPE_CHANGED"
    assert set(result.diagnosis.affected_assets) == {FAILURE_NODE}


# -- B variant: receipt-backed abstention --------------------------------------


@pytest.mark.asyncio
async def test_pair_b_abstains_with_exactly_the_two_contract_gaps(tmp_path: Path) -> None:
    """Empty v2 whitelists: both batch calls are refused atomically, the two
    representative gaps match the contract exactly, and each is witnessed by
    the per-target refusal detail in the archived trace."""

    tools = _Tools(
        schemas=_pair_records(customer_id_type="text", order_user_id_type="integer"),
        expectations=_expectation_records(bridged=True),
        definitions=_definition_records(),
        expectation_allowed=(),
        definition_allowed=(),
    )
    runner = _runner(
        tmp_path,
        tools,
        _v2_context(tmp_path, expectation=(), definition=()),
    )

    result = await runner.diagnose()

    diagnosis = result.diagnosis
    assert diagnosis.status is DiagnosisStatus.INSUFFICIENT_EVIDENCE
    assert _gaps(diagnosis) == B_GAPS
    assert isinstance(diagnosis, DiagnosisV2)
    trace_events = [
        event for event in result.trace if event.event_type == "TOOL_CALL"
    ]
    assert refusal_witnessed(
        trace_events,
        tool_name="get_relation_schema_expectation",
        target="raw_customers",
        code="RELATION_NOT_ALLOWED",
    )
    assert refusal_witnessed(
        trace_events,
        tool_name="get_dbt_node_definition",
        target=FAILURE_NODE,
        code="NODE_NOT_ALLOWED",
    )
    # The observation schema stays readable in B and is collected and cited.
    schema_subjects = {
        record.subject
        for record in result.evidence_records
        if record.evidence_type is EvidenceType.RELATION_SCHEMA
    }
    assert schema_subjects == {"raw_customers", "raw_orders"}
    assert result.metrics.tool_call_attempts <= FIXED_RULE_TOOL_LIMIT


# -- the abstention path is exhaustive -----------------------------------------


@pytest.mark.asyncio
async def test_a_missing_bridge_abstains_instead_of_guessing(tmp_path: Path) -> None:
    """E1 facts written before the identity bridge carry no identity: no
    relation may be declared a terminal, the reader refuses through them
    (UNKNOWN), and the branch must not confirm."""

    tools = _Tools(
        schemas=_pair_records(customer_id_type="text", order_user_id_type="integer"),
        expectations=_expectation_records(bridged=False),
        definitions=_definition_records(),
        expectation_allowed=("raw_customers", "raw_orders"),
        definition_allowed=(
            FAILURE_NODE,
            "model.jaffle_shop.stg_customers",
            "model.jaffle_shop.stg_orders",
        ),
    )
    runner = _runner(
        tmp_path,
        tools,
        _v2_context(
            tmp_path,
            expectation=("raw_customers", "raw_orders"),
            definition=(
                FAILURE_NODE,
                "model.jaffle_shop.stg_customers",
                "model.jaffle_shop.stg_orders",
            ),
        ),
    )

    result = await runner.diagnose()

    assert result.diagnosis.status is DiagnosisStatus.INSUFFICIENT_EVIDENCE
    assert _gaps(result.diagnosis) == {
        ("TRANSFORMATION_DEFINITION", FAILURE_NODE, "NOT_OBSERVABLE"),
    }


@pytest.mark.asyncio
async def test_an_incomplete_definition_abstains(tmp_path: Path) -> None:
    """A truncated/redacted definition of the failed node must never feed the
    reader (§2.2): the branch abstains with the definition gap."""

    tools = _Tools(
        schemas=_pair_records(customer_id_type="text", order_user_id_type="integer"),
        expectations=_expectation_records(bridged=True),
        definitions=_definition_records(customers_complete=False),
        expectation_allowed=("raw_customers", "raw_orders"),
        definition_allowed=(
            FAILURE_NODE,
            "model.jaffle_shop.stg_customers",
            "model.jaffle_shop.stg_orders",
        ),
    )
    runner = _runner(
        tmp_path,
        tools,
        _v2_context(
            tmp_path,
            expectation=("raw_customers", "raw_orders"),
            definition=(
                FAILURE_NODE,
                "model.jaffle_shop.stg_customers",
                "model.jaffle_shop.stg_orders",
            ),
        ),
    )

    result = await runner.diagnose()

    assert result.diagnosis.status is DiagnosisStatus.INSUFFICIENT_EVIDENCE
    assert _gaps(result.diagnosis) == {
        ("DBT_NODE_DEFINITION", FAILURE_NODE, "NOT_OBSERVABLE"),
    }


@pytest.mark.asyncio
async def test_a_non_unique_deviation_abstains(tmp_path: Path) -> None:
    """§4.2 ③: two deviating columns inside the involved relations cannot
    decide which one the failing expression blames."""

    schemas = _pair_records(customer_id_type="text", order_user_id_type="text")
    tools = _Tools(
        schemas=schemas,
        expectations=_expectation_records(bridged=True),
        definitions=_definition_records(),
        expectation_allowed=("raw_customers", "raw_orders"),
        definition_allowed=(
            FAILURE_NODE,
            "model.jaffle_shop.stg_customers",
            "model.jaffle_shop.stg_orders",
        ),
    )
    runner = _runner(
        tmp_path,
        tools,
        _v2_context(
            tmp_path,
            expectation=("raw_customers", "raw_orders"),
            definition=(
                FAILURE_NODE,
                "model.jaffle_shop.stg_customers",
                "model.jaffle_shop.stg_orders",
            ),
        ),
    )

    result = await runner.diagnose()

    assert result.diagnosis.status is DiagnosisStatus.INSUFFICIENT_EVIDENCE
    assert _gaps(result.diagnosis) == {
        ("TRANSFORMATION_DEFINITION", FAILURE_NODE, "NOT_OBSERVABLE"),
    }


@pytest.mark.asyncio
async def test_a_deviation_outside_the_failing_expression_abstains(tmp_path: Path) -> None:
    """§4.2 ②/④: the only deviating column is ``first_name`` — a column the
    failing join condition never reads. The deviation cannot be attributed."""

    schemas = _pair_records(customer_id_type="integer", order_user_id_type="integer")
    schemas["raw_customers"] = _schema_record(
        "raw_customers",
        (("id", "integer"), ("first_name", "integer"), ("last_name", "text")),
    )
    tools = _Tools(
        schemas=schemas,
        expectations=_expectation_records(bridged=True),
        definitions=_definition_records(),
        expectation_allowed=("raw_customers", "raw_orders"),
        definition_allowed=(
            FAILURE_NODE,
            "model.jaffle_shop.stg_customers",
            "model.jaffle_shop.stg_orders",
        ),
    )
    runner = _runner(
        tmp_path,
        tools,
        _v2_context(
            tmp_path,
            expectation=("raw_customers", "raw_orders"),
            definition=(
                FAILURE_NODE,
                "model.jaffle_shop.stg_customers",
                "model.jaffle_shop.stg_orders",
            ),
        ),
    )

    result = await runner.diagnose()

    assert result.diagnosis.status is DiagnosisStatus.INSUFFICIENT_EVIDENCE
    assert _gaps(result.diagnosis) == {
        ("TRANSFORMATION_DEFINITION", FAILURE_NODE, "NOT_OBSERVABLE"),
    }


@pytest.mark.asyncio
async def test_a_non_type_error_never_reaches_the_expectation_tools(tmp_path: Path) -> None:
    """The §4.2 confirmation path covers the type-error family only; a missing
    column abstains immediately and never asks the expectation tools."""

    tools = _Tools(
        schemas=_pair_records(customer_id_type="text", order_user_id_type="integer"),
        expectations=_expectation_records(bridged=True),
        definitions=_definition_records(),
        expectation_allowed=("raw_customers", "raw_orders"),
        definition_allowed=(
            FAILURE_NODE,
            "model.jaffle_shop.stg_customers",
            "model.jaffle_shop.stg_orders",
        ),
    )
    def _missing_column_error(run_id: str, node_id: str) -> tuple[EvidenceRecord, ...]:
        return (
            _fact_record(
                DbtNodeErrorFact(
                    kind="DBT_NODE_ERROR",
                    run_id=RUN_ID,
                    node_id=node_id,
                    resource_type="model",
                    status="error",
                    message=(
                        "Database Error in model customers (models/customers.sql)\n"
                        'column "amount" does not exist\n'
                        "LINE 20:         amount / 100 as amount\n"
                    ),
                ),
                EvidenceType.DBT_NODE_ERROR,
                node_id,
            ),
        )

    tools.get_dbt_node_error = _missing_column_error  # type: ignore[method-assign]
    runner = _runner(
        tmp_path,
        tools,
        _v2_context(
            tmp_path,
            expectation=("raw_customers", "raw_orders"),
            definition=(
                FAILURE_NODE,
                "model.jaffle_shop.stg_customers",
                "model.jaffle_shop.stg_orders",
            ),
        ),
    )

    result = await runner.diagnose()

    assert result.diagnosis.status is DiagnosisStatus.INSUFFICIENT_EVIDENCE
    assert _gaps(result.diagnosis) == {
        ("TRANSFORMATION_DEFINITION", FAILURE_NODE, "NOT_OBSERVABLE"),
    }
    assert not any(
        tool == "get_relation_schema_expectation" for tool, _ in tools.calls
    )


# -- the session seam for v2 submissions ---------------------------------------


def test_v2_gap_submission_passes_the_session_and_stays_v2() -> None:
    """The B diagnosis's v2 gaps survive the protocol: FinalSubmissionV2 builds
    a DiagnosisV2, and a batch refusal routed through the facade keeps the
    per-target detail."""

    class _Backend:
        def get_relation_schema_expectation(
            self, relation_names: str
        ) -> tuple[EvidenceRecord, ...]:
            raise BatchTargetsRefusedError(
                "not granted",
                target_refusals=(("raw_customers", "RELATION_NOT_ALLOWED"),),
            )

    context = _v2_context(Path("."), expectation=(), definition=())
    session = StrategySession(
        run_id=RUN_ID,
        tools=_Backend(),
        context=context,
        declaration=builtin_declaration(
            model_provider="none", model_name="reference-analyst", deterministic=True
        ),
        allowlist=tool_allowlist_for_context(context),
    )
    assert session.tools_facade().__class__ is ProtocolTools
    assert tool_allowlist_for_context(context) >= EVIDENCE_V2_TOOL_ALLOWLIST

    with pytest.raises(BatchTargetsRefusedError):
        session.tools_facade().get_relation_schema_expectation("raw_customers")

    receipt = session.submit(
        FinalSubmissionV2(
            status=DiagnosisStatus.INSUFFICIENT_EVIDENCE,
            summary="The decisive expectation and definition facts are not granted.",
            unresolved_evidence=(
                {
                    "evidence_kind": "RELATION_SCHEMA_EXPECTATION",
                    "subject": "raw_customers",
                    "reason_code": "RELATION_NOT_ALLOWED",
                },
            ),
            confidence=0.0,
        )
    )

    assert receipt.accepted
    assert isinstance(receipt.diagnosis, DiagnosisV2)
    assert _gaps(receipt.diagnosis) == {
        ("RELATION_SCHEMA_EXPECTATION", "raw_customers", "RELATION_NOT_ALLOWED"),
    }
