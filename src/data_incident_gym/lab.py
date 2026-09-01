from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import psycopg
from psycopg import sql

from data_incident_gym.baseline import (
    CATALOG_COLUMNS_QUERY,
    EXPECTED_RELATION_COUNTS,
    BaselineBuilder,
    BaselineSummary,
    ColumnSummary,
    RelationSummary,
    make_baseline_summary,
)
from data_incident_gym.config import PROJECT_ROOT, Settings
from data_incident_gym.dbt_runner import DbtExecutionError, DbtRunner
from data_incident_gym.lab_verifier import (
    IncidentVerifier,
    LabVerificationError,
    ScenarioVerificationStatus,
)
from data_incident_gym.profiles import (
    AggregateSnapshotReader,
    ProfileError,
    ProfileSnapshot,
    load_profile_spec,
    settings_connection_kwargs,
    write_profile_snapshot,
)
from data_incident_gym.run_context import (
    RunContextError,
    clear_active_run,
    publish_active_run,
)
from data_incident_gym.scenarios import (
    AddNullableColumnMutation,
    ColumnRenameMutation,
    ColumnTypeMutation,
    DuplicatePaymentRowsMutation,
    NoMutation,
    OrphanPaymentRowsMutation,
    PaymentRow,
    ScenarioError,
    ScenarioSpec,
    SetFieldNullMutation,
    duplicate_payment_rows,
    load_scenario_spec,
    orphan_payment_rows,
)

DatabaseConnect = Callable[..., Any]
CaseState = Literal["MISSING", "HEALTHY", "INJECTED", "DRIFTED"]
RUN_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
RunIdFactory = Callable[[], str]
_TYPE_SQL = {"integer": sql.SQL("integer"), "text": sql.SQL("text")}
_EXPECTED_ARTIFACTS = {
    "manifest": "dbt/target/manifest.json",
    "run_results": "dbt/target/run_results.json",
    "dbt_log": "dbt/logs/dbt.log",
    "schema": "schema.json",
    "profile_snapshot": "profile_snapshot.json",
    "incident_brief": "incident_brief.json",
}


class LabError(RuntimeError):
    code = "LAB_ERROR"


class InvalidIncidentState(LabError):
    code = "INVALID_INCIDENT_STATE"


class IncidentExecutionError(LabError):
    code = "INCIDENT_EXECUTION_ERROR"


class FaultVerificationError(LabError):
    code = "FAULT_VERIFICATION_ERROR"


@dataclass(frozen=True)
class ResetResult:
    case_id: str
    state: Literal["HEALTHY"]
    fingerprint: str


@dataclass(frozen=True)
class PreparationResult:
    case_id: str
    state: Literal["HEALTHY", "INJECTED"]
    fingerprint: str


@dataclass(frozen=True)
class ScenarioRun:
    run_id: str
    artifact_dir: Path
    verification_status: ScenarioVerificationStatus
    dbt_exit_code: int


