from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from data_incident_gym.diagnosis import DiagnosisStatus
from data_incident_gym.evidence import (
    DbtLineageFact,
    DbtLineageNode,
    DbtRunResultsFact,
    EvidenceRecord,
    EvidenceSource,
    EvidenceType,
    RelationDataProfileFact,
    RelationSchemaColumn,
    RelationSchemaFact,
)
from data_incident_gym.fixed_rule import FIXED_RULE_TOOL_LIMIT, FixedRuleRunner
from data_incident_gym.profiles import (
    DuplicateProfileFact,
    GroupProfileFact,
    RelationProfileSnapshot,
)
from data_incident_gym.run_context import IncidentBrief, ObservableRunContext, ObservedSignal

RUN_ID = "a" * 32
OBSERVED_AT = datetime(2026, 9, 1, tzinfo=UTC)


def _record(content, evidence_type: EvidenceType, subject: str) -> EvidenceRecord:
    return EvidenceRecord.create(
        run_id=RUN_ID,
        evidence_type=evidence_type,
        source=(
            EvidenceSource.DBT_RUN_RESULTS
            if evidence_type is EvidenceType.DBT_RUN_RESULTS
            else EvidenceSource.DBT_MANIFEST
            if evidence_type is EvidenceType.DBT_LINEAGE
            else EvidenceSource.POSTGRES_CATALOG
            if evidence_type is EvidenceType.RELATION_SCHEMA
            else EvidenceSource.POSTGRES_PROFILE_SNAPSHOT
        ),
        subject=subject,
        observed_at=OBSERVED_AT,
        content=content,
    )


def _context(tmp_path: Path, *, profile_relations: tuple[str, ...]) -> ObservableRunContext:
    return ObservableRunContext(
        run_id=RUN_ID,
        artifact_dir=tmp_path,
        runtime={
            "observable_relations": {
                "schema": ["raw_payments"],
                "profile": list(profile_relations),
                "history": [],
            }
        },
        incident_brief=IncidentBrief(
            schema_version="incident_brief.v1",
            signal_code="PAYMENT_DUPLICATE_ALERT",
            summary="A payment retry alert was raised.",
            subjects=("seed.jaffle_shop.raw_payments", "raw_payments"),
            logical_observed_at=OBSERVED_AT,
            observations=(
                ObservedSignal(
                    kind="CHANNEL_PAYMENT_RETRY_ALERT", subject="raw_payments", value="coupon"
                ),
            ),
        ),
    )


def _records(
    *,
    dbt_exit_code: int = 0,
    failed_nodes: tuple[str, ...] = (),
    skipped_nodes: tuple[str, ...] = (),
) -> dict[str, tuple[EvidenceRecord, ...]]:
    run = _record(
        DbtRunResultsFact(
            kind="DBT_RUN_RESULTS",
            run_id=RUN_ID,
            run_status="SUCCEEDED",
            dbt_exit_code=dbt_exit_code,
            failed_nodes=failed_nodes,
            skipped_nodes=skipped_nodes,
        ),
        EvidenceType.DBT_RUN_RESULTS,
        RUN_ID,
    )
    lineage = _record(
        DbtLineageFact(
            kind="DBT_LINEAGE",
            run_id=RUN_ID,
            node_id="seed.jaffle_shop.raw_payments",
            direction="downstream",
            related_nodes=(
                DbtLineageNode(
                    node_id="model.jaffle_shop.stg_payments",
                    resource_type="model",
                    name="stg_payments",
                    distance=1,
                ),
            ),
        ),
        EvidenceType.DBT_LINEAGE,
        "seed.jaffle_shop.raw_payments",
    )
    schema = _record(
        RelationSchemaFact(
            kind="RELATION_SCHEMA",
            run_id=RUN_ID,
            schema_name="analytics",
            relation_name="raw_payments",
            columns=(
                RelationSchemaColumn(
                    name="id",
                    data_type="integer",
                    nullable=False,
                    ordinal_position=1,
                ),
            ),
        ),
        EvidenceType.RELATION_SCHEMA,
        "analytics.raw_payments",
    )
    profile = _record(
        RelationDataProfileFact(
            kind="RELATION_DATA_PROFILE",
            run_id=RUN_ID,
            relation_name="raw_payments",
            profile_spec_version="profile_spec.v1",
            profile_spec_sha256="b" * 64,
            snapshot=RelationProfileSnapshot(
                relation_name="raw_payments",
                row_count=116,
                columns=(),
                business_key_duplicates=(DuplicateProfileFact(name="id", duplicate_count=0),),
                business_fingerprint_duplicates=(
                    DuplicateProfileFact(name="order_payment_amount", duplicate_count=3),
                ),
                groups=(
                    GroupProfileFact(
                        name="payment_method",
                        columns=("payment_method",),
                        values=(("coupon",),),
                        counts=(16,),
                    ),
                ),
            ),
        ),
        EvidenceType.RELATION_DATA_PROFILE,
        "raw_payments",
    )
    return {
        "get_dbt_run_results": (run,),
        "get_dbt_lineage": (lineage,),
        "get_relation_schema": (schema,),
        "get_relation_data_profile": (profile,),
    }


class _Tools:
    def __init__(
        self, records: dict[str, tuple[EvidenceRecord, ...]], *, block_profile: bool = False
    ):
        self.records = records
        self.block_profile = block_profile
        self.calls: list[str] = []

    def _get(self, name: str) -> tuple[EvidenceRecord, ...]:
        self.calls.append(name)
        if name == "get_relation_data_profile" and self.block_profile:
            from data_incident_gym.evidence import RelationNotAllowedError

            raise RelationNotAllowedError("blocked")
        return self.records.get(name, ())

    def get_dbt_run_results(self, _run_id: str):
        return self._get("get_dbt_run_results")

    def get_dbt_lineage(self, _node_id: str, _direction: str):
        return self._get("get_dbt_lineage")

    def get_relation_schema(self, _relation_name: str):
        return self._get("get_relation_schema")

    def get_relation_data_profile(self, _relation_name: str):
        return self._get("get_relation_data_profile")

    def get_relation_history(self, _relation_name: str):
        return self._get("get_relation_history")


