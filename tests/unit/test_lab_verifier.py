from __future__ import annotations

from pathlib import Path

import pytest

from data_incident_gym.baseline import ColumnSummary, RelationSummary
from data_incident_gym.config import Settings
from data_incident_gym.lab_verifier import (
    IncidentVerifier,
    LabVerificationError,
    ScenarioVerification,
    ScenarioVerificationStatus,
)
from data_incident_gym.profiles import (
    DuplicateProfileFact,
    GroupProfileFact,
    HistoryMetric,
    HistoryPoint,
    HistorySeries,
    ProfileSnapshot,
    RelationHistorySnapshot,
    RelationProfileSnapshot,
    RelationshipViolationFact,
)
from data_incident_gym.scenarios import (
    DeletePaymentRowsMutation,
    deleted_payment_rows,
    load_scenario_spec,
)

RUN_ID = "a" * 32


def _verification(case_id: str = "schema_type_change_payment_amount") -> ScenarioVerification:
    return ScenarioVerification(
        status=ScenarioVerificationStatus.EXPECTED_FAILURE,
        incident_case_id=case_id,
        run_id=RUN_ID,
        dbt_exit_code=1,
        failed_nodes=("model.jaffle_shop.stg_payments",),
        skipped_nodes=(),
        affected_assets=(
            "model.jaffle_shop.customers",
            "model.jaffle_shop.orders",
            "model.jaffle_shop.stg_payments",
        ),
        schema_fingerprint="a" * 64,
        profile_spec_sha256="b" * 64,
    )


def test_private_verification_round_trips_without_database_access(tmp_path: Path) -> None:
    path = tmp_path / ".dig" / "lab" / "private" / RUN_ID / "verification.json"
    path.parent.mkdir(parents=True)
    path.write_text(_verification().to_json(), encoding="utf-8")

    loaded = IncidentVerifier(tmp_path, settings=Settings(_env_file=None)).load_verification(RUN_ID)

    assert loaded == _verification()


def test_private_verification_rejects_duplicate_or_cross_run_payload(tmp_path: Path) -> None:
    path = tmp_path / ".dig" / "lab" / "private" / RUN_ID / "verification.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        '{"status":"EXPECTED_FAILURE","status":"EXPECTED_FAILURE",'
        f'"incident_case_id":"schema_type_change_payment_amount","run_id":"{RUN_ID}",'
        '"dbt_exit_code":1,"failed_nodes":[],"skipped_nodes":[],"affected_assets":[],'
        '"schema_fingerprint":"' + "a" * 64 + '","profile_spec_sha256":"' + "b" * 64 + '"}',
        encoding="utf-8",
    )

    with pytest.raises(LabVerificationError):
        IncidentVerifier(tmp_path, settings=Settings(_env_file=None)).load_verification(RUN_ID)


def test_scenario_verification_requires_sorted_unique_observations() -> None:
    with pytest.raises(ValueError, match="unique and sorted"):
        ScenarioVerification(
            status=ScenarioVerificationStatus.EXPECTED_FAILURE,
            incident_case_id="schema_type_change_payment_amount",
            run_id=RUN_ID,
            dbt_exit_code=1,
            failed_nodes=("z", "a"),
            skipped_nodes=(),
            affected_assets=(),
            schema_fingerprint="a" * 64,
            profile_spec_sha256="b" * 64,
        )


def test_run_results_treat_a_failed_test_as_a_failed_node(tmp_path: Path) -> None:
    test_id = "test.jaffle_shop.not_null_orders_customer_id.c5f02694af"
    path = tmp_path / "run_results.json"
    path.write_text(
        '{"results":[{"unique_id":"'
        + test_id
        + '","status":"fail"}]}',
        encoding="utf-8",
    )
    manifest = {"nodes": {test_id: {"resource_type": "test"}}}

    failed, skipped = IncidentVerifier._read_run_results(path, manifest)

    assert failed == (test_id,)
    assert skipped == ()


def test_failed_test_maps_only_to_its_distance_one_model() -> None:
    test_id = "test.jaffle_shop.not_null_orders_customer_id.c5f02694af"
    model_id = "model.jaffle_shop.orders"
    seed_id = "source.jaffle_shop.raw_orders"
    manifest = {
        "nodes": {
            test_id: {"resource_type": "test"},
            model_id: {"resource_type": "model"},
            seed_id: {"resource_type": "seed"},
        },
        "parent_map": {
            test_id: [model_id],
            model_id: [seed_id],
            seed_id: [],
        },
        "child_map": {
            test_id: [],
            model_id: [test_id],
            seed_id: [model_id],
        },
    }

    assert IncidentVerifier._affected_models(manifest, test_id) == {model_id}


