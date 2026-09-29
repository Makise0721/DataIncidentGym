"""T13 slice 4: the v2 tool surface and its identities.

Everything here is offline. The point of these tests is the *pair* of promises:
a v2 run's surface grants the two batch facts and carries an identity of its
own, while every v1 surface stays byte-identical (the frozen-manifest
regression in ``test_benchmark_manifest.py`` pins that side).
"""

from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from data_incident_gym import evidence_planner
from data_incident_gym.diagnosis import DiagnosticStrategy
from data_incident_gym.evidence import EVIDENCE_BATCH_TOOLS
from data_incident_gym.evidence_planner import (
    TOOL_OBLIGATIONS,
    V2_TOOL_OBLIGATIONS,
    evidence_planner_policy_identity,
    planner_controller_payload,
    planner_tool_catalog,
    tool_obligations_for_allowlist,
)
from data_incident_gym.fixed_rule import (
    EVIDENCE_TOOLS_V1_VERSION,
    EVIDENCE_TOOLS_V2_VERSION,
    FIXED_RULE_TOOL_NAMES,
    fixed_rule_policy_identity,
    tool_names_for_surface,
    tool_surface_for_context,
)
from data_incident_gym.reference_solver import (
    REFERENCE_ANALYST_TOOL_NAMES,
    reference_analyst_policy_identity,
)
from data_incident_gym.strategy_adapter import (
    EVIDENCE_V2_TOOL_ALLOWLIST,
    PROTOCOL_TOOL_ALLOWLIST,
    tool_allowlist_for_context,
)

V2_ADDITIONS = set(EVIDENCE_BATCH_TOOLS)


def _context(*, is_v2: bool) -> SimpleNamespace:
    return SimpleNamespace(is_v2=is_v2)


def test_the_v2_allowlist_is_the_six_plus_the_two_batch_facts() -> None:
    assert EVIDENCE_V2_TOOL_ALLOWLIST == PROTOCOL_TOOL_ALLOWLIST | V2_ADDITIONS
    assert tool_allowlist_for_context(_context(is_v2=False)) == PROTOCOL_TOOL_ALLOWLIST
    assert tool_allowlist_for_context(_context(is_v2=True)) == EVIDENCE_V2_TOOL_ALLOWLIST


def test_the_surface_follows_the_run_context() -> None:
    assert tool_surface_for_context(_context(is_v2=False)) == EVIDENCE_TOOLS_V1_VERSION
    assert tool_surface_for_context(_context(is_v2=True)) == EVIDENCE_TOOLS_V2_VERSION
    assert tool_names_for_surface(EVIDENCE_TOOLS_V1_VERSION) == FIXED_RULE_TOOL_NAMES
    assert set(tool_names_for_surface(EVIDENCE_TOOLS_V2_VERSION)) == (
        set(FIXED_RULE_TOOL_NAMES) | V2_ADDITIONS
    )


def test_the_fixed_rule_v2_identity_names_its_surface() -> None:
    v1 = fixed_rule_policy_identity()
    v2 = fixed_rule_policy_identity(EVIDENCE_TOOLS_V2_VERSION)

    # The v1 identity is the frozen one: its tool schema digest is the digest of
    # exactly the six names, with no surface key folded in.
    assert v1.tool_schema_sha256 == hashlib.sha256(
        b'["get_dbt_run_results","get_dbt_node_error","get_relation_schema",'
        b'"get_dbt_lineage","get_relation_data_profile","get_relation_history"]'
    ).hexdigest()
    assert v2.tool_schema_sha256 != v1.tool_schema_sha256
    assert v2.controller_protocol_sha256 != v1.controller_protocol_sha256
    assert v2.strategy is DiagnosticStrategy.FIXED_RULE


def test_the_reference_analyst_v2_identity_names_its_surface() -> None:
    v1 = reference_analyst_policy_identity()
    v2 = reference_analyst_policy_identity(EVIDENCE_TOOLS_V2_VERSION)

    assert set(REFERENCE_ANALYST_TOOL_NAMES) == set(FIXED_RULE_TOOL_NAMES)
    assert v2 != v1
    assert v2.tool_schema_sha256 != v1.tool_schema_sha256


def test_the_planner_v2_payload_carries_the_eight_tools() -> None:
    v1 = planner_controller_payload()
    v2 = planner_controller_payload(EVIDENCE_TOOLS_V2_VERSION)

    assert set(v1["tools"]) == set(FIXED_RULE_TOOL_NAMES)
    assert set(v2["tools"]) == set(FIXED_RULE_TOOL_NAMES) | V2_ADDITIONS
    assert "tool_surface" not in v1
    assert v2["tool_surface"] == EVIDENCE_TOOLS_V2_VERSION
    assert evidence_planner_policy_identity(
        EVIDENCE_TOOLS_V2_VERSION
    ) != evidence_planner_policy_identity()


def test_the_model_visible_list_binds_the_surface_vocabulary() -> None:
    """The terminal ``submit_diagnosis`` schema is what bounds the gap words a
    v2 model can declare; the action tools are identical across surfaces."""

    import json

    v1 = planner_controller_payload()
    v2 = planner_controller_payload(EVIDENCE_TOOLS_V2_VERSION)

    assert v1["model_tools"]["action_tools"] == v2["model_tools"]["action_tools"]
    v1_schema = json.dumps(v1["model_tools"]["output_tool"]["parameters"])
    v2_schema = json.dumps(v2["model_tools"]["output_tool"]["parameters"])
    assert "RELATION_SCHEMA_EXPECTATION" not in v1_schema
    assert "NODE_NOT_ALLOWED" not in v1_schema
    assert "RELATION_SCHEMA_EXPECTATION" in v2_schema
    assert "NODE_NOT_ALLOWED" in v2_schema


