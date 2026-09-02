from __future__ import annotations

import inspect

import pytest

from data_incident_gym.baseline import ColumnSummary, RelationSummary, make_baseline_summary
from data_incident_gym.evidence import InvalidArtifactError
from data_incident_gym.evidence_tools import EvidenceTools, _RunArtifacts


def test_business_tool_signatures_are_exactly_the_six_public_operations() -> None:
    expected = {
        "get_dbt_run_results": ("run_id",),
        "get_dbt_node_error": ("run_id", "node_id"),
        "get_relation_schema": ("relation_name",),
        "get_dbt_lineage": ("node_id", "direction"),
        "get_relation_data_profile": ("relation_name",),
        "get_relation_history": ("relation_name",),
    }

    for name, parameters in expected.items():
        actual = tuple(inspect.signature(getattr(EvidenceTools, name)).parameters)
        assert actual[1:] == parameters


def test_schema_artifact_fingerprint_is_recomputed() -> None:
    relation = RelationSummary(
        name="raw_payments",
        row_count=113,
        columns=(
            ColumnSummary(
                name="amount",
                data_type="integer",
                nullable=True,
                ordinal_position=4,
            ),
        ),
    )
    fingerprint = make_baseline_summary("analytics", (relation,)).fingerprint
    reader = object.__new__(_RunArtifacts)
    reader.schema = {
        "schema": "analytics",
        "fingerprint": fingerprint,
        "relations": [
            {
                "name": "raw_payments",
                "row_count": 113,
                "columns": [
                    {
                        "name": "amount",
                        "data_type": "integer",
                        "nullable": True,
                        "ordinal_position": 4,
                    }
                ],
            }
        ],
    }
    reader._validate_schema()

    reader.schema["fingerprint"] = "a" * 64
    with pytest.raises(InvalidArtifactError):
        reader._validate_schema()
