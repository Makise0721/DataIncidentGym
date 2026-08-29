from __future__ import annotations

import traceback
from typing import Any

import pytest
from psycopg import sql

from data_incident_gym.config import Settings
from data_incident_gym.diagnostic_config import DiagnosticSettings
from data_incident_gym.read_only_db import ReadOnlyProvisioningError, ReadOnlyRoleProvisioner


class _Cursor:
    def __init__(
        self,
        *,
        role: tuple[object, ...] | None = None,
        membership: bool = False,
        owned: tuple[bool, bool, bool] = (False, False, False),
    ) -> None:
        self.role = role
        self.membership = membership
        self.owned = owned
        self.executions: list[tuple[object, object]] = []
        self.last_query = ""

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, query: object, params: object = None) -> None:
        self.executions.append((query, params))
        self.last_query = query.as_string(None) if isinstance(query, sql.Composed) else str(query)

    def fetchone(self) -> tuple[object, ...] | None:
        if "FROM pg_roles" in self.last_query:
            return self.role
        if "pg_auth_members" in self.last_query:
            return (1,) if self.membership else None
        if "pg_database" in self.last_query:
            return self.owned
        return None


class _Connection:
    def __init__(self, cursor: _Cursor) -> None:
        self.cursor_value = cursor
        self.rollback_calls = 0

    def __enter__(self) -> _Connection:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def cursor(self) -> _Cursor:
        return self.cursor_value

    def rollback(self) -> None:
        self.rollback_calls += 1


def _settings() -> tuple[Settings, DiagnosticSettings]:
    admin = Settings(
        _env_file=None,
        postgres_host="127.0.0.1",
        postgres_port=55432,
        postgres_database="data_incident_gym",
        postgres_schema="analytics",
        postgres_user="dig_admin",
        postgres_password="TEST_REDACTED_VALUE",
    )
    reader = DiagnosticSettings(
        _env_file=None,
        postgres_host="127.0.0.1",
        postgres_port=55432,
        postgres_database="data_incident_gym",
        postgres_schema="analytics",
        postgres_password="TEST_REDACTED_VALUE",
    )
    return admin, reader


def test_provisioner_rejects_reader_equal_to_admin() -> None:
    admin, reader = _settings()
    admin = admin.model_copy(update={"postgres_user": "dig_reader"})

    with pytest.raises(ReadOnlyProvisioningError):
        ReadOnlyRoleProvisioner(admin, reader, db_connect=lambda **_: None).provision()


def test_provisioner_rejects_admin_and_diagnostic_location_mismatch() -> None:
    admin, reader = _settings()
    reader = reader.model_copy(update={"postgres_port": 55433})

    with pytest.raises(ReadOnlyProvisioningError):
        ReadOnlyRoleProvisioner(admin, reader, db_connect=lambda **_: None).provision()


def test_provisioner_rejects_reader_with_role_membership_or_owned_objects() -> None:
    admin, reader = _settings()
    cursor = _Cursor(
        role=("dig_reader", False, False, False, False, False, True, True),
        membership=True,
    )
    connection = _Connection(cursor)

    def connect(**_: Any) -> _Connection:
        return connection

    with pytest.raises(ReadOnlyProvisioningError):
        ReadOnlyRoleProvisioner(admin, reader, db_connect=connect).provision()

    assert connection.rollback_calls == 1


def test_provisioner_uses_identifiers_and_redacts_both_passwords() -> None:
    admin, reader = _settings()
    cursor = _Cursor()
    connection = _Connection(cursor)

    def connect(**_: Any) -> _Connection:
        return connection

    provisioner = ReadOnlyRoleProvisioner(admin, reader, db_connect=connect)
    provisioner.provision()

    composed = [query for query, _ in cursor.executions if isinstance(query, sql.Composed)]
    rendered = "\n".join(query.as_string(None) for query in composed)
    assert '"dig_reader"' in rendered
    assert "CREATE ROLE" in rendered
    assert "GRANT SELECT" in rendered

    failing_cursor = _Cursor()

    def fail_execute(query: object, params: object = None) -> None:
        failing_cursor.executions.append((query, params))
        raise RuntimeError("failed with TEST_REDACTED_VALUE")

    failing_cursor.execute = fail_execute  # type: ignore[method-assign]
    failing_connection = _Connection(failing_cursor)

    with pytest.raises(ReadOnlyProvisioningError) as error:
        ReadOnlyRoleProvisioner(
            admin,
            reader,
            db_connect=lambda **_: failing_connection,
        ).provision()
    assert "TEST_REDACTED_VALUE" not in str(error.value)
    assert "TEST_REDACTED_VALUE" not in "".join(traceback.format_exception(error.value))
    assert error.value.__cause__ is None
    assert error.value.__context__ is None
