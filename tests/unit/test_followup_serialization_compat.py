"""Serialization compatibility for fields added by the p1-v8 followup.

``InvestigationState.lineage_node_candidates`` and the trace event fields
``ModelProtocolTraceEvent.error_loc/error_kind`` are additive with defaults:
records written before this change parse with the new models, and new records
carry the keys explicitly.
"""

from __future__ import annotations

import json

from data_incident_gym.diagnosis import EvidenceGateTraceEvent, ModelProtocolTraceEvent
from data_incident_gym.diagnostic_contracts import InvestigationState

RUN_ID = "a" * 32


def _legacy_investigation_state_json() -> str:
    return json.dumps(
        {
            "schema_version": "p1.investigation.v1",
            "run_id": RUN_ID,
            "revision": 0,
            "allowed_root_cause_codes": [
                "SOURCE_SCHEMA_COLUMN_TYPE_CHANGED",
                "SOURCE_SCHEMA_COLUMN_RENAMED",
            ],
            "hypotheses": [],
            "gaps": [],
            "assessments": [],
            "claims": [],
            "evidence_inventory": [],
            "tool_fingerprints": [],
            "model_request_limit": 8,
            "model_requests_used": 0,
            "model_requests_remaining": 8,
            "tool_call_limit": 8,
            "tool_calls_used": 0,
            "tool_calls_remaining": 8,
            "final_status": None,
            "gate_reason": None,
            "selected_hypothesis_id": None,
        }
    )


def _legacy_protocol_event_json() -> str:
    return json.dumps(
        {
            "event_type": "MODEL_PROTOCOL",
            "stage": "OUTPUT_SCHEMA_VALIDATION",
            "tool_name": "final_result",
            "category": "OUTPUT_SCHEMA_REJECTED",
        }
    )


def test_legacy_investigation_state_parses_with_new_defaults() -> None:
    state = InvestigationState.model_validate_json(_legacy_investigation_state_json())

    assert state.lineage_node_candidates == ()
    assert state.final_status is None


def test_new_investigation_state_serialization_carries_the_key() -> None:
    state = InvestigationState.model_validate_json(_legacy_investigation_state_json())
    dumped = json.loads(state.model_dump_json())

    assert dumped["lineage_node_candidates"] == []


def test_legacy_protocol_event_parses_with_new_defaults() -> None:
    event = ModelProtocolTraceEvent.model_validate_json(_legacy_protocol_event_json())

    assert event.error_loc == ()
    assert event.error_kind == ()


def test_new_protocol_event_serialization_carries_the_keys() -> None:
    event = ModelProtocolTraceEvent.model_validate_json(_legacy_protocol_event_json())
    dumped = json.loads(event.model_dump_json())

    assert dumped["error_loc"] == []
    assert dumped["error_kind"] == []


def _legacy_gate_event_json() -> str:
    return json.dumps(
        {
            "event_type": "EVIDENCE_GATE",
            "reason_code": "ROOT_CLAIM_EVIDENCE_INCOMPATIBLE",
            "accepted": False,
        }
    )


def test_legacy_gate_event_parses_with_rejected_decision_default() -> None:
    event = EvidenceGateTraceEvent.model_validate_json(_legacy_gate_event_json())

    assert event.rejected_decision is None


def test_new_gate_event_serialization_carries_rejected_decision_key() -> None:
    event = EvidenceGateTraceEvent.model_validate_json(_legacy_gate_event_json())
    dumped = json.loads(event.model_dump_json())

    assert "rejected_decision" in dumped
    assert dumped["rejected_decision"] is None


def _pre_truncation_summary_json() -> str:
    """A rejected summary written before the truncated counters existed."""

    return json.dumps(
        {
            "schema_version": "p1.rejected_decision.v1",
            "model_request_index": 3,
            "status": "CONFIRMED",
            "selected_hypothesis_id": "h_ingestion_loss",
            "assessments": [{"hypothesis_id": "h_ingestion_loss", "verdict": "SUPPORTED"}],
            "claims": [
                {
                    "kind": "ROOT_CAUSE",
                    "known_value": "SOURCE_PAYMENT_INGESTION_LOSS",
                    "relation_name": None,
                    "evidence_ids": ["ev_" + "0" * 64],
                }
            ],
            "unresolved_evidence": [],
            "unknown_hypothesis_count": 0,
            "unknown_claim_count": 0,
            "unknown_evidence_count": 1,
            "unknown_subject_count": 0,
            "total_assessments": 1,
            "total_claims": 18,
            "total_unresolved": 0,
            "truncated": True,
        }
    )


def test_pre_truncation_summary_parses_with_zero_counters() -> None:
    """Records written before the truncated counters keep parsing; their
    ``truncated`` flag stays authoritative and the counters default to zero."""

    event = EvidenceGateTraceEvent.model_validate(
        {
            "event_type": "EVIDENCE_GATE",
            "reason_code": "ROOT_CLAIM_EVIDENCE_INCOMPATIBLE",
            "accepted": False,
            "rejected_decision": json.loads(_pre_truncation_summary_json()),
        }
    )
    summary = event.rejected_decision

    assert summary is not None
    assert summary.truncated is True
    assert summary.total_claims == 18
    assert summary.truncated_assessment_count == 0
    assert summary.truncated_claim_count == 0
    assert summary.truncated_unresolved_count == 0
    assert summary.truncated_evidence_count == 0