def test_the_registry_surface_stays_the_frozen_v1_one() -> None:
    """The registry path (manifests, identity readers) keeps the default v1
    surface; a v2 run's surface is built per run from its context."""

    from data_incident_gym.evidence_planner import (
        planner_model_tool_payload,
        planner_policy_surface,
    )

    surface = planner_policy_surface()
    assert surface.policy_identity == evidence_planner_policy_identity()
    payload = planner_model_tool_payload()
    assert surface.tool_schema_payload == (
        [*payload["action_tools"], payload["output_tool"]]
    )

    v2_surface = planner_policy_surface(EVIDENCE_TOOLS_V2_VERSION)
    assert v2_surface.policy_identity != surface.policy_identity
    assert v2_surface.final_diagnosis_schema_sha256 != surface.final_diagnosis_schema_sha256


def test_the_v2_user_prompt_names_the_definition_whitelist(tmp_path) -> None:
    """The model-visible context carries the v2 node whitelist; a v1 runtime's
    prompt stays byte-identical."""

    from datetime import UTC, datetime

    from data_incident_gym.planner_agent import _user_prompt
    from data_incident_gym.run_context import IncidentBrief, ObservableRunContext

    def _context(runtime: dict) -> ObservableRunContext:
        return ObservableRunContext(
            run_id="d" * 32,
            artifact_dir=tmp_path,
            runtime=runtime,
            incident_brief=IncidentBrief(
                schema_version="incident_brief.v1",
                signal_code="DBT_BUILD_FAILED",
                summary="A model failed.",
                subjects=("model.jaffle_shop.customers",),
                logical_observed_at=datetime(2026, 9, 19, tzinfo=UTC),
                observations=(),
            ),
        )

    v1 = _context({"observable_relations": {"schema": ["raw_customers"]}})
    v2 = _context(
        {
            "schema_version": "p1.runtime.v2",
            "observable_relations": {
                "schema": ["raw_customers"],
                "expectation": ["raw_customers"],
            },
            "observable_nodes": {"definition": ["model.jaffle_shop.customers"]},
        }
    )

    v1_catalog = planner_tool_catalog(PROTOCOL_TOOL_ALLOWLIST)
    v2_catalog = planner_tool_catalog(EVIDENCE_V2_TOOL_ALLOWLIST, EVIDENCE_TOOLS_V2_VERSION)

    assert "observable_nodes" not in _user_prompt(v1, v1_catalog)
    v2_prompt = _user_prompt(v2, v2_catalog)
    assert '"observable_nodes"' in v2_prompt
    assert "model.jaffle_shop.customers" in v2_prompt
    assert '"get_dbt_node_definition"' in v2_prompt
    assert '"get_dbt_node_definition"' not in _user_prompt(v1, v1_catalog)


def test_the_obligation_table_follows_the_granted_surface() -> None:
    assert tool_obligations_for_allowlist(PROTOCOL_TOOL_ALLOWLIST) is TOOL_OBLIGATIONS
    assert tool_obligations_for_allowlist(EVIDENCE_V2_TOOL_ALLOWLIST) is V2_TOOL_OBLIGATIONS
    for tool in EVIDENCE_BATCH_TOOLS:
        spec = V2_TOOL_OBLIGATIONS[tool]
        # The batch tools take the frozen comma-joined list, so they stay
        # single-argument string tools like the six.
        assert len(spec.arguments) == 1
        assert spec.subject_argument in spec.arguments


@pytest.mark.parametrize("tool_name", sorted(V2_ADDITIONS))
def test_a_v2_session_grants_the_batch_tools(tool_name: str) -> None:
    from tests.unit.test_strategy_adapter import _request, _session

    session = _session(allowlist=EVIDENCE_V2_TOOL_ALLOWLIST)

    receipt = session.call_tool(_request(tool_name, **{_argument_for(tool_name): ""}))

    # Granted: the call reaches argument validation instead of being refused as
    # not-allowlisted (an empty request is refused by the frozen batch rules).
    assert receipt.error is not None
    assert receipt.error.code != "TOOL_NOT_ALLOWLISTED"


def _argument_for(tool_name: str) -> str:
    return "relation_names" if tool_name == "get_relation_schema_expectation" else "node_ids"


def test_the_planner_accepts_a_v2_step_only_where_it_is_granted() -> None:
    from tests.unit.test_strategy_adapter import _session

    planner = evidence_planner
    v2_session = _session(allowlist=EVIDENCE_V2_TOOL_ALLOWLIST)
    v1_session = _session()

    v2_controller = planner.PlannerController(v2_session)
    v1_controller = planner.PlannerController(v1_session)

    accepted = v2_controller.plan_step(
        "get_relation_schema_expectation", {"relation_names": "raw_customers"}
    )
    refused = v1_controller.plan_step(
        "get_relation_schema_expectation", {"relation_names": "raw_customers"}
    )

    # Granted: the step is validated and executed (the empty backend session in
    # the fixture returns no records, which the controller reports as such).
    assert accepted.verdict.accepted is True, accepted.verdict
    # Not granted: the same step is refused before any execution.
    assert refused.verdict.accepted is False
    assert refused.verdict.code == "PLAN_TOOL_NOT_ALLOWLISTED"
