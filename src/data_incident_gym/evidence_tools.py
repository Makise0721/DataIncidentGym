from __future__ import annotations

import json
import re
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar, Literal

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
    RunContextMismatchError,
    RunNotFoundError,
    raise_without_context,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
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
_RUN_FILES = {
    "metadata": Path("metadata.json"),
    "schema": Path("schema.json"),
    "run_results": Path("dbt/target/run_results.json"),
    "manifest": Path("dbt/target/manifest.json"),
}
_STATUSES = {"error", "fail", "pass", "skipped", "success", "warn"}


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


class _RunArtifacts:
    def __init__(self, project_root: Path, run_id: str) -> None:
        self.run_id = run_id
        self.run_root = self._resolve_run_root(project_root, run_id)
        self.metadata = self._read_json("metadata")
        self._validate_metadata()
        self.schema = self._read_json("schema")
        self.run_results = self._read_json("run_results")
        self.manifest = self._read_json("manifest")
        self._validate_schema()
        self._validate_manifest()
        self._validate_run_results()
        self.generated_at = self._read_generated_at()
        self.manifest_generated_at = self._read_manifest_generated_at()

    @staticmethod
    def _resolve_run_root(project_root: Path, run_id: str) -> Path:
        base_candidate = project_root / ".dig" / "lab" / "runs"
        run_candidate = base_candidate / run_id
        try:
            base = base_candidate.resolve(strict=True)
        except OSError:
            raise_without_context(RunNotFoundError("Run artifacts were not found"))
        if base_candidate.is_symlink() or not base.is_dir():
            raise_without_context(RunNotFoundError("Run artifacts were not found"))
        if not run_candidate.exists():
            raise_without_context(RunNotFoundError("Run artifacts were not found"))
        if run_candidate.is_symlink():
            _invalid_artifact()
        try:
            run_root = run_candidate.resolve(strict=True)
        except OSError:
            raise_without_context(RunNotFoundError("Run artifacts were not found"))
        if not run_root.is_dir() or run_root.parent != base:
            _invalid_artifact()
        return run_root

    def _artifact_path(self, name: str) -> Path:
        relative = _RUN_FILES[name]
        candidate = self.run_root / relative
        current = self.run_root
        for part in relative.parts:
            current /= part
            if current.is_symlink():
                _invalid_artifact()
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            _invalid_artifact()
        if not resolved.is_file() or not resolved.is_relative_to(self.run_root):
            _invalid_artifact()
        return resolved

    def _read_json(self, name: str) -> dict[str, Any]:
        path = self._artifact_path(name)
        try:
            payload = path.read_bytes().decode("utf-8")
            value = json.loads(payload, object_pairs_hook=_reject_duplicate_json_keys)
        except (OSError, UnicodeError, json.JSONDecodeError, _DuplicateJsonKey):
            _invalid_artifact()
        if not isinstance(value, dict):
            _invalid_artifact()
        return value

    def _validate_metadata(self) -> None:
        if set(self.metadata) != _EXPECTED_METADATA_KEYS:
            _invalid_artifact()
        if self.metadata.get("schema_version") != "m2.run.v1":
            _invalid_artifact()
        if self.metadata.get("run_id") != self.run_id:
            _invalid_artifact()
        if not isinstance(self.metadata.get("incident_case_id"), str):
            _invalid_artifact()
        if type(self.metadata.get("dbt_exit_code")) is not int:
            _invalid_artifact()
        digest = self.metadata.get("ground_truth_digest")
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            _invalid_artifact()
        artifacts = self.metadata.get("artifacts")
        if artifacts != _EXPECTED_ARTIFACTS:
            _invalid_artifact()

    def _validate_schema(self) -> None:
        if not isinstance(self.schema.get("schema"), str):
            _invalid_artifact()
        relations = self.schema.get("relations")
        fingerprint = self.schema.get("fingerprint")
        if not isinstance(relations, list):
            _invalid_artifact()
        if not isinstance(fingerprint, str) or re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None:
            _invalid_artifact()
        relation_ids: set[str] = set()
        for relation in relations:
            if not isinstance(relation, dict):
                _invalid_artifact()
            name = relation.get("name")
            if not isinstance(name, str) or name in relation_ids:
                _invalid_artifact()
            relation_ids.add(name)
            if type(relation.get("row_count")) is not int:
                _invalid_artifact()
            columns = relation.get("columns")
            if not isinstance(columns, list):
                _invalid_artifact()
            column_ids: set[str] = set()
            ordinal_ids: set[int] = set()
            for column in columns:
                if not isinstance(column, dict):
                    _invalid_artifact()
                column_name = column.get("name")
                ordinal_position = column.get("ordinal_position")
                if (
                    not isinstance(column_name, str)
                    or type(column.get("data_type")) is not str
                    or type(column.get("nullable")) is not bool
                    or type(ordinal_position) is not int
                    or column_name in column_ids
                    or ordinal_position in ordinal_ids
                ):
                    _invalid_artifact()
                column_ids.add(column_name)
                ordinal_ids.add(ordinal_position)

    def _validate_manifest(self) -> None:
        nodes = self.manifest.get("nodes")
        sources = self.manifest.get("sources", {})
        if not isinstance(nodes, dict) or not isinstance(sources, dict):
            _invalid_artifact()
        entries: dict[str, Any] = {}
        for collection in (nodes, sources):
            for node_id, node in collection.items():
                if not isinstance(node_id, str) or node_id in entries:
                    _invalid_artifact()
                if not isinstance(node, dict) or type(node.get("resource_type")) is not str:
                    _invalid_artifact()
                entries[node_id] = node
        for mapping_name in ("parent_map", "child_map"):
            mapping = self.manifest.get(mapping_name)
            if not isinstance(mapping, dict):
                _invalid_artifact()
            for node_id, references in mapping.items():
                if not isinstance(node_id, str) or node_id not in entries:
                    _invalid_artifact()
                if not isinstance(references, list):
                    _invalid_artifact()
                seen: set[str] = set()
                for reference in references:
                    if (
                        not isinstance(reference, str)
                        or reference in seen
                        or reference not in entries
                    ):
                        _invalid_artifact()
                    seen.add(reference)

    def _validate_run_results(self) -> None:
        results = self.run_results.get("results")
        if not isinstance(results, list):
            _invalid_artifact()
        nodes = self.manifest["nodes"]
        sources = self.manifest.get("sources", {})
        known_ids = set(nodes) | set(sources)
        seen: set[str] = set()
        for result in results:
            if not isinstance(result, dict):
                _invalid_artifact()
            node_id = result.get("unique_id")
            status = result.get("status")
            if (
                not isinstance(node_id, str)
                or node_id in seen
                or node_id not in known_ids
                or not isinstance(status, str)
                or status not in _STATUSES
            ):
                _invalid_artifact()
            seen.add(node_id)
            if (
                "message" in result
                and result["message"] is not None
                and type(result["message"]) is not str
            ):
                _invalid_artifact()
            if status in {"error", "fail"} and (
                type(result.get("message")) is not str or not result["message"].strip()
            ):
                _invalid_artifact()

    def _read_generated_at(self) -> datetime:
        metadata = self.run_results.get("metadata")
        if not isinstance(metadata, dict) or type(metadata.get("generated_at")) is not str:
            _invalid_artifact()
        try:
            value = datetime.fromisoformat(metadata["generated_at"].replace("Z", "+00:00"))
        except (TypeError, ValueError):
            _invalid_artifact()
        if value.tzinfo is None or value.utcoffset() is None:
            _invalid_artifact()
        return value

    def _read_manifest_generated_at(self) -> datetime:
        metadata = self.manifest.get("metadata")
        if not isinstance(metadata, dict) or type(metadata.get("generated_at")) is not str:
            _invalid_artifact()
        try:
            value = datetime.fromisoformat(metadata["generated_at"].replace("Z", "+00:00"))
        except (TypeError, ValueError):
            _invalid_artifact()
        if value.tzinfo is None or value.utcoffset() is None:
            _invalid_artifact()
        return value


