from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import psycopg
import pytest

from data_incident_gym.config import PROJECT_ROOT, Settings
from data_incident_gym.diagnostic_config import DiagnosticSettings
from data_incident_gym.evidence_tools import EvidenceTools
from data_incident_gym.incidents import CASE_ID
from data_incident_gym.lab import FaultRun, IncidentLab

FAILURE_NODE = "model.jaffle_shop.stg_payments"
FORBIDDEN_TABLE = "m3_forbidden_write"


@dataclass(frozen=True)
class RealEvidenceRun:
    run: FaultRun
    tools: EvidenceTools


@pytest.fixture(scope="module")
def real_evidence_run() -> Iterator[RealEvidenceRun]:
    project_root = PROJECT_ROOT
    lab = IncidentLab(Settings(_env_file=None), project_root)
    lab.reset(CASE_ID)
    try:
        lab.inject(CASE_ID)
        run = lab.build(CASE_ID)
        tools = EvidenceTools.for_run(
            run.run_id,
            DiagnosticSettings(_env_file=None),
            project_root,
        )
        yield RealEvidenceRun(run, tools)
    finally:
        lab.reset(CASE_ID)


@pytest.mark.integration
def test_real_get_dbt_run_results_reports_expected_failure(
    real_evidence_run: RealEvidenceRun,
) -> None:
    tools = real_evidence_run.tools
    first = tools.get_dbt_run_results(real_evidence_run.run.run_id)[0]
    second = tools.get_dbt_run_results(real_evidence_run.run.run_id)[0]

    fact = first.content
    assert fact.run_status == "FAILED"
    assert fact.failed_nodes == (FAILURE_NODE,)
    assert {
        "model.jaffle_shop.orders",
        "model.jaffle_shop.customers",
    } <= set(fact.skipped_nodes)
    assert first.evidence_id == second.evidence_id
    assert first.content_digest == second.content_digest


@pytest.mark.integration
def test_real_get_dbt_node_error_is_normalized_and_path_free(
    real_evidence_run: RealEvidenceRun,
) -> None:
    tools = real_evidence_run.tools
    first = tools.get_dbt_node_error(real_evidence_run.run.run_id, FAILURE_NODE)[0]
    second = tools.get_dbt_node_error(real_evidence_run.run.run_id, FAILURE_NODE)[0]

    assert 'column "amount" does not exist' in first.content.message
    assert str(PROJECT_ROOT) not in first.content.message
    assert str(PROJECT_ROOT).replace("\\", "/") not in first.content.message
    assert "\\" not in first.content.message
    assert "compiled code at" not in first.content.message
    assert first.evidence_id == second.evidence_id
    assert first.content_digest == second.content_digest


@pytest.mark.integration
def test_real_get_relation_schema_observes_total_amount_with_reader(
    real_evidence_run: RealEvidenceRun,
) -> None:
    record = real_evidence_run.tools.get_relation_schema("raw_payments")[0]

    assert record.source.value == "postgres_catalog"
    column_names = {column.name for column in record.content.columns}
    assert "total_amount" in column_names
    assert "amount" not in column_names


@pytest.mark.integration
def test_real_get_dbt_lineage_finds_upstream_seed_and_downstream_models(
    real_evidence_run: RealEvidenceRun,
) -> None:
    tools = real_evidence_run.tools
    downstream = tools.get_dbt_lineage(FAILURE_NODE, "downstream")[0].content.related_nodes
    upstream = tools.get_dbt_lineage(FAILURE_NODE, "upstream")[0].content.related_nodes

    assert {
        node.node_id for node in downstream if node.resource_type == "model"
    } == {
        "model.jaffle_shop.orders",
        "model.jaffle_shop.customers",
    }
    assert any(
        node.node_id == "seed.jaffle_shop.raw_payments"
        and node.resource_type == "seed"
        for node in upstream
    )


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
                "SELECT rolsuper, rolcreatedb, rolcreaterole, "
                "rolreplication, rolbypassrls "
                "FROM pg_roles WHERE rolname = %s",
                (diagnostic.postgres_user,),
            )
            assert cursor.fetchone() == (False, False, False, False, False)
            cursor.execute(
                "SELECT to_regclass(%s)",
                (f"{diagnostic.postgres_schema}.{FORBIDDEN_TABLE}",),
            )
            assert cursor.fetchone() == (None,)
