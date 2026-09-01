from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql

from data_incident_gym.baseline import (
    CATALOG_COLUMNS_QUERY,
    ColumnSummary,
    RelationSummary,
    make_baseline_summary,
)
from data_incident_gym.config import PROJECT_ROOT, Settings
from data_incident_gym.profiles import ProfileError, ProfileSnapshot, load_profile_snapshot
from data_incident_gym.scenarios import (
    AddNullableColumnMutation,
    ColumnRenameMutation,
    ColumnTypeMutation,
    DuplicatePaymentRowsMutation,
    NoMutation,
    OrphanPaymentRowsMutation,
    ScenarioSpec,
    SetFieldNullMutation,
    load_scenario_spec,
    orphan_payment_rows,
    parse_scenario_spec,
)

RUN_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_RUNTIME_SCHEMA_VERSION = "p1.runtime.v1"
_SCENARIO_SNAPSHOT_KEYS = {"schema_version", "scenario_spec_sha256", "scenario"}
_EXPECTED_RUNTIME_ARTIFACTS = {
    "manifest": "dbt/target/manifest.json",
    "run_results": "dbt/target/run_results.json",
    "dbt_log": "dbt/logs/dbt.log",
    "schema": "schema.json",
    "profile_snapshot": "profile_snapshot.json",
    "incident_brief": "incident_brief.json",
}
_EXPECTED_PAYMENT_DUPLICATES = {
    "duplicate_payment_record": (114, 1, 1, "credit_card", 56),
    "duplicate_payment_coupon_a": (116, 0, 3, "coupon", 16),
    "duplicate_payment_coupon_b": (116, 0, 3, "coupon", 16),
}
_EXPECTED_ORPHAN_CHANNEL_COUNTS = {"credit_card": 56, "coupon": 16}

DatabaseConnect = Callable[..., Any]


class LabVerificationError(RuntimeError):
    """Raised when persisted lab facts do not match the private ScenarioSpec."""


class ScenarioVerificationStatus(StrEnum):
    EXPECTED_FAILURE = "EXPECTED_FAILURE"
    EXPECTED_ANOMALY = "EXPECTED_ANOMALY"
    HEALTHY_CONTROL = "HEALTHY_CONTROL"


@dataclass(frozen=True)
class ScenarioVerification:
    status: ScenarioVerificationStatus
    incident_case_id: str
    run_id: str
    dbt_exit_code: int
    failed_nodes: tuple[str, ...]
    skipped_nodes: tuple[str, ...]
    affected_assets: tuple[str, ...]
    schema_fingerprint: str
    profile_spec_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.status, ScenarioVerificationStatus):
            raise TypeError("status must be a ScenarioVerificationStatus")
        if not isinstance(self.incident_case_id, str) or not self.incident_case_id:
            raise ValueError("incident_case_id must be non-empty")
        if not isinstance(self.run_id, str) or RUN_ID_PATTERN.fullmatch(self.run_id) is None:
            raise ValueError("run_id must be a 32-character hexadecimal identifier")
        if type(self.dbt_exit_code) is not int:
            raise TypeError("dbt_exit_code must be an int")
        for values, label in (
            (self.failed_nodes, "failed_nodes"),
            (self.skipped_nodes, "skipped_nodes"),
            (self.affected_assets, "affected_assets"),
        ):
            if any(not isinstance(value, str) or not value for value in values):
                raise TypeError(f"{label} must contain non-empty strings")
            if tuple(values) != tuple(sorted(set(values))):
                raise ValueError(f"{label} must be unique and sorted")
        for value, label in (
            (self.schema_fingerprint, "schema_fingerprint"),
            (self.profile_spec_sha256, "profile_spec_sha256"),
        ):
            if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
                raise ValueError(f"{label} must be a SHA-256 digest")

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True) + "\n"


def _clean(error: LabVerificationError) -> LabVerificationError:
    error.__cause__ = None
    error.__context__ = None
    return error


