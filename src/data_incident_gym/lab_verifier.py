from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from data_incident_gym.baseline import (
    ColumnSummary,
    RelationSummary,
    make_baseline_summary,
)
from data_incident_gym.config import PROJECT_ROOT
from data_incident_gym.incidents import (
    IncidentCaseError,
    load_ground_truth,
    parse_ground_truth,
)

RUN_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_EXPECTED_ARTIFACTS = {
    "manifest": "dbt/target/manifest.json",
    "run_results": "dbt/target/run_results.json",
    "dbt_log": "dbt/logs/dbt.log",
    "schema": "schema.json",
}
_EXPECTED_METADATA_KEYS = {
    "schema_version",
    "run_id",
    "incident_case_id",
    "dbt_exit_code",
    "ground_truth_digest",
    "artifacts",
}
_EXPECTED_SCHEMA_KEYS = {"schema", "relations", "fingerprint"}
_EXPECTED_RELATION_KEYS = {"name", "row_count", "columns"}
_EXPECTED_COLUMN_KEYS = {"name", "data_type", "nullable", "ordinal_position"}


class LabVerificationError(RuntimeError):
    """Raised when persisted lab facts do not match Ground Truth."""


class _DuplicateJsonKeyError(ValueError):
    pass


def _reject_duplicate_json_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, value in pairs:
        if key in payload:
            raise _DuplicateJsonKeyError
        payload[key] = value
    return payload


@dataclass(frozen=True)
class LabVerification:
    status: Literal["EXPECTED_FAILURE"]
    incident_case_id: str
    run_id: str
    failed_nodes: tuple[str, ...]
    affected_assets: tuple[str, ...]
    error_category: str
    schema_fingerprint: str
    ground_truth_digest: str

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True) + "\n"


def _clean(error: LabVerificationError) -> LabVerificationError:
    error.__cause__ = None
    error.__context__ = None
    return error