class IncidentLab:
    def __init__(
        self,
        settings: Settings,
        project_root: Path = PROJECT_ROOT,
        *,
        baseline_builder: BaselineBuilder | None = None,
        db_connect: DatabaseConnect | None = None,
        dbt_runner: DbtRunner | None = None,
        verifier: IncidentVerifier | None = None,
        run_id_factory: RunIdFactory | None = None,
    ) -> None:
        self.settings = settings
        self.project_root = project_root
        self.db_connect = db_connect or psycopg.connect
        self.baseline_builder = baseline_builder or BaselineBuilder(
            settings,
            project_root,
            db_connect=self.db_connect,
        )
        self.dbt_runner = dbt_runner or DbtRunner(settings, project_root)
        self.verifier = verifier or IncidentVerifier(
            project_root,
            settings=settings,
            db_connect=self.db_connect,
        )
        self.run_id_factory = run_id_factory or (lambda: uuid4().hex)

    def _connection_kwargs(self) -> dict[str, object]:
        return settings_connection_kwargs(self.settings)

    def _redact(self, value: str) -> str:
        secret = self.settings.postgres_password.get_secret_value()
        return value.replace(secret, "***") if secret else value

    @staticmethod
    def _clean(error: LabError | ScenarioError) -> LabError | ScenarioError:
        error.__cause__ = None
        error.__context__ = None
        return error

    def _load_case(self, case_id: str) -> ScenarioSpec:
        try:
            return load_scenario_spec(case_id, self.project_root)
        except ScenarioError as exc:
            raise self._clean(ScenarioError(self._redact(str(exc)))) from None

    def _inspect_relation(self, relation_name: str) -> RelationSummary | None:
        try:
            with (
                self.db_connect(**self._connection_kwargs()) as connection,
                connection.cursor() as cursor,
            ):
                cursor.execute(
                    CATALOG_COLUMNS_QUERY,
                    (self.settings.postgres_schema, relation_name),
                )
                rows = cursor.fetchall()
                if not rows:
                    return None
                columns = tuple(
                    ColumnSummary(
                        name=row[0],
                        data_type=row[1],
                        nullable=row[2] == "YES",
                        ordinal_position=row[3],
                    )
                    for row in rows
                )
                cursor.execute(
                    sql.SQL("SELECT count(*) FROM {}.{}").format(
                        sql.Identifier(self.settings.postgres_schema),
                        sql.Identifier(relation_name),
                    )
                )
                count_row = cursor.fetchone()
                if count_row is None:
                    raise IncidentExecutionError(f"无法读取关系行数：{relation_name}")
                return RelationSummary(relation_name, int(count_row[0]), columns)
        except LabError:
            raise
        except Exception as exc:
            raise self._clean(
                IncidentExecutionError(f"读取 Schema 失败：{self._redact(str(exc))}")
            ) from None

    def _inspect_relations(self, relation_names: Sequence[str]) -> tuple[RelationSummary, ...]:
        inspected: list[RelationSummary] = []
        for relation_name in relation_names:
            relation = self._inspect_relation(relation_name)
            if relation is None:
                raise self._clean(IncidentExecutionError(f"关系不存在：{relation_name}"))
            inspected.append(relation)
        return tuple(inspected)

    @staticmethod
    def _fingerprint(relations: Sequence[RelationSummary], schema: str) -> str:
        return make_baseline_summary(schema, relations).fingerprint

    def _write_text(self, path: Path, text: str) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        except OSError as exc:
            raise self._clean(
                IncidentExecutionError(f"无法写入运行产物：{self._redact(str(exc))}")
            ) from None

    def _redact_file(self, path: Path) -> None:
        try:
            if not path.is_file():
                return
            text = path.read_text(encoding="utf-8", errors="replace")
            redacted = self._redact(text)
            if redacted != text:
                path.write_text(redacted, encoding="utf-8")
        except OSError as exc:
            raise self._clean(
                IncidentExecutionError(f"无法脱敏运行产物：{self._redact(str(exc))}")
            ) from None

    def _start_postgres(self) -> None:
        try:
            self.baseline_builder.start_postgres()
        except Exception as exc:
            raise self._clean(
                IncidentExecutionError(f"启动 PostgreSQL 失败：{self._redact(str(exc))}")
            ) from None

    def _build_healthy_baseline(self) -> BaselineSummary:
        try:
            return self.baseline_builder.build()
        except Exception as exc:
            raise self._clean(
                IncidentExecutionError(f"构建健康基线失败：{self._redact(str(exc))}")
            ) from None

    def _clear_active_run(self) -> None:
        try:
            clear_active_run(self.project_root)
        except RunContextError as exc:
            raise self._clean(
                IncidentExecutionError(self._redact(str(exc)))
            ) from None

    def _healthy_relation(self, relation_name: str) -> RelationSummary:
        relation = self._inspect_relation(relation_name)
        if relation is None:
            raise self._clean(InvalidIncidentState(f"健康关系不存在：{relation_name}"))
        return relation

    @staticmethod
    def _column_map(relation: RelationSummary) -> dict[str, ColumnSummary]:
        return {column.name: column for column in relation.columns}

    def _read_null_target(self, mutation: SetFieldNullMutation) -> object:
        statement = sql.SQL("SELECT {} FROM {}.{} WHERE {} = %s").format(
            sql.Identifier(mutation.column),
            sql.Identifier(self.settings.postgres_schema),
            sql.Identifier(mutation.relation),
            sql.Identifier(mutation.selector_column),
        )
        try:
            with (
                self.db_connect(**self._connection_kwargs()) as connection,
                connection.cursor() as cursor,
            ):
                cursor.execute(statement, (mutation.selector_value,))
                rows = cursor.fetchall()
        except LabError:
            raise
        except Exception as exc:
            raise self._clean(
                IncidentExecutionError(f"读取 NULL mutation 目标失败：{self._redact(str(exc))}")
            ) from None
        if len(rows) != 1:
            raise self._clean(InvalidIncidentState("NULL mutation selector 必须匹配恰好一行"))
        return rows[0][0]

    def _null_count(self, mutation: SetFieldNullMutation) -> int:
        statement = sql.SQL("SELECT count(*) FROM {}.{} WHERE {} IS NULL").format(
            sql.Identifier(self.settings.postgres_schema),
            sql.Identifier(mutation.relation),
            sql.Identifier(mutation.column),
        )
        try:
            with (
                self.db_connect(**self._connection_kwargs()) as connection,
                connection.cursor() as cursor,
            ):
                cursor.execute(statement)
                row = cursor.fetchone()
        except LabError:
            raise
        except Exception as exc:
            raise self._clean(
                IncidentExecutionError(f"读取 NULL mutation 计数失败：{self._redact(str(exc))}")
            ) from None
        if row is None:
            raise self._clean(InvalidIncidentState("NULL mutation 计数不可用"))
        return int(row[0])

    def _write_null_target(
        self,
        mutation: SetFieldNullMutation,
        *,
        expected_current: object,
        replacement: object,
    ) -> None:
        statement = sql.SQL(
            "UPDATE {}.{} SET {} = %s "
            "WHERE {} = %s AND {} IS NOT DISTINCT FROM %s"
        ).format(
            sql.Identifier(self.settings.postgres_schema),
            sql.Identifier(mutation.relation),
            sql.Identifier(mutation.column),
            sql.Identifier(mutation.selector_column),
            sql.Identifier(mutation.column),
        )
        try:
            with (
                self.db_connect(**self._connection_kwargs()) as connection,
                connection.transaction(),
                connection.cursor() as cursor,
            ):
                cursor.execute(
                    statement,
                    (replacement, mutation.selector_value, expected_current),
                )
                if cursor.rowcount != 1:
                    raise InvalidIncidentState("NULL mutation 必须更新恰好一行")
        except LabError:
            raise
        except Exception as exc:
            raise self._clean(
                IncidentExecutionError(f"写入 NULL mutation 目标失败：{self._redact(str(exc))}")
            ) from None

    def _payment_row_count(self, row: tuple[int, int, str, int]) -> int:
        statement = sql.SQL(
            "SELECT count(*) FROM {}.{} "
            "WHERE id IS NOT DISTINCT FROM %s "
            "AND order_id IS NOT DISTINCT FROM %s "
            "AND payment_method IS NOT DISTINCT FROM %s "
            "AND amount IS NOT DISTINCT FROM %s"
        ).format(
            sql.Identifier(self.settings.postgres_schema),
            sql.Identifier("raw_payments"),
        )
        try:
            with (
                self.db_connect(**self._connection_kwargs()) as connection,
                connection.cursor() as cursor,
            ):
                cursor.execute(statement, row)
                result = cursor.fetchone()
        except LabError:
            raise
        except Exception as exc:
            raise self._clean(
                IncidentExecutionError(
                    f"读取 duplicate-payment 状态失败：{self._redact(str(exc))}"
                )
            ) from None
        if result is None:
            raise InvalidIncidentState("duplicate-payment 计数不可用")
        return int(result[0])

    def _payment_row_total(self) -> int:
        relation = self._healthy_relation("raw_payments")
        return relation.row_count

    def _duplicate_payment_state(self, mutation: DuplicatePaymentRowsMutation) -> CaseState:
        pairs = duplicate_payment_rows(mutation)
        baseline_count = EXPECTED_RELATION_COUNTS["raw_payments"]
        total = self._payment_row_total()
        source_rows = tuple(source for source, _ in pairs)
        inserted_rows = tuple(inserted for _, inserted in pairs)
        if mutation.mode == "EXACT_RECORD":
            if total == baseline_count and all(
                self._payment_row_count(row) == 1 for row in source_rows
            ):
                return "HEALTHY"
            if total == baseline_count + len(inserted_rows) and all(
                self._payment_row_count(row) == 2 for row in source_rows
            ):
                return "INJECTED"
            return "DRIFTED"
        if total == baseline_count and all(
            self._payment_row_count(row) == 1 for row in source_rows
        ) and all(self._payment_row_count(row) == 0 for row in inserted_rows):
            return "HEALTHY"
        if total == baseline_count + len(inserted_rows) and all(
            self._payment_row_count(row) == 1 for row in source_rows
        ) and all(self._payment_row_count(row) == 1 for row in inserted_rows):
            return "INJECTED"
        return "DRIFTED"

    def _orphan_payment_state(self, mutation: OrphanPaymentRowsMutation) -> CaseState:
        rows = orphan_payment_rows(mutation)
        baseline_count = EXPECTED_RELATION_COUNTS["raw_payments"]
        total = self._payment_row_total()
        if total == baseline_count and all(self._payment_row_count(row) == 0 for row in rows):
            return "HEALTHY"
        if total == baseline_count + len(rows) and all(
            self._payment_row_count(row) == 1 for row in rows
        ):
            return "INJECTED"
        return "DRIFTED"

    def _insert_payment_rows(self, rows: Sequence[PaymentRow], *, label: str) -> None:
        values = sql.SQL(", ").join(sql.SQL("(%s, %s, %s, %s)") for _ in rows)
        statement = sql.SQL(
            "INSERT INTO {}.{} (id, order_id, payment_method, amount) VALUES {}"
        ).format(
            sql.Identifier(self.settings.postgres_schema),
            sql.Identifier("raw_payments"),
            values,
        )
        parameters = tuple(value for row in rows for value in row)
        try:
            with (
                self.db_connect(**self._connection_kwargs()) as connection,
                connection.transaction(),
                connection.cursor() as cursor,
            ):
                cursor.execute(statement, parameters)
                if cursor.rowcount != len(rows):
                    raise InvalidIncidentState(f"{label} 必须插入精确行数")
        except LabError:
            raise
        except Exception as exc:
            raise self._clean(
                IncidentExecutionError(
                    f"写入 {label} 失败：{self._redact(str(exc))}"
                )
            ) from None

    def _delete_payment_rows(self, rows: Sequence[PaymentRow], *, label: str) -> None:
        statement = sql.SQL(
            "WITH target AS ("
            "SELECT ctid FROM {}.{} "
            "WHERE id IS NOT DISTINCT FROM %s "
            "AND order_id IS NOT DISTINCT FROM %s "
            "AND payment_method IS NOT DISTINCT FROM %s "
            "AND amount IS NOT DISTINCT FROM %s "
            "ORDER BY ctid DESC LIMIT 1"
            ") DELETE FROM {}.{} WHERE ctid IN (SELECT ctid FROM target)"
        ).format(
            sql.Identifier(self.settings.postgres_schema),
            sql.Identifier("raw_payments"),
            sql.Identifier(self.settings.postgres_schema),
            sql.Identifier("raw_payments"),
        )
        try:
            with (
                self.db_connect(**self._connection_kwargs()) as connection,
                connection.transaction(),
                connection.cursor() as cursor,
            ):
                deleted = 0
                for row in rows:
                    cursor.execute(statement, row)
                    deleted += cursor.rowcount
                if deleted != len(rows):
                    raise InvalidIncidentState(f"{label} 必须删除精确行数")
        except LabError:
            raise
        except Exception as exc:
            raise self._clean(
                IncidentExecutionError(
                    f"恢复 {label} 失败：{self._redact(str(exc))}"
                )
            ) from None

    def _insert_payment_duplicates(self, mutation: DuplicatePaymentRowsMutation) -> None:
        rows = tuple(inserted for _, inserted in duplicate_payment_rows(mutation))
        self._insert_payment_rows(rows, label="duplicate-payment mutation")

    def _delete_payment_duplicates(self, mutation: DuplicatePaymentRowsMutation) -> None:
        rows = tuple(inserted for _, inserted in duplicate_payment_rows(mutation))
        self._delete_payment_rows(rows, label="duplicate-payment restore")

    def _insert_orphan_payments(self, mutation: OrphanPaymentRowsMutation) -> None:
        self._insert_payment_rows(orphan_payment_rows(mutation), label="orphan-payment mutation")

    def _delete_orphan_payments(self, mutation: OrphanPaymentRowsMutation) -> None:
        self._delete_payment_rows(orphan_payment_rows(mutation), label="orphan-payment restore")

    def _ensure_healthy_for_prepare(self, spec: ScenarioSpec) -> None:
        for mutation in spec.reset_and_injection_contract.mutations:
            if isinstance(mutation, NoMutation):
                continue
            if isinstance(mutation, SetFieldNullMutation):
                if (
                    self._read_null_target(mutation) != mutation.expected_value
                    or self._null_count(mutation) != 0
                ):
                    raise InvalidIncidentState("prepare 要求初始 NULL mutation 状态为健康")
                continue
            if isinstance(mutation, DuplicatePaymentRowsMutation):
                if self._duplicate_payment_state(mutation) != "HEALTHY":
                    raise InvalidIncidentState("prepare 要求 duplicate-payment 状态为健康")
                continue
            if isinstance(mutation, OrphanPaymentRowsMutation):
                if self._orphan_payment_state(mutation) != "HEALTHY":
                    raise InvalidIncidentState("prepare 要求 orphan-payment 状态为健康")
                continue
            relation = self._healthy_relation(mutation.relation)
            columns = self._column_map(relation)
            if isinstance(mutation, ColumnRenameMutation):
                if mutation.from_column not in columns or mutation.to_column in columns:
                    raise InvalidIncidentState("prepare 要求初始 Schema 为健康状态")
            elif isinstance(mutation, ColumnTypeMutation):
                column = columns.get(mutation.column)
                if column is None or column.data_type != mutation.from_type:
                    raise InvalidIncidentState("prepare 要求初始字段类型为健康状态")
            elif isinstance(mutation, AddNullableColumnMutation):
                if mutation.column in columns:
                    raise InvalidIncidentState("prepare 要求 distractor 尚未存在")
            elif not isinstance(mutation, NoMutation):
                raise InvalidIncidentState("存在未授权 mutation")

    def _drop_dependency(self, relation: str) -> None:
        view = {
            "raw_orders": "stg_orders",
            "raw_payments": "stg_payments",
        }.get(relation)
        if view is None:
            return
        statement = sql.SQL("DROP VIEW IF EXISTS {}.{}").format(
            sql.Identifier(self.settings.postgres_schema),
            sql.Identifier(view),
        )
        try:
            with (
                self.db_connect(**self._connection_kwargs()) as connection,
                connection.transaction(),
                connection.cursor() as cursor,
            ):
                cursor.execute(statement)
        except Exception as exc:
            raise self._clean(
                IncidentExecutionError(f"无法准备字段类型变化：{self._redact(str(exc))}")
            ) from None

    def _rename_column(self, mutation: ColumnRenameMutation, *, restore: bool = False) -> None:
        source = mutation.to_column if restore else mutation.from_column
        target = mutation.from_column if restore else mutation.to_column
        statement = sql.SQL("ALTER TABLE {}.{} RENAME COLUMN {} TO {}").format(
            sql.Identifier(self.settings.postgres_schema),
            sql.Identifier(mutation.relation),
            sql.Identifier(source),
            sql.Identifier(target),
        )
        try:
            with (
                self.db_connect(**self._connection_kwargs()) as connection,
                connection.transaction(),
                connection.cursor() as cursor,
            ):
                cursor.execute(statement)
        except Exception as exc:
            raise self._clean(
                IncidentExecutionError(f"字段改名失败：{self._redact(str(exc))}")
            ) from None

    def _change_column_type(
        self,
        mutation: ColumnTypeMutation,
        *,
        restore: bool = False,
    ) -> None:
        target_type = mutation.from_type if restore else mutation.to_type
        statement = sql.SQL(
            "ALTER TABLE {}.{} ALTER COLUMN {} TYPE {} USING {}::{}"
        ).format(
            sql.Identifier(self.settings.postgres_schema),
            sql.Identifier(mutation.relation),
            sql.Identifier(mutation.column),
            _TYPE_SQL[target_type],
            sql.Identifier(mutation.column),
            _TYPE_SQL[target_type],
        )
        try:
            with (
                self.db_connect(**self._connection_kwargs()) as connection,
                connection.transaction(),
                connection.cursor() as cursor,
            ):
                cursor.execute(statement)
        except Exception as exc:
            raise self._clean(
                IncidentExecutionError(f"字段类型变化失败：{self._redact(str(exc))}")
            ) from None

    def _add_nullable_column(self, mutation: AddNullableColumnMutation) -> None:
        statement = sql.SQL("ALTER TABLE {}.{} ADD COLUMN {} {} NULL").format(
            sql.Identifier(self.settings.postgres_schema),
            sql.Identifier(mutation.relation),
            sql.Identifier(mutation.column),
            _TYPE_SQL[mutation.data_type],
        )
        try:
            with (
                self.db_connect(**self._connection_kwargs()) as connection,
                connection.transaction(),
                connection.cursor() as cursor,
            ):
                cursor.execute(statement)
        except Exception as exc:
            raise self._clean(
                IncidentExecutionError(f"无法添加 distractor：{self._redact(str(exc))}")
            ) from None

    def _drop_nullable_column(self, mutation: AddNullableColumnMutation) -> None:
        self._drop_dependency(mutation.relation)
        statement = sql.SQL("ALTER TABLE {}.{} DROP COLUMN {}").format(
            sql.Identifier(self.settings.postgres_schema),
            sql.Identifier(mutation.relation),
            sql.Identifier(mutation.column),
        )
        try:
            with (
                self.db_connect(**self._connection_kwargs()) as connection,
                connection.transaction(),
                connection.cursor() as cursor,
            ):
                cursor.execute(statement)
        except Exception as exc:
            raise self._clean(
                IncidentExecutionError(f"无法移除 distractor：{self._redact(str(exc))}")
            ) from None

    def _apply_mutations(self, spec: ScenarioSpec) -> None:
        for mutation in spec.reset_and_injection_contract.mutations:
            if isinstance(mutation, ColumnRenameMutation):
                self._rename_column(mutation)
            elif isinstance(mutation, ColumnTypeMutation):
                self._drop_dependency(mutation.relation)
                self._change_column_type(mutation)
            elif isinstance(mutation, AddNullableColumnMutation):
                self._add_nullable_column(mutation)
            elif isinstance(mutation, SetFieldNullMutation):
                self._write_null_target(
                    mutation,
                    expected_current=mutation.expected_value,
                    replacement=None,
                )
            elif isinstance(mutation, DuplicatePaymentRowsMutation):
                self._insert_payment_duplicates(mutation)
            elif isinstance(mutation, OrphanPaymentRowsMutation):
                self._insert_orphan_payments(mutation)
            elif not isinstance(mutation, NoMutation):
                raise InvalidIncidentState("存在未授权 mutation")

    def _restore_mutations(self, spec: ScenarioSpec) -> None:
        for mutation in reversed(spec.reset_and_injection_contract.mutations):
            if isinstance(mutation, NoMutation):
                continue
            if isinstance(mutation, SetFieldNullMutation):
                current = self._read_null_target(mutation)
                if current == mutation.expected_value:
                    continue
                if current is not None:
                    raise InvalidIncidentState("restore 拒绝未知 NULL mutation 状态")
                self._write_null_target(
                    mutation,
                    expected_current=None,
                    replacement=mutation.expected_value,
                )
                continue
            if isinstance(mutation, DuplicatePaymentRowsMutation):
                state = self._duplicate_payment_state(mutation)
                if state == "HEALTHY":
                    continue
                if state != "INJECTED":
                    raise InvalidIncidentState("restore 拒绝未知 duplicate-payment mutation 状态")
                self._delete_payment_duplicates(mutation)
                continue
            if isinstance(mutation, OrphanPaymentRowsMutation):
                state = self._orphan_payment_state(mutation)
                if state == "HEALTHY":
                    continue
                if state != "INJECTED":
                    raise InvalidIncidentState("restore 拒绝未知 orphan-payment mutation 状态")
                self._delete_orphan_payments(mutation)
                continue
            relation = self._healthy_relation(mutation.relation)
            columns = self._column_map(relation)
            if isinstance(mutation, ColumnRenameMutation):
                is_healthy = (
                    mutation.from_column in columns and mutation.to_column not in columns
                )
                is_injected = (
                    mutation.from_column not in columns and mutation.to_column in columns
                )
                if is_healthy:
                    continue
                if not is_injected:
                    raise InvalidIncidentState("restore 拒绝未知 rename mutation 状态")
                self._rename_column(mutation, restore=True)
            elif isinstance(mutation, ColumnTypeMutation):
                column = columns.get(mutation.column)
                if column is None:
                    raise InvalidIncidentState("restore 要求 type mutation 字段存在")
                if column.data_type == mutation.from_type:
                    continue
                if column.data_type != mutation.to_type:
                    raise InvalidIncidentState("restore 拒绝未知 type mutation 状态")
                self._drop_dependency(mutation.relation)
                self._change_column_type(mutation, restore=True)
            elif isinstance(mutation, AddNullableColumnMutation):
                column = columns.get(mutation.column)
                if column is None:
                    continue
                if column.data_type != mutation.data_type or not column.nullable:
                    raise InvalidIncidentState("restore 拒绝未知 distractor mutation 状态")
                self._drop_nullable_column(mutation)
            else:
                raise InvalidIncidentState("存在未授权 mutation")

    def _verify_restored(self, spec: ScenarioSpec) -> None:
        for mutation in spec.reset_and_injection_contract.mutations:
            if isinstance(mutation, NoMutation):
                continue
            if isinstance(mutation, SetFieldNullMutation):
                if (
                    self._read_null_target(mutation) != mutation.expected_value
                    or self._null_count(mutation) != 0
                ):
                    raise InvalidIncidentState("restore 后 NULL mutation 仍然存在")
                continue
            if isinstance(mutation, DuplicatePaymentRowsMutation):
                if self._duplicate_payment_state(mutation) != "HEALTHY":
                    raise InvalidIncidentState("restore 后 duplicate-payment mutation 仍然存在")
                continue
            if isinstance(mutation, OrphanPaymentRowsMutation):
                if self._orphan_payment_state(mutation) != "HEALTHY":
                    raise InvalidIncidentState("restore 后 orphan-payment mutation 仍然存在")
                continue
            relation = self._healthy_relation(mutation.relation)
            columns = self._column_map(relation)
            if isinstance(mutation, ColumnRenameMutation):
                if mutation.from_column not in columns or mutation.to_column in columns:
                    raise InvalidIncidentState("restore 后 rename mutation 仍然存在")
            elif isinstance(mutation, ColumnTypeMutation):
                column = columns.get(mutation.column)
                if column is None or column.data_type != mutation.from_type:
                    raise InvalidIncidentState("restore 后字段类型仍然漂移")
            elif isinstance(mutation, AddNullableColumnMutation) and mutation.column in columns:
                raise InvalidIncidentState("restore 后 distractor 仍然存在")

    def reset(self, case_id: str) -> ResetResult:
        spec = self._load_case(case_id)
        self._clear_active_run()
        self._start_postgres()
        summary = self._build_healthy_baseline()
        self._verify_restored(spec)
        return ResetResult(case_id, "HEALTHY", summary.fingerprint)

    def restore(self, case_id: str) -> ResetResult:
        spec = self._load_case(case_id)
        self._restore_mutations(spec)
        result = self.reset(case_id)
        self._verify_restored(spec)
        return result

    def prepare(self, case_id: str) -> PreparationResult:
        spec = self._load_case(case_id)
        self._start_postgres()
        self._clear_active_run()
        self._ensure_healthy_for_prepare(spec)
        no_mutation = all(
            isinstance(mutation, NoMutation)
            for mutation in spec.reset_and_injection_contract.mutations
        )
        if no_mutation:
            relations = self._inspect_relations(tuple(EXPECTED_RELATION_COUNTS))
            return PreparationResult(
                case_id,
                "HEALTHY",
                self._fingerprint(relations, self.settings.postgres_schema),
            )
        self._apply_mutations(spec)
        targets = tuple(
            dict.fromkeys(
                mutation.relation
                for mutation in spec.reset_and_injection_contract.mutations
                if hasattr(mutation, "relation")
            )
        )
        relations = self._inspect_relations(targets)
        return PreparationResult(
            case_id,
            "INJECTED",
            self._fingerprint(relations, self.settings.postgres_schema),
        )

    def _public_schema(self, spec: ScenarioSpec) -> BaselineSummary:
        relations = self._inspect_relations(spec.observable_evidence_contract.schema_relations)
        return make_baseline_summary(self.settings.postgres_schema, relations)

    def _public_profile(self, spec: ScenarioSpec, run_root: Path) -> str:
        try:
            profile_spec = load_profile_spec(self.project_root)
            relation_names = tuple(
                dict.fromkeys(
                    (
                        *spec.observable_evidence_contract.profile_relations,
                        *spec.observable_evidence_contract.history_relations,
                    )
                )
            )
            reader = AggregateSnapshotReader(
                schema_name=self.settings.postgres_schema,
                spec=profile_spec,
                db_connect=self.db_connect,
                connection_kwargs=self._connection_kwargs(),
            )
            snapshot = reader.read_snapshot(relation_names)
            snapshot = ProfileSnapshot.create(
                spec=profile_spec,
                current=snapshot.current,
                history=tuple(
                    item
                    for item in snapshot.history
                    if item.relation_name in spec.observable_evidence_contract.history_relations
                ),
            )
            write_profile_snapshot(run_root / "profile_snapshot.json", snapshot)
            return profile_spec.digest()
        except ProfileError as exc:
            raise self._clean(IncidentExecutionError(str(exc))) from None

    def _write_runtime(
        self,
        run_root: Path,
        spec: ScenarioSpec,
        run_id: str,
        dbt_exit_code: int,
        profile_spec_sha256: str,
    ) -> None:
        runtime = {
            "schema_version": "p1.runtime.v1",
            "run_id": run_id,
            "dbt_exit_code": dbt_exit_code,
            "artifacts": _EXPECTED_ARTIFACTS,
            "observable_relations": {
                "schema": list(spec.observable_evidence_contract.schema_relations),
                "profile": list(spec.observable_evidence_contract.profile_relations),
                "history": list(spec.observable_evidence_contract.history_relations),
            },
            "profile_spec_sha256": profile_spec_sha256,
        }
        self._write_text(
            run_root / "runtime.json",
            json.dumps(runtime, indent=2, sort_keys=True) + "\n",
        )
        self._write_text(
            run_root / "incident_brief.json",
            json.dumps(
                spec.incident_brief.model_dump(mode="json"),
                indent=2,
                sort_keys=True,
            )
            + "\n",
        )

    def _write_private_scenario(self, run_id: str, spec: ScenarioSpec) -> None:
        payload = {
            "schema_version": "scenario_snapshot.v1",
            "scenario_spec_sha256": spec.digest(),
            "scenario": spec.model_dump(mode="json"),
        }
        self._write_text(
            self.project_root
            / ".dig"
            / "lab"
            / "private"
            / run_id
            / "scenario_snapshot.json",
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
        )

    def _publish_active_run(self, run_id: str) -> None:
        try:
            publish_active_run(self.project_root, run_id=run_id)
        except RunContextError as exc:
            raise self._clean(
                IncidentExecutionError(self._redact(str(exc)))
            ) from None

    def build(self, case_id: str) -> ScenarioRun:
        spec = self._load_case(case_id)
        self._start_postgres()
        self._clear_active_run()
        no_mutation = all(
            isinstance(mutation, NoMutation)
            for mutation in spec.reset_and_injection_contract.mutations
        )
        if no_mutation:
            self._ensure_healthy_for_prepare(spec)
        else:
            self._validate_prepared_state(spec)

        run_id = self.run_id_factory()
        if not isinstance(run_id, str) or RUN_ID_PATTERN.fullmatch(run_id) is None:
            raise self._clean(IncidentExecutionError("run_id 生成器返回非法值"))
        run_root = self.project_root / ".dig" / "lab" / "runs" / run_id
        private_root = self.project_root / ".dig" / "lab" / "private" / run_id
        try:
            run_root.mkdir(parents=True, exist_ok=False)
            private_root.mkdir(parents=True, exist_ok=False)
        except OSError as exc:
            raise self._clean(
                IncidentExecutionError(f"无法创建运行目录：{self._redact(str(exc))}")
            ) from None

        self._write_private_scenario(run_id, spec)
        target = run_root / "dbt" / "target"
        logs = run_root / "dbt" / "logs"
        try:
            dbt_result = self.dbt_runner.run_scenario(target, logs)
        except DbtExecutionError as exc:
            raise self._clean(IncidentExecutionError(self._redact(str(exc)))) from None
        self._write_text(run_root / "dbt/stdout.log", self._redact(dbt_result.stdout))
        self._write_text(run_root / "dbt/stderr.log", self._redact(dbt_result.stderr))
        self._redact_file(logs / "dbt.log")

        schema = self._public_schema(spec)
        self._write_text(run_root / "schema.json", schema.to_json())
        profile_hash = self._public_profile(spec, run_root)
        self._write_runtime(run_root, spec, run_id, dbt_result.return_code, profile_hash)
        for artifact in (
            run_root / "dbt/target/manifest.json",
            run_root / "dbt/target/run_results.json",
            run_root / "dbt/logs/dbt.log",
            run_root / "schema.json",
            run_root / "profile_snapshot.json",
            run_root / "runtime.json",
            run_root / "incident_brief.json",
            run_root / "dbt/stdout.log",
            run_root / "dbt/stderr.log",
        ):
            self._redact_file(artifact)

        try:
            verification = self.verifier.verify(run_id)
        except LabVerificationError as exc:
            raise self._clean(FaultVerificationError(self._redact(str(exc)))) from None
        self._publish_active_run(run_id)
        return ScenarioRun(
            run_id=run_id,
            artifact_dir=run_root,
            verification_status=ScenarioVerificationStatus(verification.status),
            dbt_exit_code=dbt_result.return_code,
        )

    def _validate_prepared_state(self, spec: ScenarioSpec) -> None:
        for mutation in spec.reset_and_injection_contract.mutations:
            if isinstance(mutation, SetFieldNullMutation):
                if (
                    self._read_null_target(mutation) is not None
                    or self._null_count(mutation) != 1
                ):
                    raise InvalidIncidentState("build 要求已完成 NULL mutation")
                continue
            if isinstance(mutation, DuplicatePaymentRowsMutation):
                if self._duplicate_payment_state(mutation) != "INJECTED":
                    raise InvalidIncidentState("build 要求已完成 duplicate-payment mutation")
                continue
            if isinstance(mutation, OrphanPaymentRowsMutation):
                if self._orphan_payment_state(mutation) != "INJECTED":
                    raise InvalidIncidentState("build 要求已完成 orphan-payment mutation")
                continue
            relation = self._healthy_relation(mutation.relation)
            columns = self._column_map(relation)
            if isinstance(mutation, ColumnRenameMutation):
                if mutation.from_column in columns or mutation.to_column not in columns:
                    raise InvalidIncidentState("build 要求已完成 rename mutation")
            elif isinstance(mutation, ColumnTypeMutation):
                column = columns.get(mutation.column)
                if column is None or column.data_type != mutation.to_type:
                    raise InvalidIncidentState("build 要求已完成 type mutation")
            elif isinstance(mutation, AddNullableColumnMutation):
                column = columns.get(mutation.column)
                if column is None or column.data_type != mutation.data_type or not column.nullable:
                    raise InvalidIncidentState("build 要求已完成 distractor mutation")
