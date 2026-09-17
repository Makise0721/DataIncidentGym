"""External strategy client for the unified strategy access protocol (T09).

This script is the reference third-party client: it declares what it is, reads
the public task context, makes read-only tool calls through the harness and
submits one final answer per run. It completes the three terminal paths the
protocol must support — CONFIRMED, INSUFFICIENT_EVIDENCE (a qualified abstention
backed by a real refusal receipt) and NO_INCIDENT — against deterministic
synthetic backends, so the whole flow runs without a database or a model:

    uv run python examples/external_strategy_client.py

The harness owns the run id, the allowlist, the budget and the evidence
registration; this client can only cite what its tool calls actually returned.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from data_incident_gym.diagnosis import (
    AffectedAssetClaim,
    HealthStateClaim,
    RootCauseClaim,
    UnresolvedEvidence,
)
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
)
from data_incident_gym.run_context import IncidentBrief, ObservableRunContext
from data_incident_gym.strategy_adapter import (
    FinalSubmission,
    StrategyDeclaration,
    StrategySession,
    ToolRequest,
)

RUN_ID = "e" * 32
TEST_NODE = "test.jaffle_shop.not_null_orders_customer_id"
ORDERS_MODEL = "model.jaffle_shop.orders"
OBSERVED_AT = datetime(2026, 9, 16, tzinfo=UTC)


def declaration() -> StrategyDeclaration:
    return StrategyDeclaration(
        framework="data-incident-gym-example",
        framework_version="1.0",
        model_provider="none",
        model_name="deterministic-script",
        deterministic=True,
        visible_context=("incident_brief", "relation_whitelist"),
    )


def _record(evidence_type: EvidenceType, source: EvidenceSource, subject: str, content: Any):
    return EvidenceRecord.create(
        run_id=RUN_ID,
        evidence_type=evidence_type,
        source=source,
        subject=subject,
        observed_at=OBSERVED_AT,
        content=content,
    )


class PathBackend:
    """Deterministic evidence for one terminal path."""

    def __init__(self, *, fail: bool, refuse_profile_for: str | None = None) -> None:
        self.fail = fail
        self.refuse_profile_for = refuse_profile_for
        self.run = _record(
            EvidenceType.DBT_RUN_RESULTS,
            EvidenceSource.DBT_RUN_RESULTS,
            RUN_ID,
            DbtRunResultsFact(
                kind="DBT_RUN_RESULTS",
                run_id=RUN_ID,
                run_status="FAILED" if fail else "SUCCEEDED",
                dbt_exit_code=1 if fail else 0,
                failed_nodes=(TEST_NODE,) if fail else (),
                skipped_nodes=(),
            ),
        )
        self.node_error = _record(
            EvidenceType.DBT_NODE_ERROR,
            EvidenceSource.DBT_RUN_RESULTS,
            TEST_NODE,
            DbtNodeErrorFact(
                kind="DBT_NODE_ERROR",
                run_id=RUN_ID,
                node_id=TEST_NODE,
                resource_type="test",
                status="fail",
                message="null value in column user_id",
            ),
        )
        self.lineage = _record(
            EvidenceType.DBT_LINEAGE,
            EvidenceSource.DBT_MANIFEST,
            TEST_NODE,
            DbtLineageFact(
                kind="DBT_LINEAGE",
                run_id=RUN_ID,
                node_id=TEST_NODE,
                direction="downstream",
                related_nodes=(
                    DbtLineageNode(
                        node_id=ORDERS_MODEL,
                        resource_type="model",
                        name="orders",
                        distance=1,
                    ),
                ),
            ),
        )
        self.profile = _record(
            EvidenceType.RELATION_DATA_PROFILE,
            EvidenceSource.POSTGRES_PROFILE_SNAPSHOT,
            "raw_orders",
            _profile_fact(),
        )
        self.history = _record(
            EvidenceType.RELATION_HISTORY,
            EvidenceSource.POSTGRES_PROFILE_SNAPSHOT,
            "raw_orders",
            _history_fact(),
        )

    def _one(self, record: EvidenceRecord) -> tuple[EvidenceRecord, ...]:
        return (record,)

    def get_dbt_run_results(self, run_id: str) -> tuple[EvidenceRecord, ...]:
        return self._one(self.run)

    def get_dbt_node_error(self, run_id: str, node_id: str) -> tuple[EvidenceRecord, ...]:
        return self._one(self.node_error)

    def get_dbt_lineage(self, node_id: str, direction: str) -> tuple[EvidenceRecord, ...]:
        return self._one(self.lineage)

    def get_relation_schema(self, relation_name: str) -> tuple[EvidenceRecord, ...]:
        return ()

    def get_relation_data_profile(self, relation_name: str) -> tuple[EvidenceRecord, ...]:
        if self.refuse_profile_for is not None and relation_name == self.refuse_profile_for:
            raise RelationNotAllowedError(relation_name)
        return self._one(self.profile)

    def get_relation_history(self, relation_name: str) -> tuple[EvidenceRecord, ...]:
        return self._one(self.history)


def _profile_fact() -> Any:
    from data_incident_gym.evidence import RelationDataProfileFact
    from data_incident_gym.profiles import ColumnProfileFact, RelationProfileSnapshot

    return RelationDataProfileFact(
        kind="RELATION_DATA_PROFILE",
        run_id=RUN_ID,
        relation_name="raw_orders",
        profile_spec_version="profile_spec.v1",
        profile_spec_sha256="b" * 64,
        snapshot=RelationProfileSnapshot(
            relation_name="raw_orders",
            row_count=100,
            columns=(
                ColumnProfileFact(column_name="user_id", null_count=1, distinct_count=99),
            ),
        ),
    )


def _history_fact() -> Any:
    from data_incident_gym.evidence import RelationHistoryFact
    from data_incident_gym.profiles import HistoryPoint, HistorySeries, RelationHistorySnapshot

    return RelationHistoryFact(
        kind="RELATION_HISTORY",
        run_id=RUN_ID,
        relation_name="raw_orders",
        profile_spec_version="profile_spec.v1",
        profile_spec_sha256="b" * 64,
        snapshot=RelationHistorySnapshot(
            relation_name="raw_orders",
            histories=(
                HistorySeries(
                    name="order_count_by_day",
                    metric="count",
                    points=tuple(
                        HistoryPoint(bucket=day, periodic_key="1", value=1)
                        for day in ("2018-03-26", "2018-04-02")
                    ),
                    watermark_column="order_date",
                    watermark_value="2018-04-02",
                ),
            ),
        ),
    )


def open_session(backend: PathBackend) -> StrategySession:
    return StrategySession(
        run_id=RUN_ID,
        tools=backend,
        context=ObservableRunContext(
            run_id=RUN_ID,
            artifact_dir=Path("."),
            runtime={"observable_relations": {"profile": ["raw_orders"]}},
            incident_brief=IncidentBrief(
                schema_version="incident_brief.v1",
                signal_code="DBT_TEST_FAILED",
                summary="A dbt test failed.",
                subjects=(TEST_NODE,),
                logical_observed_at=OBSERVED_AT,
                observations=(),
            ),
        ),
        declaration=declaration(),
    )


def _call(session: StrategySession, name: str, **arguments: str):
    return session.call_tool(ToolRequest(request_id=f"{name}:{arguments}", tool_name=name,
                                          arguments=arguments))


def _fact(receipt, fact_type):
    for record in receipt.evidence:
        if isinstance(record.content, fact_type):
            return record.content
    raise AssertionError(f"decisive fact {fact_type.__name__} missing from the receipt")


def diagnose(session: StrategySession) -> dict[str, Any]:
    """Read the public facts and conclude from them — nothing is preset.

    The decision rule is deliberately small: a failed run plus a null column
    confirms the required-field root cause with the lineage-named model; a
    refused decisive profile abstains on the real refusal receipt; a succeeded
    run with in-range history concludes healthy. Change any decisive fact and
    the conclusion changes with it.
    """

    run_receipt = _call(session, "get_dbt_run_results", run_id=RUN_ID)
    run = _fact(run_receipt, DbtRunResultsFact)

    if run.run_status == "SUCCEEDED":
        profile_receipt = _call(session, "get_relation_data_profile", relation_name="raw_orders")
        history_receipt = _call(session, "get_relation_history", relation_name="raw_orders")
        history = _fact(history_receipt, RelationHistoryFact)
        series = history.snapshot.histories[0]
        current = series.points[-1]
        cited = (*run_receipt.evidence_ids, *profile_receipt.evidence_ids,
                 *history_receipt.evidence_ids)
        receipt = session.submit(
            FinalSubmission(
                status="NO_INCIDENT",
                summary="The run succeeded and the current count sits in the observed window.",
                evidence_ids=cited,
                claims=(
                    HealthStateClaim(
                        kind="HEALTH_STATE",
                        relation_name="raw_orders",
                        history_name=series.name,
                        bucket=current.bucket,
                        current_value=current.value,
                        evidence_ids=cited,
                    ),
                ),
                confidence=0.8,
            )
        )
        return {"accepted": receipt.accepted,
                "status": receipt.diagnosis.status.value if receipt.diagnosis
                else receipt.error.code}

    node_receipt = _call(session, "get_dbt_node_error", run_id=RUN_ID, node_id=TEST_NODE)
    lineage_receipt = _call(
        session, "get_dbt_lineage", node_id=TEST_NODE, direction="downstream"
    )
    profile_receipt = _call(session, "get_relation_data_profile", relation_name="raw_orders")

    if not profile_receipt.accepted:
        assert profile_receipt.error is not None
        receipt = session.submit(
            FinalSubmission(
                status="INSUFFICIENT_EVIDENCE",
                summary="The decisive profile is not observable; the refusal receipt is the gap.",
                unresolved_evidence=(
                    UnresolvedEvidence(
                        evidence_kind="RELATION_DATA_PROFILE",
                        subject="raw_orders",
                        reason_code=profile_receipt.error.code,
                    ),
                ),
                confidence=0.3,
            )
        )
        return {"accepted": receipt.accepted,
                "status": receipt.diagnosis.status.value if receipt.diagnosis
                else receipt.error.code,
                "refusal_code": profile_receipt.error.code}

    node = _fact(node_receipt, DbtNodeErrorFact)
    lineage = _fact(lineage_receipt, DbtLineageFact)
    profile = _fact(profile_receipt, RelationDataProfileFact)
    null_column = next(
        (column for column in profile.snapshot.columns if column.null_count >= 1), None
    )
    downstream_model = next(
        (node for node in lineage.related_nodes if node.resource_type == "model"), None
    )
    if null_column is None or downstream_model is None:
        session.cancel("STRATEGY_CANCELLED")
        return {"accepted": False, "status": "STRATEGY_CANCELLED"}

    cited = (*node_receipt.evidence_ids, *profile_receipt.evidence_ids,
             *lineage_receipt.evidence_ids)
    receipt = session.submit(
        FinalSubmission(
            status="CONFIRMED",
            summary=f"Node error on {node.node_id} plus the null column "
            f"{null_column.column_name} prove a required field is null; "
            f"{downstream_model.node_id} is downstream of it.",
            root_cause_code="SOURCE_REQUIRED_FIELD_NULL",
            affected_assets=(downstream_model.node_id,),
            evidence_ids=cited,
            claims=(
                RootCauseClaim(
                    kind="ROOT_CAUSE",
                    root_cause_code="SOURCE_REQUIRED_FIELD_NULL",
                    evidence_ids=(*node_receipt.evidence_ids, *profile_receipt.evidence_ids),
                ),
                AffectedAssetClaim(
                    kind="AFFECTED_ASSET",
                    asset=downstream_model.node_id,
                    evidence_ids=lineage_receipt.evidence_ids,
                ),
            ),
            confidence=0.9,
        )
    )
    return {"accepted": receipt.accepted,
            "status": receipt.diagnosis.status.value if receipt.diagnosis
            else receipt.error.code}


def confirmed_path(session: StrategySession) -> dict[str, Any]:
    return diagnose(session)


def abstain_path(session: StrategySession) -> dict[str, Any]:
    return diagnose(session)


def health_path(session: StrategySession) -> dict[str, Any]:
    return diagnose(session)


def main() -> int:
    paths = (
        ("confirmed", confirmed_path, PathBackend(fail=True)),
        ("abstain", abstain_path, PathBackend(fail=True, refuse_profile_for="raw_orders")),
        ("health", health_path, PathBackend(fail=False)),
    )
    for name, function, backend in paths:
        session = open_session(backend)
        context = session.task_context()
        outcome = function(session)
        snapshot = session.snapshot()
        print(f"[{name}] protocol={context.protocol_version} tools={context.tool_allowlist[:2]}…")
        print(
            f"[{name}] outcome={outcome} attempts={snapshot['tool_call_attempts']} "
            f"registered={snapshot['registered_evidence']} usage={snapshot['self_reported_usage']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
