"""Every rejection code the model can receive must carry usable feedback.

The kernel rejects a structured decision or a business call with a code, and the
controller turns that code into the message the model reads on its retry. A code
without its own message falls back to a generic sentence, which tells the model
that something was wrong but not what to change. The p1-formal-v11 recoverability
analysis (``docs/superpowers/reports/2026-09-11-p1-formal-v11-recoverability-analysis.md``)
showed two rejected cells that needed exactly one field or one declaration
corrected, so the feedback has to name the correction.

The code inventory is extracted from the source rather than hand-listed, so a new
``_error``/``KernelError``/``_PolicyError`` site without feedback fails this test.
"""

from __future__ import annotations

import re
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[2] / "src" / "data_incident_gym"
KERNEL_SOURCE = PACKAGE / "diagnostic_kernel.py"
VALIDATION_SOURCE = PACKAGE / "diagnostic_validation.py"
AGENT_SOURCE = PACKAGE / "diagnostic_agent.py"

_RAISED_CODE = re.compile(r'(?:_error|KernelError)\(\s*"([A-Z_][A-Z0-9_]*)"')
_POLICY_CODE = re.compile(r'_PolicyError\(\s*"([A-Z_][A-Z0-9_]*)"')
_FALLBACK = re.compile(r'messages\.get\(code,\s*"([^"]+)"\)')

# Codes raised only by controller-internal invariants: the model cannot reach
# them by changing its next response, and each one means the controller state and
# the object it was handed disagree.
_CONTROLLER_INTERNAL_CODES = {
    # finalize/terminate called twice on the same kernel instance.
    "KERNEL_FINALIZED",
    # a prepared call that this kernel never handed out.
    "PREPARED_CALL_INVALID",
    # closing a gap that is not open, or is not the one the call was prepared for.
    "GAP_NOT_OPEN",
    # the kernel was never constructed for this run.
    "KERNEL_NOT_INITIALIZED",
    # terminate_model_error was called with a reason outside the frozen set.
    "MODEL_ERROR_REASON_INVALID",
}


def _source_codes() -> set[str]:
    """Every rejection code a kernel or policy object can raise."""

    codes: set[str] = set()
    for path in (KERNEL_SOURCE, VALIDATION_SOURCE, AGENT_SOURCE):
        text = path.read_text(encoding="utf-8")
        pattern = _POLICY_CODE if path is AGENT_SOURCE else _RAISED_CODE
        if path is AGENT_SOURCE:
            codes.update(pattern.findall(text))
            codes.update(_RAISED_CODE.findall(text))
        else:
            codes.update(pattern.findall(text))
    return codes


def _generic_fallback() -> str:
    match = _FALLBACK.search(AGENT_SOURCE.read_text(encoding="utf-8"))
    assert match is not None, "the generic retry fallback is no longer recognisable"
    return match.group(1)


def _feedback(code: str) -> str:
    from data_incident_gym.diagnostic_agent import _kernel_retry_message

    return _kernel_retry_message(code)


def test_the_code_inventory_is_not_vacuous() -> None:
    """Guards the extraction itself, so the checks below cannot pass on empty input."""

    codes = _source_codes()

    assert len(codes) >= 30, sorted(codes)
    assert {
        "ALTERNATIVE_HYPOTHESIS_REQUIRED",
        "ASSET_CLAIM_EVIDENCE_INCOMPATIBLE",
        "ROOT_CLAIM_MISMATCH",
        "RELATION_NOT_ALLOWED",
        "HEALTH_SLA_NOT_SATISFIED",
    } <= codes


def test_every_model_facing_code_carries_specific_feedback() -> None:
    fallback = _generic_fallback()
    missing = []
    for code in sorted(_source_codes() - _CONTROLLER_INTERNAL_CODES):
        message = _feedback(code)
        body = message.removeprefix(f"{code}: ").strip()
        if fallback in message or len(body) < 30:
            missing.append((code, body))

    assert missing == [], missing


def test_controller_internal_codes_are_the_documented_exclusions() -> None:
    """The exclusion set may only shrink or change deliberately."""

    assert _source_codes() & _CONTROLLER_INTERNAL_CODES == _CONTROLLER_INTERNAL_CODES
    assert len(_CONTROLLER_INTERNAL_CODES) == 5


def test_feedback_never_leaks_private_scenario_fields() -> None:
    private = (
        "expected_status",
        "ground_truth",
        "case_id",
        "required_evidence_types",
        "expected_root_cause",
    )
    for code in sorted(_source_codes()):
        message = _feedback(code)
        assert not any(term in message for term in private), (code, message)
        assert re.search(r"ev_[0-9a-f]{64}", message) is None, (code, message)


def test_seq62_root_claim_mismatch_names_the_registered_code() -> None:
    """seq62 was rejected with an unknown ROOT_CAUSE value; offline, replacing it
    with the selected hypothesis's code was accepted with no further rejection
    (docs/superpowers/reports/2026-09-11-p1-formal-v11-recoverability-analysis.md).
    """

    message = _feedback("ROOT_CLAIM_MISMATCH")

    assert "root cause code of the selected" in message
    assert "copy that registered code" in message


def test_seq50_asset_claim_mismatch_names_what_can_support_an_asset() -> None:
    """seq50 was rejected because an affected-asset value cited only a profile;
    offline, removing that one claim accepted the decision and passed the
    evaluator (same report).
    """

    message = _feedback("ASSET_CLAIM_EVIDENCE_INCOMPATIBLE")

    assert "that same claim cites" in message
    assert "A profile cannot support an" in message
    assert "remove any asset claim" in message


def test_seq59_alternative_requirement_names_the_registration_limit() -> None:
    """seq59 registered one hypothesis and spent the tool budget, and the replay
    proved no later registration path exists, so the feedback must say both that
    two hypotheses are required and that only a business call can register one.
    """

    message = _feedback("ALTERNATIVE_HYPOTHESIS_REQUIRED")

    assert "at least two registered hypotheses" in message
    assert "registered only with a business call" in message
    assert "once the tool budget is spent" in message
