from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import psycopg
from psycopg import sql

from data_incident_gym.baseline import (
    CATALOG_COLUMNS_QUERY,
    BaselineBuilder,
    BaselineSummary,
    ColumnSummary,
    RelationSummary,
    make_baseline_summary,
)
from data_incident_gym.config import PROJECT_ROOT, Settings
from data_incident_gym.incidents import GroundTruth, IncidentCaseError, load_ground_truth

DatabaseConnect = Callable[..., Any]
CaseState = Literal["MISSING", "HEALTHY", "INJECTED", "DRIFTED"]

_ALLOWED_RENAMES = {
    ("raw_payments", "amount", "total_amount"),
    ("raw_payments", "total_amount", "amount"),
}


class LabError(RuntimeError):
    code = "LAB_ERROR"


class InvalidIncidentState(LabError):
    code = "INVALID_INCIDENT_STATE"


class IncidentExecutionError(LabError):
    code = "INCIDENT_EXECUTION_ERROR"


@dataclass(frozen=True)
class ResetResult:
    case_id: str
    state: Literal["HEALTHY"]
    fingerprint: str


@dataclass(frozen=True)
class InjectionResult:
    case_id: str
    state: Literal["INJECTED"]
    fingerprint: str


class IncidentLab:
    def __init__(
        self,
        settings: Settings,
        project_root: Path = PROJECT_ROOT,
        *,
        baseline_builder: BaselineBuilder | None = None,
        db_connect: DatabaseConnect | None = None,
    ) -> None:
        self.settings = settings
        self.project_root = project_root
        self.db_connect = db_connect or psycopg.connect
        self.baseline_builder = baseline_builder or BaselineBuilder(
            settings,
            project_root,
            db_connect=self.db_connect,
        )

    def _connection_kwargs(self) -> dict[str, object]:
        return {
            "host": self.settings.postgres_host,
            "port": self.settings.postgres_port,
            "dbname": self.settings.postgres_database,
            "user": self.settings.postgres_user,
            "password": self.settings.postgres_password.get_secret_value(),
        }

    def _redact(self, value: str) -> str:
        secret = self.settings.postgres_password.get_secret_value()
        return value.replace(secret, "***") if secret else value

    @staticmethod
    def _clean(error: LabError | IncidentCaseError) -> LabError | IncidentCaseError:
        error.__cause__ = None
        error.__context__ = None
        return error

    def _load_case(self, case_id: str) -> GroundTruth:
        error: IncidentCaseError | None = None
        try:
            return load_ground_truth(case_id, self.project_root)
        except IncidentCaseError as exc:
            error = IncidentCaseError(self._redact(str(exc)))
        assert error is not None
        raise self._clean(error)

    def _inspect_relation(self, truth: GroundTruth) -> RelationSummary | None:
        relation_name = truth.expected_schema.relation
        relation: RelationSummary | None = None
        error: LabError | None = None
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
                    error = IncidentExecutionError(f"无法读取行数：{relation_name}")
                else:
                    relation = RelationSummary(
                        relation_name,
                        count_row[0],
                        columns,
                    )
        except Exception as exc:
            if isinstance(exc, LabError):
                error = type(exc)(self._redact(str(exc)))
            else:
                error = IncidentExecutionError(
                    f"读取故障 Schema 失败：{self._redact(str(exc))}"
                )
        if error is not None:
            raise self._clean(error)
        return relation

    @staticmethod
    def _classify_state(
        relation: RelationSummary | None,
        truth: GroundTruth,
    ) -> CaseState:
        if relation is None:
            return "MISSING"
        columns = tuple(column.name for column in relation.columns)
        if (
            columns == truth.expected_schema.healthy_columns
            and relation.row_count == truth.expected_schema.row_count
        ):
            return "HEALTHY"
        if (
            columns == truth.expected_schema.fault_columns
            and relation.row_count == truth.expected_schema.row_count
        ):
            return "INJECTED"
        return "DRIFTED"

    def _rename_column(self, relation: str, source: str, target: str) -> None:
        if (relation, source, target) not in _ALLOWED_RENAMES:
            raise InvalidIncidentState("拒绝执行未授权的故障字段改名")
        statement = sql.SQL(
            "ALTER TABLE {}.{} RENAME COLUMN {} TO {}"
        ).format(
            sql.Identifier(self.settings.postgres_schema),
            sql.Identifier(relation),
            sql.Identifier(source),
            sql.Identifier(target),
        )
        error: IncidentExecutionError | None = None
        try:
            with (
                self.db_connect(**self._connection_kwargs()) as connection,
                connection.transaction(),
                connection.cursor() as cursor,
            ):
                cursor.execute(statement)
        except Exception as exc:
            error = IncidentExecutionError(
                f"故障字段改名失败：{self._redact(str(exc))}"
            )
        if error is not None:
            raise self._clean(error)

    def _fingerprint(self, relation: RelationSummary) -> str:
        return make_baseline_summary(
            self.settings.postgres_schema,
            (relation,),
        ).fingerprint

    def _start_postgres(self) -> None:
        error: IncidentExecutionError | None = None
        try:
            self.baseline_builder.start_postgres()
        except Exception as exc:
            error = IncidentExecutionError(
                f"启动 PostgreSQL 失败：{self._redact(str(exc))}"
            )
        if error is not None:
            raise self._clean(error)

    def _build_healthy_baseline(self) -> BaselineSummary:
        summary: BaselineSummary | None = None
        error: IncidentExecutionError | None = None
        try:
            summary = self.baseline_builder.build()
        except Exception as exc:
            error = IncidentExecutionError(
                f"构建健康基线失败：{self._redact(str(exc))}"
            )
        if error is not None:
            raise self._clean(error)
        assert summary is not None
        return summary

    def reset(self, case_id: str) -> ResetResult:
        truth = self._load_case(case_id)
        self._start_postgres()
        current = self._inspect_relation(truth)
        state = self._classify_state(current, truth)
        if state == "INJECTED":
            self._rename_column(
                truth.injection.relation,
                truth.injection.to_column,
                truth.injection.from_column,
            )
        elif state not in {"MISSING", "HEALTHY"}:
            raise InvalidIncidentState(
                f"无法从未知 Schema 状态重置案例：{case_id}"
            )

        summary = self._build_healthy_baseline()
        relation = next(
            (
                item
                for item in summary.relations
                if item.name == truth.expected_schema.relation
            ),
            None,
        )
        if self._classify_state(relation, truth) != "HEALTHY":
            raise InvalidIncidentState("重置后未恢复健康 Schema")
        return ResetResult(case_id, "HEALTHY", summary.fingerprint)

    def inject(self, case_id: str) -> InjectionResult:
        truth = self._load_case(case_id)
        self._start_postgres()
        before = self._inspect_relation(truth)
        state = self._classify_state(before, truth)
        if state != "HEALTHY":
            raise InvalidIncidentState(
                f"故障注入要求健康状态，当前状态：{state}"
            )
        self._rename_column(
            truth.injection.relation,
            truth.injection.from_column,
            truth.injection.to_column,
        )
        after = self._inspect_relation(truth)
        if self._classify_state(after, truth) != "INJECTED" or after is None:
            raise InvalidIncidentState("故障注入后 Schema 不符合预期")
        return InjectionResult(case_id, "INJECTED", self._fingerprint(after))
