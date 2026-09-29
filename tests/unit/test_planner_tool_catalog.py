"""T12 planner tool-catalog visibility: what the model is actually offered.

The paused 29-cell prefix showed the model guessing tool names and argument
sets: the planner's user payload carried the run, the brief and the visible
relations, but never the precise contract of the six read-only tools it was
supposed to name inside ``plan_step``. These tests drive the real runner with
a scripted ``FunctionModel`` and assert on the messages that model receives —
not on helper outputs — so "the catalog was delivered" means delivered.

The director helpers read the tool names and argument rules **only** from the
model-visible messages (catalog + public payload). They never import
``TOOL_OBLIGATIONS`` as an answer key and never read scenario expectations.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel

from data_incident_gym.diagnosis import DiagnosticStrategy
from data_incident_gym.diagnostic_agent import (
    ModelIdentity,
    policy_surface_for_strategy,
)
from data_incident_gym.evidence_planner import (
    TOOL_OBLIGATIONS,
    _digest,
    evidence_planner_policy_identity,
    obligation_tool_schemas,
    planner_controller_payload,
    planner_tool_catalog,
)
from data_incident_gym.fixed_rule import (
    EVIDENCE_TOOLS_V1_VERSION,
    EVIDENCE_TOOLS_V2_VERSION,
)
from data_incident_gym.planner_agent import EvidencePlannerRunner
from data_incident_gym.run_context import ObservableRunContext
from data_incident_gym.strategy_adapter import (
    EVIDENCE_V2_TOOL_ALLOWLIST,
    PROTOCOL_TOOL_ALLOWLIST,
    StrategySession,
    builtin_declaration,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from unit.test_evidence_planner import (  # noqa: E402
    NODE,
    RUN_ID,
    _PlannerTools,
)
from unit.test_kernel_ledger_instructions import _write_run_context  # noqa: E402
from unit.test_strategy_adapter import _context, _declaration  # noqa: E402

# -- message-capture helpers -------------------------------------------------


def _user_texts(messages) -> list[str]:
    """Every user-prompt string the model has been offered so far."""

    texts: list[str] = []
    for message in messages:
        for part in getattr(message, "parts", ()):
            if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                texts.append(part.content)
    return texts


def _payload_of(text: str) -> dict:
    """The JSON payload at the end of a planner user prompt."""

    return json.loads(text[text.index("{") :])


def catalog_from_messages(messages) -> list[dict]:
    """The ``evidence_tool_catalog`` as the model itself would read it."""

    latest = _user_texts(messages)[-1]
    return _payload_of(latest)["evidence_tool_catalog"]


def _capture_runner(project_root: Path, play, *, backend=None) -> EvidencePlannerRunner:
    session = StrategySession(
        run_id=RUN_ID,
        tools=backend or _PlannerTools(),
        context=_context(),
        declaration=_declaration(),
    )
    return EvidencePlannerRunner.for_run(
        RUN_ID,
        SimpleNamespace(),
        project_root,
        model=FunctionModel(play),
        model_identity=ModelIdentity("test", "scripted"),
        session=session,
    )


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    _write_run_context(tmp_path, RUN_ID)
    return tmp_path


def _abstain() -> dict[str, object]:
    return {
        "status": "INSUFFICIENT_EVIDENCE",
        "summary": "The scripted model abstains after one declared step.",
        "unresolved_evidence": [
            {
                "evidence_kind": "RELATION_DATA_PROFILE",
                "subject": "raw_orders",
                "reason_code": "NOT_OBSERVABLE",
            }
        ],
        "confidence": 0.2,
    }


def _scripted(events):
    """Play fixed ``(tool_name, arguments)`` turns, then abstain."""

    remaining = list(events)

    def play(_messages, _info) -> ModelResponse:
        if not remaining:
            return ModelResponse(parts=[ToolCallPart("submit_diagnosis", _abstain())])
        name, arguments = remaining.pop(0)
        return ModelResponse(parts=[ToolCallPart(name, arguments)])

    return play


# -- the catalog-driven director ---------------------------------------------


def _request_from_catalog(
    catalog: list[dict], purpose_keyword: str, messages
) -> tuple[str, dict[str, str]]:
    """Resolve one legal request using ONLY the catalog and public payload.

    Selection is by description semantics (the only channel a model has);
    arguments come from ``input_schema`` (names, required) plus the public
    payload values; the lineage direction comes from ``argument_notes``.
    """

    entry = next(item for item in catalog if purpose_keyword in item["description"])
    payload = _payload_of(_user_texts(messages)[-1])
    schema = entry["input_schema"]
    notes = entry["argument_notes"]
    arguments: dict[str, str] = {}
    for name in schema["required"]:
        if name == "run_id":
            arguments[name] = payload["run_id"]
        elif name == "relation_name":
            relations = payload["observable_relations"]
            arguments[name] = sorted(relations.values())[0][0] if relations else ""
        elif name == "node_id":
            arguments[name] = payload["incident_brief"]["subjects"][0]
        elif name == "direction":
            assert "upstream" in notes and "downstream" in notes
            arguments[name] = "downstream" if "downstream" in notes else "upstream"
        else:  # pragma: no cover - the v1/v2 surfaces define no other argument
            raise AssertionError(f"director cannot fill argument {name!r}")
    return entry["name"], arguments


def _catalog_director(purpose_keyword: str, submission: dict[str, object], chosen: dict):
    """Plan one step straight from the catalog, then submit.

    ``chosen`` records the request the director resolved, so a test can assert
    on what the catalog actually offered rather than on a hardcoded answer.
    """

    state = {"planned": False}

    def play(messages, _info) -> ModelResponse:
        catalog = catalog_from_messages(messages)
        assert catalog, "the director cannot act without a delivered catalog"
        if not state["planned"]:
            state["planned"] = True
            name, arguments = _request_from_catalog(catalog, purpose_keyword, messages)
            chosen.update(name=name, arguments=dict(arguments))
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "plan_step",
                        {"tool_name": name, "arguments": arguments, "intent": "director"},
                    )
                ]
            )
        return ModelResponse(parts=[ToolCallPart("submit_diagnosis", submission)])

    return play


# -- visibility (the failing-first regression) --------------------------------


def test_the_first_model_request_carries_the_full_evidence_tool_catalog(
    project_root: Path,
) -> None:
    """The six granted tools, by exact name and argument contract, in the
    initial user payload — the contract the paused prefix never had."""

    seen: list[list] = []

    def play(messages, _info) -> ModelResponse:
        seen.append(list(messages))
        return ModelResponse(parts=[ToolCallPart("submit_diagnosis", _abstain())])

    runner = _capture_runner(project_root, play)
    result = asyncio.run(runner.diagnose())

    assert result.diagnosis.status.value == "INSUFFICIENT_EVIDENCE"
    catalog = catalog_from_messages(seen[0])
    assert [item["name"] for item in catalog] == [
        "get_dbt_lineage",
        "get_dbt_node_error",
        "get_dbt_run_results",
        "get_relation_data_profile",
        "get_relation_history",
        "get_relation_schema",
    ]
    by_name = {item["name"]: item for item in catalog}
    schema = by_name["get_dbt_node_error"]["input_schema"]
    assert schema["required"] == ["run_id", "node_id"]
    assert schema["additionalProperties"] is False
    assert all(
        property["type"] == "string" for property in schema["properties"].values()
    )


def test_a_director_working_only_from_the_messages_plans_and_receives_a_receipt(
    project_root: Path,
) -> None:
    """No answer keys: name and arguments resolved from the delivered catalog
    alone must clear validation and earn a real backend receipt."""

    seen: list[list] = []
    chosen: dict = {}
    director = _catalog_director("aggregate profile", _abstain(), chosen)

    def play(messages, _info) -> ModelResponse:
        seen.append(list(messages))
        return director(messages, _info)

    runner = _capture_runner(project_root, play)
    result = asyncio.run(runner.diagnose())

    assert runner.controller.snapshot()["plan_steps_executed"] == 1
    assert runner.controller.snapshot()["plan_refusals_used"] == 0
    # The chosen relation came from the run's own public projection, whatever
    # the resolved context happens to grant — not from this test's expectations.
    payload = _payload_of(_user_texts(seen[0])[-1])
    granted = [name for names in payload["observable_relations"].values() for name in names]
    assert chosen["name"] == "get_relation_data_profile"
    assert chosen["arguments"]["relation_name"] in granted
    assert [record.subject for record in result.evidence_records] == [
        f"get_relation_data_profile:{chosen['arguments']['relation_name']}"
    ]
    assert result.evidence_records, "the plan step must return real evidence"


def test_the_catalog_stays_visible_in_later_turns_without_re_injection(
    project_root: Path,
) -> None:
    """One initial injection: every later turn still reads the same catalog
    from the message history, and no second copy appears."""

    seen: list[list] = []

    def play(messages, _info) -> ModelResponse:
        seen.append(list(messages))
        if len(seen) == 1:
            name, arguments = "get_dbt_run_results", {"run_id": RUN_ID}
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "plan_step",
                        {"tool_name": name, "arguments": arguments, "intent": "read"},
                    )
                ]
            )
        return ModelResponse(parts=[ToolCallPart("submit_diagnosis", _abstain())])

    runner = _capture_runner(project_root, play)
    asyncio.run(runner.diagnose())

    first = catalog_from_messages(seen[0])
    for turn in seen[1:]:
        assert catalog_from_messages(turn) == first
    # No re-injection: the payload appears once per user message, and there is
    # exactly one user message — the initial one.
    assert len(_user_texts(seen[-1])) == 1


# -- catalog generation (pure, same-source) -----------------------------------

_V1_NAMES = sorted(
    {
        "get_dbt_run_results",
        "get_dbt_node_error",
        "get_dbt_lineage",
        "get_relation_schema",
        "get_relation_data_profile",
        "get_relation_history",
    }
)


def test_the_v1_catalog_is_the_full_grant_with_the_identity_schemas() -> None:
    catalog = planner_tool_catalog(PROTOCOL_TOOL_ALLOWLIST, EVIDENCE_TOOLS_V1_VERSION)

    assert [item["name"] for item in catalog] == _V1_NAMES
    identity_schemas = {
        entry["name"]: entry["input_schema"]
        for entry in obligation_tool_schemas(TOOL_OBLIGATIONS)
    }
    for item in catalog:
        assert set(item) == {"name", "description", "input_schema", "argument_notes"}
        assert item["description"].strip()
        assert item["argument_notes"].strip()
        # Same-source signature: the model-visible schema IS the identity
        # schema, not a parallel copy.
        assert item["input_schema"] == identity_schemas[item["name"]]
    assert identity_schemas["get_dbt_node_error"]["required"] == ["run_id", "node_id"]


def test_the_v2_catalog_extends_the_surface_without_crossing_it() -> None:
    catalog = planner_tool_catalog(EVIDENCE_V2_TOOL_ALLOWLIST, EVIDENCE_TOOLS_V2_VERSION)
    names = [item["name"] for item in catalog]

    assert names == sorted(_V1_NAMES + ["get_dbt_node_definition",
                                        "get_relation_schema_expectation"])
    batch = {item["name"]: item for item in catalog}["get_relation_schema_expectation"]
    assert batch["input_schema"]["required"] == ["relation_names"]
    assert "comma-joined" in batch["argument_notes"]
    assert "8 targets" in batch["argument_notes"]
    # The v1 catalog never offers the v2 batch tools.
    v1_names = {item["name"] for item in planner_tool_catalog(
        PROTOCOL_TOOL_ALLOWLIST, EVIDENCE_TOOLS_V1_VERSION)}
    assert not v1_names & {"get_relation_schema_expectation", "get_dbt_node_definition"}


def test_a_granted_subset_stays_name_sorted_regardless_of_input_order() -> None:
    granted = ["get_relation_schema", "get_dbt_run_results", "get_dbt_lineage"]
    catalog = planner_tool_catalog(granted, EVIDENCE_TOOLS_V1_VERSION)

    assert [item["name"] for item in catalog] == sorted(granted)


def test_an_empty_grant_is_an_empty_catalog_not_a_full_fallback() -> None:
    assert planner_tool_catalog(set(), EVIDENCE_TOOLS_V1_VERSION) == []


@pytest.mark.parametrize(
    ("grant", "surface"),
    [
        ({"run_sql"}, EVIDENCE_TOOLS_V1_VERSION),
        (PROTOCOL_TOOL_ALLOWLIST | {"get_relation_schema_expectation"},
         EVIDENCE_TOOLS_V1_VERSION),
    ],
)
def test_an_unknown_granted_name_fails_before_any_agent_starts(
    grant, surface
) -> None:
    with pytest.raises(ValueError, match="outside the"):
        planner_tool_catalog(grant, surface)


def test_runner_construction_fails_on_an_unknown_granted_tool(
    project_root: Path,
) -> None:
    session = StrategySession(
        run_id=RUN_ID,
        tools=_PlannerTools(),
        context=_context(),
        declaration=_declaration(),
        allowlist=frozenset({"get_dbt_run_results", "run_sql"}),
    )
    with pytest.raises(ValueError, match="outside the"):
        EvidencePlannerRunner.for_run(
            RUN_ID,
            SimpleNamespace(),
            project_root,
            model=FunctionModel(lambda _messages, _info: None),
            model_identity=ModelIdentity("test", "scripted"),
            session=session,
        )


def test_a_v2_context_delivers_the_v2_catalog() -> None:
    template = _context()
    v2_context = ObservableRunContext(
        run_id=template.run_id,
        artifact_dir=template.artifact_dir,
        runtime={
            "schema_version": "p1.runtime.v2",
            "observable_relations": {"schema": ["raw_payments"]},
        },
        incident_brief=template.incident_brief,
    )
    session = StrategySession(
        run_id=RUN_ID,
        tools=_PlannerTools(),
        context=v2_context,
        declaration=_declaration(),
        allowlist=EVIDENCE_V2_TOOL_ALLOWLIST,
    )
    runner = EvidencePlannerRunner(
        run_id=RUN_ID,
        context=v2_context,
        session=session,
        model=FunctionModel(lambda _messages, _info: None),
        model_identity=ModelIdentity("test", "scripted"),
    )

    assert [item["name"] for item in runner._catalog] == sorted(
        _V1_NAMES + ["get_dbt_node_definition", "get_relation_schema_expectation"]
    )


# -- every catalog tool is callable through the real runner -------------------


@pytest.mark.parametrize(
    ("keyword", "tool"),
    [
        ("run results summary", "get_dbt_run_results"),
        ("failure detail", "get_dbt_node_error"),
        ("neighbors", "get_dbt_lineage"),
        ("column list", "get_relation_schema"),
        ("aggregate profile", "get_relation_data_profile"),
        ("history series", "get_relation_history"),
    ],
)
def test_each_catalog_entry_plans_and_earns_a_real_receipt(
    project_root: Path, keyword: str, tool: str
) -> None:
    chosen: dict = {}
    director = _catalog_director(keyword, _abstain(), chosen)

    runner = _capture_runner(project_root, director)
    result = asyncio.run(runner.diagnose())

    assert chosen["name"] == tool
    assert runner.controller.snapshot()["plan_steps_executed"] == 1
    assert runner.controller.snapshot()["plan_refusals_used"] == 0
    assert result.evidence_records
    assert result.evidence_records[0].subject.startswith(f"{tool}:")


# -- name rejection, then correction from the delivered catalog ----------------


def test_a_rejected_name_is_corrected_from_the_catalog(
    project_root: Path,
) -> None:
    """The paused prefix's observed guess `relation_schema` is refused with the
    original code; the next turn resolves the real name from the catalog."""

    wrong = ("plan_step", {"tool_name": "relation_schema",
                           "arguments": {"relation_name": "raw_payments"},
                           "intent": "guessed name"})
    chosen: dict = {}
    director = _catalog_director("column list", _abstain(), chosen)

    def play(messages, _info) -> ModelResponse:
        if not getattr(play, "used_script", False):
            play.used_script = True  # type: ignore[attr-defined]
            return ModelResponse(parts=[ToolCallPart(*wrong)])
        return director(messages, _info)

    runner = _capture_runner(project_root, play)
    result = asyncio.run(runner.diagnose())

    snapshot = runner.controller.snapshot()
    assert snapshot["refusals_by_code"] == {"PLAN_TOOL_NOT_ALLOWLISTED": 1}
    assert snapshot["plan_steps_executed"] == 1
    # Tool attempts only move on execution: one refused declaration, one call.
    assert result.metrics.tool_call_attempts == 1
    assert chosen["name"] == "get_relation_schema"
    assert result.evidence_records[0].subject.startswith("get_relation_schema:")


# -- argument and permission boundaries keep their original verdicts ----------


@pytest.mark.parametrize(
    ("arguments", "code"),
    [
        ({"run_id": RUN_ID}, "PLAN_ARGUMENTS_INVALID"),  # missing node_id
        ({"run_id": RUN_ID, "node_id": NODE, "extra": "x"}, "PLAN_ARGUMENTS_INVALID"),
        ({"run_id": RUN_ID, "node_id": 7}, "PLAN_ARGUMENTS_INVALID"),  # wrong type
        ({"run_id": "someone-elses-run", "node_id": NODE}, "PLAN_RUN_SCOPE_MISMATCH"),
    ],
)
def test_argument_boundary_verdicts_are_unchanged(
    project_root: Path, arguments: dict, code: str
) -> None:
    declaration = ("plan_step", {"tool_name": "get_dbt_node_error",
                                 "arguments": arguments, "intent": "boundary"})
    runner = _capture_runner(
        project_root, _scripted([declaration, ("submit_diagnosis", _abstain())])
    )
    result = asyncio.run(runner.diagnose())

    assert runner.controller.snapshot()["refusals_by_code"] == {code: 1}
    assert result.metrics.tool_call_attempts == 0


def test_a_legal_signature_can_still_meet_a_real_backend_refusal(
    project_root: Path,
) -> None:
    backend = _PlannerTools(refuse=("get_relation_schema",))
    chosen: dict = {}
    director = _catalog_director("column list", _abstain(), chosen)

    runner = _capture_runner(project_root, director, backend=backend)
    result = asyncio.run(runner.diagnose())

    assert chosen["name"] == "get_relation_schema"
    assert runner.controller.snapshot()["plan_steps_executed"] == 1
    tool_events = [event for event in result.trace if event.event_type == "TOOL_CALL"]
    assert [event.error_code for event in tool_events] == ["RELATION_NOT_ALLOWED"]
    assert not result.evidence_records
    # The real backend refusal is not a plan refusal: the budget is untouched.
    assert runner.controller.snapshot()["plan_refusals_used"] == 0


def test_two_plan_refusals_block_a_third_catalog_derived_plan(
    project_root: Path,
) -> None:
    wrong = ("plan_step", {"tool_name": "get_relation_profile",
                           "arguments": {"relation_name": "raw_payments"},
                           "intent": "guessed"})
    chosen: dict = {}
    director = _catalog_director("aggregate profile", _abstain(), chosen)

    def play(messages, _info) -> ModelResponse:
        if getattr(play, "calls", 0) < 2:
            play.calls = getattr(play, "calls", 0) + 1  # type: ignore[attr-defined]
            return ModelResponse(parts=[ToolCallPart(*wrong)])
        return director(messages, _info)

    runner = _capture_runner(project_root, play)
    result = asyncio.run(runner.diagnose())

    snapshot = runner.controller.snapshot()
    assert snapshot["plan_refusals_used"] == 2
    assert snapshot["plan_operations_blocked"] >= 1
    assert snapshot["plan_steps_executed"] == 0
    assert result.metrics.tool_call_attempts == 0
    # The submission channel is not the plan budget.
    assert runner.session.final_diagnosis is not None
    assert result.diagnosis.status.value == "INSUFFICIENT_EVIDENCE"


# -- privacy: the catalog is a function of public grants only ------------------


def test_the_catalog_ignores_everything_but_the_public_grant(
    project_root: Path,
) -> None:
    seen: list[list] = []

    def play(messages, _info) -> ModelResponse:
        seen.append(list(messages))
        return ModelResponse(parts=[ToolCallPart("submit_diagnosis", _abstain())])

    template = _context()
    private_context = ObservableRunContext(
        run_id=template.run_id,
        artifact_dir=template.artifact_dir,
        runtime={
            "schema_version": "p1.runtime.v1",
            "observable_relations": {"profile": ["raw_orders"]},
            "private_expectation_sentinel": "PRIVATE-SENTINEL-DO-NOT-SHOW",
        },
        incident_brief=template.incident_brief,
    )
    session = StrategySession(
        run_id=RUN_ID,
        tools=_PlannerTools(),
        context=private_context,
        declaration=_declaration(),
    )
    plain_runner = _capture_runner(project_root, play)
    private_runner = EvidencePlannerRunner.for_run(
        RUN_ID,
        SimpleNamespace(),
        project_root,
        model=FunctionModel(play),
        model_identity=ModelIdentity("test", "scripted"),
        session=session,
    )
    asyncio.run(plain_runner.diagnose())
    asyncio.run(private_runner.diagnose())

    # Same public grant, different run content: identical catalogs.
    assert plain_runner._catalog == private_runner._catalog
    assert "PRIVATE-SENTINEL-DO-NOT-SHOW" not in json.dumps(
        [
            str(part)
            for turn in seen
            for message in turn
            for part in getattr(message, "parts", ())
        ]
    )


# -- identity: the catalog is bound into the planner's policy identity --------


def test_the_planner_identity_is_bumped_to_v2() -> None:
    identity = evidence_planner_policy_identity()

    assert identity.strategy_prompt_version == "p1.planner.v2"
    assert identity.controller_protocol_version == "p1.planner_controller.v2"
    # The signature table itself is unchanged, so its digest keeps the v1 value.
    assert identity.tool_schema_sha256 == _digest(obligation_tool_schemas())


def test_the_model_visible_catalog_equals_the_identity_source(
    project_root: Path,
) -> None:
    seen: list[list] = []

    def play(messages, _info) -> ModelResponse:
        seen.append(list(messages))
        return ModelResponse(parts=[ToolCallPart("submit_diagnosis", _abstain())])

    runner = _capture_runner(project_root, play)
    asyncio.run(runner.diagnose())

    delivered = catalog_from_messages(seen[0])
    expected = planner_tool_catalog(PROTOCOL_TOOL_ALLOWLIST, EVIDENCE_TOOLS_V1_VERSION)
    assert delivered == expected
    projection = planner_controller_payload()["catalog_projection"]
    assert projection["field_name"] == "evidence_tool_catalog"
    # Identity binds the FULL surface; the run delivers the granted subset.
    assert projection["full_catalog"] == planner_tool_catalog(
        set(TOOL_OBLIGATIONS), EVIDENCE_TOOLS_V1_VERSION
    )
    assert {item["name"] for item in delivered} <= {
        item["name"] for item in projection["full_catalog"]
    }


def test_catalog_mutations_change_the_controller_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = evidence_planner_policy_identity().controller_protocol_sha256

    import data_incident_gym.evidence_planner as planner_module

    original = planner_module._TOOL_DESCRIPTIONS["get_dbt_lineage"]
    monkeypatch.setitem(
        planner_module._TOOL_DESCRIPTIONS,
        "get_dbt_lineage",
        original + " (mutated)",
    )
    assert (
        evidence_planner_policy_identity().controller_protocol_sha256 != baseline
    )
    monkeypatch.undo()

    original_note = planner_module._TOOL_ARGUMENT_NOTES["get_dbt_lineage"]
    monkeypatch.setitem(
        planner_module._TOOL_ARGUMENT_NOTES, "get_dbt_lineage", original_note + "!"
    )
    assert (
        evidence_planner_policy_identity().controller_protocol_sha256 != baseline
    )
    monkeypatch.undo()

    # A dropped entry field (a changed catalog structure) must not slip through
    # an unchanged identity either.
    real_catalog = planner_module.planner_tool_catalog

    def stripped(allowlist, surface=EVIDENCE_TOOLS_V1_VERSION):  # noqa: ANN001
        return [
            {key: value for key, value in item.items() if key != "argument_notes"}
            for item in real_catalog(allowlist, surface)
        ]

    monkeypatch.setattr(planner_module, "planner_tool_catalog", stripped)
    try:
        assert (
            evidence_planner_policy_identity().controller_protocol_sha256 != baseline
        )
    finally:
        monkeypatch.undo()


def test_kernel_and_static_identities_are_untouched() -> None:
    kernel = policy_surface_for_strategy(DiagnosticStrategy.DIAGNOSTIC_KERNEL)
    static = policy_surface_for_strategy(DiagnosticStrategy.STATIC_SKILL)

    assert kernel.policy_identity.strategy_prompt_version == "p1.kernel.v18"
    assert kernel.policy_identity.controller_protocol_version == "p1.controller.v22"
    assert static.policy_identity.strategy_prompt_version == "p1.static.v5"
    assert static.policy_identity.controller_protocol_version == "p1.controller.v22"
    # The shared default declaration still declares the two shared inputs only.
    assert builtin_declaration(
        model_provider="x", model_name="y", deterministic=False
    ).visible_context == ("incident_brief", "relation_whitelist")


def test_the_planner_declaration_discloses_the_catalog_marker(
    project_root: Path,
) -> None:
    """The marker rides the runner's own session construction; an injected
    session keeps whatever declaration the test wired (the normal path is
    what carries the disclosure)."""

    runner = EvidencePlannerRunner.for_run(
        RUN_ID,
        SimpleNamespace(),
        project_root,
        model=FunctionModel(lambda _messages, _info: None),
        model_identity=ModelIdentity("test", "scripted"),
        backend=_PlannerTools(),
    )

    assert runner.session.declaration.visible_context == (
        "incident_brief", "relation_whitelist", "evidence_tool_catalog"
    )
    # An injected session's declaration is the caller's choice — and the
    # catalog still follows that session's actual authorization.
    injected = _capture_runner(
        project_root, _scripted([("submit_diagnosis", _abstain())])
    )
    assert "evidence_tool_catalog" not in injected.session.declaration.visible_context
    assert injected._catalog == planner_tool_catalog(
        PROTOCOL_TOOL_ALLOWLIST, EVIDENCE_TOOLS_V1_VERSION
    )


# -- probe wiring: the offline probe sees the same catalog ---------------------


def test_the_offline_probe_receives_the_catalog_and_completes_its_loop() -> None:
    from data_incident_gym.planner_probe import run_planner_compatibility_probe

    seen: list[list] = []

    def play(messages, _info) -> ModelResponse:
        seen.append(list(messages))
        if len(seen) == 1:
            name, arguments = _request_from_catalog(
                catalog_from_messages(messages), "run results summary", messages
            )
            return ModelResponse(
                parts=[ToolCallPart("plan_step", {
                    "tool_name": name, "arguments": arguments, "intent": "probe",
                })]
            )
        return ModelResponse(parts=[ToolCallPart("submit_diagnosis", {
            "status": "INSUFFICIENT_EVIDENCE",
            "summary": "Probe loop completed without decisive evidence.",
            "unresolved_evidence": [{
                "evidence_kind": "RELATION_DATA_PROFILE",
                "subject": "raw_payments",
                "reason_code": "NOT_OBSERVABLE",
            }],
            "confidence": 0.2,
        })])

    result = asyncio.run(run_planner_compatibility_probe(
        FunctionModel(play), ModelIdentity("test", "probe-scripted"),
        timeout_seconds=30,
    ))

    assert result.passed, result.detail
    assert result.observed == "PLAN_LOOP_COMPLETED"
    assert catalog_from_messages(seen[0]) == planner_tool_catalog(
        PROTOCOL_TOOL_ALLOWLIST, EVIDENCE_TOOLS_V1_VERSION
    )
    declaration_marker = "evidence_tool_catalog"
    assert declaration_marker  # the probe's own declaration carries it too
    from data_incident_gym.planner_agent import PLANNER_VISIBLE_CONTEXT

    assert PLANNER_VISIBLE_CONTEXT == (
        "incident_brief", "relation_whitelist", "evidence_tool_catalog"
    )