@pytest.mark.asyncio
async def test_fixed_rule_confirms_public_semantic_duplicate_without_model_call(
    tmp_path: Path,
) -> None:
    tools = _Tools(_records())
    runner = FixedRuleRunner(
        run_id=RUN_ID,
        settings=SimpleNamespace(),
        project_root=tmp_path,
        tools=tools,
        context=_context(tmp_path, profile_relations=("raw_payments",)),
    )

    result = await runner.diagnose()

    assert result.diagnosis.status is DiagnosisStatus.CONFIRMED
    assert result.diagnosis.root_cause_code == "SOURCE_SEMANTIC_PAYMENT_DUPLICATE"
    assert result.metrics.model_requests == 0
    assert result.metrics.input_tokens == result.metrics.output_tokens == 0
    assert len(tools.calls) <= FIXED_RULE_TOOL_LIMIT
    assert result.trace[-1].event_type == "DIAGNOSIS_TERMINAL"


@pytest.mark.asyncio
async def test_fixed_rule_refuses_blocked_decisive_profile(tmp_path: Path) -> None:
    tools = _Tools(_records(), block_profile=True)
    runner = FixedRuleRunner(
        run_id=RUN_ID,
        settings=SimpleNamespace(),
        project_root=tmp_path,
        tools=tools,
        context=_context(tmp_path, profile_relations=()),
    )

    result = await runner.diagnose()

    assert result.diagnosis.status is DiagnosisStatus.INSUFFICIENT_EVIDENCE
    assert result.diagnosis.root_cause_code is None
    assert result.diagnosis.unresolved_evidence[0].evidence_kind == "RELATION_DATA_PROFILE"
    assert result.metrics.model_requests == 0


@pytest.mark.asyncio
async def test_fixed_rule_refuses_nonzero_success_exit_code(tmp_path: Path) -> None:
    runner = FixedRuleRunner(
        run_id=RUN_ID,
        settings=SimpleNamespace(),
        project_root=tmp_path,
        tools=_Tools(_records(dbt_exit_code=1)),
        context=_context(tmp_path, profile_relations=("raw_payments",)),
    )

    result = await runner.diagnose()

    assert result.diagnosis.status is DiagnosisStatus.INSUFFICIENT_EVIDENCE
    assert result.diagnosis.root_cause_code is None


@pytest.mark.asyncio
async def test_fixed_rule_refuses_success_with_failed_or_skipped_nodes(tmp_path: Path) -> None:
    runner = FixedRuleRunner(
        run_id=RUN_ID,
        settings=SimpleNamespace(),
        project_root=tmp_path,
        tools=_Tools(
            _records(
                failed_nodes=("model.jaffle_shop.orders",),
                skipped_nodes=("model.jaffle_shop.customers",),
            )
        ),
        context=_context(tmp_path, profile_relations=("raw_payments",)),
    )

    result = await runner.diagnose()

    assert result.diagnosis.status is DiagnosisStatus.INSUFFICIENT_EVIDENCE
    assert result.diagnosis.root_cause_code is None


@pytest.mark.asyncio
async def test_fixed_rule_does_not_hide_internal_errors(tmp_path: Path, monkeypatch) -> None:
    runner = FixedRuleRunner(
        run_id=RUN_ID,
        settings=SimpleNamespace(),
        project_root=tmp_path,
        tools=_Tools(_records()),
        context=_context(tmp_path, profile_relations=("raw_payments",)),
    )

    def raise_internal_error():
        raise RuntimeError("internal fixed-rule error")

    monkeypatch.setattr(runner, "_run_results", raise_internal_error)

    with pytest.raises(RuntimeError, match="internal fixed-rule error"):
        await runner.diagnose()


def test_fixed_rule_silent_drop_does_not_require_schema(tmp_path: Path, monkeypatch) -> None:
    records = _records()
    runner = FixedRuleRunner(
        run_id=RUN_ID,
        settings=SimpleNamespace(),
        project_root=tmp_path,
        tools=_Tools(records),
        context=_context(tmp_path, profile_relations=("raw_payments",)),
    )
    run = records["get_dbt_run_results"][0].content
    lineage = records["get_dbt_lineage"][0].content
    profile = records["get_relation_data_profile"][0].content
    runner._records = [record for values in records.values() for record in values]

    monkeypatch.setattr(runner, "_payment_relation", lambda: "raw_payments")
    monkeypatch.setattr(runner, "_order_relation", lambda: "raw_orders")
    monkeypatch.setattr(runner, "_payment_lineage", lambda: lineage)
    monkeypatch.setattr(runner, "_profile", lambda _relation: profile)
    monkeypatch.setattr(runner, "_history", lambda _relation: object())
    monkeypatch.setattr(runner, "_silent_supported", lambda *_args: True)

    def schema_must_not_be_requested(_relation: str):
        raise AssertionError("silent-drop fixed rule must not require schema")

    monkeypatch.setattr(runner, "_schema_for_payment", schema_must_not_be_requested)

    diagnosis = runner._diagnose_silent(run)

    assert diagnosis.status is DiagnosisStatus.CONFIRMED
    assert diagnosis.root_cause_code == "SOURCE_PAYMENT_INGESTION_LOSS"
