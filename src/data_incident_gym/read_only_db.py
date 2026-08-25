from __future__ import annotations

from collections.abc import Callable
from typing import Any, NoReturn

import psycopg
from psycopg import sql

from data_incident_gym.config import Settings
from data_incident_gym.diagnostic_config import DiagnosticSettings

DatabaseConnect = Callable[..., Any]


class ReadOnlyProvisioningError(RuntimeError):
    code = "READ_ONLY_PROVISIONING_ERROR"


def _raise_without_context(error: ReadOnlyProvisioningError) -> NoReturn:
    try:
        raise error from None
    except ReadOnlyProvisioningError as raised:
        raised.__cause__ = None
        raised.__context__ = None
        raise


class ReadOnlyRoleProvisioner:
    """Converge the diagnostic role from the administrative connection only."""

    def __init__(
        self,
        admin_settings: Settings,
        diagnostic_settings: DiagnosticSettings,
        *,
        db_connect: DatabaseConnect = psycopg.connect,
    ) -> None:
        self.admin_settings = admin_settings
        self.diagnostic_settings = diagnostic_settings
        self.db_connect = db_connect

    def _validate_configuration(self) -> None:
        admin = self.admin_settings
        diagnostic = self.diagnostic_settings
        if admin.postgres_user == diagnostic.postgres_user:
            _raise_without_context(
                ReadOnlyProvisioningError("管理用户与诊断 reader 用户不能同名")
            )
        if any(
            (
                admin.postgres_host != diagnostic.postgres_host,
                admin.postgres_port != diagnostic.postgres_port,
                admin.postgres_database != diagnostic.postgres_database,
                admin.postgres_schema != diagnostic.postgres_schema,
            )
        ):
            _raise_without_context(
                ReadOnlyProvisioningError("管理连接与诊断连接位置不一致")
            )

    @staticmethod
    def _redact(value: str, *secrets: str) -> str:
        redacted = value
        for secret in secrets:
            if secret:
                redacted = redacted.replace(secret, "***")
        return redacted

    def _reject_unsafe_existing_role(self, cursor: Any, role_name: str) -> bool:
        cursor.execute(
            sql.SQL(
                "SELECT rolname, rolsuper, rolcreatedb, rolcreaterole, "
                "rolreplication, rolbypassrls, rolinherit, rolcanlogin "
                "FROM pg_roles WHERE rolname = %s"
            ),
            (role_name,),
        )
        role = cursor.fetchone()
        if role is None:
            return False

        cursor.execute(
            sql.SQL(
                "SELECT 1 FROM pg_auth_members members "
                "JOIN pg_roles member_role ON member_role.oid = members.member "
                "WHERE member_role.rolname = %s LIMIT 1"
            ),
            (role_name,),
        )
        if cursor.fetchone() is not None:
            _raise_without_context(
                ReadOnlyProvisioningError("reader role membership 不符合只读边界")
            )

        cursor.execute(
            sql.SQL(
                "SELECT EXISTS (SELECT 1 FROM pg_database "
                "WHERE datname = %s AND datdba = (SELECT oid FROM pg_roles WHERE rolname = %s)), "
                "EXISTS (SELECT 1 FROM pg_namespace "
                "WHERE nspname = %s AND nspowner = (SELECT oid FROM pg_roles WHERE rolname = %s)), "
                "EXISTS (SELECT 1 FROM pg_class relation "
                "JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace "
                "WHERE namespace.nspname = %s "
                "AND relation.relowner = (SELECT oid FROM pg_roles WHERE rolname = %s))"
            ),
            (
                self.admin_settings.postgres_database,
                role_name,
                self.admin_settings.postgres_schema,
                role_name,
                self.admin_settings.postgres_schema,
                role_name,
            ),
        )
        ownership = cursor.fetchone()
        if ownership is None or any(ownership):
            _raise_without_context(
                ReadOnlyProvisioningError("reader role owned objects 不符合只读边界")
            )
        return True

    def _create_or_converge_role(self, cursor: Any, role_name: str, password: str) -> None:
        role_identifier = sql.Identifier(role_name)
        password_literal = sql.Literal(password)
        cursor.execute(
            sql.SQL(
                "SELECT rolname FROM pg_roles WHERE rolname = %s"
            ),
            (role_name,),
        )
        role_exists = cursor.fetchone() is not None
        if not role_exists:
            cursor.execute(
                sql.SQL(
                    "CREATE ROLE {} WITH LOGIN NOINHERIT NOSUPERUSER NOCREATEDB "
                    "NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}"
                ).format(role_identifier, password_literal)
            )
        else:
            cursor.execute(
                sql.SQL(
                    "ALTER ROLE {} WITH LOGIN NOINHERIT NOSUPERUSER NOCREATEDB "
                    "NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}"
                ).format(role_identifier, password_literal)
            )
        cursor.execute(
            sql.SQL("ALTER ROLE {} SET default_transaction_read_only = on").format(
                role_identifier
            )
        )

    def _converge_privileges(self, cursor: Any, role_name: str) -> None:
        database = sql.Identifier(self.admin_settings.postgres_database)
        schema = sql.Identifier(self.admin_settings.postgres_schema)
        reader = sql.Identifier(role_name)
        admin = sql.Identifier(self.admin_settings.postgres_user)
        cursor.execute(
            sql.SQL("REVOKE ALL PRIVILEGES ON DATABASE {} FROM {}").format(database, reader)
        )
        cursor.execute(
            sql.SQL("REVOKE ALL PRIVILEGES ON SCHEMA {} FROM {}").format(schema, reader)
        )
        cursor.execute(
            sql.SQL("REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA {} FROM {}").format(
                schema, reader
            )
        )
        cursor.execute(
            sql.SQL("REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA {} FROM {}").format(
                schema, reader
            )
        )
        cursor.execute(
            sql.SQL(
                "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA {} "
                "REVOKE ALL ON TABLES FROM {}"
            ).format(admin, schema, reader)
        )
        cursor.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(database, reader))
        cursor.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(schema, reader))
        cursor.execute(
            sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA {} TO {}").format(schema, reader)
        )
        cursor.execute(
            sql.SQL(
                "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA {} "
                "GRANT SELECT ON TABLES TO {}"
            ).format(admin, schema, reader)
        )

    def provision(self) -> None:
        self._validate_configuration()
        role_name = self.diagnostic_settings.postgres_user
        admin_password = self.admin_settings.postgres_password.get_secret_value()
        reader_password = self.diagnostic_settings.postgres_password.get_secret_value()
        try:
            with self.db_connect(
                host=self.admin_settings.postgres_host,
                port=self.admin_settings.postgres_port,
                dbname=self.admin_settings.postgres_database,
                user=self.admin_settings.postgres_user,
                password=admin_password,
            ) as connection:
                try:
                    with connection.cursor() as cursor:
                        self._reject_unsafe_existing_role(cursor, role_name)
                        self._create_or_converge_role(cursor, role_name, reader_password)
                        self._converge_privileges(cursor, role_name)
                except Exception:
                    connection.rollback()
                    raise
        except ReadOnlyProvisioningError:
            raise
        except Exception as exc:
            message = self._redact(str(exc), admin_password, reader_password)
            _raise_without_context(ReadOnlyProvisioningError(f"只读角色配置失败：{message}"))