class EvidenceTools:
    _PROJECT_ROOT: ClassVar[Path] = PROJECT_ROOT

    def __init__(self, run_id: str, artifacts: _RunArtifacts) -> None:
        self._run_id = run_id
        self._artifacts = artifacts

    @classmethod
    def for_run(
        cls,
        run_id: str,
        settings: DiagnosticSettings,
        project_root: Path = PROJECT_ROOT,
    ) -> EvidenceTools:
        del settings
        if not isinstance(run_id, str) or RUN_ID_PATTERN.fullmatch(run_id) is None:
            raise_without_context(InvalidRunIdError("Invalid run identifier"))
        artifacts = _RunArtifacts(project_root, run_id)
        return cls(run_id, artifacts)

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
        exit_code = self._artifacts.metadata["dbt_exit_code"]
        content = DbtRunResultsFact(
            kind="DBT_RUN_RESULTS",
            run_id=self._run_id,
            run_status="SUCCEEDED" if exit_code == 0 else "FAILED",
            dbt_exit_code=exit_code,
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

        mapping_name = "parent_map" if direction == "upstream" else "child_map"
        mapping = self._artifacts.manifest[mapping_name]
        colors: dict[str, int] = {}

        def visit(current_id: str) -> None:
            color = colors.get(current_id, 0)
            if color == 1:
                _invalid_artifact()
            if color == 2:
                return
            entry = catalog[current_id]
            if (
                not isinstance(entry, dict)
                or type(entry.get("resource_type")) is not str
                or type(entry.get("name")) is not str
            ):
                _invalid_artifact()
            colors[current_id] = 1
            if current_id not in mapping or not isinstance(mapping[current_id], list):
                _invalid_artifact()
            references = mapping[current_id]
            if len(references) != len(set(references)):
                _invalid_artifact()
            for reference in references:
                if not isinstance(reference, str) or reference not in catalog:
                    _invalid_artifact()
                visit(reference)
            colors[current_id] = 2

        visit(node_id)

        distances: dict[str, int] = {node_id: 0}
        queue: deque[tuple[str, int]] = deque([(node_id, 0)])
        while queue:
            current_id, distance = queue.popleft()
            for reference in mapping[current_id]:
                if reference not in distances:
                    next_distance = distance + 1
                    distances[reference] = next_distance
                    queue.append((reference, next_distance))

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

    @staticmethod
    def _normalize_message(message: str) -> str:
        normalized = message.replace("\r\n", "\n").replace("\r", "\n").replace("\\", "/")
        lines = [
            line
            for line in normalized.split("\n")
            if not line.strip().lower().startswith("compiled code at ")
        ]
        return "\n".join(lines).strip()
