from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

import psycopg
from psycopg import sql
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    StrictStr,
    model_validator,
)

from data_incident_gym.config import PROJECT_ROOT, Settings

DatabaseConnect = Callable[..., Any]

PROFILE_SPEC_PATH = Path("config/profiles/jaffle_shop.v1.json")
PROFILE_SNAPSHOT_SCHEMA_VERSION = "profile_snapshot.v1"
MAX_GROUP_ROWS = 128
MAX_HISTORY_POINTS = 90
_IDENTIFIER_PATTERN = r"^[a-z_][a-z0-9_]*$"


class ProfileError(ValueError):
    """Raised when a ProfileSpec or aggregate snapshot is invalid."""


def parse_watermark_value(value: str) -> datetime:
    """Parse a date or ISO watermark as an aware UTC datetime."""

    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ProfileError("watermark must be a date or ISO datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


class SnapshotKind(StrEnum):
    CURRENT = "current"
    HISTORY = "history"


class HistoryMetric(StrEnum):
    COUNT = "count"
    RATIO = "ratio"


class Periodicity(StrEnum):
    DAY = "DAY"
    DAY_OF_WEEK = "DAY_OF_WEEK"


class ProfileColumnSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: StrictStr = Field(pattern=_IDENTIFIER_PATTERN)


class BusinessKeySpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: StrictStr = Field(pattern=_IDENTIFIER_PATTERN)
    columns: tuple[StrictStr, ...] = Field(min_length=1)


class BusinessFingerprintSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: StrictStr = Field(pattern=_IDENTIFIER_PATTERN)
    columns: tuple[StrictStr, ...] = Field(min_length=1)


class RelationshipSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: StrictStr = Field(pattern=_IDENTIFIER_PATTERN)
    local_columns: tuple[StrictStr, ...] = Field(min_length=1)
    referenced_relation: StrictStr = Field(pattern=_IDENTIFIER_PATTERN)
    referenced_columns: tuple[StrictStr, ...] = Field(min_length=1)


class GroupSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: StrictStr = Field(pattern=_IDENTIFIER_PATTERN)
    columns: tuple[StrictStr, ...] = Field(min_length=1)
    max_rows: StrictInt = Field(default=MAX_GROUP_ROWS, ge=1, le=MAX_GROUP_ROWS)


class JoinPathSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    relation: StrictStr = Field(pattern=_IDENTIFIER_PATTERN)
    local_columns: tuple[StrictStr, ...] = Field(min_length=1)
    referenced_relation: StrictStr = Field(pattern=_IDENTIFIER_PATTERN)
    referenced_columns: tuple[StrictStr, ...] = Field(min_length=1)


class HistorySpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: StrictStr = Field(pattern=_IDENTIFIER_PATTERN)
    metric: HistoryMetric
    time_column: StrictStr = Field(pattern=_IDENTIFIER_PATTERN)
    periodicity: Periodicity
    max_points: StrictInt = Field(default=MAX_HISTORY_POINTS, ge=1, le=MAX_HISTORY_POINTS)
    join_path: JoinPathSpec | None = None
    watermark_column: StrictStr | None = Field(default=None, pattern=_IDENTIFIER_PATTERN)
    sla_seconds: StrictInt | None = Field(default=None, ge=0)


class RelationProfileSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    relation_name: StrictStr = Field(pattern=_IDENTIFIER_PATTERN)
    columns: tuple[ProfileColumnSpec, ...] = Field(min_length=1)
    business_keys: tuple[BusinessKeySpec, ...] = ()
    business_fingerprints: tuple[BusinessFingerprintSpec, ...] = ()
    relationships: tuple[RelationshipSpec, ...] = ()
    groups: tuple[GroupSpec, ...] = ()
    histories: tuple[HistorySpec, ...] = ()

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(column.name for column in self.columns)


class ProfileSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["profile_spec.v1"]
    max_group_rows: StrictInt = Field(default=MAX_GROUP_ROWS, ge=1, le=MAX_GROUP_ROWS)
    max_history_points: StrictInt = Field(default=MAX_HISTORY_POINTS, ge=1, le=MAX_HISTORY_POINTS)
    relations: tuple[RelationProfileSpec, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_model(self) -> ProfileSpec:
        return self.validate_contract()

    def canonical_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def canonical_json(self) -> str:
        return json.dumps(
            self.canonical_payload(),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )

    def digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    def relation(self, relation_name: str) -> RelationProfileSpec:
        for relation in self.relations:
            if relation.relation_name == relation_name:
                return relation
        raise ProfileError(f"未声明关系：{relation_name}")

    @staticmethod
    def _unique(values: Sequence[str], label: str) -> None:
        if len(set(values)) != len(values):
            raise ProfileError(f"{label} 不能重复")

    @staticmethod
    def _validate_columns(
        columns: Sequence[str],
        declared: set[str],
        label: str,
    ) -> None:
        ProfileSpec._unique(columns, label)
        unknown = set(columns) - declared
        if unknown:
            raise ProfileError(f"{label} 引用了未声明列：{sorted(unknown)}")

    def validate_contract(self) -> ProfileSpec:
        relation_names = tuple(relation.relation_name for relation in self.relations)
        self._unique(relation_names, "关系名")
        relations = set(relation_names)
        if self.max_group_rows > MAX_GROUP_ROWS or self.max_history_points > MAX_HISTORY_POINTS:
            raise ProfileError("ProfileSpec 超过全局输出上限")

        for relation in self.relations:
            columns = set(relation.column_names)
            self._unique(relation.column_names, f"{relation.relation_name} 列名")
            self._unique(
                tuple(item.name for item in relation.business_keys),
                f"{relation.relation_name} business key 名称",
            )
            self._unique(
                tuple(item.name for item in relation.business_fingerprints),
                f"{relation.relation_name} fingerprint 名称",
            )
            self._unique(
                tuple(item.name for item in relation.relationships),
                f"{relation.relation_name} relationship 名称",
            )
            self._unique(
                tuple(item.name for item in relation.groups),
                f"{relation.relation_name} group 名称",
            )
            self._unique(
                tuple(item.name for item in relation.histories),
                f"{relation.relation_name} history 名称",
            )
            for item in relation.business_keys:
                self._validate_columns(
                    item.columns,
                    columns,
                    f"{relation.relation_name}.{item.name}",
                )
            for item in relation.business_fingerprints:
                self._validate_columns(
                    item.columns,
                    columns,
                    f"{relation.relation_name}.{item.name}",
                )
            for item in relation.relationships:
                self._validate_columns(
                    item.local_columns,
                    columns,
                    f"{relation.relation_name}.{item.name}.local_columns",
                )
                if item.referenced_relation not in relations:
                    raise ProfileError(f"relationship 引用了未声明关系：{item.referenced_relation}")
                referenced = self.relation(item.referenced_relation)
                self._validate_columns(
                    item.referenced_columns,
                    set(referenced.column_names),
                    f"{relation.relation_name}.{item.name}.referenced_columns",
                )
                if len(item.local_columns) != len(item.referenced_columns):
                    raise ProfileError(f"{relation.relation_name}.{item.name} join key 数量不一致")
            for item in relation.groups:
                self._validate_columns(
                    item.columns,
                    columns,
                    f"{relation.relation_name}.{item.name}",
                )
                if item.max_rows > self.max_group_rows:
                    raise ProfileError(
                        f"{relation.relation_name}.{item.name} 超过 spec group 上限"
                    )
            for item in relation.histories:
                history_columns = columns
                if item.join_path is not None:
                    if item.join_path.referenced_relation not in relations:
                        raise ProfileError(
                            f"history 引用了未声明关系：{item.join_path.referenced_relation}"
                        )
                    history_columns = set(
                        self.relation(item.join_path.referenced_relation).column_names
                    )
                self._validate_columns(
                    (item.time_column,),
                    history_columns,
                    f"{relation.relation_name}.{item.name}.time_column",
                )
                if item.watermark_column is not None:
                    self._validate_columns(
                        (item.watermark_column,),
                        columns,
                        f"{relation.relation_name}.{item.name}.watermark_column",
                    )
                if item.max_points > self.max_history_points:
                    raise ProfileError(
                        f"{relation.relation_name}.{item.name} 超过 spec history 上限"
                    )
                if item.join_path is not None:
                    join = item.join_path
                    if join.relation != relation.relation_name:
                        raise ProfileError(
                            f"{relation.relation_name}.{item.name} join path 起点不匹配"
                        )
                    self._validate_columns(
                        join.local_columns,
                        columns,
                        f"{relation.relation_name}.{item.name}.join_path.local_columns",
                    )
                    referenced = self.relation(join.referenced_relation)
                    self._validate_columns(
                        join.referenced_columns,
                        set(referenced.column_names),
                        f"{relation.relation_name}.{item.name}.join_path.referenced_columns",
                    )
                    if len(join.local_columns) != len(join.referenced_columns):
                        raise ProfileError(
                            f"{relation.relation_name}.{item.name} join key 数量不一致"
                        )
        return self


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProfileError(f"ProfileSpec 包含重复键：{key}")
        result[key] = value
    return result


def parse_profile_spec(payload: str, source: str = "ProfileSpec") -> ProfileSpec:
    try:
        parsed = json.loads(payload, object_pairs_hook=_reject_duplicate_keys)
        spec = ProfileSpec.model_validate(parsed)
        return spec.validate_contract()
    except (json.JSONDecodeError, ProfileError, ValueError, TypeError) as exc:
        message = str(exc)
        raise ProfileError(f"{source} 无效：{message}") from None


def load_profile_spec(project_root: Path = PROJECT_ROOT) -> ProfileSpec:
    path = project_root / PROFILE_SPEC_PATH
    try:
        return parse_profile_spec(path.read_text(encoding="utf-8"), str(path))
    except OSError as exc:
        raise ProfileError(f"无法读取 ProfileSpec：{path}") from exc


class ColumnProfileFact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    column_name: StrictStr
    null_count: StrictInt = Field(ge=0)
    distinct_count: StrictInt = Field(ge=0)


class DuplicateProfileFact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: StrictStr
    duplicate_count: StrictInt = Field(ge=0)


class RelationshipViolationFact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: StrictStr
    violation_count: StrictInt = Field(ge=0)


class GroupProfileFact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: StrictStr
    columns: tuple[StrictStr, ...]
    values: tuple[tuple[StrictStr | None, ...], ...]
    counts: tuple[StrictInt, ...] = Field(min_length=0)

    @model_validator(mode="after")
    def validate_shape(self) -> GroupProfileFact:
        if len(self.values) != len(self.counts):
            raise ValueError("group values and counts must have the same length")
        if any(len(row) != len(self.columns) for row in self.values):
            raise ValueError("group values must match declared columns")
        if any(count < 0 for count in self.counts):
            raise ValueError("group counts must not be negative")
        return self

    @classmethod
    def create(
        cls,
        *,
        name: str,
        columns: Sequence[str],
        rows: Sequence[tuple[Any, ...]],
    ) -> GroupProfileFact:
        values: list[tuple[str | None, ...]] = []
        counts: list[int] = []
        for row in rows:
            if len(row) != len(columns) + 1:
                raise ProfileError(f"group {name} 返回列数不匹配")
            values.append(tuple(None if value is None else str(value) for value in row[:-1]))
            counts.append(int(row[-1]))
        return cls(name=name, columns=tuple(columns), values=tuple(values), counts=tuple(counts))


class RelationProfileSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    relation_name: StrictStr = Field(pattern=_IDENTIFIER_PATTERN)
    row_count: StrictInt = Field(ge=0)
    columns: tuple[ColumnProfileFact, ...]
    business_key_duplicates: tuple[DuplicateProfileFact, ...] = ()
    business_fingerprint_duplicates: tuple[DuplicateProfileFact, ...] = ()
    relationship_violations: tuple[RelationshipViolationFact, ...] = ()
    groups: tuple[GroupProfileFact, ...] = ()


class HistoryPoint(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    bucket: StrictStr
    periodic_key: StrictStr
    value: StrictInt | StrictFloat


class HistorySeries(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: StrictStr
    metric: HistoryMetric
    points: tuple[HistoryPoint, ...]
    watermark_column: StrictStr | None = None
    watermark_value: StrictStr | None = None
    sla_seconds: StrictInt | None = Field(default=None, ge=0)


class RelationHistorySnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    relation_name: StrictStr = Field(pattern=_IDENTIFIER_PATTERN)
    histories: tuple[HistorySeries, ...]


class ProfileSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["profile_snapshot.v1"]
    profile_spec_version: Literal["profile_spec.v1"]
    profile_spec_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    current: tuple[RelationProfileSnapshot, ...]
    history: tuple[RelationHistorySnapshot, ...]

    @model_validator(mode="after")
    def validate_shape(self) -> ProfileSnapshot:
        current_names = tuple(item.relation_name for item in self.current)
        history_names = tuple(item.relation_name for item in self.history)
        for names, label in (
            (current_names, "current relation names"),
            (history_names, "history relation names"),
        ):
            if len(set(names)) != len(names):
                raise ValueError(f"{label} must be unique")
            if names != tuple(sorted(names)):
                raise ValueError(f"{label} must be sorted")
        return self

    @classmethod
    def create(
        cls,
        *,
        spec: ProfileSpec,
        current: Sequence[RelationProfileSnapshot],
        history: Sequence[RelationHistorySnapshot],
    ) -> ProfileSnapshot:
        return cls(
            schema_version=PROFILE_SNAPSHOT_SCHEMA_VERSION,
            profile_spec_version=spec.schema_version,
            profile_spec_sha256=spec.digest(),
            current=tuple(sorted(current, key=lambda item: item.relation_name)),
            history=tuple(sorted(history, key=lambda item: item.relation_name)),
        )

    def canonical_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
        ) + "\n"


def write_profile_snapshot(path: Path, snapshot: ProfileSnapshot) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f"{path.name}.", suffix=".tmp", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(snapshot.canonical_json())
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    except OSError as exc:
        temporary_path.unlink(missing_ok=True)
        raise ProfileError(f"无法写入 profile snapshot：{path}") from exc


def load_profile_snapshot(path: Path) -> ProfileSnapshot:
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
        return ProfileSnapshot.model_validate(payload)
    except (OSError, json.JSONDecodeError, ProfileError, ValueError, TypeError) as exc:
        raise ProfileError(f"profile snapshot 无效：{path}") from exc


class AggregateSnapshotReader:
    def __init__(
        self,
        *,
        schema_name: str,
        spec: ProfileSpec,
        db_connect: DatabaseConnect = psycopg.connect,
        connection_kwargs: dict[str, object],
        read_only: bool = False,
    ) -> None:
        self._schema_name = schema_name
        self._spec = spec
        self._db_connect = db_connect
        self._connection_kwargs = dict(connection_kwargs)
        self._read_only = read_only

    def _relation(self, relation_name: str) -> RelationProfileSpec:
        if not isinstance(relation_name, str) or not re.fullmatch(
            _IDENTIFIER_PATTERN, relation_name
        ):
            raise ProfileError("关系名必须是未限定的安全标识符")
        return self._spec.relation(relation_name)

    @staticmethod
    def _identifier_list(columns: Sequence[str]) -> sql.Composed:
        return sql.SQL(", ").join(sql.Identifier(column) for column in columns)

    def _qualified(self, relation_name: str) -> sql.Composed:
        return sql.SQL("{}.{}").format(
            sql.Identifier(self._schema_name),
            sql.Identifier(relation_name),
        )

    def _query_scalar(self, cursor: Any, query: sql.Composed, params: Sequence[Any] = ()) -> Any:
        cursor.execute(query, tuple(params) if params else None)
        row = cursor.fetchone()
        if row is None:
            raise ProfileError("聚合查询没有返回结果")
        return row[0]

    def _read_current(self, cursor: Any, relation: RelationProfileSpec) -> RelationProfileSnapshot:
        table = self._qualified(relation.relation_name)
        row_count = int(
            self._query_scalar(cursor, sql.SQL("SELECT count(*) FROM {}").format(table))
        )
        columns: list[ColumnProfileFact] = []
        for column in relation.columns:
            identifier = sql.Identifier(column.name)
            query = sql.SQL(
                "SELECT count(*) - count({0}), count(DISTINCT {0}) FROM {1}"
            ).format(identifier, table)
            cursor.execute(query)
            row = cursor.fetchone()
            if row is None:
                raise ProfileError(f"列聚合没有返回结果：{relation.relation_name}.{column.name}")
            columns.append(
                ColumnProfileFact(
                    column_name=column.name,
                    null_count=int(row[0]),
                    distinct_count=int(row[1]),
                )
            )

        def duplicate_fact(name: str, selected_columns: Sequence[str]) -> DuplicateProfileFact:
            selected = self._identifier_list(selected_columns)
            query = sql.SQL(
                "SELECT COALESCE(sum(group_count - 1) FILTER (WHERE group_count > 1), 0) "
                "FROM (SELECT {0}, count(*) AS group_count FROM {1} GROUP BY {0}) grouped"
            ).format(selected, table)
            return DuplicateProfileFact(
                name=name,
                duplicate_count=int(self._query_scalar(cursor, query)),
            )

        key_duplicates = tuple(
            duplicate_fact(item.name, item.columns) for item in relation.business_keys
        )
        fingerprint_duplicates = tuple(
            duplicate_fact(item.name, item.columns) for item in relation.business_fingerprints
        )

        relationship_facts: list[RelationshipViolationFact] = []
        for item in relation.relationships:
            referenced = self._qualified(item.referenced_relation)
            left = sql.SQL(" AND ").join(
                sql.SQL("local_table.{}::text = remote_table.{}::text").format(
                    sql.Identifier(local), sql.Identifier(remote)
                )
                for local, remote in zip(
                    item.local_columns, item.referenced_columns, strict=True
                )
            )
            query = sql.SQL(
                "SELECT count(*) FROM {0} AS local_table "
                "WHERE NOT EXISTS (SELECT 1 FROM {1} AS remote_table WHERE {2})"
            ).format(table, referenced, left)
            relationship_facts.append(
                RelationshipViolationFact(
                    name=item.name,
                    violation_count=int(self._query_scalar(cursor, query)),
                )
            )

        groups: list[GroupProfileFact] = []
        for item in relation.groups:
            selected = self._identifier_list(item.columns)
            query = sql.SQL(
                "SELECT {0}, count(*) FROM {1} GROUP BY {0} "
                "ORDER BY {0} LIMIT %s"
            ).format(selected, table)
            cursor.execute(query, (item.max_rows,))
            groups.append(
                GroupProfileFact.create(
                    name=item.name,
                    columns=item.columns,
                    rows=cursor.fetchall(),
                )
            )

        return RelationProfileSnapshot(
            relation_name=relation.relation_name,
            row_count=row_count,
            columns=tuple(columns),
            business_key_duplicates=key_duplicates,
            business_fingerprint_duplicates=fingerprint_duplicates,
            relationship_violations=tuple(relationship_facts),
            groups=tuple(groups),
        )

    @staticmethod
    def _periodic_key(value: Any, periodicity: Periodicity) -> str:
        if isinstance(value, datetime):
            value = value.date()
        if isinstance(value, date):
            if periodicity is Periodicity.DAY_OF_WEEK:
                return str(value.isoweekday())
            return value.isoformat()
        return str(value)

    @staticmethod
    def _bucket(value: Any) -> str:
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, date):
            return value.isoformat()
        return str(value)

    def _history_query(self, relation: RelationProfileSpec, item: HistorySpec) -> sql.Composed:
        table = self._qualified(relation.relation_name)
        time_expression = sql.SQL("local_table.{} ").format(sql.Identifier(item.time_column))
        from_expression = sql.SQL("{} AS local_table").format(table)
        if item.join_path is not None:
            join = item.join_path
            remote = self._qualified(join.referenced_relation)
            condition = sql.SQL(" AND ").join(
                sql.SQL("local_table.{} = remote_table.{}").format(
                    sql.Identifier(local), sql.Identifier(remote_column)
                )
                for local, remote_column in zip(
                    join.local_columns, join.referenced_columns, strict=True
                )
            )
            from_expression = sql.SQL("{} JOIN {} AS remote_table ON {} ").format(
                from_expression, remote, condition
            )
            time_expression = sql.SQL("remote_table.{} ").format(sql.Identifier(item.time_column))
        if item.metric is HistoryMetric.COUNT:
            value_expression = sql.SQL("count(*)")
        else:
            value_expression = sql.SQL(
                "count(*)::double precision / NULLIF(sum(count(*)) OVER (), 0)"
            )
        return sql.SQL(
            "SELECT {0}, {1} FROM {2} GROUP BY {0} ORDER BY {0} DESC LIMIT %s"
        ).format(time_expression, value_expression, from_expression)

    def _watermark_query(self, relation: RelationProfileSpec, item: HistorySpec) -> sql.Composed:
        if item.watermark_column is None:
            raise ProfileError(f"history {item.name} 未声明 watermark")
        return sql.SQL("SELECT MAX({0}) FROM {1}").format(
            sql.Identifier(item.watermark_column),
            self._qualified(relation.relation_name),
        )

    def _read_history(self, cursor: Any, relation: RelationProfileSpec) -> RelationHistorySnapshot:
        histories: list[HistorySeries] = []
        for item in relation.histories:
            cursor.execute(self._history_query(relation, item), (item.max_points,))
            points: list[HistoryPoint] = []
            for row in cursor.fetchall():
                if len(row) != 2:
                    raise ProfileError(f"history {item.name} 返回列数不匹配")
                points.append(
                    HistoryPoint(
                        bucket=self._bucket(row[0]),
                        periodic_key=self._periodic_key(row[0], item.periodicity),
                        value=float(row[1]) if item.metric is HistoryMetric.RATIO else int(row[1]),
                    )
                )
            points.sort(key=lambda point: point.bucket)
            watermark_value = None
            if item.watermark_column is not None:
                cursor.execute(self._watermark_query(relation, item))
                watermark_row = cursor.fetchone()
                if watermark_row is None or len(watermark_row) != 1:
                    raise ProfileError(f"history {item.name} watermark 返回值无效")
                watermark_value = (
                    None
                    if watermark_row[0] is None
                    else self._bucket(watermark_row[0])
                )
            histories.append(
                HistorySeries(
                    name=item.name,
                    metric=item.metric,
                    points=tuple(points),
                    watermark_column=item.watermark_column,
                    watermark_value=watermark_value,
                    sla_seconds=item.sla_seconds,
                )
            )
        return RelationHistorySnapshot(
            relation_name=relation.relation_name,
            histories=tuple(histories),
        )

    def _read(self, relation_name: str, kind: SnapshotKind) -> Any:
        relation = self._relation(relation_name)
        try:
            with (
                self._db_connect(**self._connection_kwargs) as connection,
                connection.cursor() as cursor,
            ):
                if self._read_only:
                    cursor.execute("SET TRANSACTION READ ONLY")
                    cursor.execute("SHOW transaction_read_only")
                    if cursor.fetchone() != ("on",):
                        raise ProfileError("数据库未启用只读事务")
                if kind is SnapshotKind.CURRENT:
                    return self._read_current(cursor, relation)
                return self._read_history(cursor, relation)
        except ProfileError:
            raise
        except Exception as exc:
            raise ProfileError(f"读取聚合快照失败：{relation_name}: {exc}") from None

    def read_current(self, relation_name: str) -> RelationProfileSnapshot:
        return self._read(relation_name, SnapshotKind.CURRENT)

    def read_history(self, relation_name: str) -> RelationHistorySnapshot:
        return self._read(relation_name, SnapshotKind.HISTORY)

    def read_snapshot(self, relation_names: Sequence[str] | None = None) -> ProfileSnapshot:
        selected = (
            tuple(relation.relation_name for relation in self._spec.relations)
            if relation_names is None
            else tuple(relation_names)
        )
        current = tuple(self.read_current(name) for name in selected)
        history = tuple(self.read_history(name) for name in selected)
        return ProfileSnapshot.create(spec=self._spec, current=current, history=history)


def settings_connection_kwargs(settings: Settings) -> dict[str, object]:
    return {
        "host": settings.postgres_host,
        "port": settings.postgres_port,
        "dbname": settings.postgres_database,
        "user": settings.postgres_user,
        "password": settings.postgres_password.get_secret_value(),
    }


def build_profile_snapshot(
    *,
    settings: Settings,
    project_root: Path = PROJECT_ROOT,
    db_connect: DatabaseConnect = psycopg.connect,
    relation_names: Sequence[str] | None = None,
) -> ProfileSnapshot:
    spec = load_profile_spec(project_root)
    reader = AggregateSnapshotReader(
        schema_name=settings.postgres_schema,
        spec=spec,
        db_connect=db_connect,
        connection_kwargs=settings_connection_kwargs(settings),
    )
    return reader.read_snapshot(relation_names)
