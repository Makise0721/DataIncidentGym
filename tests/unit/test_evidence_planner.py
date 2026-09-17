"""T12 planner contract tests: the deterministic plan-validation layer.

Every rule of the design's §2 is pinned here without a model and without a
database: obligation identity over all arguments, the four SATISFIED
conditions, the fixed behaviour of a closed obligation, the validation order,
the separation between a ``PlanVerdict`` and a real ``ToolReceipt``, and the
budget/refusal accounting.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from data_incident_gym.diagnosis import (
    KERNEL_STRATEGIES,
    MAIN_STRATEGIES,
    MODEL_STRATEGIES,
    DiagnosisStatus,
    DiagnosticStrategy,
    UnresolvedEvidence,
)
from data_incident_gym.diagnostic_agent import (
    KERNEL_PROMPT,
    NO_TOOL_PROMPT,
    PLANNER_PROMPT,
    PLANNER_PROMPT_VERSION,
    STATIC_PROMPT,
    load_strategy_prompt,
)
from data_incident_gym.evidence import EvidenceRecord, RelationNotAllowedError
from data_incident_gym.evidence_planner import (
    PLAN_ERROR_CODES,
    PLANNER_PROTOCOL_VERSION,
    TOOL_OBLIGATIONS,
    PlannerController,
    PlanVerdict,
    ToolObligationSpec,
    canonical_arguments,
    evidence_planner_policy_identity,
    obligation_id_for,
    obligation_tool_schemas,
    planner_controller_payload,
)
from data_incident_gym.strategy_adapter import FinalSubmission, StrategySession

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from unit.test_strategy_adapter import (  # noqa: E402
    RUN_ID,
    _context,
    _declaration,
    _run_record,
)

NODE = "test.jaffle_shop.not_null_orders_customer_id"


def _record_for(tool_name: str, subject: str) -> EvidenceRecord:
    base = _run_record()
    return EvidenceRecord.create(
        run_id=RUN_ID,
        evidence_type=base.evidence_type,
        source=base.source,
        subject=f"{tool_name}:{subject}",
        observed_at=base.observed_at,
        content=base.content,
    )


class _PlannerTools:
    """One record per (tool, subject); configurable refusal and empty results."""

    def __init__(
        self, *, refuse: tuple[str, ...] = (), empty: tuple[str, ...] = ()
    ) -> None:
        self.refuse = set(refuse)
        self.empty = set(empty)
        self.calls: list[str] = []

    def _one(self, tool_name: str, subject: str) -> tuple[EvidenceRecord, ...]:
        self.calls.append(tool_name)
        if tool_name in self.refuse:
            raise RelationNotAllowedError(subject)
        if tool_name in self.empty:
            return ()
        return (_record_for(tool_name, subject),)

    def get_dbt_run_results(self, run_id: str) -> tuple[EvidenceRecord, ...]:
        return self._one("get_dbt_run_results", run_id)

    def get_dbt_node_error(self, run_id: str, node_id: str) -> tuple[EvidenceRecord, ...]:
        return self._one("get_dbt_node_error", node_id)

    def get_dbt_lineage(self, node_id: str, direction: str) -> tuple[EvidenceRecord, ...]:
        return self._one("get_dbt_lineage", f"{node_id}:{direction}")

    def get_relation_schema(self, relation_name: str) -> tuple[EvidenceRecord, ...]:
        return self._one("get_relation_schema", relation_name)

    def get_relation_data_profile(self, relation_name: str) -> tuple[EvidenceRecord, ...]:
        return self._one("get_relation_data_profile", relation_name)

    def get_relation_history(self, relation_name: str) -> tuple[EvidenceRecord, ...]:
        return self._one("get_relation_history", relation_name)


def _controller(
    tools: _PlannerTools | None = None, *, clock=None
) -> tuple[PlannerController, StrategySession, _PlannerTools]:
    backend = tools or _PlannerTools()
    session = StrategySession(
        run_id=RUN_ID,
        tools=backend,
        context=_context(),
        declaration=_declaration(),
        clock=clock,
    )
    return PlannerController(session), session, backend


def _submission() -> FinalSubmission:
    return FinalSubmission(
        status=DiagnosisStatus.INSUFFICIENT_EVIDENCE,
        summary="No decisive evidence was collected.",
        unresolved_evidence=(
            UnresolvedEvidence(
                evidence_kind="RELATION_DATA_PROFILE",
                subject="raw_orders",
                reason_code="NOT_OBSERVABLE",
            ),
        ),
        confidence=0.1,
    )


# -- obligation identity ---------------------------------------------------


def test_obligation_identity_covers_every_argument() -> None:
    upstream = obligation_id_for("get_dbt_lineage", {"node_id": NODE, "direction": "upstream"})
    downstream = obligation_id_for(
        "get_dbt_lineage", {"node_id": NODE, "direction": "downstream"}
    )

    assert upstream != downstream
    assert upstream == obligation_id_for(
        "get_dbt_lineage", {"direction": "upstream", "node_id": NODE}
    )
    assert canonical_arguments({"b": "2", "a": "1"}) == '{"a":"1","b":"2"}'


def test_argument_encoding_cannot_collide_on_a_separator() -> None:
    """Audit regression: ``name=value`` concatenation merged these two calls."""

    first = {"direction": "upstream,node_id=x", "node_id": "y"}
    second = {"direction": "upstream", "node_id": "x,node_id=y"}

    assert canonical_arguments(first) != canonical_arguments(second)
    assert obligation_id_for("get_dbt_lineage", first) != obligation_id_for(
        "get_dbt_lineage", second
    )

    controller, session, _ = _controller()
    one = controller.plan_step("get_dbt_lineage", first)
    two = controller.plan_step("get_dbt_lineage", second)

    assert one.verdict.obligation_id != two.verdict.obligation_id
    assert len(controller.obligations()) == 2
    assert session.snapshot()["tool_call_attempts"] == 2


def test_closing_one_direction_does_not_close_the_other() -> None:
    controller, session, _ = _controller()

    first = controller.plan_step("get_dbt_lineage", {"node_id": NODE, "direction": "upstream"})
    assert first.verdict.accepted is True
    closed = controller.close_obligation(
        first.verdict.obligation_id, "SATISFIED", evidence_ids=tuple(first.receipt.evidence_ids)
    )
    assert closed.accepted is True

    second = controller.plan_step("get_dbt_lineage", {"node_id": NODE, "direction": "downstream"})
    assert second.verdict.accepted is True
    assert second.verdict.obligation_id != first.verdict.obligation_id
    assert session.snapshot()["tool_call_attempts"] == 2
    assert len(controller.obligations()) == 2


# -- execution and receipts ------------------------------------------------


def test_accepted_step_executes_and_registers_evidence() -> None:
    controller, session, backend = _controller()

    result = controller.plan_step(
        "get_dbt_node_error", {"run_id": RUN_ID, "node_id": NODE}, intent="read the failure"
    )

    assert result.verdict.accepted is True
    assert result.verdict.code is None
    assert result.receipt is not None and result.receipt.accepted is True
    assert backend.calls == ["get_dbt_node_error"]
    assert session.snapshot()["tool_call_attempts"] == 1
    assert session.registered_evidence_ids() == result.receipt.evidence_ids
    obligation = controller.obligations()[0]
    assert obligation.evidence_kind == "DBT_NODE_ERROR"
    assert obligation.subject == NODE
    assert obligation.last_intent == "read the failure"
    assert obligation.status == "OPEN"


def test_backend_refusal_keeps_the_real_code_and_counts_the_attempt() -> None:
    controller, session, _ = _controller(_PlannerTools(refuse=("get_relation_data_profile",)))

    result = controller.plan_step("get_relation_data_profile", {"relation_name": "raw_customers"})

    assert result.verdict.accepted is True  # the declaration was valid
    assert result.receipt is not None and result.receipt.accepted is False
    assert result.receipt.error is not None
    assert result.receipt.error.code == RelationNotAllowedError.code
    assert result.receipt.error.code not in PLAN_ERROR_CODES
    assert session.snapshot()["tool_call_attempts"] == 1


# -- validation refusals ---------------------------------------------------


@pytest.mark.parametrize(
    ("tool_name", "arguments", "expected"),
    (
        ("run_sql", {"query": "select 1"}, "PLAN_TOOL_NOT_ALLOWLISTED"),
        ("get_relation_data_profile", {}, "PLAN_ARGUMENTS_INVALID"),
        (
            "get_relation_data_profile",
            {"relation_name": "raw_orders", "extra": "x"},
            "PLAN_ARGUMENTS_INVALID",
        ),
        ("get_relation_data_profile", {"relation_name": 5}, "PLAN_ARGUMENTS_INVALID"),
        ("get_dbt_run_results", {"run_id": "d" * 32}, "PLAN_RUN_SCOPE_MISMATCH"),
    ),
)
def test_invalid_declarations_are_refused_without_touching_the_backend(
    tool_name: str, arguments: dict[str, object], expected: str
) -> None:
    controller, session, backend = _controller()

    result = controller.plan_step(tool_name, arguments)

    assert result.verdict.accepted is False
    assert result.verdict.code == expected
    assert result.receipt is None
    assert result.evidence == ()
    assert backend.calls == []
    assert session.snapshot()["tool_call_attempts"] == 0
    assert controller.snapshot()["plan_refusals_used"] == 1


def test_verdicts_never_carry_evidence_or_backend_codes() -> None:
    controller, _, _ = _controller()

    refusal = controller.plan_step("run_sql", {"query": "select 1"})
    verdict = refusal.verdict

    assert isinstance(verdict, PlanVerdict)
    assert set(PlanVerdict.model_fields) == {
        "accepted",
        "code",
        "obligation_id",
        "detail",
        "plan_refusals_used",
        "plan_refusal_limit",
        "tool_calls_used",
    }
    assert verdict.code in PLAN_ERROR_CODES
    assert refusal.receipt is None


# -- SATISFIED conditions --------------------------------------------------


def test_satisfied_needs_at_least_one_evidence_id_from_this_call() -> None:
    controller, _, _ = _controller()

    first = controller.plan_step("get_dbt_run_results", {"run_id": RUN_ID})
    foreign = controller.plan_step("get_relation_history", {"relation_name": "raw_orders"})
    assert first.verdict.obligation_id != foreign.verdict.obligation_id

    wrong = controller.close_obligation(
        first.verdict.obligation_id, "SATISFIED", evidence_ids=tuple(foreign.receipt.evidence_ids)
    )
    assert wrong.accepted is False
    assert wrong.code == "PLAN_EVIDENCE_NOT_RETURNED_BY_THIS_CALL"

    empty = controller.close_obligation(first.verdict.obligation_id, "SATISFIED")
    assert empty.accepted is False
    assert empty.code == "PLAN_NO_EVIDENCE_FROM_LAST_CALL"
    assert controller.obligations()[0].status == "OPEN"


def test_satisfied_succeeds_with_the_calls_own_evidence() -> None:
    controller, _, _ = _controller()
    step = controller.plan_step("get_dbt_run_results", {"run_id": RUN_ID})

    good = controller.close_obligation(
        step.verdict.obligation_id, "SATISFIED", evidence_ids=tuple(step.receipt.evidence_ids)
    )

    assert good.accepted is True
    obligation = controller.obligations()[0]
    assert obligation.status == "SATISFIED"
    assert obligation.satisfied_with == tuple(step.receipt.evidence_ids)


def test_a_refused_call_can_only_be_revoked() -> None:
    controller, _, _ = _controller(_PlannerTools(refuse=("get_relation_data_profile",)))

    refused = controller.plan_step(
        "get_relation_data_profile", {"relation_name": "raw_customers"}
    )
    assert (
        controller.close_obligation(refused.verdict.obligation_id, "SATISFIED").code
        == "PLAN_NO_EVIDENCE_FROM_LAST_CALL"
    )
    revoked = controller.close_obligation(
        refused.verdict.obligation_id,
        "REVOKED",
        reason="the relation is not observable in this run",
    )

    assert revoked.accepted is True
    assert controller.obligations()[0].status == "REVOKED"
    assert controller.obligations()[0].close_reason == "the relation is not observable in this run"


def test_an_empty_result_can_only_be_revoked() -> None:
    controller, _, _ = _controller(_PlannerTools(empty=("get_relation_schema",)))

    empty = controller.plan_step("get_relation_schema", {"relation_name": "raw_orders"})
    assert empty.receipt is not None and empty.receipt.accepted is True
    assert empty.receipt.evidence_ids == ()
    assert (
        controller.close_obligation(empty.verdict.obligation_id, "SATISFIED").code
        == "PLAN_NO_EVIDENCE_FROM_LAST_CALL"
    )
    revoked = controller.close_obligation(
        empty.verdict.obligation_id, "REVOKED", reason="the call returned no records"
    )

    assert revoked.accepted is True
    assert controller.obligations()[0].status == "REVOKED"


def test_revoked_obligation_needs_a_public_reason() -> None:
    controller, _, _ = _controller()
    step = controller.plan_step("get_relation_history", {"relation_name": "raw_orders"})

    refused = controller.close_obligation(step.verdict.obligation_id, "REVOKED")

    assert refused.accepted is False
    assert refused.code == "PLAN_REVOKE_REASON_REQUIRED"
    assert controller.obligations()[0].status == "OPEN"


# -- closed obligations ----------------------------------------------------


def test_closed_obligations_stay_closed_with_a_fixed_code() -> None:
    controller, session, _ = _controller()
    step = controller.plan_step("get_dbt_run_results", {"run_id": RUN_ID})
    obligation_id = step.verdict.obligation_id
    assert controller.close_obligation(obligation_id, "SATISFIED", evidence_ids=(
        *step.receipt.evidence_ids,
    )).accepted

    again = controller.close_obligation(obligation_id, "SATISFIED")
    assert again.accepted is False
    assert again.code == "PLAN_OBLIGATION_CLOSED"

    replan = controller.plan_step("get_dbt_run_results", {"run_id": RUN_ID})
    assert replan.verdict.accepted is False
    assert replan.verdict.code == "PLAN_OBLIGATION_CLOSED"
    assert replan.receipt is None
    # Neither the second close nor the refused replan touched the backend.
    assert session.snapshot()["tool_call_attempts"] == 1
    assert controller.obligations()[0].status == "SATISFIED"


# -- budgets ---------------------------------------------------------------


def test_budget_exhaustion_is_a_verdict_not_a_receipt() -> None:
    controller, session, backend = _controller()
    for index in range(8):
        result = controller.plan_step(
            "get_dbt_node_error", {"run_id": RUN_ID, "node_id": f"test.node_{index}"}
        )
        assert result.verdict.accepted is True
    assert session.snapshot()["tool_call_attempts"] == 8

    ninth = controller.plan_step("get_dbt_run_results", {"run_id": RUN_ID})

    assert ninth.verdict.accepted is False
    assert ninth.verdict.code == "PLAN_TOOL_BUDGET_EXHAUSTED"
    assert ninth.receipt is None
    assert session.snapshot()["tool_call_attempts"] == 8
    assert len(backend.calls) == 8


def test_plan_refusal_budget_stops_every_further_plan_operation() -> None:
    """Two invalid declarations spend the plan budget; after that even a valid
    step or close is refused with the fixed terminal code — while the T09
    submission path stays exactly as it was."""

    controller, session, backend = _controller()
    ok = controller.plan_step("get_dbt_run_results", {"run_id": RUN_ID})

    for _ in range(2):
        assert controller.plan_step("run_sql", {"query": "select 1"}).verdict.code == (
            "PLAN_TOOL_NOT_ALLOWLISTED"
        )
    assert controller.snapshot()["plan_refusals_used"] == 2
    assert controller.snapshot()["refusals_by_code"] == {"PLAN_TOOL_NOT_ALLOWLISTED": 2}

    step = controller.plan_step("get_relation_history", {"relation_name": "raw_orders"})
    close = controller.close_obligation(
        ok.verdict.obligation_id, "SATISFIED", evidence_ids=tuple(ok.receipt.evidence_ids)
    )
    assert step.verdict.code == "PLAN_OUTPUT_RETRY_EXHAUSTED"
    assert close.code == "PLAN_OUTPUT_RETRY_EXHAUSTED"
    assert step.receipt is None and close.obligation_id is None
    # The blocked attempts are recorded, they do not inflate the refusal count.
    assert controller.snapshot()["plan_operations_blocked"] == 2
    assert controller.snapshot()["plan_refusals_used"] == 2
    assert backend.calls == ["get_dbt_run_results"]
    assert session.snapshot()["tool_call_attempts"] == 1
    # The plan layer stops issuing steps; the T09 submission path is untouched.
    assert session.submit(_submission()).accepted is True
    assert controller.snapshot()["obligations_open"]


def test_deadline_and_terminal_state_close_the_planner() -> None:
    now = {"t": 0.0}
    controller, session, _ = _controller(clock=lambda: now["t"])
    assert controller.plan_step("get_dbt_run_results", {"run_id": RUN_ID}).verdict.accepted

    now["t"] = 400.0
    late = controller.plan_step("get_relation_history", {"relation_name": "raw_orders"})
    assert late.verdict.code == "PLAN_DEADLINE_EXCEEDED"
    assert session.snapshot()["tool_call_attempts"] == 1

    now["t"] = 0.0
    assert session.submit(_submission()).accepted is True
    closed = controller.plan_step("get_relation_history", {"relation_name": "raw_orders"})
    assert closed.verdict.code == "PLAN_SESSION_CLOSED"


@pytest.mark.parametrize("bogus", ("BOGUS", "revoked", "satisfied", "", "SATISFIED "))
def test_a_bogus_close_outcome_never_becomes_a_revoke(bogus: str) -> None:
    """Audit regression: every non-``SATISFIED`` value used to become REVOKED."""

    controller, _, _ = _controller()
    step = controller.plan_step("get_dbt_run_results", {"run_id": RUN_ID})

    verdict = controller.close_obligation(step.verdict.obligation_id, bogus)

    assert verdict.accepted is False
    assert verdict.code == "PLAN_OUTCOME_INVALID"
    obligation = controller.obligations()[0]
    assert obligation.status == "OPEN"
    assert obligation.close_reason is None


def test_a_stream_of_invalid_closes_is_blocked_once_the_budget_is_spent() -> None:
    """Audit regression: the close entry point validated the outcome before the
    refusal budget, so invalid closes kept returning specific codes and kept
    growing the counter past its limit."""

    controller, _, _ = _controller()
    step = controller.plan_step("get_dbt_run_results", {"run_id": RUN_ID})
    obligation_id = step.verdict.obligation_id

    codes = [
        controller.close_obligation(obligation_id, "BOGUS").code for _ in range(4)
    ]

    assert codes == [
        "PLAN_OUTCOME_INVALID",
        "PLAN_OUTCOME_INVALID",
        "PLAN_OUTPUT_RETRY_EXHAUSTED",
        "PLAN_OUTPUT_RETRY_EXHAUSTED",
    ]
    snapshot = controller.snapshot()
    assert snapshot["plan_refusals_used"] == 2
    assert snapshot["refusals_by_code"] == {"PLAN_OUTCOME_INVALID": 2}
    assert snapshot["plan_operations_blocked"] == 2
    obligation = controller.obligations()[0]
    assert obligation.status == "OPEN"
    assert obligation.close_reason is None


def test_a_valid_close_still_works_after_one_bogus_outcome() -> None:
    controller, _, _ = _controller()
    step = controller.plan_step("get_dbt_run_results", {"run_id": RUN_ID})
    obligation_id = step.verdict.obligation_id

    assert controller.close_obligation(obligation_id, "BOGUS").code == "PLAN_OUTCOME_INVALID"
    good = controller.close_obligation(
        obligation_id, "SATISFIED", evidence_ids=tuple(step.receipt.evidence_ids)
    )

    assert good.accepted is True
    assert controller.obligations()[0].status == "SATISFIED"
    assert controller.snapshot()["refusals_by_code"] == {"PLAN_OUTCOME_INVALID": 1}


def test_the_refusal_budget_is_checked_before_the_specific_rules() -> None:
    """Audit regression: a stream of invalid calls kept returning fresh codes and
    kept incrementing the counter past its limit."""

    controller, session, backend = _controller()

    codes = [
        controller.plan_step("run_sql", {"query": "select 1"}).verdict.code for _ in range(4)
    ]

    assert codes == [
        "PLAN_TOOL_NOT_ALLOWLISTED",
        "PLAN_TOOL_NOT_ALLOWLISTED",
        "PLAN_OUTPUT_RETRY_EXHAUSTED",
        "PLAN_OUTPUT_RETRY_EXHAUSTED",
    ]
    snapshot = controller.snapshot()
    assert snapshot["plan_refusals_used"] == 2
    assert snapshot["refusals_by_code"] == {"PLAN_TOOL_NOT_ALLOWLISTED": 2}
    assert snapshot["plan_operations_blocked"] == 2
    assert backend.calls == []
    assert session.snapshot()["tool_call_attempts"] == 0


# -- identity registration -------------------------------------------------


def _digest(payload: object) -> str:
    """Independent recomputation of the canonical digest the identity uses."""

    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def test_planner_identity_binds_the_prompt_and_the_plan_contract() -> None:
    identity = evidence_planner_policy_identity()

    assert identity.strategy is DiagnosticStrategy.EVIDENCE_PLANNER
    assert identity.strategy_prompt_version == PLANNER_PROMPT_VERSION == "p1.planner.v1"
    # Prompts are hashed as raw UTF-8 text; structured payloads use _digest.
    assert identity.strategy_prompt_sha256 == hashlib.sha256(
        PLANNER_PROMPT.encode("utf-8")
    ).hexdigest()
    assert identity.controller_protocol_version == PLANNER_PROTOCOL_VERSION
    assert identity.controller_protocol_sha256 == _digest(planner_controller_payload())
    assert identity.tool_schema_sha256 == _digest(obligation_tool_schemas())
    # Deterministic: the same surface always yields the same identity.
    assert evidence_planner_policy_identity() == identity


def test_the_controller_digest_covers_every_contract_element() -> None:
    """Any change to the promise changes the digest — tools, budget and gate."""

    base = planner_controller_payload()
    tool = "get_dbt_lineage"
    payloads = (
        {**base, "tools": {**base["tools"], "get_secret": base["tools"][tool]}},
        {**base, "error_codes": [*base["error_codes"], "PLAN_NEW_CODE"]},
        {**base, "outcomes": ["SATISFIED"]},
        {**base, "budget": {**base["budget"], "tool_call_limit": 9}},
        {
            **base,
            "plan_refusal_budget": {**base["plan_refusal_budget"], "limit": 3},
        },
        # Audit reproduction: the subject mapping used to be invisible.
        {
            **base,
            "tools": {
                **base["tools"],
                tool: {**base["tools"][tool], "subject_argument": "direction"},
            },
        },
        {
            **base,
            "tools": {
                **base["tools"],
                tool: {**base["tools"][tool], "arguments": ["node_id"]},
            },
        },
        {**base, "tool_schema_sha256": "0" * 64},
    )

    for payload in payloads:
        assert _digest(payload) != _digest(base)


def test_editing_a_tool_spec_changes_both_identity_digests(monkeypatch) -> None:
    """Audit regression: subject mapping and argument set were outside the
    identity — the first changed nothing, the second left ``tool_schema_sha256``
    untouched."""

    before = evidence_planner_policy_identity()
    tool = "get_dbt_lineage"
    original = TOOL_OBLIGATIONS[tool]

    monkeypatch.setitem(
        TOOL_OBLIGATIONS,
        tool,
        ToolObligationSpec(original.evidence_kind, "direction", original.arguments),
    )
    after_subject = evidence_planner_policy_identity()
    assert after_subject.controller_protocol_sha256 != before.controller_protocol_sha256
    assert after_subject != before

    monkeypatch.setitem(
        TOOL_OBLIGATIONS,
        tool,
        ToolObligationSpec(original.evidence_kind, original.subject_argument, ("node_id",)),
    )
    after_arguments = evidence_planner_policy_identity()
    assert after_arguments.tool_schema_sha256 != before.tool_schema_sha256
    assert after_arguments.controller_protocol_sha256 != before.controller_protocol_sha256
    assert after_arguments != before


def test_tool_schema_digest_binds_arguments_types_and_required() -> None:
    schemas = {entry["name"]: entry["input_schema"] for entry in obligation_tool_schemas()}

    assert sorted(schemas) == sorted(TOOL_OBLIGATIONS)
    for tool, spec in TOOL_OBLIGATIONS.items():
        schema = schemas[tool]
        assert list(schema["properties"]) == list(spec.arguments)
        assert schema["required"] == list(spec.arguments)
        assert schema["additionalProperties"] is False
        assert all(entry == {"type": "string"} for entry in schema["properties"].values())


def test_the_planner_has_its_own_prompt() -> None:
    prompt = load_strategy_prompt(DiagnosticStrategy.EVIDENCE_PLANNER)

    assert prompt is PLANNER_PROMPT
    assert prompt.strip()
    for other in (KERNEL_PROMPT, STATIC_PROMPT, NO_TOOL_PROMPT):
        assert prompt != other
    # The plan contract is described to the model, not just enforced.
    for marker in ("plan_step", "close_obligation", "submit_diagnosis", "SATISFIED", "REVOKED"):
        assert marker in prompt


def test_the_prompt_keeps_the_two_refusal_budgets_apart() -> None:
    """Audit regression: the prompt used to describe plan refusals as consuming
    the submission retry budget."""

    prompt = PLANNER_PROMPT

    assert "计划拒绝预算（2 次）" in prompt
    assert "提交被拒预算（2 次）" in prompt
    assert "计划被拒不会消耗它" in prompt
    assert "计划被拒绝不会消耗工具预算，但会消耗输出重试预算" not in prompt
    assert "2 次输出重试" not in prompt


def test_the_planner_stays_out_of_the_existing_report_surfaces_for_now() -> None:
    """Registration into the schedule/report tuples is deferred to the runner
    slice: today no report section, benchmark schedule or frozen manifest sees a
    new member."""

    planner = DiagnosticStrategy.EVIDENCE_PLANNER

    assert planner not in MAIN_STRATEGIES
    assert planner not in MODEL_STRATEGIES
    assert planner not in KERNEL_STRATEGIES


def test_unknown_obligation_cannot_be_closed() -> None:
    controller, _, _ = _controller()

    verdict = controller.close_obligation("get_relation_history:relation_name=raw_orders",
                                         "REVOKED", reason="not needed")

    assert verdict.accepted is False
    assert verdict.code == "PLAN_UNKNOWN_OBLIGATION"


# -- model-visible surface (slice 3, part A) --------------------------------


def test_registered_model_tools_match_the_identity_payload() -> None:
    """Same source: the payload is read back from the registered agent."""

    from pydantic_ai import Agent
    from pydantic_ai.models.function import FunctionModel

    from data_incident_gym.evidence_planner import (
        PLANNER_ACTION_TOOLS,
        PLANNER_OUTPUT_TOOL,
        PlannerDeps,
        planner_model_tool_payload,
        planner_output_definition,
        register_planner_tools,
    )

    payload = planner_model_tool_payload()
    agent: Agent = Agent(
        FunctionModel(lambda _messages, _info: None),
        deps_type=PlannerDeps,
        output_type=planner_output_definition(),
    )
    register_planner_tools(agent)
    registered = agent._function_toolset.tools

    assert [entry["name"] for entry in payload["action_tools"]] == [
        name for name, _description, _model in PLANNER_ACTION_TOOLS
    ]
    for entry in payload["action_tools"]:
        assert entry["parameters"] == registered[entry["name"]].function_schema.json_schema
    assert payload["output_tool"]["name"] == PLANNER_OUTPUT_TOOL[0]
    assert planner_output_definition().name == PLANNER_OUTPUT_TOOL[0]

    plan_step_params = payload["action_tools"][0]["parameters"]
    assert list(plan_step_params["properties"]) == ["tool_name", "arguments", "intent"]
    assert plan_step_params["required"] == ["tool_name", "arguments"]
    # Permissive on purpose: the plan layer judges these at runtime.
    assert plan_step_params["properties"]["arguments"]["type"] == "object"
    close_params = payload["action_tools"][1]["parameters"]
    assert close_params["properties"]["outcome"]["type"] == "string"


def test_the_identity_binds_the_registered_model_tools() -> None:
    payload = planner_controller_payload()

    assert "model_tools" in payload
    assert [entry["name"] for entry in payload["model_tools"]["action_tools"]] == [
        "plan_step",
        "close_obligation",
    ]
    assert payload["model_tools"]["output_tool"]["name"] == "submit_diagnosis"
    assert evidence_planner_policy_identity().controller_protocol_sha256 == _digest(payload)


def test_executed_steps_are_recorded_for_the_trace() -> None:
    controller, _, _ = _controller()

    controller.plan_step("get_dbt_node_error", {"run_id": RUN_ID, "node_id": NODE})
    refused = controller.plan_step("run_sql", {"query": "select 1"})
    assert refused.verdict.accepted is False

    records = controller.step_records()
    assert len(records) == 1
    assert records[0]["tool_name"] == "get_dbt_node_error"
    assert records[0]["accepted"] is True
    assert records[0]["error_code"] is None
    assert records[0]["evidence_ids"]
    assert records[0]["elapsed_ms"] >= 0
    assert controller.snapshot()["plan_steps_recorded"] == 1
