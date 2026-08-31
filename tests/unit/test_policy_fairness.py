from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from pydantic_ai.models.function import FunctionModel

from data_incident_gym.diagnosis import DiagnosticStrategy
from data_incident_gym.diagnostic_agent import (
    TOOL_NAMES,
    DiagnosisBudget,
    DiagnosisRunner,
    ModelIdentity,
)
from data_incident_gym.run_context import resolve_run_context
from data_incident_gym.scenarios import load_scenario_spec

RUN_IDS = tuple(f"{digit}" * 32 for digit in "0123456789")
CASE_IDS = (
    "schema_type_change_payment_amount",
    "schema_type_change_order_customer_a",
    "schema_type_change_order_customer_b",
    "order_volume_pattern_a",
    "required_null_payment_id",
    "required_null_order_customer_a",
    "required_null_order_customer_b",
    "duplicate_payment_record",
    "duplicate_payment_coupon_a",
    "duplicate_payment_coupon_b",
)


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        model_base_url="http://127.0.0.1:11434/v1",
        model_name="synthetic-model",
        model_api_key=SimpleNamespace(get_secret_value=lambda: "synthetic-key"),
    )


def _write_public_run(project_root: Path, *, run_id: str, case_id: str) -> None:
    scenario = load_scenario_spec(case_id)
    run_root = project_root / ".dig" / "lab" / "runs" / run_id
    run_root.mkdir(parents=True)
    (run_root / "runtime.json").write_text(
        json.dumps(
            {
                "schema_version": "p1.runtime.v1",
                "run_id": run_id,
                "dbt_exit_code": 0 if case_id == "order_volume_pattern_a" else 1,
                "artifacts": {
                    "manifest": "dbt/target/manifest.json",
                    "run_results": "dbt/target/run_results.json",
                    "dbt_log": "dbt/logs/dbt.log",
                    "schema": "schema.json",
                    "profile_snapshot": "profile_snapshot.json",
                    "incident_brief": "incident_brief.json",
                },
                "observable_relations": {
                    "schema": list(scenario.observable_evidence_contract.schema_relations),
                    "profile": list(scenario.observable_evidence_contract.profile_relations),
                    "history": list(scenario.observable_evidence_contract.history_relations),
                },
                "profile_spec_sha256": "b" * 64,
            }
        ),
        encoding="utf-8",
    )
    (run_root / "incident_brief.json").write_text(
        scenario.incident_brief.model_copy(
            update={"logical_observed_at": datetime(2026, 8, 30, tzinfo=UTC)}
        ).model_dump_json(),
        encoding="utf-8",
    )


def test_each_p1_case_exposes_identical_policy_surface(tmp_path: Path) -> None:
    settings = _settings()
    model = FunctionModel(lambda _messages, _info: None)

    for case_id, run_id in zip(CASE_IDS, RUN_IDS, strict=True):
        _write_public_run(tmp_path, run_id=run_id, case_id=case_id)
        context = resolve_run_context(run_id, project_root=tmp_path)
        public_context = context.incident_brief.model_dump_json() + json.dumps(context.runtime)
        assert case_id not in public_context
        assert "answerability" not in public_context
        assert "expected_status" not in public_context

    static, kernel = tuple(
        DiagnosisRunner.for_run(
            RUN_IDS[0],
            settings,
            strategy,
            tmp_path,
            model=model,
            tools=SimpleNamespace(),
            model_identity=ModelIdentity("synthetic", "synthetic-model"),
        )
        for strategy in DiagnosticStrategy
    )

    assert static.model_identity == kernel.model_identity
    assert static.incident_brief.model_dump_json() == kernel.incident_brief.model_dump_json()
    assert static._tool_schema_payload == kernel._tool_schema_payload
    assert static.tool_schema_sha256 == kernel.tool_schema_sha256
    assert static.final_diagnosis_schema_sha256 == kernel.final_diagnosis_schema_sha256
    assert static.budget == kernel.budget == DiagnosisBudget(8, 8, 2, 300)
    assert (
        static.policy_identity.strategy_prompt_sha256
        != kernel.policy_identity.strategy_prompt_sha256
    )
    assert static.policy_identity.controller_protocol_sha256 != (
        kernel.policy_identity.controller_protocol_sha256
    )

    assert TOOL_NAMES == (
        "get_dbt_run_results",
        "get_dbt_node_error",
        "get_relation_schema",
        "get_dbt_lineage",
        "get_relation_data_profile",
        "get_relation_history",
    )
