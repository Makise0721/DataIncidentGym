from __future__ import annotations

import pytest
from pydantic import ValidationError

from data_incident_gym.diagnostic_kernel import (
    DiagnosticKernel,
    Hypothesis,
    InvestigationIntent,
)

RUN_ID = "a" * 32
CASE_ID = "synthetic_case"
ONTOLOGY = (
    "SOURCE_SCHEMA_COLUMN_RENAMED",
    "SOURCE_SCHEMA_COLUMN_TYPE_CHANGED",
)


def test_start_exposes_one_small_frozen_state_interface() -> None:
    kernel = DiagnosticKernel.start(
        incident_case_id=CASE_ID,
        run_id=RUN_ID,
        allowed_root_cause_codes=ONTOLOGY,
        model_request_limit=8,
        tool_call_limit=8,
    )
    state = kernel.snapshot(model_requests_used=0)
    assert state.schema_version == "m6.investigation.v1"
    assert state.incident_case_id == CASE_ID
    assert state.run_id == RUN_ID
    assert state.revision == 0
    assert state.hypotheses == ()
    assert state.gaps == ()
    assert state.evidence_inventory == ()
    assert state.model_requests_remaining == 8
    assert state.tool_calls_remaining == 8
    with pytest.raises(ValidationError):
        state.revision = 1


def test_public_models_forbid_extra_fields_and_coercion() -> None:
    with pytest.raises(ValidationError):
        Hypothesis.model_validate(
            {
                "hypothesis_id": "h_rename",
                "root_cause_code": "SOURCE_SCHEMA_COLUMN_RENAMED",
                "extra": True,
            }
        )
    with pytest.raises(ValidationError):
        InvestigationIntent.model_validate(
            {
                "gap_id": "g_failure",
                "gap_kind": "LOCATE_FAILURE",
                "hypothesis_ids": [],
                "new_hypotheses": [],
                "unexpected": "value",
            }
        )
