from __future__ import annotations

import json
import re
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, Literal

import psycopg

from data_incident_gym.baseline import (
    ColumnSummary,
    RelationSummary,
    make_baseline_summary,
)
from data_incident_gym.diagnostic_config import DiagnosticSettings
from data_incident_gym.evidence import (
    DbtLineageFact,
    DbtLineageNode,
    DbtNodeErrorFact,
    DbtRunResultsFact,
    EvidenceRecord,
    EvidenceSource,
    EvidenceType,
    InvalidArtifactError,
    InvalidDirectionError,
    InvalidRunIdError,
    NodeErrorNotFoundError,
    NodeNotFoundError,
    ProfileMetricUnavailableError,
    ProfileOutputLimitError,
    ProfileSnapshotMismatchError,
    ProfileSpecInvalidError,
    ReadOnlyDatabaseError,
    RelationDataProfileFact,
    RelationHistoryFact,
    RelationNotAllowedError,
    RelationNotFoundError,
    RelationSchemaColumn,
    RelationSchemaFact,
    RunContextMismatchError,
    RunNotFoundError,
    RunStateDriftError,
    raise_without_context,
)
from data_incident_gym.profiles import (
    AggregateSnapshotReader,
    ProfileError,
    ProfileSnapshot,
    RelationHistorySnapshot,
    RelationProfileSnapshot,
    load_profile_snapshot,
    load_profile_spec,
)
from data_incident_gym.run_context import (
    RunContextError,
    resolve_run_context,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUN_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_STATUSES = {"error", "fail", "pass", "skipped", "success", "warn"}
_IDENTIFIER_PATTERN = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


class _DuplicateJsonKey(ValueError):
    pass


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey
        result[key] = value
    return result


def _invalid_artifact() -> None:
    raise_without_context(InvalidArtifactError("Invalid run artifact"))


def _safe_identifier(value: object) -> str:
    if not isinstance(value, str) or _IDENTIFIER_PATTERN.fullmatch(value) is None:
        raise_without_context(RelationNotAllowedError("Relation is not allowed"))
    return value


class _RunArtifacts:
    def __init__(
        self,
        project_root: Path,
        run_id: str,
    ) -> None:
        try:
            self.context = resolve_run_context(run_id, project_root)
        except RunContextError:
            raise_without_context(RunNotFoundError("Run artifacts were not found"))
        self.run_id = run_id
        self.run_root = self.context.artifact_dir
        self.schema = self._read_json(self.context.schema_path)
        self.run_results = self._read_json(self.context.run_results_path)
        self.manifest = self._read_json(self.context.manifest_path)
        self.profile_snapshot = self._read_profile_snapshot()
        self.profile_spec = self._read_profile_spec()
        self._validate_schema()
        self._validate_manifest()
        self._validate_run_results()
        self.generated_at = self._read_generated_at(self.run_results)
        self.manifest_generated_at = self._read_generated_at(self.manifest)

    def _artifact_path(self, path: Path) -> Path:
        try:
            if path.is_symlink():
                _invalid_artifact()
            resolved = path.resolve(strict=True)
        except OSError:
            _invalid_artifact()
        if not resolved.is_file() or not resolved.is_relative_to(self.run_root):
            _invalid_artifact()
        return resolved

    def _read_json(self, path: Path) -> dict[str, Any]:
        path = self._artifact_path(path)
        try:
            payload = json.loads(
                path.read_bytes().decode("utf-8"),
                object_pairs_hook=_reject_duplicate_json_keys,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
            )
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError, _DuplicateJsonKey):
            _invalid_artifact()
        if not isinstance(payload, dict):
            _invalid_artifact()
        return payload

    def _read_profile_snapshot(self) -> ProfileSnapshot:
        try:
            return load_profile_snapshot(self._artifact_path(self.context.profile_snapshot_path))
        except ProfileError:
            raise_without_context(ProfileSpecInvalidError("Invalid profile snapshot"))

    def _read_profile_spec(self):
        try:
            spec = load_profile_spec(self.context.artifact_dir.parents[3])
        except (ProfileError, OSError):
            raise_without_context(ProfileSpecInvalidError("Invalid ProfileSpec"))
        if spec.digest() != self.context.runtime["profile_spec_sha256"]:
            raise_without_context(ProfileSpecInvalidError("ProfileSpec hash does not match run"))
        if self.profile_snapshot.profile_spec_sha256 != spec.digest():
            raise_without_context(ProfileSnapshotMismatchError("Profile snapshot hash drifted"))
        return spec

    def _validate_schema(self) -> None:
        relations = self.schema.get("relations")
        if not isinstance(self.schema.get("schema"), str) or not isinstance(relations, list):
            _invalid_artifact()
        fingerprint = self.schema.get("fingerprint")
        if not isinstance(fingerprint, str) or re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None:
            _invalid_artifact()
        relation_names: set[str] = set()
        summaries: list[RelationSummary] = []
        for relation in relations:
            if not isinstance(relation, dict) or set(relation) != {
                "name",
                "row_count",
                "columns",
            }:
                _invalid_artifact()
            name = relation.get("name")
            if not isinstance(name, str) or name in relation_names:
                _invalid_artifact()
            relation_names.add(name)
            if type(relation.get("row_count")) is not int or relation["row_count"] < 0:
                _invalid_artifact()
            columns = relation.get("columns")
            if not isinstance(columns, list):
                _invalid_artifact()
            column_names: set[str] = set()
            ordinal_positions: set[int] = set()
            summary_columns: list[ColumnSummary] = []
            for column in columns:
                if not isinstance(column, dict) or set(column) != {
                    "name",
                    "data_type",
                    "nullable",
                    "ordinal_position",
                }:
                    _invalid_artifact()
                name = column.get("name")
                ordinal = column.get("ordinal_position")
                if (
                    not isinstance(name, str)
                    or name in column_names
                    or type(column.get("data_type")) is not str
                    or type(column.get("nullable")) is not bool
                    or type(ordinal) is not int
                    or ordinal <= 0
                    or ordinal in ordinal_positions
                ):
                    _invalid_artifact()
                column_names.add(name)
                ordinal_positions.add(ordinal)
                summary_columns.append(
                    ColumnSummary(
                        name=name,
                        data_type=column["data_type"],
                        nullable=column["nullable"],
                        ordinal_position=ordinal,
                    )
                )
            summaries.append(
                RelationSummary(
                    name=relation["name"],
                    row_count=relation["row_count"],
                    columns=tuple(summary_columns),
                )
            )
        try:
            expected_fingerprint = make_baseline_summary(
                self.schema["schema"], summaries
            ).fingerprint
        except (TypeError, ValueError):
            _invalid_artifact()
        if fingerprint != expected_fingerprint:
            _invalid_artifact()

    def _validate_manifest(self) -> None:
        nodes = self.manifest.get("nodes")
        sources = self.manifest.get("sources", {})
        if not isinstance(nodes, dict) or not isinstance(sources, dict):
            _invalid_artifact()
        entries: dict[str, Any] = {}
        for collection in (nodes, sources):
            for node_id, node in collection.items():
                if (
                    not isinstance(node_id, str)
                    or node_id in entries
                    or not isinstance(node, dict)
                    or type(node.get("resource_type")) is not str
                    or type(node.get("name")) is not str
                ):
                    _invalid_artifact()
                entries[node_id] = node
        for mapping_name in ("parent_map", "child_map"):
            mapping = self.manifest.get(mapping_name)
            if not isinstance(mapping, dict) or set(mapping) != set(entries):
                _invalid_artifact()
            for _node_id, references in mapping.items():
                if not isinstance(references, list) or len(references) != len(set(references)):
                    _invalid_artifact()
                if any(
                    not isinstance(reference, str) or reference not in entries
                    for reference in references
                ):
                    _invalid_artifact()

    def _validate_run_results(self) -> None:
        results = self.run_results.get("results")
        nodes = self.manifest.get("nodes", {})
        sources = self.manifest.get("sources", {})
        if (
            not isinstance(results, list)
            or not isinstance(nodes, dict)
            or not isinstance(sources, dict)
        ):
            _invalid_artifact()
        known = set(nodes) | set(sources)
        seen: set[str] = set()
        for result in results:
            if not isinstance(result, dict):
                _invalid_artifact()
            node_id = result.get("unique_id")
            status = result.get("status")
            if (
                not isinstance(node_id, str)
                or node_id in seen
                or node_id not in known
                or not isinstance(status, str)
                or status not in _STATUSES
            ):
                _invalid_artifact()
            seen.add(node_id)
            if status in {"error", "fail"} and (
                not isinstance(result.get("message"), str) or not result["message"].strip()
            ):
                _invalid_artifact()

    @staticmethod
    def _read_generated_at(payload: dict[str, Any]) -> datetime:
        metadata = payload.get("metadata")
        generated_at = metadata.get("generated_at") if isinstance(metadata, dict) else None
        if not isinstance(generated_at, str):
            _invalid_artifact()
        try:
            value = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
        except ValueError:
            _invalid_artifact()
        if value.tzinfo is None or value.utcoffset() is None:
            _invalid_artifact()
        return value


