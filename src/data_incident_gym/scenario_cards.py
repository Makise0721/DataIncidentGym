"""Scenario cards and A/B symmetry checks (T06, management plane).

A scenario card is the management-plane description of one catalog case: its
fault mechanism, its healthy control, the decisive-evidence difference to its
A/B partner, its solvability argument from certification, the read-only tool
path, the budget, the legitimate alternative diagnoses, and the ground-truth
forbidden zone.

Cards read the private scenario contract, but they never travel in the
strategy-facing direction: nothing on the diagnosis plane may import this
module, and every card field restates either public surface facts or
management-plane certification facts.

``ab_symmetry_findings`` enforces the A/B design rule: paired scenarios must
present identical surface symptoms (incident brief, seed, injection contract)
and may differ only in the answer-side fields that carry the decisive
evidence difference.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, StrictBool, StrictStr, model_validator

from data_incident_gym.config import PROJECT_ROOT
from data_incident_gym.diagnostic_agent import (
    MODEL_REQUEST_LIMIT,
    OUTPUT_RETRY_LIMIT,
    TIMEOUT_SECONDS,
    TOOL_CALL_LIMIT,
)
from data_incident_gym.fixed_rule import FIXED_RULE_TOOL_NAMES
from data_incident_gym.reference_solver import REFERENCE_ANALYST_TOOL_LIMIT
from data_incident_gym.scenario_certification import (
    CertificationFinding,
    ScenarioCertification,
)
from data_incident_gym.scenarios import (
    AddNullableColumnMutation,
    Answerability,
    ColumnRenameMutation,
    ColumnTypeMutation,
    DeletePaymentRowsMutation,
    DuplicatePaymentRowsMutation,
    NoMutation,
    OrphanPaymentRowsMutation,
    ScenarioMutation,
    ScenarioSpec,
    SetFieldNullMutation,
    VariantRole,
    load_scenario_spec,
)

CARD_SCHEMA_VERSION = "p1.scenario_card.v1"
SCENARIO_CARD_BUDGET = (
    f"{MODEL_REQUEST_LIMIT} model requests / {TOOL_CALL_LIMIT} tool calls / "
    f"{OUTPUT_RETRY_LIMIT} retries / {TIMEOUT_SECONDS}s"
)

# The catalog's real A/B pairs. Both members answer the same incident signal;
# the confirmable variant observes the decisive evidence the insufficient
# variant cannot reach.
AB_SCENARIO_PAIRS: tuple[tuple[str, str], ...] = (
    ("schema_type_change_order_customer_a", "schema_type_change_order_customer_b"),
    ("required_null_order_customer_a", "required_null_order_customer_b"),
    ("duplicate_payment_coupon_a", "duplicate_payment_coupon_b"),
    ("orphan_payment_coupon_a", "orphan_payment_coupon_b"),
    ("silent_payment_drop_partition_a", "silent_payment_drop_partition_b"),
    # T12 dev-extension pairs (payments side, recombined frozen mutations).
    ("required_null_payment_id_distractor_a", "required_null_payment_id_distractor_b"),
    ("type_change_payment_amount_drift_a", "type_change_payment_amount_drift_b"),
    # T13 public-evidence pairs (design §4.1): mirror pairs whose type deviation
    # falls on the left or the right origin of the same failing join.
    ("schema_type_change_raw_customer_id_a", "schema_type_change_raw_customer_id_b"),
    ("schema_type_change_raw_order_user_id_a", "schema_type_change_raw_order_user_id_b"),
)

# Scenarios that are themselves healthy controls (no injection, NO_INCIDENT).
HEALTH_CONTROL_SCENARIO_IDS: tuple[str, ...] = (
    "order_volume_pattern_a",
    "order_volume_within_sla",
)

# Fields an A/B pair may legitimately differ in: the answer side. Everything
# else — surface symptoms and identity constants — must be identical. Any
# difference outside this set is a symmetry finding.
_ALLOWED_AB_DIFFERENCE_FIELDS = frozenset(
    {
        "incident_case_id",
        "variant_role",
        "answerability",
        "expected_status",
        "ground_truth_or_acceptable_root_causes",
        "observable_evidence_contract",
        "required_evidence_types",
        "distractors",
        "direct_failure",
        "affected_assets",
    }
)

_AB_ROLE_STRUCTURE = (
    VariantRole.TEST_CONFIRMABLE,
    VariantRole.TEST_INSUFFICIENT,
)
_AB_ANSWERABILITY_STRUCTURE = (Answerability.CONFIRMABLE, Answerability.INSUFFICIENT)


def ab_partner(case_id: str) -> str | None:
    """The paired A/B case id, or None when the scenario is unpaired."""

    for left, right in AB_SCENARIO_PAIRS:
        if case_id == left:
            return right
        if case_id == right:
            return left
    return None


class CardError(RuntimeError):
    def __init__(self, code: str, *, detail: str | None = None) -> None:
        self.code = code
        self.detail = detail
        super().__init__(code if detail is None else f"{code}: {detail}")
        self.__cause__ = None
        self.__context__ = None


def _surface_differences(a: ScenarioSpec, b: ScenarioSpec) -> tuple[str, ...]:
    """Dotted paths where the two specs differ outside the allowed fields."""

    left = a.model_dump(mode="json")
    right = b.model_dump(mode="json")
    differences: list[str] = []

    def walk(lh: object, rh: object, prefix: str) -> None:
        if type(lh) is not type(rh):
            differences.append(prefix)
            return
        if isinstance(lh, dict) and isinstance(rh, dict):
            for key in sorted(set(lh) | set(rh)):
                if key not in lh or key not in rh:
                    differences.append(f"{prefix}.{key}" if prefix else key)
                else:
                    walk(lh[key], rh[key], f"{prefix}.{key}" if prefix else key)
            return
        if isinstance(lh, list) and isinstance(rh, list):
            if len(lh) != len(rh):
                differences.append(f"{prefix}[] ({len(lh)} vs {len(rh)} entries)")
                return
            for index, (litem, ritem) in enumerate(zip(lh, rh, strict=True)):
                walk(litem, ritem, f"{prefix}[{index}]")
            return
        if lh != rh:
            differences.append(prefix)

    for key in sorted(set(left) | set(right)):
        if key in _ALLOWED_AB_DIFFERENCE_FIELDS:
            continue
        walk(left[key], right.get(key), key)
    return tuple(differences)


def ab_symmetry_findings(a: ScenarioSpec, b: ScenarioSpec) -> tuple[CertificationFinding, ...]:
    """Fixed-code findings stating whether two specs form a sound A/B pair."""

    family_ok = a.fault_family is b.fault_family and a.suite is b.suite
    ids_ok = a.incident_case_id != b.incident_case_id
    surface = _surface_differences(a, b)
    roles = {a.variant_role, b.variant_role}
    answerabilities = {a.answerability, b.answerability}
    structure_ok = roles == set(_AB_ROLE_STRUCTURE) and answerabilities == set(
        _AB_ANSWERABILITY_STRUCTURE
    )
    return (
        CertificationFinding(
            code="AB_PAIR_FAULT_FAMILY_MISMATCH",
            satisfied=family_ok,
            detail=None
            if family_ok
            else f"{a.fault_family.value}/{a.suite} vs {b.fault_family.value}/{b.suite}",
        ),
        CertificationFinding(
            code="AB_PAIR_CASE_ID_COLLISION",
            satisfied=ids_ok,
            detail=None if ids_ok else f"both specs declare {a.incident_case_id}",
        ),
        CertificationFinding(
            code="AB_PAIR_SURFACE_MISMATCH",
            satisfied=not surface,
            detail=None if not surface else "; ".join(surface),
        ),
        CertificationFinding(
            code="AB_PAIR_ROLE_STRUCTURE",
            satisfied=structure_ok,
            detail=None
            if structure_ok
            else (
                f"roles {sorted(role.value for role in roles if role)}, "
                f"answerability {sorted(item.value for item in answerabilities)}"
            ),
        ),
    )


class HealthyControl(BaseModel):
    """Healthy-control fact for one scenario.

    ``is_control`` marks scenarios that are themselves healthy controls (no
    injection, expected NO_INCIDENT). ``control_case_id`` names the paired
    healthy-control scenario for an incident scenario, when the catalog
    declares one.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    is_control: StrictBool
    control_case_id: StrictStr | None = None

    @model_validator(mode="after")
    def validate_control(self) -> HealthyControl:
        if self.is_control and self.control_case_id is not None:
            raise ValueError("a healthy control does not pair with another control")
        return self


