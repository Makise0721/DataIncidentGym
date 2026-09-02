from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import psycopg
import pytest

from data_incident_gym.config import PROJECT_ROOT, Settings
from data_incident_gym.diagnostic_config import DiagnosticSettings
from data_incident_gym.evidence_tools import EvidenceTools
from data_incident_gym.lab import IncidentLab, ScenarioRun

SCENARIO_ID = "schema_type_change_payment_amount"
FAILURE_NODE = "model.jaffle_shop.stg_payments"
FORBIDDEN_TABLE = "m7_forbidden_write"


@dataclass(frozen=True)
class RealEvidenceRun:
    run: ScenarioRun
    tools: EvidenceTools


@pytest.fixture(scope="module")
def real_evidence_run() -> Iterator[RealEvidenceRun]:
    lab = IncidentLab(Settings(_env_file=None), PROJECT_ROOT)
    lab.reset(SCENARIO_ID)
    try:
        lab.prepare(SCENARIO_ID)
        run = lab.build(SCENARIO_ID)
        tools = EvidenceTools.for_run(
            run.run_id,
            DiagnosticSettings(_env_file=None),
            PROJECT_ROOT,
        )
        yield RealEvidenceRun(run, tools)
    finally:
        lab.restore(SCENARIO_ID)


@pytest.mark.integration
def test_real_run_results_and_node_error_are_stable(
    real_evidence_run: RealEvidenceRun,
) -> None:
    tools = real_evidence_run.tools
    run_id = real_evidence_run.run.run_id
    first = tools.get_dbt_run_results(run_id)[0]
    second = tools.get_dbt_run_results(run_id)[0]
    error = tools.get_dbt_node_error(run_id, FAILURE_NODE)[0]

    assert first.content.run_status == "FAILED"
    assert first.content.failed_nodes == (FAILURE_NODE,)
    assert first.evidence_id == second.evidence_id
    assert first.content_digest == second.content_digest
    assert error.content.node_id == FAILURE_NODE
    assert "operator does not exist: text / integer" in error.content.message
    assert "\\" not in error.content.message
    assert "compiled code at" not in error.content.message


@pytest.mark.integration
def test_real_schema_profile_and_history_are_stable(
    real_evidence_run: RealEvidenceRun,
) -> None:
    tools = real_evidence_run.tools
    schema_first = tools.get_relation_schema("raw_payments")[0]
    schema_second = tools.get_relation_schema("raw_payments")[0]
    profile_first = tools.get_relation_data_profile("raw_payments")[0]
    profile_second = tools.get_relation_data_profile("raw_payments")[0]
    history_first = tools.get_relation_history("raw_orders")[0]
    history_second = tools.get_relation_history("raw_orders")[0]

    columns = {column.name: column for column in schema_first.content.columns}
    assert columns["amount"].data_type == "text"
    assert "total_amount" not in columns
    assert schema_first.evidence_id == schema_second.evidence_id
    assert profile_first.evidence_id == profile_second.evidence_id
    assert history_first.evidence_id == history_second.evidence_id

    points = {
        point.bucket: point.value
        for series in history_first.content.snapshot.histories
        if series.name == "order_count_by_day"
        for point in series.points
    }
    assert points["2018-04-02"] == 1
    assert points["2018-03-26"] == 3


@pytest.mark.integration
def test_real_reader_permission_is_read_only() -> None:
    diagnostic = DiagnosticSettings(_env_file=None)
    admin = Settings(_env_file=None)
    reader_kwargs = {
        "host": diagnostic.postgres_host,
        "port": diagnostic.postgres_port,
        "dbname": diagnostic.postgres_database,
        "user": diagnostic.postgres_user,
        "password": diagnostic.postgres_password.get_secret_value(),
    }
    admin_kwargs = {
        "host": admin.postgres_host,
        "port": admin.postgres_port,
        "dbname": admin.postgres_database,
        "user": admin.postgres_user,
        "password": admin.postgres_password.get_secret_value(),
    }

    try:
        with psycopg.connect(**reader_kwargs) as connection:
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SHOW transaction_read_only")
                    assert cursor.fetchone() == ("on",)
                    cursor.execute(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = %s AND table_name = %s",
                        (diagnostic.postgres_schema, "raw_payments"),
                    )
                    assert cursor.fetchall()
                    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
                        cursor.execute(
                            f"CREATE TABLE {diagnostic.postgres_schema}.{FORBIDDEN_TABLE} "
                            "(id integer)"
                        )
            finally:
                connection.rollback()
    finally:
        with psycopg.connect(**admin_kwargs) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT to_regclass(%s)",
                (f"{diagnostic.postgres_schema}.{FORBIDDEN_TABLE}",),
            )
            assert cursor.fetchone() == (None,)