class EvidenceTools:
    _PROJECT_ROOT: ClassVar[Path] = PROJECT_ROOT

    def __init__(
        self,
        run_id: str,
        artifacts: _RunArtifacts,
        settings: DiagnosticSettings,
        db_connect: Callable[..., Any],
    ) -> None:
        self._run_id = run_id
        self._artifacts = artifacts
        self._settings = settings
        self._db_connect = db_connect

    @classmethod
    def for_run(
        cls,
        run_id: str,
        settings: DiagnosticSettings,
        project_root: Path = PROJECT_ROOT,
        *,
        db_connect: Callable[..., Any] | None = None,
    ) -> EvidenceTools:
        if not isinstance(run_id, str) or RUN_ID_PATTERN.fullmatch(run_id) is None:
            raise_without_context(InvalidRunIdError("Invalid run identifier"))
        return cls(
            run_id,
            _RunArtifacts(project_root, run_id),
            settings,
            db_connect or psycopg.connect,
        )

    def _validate_context(self, run_id: str) -> None:
        if not isinstance(run_id, str) or RUN_ID_PATTERN.fullmatch(run_id) is None:
            raise_without_context(InvalidRunIdError("Invalid run identifier"))
        if run_id != self._run_id:
            raise_without_context(RunContextMismatchError("Run context does not match"))

    def get_dbt_run_results(self, run_id: str) -> tuple[EvidenceRecord, ...]:
        self._validate_context(run_id)
        results = self._artifacts.run_results["results"]
        failed_nodes = tuple(
            sorted(
                result["unique_id"]
                for result in results
                if result["status"] in {"error", "fail"}
            )
        )
        skipped_nodes = tuple(
            sorted(result["unique_id"] for result in results if result["status"] == "skipped")
        )
        content = DbtRunResultsFact(
            kind="DBT_RUN_RESULTS",
            run_id=self._run_id,
            run_status=(
                "SUCCEEDED"
                if self._artifacts.context.runtime["dbt_exit_code"] == 0
                else "FAILED"
            ),
            dbt_exit_code=self._artifacts.context.runtime["dbt_exit_code"],
            failed_nodes=failed_nodes,
            skipped_nodes=skipped_nodes,
        )
        return (
            EvidenceRecord.create(
                run_id=self._run_id,
                evidence_type=EvidenceType.DBT_RUN_RESULTS,
                source=EvidenceSource.DBT_RUN_RESULTS,
                subject=self._run_id,
                observed_at=self._artifacts.generated_at,
                content=content,
            ),
        )

    def get_dbt_node_error(self, run_id: str, node_id: str) -> tuple[EvidenceRecord, ...]:
        self._validate_context(run_id)
        if not isinstance(node_id, str):
            raise_without_context(NodeErrorNotFoundError("Node error was not found"))
        result = next(
            (
                item
                for item in self._artifacts.run_results["results"]
                if item["unique_id"] == node_id
            ),
            None,
        )
        if result is None or result["status"] not in {"error", "fail"}:
            raise_without_context(NodeErrorNotFoundError("Node error was not found"))
        manifest_node = self._artifacts.manifest["nodes"].get(node_id)
        if not isinstance(manifest_node, dict):
            raise_without_context(NodeErrorNotFoundError("Node error was not found"))
        message = self._normalize_message(result["message"])
        if not message:
            raise_without_context(InvalidArtifactError("Invalid node error artifact"))
        content = DbtNodeErrorFact(
            kind="DBT_NODE_ERROR",
            run_id=self._run_id,
            node_id=node_id,
            resource_type=manifest_node["resource_type"],
            status=result["status"],
            message=message,
        )
        return (
            EvidenceRecord.create(
                run_id=self._run_id,
                evidence_type=EvidenceType.DBT_NODE_ERROR,
                source=EvidenceSource.DBT_RUN_RESULTS,
                subject=node_id,
                observed_at=self._artifacts.generated_at,
                content=content,
            ),
        )

    def get_dbt_lineage(
        self,
        node_id: str,
        direction: Literal["upstream", "downstream"],
    ) -> tuple[EvidenceRecord, ...]:
        if not isinstance(direction, str) or direction not in {"upstream", "downstream"}:
            raise_without_context(InvalidDirectionError("Invalid lineage direction"))
        nodes = self._artifacts.manifest["nodes"]
        sources = self._artifacts.manifest.get("sources", {})
        catalog = {**nodes, **sources}
        if not isinstance(node_id, str) or node_id not in catalog:
            raise_without_context(NodeNotFoundError("Lineage node was not found"))
        mapping = self._artifacts.manifest["parent_map" if direction == "upstream" else "child_map"]
        colors: dict[str, int] = {}

        def visit(current_id: str) -> None:
            color = colors.get(current_id, 0)
            if color == 1:
                _invalid_artifact()
            if color == 2:
                return
            colors[current_id] = 1
            for reference in mapping[current_id]:
                visit(reference)
            colors[current_id] = 2

        visit(node_id)
        distances: dict[str, int] = {node_id: 0}
        queue: deque[tuple[str, int]] = deque([(node_id, 0)])
        while queue:
            current_id, distance = queue.popleft()
            for reference in mapping[current_id]:
                if reference not in distances:
                    distances[reference] = distance + 1
                    queue.append((reference, distance + 1))
        related_nodes = tuple(
            DbtLineageNode(
                node_id=related_id,
                resource_type=catalog[related_id]["resource_type"],
                name=catalog[related_id]["name"],
                distance=distance,
            )
            for related_id, distance in sorted(
                distances.items(), key=lambda item: (item[1], item[0])
            )
            if related_id != node_id
            and catalog[related_id].get("resource_type") in {"model", "seed", "source"}
        )
        content = DbtLineageFact(
            kind="DBT_LINEAGE",
            run_id=self._run_id,
            node_id=node_id,
            direction=direction,
            related_nodes=related_nodes,
        )
        return (
            EvidenceRecord.create(
                run_id=self._run_id,
                evidence_type=EvidenceType.DBT_LINEAGE,
                source=EvidenceSource.DBT_MANIFEST,
                subject=node_id,
                observed_at=self._artifacts.manifest_generated_at,
                content=content,
            ),
        )

    def _schema_relation(self, relation_name: str) -> dict[str, Any]:
        relation_name = _safe_identifier(relation_name)
        allowed = self._artifacts.context.runtime["observable_relations"]["schema"]
        if relation_name not in allowed:
            raise_without_context(RelationNotAllowedError("Relation is not allowed"))
        relation = next(
            (
                item
                for item in self._artifacts.schema["relations"]
                if isinstance(item, dict) and item.get("name") == relation_name
            ),
            None,
        )
        if relation is None:
            raise_without_context(RelationNotAllowedError("Relation is not allowed"))
        if not self._manifest_has_relation(relation_name):
            raise_without_context(RelationNotAllowedError("Relation is not allowed"))
        return relation

    def _manifest_has_relation(self, relation_name: str) -> bool:
        catalog = {
            **self._artifacts.manifest.get("nodes", {}),
            **self._artifacts.manifest.get("sources", {}),
        }
        return any(
            entry.get("name") == relation_name
            for entry in catalog.values()
            if isinstance(entry, dict)
        )

    def _read_only_connection(self) -> Any:
        password = self._settings.postgres_password.get_secret_value()
        return self._db_connect(
            host=self._settings.postgres_host,
            port=self._settings.postgres_port,
            dbname=self._settings.postgres_database,
            user=self._settings.postgres_user,
            password=password,
        )

    def get_relation_schema(self, relation_name: str) -> tuple[EvidenceRecord, ...]:
        relation = self._schema_relation(relation_name)
        configured_schema = self._settings.postgres_schema
        if self._artifacts.schema.get("schema") != configured_schema:
            raise_without_context(RunStateDriftError("Run schema does not match configuration"))
        password = self._settings.postgres_password.get_secret_value()
        try:
            with self._read_only_connection() as connection, connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION READ ONLY")
                cursor.execute("SHOW transaction_read_only")
                if cursor.fetchone() != ("on",):
                    raise RuntimeError("database did not enable read-only transaction")
                cursor.execute(
                    "SELECT column_name, data_type, is_nullable, ordinal_position "
                    "FROM information_schema.columns WHERE table_schema = %s AND table_name = %s "
                    "ORDER BY ordinal_position",
                    (configured_schema, relation_name),
                )
                rows = cursor.fetchall()
                if not rows:
                    raise RelationNotFoundError("Relation was not found")
                live_columns = tuple(
                    (row[0], row[1], row[2] == "YES", row[3])
                    for row in rows
                    if len(row) == 4 and row[2] in {"YES", "NO"}
                )
                snapshot_columns = tuple(
                    (
                        column["name"],
                        column["data_type"],
                        column["nullable"],
                        column["ordinal_position"],
                    )
                    for column in relation["columns"]
                )
                if live_columns != snapshot_columns:
                    raise RunStateDriftError("Run schema state drifted")
                cursor.execute("SELECT CURRENT_TIMESTAMP")
                observed = cursor.fetchone()
                if not observed or not isinstance(observed[0], datetime):
                    raise RuntimeError("database timestamp was not returned")
                content = RelationSchemaFact(
                    kind="RELATION_SCHEMA",
                    run_id=self._run_id,
                    schema_name=configured_schema,
                    relation_name=relation_name,
                    columns=tuple(
                        RelationSchemaColumn(
                            name=name,
                            data_type=data_type,
                            nullable=nullable,
                            ordinal_position=ordinal_position,
                        )
                        for name, data_type, nullable, ordinal_position in live_columns
                    ),
                )
                return (
                    EvidenceRecord.create(
                        run_id=self._run_id,
                        evidence_type=EvidenceType.RELATION_SCHEMA,
                        source=EvidenceSource.POSTGRES_CATALOG,
                        subject=f"{configured_schema}.{relation_name}",
                        observed_at=observed[0],
                        content=content,
                    ),
                )
        except (RelationNotFoundError, RunStateDriftError):
            raise
        except Exception as exc:
            message = str(exc).replace(password, "***") if password else str(exc)
            raise_without_context(
                ReadOnlyDatabaseError(f"Read-only database query failed: {message}")
            )

    def _profile_relation(self, relation_name: str, kind: Literal["profile", "history"]) -> str:
        relation_name = _safe_identifier(relation_name)
        if relation_name not in self._artifacts.context.runtime["observable_relations"][kind]:
            raise_without_context(RelationNotAllowedError("Relation is not allowed"))
        if not self._manifest_has_relation(relation_name):
            raise_without_context(RelationNotAllowedError("Relation is not allowed"))
        try:
            self._artifacts.profile_spec.relation(relation_name)
        except ProfileError:
            raise_without_context(ProfileSpecInvalidError("Relation is not in ProfileSpec"))
        return relation_name

    def _reader(self) -> AggregateSnapshotReader:
        return AggregateSnapshotReader(
            schema_name=self._settings.postgres_schema,
            spec=self._artifacts.profile_spec,
            db_connect=self._db_connect,
            connection_kwargs={
                "host": self._settings.postgres_host,
                "port": self._settings.postgres_port,
                "dbname": self._settings.postgres_database,
                "user": self._settings.postgres_user,
                "password": self._settings.postgres_password.get_secret_value(),
            },
            read_only=True,
        )

    def get_relation_data_profile(self, relation_name: str) -> tuple[EvidenceRecord, ...]:
        relation_name = self._profile_relation(relation_name, "profile")
        expected = next(
            (
                item
                for item in self._artifacts.profile_snapshot.current
                if item.relation_name == relation_name
            ),
            None,
        )
        if expected is None:
            raise_without_context(ProfileMetricUnavailableError("Profile metric is unavailable"))
        try:
            actual = self._reader().read_current(relation_name)
        except ProfileError as exc:
            raise_without_context(ProfileMetricUnavailableError(str(exc)))
        if actual != expected:
            raise_without_context(
                ProfileSnapshotMismatchError("Profile snapshot does not match live data")
            )
        self._check_profile_limits(actual)
        content = RelationDataProfileFact(
            kind="RELATION_DATA_PROFILE",
            run_id=self._run_id,
            relation_name=relation_name,
            profile_spec_version=self._artifacts.profile_spec.schema_version,
            profile_spec_sha256=self._artifacts.profile_spec.digest(),
            snapshot=actual,
        )
        return (
            EvidenceRecord.create(
                run_id=self._run_id,
                evidence_type=EvidenceType.RELATION_DATA_PROFILE,
                source=EvidenceSource.POSTGRES_PROFILE_SNAPSHOT,
                subject=relation_name,
                observed_at=datetime.now(UTC),
                content=content,
            ),
        )

    def get_relation_history(self, relation_name: str) -> tuple[EvidenceRecord, ...]:
        relation_name = self._profile_relation(relation_name, "history")
        expected = next(
            (
                item
                for item in self._artifacts.profile_snapshot.history
                if item.relation_name == relation_name
            ),
            None,
        )
        if expected is None or not expected.histories:
            raise_without_context(ProfileMetricUnavailableError("History metric is unavailable"))
        try:
            actual = self._reader().read_history(relation_name)
        except ProfileError as exc:
            raise_without_context(ProfileMetricUnavailableError(str(exc)))
        if actual != expected:
            raise_without_context(
                ProfileSnapshotMismatchError("History snapshot does not match live data")
            )
        self._check_history_limits(actual)
        content = RelationHistoryFact(
            kind="RELATION_HISTORY",
            run_id=self._run_id,
            relation_name=relation_name,
            profile_spec_version=self._artifacts.profile_spec.schema_version,
            profile_spec_sha256=self._artifacts.profile_spec.digest(),
            snapshot=actual,
        )
        return (
            EvidenceRecord.create(
                run_id=self._run_id,
                evidence_type=EvidenceType.RELATION_HISTORY,
                source=EvidenceSource.POSTGRES_PROFILE_SNAPSHOT,
                subject=relation_name,
                observed_at=datetime.now(UTC),
                content=content,
            ),
        )

    def _check_profile_limits(self, snapshot: RelationProfileSnapshot) -> None:
        if any(
            len(group.values) > self._artifacts.profile_spec.max_group_rows
            for group in snapshot.groups
        ):
            raise_without_context(ProfileOutputLimitError("Profile group output exceeds limit"))

    def _check_history_limits(self, snapshot: RelationHistorySnapshot) -> None:
        if any(
            len(series.points) > self._artifacts.profile_spec.max_history_points
            for series in snapshot.histories
        ):
            raise_without_context(ProfileOutputLimitError("Profile history output exceeds limit"))

    @staticmethod
    def _normalize_message(message: str) -> str:
        normalized = message.replace("\r\n", "\n").replace("\r", "\n").replace("\\", "/")
        lines = [
            line
            for line in normalized.split("\n")
            if not line.strip().lower().startswith("compiled code at ")
        ]
        return "\n".join(lines).strip()
