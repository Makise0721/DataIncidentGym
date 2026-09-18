"""T13 v2 batch evidence tools: E1 expectations and E2 node definitions.

Both tools share one frozen batch protocol:

- the argument is the comma-joined request encoding (request order, deduped by
  first occurrence, at most ``BATCH_TARGET_LIMIT`` targets, empty refused);
- the call is **atomic**: any refused target refuses the whole call, and the
  authoritative detail is the per-target ``(target, code)`` list — different
  codes may appear in one batch (for example ``NODE_NOT_ALLOWED`` alongside
  ``NODE_NOT_FOUND``);
- one call is one tool attempt no matter the list length; one fact record is
  returned per requested target, in request order.

E1 reads the run-bound copy of the trusted healthy baseline (never the global
file) and reports expectations only — it never compares them with anything. E2
reads the run's own artifacts behind two separately checked guarantees: run
membership (recorded digests and the dbt invocation id) and content agreement
across the present sources, with the source precedence frozen as run_results →
compiled file → manifest.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from data_incident_gym.evidence import (
    BatchTargetsRefusedError,
    BatchTooLargeError,
    DbtNodeDefinitionFact,
    EvidenceIntegrityError,
    EvidenceRecord,
    EvidenceSource,
    EvidenceType,
    ExpectedColumn,
    RelationSchemaExpectationFact,
    TargetsEmptyError,
    raise_without_context,
)
from data_incident_gym.run_context import canonical_compiled_text, compiled_tree_digest

#: Frozen batch semantics: at most this many targets per call.
BATCH_TARGET_LIMIT = 8
#: Compiled SQL above this size is returned truncated with ``complete=false``;
#: a partial definition must never feed the column-mapping reader.
MAX_COMPILED_SQL_BYTES = 16 * 1024
EVIDENCE_BASELINE_SCHEMA_VERSION = "p1.evidence_baseline.v1"
_COMPILED_SOURCE_PRECEDENCE = ("run_results", "file", "manifest")
_TIMESTAMP_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")


@dataclass(frozen=True)
class _EvidenceBaseline:
    fingerprint: str
    generated_at: datetime
    relations: dict[str, tuple[ExpectedColumn, ...]]


def truncate_utf8(text: str, limit: int) -> str:
    """Cut ``text`` to at most ``limit`` UTF-8 bytes without splitting a char.

    A character slice (``text[:limit]``) returns up to four times the limit for
    non-ASCII text; the cap is defined in bytes, so the cut happens on the
    encoded form and a trailing partial sequence is dropped.
    """

    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    return encoded[:limit].decode("utf-8", errors="ignore")


def batch_targets(raw: str) -> tuple[str, ...]:
    """The frozen batch request encoding: comma-joined, deduped, in order."""

    if not isinstance(raw, str):
        raise_without_context(TargetsEmptyError("Batch request must be a string"))
    targets = tuple(dict.fromkeys(part for part in raw.split(",") if part))
    if not targets:
        raise_without_context(TargetsEmptyError("Batch request is empty"))
    if len(targets) > BATCH_TARGET_LIMIT:
        raise_without_context(
            BatchTooLargeError(f"Batch request exceeds {BATCH_TARGET_LIMIT} targets")
        )
    return targets


def _parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or _TIMESTAMP_PATTERN.match(value) is None:
        raise_without_context(EvidenceIntegrityError("Baseline timestamp is invalid"))
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise_without_context(EvidenceIntegrityError("Baseline timestamp is invalid"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise_without_context(EvidenceIntegrityError("Baseline timestamp is invalid"))
    return parsed


def parse_evidence_baseline(
    payload: dict[str, Any], binding: dict[str, Any]
) -> _EvidenceBaseline:
    """Strictly parse the run-bound baseline snapshot against its binding."""

    if set(payload) != {"schema_version", "generated_at", "baseline_fingerprint", "relations"}:
        raise_without_context(EvidenceIntegrityError("Baseline snapshot shape is invalid"))
    if payload["schema_version"] != EVIDENCE_BASELINE_SCHEMA_VERSION:
        raise_without_context(EvidenceIntegrityError("Baseline snapshot version is invalid"))
    if payload["baseline_fingerprint"] != binding.get("baseline_fingerprint"):
        raise_without_context(EvidenceIntegrityError("Baseline fingerprint does not match"))
    generated_at = _parse_timestamp(payload["generated_at"])
    relations: dict[str, tuple[ExpectedColumn, ...]] = {}
    raw_relations = payload["relations"]
    if not isinstance(raw_relations, list):
        raise_without_context(EvidenceIntegrityError("Baseline relations are invalid"))
    for relation in raw_relations:
        if not isinstance(relation, dict) or set(relation) != {"name", "columns"}:
            raise_without_context(EvidenceIntegrityError("Baseline relation is invalid"))
        name = relation["name"]
        raw_columns = relation["columns"]
        if not isinstance(name, str) or name in relations or not isinstance(raw_columns, list):
            raise_without_context(EvidenceIntegrityError("Baseline relation is invalid"))
        columns: list[ExpectedColumn] = []
        for column in raw_columns:
            try:
                columns.append(ExpectedColumn.model_validate(column))
            except Exception:
                raise_without_context(EvidenceIntegrityError("Baseline column is invalid"))
        relations[name] = tuple(columns)
    return _EvidenceBaseline(
        fingerprint=str(payload["baseline_fingerprint"]),
        generated_at=generated_at,
        relations=relations,
    )


class BatchEvidenceTools:
    """The two v2 tools over one run's artifacts (no database access)."""

    def __init__(self, run_id: str, artifacts: Any) -> None:
        self._run_id = run_id
        self._artifacts = artifacts

    # -- E1 ---------------------------------------------------------------

    def get_relation_schema_expectation(
        self, relation_names: str
    ) -> tuple[EvidenceRecord, ...]:
        targets = batch_targets(relation_names)
        context = self._artifacts.context
        allowed = set(context.expectation_relations) if context.is_v2 else set()
        refused = [(target, "RELATION_NOT_ALLOWED") for target in targets if target not in allowed]
        if refused:
            raise_without_context(
                BatchTargetsRefusedError(
                    "one or more expectation relations are not granted",
                    target_refusals=tuple(refused),
                )
            )
        baseline = self._evidence_baseline()
        records: list[EvidenceRecord] = []
        for target in targets:
            expected = baseline.relations.get(target)
            content = RelationSchemaExpectationFact(
                kind="RELATION_SCHEMA_EXPECTATION",
                run_id=self._run_id,
                relation_name=target,
                baseline_fingerprint=baseline.fingerprint,
                known=expected is not None,
                columns=() if expected is None else expected,
            )
            records.append(
                EvidenceRecord.create(
                    run_id=self._run_id,
                    evidence_type=EvidenceType.RELATION_SCHEMA_EXPECTATION,
                    source=EvidenceSource.RUN_BASELINE,
                    subject=target,
                    observed_at=baseline.generated_at,
                    content=content,
                )
            )
        return tuple(records)

    # -- E2 ---------------------------------------------------------------

    def get_dbt_node_definition(self, node_ids: str) -> tuple[EvidenceRecord, ...]:
        targets = batch_targets(node_ids)
        context = self._artifacts.context
        granted = set(context.definition_nodes) if context.is_v2 else set()
        catalog = self._catalog()
        closure = self._upstream_closure()

        def refused_code(target: str) -> str | None:
            if target not in granted:
                return "NODE_NOT_ALLOWED"
            if target not in catalog:
                return "NODE_NOT_FOUND"
            if target not in closure:
                return "NODE_NOT_ALLOWED"
            return None

        refusals = [
            (target, code)
            for target in targets
            for code in (refused_code(target),)
            if code is not None
        ]
        if refusals:
            raise_without_context(
                BatchTargetsRefusedError(
                    "one or more definition nodes are not granted",
                    target_refusals=tuple(refusals),
                )
            )
        self._verify_definition_integrity()
        sources = self._node_sources()
        observed_at = self._artifacts.manifest_generated_at
        records: list[EvidenceRecord] = []
        recorded = context.runtime["build_provenance"]["node_definitions"]
        for target in targets:
            node = catalog[target]
            text = self._compiled_text(sources.get(target))
            if text is not None:
                # Run membership for the text itself: it must be the archived
                # definition, and a redaction-modified definition is never
                # complete (recorded at build time, never guessed from ``***``).
                entry = recorded.get(target)
                if entry is None:
                    raise_without_context(
                        EvidenceIntegrityError("Definition is not bound to this build")
                    )
                if hashlib.sha256(text.encode("utf-8")).hexdigest() != entry["sha256"]:
                    raise_without_context(
                        EvidenceIntegrityError("Definition text does not match the build record")
                    )
            complete = (
                text is not None
                and not recorded.get(target, {}).get("redacted", True)
                and len(text.encode("utf-8")) <= MAX_COMPILED_SQL_BYTES
            )
            content = DbtNodeDefinitionFact(
                kind="DBT_NODE_DEFINITION",
                run_id=self._run_id,
                node_id=target,
                known=text is not None,
                resource_type=node.get("resource_type")
                if isinstance(node.get("resource_type"), str)
                else None,
                declared_columns=tuple(
                    sorted(str(name) for name in (node.get("columns") or {}))
                ),
                depends_on=tuple(
                    str(item) for item in (node.get("depends_on") or {}).get("nodes", [])
                ),
                compiled_sql_sha256=None
                if text is None
                else hashlib.sha256(text.encode("utf-8")).hexdigest(),
                compiled_sql=None if text is None else truncate_utf8(text, MAX_COMPILED_SQL_BYTES),
                complete=complete,
            )
            records.append(
                EvidenceRecord.create(
                    run_id=self._run_id,
                    evidence_type=EvidenceType.DBT_NODE_DEFINITION,
                    source=EvidenceSource.DBT_MANIFEST,
                    subject=target,
                    observed_at=observed_at,
                    content=content,
                )
            )
        return tuple(records)

    # -- internals --------------------------------------------------------

    def _catalog(self) -> dict[str, dict[str, Any]]:
        return {
            **self._artifacts.manifest["nodes"],
            **self._artifacts.manifest.get("sources", {}),
        }

    def _upstream_closure(self) -> frozenset[str]:
        parent_map = self._artifacts.manifest["parent_map"]
        failed = [
            result["unique_id"]
            for result in self._artifacts.run_results.get("results", [])
            if isinstance(result, dict)
            and result.get("status") in {"error", "fail"}
            and isinstance(result.get("unique_id"), str)
        ]
        seen: set[str] = set()
        stack = list(failed)
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            stack.extend(parent_map.get(node, ()))
        return frozenset(seen)

    def _evidence_baseline(self) -> _EvidenceBaseline:
        context = self._artifacts.context
        if not context.is_v2:
            raise_without_context(
                EvidenceIntegrityError("Run context carries no v2 baseline binding")
            )
        binding = context.runtime["evidence_baseline"]
        path = context.evidence_baseline_path
        try:
            if path.is_symlink():
                raise_without_context(EvidenceIntegrityError("Baseline snapshot is a symlink"))
            raw = path.read_bytes()
            payload = self._artifacts.read_json(path)
        except OSError:
            raise_without_context(EvidenceIntegrityError("Baseline snapshot is unreadable"))
        if hashlib.sha256(raw).hexdigest() != binding.get("sha256"):
            raise_without_context(EvidenceIntegrityError("Baseline snapshot digest mismatch"))
        return parse_evidence_baseline(payload, binding)

    def _verify_definition_integrity(self) -> None:
        context = self._artifacts.context
        if not context.is_v2:
            raise_without_context(
                EvidenceIntegrityError("Run context carries no v2 provenance binding")
            )
        provenance = context.runtime["build_provenance"]
        recorded = provenance["artifact_sha256"]
        for artefact, path in (
            ("manifest", context.manifest_path),
            ("run_results", context.run_results_path),
        ):
            try:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                raise_without_context(EvidenceIntegrityError("Run artifact is unreadable"))
            if digest != recorded[artefact]:
                raise_without_context(
                    EvidenceIntegrityError("Run artifact does not belong to this build")
                )
        if compiled_tree_digest(self._compiled_root()) != recorded["compiled_tree"]:
            raise_without_context(
                EvidenceIntegrityError("Compiled tree does not belong to this build")
            )
        invocation = provenance["dbt_invocation_id"]
        if (
            self._artifacts.manifest.get("metadata", {}).get("invocation_id") != invocation
            or self._artifacts.run_results.get("metadata", {}).get("invocation_id") != invocation
        ):
            raise_without_context(
                EvidenceIntegrityError("Artifact invocation does not match this build")
            )
        for node_id, sources in self._node_sources().items():
            # Line endings are the platform's, not the SQL's: agree on the
            # canonical text so a Windows compiled file (CRLF) does not look
            # like a different definition from its JSON copy (LF).
            canonical = {
                canonical_compiled_text(text)
                for text in sources.values()
                if text is not None
            }
            if len(canonical) > 1:
                raise_without_context(
                    EvidenceIntegrityError(
                        f"compiled text disagrees across sources: {node_id}"
                    )
                )

    def _compiled_root(self) -> Path:
        return self._artifacts.run_root / "dbt" / "target" / "compiled"

    def _node_sources(self) -> dict[str, dict[str, str | None]]:
        """Compiled text per node from each present source."""

        results_by_id: dict[str, dict[str, Any]] = {}
        for result in self._artifacts.run_results.get("results", []):
            if isinstance(result, dict) and isinstance(result.get("unique_id"), str):
                results_by_id.setdefault(result["unique_id"], result)
        sources: dict[str, dict[str, str | None]] = {}
        for node_id, node in self._artifacts.manifest["nodes"].items():
            if not isinstance(node, dict):
                continue
            entry: dict[str, str | None] = {
                "manifest": None,
                "run_results": None,
                "file": None,
            }
            if isinstance(node.get("compiled_code"), str):
                entry["manifest"] = node["compiled_code"]
            result = results_by_id.get(node_id)
            if result is not None and isinstance(result.get("compiled_code"), str):
                entry["run_results"] = result["compiled_code"]
            compiled_path = node.get("compiled_path")
            if isinstance(compiled_path, str):
                entry["file"] = self._read_compiled_file(compiled_path)
            if any(value is not None for value in entry.values()):
                sources[node_id] = entry
        return sources

    def _read_compiled_file(self, compiled_path: str) -> str | None:
        run_root = self._artifacts.run_root
        candidate = Path(compiled_path)
        if not candidate.is_absolute():
            candidate = run_root / candidate
        try:
            if candidate.is_symlink():
                raise_without_context(
                    EvidenceIntegrityError("compiled path must not be a symlink")
                )
            resolved = candidate.resolve(strict=True)
        except FileNotFoundError:
            return None
        except OSError:
            raise_without_context(EvidenceIntegrityError("compiled file is unreadable"))
        if not resolved.is_file() or not resolved.is_relative_to(run_root):
            raise_without_context(EvidenceIntegrityError("compiled path escaped the run"))
        try:
            return resolved.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError):
            raise_without_context(EvidenceIntegrityError("compiled file is unreadable"))

    @staticmethod
    def _compiled_text(sources: dict[str, str | None] | None) -> str | None:
        if sources is None:
            return None
        for name in _COMPILED_SOURCE_PRECEDENCE:
            text = sources.get(name)
            if text is not None:
                return text
        return None