def test_mutation_schema_projects_all_mutations_on_one_relation_before_comparing() -> None:
    scenario = load_scenario_spec("duplicate_payment_coupon_a")
    baseline = RelationSummary(
        "raw_payments",
        113,
        (
            ColumnSummary("id", "integer", True, 1),
            ColumnSummary("order_id", "integer", True, 2),
            ColumnSummary("payment_method", "text", True, 3),
            ColumnSummary("amount", "integer", True, 4),
        ),
    )
    actual = RelationSummary(
        "raw_payments",
        116,
        baseline.columns + (ColumnSummary("source_batch_note", "text", True, 5),),
    )

    IncidentVerifier._validate_mutation_schema(
        scenario,
        {"raw_payments": actual},
        {"raw_payments": baseline},
    )


def test_mutation_schema_projects_orphan_rows_before_comparing() -> None:
    scenario = load_scenario_spec("orphan_payment_coupon_a")
    baseline = RelationSummary(
        "raw_payments",
        113,
        (
            ColumnSummary("id", "integer", True, 1),
            ColumnSummary("order_id", "integer", True, 2),
            ColumnSummary("payment_method", "text", True, 3),
            ColumnSummary("amount", "integer", True, 4),
        ),
    )
    actual = RelationSummary(
        "raw_payments",
        116,
        baseline.columns + (ColumnSummary("source_batch_note", "text", True, 5),),
    )

    IncidentVerifier._validate_mutation_schema(
        scenario,
        {"raw_payments": actual},
        {"raw_payments": baseline},
    )


def test_mutation_schema_projects_deleted_rows_before_comparing() -> None:
    scenario = load_scenario_spec("silent_payment_drop_partition_a")
    baseline = RelationSummary(
        "raw_payments",
        113,
        (
            ColumnSummary("id", "integer", True, 1),
            ColumnSummary("order_id", "integer", True, 2),
            ColumnSummary("payment_method", "text", True, 3),
            ColumnSummary("amount", "integer", True, 4),
        ),
    )
    actual = RelationSummary(
        "raw_payments",
        111,
        baseline.columns + (ColumnSummary("source_batch_note", "text", True, 5),),
    )

    IncidentVerifier._validate_mutation_schema(
        scenario,
        {"raw_payments": actual},
        {"raw_payments": baseline},
    )


class _AggregateCursor:
    def __init__(
        self,
        rows: tuple[tuple[object, ...], ...],
        scalar_values: tuple[tuple[object, ...], ...],
    ) -> None:
        self.rows = rows
        self.scalar_values = iter(scalar_values)
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def __enter__(self) -> _AggregateCursor:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, statement: object, params: tuple[object, ...] = ()) -> None:
        self.calls.append((str(statement), params))

    def fetchall(self) -> list[tuple[object, ...]]:
        return list(self.rows)

    def fetchone(self) -> tuple[object, ...]:
        return next(self.scalar_values)


class _AggregateConnection:
    def __init__(self, cursor: _AggregateCursor) -> None:
        self.cursor_instance = cursor

    def __enter__(self) -> _AggregateConnection:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def cursor(self) -> _AggregateCursor:
        return self.cursor_instance


class _SilentAggregateCursor(_AggregateCursor):
    def __init__(
        self,
        target_rows: tuple[tuple[object, ...], ...],
        sentinel_rows: tuple[tuple[object, ...], ...],
        scalar_values: tuple[tuple[object, ...], ...],
    ) -> None:
        super().__init__((), scalar_values)
        self._row_batches = (target_rows, sentinel_rows)
        self._fetchall_count = 0

    def fetchall(self) -> list[tuple[object, ...]]:
        rows = self._row_batches[self._fetchall_count]
        self._fetchall_count += 1
        return list(rows)