class IncidentVerifier:
    def __init__(self, project_root: Path = PROJECT_ROOT) -> None:
        self.project_root = project_root

    @staticmethod
    def _read_object(path: Path) -> dict[str, Any]:
        error: LabVerificationError | None = None
        try:
            payload = json.loads(
                path.read_text(encoding="utf-8"),
                object_pairs_hook=_reject_duplicate_json_keys,
            )
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            _DuplicateJsonKeyError,
        ):
            error = LabVerificationError(f"无法读取验证产物：{path.name}")
        if error is not None:
            raise _clean(error)
        if not isinstance(payload, dict):
            raise _clean(
                LabVerificationError(f"验证产物必须是 JSON object：{path.name}")
            )
        return payload

    @staticmethod
    def _model_descendants(manifest: dict[str, Any], start: str) -> set[str]:
        nodes = manifest.get("nodes")
        child_map = manifest.get("child_map")
        if not isinstance(nodes, dict) or not isinstance(child_map, dict):
            raise _clean(LabVerificationError("manifest 缺少 nodes/child_map"))
        start_node = nodes.get(start)
        if not isinstance(start_node, dict) or start_node.get("resource_type") != "model":
            raise _clean(LabVerificationError("manifest 缺少直接失败模型"))

        found = {start}
        visited: set[str] = set()
        visiting: set[str] = set()
        pending: list[tuple[str, bool]] = [(start, False)]
        while pending:
            current, exiting = pending.pop()
            if exiting:
                visiting.remove(current)
                visited.add(current)
                continue
            if current in visited:
                continue
            visiting.add(current)
            pending.append((current, True))
            if current not in child_map:
                raise _clean(LabVerificationError("manifest child_map 缺少节点"))
            children = child_map[current]
            if not isinstance(children, list):
                raise _clean(LabVerificationError("manifest child_map 无效"))
            seen_children: set[str] = set()
            for child in reversed(children):
                if not isinstance(child, str):
                    raise _clean(LabVerificationError("manifest child_map 无效"))
                if child in seen_children:
                    raise _clean(LabVerificationError("manifest child_map 节点重复"))
                seen_children.add(child)
                node = nodes.get(child)
                if node is None:
                    raise _clean(LabVerificationError("manifest child_map 节点不存在"))
                if not isinstance(node, dict):
                    raise _clean(LabVerificationError("manifest model 节点无效"))
                if node.get("resource_type") != "model":
                    continue
                if child in visiting:
                    raise _clean(LabVerificationError("manifest child_map 存在循环"))
                if child not in visited:
                    found.add(child)
                    pending.append((child, False))
        return found

    @staticmethod
    def _read_ground_truth(run_root: Path, case_id: str, project_root: Path):
        error: LabVerificationError | None = None
        try:
            committed_truth = load_ground_truth(case_id, project_root)
            committed_text = (
                project_root
                / "config"
                / "incidents"
                / f"{committed_truth.incident_case_id}.json"
            ).read_text(encoding="utf-8")
            json.loads(
                committed_text,
                object_pairs_hook=_reject_duplicate_json_keys,
            )
            snapshot_text = (run_root / "ground_truth.json").read_text(encoding="utf-8")
            json.loads(
                snapshot_text,
                object_pairs_hook=_reject_duplicate_json_keys,
            )
            snapshot_truth = parse_ground_truth(snapshot_text, "ground_truth.json")
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            _DuplicateJsonKeyError,
            IncidentCaseError,
        ):
            error = LabVerificationError("Ground Truth 快照无效")
        if error is not None:
            raise _clean(error)
        if snapshot_truth.digest() != committed_truth.digest():
            raise _clean(LabVerificationError("Ground Truth 快照与提交版本不一致"))
        return committed_truth

    def verify(self, run_id: str) -> LabVerification:
        if not isinstance(run_id, str) or RUN_ID_PATTERN.fullmatch(run_id) is None:
            raise _clean(LabVerificationError(f"非法 run_id：{run_id}"))
        run_root = self.project_root / ".dig" / "lab" / "runs" / run_id
        metadata = self._read_object(run_root / "metadata.json")
        if set(metadata) != _EXPECTED_METADATA_KEYS:
            raise _clean(LabVerificationError("metadata 字段集合无效"))
        if metadata.get("schema_version") != "m2.run.v1":
            raise _clean(LabVerificationError("metadata schema_version 无效"))
        if metadata.get("run_id") != run_id:
            raise _clean(LabVerificationError("metadata run_id 不一致"))
        case_id = metadata.get("incident_case_id")
        if not isinstance(case_id, str):
            raise _clean(LabVerificationError("metadata 缺少 incident_case_id"))

        committed_truth = self._read_ground_truth(run_root, case_id, self.project_root)
        truth_digest = committed_truth.digest()
        if metadata.get("ground_truth_digest") != truth_digest:
            raise _clean(LabVerificationError("metadata Ground Truth digest 不一致"))
        if metadata.get("artifacts") != _EXPECTED_ARTIFACTS:
            raise _clean(LabVerificationError("metadata artifacts 清单无效"))

        dbt_exit_code = metadata.get("dbt_exit_code")
        if (
            not isinstance(dbt_exit_code, int)
            or isinstance(dbt_exit_code, bool)
            or dbt_exit_code == 0
        ):
            raise _clean(LabVerificationError("dbt 意外成功，未触发预期故障"))

        run_results = self._read_object(run_root / "dbt/target/run_results.json")
        results = run_results.get("results")
        if not isinstance(results, list):
            raise _clean(LabVerificationError("run_results 缺少 results"))
        failed_node_ids: list[str] = []
        seen_result_ids: set[str] = set()
        for result in results:
            if not isinstance(result, dict):
                raise _clean(LabVerificationError("run_results 结果项无效"))
            if (
                not isinstance(result.get("unique_id"), str)
                or not isinstance(result.get("status"), str)
            ):
                raise _clean(LabVerificationError("run_results 结果项无效"))
            unique_id = result["unique_id"]
            if unique_id in seen_result_ids:
                raise _clean(LabVerificationError("run_results unique_id 重复"))
            seen_result_ids.add(unique_id)
            if result.get("status") != "error":
                continue
            failed_node_ids.append(unique_id)
        failed_nodes = tuple(sorted(failed_node_ids))
        if failed_nodes != (committed_truth.direct_failure,):
            raise _clean(LabVerificationError(f"直接失败节点不匹配：{failed_nodes}"))

        manifest = self._read_object(run_root / "dbt/target/manifest.json")
        affected = self._model_descendants(manifest, committed_truth.direct_failure)
        if affected != set(committed_truth.affected_assets):
            raise _clean(LabVerificationError(f"影响模型不匹配：{sorted(affected)}"))

        schema = self._read_object(run_root / "schema.json")
        if set(schema) != _EXPECTED_SCHEMA_KEYS:
            raise _clean(LabVerificationError("Schema 字段集合无效"))
        relations = schema.get("relations")
        if not isinstance(relations, list) or len(relations) != 1:
            raise _clean(LabVerificationError("Schema 快照必须只包含故障关系"))
        relation = relations[0]
        if not isinstance(relation, dict):
            raise _clean(LabVerificationError("Schema relation 无效"))
        if set(relation) != _EXPECTED_RELATION_KEYS:
            raise _clean(LabVerificationError("Schema relation 字段集合无效"))
        if relation.get("name") != committed_truth.expected_schema.relation:
            raise _clean(LabVerificationError("故障 Schema 关系不匹配"))
        columns = relation.get("columns")
        if not isinstance(columns, list):
            raise _clean(LabVerificationError("Schema columns 无效"))
        for column in columns:
            if not isinstance(column, dict):
                raise _clean(LabVerificationError("Schema column 内容无效"))
            if set(column) != _EXPECTED_COLUMN_KEYS:
                raise _clean(LabVerificationError("Schema column 字段集合无效"))
            if (
                not isinstance(column.get("name"), str)
                or not isinstance(column.get("data_type"), str)
                or type(column.get("nullable")) is not bool
                or type(column.get("ordinal_position")) is not int
            ):
                raise _clean(LabVerificationError("Schema column 类型无效"))
        try:
            column_summaries = tuple(
                ColumnSummary(
                    name=column["name"],
                    data_type=column["data_type"],
                    nullable=column["nullable"],
                    ordinal_position=column["ordinal_position"],
                )
                for column in columns
                if isinstance(column, dict)
            )
        except (KeyError, TypeError):
            raise _clean(LabVerificationError("Schema column 内容无效")) from None
        if len(column_summaries) != len(columns):
            raise _clean(LabVerificationError("Schema column 内容无效"))
        expected_columns = tuple(
            (column.name, column.data_type, column.nullable, column.ordinal_position)
            for column in committed_truth.expected_schema.fault_column_metadata
        )
        actual_columns = tuple(
            (column.name, column.data_type, column.nullable, column.ordinal_position)
            for column in column_summaries
        )
        if actual_columns != expected_columns:
            raise _clean(LabVerificationError("故障 Schema 列元数据不匹配"))
        if (
            type(relation.get("row_count")) is not int
            or relation.get("row_count") != committed_truth.expected_schema.row_count
        ):
            raise _clean(LabVerificationError("故障 Schema 行数不匹配"))
        fingerprint = schema.get("fingerprint")
        if not isinstance(fingerprint, str) or re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None:
            raise _clean(LabVerificationError("Schema fingerprint 无效"))
        schema_name = schema.get("schema")
        if not isinstance(schema_name, str):
            raise _clean(LabVerificationError("Schema 名称无效"))
        recomputed = make_baseline_summary(
            schema_name,
            (
                RelationSummary(
                    name=committed_truth.expected_schema.relation,
                    row_count=committed_truth.expected_schema.row_count,
                    columns=column_summaries,
                ),
            ),
        )
        if recomputed.fingerprint != fingerprint:
            raise _clean(LabVerificationError("Schema fingerprint 与内容不一致"))

        dbt_log = run_root / "dbt/logs/dbt.log"
        log_error: LabVerificationError | None = None
        try:
            log_is_valid = dbt_log.is_file() and dbt_log.stat().st_size > 0
        except OSError:
            log_error = LabVerificationError("无法检查 dbt.log")
        if log_error is not None:
            raise _clean(log_error)
        if not log_is_valid:
            raise _clean(LabVerificationError("缺少 dbt.log"))
        for capture in (run_root / "dbt/stdout.log", run_root / "dbt/stderr.log"):
            if not capture.is_file():
                raise _clean(LabVerificationError(f"缺少 {capture.name}"))

        verification = LabVerification(
            status="EXPECTED_FAILURE",
            incident_case_id=case_id,
            run_id=run_id,
            failed_nodes=failed_nodes,
            affected_assets=committed_truth.affected_assets,
            error_category=committed_truth.expected_failure_category,
            schema_fingerprint=fingerprint,
            ground_truth_digest=truth_digest,
        )
        write_error: LabVerificationError | None = None
        try:
            (run_root / "verification.json").write_text(
                verification.to_json(),
                encoding="utf-8",
            )
        except OSError:
            write_error = LabVerificationError("无法写入 verification.json")
        if write_error is not None:
            raise _clean(write_error)
        return verification