class IncidentVerifier:
    def __init__(
        self,
        project_root: Path = PROJECT_ROOT,
        *,
        settings: Settings | None = None,
        db_connect: DatabaseConnect | None = None,
    ) -> None:
        self.project_root = project_root
        self.settings = settings or Settings(_env_file=None)
        self.db_connect = db_connect or psycopg.connect

    @staticmethod
    def _read_object(path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(
                path.read_text(encoding="utf-8"),
                object_pairs_hook=IncidentVerifier._reject_duplicate_json_keys,
            )
        except (OSError, UnicodeError, json.JSONDecodeError, LabVerificationError):
            raise _clean(LabVerificationError(f"无法读取验证产物：{path.name}")) from None
        if not isinstance(payload, dict):
            raise _clean(LabVerificationError(f"验证产物必须是 JSON object：{path.name}"))
        return payload

    @staticmethod
    def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for key, value in pairs:
            if key in payload:
                raise LabVerificationError("JSON 存在重复键")
            payload[key] = value
        return payload

    def _read_scenario_snapshot(self, run_id: str) -> ScenarioSpec:
        path = self.project_root / ".dig" / "lab" / "private" / run_id / "scenario_snapshot.json"
        payload = self._read_object(path)
        if set(payload) != _SCENARIO_SNAPSHOT_KEYS:
            raise _clean(LabVerificationError("Scenario snapshot 字段集合无效"))
        scenario_payload = payload.get("scenario")
        if not isinstance(scenario_payload, dict):
            raise _clean(LabVerificationError("Scenario snapshot 缺少 ScenarioSpec"))
        try:
            scenario = parse_scenario_spec(
                json.dumps(scenario_payload),
                "scenario_snapshot.json",
            )
            committed = load_scenario_spec(scenario.incident_case_id, self.project_root)
        except Exception:
            raise _clean(LabVerificationError("Scenario snapshot 无效")) from None
        if payload.get("scenario_spec_sha256") != committed.digest():
            raise _clean(LabVerificationError("ScenarioSpec digest 不一致"))
        if scenario.digest() != committed.digest():
            raise _clean(LabVerificationError("Scenario snapshot 与提交版本不一致"))
        return committed

    def _connection_kwargs(self) -> dict[str, object]:
        return {
            "host": self.settings.postgres_host,
            "port": self.settings.postgres_port,
            "dbname": self.settings.postgres_database,
            "user": self.settings.postgres_user,
            "password": self.settings.postgres_password.get_secret_value(),
        }

    def _inspect_relation(self, relation_name: str) -> RelationSummary:
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
                    raise LabVerificationError(f"关系不存在：{relation_name}")
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
                row = cursor.fetchone()
                if row is None:
                    raise LabVerificationError(f"无法读取关系行数：{relation_name}")
                return RelationSummary(relation_name, int(row[0]), columns)
        except LabVerificationError:
            raise
        except Exception as exc:
            raise _clean(LabVerificationError(f"无法检查关系：{relation_name}: {exc}")) from None

    def _inspect_relations(self, relation_names: tuple[str, ...]) -> tuple[RelationSummary, ...]:
        return tuple(self._inspect_relation(relation_name) for relation_name in relation_names)

    @staticmethod
    def _affected_models(manifest: dict[str, Any], start: str) -> set[str]:
        nodes = manifest.get("nodes")
        child_map = manifest.get("child_map")
        if not isinstance(nodes, dict) or not isinstance(child_map, dict):
            raise _clean(LabVerificationError("manifest 缺少 nodes/child_map"))
        if set(nodes) != set(child_map):
            raise _clean(LabVerificationError("manifest child_map 节点集合不完整"))
        adjacency: dict[str, tuple[str, ...]] = {}
        for node_id, node in nodes.items():
            if not isinstance(node, dict) or not isinstance(node.get("resource_type"), str):
                raise _clean(LabVerificationError("manifest 节点无效"))
            children = child_map[node_id]
            if not isinstance(children, list) or any(
                not isinstance(child, str) for child in children
            ):
                raise _clean(LabVerificationError("manifest child_map 无效"))
            if len(children) != len(set(children)) or any(child not in nodes for child in children):
                raise _clean(LabVerificationError("manifest child_map 引用无效"))
            adjacency[node_id] = tuple(children)
        start_node = nodes.get(start)
        if not isinstance(start_node, dict) or not isinstance(
            start_node.get("resource_type"), str
        ):
            raise _clean(LabVerificationError("manifest 直接失败节点无效"))
        if start_node["resource_type"] == "test":
            parent_map = manifest.get("parent_map")
            if not isinstance(parent_map, dict) or set(parent_map) != set(nodes):
                raise _clean(LabVerificationError("manifest parent_map 节点集合不完整"))
            parents = parent_map.get(start)
            if not isinstance(parents, list) or any(
                not isinstance(parent, str) or parent not in nodes for parent in parents
            ):
                raise _clean(LabVerificationError("manifest parent_map 引用无效"))
            affected = {
                parent
                for parent in parents
                if nodes[parent].get("resource_type") == "model"
            }
            if not affected:
                raise _clean(LabVerificationError("failed test has no tested model"))
            return affected
        if start_node["resource_type"] not in {"model", "seed"}:
            raise _clean(LabVerificationError("manifest 缺少可遍历的模型或 seed 节点"))
        found: set[str] = set()
        pending = [start] if start_node["resource_type"] == "model" else list(adjacency[start])
        while pending:
            node_id = pending.pop()
            if node_id in found:
                continue
            found.add(node_id)
            for child in adjacency[node_id]:
                child_node = nodes[child]
                if child_node.get("resource_type") == "model":
                    pending.append(child)
        return {node_id for node_id in found if nodes[node_id].get("resource_type") == "model"}

    @staticmethod
    def _read_schema(path: Path) -> tuple[str, tuple[RelationSummary, ...], str]:
        payload = IncidentVerifier._read_object(path)
        if set(payload) != {"schema", "relations", "fingerprint"}:
            raise _clean(LabVerificationError("schema 快照字段集合无效"))
        schema_name = payload.get("schema")
        relations_payload = payload.get("relations")
        fingerprint = payload.get("fingerprint")
        if (
            not isinstance(schema_name, str)
            or not isinstance(relations_payload, list)
            or not isinstance(fingerprint, str)
            or re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None
        ):
            raise _clean(LabVerificationError("schema 快照内容无效"))
        relations: list[RelationSummary] = []
        for relation in relations_payload:
            if not isinstance(relation, dict) or set(relation) != {"name", "row_count", "columns"}:
                raise _clean(LabVerificationError("schema relation 无效"))
            columns_payload = relation.get("columns")
            if (
                not isinstance(relation.get("name"), str)
                or type(relation.get("row_count")) is not int
                or not isinstance(columns_payload, list)
            ):
                raise _clean(LabVerificationError("schema relation 内容无效"))
            columns: list[ColumnSummary] = []
            for column in columns_payload:
                if not isinstance(column, dict) or set(column) != {
                    "name",
                    "data_type",
                    "nullable",
                    "ordinal_position",
                }:
                    raise _clean(LabVerificationError("schema column 无效"))
                if (
                    not isinstance(column.get("name"), str)
                    or not isinstance(column.get("data_type"), str)
                    or type(column.get("nullable")) is not bool
                    or type(column.get("ordinal_position")) is not int
                ):
                    raise _clean(LabVerificationError("schema column 内容无效"))
                columns.append(
                    ColumnSummary(
                        column["name"],
                        column["data_type"],
                        column["nullable"],
                        column["ordinal_position"],
                    )
                )
            relations.append(
                RelationSummary(relation["name"], relation["row_count"], tuple(columns))
            )
        recomputed = make_baseline_summary(schema_name, tuple(relations))
        if recomputed.fingerprint != fingerprint:
            raise _clean(LabVerificationError("schema fingerprint 不匹配"))
        return schema_name, recomputed.relations, fingerprint

    @staticmethod
    def _validate_runtime(run_root: Path, run_id: str) -> dict[str, Any]:
        payload = IncidentVerifier._read_object(run_root / "runtime.json")
        expected = {
            "schema_version",
            "run_id",
            "dbt_exit_code",
            "artifacts",
            "observable_relations",
            "profile_spec_sha256",
        }
        if set(payload) != expected or payload.get("schema_version") != _RUNTIME_SCHEMA_VERSION:
            raise _clean(LabVerificationError("runtime 字段集合无效"))
        if payload.get("run_id") != run_id or type(payload.get("dbt_exit_code")) is not int:
            raise _clean(LabVerificationError("runtime identity 无效"))
        artifacts = payload.get("artifacts")
        if artifacts != _EXPECTED_RUNTIME_ARTIFACTS:
            raise _clean(LabVerificationError("runtime artifacts 清单无效"))
        observable = payload.get("observable_relations")
        if (
            not isinstance(observable, dict)
            or set(observable) != {"schema", "profile", "history"}
            or any(not isinstance(values, list) for values in observable.values())
        ):
            raise _clean(LabVerificationError("runtime observable relations 无效"))
        return payload

    @staticmethod
    def _read_run_results(
        path: Path,
        manifest: dict[str, Any],
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        payload = IncidentVerifier._read_object(path)
        results = payload.get("results")
        nodes = manifest.get("nodes")
        if not isinstance(results, list) or not isinstance(nodes, dict):
            raise _clean(LabVerificationError("run_results 内容无效"))
        failed: list[str] = []
        skipped: list[str] = []
        seen: set[str] = set()
        for result in results:
            if not isinstance(result, dict):
                raise _clean(LabVerificationError("run_results 结果项无效"))
            unique_id = result.get("unique_id")
            status = result.get("status")
            if not isinstance(unique_id, str) or not isinstance(status, str):
                raise _clean(LabVerificationError("run_results 结果项无效"))
            if unique_id in seen or unique_id not in nodes:
                raise _clean(LabVerificationError("run_results 节点引用无效"))
            seen.add(unique_id)
            if status in {"error", "fail"}:
                failed.append(unique_id)
            elif status == "skipped":
                skipped.append(unique_id)
        return tuple(sorted(failed)), tuple(sorted(skipped))

    @staticmethod
    def _validate_mutation_schema(
        scenario: ScenarioSpec,
        relations: dict[str, RelationSummary],
        baseline_relations: dict[str, RelationSummary],
    ) -> None:
        expected_relations = dict(baseline_relations)
        for mutation in scenario.reset_and_injection_contract.mutations:
            expected = expected_relations.get(mutation.relation)
            actual = relations.get(mutation.relation)
            if expected is None or actual is None:
                raise _clean(LabVerificationError(f"mutation relation 未捕获：{mutation.relation}"))
            columns = list(expected.columns)
            expected_row_count = expected.row_count
            column_map = {column.name: column for column in columns}
            if isinstance(mutation, ColumnRenameMutation):
                if mutation.from_column not in column_map or mutation.to_column in column_map:
                    raise _clean(LabVerificationError("rename mutation 状态不匹配"))
                columns = [
                    ColumnSummary(
                        mutation.to_column if column.name == mutation.from_column else column.name,
                        column.data_type,
                        column.nullable,
                        column.ordinal_position,
                    )
                    for column in columns
                ]
            elif isinstance(mutation, ColumnTypeMutation):
                column = column_map.get(mutation.column)
                if column is None or column.data_type != mutation.from_type:
                    raise _clean(LabVerificationError("type mutation baseline 不匹配"))
                columns = [
                    ColumnSummary(
                        column.name,
                        mutation.to_type if column.name == mutation.column else column.data_type,
                        column.nullable,
                        column.ordinal_position,
                    )
                    for column in columns
                ]
            elif isinstance(mutation, AddNullableColumnMutation):
                column = column_map.get(mutation.column)
                if (
                    column is not None
                ):
                    raise _clean(LabVerificationError("distractor 已存在于健康 baseline"))
                columns.append(
                    ColumnSummary(
                        mutation.column,
                        mutation.data_type,
                        True,
                        max((item.ordinal_position for item in columns), default=0) + 1,
                    )
                )
            elif isinstance(mutation, (DuplicatePaymentRowsMutation, OrphanPaymentRowsMutation)):
                expected_row_count += len(mutation.inserted_payment_ids)
            elif isinstance(mutation, SetFieldNullMutation):
                pass
            elif not isinstance(mutation, NoMutation):
                raise _clean(LabVerificationError("未知 mutation"))
            expected_relations[mutation.relation] = RelationSummary(
                mutation.relation,
                expected_row_count,
                tuple(columns),
            )
        for relation_name in {
            mutation.relation for mutation in scenario.reset_and_injection_contract.mutations
        }:
            actual = relations.get(relation_name)
            if actual != expected_relations[relation_name]:
                raise _clean(LabVerificationError("fault schema 与声明 mutation 不匹配"))

    def _validate_null_mutations(self, scenario: ScenarioSpec) -> None:
        mutations = tuple(
            mutation
            for mutation in scenario.reset_and_injection_contract.mutations
            if isinstance(mutation, SetFieldNullMutation)
        )
        for mutation in mutations:
            target_query = sql.SQL("SELECT {} FROM {}.{} WHERE {} = %s").format(
                sql.Identifier(mutation.column),
                sql.Identifier(self.settings.postgres_schema),
                sql.Identifier(mutation.relation),
                sql.Identifier(mutation.selector_column),
            )
            count_query = sql.SQL("SELECT count(*) FROM {}.{} WHERE {} IS NULL").format(
                sql.Identifier(self.settings.postgres_schema),
                sql.Identifier(mutation.relation),
                sql.Identifier(mutation.column),
            )
            try:
                with (
                    self.db_connect(**self._connection_kwargs()) as connection,
                    connection.cursor() as cursor,
                ):
                    cursor.execute(target_query, (mutation.selector_value,))
                    rows = cursor.fetchall()
                    cursor.execute(count_query)
                    count_row = cursor.fetchone()
            except LabVerificationError:
                raise
            except Exception as exc:
                raise _clean(
                    LabVerificationError(
                        f"无法验证 NULL mutation：{mutation.relation}.{mutation.column}: {exc}"
                    )
                ) from None
            if (
                len(rows) != 1
                or rows[0][0] is not None
                or count_row is None
                or int(count_row[0]) != 1
            ):
                raise _clean(
                    LabVerificationError(
                        f"NULL mutation 数据状态不匹配：{mutation.relation}.{mutation.column}"
                    )
                )

    def _validate_payment_duplicates(
        self,
        scenario: ScenarioSpec,
        profile: ProfileSnapshot,
    ) -> None:
        mutation = next(
            (
                item
                for item in scenario.reset_and_injection_contract.mutations
                if isinstance(item, DuplicatePaymentRowsMutation)
            ),
            None,
        )
        expected = _EXPECTED_PAYMENT_DUPLICATES.get(scenario.incident_case_id)
        if mutation is None or expected is None:
            raise _clean(LabVerificationError("duplicate-payment mutation 不在冻结私有集合中"))
        table = sql.SQL("{}.{}").format(
            sql.Identifier(self.settings.postgres_schema),
            sql.Identifier(mutation.relation),
        )
        row_count_query = sql.SQL("SELECT count(*) FROM {}").format(table)
        key_query = sql.SQL(
            "SELECT COALESCE(sum(group_count - 1) FILTER (WHERE group_count > 1), 0) "
            "FROM (SELECT id, count(*) AS group_count FROM {} GROUP BY id) grouped"
        ).format(table)
        fingerprint_query = sql.SQL(
            "SELECT COALESCE(sum(group_count - 1) FILTER (WHERE group_count > 1), 0) "
            "FROM (SELECT order_id, payment_method, amount, count(*) AS group_count "
            "FROM {} GROUP BY order_id, payment_method, amount) grouped"
        ).format(table)
        channel_query = sql.SQL(
            "SELECT count(*) FROM {} WHERE payment_method IS NOT DISTINCT FROM %s"
        ).format(table)

        def scalar(cursor: Any, query: sql.Composed, params: tuple[Any, ...] = ()) -> int:
            cursor.execute(query, params)
            row = cursor.fetchone()
            if row is None:
                raise LabVerificationError("duplicate-payment 聚合查询没有返回结果")
            return int(row[0])

        try:
            with (
                self.db_connect(**self._connection_kwargs()) as connection,
                connection.cursor() as cursor,
            ):
                observed = (
                    scalar(cursor, row_count_query),
                    scalar(cursor, key_query),
                    scalar(cursor, fingerprint_query),
                    expected[3],
                    scalar(cursor, channel_query, (expected[3],)),
                )
        except LabVerificationError:
            raise
        except Exception as exc:
            raise _clean(
                LabVerificationError(f"无法验证 duplicate-payment 聚合：{exc}")
            ) from None
        if observed != expected:
            raise _clean(LabVerificationError("duplicate-payment 私有聚合事实不匹配"))

        public_snapshot = next(
            (item for item in profile.current if item.relation_name == mutation.relation),
            None,
        )
        profile_is_public = (
            mutation.relation in scenario.observable_evidence_contract.profile_relations
        )
        if profile_is_public != (public_snapshot is not None):
            raise _clean(LabVerificationError("duplicate-payment profile 公开边界不匹配"))
        if public_snapshot is None:
            return
        key_fact = next(
            (item for item in public_snapshot.business_key_duplicates if item.name == "id"),
            None,
        )
        fingerprint_fact = next(
            (
                item
                for item in public_snapshot.business_fingerprint_duplicates
                if item.name == "order_payment_amount"
            ),
            None,
        )
        group_fact = next(
            (item for item in public_snapshot.groups if item.name == "payment_method"),
            None,
        )
        if key_fact is None or fingerprint_fact is None or group_fact is None:
            raise _clean(LabVerificationError("duplicate-payment public profile 不完整"))
        channel_count = next(
            (
                count
                for values, count in zip(group_fact.values, group_fact.counts, strict=True)
                if values == (expected[3],)
            ),
            None,
        )
        if (
            key_fact.duplicate_count != expected[1]
            or fingerprint_fact.duplicate_count != expected[2]
            or channel_count != expected[4]
        ):
            raise _clean(LabVerificationError("duplicate-payment public profile 事实不匹配"))

    def _validate_orphan_payments(
        self,
        scenario: ScenarioSpec,
        profile: ProfileSnapshot,
    ) -> None:
        mutation = next(
            (
                item
                for item in scenario.reset_and_injection_contract.mutations
                if isinstance(item, OrphanPaymentRowsMutation)
            ),
            None,
        )
        if mutation is None:
            raise _clean(LabVerificationError("orphan-payment mutation 不在冻结私有集合中"))
        rows = orphan_payment_rows(mutation)
        channel = rows[0][2]
        expected_channel_count = _EXPECTED_ORPHAN_CHANNEL_COUNTS.get(channel)
        if expected_channel_count is None:
            raise _clean(LabVerificationError("orphan-payment 渠道不在冻结私有集合中"))
        table = sql.SQL("{}.{}").format(
            sql.Identifier(self.settings.postgres_schema),
            sql.Identifier(mutation.relation),
        )
        orders_table = sql.SQL("{}.{}").format(
            sql.Identifier(self.settings.postgres_schema),
            sql.Identifier("raw_orders"),
        )
        rows_query = sql.SQL(
            "SELECT id, order_id, payment_method, amount FROM {} "
            "WHERE id = ANY(%s) ORDER BY id"
        ).format(table)
        row_count_query = sql.SQL("SELECT count(*) FROM {}").format(table)
        key_query = sql.SQL(
            "SELECT COALESCE(sum(group_count - 1) FILTER (WHERE group_count > 1), 0) "
            "FROM (SELECT id, count(*) AS group_count FROM {} GROUP BY id) grouped"
        ).format(table)
        fingerprint_query = sql.SQL(
            "SELECT COALESCE(sum(group_count - 1) FILTER (WHERE group_count > 1), 0) "
            "FROM (SELECT order_id, payment_method, amount, count(*) AS group_count "
            "FROM {} GROUP BY order_id, payment_method, amount) grouped"
        ).format(table)
        relationship_query = sql.SQL(
            "SELECT count(*) FROM {} payments "
            "LEFT JOIN {} orders ON payments.order_id = orders.id "
            "WHERE orders.id IS NULL"
        ).format(table, orders_table)
        channel_query = sql.SQL(
            "SELECT count(*) FROM {} WHERE payment_method IS NOT DISTINCT FROM %s"
        ).format(table)
        order_count_query = sql.SQL("SELECT count(*) FROM {}").format(orders_table)
        missing_orders_query = sql.SQL(
            "SELECT count(*) FROM {} WHERE id = ANY(%s)"
        ).format(orders_table)

        def scalar(cursor: Any, query: sql.Composed, params: tuple[Any, ...] = ()) -> int:
            cursor.execute(query, params)
            row = cursor.fetchone()
            if row is None:
                raise LabVerificationError("orphan-payment 聚合查询没有返回结果")
            return int(row[0])

        try:
            with (
                self.db_connect(**self._connection_kwargs()) as connection,
                connection.cursor() as cursor,
            ):
                cursor.execute(rows_query, (list(mutation.inserted_payment_ids),))
                observed_rows = tuple(tuple(row) for row in cursor.fetchall())
                observed = (
                    scalar(cursor, row_count_query),
                    scalar(cursor, key_query),
                    scalar(cursor, fingerprint_query),
                    scalar(cursor, relationship_query),
                    scalar(cursor, channel_query, (channel,)),
                    scalar(cursor, order_count_query),
                    scalar(cursor, missing_orders_query, (list(mutation.missing_order_ids),)),
                )
        except LabVerificationError:
            raise
        except Exception as exc:
            raise _clean(LabVerificationError(f"无法验证 orphan-payment 聚合：{exc}")) from None

        expected_rows = tuple(sorted(rows))
        expected = (
            113 + len(rows),
            0,
            0,
            len(rows),
            expected_channel_count,
            99,
            0,
        )
        if observed_rows != expected_rows or observed != expected:
            raise _clean(LabVerificationError("orphan-payment 私有聚合事实不匹配"))

        public_snapshot = next(
            (item for item in profile.current if item.relation_name == mutation.relation),
            None,
        )
        if (
            public_snapshot is None
            or mutation.relation not in scenario.observable_evidence_contract.profile_relations
        ):
            raise _clean(LabVerificationError("orphan-payment profile 公开边界不匹配"))
        relationship_fact = next(
            (
                item
                for item in public_snapshot.relationship_violations
                if item.name == "order_id_to_raw_orders_id"
            ),
            None,
        )
        key_fact = next(
            (item for item in public_snapshot.business_key_duplicates if item.name == "id"),
            None,
        )
        fingerprint_fact = next(
            (
                item
                for item in public_snapshot.business_fingerprint_duplicates
                if item.name == "order_payment_amount"
            ),
            None,
        )
        group_fact = next(
            (item for item in public_snapshot.groups if item.name == "payment_method"),
            None,
        )
        channel_count = None
        if group_fact is not None:
            channel_count = next(
                (
                    count
                    for values, count in zip(group_fact.values, group_fact.counts, strict=True)
                    if values == (channel,)
                ),
                None,
            )
        if (
            relationship_fact is None
            or key_fact is None
            or fingerprint_fact is None
            or relationship_fact.violation_count != len(rows)
            or key_fact.duplicate_count != 0
            or fingerprint_fact.duplicate_count != 0
            or channel_count != expected_channel_count
        ):
            raise _clean(LabVerificationError("orphan-payment public profile 事实不匹配"))

        history_is_public = "raw_orders" in scenario.observable_evidence_contract.history_relations
        history_snapshot = next(
            (item for item in profile.history if item.relation_name == "raw_orders"),
            None,
        )
        order_snapshot = next(
            (item for item in profile.current if item.relation_name == "raw_orders"),
            None,
        )
        if not history_is_public:
            if history_snapshot is not None or order_snapshot is not None:
                raise _clean(LabVerificationError("orphan-payment history 公开边界不匹配"))
            return
        if history_snapshot is None or order_snapshot is None or order_snapshot.row_count != 99:
            raise _clean(LabVerificationError("orphan-payment history 公开事实缺失"))
        series = next(
            (item for item in history_snapshot.histories if item.name == "order_count_by_day"),
            None,
        )
        if (
            series is None
            or not series.points
            or series.watermark_column != "order_date"
            or series.watermark_value != "2018-04-09"
        ):
            raise _clean(LabVerificationError("orphan-payment history watermark 无效"))

    def _write_private_verification(
        self,
        run_id: str,
        verification: ScenarioVerification,
    ) -> None:
        path = self.project_root / ".dig" / "lab" / "private" / run_id / "verification.json"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(verification.to_json(), encoding="utf-8")
        except OSError:
            raise _clean(LabVerificationError("无法写入私有 verification.json")) from None

    def verify(self, run_id: str) -> ScenarioVerification:
        if not isinstance(run_id, str) or RUN_ID_PATTERN.fullmatch(run_id) is None:
            raise _clean(LabVerificationError(f"非法 run_id：{run_id}"))
        run_root = self.project_root / ".dig" / "lab" / "runs" / run_id
        runtime = self._validate_runtime(run_root, run_id)
        scenario = self._read_scenario_snapshot(run_id)
        if runtime["profile_spec_sha256"] != self._profile_spec_hash():
            raise _clean(LabVerificationError("ProfileSpec hash 不一致"))

        manifest = self._read_object(run_root / "dbt/target/manifest.json")
        failed_nodes, skipped_nodes = self._read_run_results(
            run_root / "dbt/target/run_results.json",
            manifest,
        )
        schema_name, schema_relations, schema_fingerprint = self._read_schema(
            run_root / "schema.json"
        )
        if schema_name != self.settings.postgres_schema:
            raise _clean(LabVerificationError("schema 名称不匹配"))
        observable = runtime["observable_relations"]
        if tuple(relation.name for relation in schema_relations) != tuple(
            sorted(observable["schema"])
        ):
            raise _clean(LabVerificationError("公开 schema 关系集合不匹配"))

        try:
            profile = load_profile_snapshot(run_root / "profile_snapshot.json")
        except ProfileError as exc:
            raise _clean(LabVerificationError(str(exc))) from None
        current_names = tuple(item.relation_name for item in profile.current)
        history_names = tuple(item.relation_name for item in profile.history)
        expected_current_names = set(observable["profile"]) | set(observable["history"])
        if set(current_names) != expected_current_names:
            raise _clean(LabVerificationError("公开 profile 关系集合不匹配"))
        if set(history_names) != set(observable["history"]):
            raise _clean(LabVerificationError("公开 history 关系集合不匹配"))
        if profile.profile_spec_sha256 != runtime["profile_spec_sha256"]:
            raise _clean(LabVerificationError("profile snapshot hash 不一致"))

        current_profiles = {item.relation_name: item for item in profile.current}
        for mutation in scenario.reset_and_injection_contract.mutations:
            if not isinstance(mutation, SetFieldNullMutation):
                continue
            snapshot = current_profiles.get(mutation.relation)
            if mutation.relation not in observable["profile"]:
                if snapshot is not None:
                    raise _clean(
                        LabVerificationError("未公开 profile 的 NULL mutation 不得出现在快照中")
                    )
                continue
            if snapshot is None:
                raise _clean(LabVerificationError("NULL mutation profile 缺少目标关系"))
            column = next(
                (item for item in snapshot.columns if item.column_name == mutation.column),
                None,
            )
            if column is None or column.null_count != 1:
                raise _clean(LabVerificationError("NULL mutation profile 计数不匹配"))

        dbt_exit_code = runtime["dbt_exit_code"]
        relations = {relation.name: relation for relation in schema_relations}
        mutation_relations = {
            getattr(mutation, "relation", "")
            for mutation in scenario.reset_and_injection_contract.mutations
        }
        for relation_name in mutation_relations:
            if relation_name and relation_name not in relations:
                relations[relation_name] = self._inspect_relation(relation_name)

        if (
            scenario.variant_role is not None
            and scenario.variant_role.value == "NO_INCIDENT_CONTROL"
        ):
            if dbt_exit_code != 0 or failed_nodes or skipped_nodes:
                raise _clean(LabVerificationError("health control 的 dbt build 未健康完成"))
            if any(
                result.get("status") not in {"success", "pass"}
                for result in self._read_object(run_root / "dbt/target/run_results.json")["results"]
            ):
                raise _clean(LabVerificationError("health control 存在非成功结果"))
            try:
                _, baseline_relations, _ = self._read_schema(
                    self.project_root / ".dig" / "baseline-summary.json"
                )
                baseline_schema = {item.name: item for item in baseline_relations}
                current_schema = {
                    item.name: item
                    for item in self._inspect_relations(tuple(baseline_schema))
                }
                if current_schema != baseline_schema:
                    raise LabVerificationError("health control 的 schema 与健康基线不一致")
                baseline_profile = load_profile_snapshot(
                    self.project_root / ".dig" / "baseline" / "profile_snapshot.json"
                )
                current_profile = {
                    item.relation_name: item
                    for item in profile.current
                    if item.relation_name in observable["profile"]
                }
                expected_profile = {
                    item.relation_name: item
                    for item in baseline_profile.current
                    if item.relation_name in observable["profile"]
                }
                current_history = {
                    item.relation_name: item
                    for item in profile.history
                    if item.relation_name in observable["history"]
                }
                expected_history = {
                    item.relation_name: item
                    for item in baseline_profile.history
                    if item.relation_name in observable["history"]
                }
                if current_profile != expected_profile or current_history != expected_history:
                    raise LabVerificationError(
                        "health control 的 profile 与健康基线不一致"
                    )
            except (OSError, LabVerificationError, ProfileError):
                raise _clean(LabVerificationError("health control 基线校验失败")) from None
            status = ScenarioVerificationStatus.HEALTHY_CONTROL
        elif scenario.direct_failure is None:
            if dbt_exit_code != 0 or failed_nodes or skipped_nodes:
                raise _clean(LabVerificationError("data anomaly 场景的 dbt build 未健康完成"))
            mutation = next(
                item
                for item in scenario.reset_and_injection_contract.mutations
                if isinstance(
                    item,
                    (DuplicatePaymentRowsMutation, OrphanPaymentRowsMutation),
                )
            )
            seed_nodes = tuple(
                node_id
                for node_id, node in manifest["nodes"].items()
                if node.get("resource_type") == "seed" and node.get("name") == mutation.relation
            )
            if len(seed_nodes) != 1:
                raise _clean(LabVerificationError("source-data seed anchor 不唯一"))
            affected = self._affected_models(manifest, seed_nodes[0])
            if affected != set(scenario.affected_assets):
                raise _clean(LabVerificationError("data anomaly 影响模型集合不匹配"))
            try:
                _, baseline_relation_items, _ = self._read_schema(
                    self.project_root / ".dig" / "baseline-summary.json"
                )
            except (OSError, LabVerificationError):
                raise _clean(LabVerificationError("健康基线 schema 不可用")) from None
            self._validate_mutation_schema(
                scenario,
                relations,
                {item.name: item for item in baseline_relation_items},
            )
            if isinstance(mutation, DuplicatePaymentRowsMutation):
                self._validate_payment_duplicates(scenario, profile)
            elif isinstance(mutation, OrphanPaymentRowsMutation):
                self._validate_orphan_payments(scenario, profile)
            else:
                raise _clean(LabVerificationError("data anomaly mutation 未授权"))
            status = ScenarioVerificationStatus.EXPECTED_ANOMALY
        else:
            if dbt_exit_code == 0:
                raise _clean(LabVerificationError("故障场景 dbt 意外成功"))
            if scenario.direct_failure is None or failed_nodes != (scenario.direct_failure,):
                raise _clean(LabVerificationError("直接失败节点不匹配"))
            affected = self._affected_models(manifest, scenario.direct_failure)
            if affected != set(scenario.affected_assets):
                raise _clean(LabVerificationError("影响模型集合不匹配"))
            try:
                _, baseline_relation_items, _ = self._read_schema(
                    self.project_root / ".dig" / "baseline-summary.json"
                )
            except (OSError, LabVerificationError):
                raise _clean(LabVerificationError("健康基线 schema 不可用")) from None
            self._validate_mutation_schema(
                scenario,
                relations,
                {item.name: item for item in baseline_relation_items},
            )
            self._validate_null_mutations(scenario)
            if any(
                isinstance(item, DuplicatePaymentRowsMutation)
                for item in scenario.reset_and_injection_contract.mutations
            ):
                self._validate_payment_duplicates(scenario, profile)
            status = ScenarioVerificationStatus.EXPECTED_FAILURE

        verification = ScenarioVerification(
            status=status,
            incident_case_id=scenario.incident_case_id,
            run_id=run_id,
            dbt_exit_code=dbt_exit_code,
            failed_nodes=failed_nodes,
            skipped_nodes=skipped_nodes,
            affected_assets=tuple(sorted(scenario.affected_assets)),
            schema_fingerprint=schema_fingerprint,
            profile_spec_sha256=profile.profile_spec_sha256,
        )
        self._write_private_verification(run_id, verification)
        return verification

    def load_verification(self, run_id: str) -> ScenarioVerification:
        """Load the frozen private verification without re-querying the database."""

        if not isinstance(run_id, str) or RUN_ID_PATTERN.fullmatch(run_id) is None:
            raise _clean(LabVerificationError(f"非法 run_id：{run_id}"))
        path = self.project_root / ".dig" / "lab" / "private" / run_id / "verification.json"
        payload = self._read_object(path)
        expected = {
            "status",
            "incident_case_id",
            "run_id",
            "dbt_exit_code",
            "failed_nodes",
            "skipped_nodes",
            "affected_assets",
            "schema_fingerprint",
            "profile_spec_sha256",
        }
        if set(payload) != expected or payload.get("run_id") != run_id:
            raise _clean(LabVerificationError("verification 字段集合或 run_id 无效"))
        try:
            return ScenarioVerification(
                status=ScenarioVerificationStatus(payload["status"]),
                incident_case_id=payload["incident_case_id"],
                run_id=payload["run_id"],
                dbt_exit_code=payload["dbt_exit_code"],
                failed_nodes=tuple(payload["failed_nodes"]),
                skipped_nodes=tuple(payload["skipped_nodes"]),
                affected_assets=tuple(payload["affected_assets"]),
                schema_fingerprint=payload["schema_fingerprint"],
                profile_spec_sha256=payload["profile_spec_sha256"],
            )
        except (KeyError, TypeError, ValueError):
            raise _clean(LabVerificationError("verification 内容无效")) from None

    def _profile_spec_hash(self) -> str:
        from data_incident_gym.profiles import load_profile_spec

        return load_profile_spec(self.project_root).digest()