def _orphan_profile(
    *,
    row_count: int,
    violation_count: int,
    channel: str,
    channel_count: int,
    history: bool,
) -> ProfileSnapshot:
    current = [
        RelationProfileSnapshot(
            relation_name="raw_payments",
            row_count=row_count,
            columns=(),
            business_key_duplicates=(DuplicateProfileFact(name="id", duplicate_count=0),),
            business_fingerprint_duplicates=(
                DuplicateProfileFact(name="order_payment_amount", duplicate_count=0),
            ),
            relationship_violations=(
                RelationshipViolationFact(
                    name="order_id_to_raw_orders_id",
                    violation_count=violation_count,
                ),
            ),
            groups=(
                GroupProfileFact(
                    name="payment_method",
                    columns=("payment_method",),
                    values=((channel,),),
                    counts=(channel_count,),
                ),
            ),
        ),
    ]
    history_snapshots = ()
    if history:
        current.append(
            RelationProfileSnapshot(relation_name="raw_orders", row_count=99, columns=())
        )
        history_snapshots = (
            RelationHistorySnapshot(
                relation_name="raw_orders",
                histories=(
                    HistorySeries(
                        name="order_count_by_day",
                        metric=HistoryMetric.COUNT,
                        points=(
                            HistoryPoint(bucket="2018-04-09", periodic_key="1", value=1),
                        ),
                        watermark_column="order_date",
                        watermark_value="2018-04-09",
                    ),
                ),
            ),
        )
    return ProfileSnapshot(
        schema_version="profile_snapshot.v1",
        profile_spec_version="profile_spec.v1",
        profile_spec_sha256="b" * 64,
        current=tuple(sorted(current, key=lambda item: item.relation_name)),
        history=history_snapshots,
    )


def _silent_profile(
    *,
    row_count: int,
    reverse_violations: int,
    history: bool,
    payment_bucket: str,
    payment_count: int,
    channels: dict[str, int],
) -> ProfileSnapshot:
    payment_profile = RelationProfileSnapshot(
        relation_name="raw_payments",
        row_count=row_count,
        columns=(),
        business_key_duplicates=(DuplicateProfileFact(name="id", duplicate_count=0),),
        business_fingerprint_duplicates=(
            DuplicateProfileFact(name="order_payment_amount", duplicate_count=0),
        ),
        relationship_violations=(
            RelationshipViolationFact(
                name="order_id_to_raw_orders_id",
                violation_count=0,
            ),
        ),
        groups=(
            GroupProfileFact(
                name="payment_method",
                columns=("payment_method",),
                values=tuple((channel,) for channel in channels),
                counts=tuple(channels.values()),
            ),
        ),
    )
    order_profile = RelationProfileSnapshot(
        relation_name="raw_orders",
        row_count=99,
        columns=(),
        relationship_violations=(
            RelationshipViolationFact(
                name="id_to_raw_payments_order_id",
                violation_count=reverse_violations,
            ),
        ),
    )
    if not history:
        history_snapshots = ()
    else:
        history_snapshots = (
            RelationHistorySnapshot(
                relation_name="raw_orders",
                histories=(
                    HistorySeries(
                        name="order_count_by_day",
                        metric=HistoryMetric.COUNT,
                        points=(
                            HistoryPoint(bucket="2018-04-09", periodic_key="1", value=1),
                        ),
                        watermark_column="order_date",
                        watermark_value="2018-04-09",
                        sla_seconds=86400,
                    ),
                ),
            ),
            RelationHistorySnapshot(
                relation_name="raw_payments",
                histories=(
                    HistorySeries(
                        name="payment_count_by_order_date",
                        metric=HistoryMetric.COUNT,
                        points=(
                            HistoryPoint(
                                bucket=payment_bucket,
                                periodic_key=payment_bucket,
                                value=payment_count,
                            ),
                        ),
                    ),
                ),
            ),
        )
    return ProfileSnapshot(
        schema_version="profile_snapshot.v1",
        profile_spec_version="profile_spec.v1",
        profile_spec_sha256="b" * 64,
        current=tuple(
            sorted((order_profile, payment_profile), key=lambda item: item.relation_name)
        ),
        history=history_snapshots,
    )


