"""Public-evidence reference solution for scenario certification (T04).

``ReferenceAnalystRunner`` is a deterministic analyst that receives exactly the
public surface a scored strategy receives: the verified run's incident brief,
the runtime relation whitelist and the six read-only evidence tools. It never
reads the private scenario contract, the expected answer, or the case identity
of the run it is investigating; every conclusion must be derivable from the
records the tools returned.

It extends the fixed-rule engine with the generalisations needed to cover the
whole scenario catalogue: explicit missing-column and type-mismatch signatures
for build failures, duplicate detection for failing tests, and affected-asset
expansion to the failed model's accepted downstream lineage. When the decisive
relation is not readable it fails closed and reports the real refusal receipt
plus the transformation gap, which is what the insufficient scenarios expect.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from data_incident_gym.diagnosis import (
    AffectedAssetClaim,
    Diagnosis,
    DiagnosisStatus,
    DiagnosticStrategy,
    PolicyIdentity,
    RootCauseClaim,
    ToolTraceEvent,
)
from data_incident_gym.evidence import (
    DbtLineageFact,
    DbtNodeErrorFact,
    DbtRunResultsFact,
    RelationDataProfileFact,
    RelationHistoryFact,
    RelationSchemaFact,
)
from data_incident_gym.fixed_rule import (
    BASE_PROMPT,
    BASE_PROMPT_VERSION,
    EVIDENCE_TOOLS_V1_VERSION,
    FIXED_RULE_TOOL_LIMIT,
    FixedRuleRunner,
    _digest,
    _relation_names,
    _surface_payload,
    tool_names_for_surface,
    tool_surface_for_context,
)

REFERENCE_ANALYST_VERSION = "p1.reference-analyst.v1"
REFERENCE_ANALYST_TOOL_LIMIT = FIXED_RULE_TOOL_LIMIT
#: The v1 names stay exported for the frozen identity readers; a run's own
#: surface comes from its context (v2 adds the two batch facts).
REFERENCE_ANALYST_TOOL_NAMES = tool_names_for_surface(EVIDENCE_TOOLS_V1_VERSION)

_STRING_TYPES = frozenset({"text", "character varying", "character", "varchar", "string"})
_KEY_COLUMN_PATTERN = re.compile(r"^[a-z0-9]*(?:_[a-z0-9]+)*_?id$", re.IGNORECASE)
_MISSING_COLUMN_PATTERN = re.compile(r'(?i)column "([a-z0-9_]+)" does not exist')
_TYPE_MISMATCH_PATTERN = re.compile(
    r"(?i)(operator does not exist|cannot cast|invalid input syntax|does not exist: \w+)"
)
_IDENTIFIER_PATTERN = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*")


def reference_analyst_policy_identity(
    surface: str = EVIDENCE_TOOLS_V1_VERSION,
) -> PolicyIdentity:
    tools = tool_names_for_surface(surface)
    return PolicyIdentity(
        strategy=DiagnosticStrategy.REFERENCE_ANALYST,
        base_prompt_version=BASE_PROMPT_VERSION,
        base_prompt_sha256=hashlib.sha256(BASE_PROMPT.encode("utf-8")).hexdigest(),
        strategy_prompt_version=REFERENCE_ANALYST_VERSION,
        strategy_prompt_sha256=_digest(
            {
                "policy": REFERENCE_ANALYST_VERSION,
                "rule": "public evidence only, bounded calls, fail closed",
            }
        ),
        controller_protocol_version=REFERENCE_ANALYST_VERSION,
        controller_protocol_sha256=_digest(
            {
                "policy": REFERENCE_ANALYST_VERSION,
                "tool_limit": REFERENCE_ANALYST_TOOL_LIMIT,
                "tools": tools,
                **_surface_payload(surface),
            }
        ),
        tool_schema_sha256=_digest(tools),
    )


class ReferenceAnalystRunner(FixedRuleRunner):
    """Deterministic reference solution driven only by public evidence."""

    @property
    def strategy(self) -> DiagnosticStrategy:
        return DiagnosticStrategy.REFERENCE_ANALYST

    @property
    def _provider_label(self) -> str:
        return "reference-analyst"

    def _build_policy_identity(self) -> PolicyIdentity:
        return reference_analyst_policy_identity(tool_surface_for_context(self._context))

    # ------------------------------------------------------------------
    # Evidence helpers
    # ------------------------------------------------------------------

    def _source_candidates(self, lineage: DbtLineageFact) -> tuple[str, ...]:
        """Upstream seed/source relations, nearest first, then name order."""

        ordered = sorted(
            (
                node
                for node in lineage.related_nodes
                if node.resource_type in {"seed", "source"}
            ),
            key=lambda node: (node.distance, node.name),
        )
        return tuple(dict.fromkeys(node.name for node in ordered))

    def _transformation_subject(
        self,
        lineage: DbtLineageFact,
        source_relation: str | None,
        affected_model: str,
    ) -> str:
        """Pick the transformation definition subject for an unavailable fact.

        Known assumption: the staging model that consumes a seed is named after
        it (``raw_orders`` -> a model whose name contains ``orders``). Renamed
        or ambiguous staging names fall back to the nearest distance-1 model;
        counter-examples for both cases are pinned in
        ``tests/unit/test_reference_solver.py``.
        """

        candidates = tuple(
            node
            for node in lineage.related_nodes
            if node.resource_type == "model" and node.name.startswith("stg_")
        )
        token = (source_relation or "").rsplit("_", 1)[-1].lower()
        if token:
            match = next(
                (node for node in candidates if token and token in node.name.lower()),
                None,
            )
            if match is not None:
                return match.node_id
        return next(
            (
                node.node_id
                for node in candidates
                if node.distance == min(item.distance for item in candidates)
            ),
            next(
                (
                    node.node_id
                    for node in lineage.related_nodes
                    if node.resource_type == "model" and node.distance == 1
                ),
                affected_model,
            ),
        )

    def _evidence_id(self, content_type: type[Any]) -> str | None:
        return next(
            (
                record.evidence_id
                for record in self._records
                if isinstance(record.content, content_type)
            ),
            None,
        )

    def _attempted(self, tool_name: str, subject: str) -> bool:
        return any(
            event.tool_name == tool_name and subject in event.arguments.values()
            for event in self._trace
            if isinstance(event, ToolTraceEvent)
        )

    def _complete_relation_evidence(self, relation: str, types: tuple[str, ...]) -> None:
        """Collect the requested relation-evidence types for the implicated relation.

        The public ledger already projects which allowed relations still lack an
        accepted record; this mirrors that completeness behaviour for the single
        relation the investigation implicates, and never repeats an attempt
        (a refused probe stays a single receipt).
        """

        readers = {
            "schema": ("get_relation_schema", self._schema, RelationSchemaFact),
            "profile": ("get_relation_data_profile", self._profile, RelationDataProfileFact),
            "history": ("get_relation_history", self._history, RelationHistoryFact),
        }
        for name in types:
            tool_name, read, content_type = readers[name]
            if self._evidence_id(content_type) is not None:
                continue
            if self._attempted(tool_name, relation):
                continue
            read(relation)

    def _confirm_with_evidence(
        self,
        *,
        root_cause_code: str,
        assets: tuple[tuple[str, str], ...],
        summary: str,
        action: str,
    ) -> Diagnosis:
        unique_assets = tuple(dict.fromkeys(asset for asset, _ in assets))
        if not unique_assets:
            return self._insufficient(
                (("TRANSFORMATION_DEFINITION", "affected assets", "NOT_OBSERVABLE"),)
            )
        evidence_ids = tuple(record.evidence_id for record in self._records)
        claims = (
            RootCauseClaim(
                kind="ROOT_CAUSE",
                root_cause_code=root_cause_code,
                evidence_ids=evidence_ids,
            ),
            *(
                AffectedAssetClaim(
                    kind="AFFECTED_ASSET",
                    asset=asset,
                    evidence_ids=(evidence_id,),
                )
                for asset, evidence_id in assets
            ),
        )
        return Diagnosis(
            status=DiagnosisStatus.CONFIRMED,
            run_id=self._run_id,
            root_cause_code=root_cause_code,
            summary=summary,
            affected_assets=unique_assets,
            evidence_ids=evidence_ids,
            claims=claims,
            recommended_actions=(action,),
            confidence=0.9,
        )

    def _failed_model_assets(
        self,
        failure_node: str,
        node_error: DbtNodeErrorFact,
        affected_model: str,
    ) -> tuple[tuple[str, str], ...]:
        """The failed model plus every model its downstream lineage names."""

        node_error_id = self._evidence_id(DbtNodeErrorFact)
        downstream = self._lineage(affected_model, "downstream")
        assets: list[tuple[str, str]] = []
        if affected_model == failure_node and node_error_id is not None:
            assets.append((affected_model, node_error_id))
        if downstream is not None:
            downstream_id = next(
                (
                    record.evidence_id
                    for record in self._records
                    if record.content is downstream
                ),
                None,
            )
            downstream_models = tuple(
                node.node_id
                for node in downstream.related_nodes
                if node.resource_type == "model"
            )
            if downstream_models and downstream_id is not None:
                assets.extend(
                    (asset, downstream_id) for asset in downstream_models
                )
        if not assets:
            lineage_id = self._evidence_id(DbtLineageFact)
            if lineage_id is not None:
                assets.append((affected_model, lineage_id))
        return tuple(assets)

    # ------------------------------------------------------------------
    # Public-evidence rules
    # ------------------------------------------------------------------

    def _diagnose_failed(self, run: DbtRunResultsFact) -> Diagnosis:
        if len(run.failed_nodes) != 1:
            return self._insufficient(
                (("TRANSFORMATION_DEFINITION", "failed nodes", "NOT_OBSERVABLE"),)
            )
        failure_node = run.failed_nodes[0]
        node_error = self._node_error(failure_node)
        if node_error is None:
            return self._insufficient(
                (("TRANSFORMATION_DEFINITION", failure_node, "NOT_OBSERVABLE"),)
            )
        lineage = self._lineage(failure_node, "upstream")
        if lineage is None:
            return self._insufficient(
                (("TRANSFORMATION_DEFINITION", failure_node, "NOT_OBSERVABLE"),)
            )

        text = " ".join(
            (failure_node, node_error.message, self._context.incident_brief.summary)
        )
        source_relation = self._source_relation(lineage, text)
        affected_model = self._affected_model(failure_node, node_error, lineage)
        if source_relation is None or affected_model is None:
            return self._insufficient(
                (("TRANSFORMATION_DEFINITION", failure_node, "NOT_OBSERVABLE"),)
            )
        transform_subject = self._transformation_subject(
            lineage, source_relation, affected_model
        )

        if (
            node_error.resource_type == "test"
            or self._context.incident_brief.signal_code == "DBT_TEST_FAILED"
        ):
            return self._diagnose_failed_test(
                source_relation=source_relation,
                transform_subject=transform_subject,
                affected_model=affected_model,
            )
        return self._diagnose_failed_build(
            failure_node=failure_node,
            node_error=node_error,
            lineage=lineage,
            transform_subject=transform_subject,
            affected_model=affected_model,
        )

    def _diagnose_failed_test(
        self,
        *,
        source_relation: str,
        transform_subject: str,
        affected_model: str,
    ) -> Diagnosis:
        profile = self._profile(source_relation)
        if profile is None:
            for relation in _relation_names(self._context, "profile"):
                if relation != source_relation and self._profile(relation) is not None:
                    break
            self._complete_relation_evidence(source_relation, ("schema",))
            return self._insufficient(
                (
                    ("RELATION_DATA_PROFILE", source_relation, "RELATION_NOT_ALLOWED"),
                    ("TRANSFORMATION_DEFINITION", transform_subject, "NOT_OBSERVABLE"),
                )
            )
        self._complete_relation_evidence(source_relation, ("schema",))
        null_fact = next(
            (column for column in profile.snapshot.columns if column.null_count > 0),
            None,
        )
        if null_fact is not None:
            lineage_id = self._evidence_id(DbtLineageFact)
            if lineage_id is None:
                return self._insufficient(
                    (("TRANSFORMATION_DEFINITION", transform_subject, "NOT_OBSERVABLE"),)
                )
            return self._confirm_with_evidence(
                root_cause_code="SOURCE_REQUIRED_FIELD_NULL",
                assets=((affected_model, lineage_id),),
                summary="A required source field contains null values.",
                action="Restore the required source field and rebuild the affected model.",
            )
        key_duplicate = next(
            (
                item
                for item in profile.snapshot.business_key_duplicates
                if item.duplicate_count > 0
            ),
            None,
        )
        fingerprint_duplicate = next(
            (
                item
                for item in profile.snapshot.business_fingerprint_duplicates
                if item.duplicate_count > 0
            ),
            None,
        )
        if key_duplicate is not None or fingerprint_duplicate is not None:
            code = (
                "SOURCE_EXACT_PAYMENT_DUPLICATE"
                if key_duplicate is not None
                else "SOURCE_SEMANTIC_PAYMENT_DUPLICATE"
            )
            lineage_id = self._evidence_id(DbtLineageFact)
            if lineage_id is None:
                return self._insufficient(
                    (("TRANSFORMATION_DEFINITION", transform_subject, "NOT_OBSERVABLE"),)
                )
            return self._confirm_with_evidence(
                root_cause_code=code,
                assets=((affected_model, lineage_id),),
                summary="The failed uniqueness test matches duplicate source records.",
                action="Deduplicate the source records before the next build.",
            )
        return self._insufficient(
            (("TRANSFORMATION_DEFINITION", transform_subject, "NOT_OBSERVABLE"),)
        )

    def _diagnose_failed_build(
        self,
        *,
        failure_node: str,
        node_error: DbtNodeErrorFact,
        lineage: DbtLineageFact,
        transform_subject: str,
        affected_model: str,
    ) -> Diagnosis:
        candidates = self._source_candidates(lineage)
        if not candidates:
            return self._insufficient(
                (("TRANSFORMATION_DEFINITION", transform_subject, "NOT_OBSERVABLE"),)
            )
        message = node_error.message
        missing = _MISSING_COLUMN_PATTERN.search(message)
        tokens = {token.lower() for token in _IDENTIFIER_PATTERN.findall(message)}
        named: list[tuple[str, RelationSchemaFact]] = []
        key_like: list[tuple[str, RelationSchemaFact]] = []
        for relation in candidates:
            schema = self._schema(relation)
            if schema is None:
                self._complete_relation_evidence(relation, ("profile", "history"))
                return self._insufficient(
                    (
                        ("RELATION_SCHEMA", relation, "RELATION_NOT_ALLOWED"),
                        (
                            "TRANSFORMATION_DEFINITION",
                            self._transformation_subject(lineage, relation, affected_model),
                            "NOT_OBSERVABLE",
                        ),
                    )
                )
            columns = {column.name.lower(): column for column in schema.columns}
            if missing is not None and missing.group(1).lower() not in columns:
                return self._confirm_with_evidence(
                    root_cause_code="SOURCE_SCHEMA_COLUMN_RENAMED",
                    assets=self._failed_model_assets(failure_node, node_error, affected_model),
                    summary="A source column referenced by the transformation no longer exists.",
                    action="Restore the renamed source column and rebuild the affected model.",
                )
            named.extend(
                (relation, column)
                for column in schema.columns
                if column.data_type.lower() in _STRING_TYPES
                and column.name.lower() in tokens
            )
            key_like.extend(
                (relation, column)
                for column in schema.columns
                if _KEY_COLUMN_PATTERN.match(column.name)
                and column.data_type.lower() in _STRING_TYPES
            )
        if _TYPE_MISMATCH_PATTERN.search(message):
            if len({item[1].name for item in named}) == 1:
                decisive = named
            elif len({item[0] for item in key_like}) == 1:
                decisive = key_like
            if decisive is not None:
                return self._confirm_with_evidence(
                    root_cause_code="SOURCE_SCHEMA_COLUMN_TYPE_CHANGED",
                    assets=self._failed_model_assets(failure_node, node_error, affected_model),
                    summary="A source column type is incompatible with the failed transformation.",
                    action="Restore the source schema contract and rebuild the affected model.",
                )
        return self._insufficient(
            (("TRANSFORMATION_DEFINITION", transform_subject, "NOT_OBSERVABLE"),)
        )


__all__ = [
    "REFERENCE_ANALYST_TOOL_LIMIT",
    "REFERENCE_ANALYST_TOOL_NAMES",
    "REFERENCE_ANALYST_VERSION",
    "ReferenceAnalystRunner",
    "reference_analyst_policy_identity",
]