class AbPairEvidence(BaseModel):
    """The decisive-evidence difference between this card and its partner."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    partner_case_id: StrictStr
    role: StrictStr
    decisive_difference: StrictStr


class Solvability(BaseModel):
    """Solvability argument restated from the certification run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    certified: StrictBool | None = None
    summary: StrictStr | None = None


class GroundTruthForbiddenZone(BaseModel):
    """Restatement of the scenario's forbidden-leakage contract."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    leakage_codes: tuple[StrictStr, ...]
    private_paths: tuple[StrictStr, ...]

    @model_validator(mode="after")
    def validate_non_empty(self) -> GroundTruthForbiddenZone:
        if not self.leakage_codes or not self.private_paths:
            raise ValueError("forbidden zone must name leakage codes and private paths")
        return self


class ScenarioCardVersion(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    card: StrictStr
    scenario: StrictStr


class ScenarioCard(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: StrictStr
    fault_mechanism: StrictStr
    healthy_control: HealthyControl
    ab_pair: AbPairEvidence | None
    solvability: Solvability
    readonly_path: tuple[StrictStr, ...]
    budget: StrictStr
    legitimate_alternatives: tuple[StrictStr, ...]
    ground_truth_forbidden_zone: GroundTruthForbiddenZone
    source: StrictStr
    version: ScenarioCardVersion

    def completeness_issues(self) -> tuple[str, ...]:
        """Card fields that still lack a decision (used by admission)."""

        issues: list[str] = []
        if not self.fault_mechanism:
            issues.append("fault_mechanism")
        if not self.readonly_path:
            issues.append("readonly_path")
        if not self.budget:
            issues.append("budget")
        if self.solvability.certified is None or not self.solvability.summary:
            issues.append("solvability")
        if not self.source:
            issues.append("source")
        return tuple(issues)


def _mutation_summary(mutation: ScenarioMutation) -> str:
    if isinstance(mutation, ColumnRenameMutation):
        return f"COLUMN_RENAME({mutation.relation}.{mutation.from_column}->{mutation.to_column})"
    if isinstance(mutation, ColumnTypeMutation):
        return (
            f"COLUMN_TYPE_CHANGE({mutation.relation}.{mutation.column}:"
            f"{mutation.from_type}->{mutation.to_type})"
        )
    if isinstance(mutation, AddNullableColumnMutation):
        return f"ADD_NULLABLE_COLUMN({mutation.relation}.{mutation.column}:{mutation.data_type})"
    if isinstance(mutation, SetFieldNullMutation):
        return (
            f"SET_FIELD_NULL({mutation.purpose} {mutation.relation}.{mutation.column} "
            f"where {mutation.selector_column}={mutation.selector_value})"
        )
    if isinstance(mutation, DuplicatePaymentRowsMutation):
        return f"DUPLICATE_PAYMENT_ROWS({mutation.mode})"
    if isinstance(mutation, OrphanPaymentRowsMutation):
        return f"ORPHAN_PAYMENT_ROWS({mutation.mode})"
    if isinstance(mutation, DeletePaymentRowsMutation):
        return f"DELETE_PAYMENT_ROWS({mutation.mode})"
    if isinstance(mutation, NoMutation):
        return "NO_MUTATION"
    raise CardError("CARD_MUTATION_INVALID", detail=str(mutation))


def _fault_mechanism(scenario: ScenarioSpec) -> str:
    mutations = scenario.reset_and_injection_contract.mutations
    summaries = "; ".join(_mutation_summary(mutation) for mutation in mutations)
    return f"{scenario.fault_family.value}: {summaries}"


def _readonly_path(scenario: ScenarioSpec) -> tuple[str, ...]:
    """Read-only tools the reference solution exercises for this scenario.

    Restates the certification run's tool path from the public contract: the
    dbt readers follow the required evidence types and the relation readers
    only appear when the scenario whitelists at least one such relation.
    """

    contract = scenario.observable_evidence_contract
    relation_declared = {
        "get_relation_schema": bool(contract.schema_relations),
        "get_relation_data_profile": bool(contract.profile_relations),
        "get_relation_history": bool(contract.history_relations),
    }
    required = set(scenario.required_evidence_types)
    evidence_declared = {
        "get_dbt_run_results": "DBT_RUN_RESULTS" in required,
        "get_dbt_node_error": "DBT_NODE_ERROR" in required,
        "get_dbt_lineage": "DBT_LINEAGE" in required,
        "get_relation_schema": "RELATION_SCHEMA" in required,
        "get_relation_data_profile": "RELATION_DATA_PROFILE" in required,
        "get_relation_history": "RELATION_HISTORY" in required,
    }
    return tuple(
        name
        for name in FIXED_RULE_TOOL_NAMES
        if evidence_declared[name] and relation_declared.get(name, True)
    )


def _legitimate_alternatives(scenario: ScenarioSpec) -> tuple[str, ...]:
    causes = scenario.ground_truth_or_acceptable_root_causes
    if scenario.answerability is Answerability.INSUFFICIENT:
        # Evidence cannot decide between the accepted causes, so every one of
        # them is a legitimate diagnosis; none is privileged as ground truth.
        return causes
    return causes[1:]


def _healthy_control(scenario: ScenarioSpec) -> HealthyControl:
    if scenario.incident_case_id in HEALTH_CONTROL_SCENARIO_IDS:
        return HealthyControl(is_control=True)
    return HealthyControl(is_control=False)


def _decisive_difference(confirmable: ScenarioSpec, insufficient: ScenarioSpec) -> str:
    left = confirmable.observable_evidence_contract
    right = insufficient.observable_evidence_contract
    lost: list[str] = []
    for kind, left_relations, right_relations in (
        ("schema", left.schema_relations, right.schema_relations),
        ("profile", left.profile_relations, right.profile_relations),
        ("history", left.history_relations, right.history_relations),
    ):
        missing = [relation for relation in left_relations if relation not in right_relations]
        if missing:
            lost.append(f"{kind} {','.join(missing)}")
    gaps = ", ".join(
        f"{gap.gap_kind}({gap.subject})/{gap.reason_code}"
        for gap in right.unresolved_gaps
    )
    parts: list[str] = []
    if lost:
        parts.append(f"insufficient variant loses read access to {'; '.join(lost)}")
    if gaps:
        parts.append(f"carries unresolved gaps {gaps}")
    if not parts:
        return "no observable-evidence difference declared"
    return "; ".join(parts)


def _ab_pair(scenario: ScenarioSpec, partner: ScenarioSpec) -> AbPairEvidence:
    confirmable, insufficient = (
        (scenario, partner)
        if scenario.answerability is Answerability.CONFIRMABLE
        else (partner, scenario)
    )
    return AbPairEvidence(
        partner_case_id=partner.incident_case_id,
        role=scenario.variant_role.value if scenario.variant_role else "UNROLE",
        decisive_difference=_decisive_difference(confirmable, insufficient),
    )


def _solvability(certification: ScenarioCertification | None) -> Solvability:
    if certification is None:
        return Solvability(certified=None, summary=None)
    if certification.certified:
        calls = (
            f"{certification.run.tool_calls}/{REFERENCE_ANALYST_TOOL_LIMIT}"
            if certification.run is not None
            else "n/a"
        )
        return Solvability(
            certified=True,
            summary=(
                "the public-evidence reference analyst reaches the expected answer "
                f"within the reference budget (tool calls {calls})"
            ),
        )
    classes = ",".join(certification.failure_classes) or "UNCLASSIFIED"
    return Solvability(
        certified=False,
        summary=f"not certified; certification failure classes: {classes}",
    )


def build_scenario_card(
    case_id: str,
    *,
    project_root: Path = PROJECT_ROOT,
    certification: ScenarioCertification | None = None,
) -> ScenarioCard:
    """Build the management-plane card for one catalog scenario.

    Reads the private scenario contract (cards are management-plane
    documents) plus the certification result when one exists.
    """

    scenario = load_scenario_spec(case_id, project_root)
    partner_id = ab_partner(case_id)
    ab_pair: AbPairEvidence | None = None
    if partner_id is not None:
        partner = load_scenario_spec(partner_id, project_root)
        ab_pair = _ab_pair(scenario, partner)
    return ScenarioCard(
        case_id=scenario.incident_case_id,
        fault_mechanism=_fault_mechanism(scenario),
        healthy_control=_healthy_control(scenario),
        ab_pair=ab_pair,
        solvability=_solvability(certification),
        readonly_path=_readonly_path(scenario),
        budget=SCENARIO_CARD_BUDGET,
        legitimate_alternatives=_legitimate_alternatives(scenario),
        ground_truth_forbidden_zone=GroundTruthForbiddenZone(
            leakage_codes=tuple(item.value for item in scenario.forbidden_leakage),
            private_paths=(f"config/scenarios/{case_id}.json",),
        ),
        source=f"config/scenarios/{case_id}.json",
        version=ScenarioCardVersion(card=CARD_SCHEMA_VERSION, scenario=scenario.schema_version),
    )


__all__ = [
    "AB_SCENARIO_PAIRS",
    "CARD_SCHEMA_VERSION",
    "HEALTH_CONTROL_SCENARIO_IDS",
    "SCENARIO_CARD_BUDGET",
    "AbPairEvidence",
    "CardError",
    "GroundTruthForbiddenZone",
    "HealthyControl",
    "ScenarioCard",
    "ScenarioCardVersion",
    "Solvability",
    "ab_partner",
    "ab_symmetry_findings",
    "build_scenario_card",
]