@pytest.mark.parametrize(
    ("case_id", "rows", "scalars", "history"),
    (
        (
            "silent_payment_drop_record",
            (),
            ((112,), (1,), (0,), (1,), (0,), (0,), (32,), (13,), (55,), (12,)),
            True,
        ),
        (
            "silent_payment_drop_partition_a",
            (),
            ((111,), (3,), (0,), (2,), (0,), (0,), (32,), (13,), (55,), (11,)),
            True,
        ),
        (
            "silent_payment_drop_partition_b",
            (),
            ((111,), (3,), (0,), (2,), (0,), (0,), (32,), (13,), (55,), (11,)),
            False,
        ),
    ),
)
def test_silent_verifier_accepts_exact_private_facts(
    tmp_path: Path,
    case_id: str,
    rows: tuple[tuple[object, ...], ...],
    scalars: tuple[tuple[object, ...], ...],
    history: bool,
) -> None:
    scenario = load_scenario_spec(case_id)
    mutation = next(
        item
        for item in scenario.reset_and_injection_contract.mutations
        if isinstance(item, DeletePaymentRowsMutation)
    )
    deleted = deleted_payment_rows(mutation)
    sentinel = tuple(
        row
        for row in (
            (89, 78, "bank_transfer", 2600),
            (92, 80, "gift_card", 300),
            (111, 97, "bank_transfer", 1400),
        )
        if row not in deleted
    )
    cursor = _SilentAggregateCursor((), sentinel, scalars)
    verifier = IncidentVerifier(
        tmp_path,
        settings=Settings(_env_file=None),
        db_connect=lambda **_: _AggregateConnection(cursor),
    )
    profile = _silent_profile(
        row_count=scalars[0][0],
        reverse_violations=scalars[3][0],
        history=history,
        payment_bucket=(
            "2018-04-07" if case_id.endswith("record") else "2018-03-23"
        ),
        payment_count=scalars[1][0],
        channels={
            "bank_transfer": scalars[6][0],
            "coupon": scalars[7][0],
            "credit_card": scalars[8][0],
            "gift_card": scalars[9][0],
        },
    )

    verifier._validate_silent_payment_drop(scenario, profile)


@pytest.mark.parametrize(
    ("case_id", "rows", "scalars"),
    (
        (
            "orphan_payment_record",
            ((114, 1000, "credit_card", 1000),),
            ((114,), (0,), (0,), (1,), (56,), (99,), (0,)),
        ),
        (
            "orphan_payment_coupon_a",
            (
                (114, 1000, "coupon", 1700),
                (115, 1001, "coupon", 1800),
                (116, 1002, "coupon", 200),
            ),
            ((116,), (0,), (0,), (3,), (16,), (99,), (0,)),
        ),
    ),
)
def test_orphan_verifier_accepts_exact_private_facts(
    tmp_path: Path,
    case_id: str,
    rows: tuple[tuple[object, ...], ...],
    scalars: tuple[tuple[object, ...], ...],
) -> None:
    scenario = load_scenario_spec(case_id)
    cursor = _AggregateCursor(rows, scalars)
    verifier = IncidentVerifier(
        tmp_path,
        settings=Settings(_env_file=None),
        db_connect=lambda **_: _AggregateConnection(cursor),
    )
    profile = _orphan_profile(
        row_count=scalars[0][0],
        violation_count=scalars[3][0],
        channel="credit_card" if case_id == "orphan_payment_record" else "coupon",
        channel_count=scalars[4][0],
        history=True,
    )

    verifier._validate_orphan_payments(scenario, profile)


def test_orphan_verifier_rejects_wrong_relationship_fact(tmp_path: Path) -> None:
    scenario = load_scenario_spec("orphan_payment_coupon_a")
    cursor = _AggregateCursor(
        (
            (114, 1000, "coupon", 1700),
            (115, 1001, "coupon", 1800),
            (116, 1002, "coupon", 200),
        ),
        ((116,), (0,), (0,), (2,), (16,), (99,), (0,)),
    )
    verifier = IncidentVerifier(
        tmp_path,
        settings=Settings(_env_file=None),
        db_connect=lambda **_: _AggregateConnection(cursor),
    )

    with pytest.raises(LabVerificationError, match="orphan-payment"):
        verifier._validate_orphan_payments(
            scenario,
            _orphan_profile(
                row_count=116,
                violation_count=2,
                channel="coupon",
                channel_count=16,
                history=True,
            ),
        )


def test_orphan_verifier_requires_public_history_for_confirmable_case(tmp_path: Path) -> None:
    scenario = load_scenario_spec("orphan_payment_coupon_a")
    cursor = _AggregateCursor(
        (
            (114, 1000, "coupon", 1700),
            (115, 1001, "coupon", 1800),
            (116, 1002, "coupon", 200),
        ),
        ((116,), (0,), (0,), (3,), (16,), (99,), (0,)),
    )
    verifier = IncidentVerifier(
        tmp_path,
        settings=Settings(_env_file=None),
        db_connect=lambda **_: _AggregateConnection(cursor),
    )

    with pytest.raises(LabVerificationError, match="history"):
        verifier._validate_orphan_payments(
            scenario,
            _orphan_profile(
                row_count=116,
                violation_count=3,
                channel="coupon",
                channel_count=16,
                history=False,
            ),
        )
