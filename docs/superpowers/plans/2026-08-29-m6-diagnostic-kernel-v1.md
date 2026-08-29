# M6 Diagnostic Kernel v1 and Column Type Change Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the first P1 vertical slice by adding a reproducible source-column type-change incident and replacing M5's answer-materializing controller with an explicit, auditable Diagnostic Kernel that tracks hypotheses, evidence gaps, claim-to-evidence bindings, and remaining budgets.

**Architecture:** Introduce one deep in-process Module at the diagnostic seam. Its small Interface accepts model-declared investigation intent, validates and records tool transitions, and either rejects finalization or returns a diagnosis projection; PydanticAI remains the model adapter, the existing four M3 tools remain the only evidence adapters, and the deterministic evaluator remains the only reader of Ground Truth. Preserve the canonical six-file artifact contract by persisting the final InvestigationState as a typed terminal event in trace.jsonl and rendering it in report.md.

**Tech Stack:** Windows 11, PowerShell 7, Python 3.12.10, Pydantic 2.13.4, PydanticAI 2.34.0, dbt-core 1.12.3, dbt-postgres 1.11.0, PostgreSQL 17.6, pytest 9.1.1, Ruff 0.16.4, Jinja2 3.1.6, OpenAI-compatible Xiaomi MiMo mimo-v2.5.

---

## Fixed context and execution preconditions

- The only valid M5/P0 baseline is commit c4c4cb820aa805976e77e6602e6b3f82b0ff5dfc on branch codex/m5-reimplementation-20260828.
- GitHub Actions run 33233186216 succeeded for that exact commit. It covered Ruff, unit, integration, and ordinary e2e on Ubuntu.
- The existing local branch codex/m6-20260829 points to e463ce2 and does not contain the M5 closure commit. Do not implement M6 on that stale branch.
- At execution time, use the using-git-worktrees skill to create a new isolated worktree and a new branch named codex/m6-diagnostic-kernel-v1-20260829 from exact commit c4c4cb820aa805976e77e6602e6b3f82b0ff5dfc.
- The current M5 worktree contains user-owned root Markdown changes and generated third_party/jaffle_shop/.dig output. Leave those files in the M5 worktree untouched. Copy only this plan into M6; do not copy root Markdown, generated .dig, artifacts, caches, or database output.
- Use C:\Users\29913\.config\superpowers\worktrees\DataIncidentGym\m6-diagnostic-kernel-v1-20260829 as the M6 worktree path. Copy this plan there before editing requirements, then verify source and destination bytes are identical with Get-FileHash -Algorithm SHA256.
- Approval of this plan authorizes planning and implementation after the user selects an execution mode. It does not authorize real MiMo requests, pushing, or deleting generated files; those gates remain explicit in Tasks 11 and 12.

## Approved M6 contract

1. M6 is one P1 vertical slice, not the whole P1 roadmap.
2. The two supported cases are:
   - existing schema_rename_payment_amount;
   - new schema_type_change_payment_amount, which changes raw_payments.amount from PostgreSQL integer to text and deterministically breaks stg_payments.
3. The existing schema-rename Ground Truth JSON must remain byte-for-byte unchanged as a committed Git blob. Its baseline blob OID is eb2cca9026c778f25119660affa206ce1377f46d, its baseline committed-byte SHA-256 is 9e0dee17c9e59336f4ab82d5219a203c87763bbb9d015670a6e71e3eadc18237, and its loaded canonical GroundTruth.digest() remains c2fa0d97b603c37a21d07123481b2a9d09a34dbd3aab38e6e102a55d59ce4491. Do not use the checkout-file SHA because CRLF conversion is platform-dependent.
4. The only new root-cause ontology member is SOURCE_SCHEMA_COLUMN_TYPE_CHANGED. Existing SOURCE_SCHEMA_COLUMN_RENAMED semantics remain unchanged.
5. The model must explicitly maintain at least two candidate hypotheses before a CONFIRMED result, select one supported hypothesis, refute at least one alternative, identify open evidence gaps, and bind every final claim to current-run EvidenceRecord IDs.
6. The model again owns root-cause selection, affected-asset claims, and evidence citations. The Kernel may validate, reject, normalize ordering, and project model claims into Diagnosis; it must not invent an affected asset, choose a root cause, or attach an evidence ID that the model did not provide.
7. A CONFIRMED result requires:
   - one selected SUPPORTED hypothesis;
   - at least one REFUTED alternative;
   - no unresolved evidence gap;
   - a ROOT_CAUSE claim supported by DBT_NODE_ERROR and RELATION_SCHEMA evidence;
   - one AFFECTED_ASSET claim per reported asset, each supported by compatible node-error or downstream-lineage evidence;
   - current-run scope, exact evidence identity, provenance-safe tool arguments, and remaining budget.
8. INSUFFICIENT_EVIDENCE remains a valid refusal and preserves the incomplete InvestigationState. MODEL_ERROR remains a fixed safe terminal outcome.
9. Ground Truth remains unavailable to DiagnosticKernel, DiagnosticRunner, PydanticAI messages, tool wrappers, EvidenceTools, trace, and report generation. Only IncidentLab/IncidentVerifier and DeterministicEvaluator may read it.
10. M3 still exposes exactly four read-only evidence tools. InvestigationIntent is controller metadata on those wrappers and is not a fifth evidence tool.
11. Budgets remain 8 model requests, 8 tool-call attempts, 2 output retries, and 300 seconds.
12. The canonical artifact set remains exactly metadata.json, trace.jsonl, evidence.json, diagnosis.json, evaluation.json, and report.md. The final typed InvestigationState is the last trace event and is rendered in report.md.
13. No static Skill baseline, ablation, Accuracy/F1, cross-variant score, user-defined incident DSL, free SQL, free path, write tool, automatic repair, Airflow, OpenLineage, Marquez, LangGraph, or Web UI is added in M6.
14. Local deterministic acceptance covers both cases. Real-model acceptance is exactly three independent attempts per case and at least two PASSED attempts per case; all six attempts remain in the denominator.

## Deep Module design

The Module is src/data_incident_gym/diagnostic_kernel.py. The seam is its public DiagnosticKernel Interface:

~~~python
kernel = DiagnosticKernel.start(
    incident_case_id=case_id,
    run_id=run_id,
    allowed_root_cause_codes=(
        "SOURCE_SCHEMA_COLUMN_RENAMED",
        "SOURCE_SCHEMA_COLUMN_TYPE_CHANGED",
    ),
    model_request_limit=8,
    tool_call_limit=8,
)

prepared = kernel.prepare_tool(
    intent=intent,
    tool_name="get_relation_schema",
    arguments={"relation_name": "raw_payments"},
)
records = kernel.record_tool_result(prepared, returned_records)
outcome = kernel.finalize(decision)
snapshot = kernel.snapshot(model_requests_used=usage.requests)
~~~

The caller does not edit hypotheses, gaps, evidence inventory, fingerprints, or claim bindings directly. DiagnosticRunner owns model execution and calls EvidenceTools; DiagnosticKernel owns all state transitions and final evidence sufficiency; DeterministicEvaluator independently scores the frozen result. The Module is in-process and pure apart from its own in-memory state, so no new port or adapter abstraction is introduced.

## File structure

| Path | Responsibility |
|---|---|
| docs/requirements.md | Freeze the approved M6/P1 contract and acceptance boundary. |
| docs/superpowers/plans/2026-08-29-m6-diagnostic-kernel-v1.md | This executable implementation plan. |
| config/incidents/schema_type_change_payment_amount.json | Committed Ground Truth for the second incident. |
| src/data_incident_gym/incidents.py | Two-case registry and strict typed mutation contracts. |
| src/data_incident_gym/lab.py | Allowlisted rename/type-change injection and inverse recovery. |
| src/data_incident_gym/lab_verifier.py | Case-neutral persisted-run verification. |
| src/data_incident_gym/diagnostic_kernel.py | Deep Module: hypotheses, gaps, provenance, budgets, claim matrix, final gates. |
| src/data_incident_gym/diagnosis.py | Frozen DiagnosisRunResult plus typed terminal Kernel trace snapshot. |
| src/data_incident_gym/diagnostic_agent.py | Thin PydanticAI orchestration adapter around DiagnosticKernel and EvidenceTools. |
| src/data_incident_gym/evaluation.py | Independent M6 structural and exact Ground Truth checks. |
| src/data_incident_gym/artifacts.py | Versioned M6 schemas while preserving the exact six files. |
| src/data_incident_gym/templates/report.md.j2 | Render hypotheses, gaps, claim matrix, budgets, and final gate. |
| src/data_incident_gym/cli.py | Provider-neutral two-case help and unchanged fixed-case argument surface. |
| tests/unit/test_incidents.py | Registry, historic digest, type-change fixture, strict parsing. |
| tests/unit/test_lab.py | Allowlisted SQL, dispatch, state classification, inverse mutation. |
| tests/unit/test_diagnostic_kernel.py | Tests only through the Kernel Interface. |
| tests/unit/test_diagnostic_agent.py | Model-adapter, budget, safety, and Kernel-integration tests. |
| tests/unit/test_diagnosis.py | InvestigationState and trace/run-result contracts. |
| tests/unit/test_evaluation.py | Independent Kernel-state and claim-matrix rejection tests. |
| tests/unit/test_artifacts.py | Six-file M6 round-trip and report tests. |
| tests/unit/test_cli.py | Two supported case IDs and provider-neutral messaging. |
| tests/integration/test_incident_lab.py | Real PostgreSQL/dbt inject/build/reset for both cases. |
| tests/integration/test_diagnostic_agent.py | Real M3 evidence plus FunctionModel Kernel flow for both cases. |
| tests/integration/test_evaluation_runner.py | Full two-case FunctionModel/evaluator/artifact/recovery loop. |
| tests/e2e/test_incident_reproducibility.py | Ten deterministic inject/build/reset cycles per case. |
| tests/e2e/test_real_model_diagnosis.py | Provider-neutral replacement for test_ollama_diagnosis.py. |
| tests/e2e/test_real_model_evaluation.py | Exactly three real samples per case and 2/3 gate. |

### Task 1: Create the isolated M6 baseline and freeze the requirements delta

**Files:**
- Modify: docs/requirements.md
- Add: docs/superpowers/plans/2026-08-29-m6-diagnostic-kernel-v1.md
- Verify without modification: AGENT.md
- Verify without modification: README.md
- Verify without modification: mistake.md
- Verify without modification: third_party/jaffle_shop

- [ ] **Step 1: Create the isolated execution worktree**

Use the using-git-worktrees skill. Select exact starting commit c4c4cb820aa805976e77e6602e6b3f82b0ff5dfc and create branch codex/m6-diagnostic-kernel-v1-20260829. Do not reuse codex/m6-20260829.

The selected skill must create:

~~~text
C:\Users\29913\.config\superpowers\worktrees\DataIncidentGym\m6-diagnostic-kernel-v1-20260829
~~~

After creation, copy and verify only the plan:

~~~powershell
$sourcePlan = 'C:\Users\29913\.config\superpowers\worktrees\DataIncidentGym\m5-reimplementation-20260828\docs\superpowers\plans\2026-08-29-m6-diagnostic-kernel-v1.md'
$m6Root = 'C:\Users\29913\.config\superpowers\worktrees\DataIncidentGym\m6-diagnostic-kernel-v1-20260829'
$destinationPlan = Join-Path $m6Root 'docs\superpowers\plans\2026-08-29-m6-diagnostic-kernel-v1.md'
Copy-Item -LiteralPath $sourcePlan -Destination $destinationPlan
$sourceHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $sourcePlan).Hash
$destinationHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $destinationPlan).Hash
if ($sourceHash -ne $destinationHash) { throw 'M6 plan copy mismatch' }
Set-Location -LiteralPath $m6Root
~~~

Then run:

~~~powershell
git rev-parse HEAD
git status --short --branch
git -C third_party/jaffle_shop rev-parse HEAD
git -C third_party/jaffle_shop status --short
~~~

Expected: HEAD is c4c4cb820aa805976e77e6602e6b3f82b0ff5dfc; the new branch name is exact; submodule HEAD is 36bde6cba69d962b83be1d52fc65a0dce1cb4ebb; submodule status has no output.

- [ ] **Step 2: Write the failing requirements-presence check**

Run:

~~~powershell
rg -n 'M6：Diagnostic Kernel v1|schema_type_change_payment_amount|SOURCE_SCHEMA_COLUMN_TYPE_CHANGED|至少两个候选假设|每个案例精确三次' docs/requirements.md
~~~

Expected: no M6 contract matches are present.

- [ ] **Step 3: Add the approved M6 section under P1**

Insert this exact section immediately after the P1 incident list:

~~~markdown
#### M6：Diagnostic Kernel v1 与字段类型变化纵切

M6 只交付第一个 P1 纵切：

- 保留 schema_rename_payment_amount，并新增 schema_type_change_payment_amount；
- 新案例把 raw_payments.amount 从 integer 改为 text，root_cause_code 为 SOURCE_SCHEMA_COLUMN_TYPE_CHANGED；
- Diagnosis Agent 显式维护候选假设、EvidenceGap、claim-evidence bindings 和剩余预算；
- CONFIRMED 前至少有两个候选假设、一个受支持的选中假设和一个被证据反驳的替代假设；
- 模型负责 root cause、affected assets 与 evidence IDs；Diagnostic Kernel 只验证、拒绝和投影模型声明，不替模型生成答案；
- evaluator 继续独立读取 Ground Truth，并对冻结的 InvestigationState、Diagnosis、EvidenceRecord 与 trace 做确定性评分；
- 六文件产物合同不变，最终 InvestigationState 作为 trace.jsonl 的类型化终态事件保存并进入 report.md；
- 两个案例分别执行精确三个真实模型样本，每个案例至少两个 PASSED；失败全部保留并进入分母。

M6 不实现静态 Skill baseline、消融、Accuracy/F1、跨变体结论、用户自定义故障 DSL、自由 SQL、写工具或自动修复。
~~~

- [ ] **Step 4: Verify the requirements delta and planning-only scope**

Run:

~~~powershell
rg -n 'M6：Diagnostic Kernel v1|schema_type_change_payment_amount|SOURCE_SCHEMA_COLUMN_TYPE_CHANGED|至少有两个候选假设|每个案例至少两个 PASSED' docs/requirements.md
git diff -- docs/requirements.md docs/superpowers/plans/2026-08-29-m6-diagnostic-kernel-v1.md
git diff --cached --name-only
~~~

Expected: each required statement appears once; only the requirements file and this plan are part of the tracked M6 planning delta; index is empty.

- [ ] **Step 5: Commit the planning contract**

~~~powershell
git add docs/requirements.md docs/superpowers/plans/2026-08-29-m6-diagnostic-kernel-v1.md
git diff --cached --name-only
git commit -m "docs: freeze m6 diagnostic kernel contract"
~~~

Expected: cached names are exactly the two docs paths; root Markdown, runtime output, source, tests, lockfile, CI, and submodule entry are absent.

### Task 2: Generalize the incident registry and add strict type-change Ground Truth

**Files:**
- Add: config/incidents/schema_type_change_payment_amount.json
- Modify: src/data_incident_gym/incidents.py
- Modify: tests/unit/test_incidents.py

- [ ] **Step 1: Write failing two-case registry tests**

Add these tests:

~~~python
from data_incident_gym.incidents import (
    CASE_ID,
    SUPPORTED_CASE_IDS,
    TYPE_CHANGE_CASE_ID,
    ColumnRenameInjection,
    ColumnTypeChangeInjection,
    load_ground_truth,
)


def test_supported_case_registry_preserves_m5_digest(project_root: Path) -> None:
    assert SUPPORTED_CASE_IDS == (
        "schema_rename_payment_amount",
        "schema_type_change_payment_amount",
    )
    rename = load_ground_truth(CASE_ID, project_root)
    assert isinstance(rename.injection, ColumnRenameInjection)
    assert rename.digest() == (
        "c2fa0d97b603c37a21d07123481b2a9d09a34dbd3aab38e6e102a55d59ce4491"
    )


def test_type_change_ground_truth_is_strict_and_canonical(project_root: Path) -> None:
    truth = load_ground_truth(TYPE_CHANGE_CASE_ID, project_root)
    assert truth.incident_case_id == TYPE_CHANGE_CASE_ID
    assert truth.root_cause_code == "SOURCE_SCHEMA_COLUMN_TYPE_CHANGED"
    assert truth.direct_failure == "model.jaffle_shop.stg_payments"
    assert truth.affected_assets == (
        "model.jaffle_shop.stg_payments",
        "model.jaffle_shop.orders",
        "model.jaffle_shop.customers",
    )
    assert isinstance(truth.injection, ColumnTypeChangeInjection)
    assert truth.injection.model_dump() == {
        "relation": "raw_payments",
        "column": "amount",
        "from_type": "integer",
        "to_type": "text",
    }
    assert tuple(
        (column.name, column.data_type)
        for column in truth.expected_schema.fault_column_metadata
    ) == (
        ("id", "integer"),
        ("order_id", "integer"),
        ("payment_method", "text"),
        ("amount", "text"),
    )
    assert len(truth.digest()) == 64


def test_loader_rejects_unregistered_well_formed_case(tmp_path: Path) -> None:
    target = tmp_path / "config/incidents/unregistered_case.json"
    target.parent.mkdir(parents=True)
    target.write_text("{}", encoding="utf-8")
    with pytest.raises(IncidentCaseError, match="未知故障案例"):
        load_ground_truth("unregistered_case", tmp_path)
~~~

- [ ] **Step 2: Run the focused test and verify RED**

~~~powershell
uv run pytest tests/unit/test_incidents.py -q
~~~

Expected: collection fails because TYPE_CHANGE_CASE_ID, SUPPORTED_CASE_IDS, and the new injection class do not exist.

- [ ] **Step 3: Add the exact second Ground Truth file**

Create config/incidents/schema_type_change_payment_amount.json:

~~~json
{
  "affected_assets": [
    "model.jaffle_shop.stg_payments",
    "model.jaffle_shop.orders",
    "model.jaffle_shop.customers"
  ],
  "direct_failure": "model.jaffle_shop.stg_payments",
  "expected_failure_category": "DBT_MODEL_ERROR",
  "expected_schema": {
    "fault_columns": [
      "id",
      "order_id",
      "payment_method",
      "amount"
    ],
    "fault_column_metadata": [
      {
        "data_type": "integer",
        "name": "id",
        "nullable": true,
        "ordinal_position": 1
      },
      {
        "data_type": "integer",
        "name": "order_id",
        "nullable": true,
        "ordinal_position": 2
      },
      {
        "data_type": "text",
        "name": "payment_method",
        "nullable": true,
        "ordinal_position": 3
      },
      {
        "data_type": "text",
        "name": "amount",
        "nullable": true,
        "ordinal_position": 4
      }
    ],
    "healthy_columns": [
      "id",
      "order_id",
      "payment_method",
      "amount"
    ],
    "healthy_column_metadata": [
      {
        "data_type": "integer",
        "name": "id",
        "nullable": true,
        "ordinal_position": 1
      },
      {
        "data_type": "integer",
        "name": "order_id",
        "nullable": true,
        "ordinal_position": 2
      },
      {
        "data_type": "text",
        "name": "payment_method",
        "nullable": true,
        "ordinal_position": 3
      },
      {
        "data_type": "integer",
        "name": "amount",
        "nullable": true,
        "ordinal_position": 4
      }
    ],
    "relation": "raw_payments",
    "row_count": 113
  },
  "incident_case_id": "schema_type_change_payment_amount",
  "injection": {
    "column": "amount",
    "from_type": "integer",
    "relation": "raw_payments",
    "to_type": "text"
  },
  "required_evidence_types": [
    "DBT_NODE_ERROR",
    "RELATION_SCHEMA",
    "DBT_LINEAGE"
  ],
  "root_cause_code": "SOURCE_SCHEMA_COLUMN_TYPE_CHANGED",
  "schema_version": "ground_truth.v1"
}
~~~

- [ ] **Step 4: Replace the single-case literals with a strict two-case contract**

Keep the existing JSON unchanged. In incidents.py introduce:

~~~python
CASE_ID = "schema_rename_payment_amount"
TYPE_CHANGE_CASE_ID = "schema_type_change_payment_amount"
SUPPORTED_CASE_IDS = (CASE_ID, TYPE_CHANGE_CASE_ID)


class ColumnRenameInjection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    relation: Literal["raw_payments"]
    from_column: Literal["amount"]
    to_column: Literal["total_amount"]


class ColumnTypeChangeInjection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    relation: Literal["raw_payments"]
    column: Literal["amount"]
    from_type: Literal["integer"]
    to_type: Literal["text"]


InjectionSpec = ColumnRenameInjection | ColumnTypeChangeInjection
RootCauseCode = Literal[
    "SOURCE_SCHEMA_COLUMN_RENAMED",
    "SOURCE_SCHEMA_COLUMN_TYPE_CHANGED",
]
~~~

Change GroundTruth fields to:

~~~python
class GroundTruth(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["ground_truth.v1"]
    incident_case_id: Literal[
        "schema_rename_payment_amount",
        "schema_type_change_payment_amount",
    ]
    root_cause_code: RootCauseCode
    injection: InjectionSpec
    direct_failure: Literal["model.jaffle_shop.stg_payments"]
    affected_assets: tuple[StrictStr, ...]
    required_evidence_types: tuple[StrictStr, ...]
    expected_failure_category: Literal["DBT_MODEL_ERROR"]
    expected_schema: ExpectedSchema
~~~

The model validator must preserve all existing exact schema checks and select the expected fault contract by incident_case_id:

~~~python
        expected_assets = (
            "model.jaffle_shop.stg_payments",
            "model.jaffle_shop.orders",
            "model.jaffle_shop.customers",
        )
        expected_evidence = (
            "DBT_NODE_ERROR",
            "RELATION_SCHEMA",
            "DBT_LINEAGE",
        )
        if self.affected_assets != expected_assets:
            raise ValueError("affected_assets 不匹配支持案例")
        if self.required_evidence_types != expected_evidence:
            raise ValueError("required_evidence_types 不匹配支持案例")

        if self.incident_case_id == CASE_ID:
            if (
                self.root_cause_code != "SOURCE_SCHEMA_COLUMN_RENAMED"
                or not isinstance(self.injection, ColumnRenameInjection)
            ):
                raise ValueError("schema rename 合同不匹配")
            expected_fault_metadata = (
                *expected_healthy_metadata[:3],
                ExpectedColumn(
                    name="total_amount",
                    data_type="integer",
                    nullable=True,
                    ordinal_position=4,
                ),
            )
        else:
            if (
                self.root_cause_code != "SOURCE_SCHEMA_COLUMN_TYPE_CHANGED"
                or not isinstance(self.injection, ColumnTypeChangeInjection)
            ):
                raise ValueError("schema type change 合同不匹配")
            expected_fault_metadata = (
                *expected_healthy_metadata[:3],
                ExpectedColumn(
                    name="amount",
                    data_type="text",
                    nullable=True,
                    ordinal_position=4,
                ),
            )
~~~

Set fault_columns from expected_fault_metadata and reject duplicates in assets, evidence types, and both column lists. Change load_ground_truth to accept only membership in SUPPORTED_CASE_IDS and build the path from the already-validated case ID.

- [ ] **Step 5: Run focused unit tests and digest verification**

~~~powershell
uv run pytest tests/unit/test_incidents.py -q
uv run python -c "from data_incident_gym.incidents import CASE_ID, load_ground_truth; assert load_ground_truth(CASE_ID).digest() == 'c2fa0d97b603c37a21d07123481b2a9d09a34dbd3aab38e6e102a55d59ce4491'"
git diff --exit-code c4c4cb820aa805976e77e6602e6b3f82b0ff5dfc -- config/incidents/schema_rename_payment_amount.json
$renameBlob = git hash-object --path=config/incidents/schema_rename_payment_amount.json config/incidents/schema_rename_payment_amount.json
if ($renameBlob -ne 'eb2cca9026c778f25119660affa206ce1377f46d') { throw 'historic Ground Truth Git blob changed' }
uv run ruff check src/data_incident_gym/incidents.py tests/unit/test_incidents.py
~~~

Expected: all tests pass; both the canonical semantic digest and exact baseline Git blob assertions pass; Ruff exits 0.

- [ ] **Step 6: Commit the incident contracts**

~~~powershell
git add config/incidents/schema_type_change_payment_amount.json src/data_incident_gym/incidents.py tests/unit/test_incidents.py
git diff --cached --name-only
git commit -m "feat: add column type change incident contract"
~~~

Expected: exactly the three listed paths are committed.

### Task 3: Add allowlisted type mutation and inverse recovery

**Files:**
- Modify: src/data_incident_gym/lab.py
- Modify: tests/unit/test_lab.py

- [ ] **Step 1: Write failing SQL and dispatch tests**

Add a type-aware relation helper and tests:

~~~python
def _typed_relation(amount_type: str) -> RelationSummary:
    return RelationSummary(
        name="raw_payments",
        row_count=113,
        columns=(
            ColumnSummary("id", "integer", True, 1),
            ColumnSummary("order_id", "integer", True, 2),
            ColumnSummary("payment_method", "text", True, 3),
            ColumnSummary("amount", amount_type, True, 4),
        ),
    )


TYPE_HEALTHY = _typed_relation("integer")
TYPE_INJECTED = _typed_relation("text")


def test_type_change_uses_fixed_allowlisted_sql(tmp_path: Path) -> None:
    executed: list[object] = []
    connection = _recording_connection(executed)
    lab = IncidentLab(
        Settings(_env_file=None),
        tmp_path,
        baseline_builder=SimpleNamespace(),
        db_connect=lambda **_: connection,
    )

    lab._change_column_type("raw_payments", "amount", "integer", "text")

    assert len(executed) == 1
    assert isinstance(executed[0], sql.Composed)
    assert executed[0].as_string(None) == (
        'ALTER TABLE "analytics"."raw_payments" '
        'ALTER COLUMN "amount" TYPE text USING "amount"::text'
    )


def test_type_change_rejects_nonallowlisted_type(tmp_path: Path) -> None:
    lab = IncidentLab(
        Settings(_env_file=None),
        tmp_path,
        baseline_builder=SimpleNamespace(),
    )
    with pytest.raises(InvalidIncidentState, match="未授权"):
        lab._change_column_type("raw_payments", "amount", "integer", "jsonb")


def test_type_case_injects_and_reset_applies_inverse_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _prepare_ground_truth(tmp_path, TYPE_CHANGE_CASE_ID)
    lab, baseline = _lab(tmp_path, TYPE_CHANGE_CASE_ID)
    states = iter((TYPE_HEALTHY, TYPE_INJECTED))
    changes: list[tuple[str, str, str, str]] = []
    monkeypatch.setattr(lab, "_inspect_relation", lambda _: next(states))
    monkeypatch.setattr(
        lab,
        "_change_column_type",
        lambda relation, column, source, target: changes.append(
            (relation, column, source, target)
        ),
    )

    assert lab.inject(TYPE_CHANGE_CASE_ID).state == "INJECTED"
    assert changes == [("raw_payments", "amount", "integer", "text")]
    assert baseline.calls == ["start_postgres"]

    monkeypatch.setattr(lab, "_inspect_relation", lambda _: TYPE_INJECTED)
    changes.clear()
    assert lab.reset(TYPE_CHANGE_CASE_ID).state == "HEALTHY"
    assert changes == [("raw_payments", "amount", "text", "integer")]
~~~

Factor the existing local Cursor/Transaction/Connection fake into _recording_connection so both rename and type-change SQL tests use the same complete fake.

- [ ] **Step 2: Run focused tests and verify RED**

~~~powershell
uv run pytest tests/unit/test_lab.py -q
~~~

Expected: failures show missing TYPE_CHANGE_CASE_ID-aware helpers and missing _change_column_type.

- [ ] **Step 3: Implement the allowlisted SQL and typed dispatcher**

Add:

~~~python
_ALLOWED_TYPE_CHANGES = {
    ("raw_payments", "amount", "integer", "text"),
    ("raw_payments", "amount", "text", "integer"),
}
_TYPE_SQL = {
    "integer": sql.SQL("integer"),
    "text": sql.SQL("text"),
}


def _change_column_type(
    self,
    relation: str,
    column: str,
    source_type: str,
    target_type: str,
) -> None:
    key = (relation, column, source_type, target_type)
    if key not in _ALLOWED_TYPE_CHANGES:
        raise InvalidIncidentState("拒绝执行未授权的故障字段类型变化")
    statement = sql.SQL(
        "ALTER TABLE {}.{} ALTER COLUMN {} TYPE {} USING {}::{}"
    ).format(
        sql.Identifier(self.settings.postgres_schema),
        sql.Identifier(relation),
        sql.Identifier(column),
        _TYPE_SQL[target_type],
        sql.Identifier(column),
        _TYPE_SQL[target_type],
    )
    try:
        with (
            self.db_connect(**self._connection_kwargs()) as connection,
            connection.transaction(),
            connection.cursor() as cursor,
        ):
            cursor.execute(statement)
    except Exception as exc:
        raise self._clean(
            IncidentExecutionError(
                f"故障字段类型变化失败：{self._redact(str(exc))}"
            )
        )
~~~

Add one dispatcher:

~~~python
def _apply_mutation(self, truth: GroundTruth, *, inject: bool) -> None:
    mutation = truth.injection
    if isinstance(mutation, ColumnRenameInjection):
        source = mutation.from_column if inject else mutation.to_column
        target = mutation.to_column if inject else mutation.from_column
        self._rename_column(mutation.relation, source, target)
        return
    source_type = mutation.from_type if inject else mutation.to_type
    target_type = mutation.to_type if inject else mutation.from_type
    self._change_column_type(
        mutation.relation,
        mutation.column,
        source_type,
        target_type,
    )
~~~

Use _apply_mutation(truth, inject=True) in inject and _apply_mutation(truth, inject=False) in reset. Keep _classify_state metadata-based so both mutation types use the committed healthy/fault schemas.

- [ ] **Step 4: Run unit tests and safety scans**

~~~powershell
uv run pytest tests/unit/test_incidents.py tests/unit/test_lab.py -q
uv run ruff check src/data_incident_gym/incidents.py src/data_incident_gym/lab.py tests/unit/test_incidents.py tests/unit/test_lab.py
rg -n 'sql.SQL\(target_type\)|ALTER TABLE.*\{.*\}|jsonb|varchar' src/data_incident_gym/lab.py
~~~

Expected: tests and Ruff pass; the scan has no unsafe dynamic type construction and no unapproved type.

- [ ] **Step 5: Commit the mutation implementation**

~~~powershell
git add src/data_incident_gym/lab.py tests/unit/test_lab.py
git diff --cached --name-only
git commit -m "feat: inject reversible column type incidents"
~~~

Expected: exactly lab.py and test_lab.py are committed.

### Task 4: Prove the second lab case with real PostgreSQL and dbt

**Files:**
- Modify: src/data_incident_gym/lab_verifier.py
- Modify: tests/unit/test_lab_verifier.py
- Modify: tests/integration/test_incident_lab.py
- Modify: tests/e2e/test_incident_reproducibility.py

- [ ] **Step 1: Write failing case-parameterized verification tests**

Parameterize the existing integration test:

~~~python
@pytest.mark.integration
@pytest.mark.parametrize("case_id", SUPPORTED_CASE_IDS)
def test_incident_lab_captures_expected_failure_and_recovers(
    project_root: Path,
    case_id: str,
) -> None:
    lab = IncidentLab(Settings(_env_file=None), project_root)
    baseline = lab.reset(case_id)
    try:
        injected = lab.inject(case_id)
        run = lab.build(case_id)
        assert injected.state == "INJECTED"
        assert injected.fingerprint != baseline.fingerprint
        assert run.dbt_exit_code != 0
        assert run.verification.incident_case_id == case_id
        assert run.verification.failed_nodes == (
            "model.jaffle_shop.stg_payments",
        )
        assert run.verification.affected_assets == (
            "model.jaffle_shop.stg_payments",
            "model.jaffle_shop.orders",
            "model.jaffle_shop.customers",
        )
    finally:
        recovered = lab.reset(case_id)
    assert recovered.state == "HEALTHY"
    assert recovered.fingerprint == baseline.fingerprint
~~~

Parameterize the ten-run e2e test with ids=SUPPORTED_CASE_IDS and keep a separate stable projection per case:

~~~python
@pytest.mark.e2e
@pytest.mark.parametrize("case_id", SUPPORTED_CASE_IDS, ids=SUPPORTED_CASE_IDS)
def test_incident_is_reproducible_across_ten_runs(
    project_root: Path,
    case_id: str,
) -> None:
    lab = IncidentLab(Settings(_env_file=None), project_root)
    initial = lab.reset(case_id)
    stable_results: list[str] = []
    run_dirs: list[Path] = []
    try:
        for run_number in range(1, 11):
            reset = lab.reset(case_id)
            injection = lab.inject(case_id)
            run = lab.build(case_id)
            projection = {
                "case_id": case_id,
                "failed_nodes": run.verification.failed_nodes,
                "affected_assets": run.verification.affected_assets,
                "error_category": run.verification.error_category,
                "schema_fingerprint": run.verification.schema_fingerprint,
                "ground_truth_digest": run.verification.ground_truth_digest,
            }
            stable_results.append(json.dumps(projection, sort_keys=True))
            run_dirs.append(run.artifact_dir)
            assert reset.fingerprint == initial.fingerprint
            assert injection.fingerprint != initial.fingerprint
    finally:
        recovered = lab.reset(case_id)
    assert len(stable_results) == 10
    assert len(set(stable_results)) == 1
    assert len({path.name for path in run_dirs}) == 10
    assert recovered.fingerprint == initial.fingerprint
~~~

- [ ] **Step 2: Run the new integration parameter and verify RED**

~~~powershell
uv run pytest tests/integration/test_incident_lab.py -q
~~~

Expected: the existing rename parameter passes; the type-change parameter fails at the first verifier assumption that remains case-specific.

- [ ] **Step 3: Make IncidentVerifier case-neutral without weakening checks**

Keep the committed-truth digest, exact direct failure, exact affected set, exact fault schema metadata, nonzero dbt exit, log presence, duplicate-key rejection, and fingerprint recomputation. Replace any remaining CASE_ID or rename-specific assertion with the already-loaded committed_truth fields. Do not infer a case from filenames or error text.

Add a unit test that passes each committed Ground Truth through the same verification projection:

~~~python
@pytest.mark.parametrize("case_id", SUPPORTED_CASE_IDS)
def test_verifier_contract_is_selected_by_committed_case(
    project_root: Path,
    case_id: str,
) -> None:
    truth = load_ground_truth(case_id, project_root)
    assert truth.expected_schema.relation == truth.injection.relation
    assert truth.direct_failure in truth.affected_assets
    assert truth.required_evidence_types == (
        "DBT_NODE_ERROR",
        "RELATION_SCHEMA",
        "DBT_LINEAGE",
    )
~~~

- [ ] **Step 4: Run integration and the two ten-run reproductions**

~~~powershell
uv run pytest tests/unit/test_lab_verifier.py tests/integration/test_incident_lab.py -q
uv run pytest tests/e2e/test_incident_reproducibility.py -q -s
~~~

Expected: two integration parameters pass; each e2e parameter produces ten unique run IDs with one stable projection; both finally blocks restore the identical healthy fingerprint.

- [ ] **Step 5: Verify the submodule and Git scope**

~~~powershell
git -C third_party/jaffle_shop status --short
git diff --check
git status --short
~~~

Expected: submodule status is empty; only the four Task 4 paths and ignored project-root runtime outputs changed.

- [ ] **Step 6: Commit the second-case lab proof**

~~~powershell
git add src/data_incident_gym/lab_verifier.py tests/unit/test_lab_verifier.py tests/integration/test_incident_lab.py tests/e2e/test_incident_reproducibility.py
git diff --cached --name-only
git commit -m "test: prove column type incident reproducibility"
~~~

Expected: exactly the four listed paths are committed.

### Task 5: Define the Diagnostic Kernel Interface and frozen state models

**Files:**
- Add: src/data_incident_gym/diagnostic_kernel.py
- Add: tests/unit/test_diagnostic_kernel.py

- [ ] **Step 1: Write failing Interface tests**

Create tests/unit/test_diagnostic_kernel.py with strict construction tests:

~~~python
from __future__ import annotations

import pytest
from pydantic import ValidationError

from data_incident_gym.diagnostic_kernel import (
    ClaimEvidence,
    ClaimKind,
    DiagnosticKernel,
    EvidenceGapKind,
    Hypothesis,
    HypothesisAssessment,
    HypothesisVerdict,
    InvestigationIntent,
    KernelDecision,
    KernelFinalStatus,
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
~~~

- [ ] **Step 2: Run the focused test and verify RED**

~~~powershell
uv run pytest tests/unit/test_diagnostic_kernel.py -q
~~~

Expected: collection fails because data_incident_gym.diagnostic_kernel does not exist.

- [ ] **Step 3: Implement the public models and exact enums**

Create diagnostic_kernel.py with these public contracts:

~~~python
class EvidenceGapKind(StrEnum):
    LOCATE_FAILURE = "LOCATE_FAILURE"
    EXPLAIN_FAILURE = "EXPLAIN_FAILURE"
    DISCOVER_SOURCE_RELATION = "DISCOVER_SOURCE_RELATION"
    DISCRIMINATE_SCHEMA = "DISCRIMINATE_SCHEMA"
    MAP_IMPACT = "MAP_IMPACT"


class EvidenceGapStatus(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    BLOCKED = "BLOCKED"


class HypothesisVerdict(StrEnum):
    SUPPORTED = "SUPPORTED"
    REFUTED = "REFUTED"


class ClaimKind(StrEnum):
    ROOT_CAUSE = "ROOT_CAUSE"
    AFFECTED_ASSET = "AFFECTED_ASSET"


class KernelFinalStatus(StrEnum):
    CONFIRMED = "CONFIRMED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    MODEL_ERROR = "MODEL_ERROR"


class Hypothesis(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    hypothesis_id: StrictStr = Field(pattern=r"^h_[a-z0-9_]{1,32}$")
    root_cause_code: StrictStr = Field(
        pattern=r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*$"
    )


class HypothesisAssessment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    hypothesis_id: StrictStr = Field(pattern=r"^h_[a-z0-9_]{1,32}$")
    verdict: HypothesisVerdict
    evidence_ids: tuple[
        Annotated[StrictStr, Field(pattern=r"^ev_[0-9a-f]{64}$")],
        ...,
    ]


class InvestigationIntent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    gap_id: StrictStr = Field(pattern=r"^g_[a-z0-9_]{1,32}$")
    gap_kind: EvidenceGapKind
    hypothesis_ids: tuple[
        Annotated[StrictStr, Field(pattern=r"^h_[a-z0-9_]{1,32}$")],
        ...,
    ] = ()
    new_hypotheses: tuple[Hypothesis, ...] = ()


class EvidenceGap(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    gap_id: StrictStr = Field(pattern=r"^g_[a-z0-9_]{1,32}$")
    gap_kind: EvidenceGapKind
    hypothesis_ids: tuple[
        Annotated[StrictStr, Field(pattern=r"^h_[a-z0-9_]{1,32}$")],
        ...,
    ]
    tool_name: StrictStr
    status: EvidenceGapStatus
    evidence_ids: tuple[
        Annotated[StrictStr, Field(pattern=r"^ev_[0-9a-f]{64}$")],
        ...,
    ] = ()
    error_code: StrictStr | None = None


class ClaimEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: ClaimKind
    value: StrictStr
    evidence_ids: tuple[
        Annotated[StrictStr, Field(pattern=r"^ev_[0-9a-f]{64}$")],
        ...,
    ]


class KernelDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["CONFIRMED", "INSUFFICIENT_EVIDENCE"]
    incident_case_id: StrictStr = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    run_id: StrictStr = Field(pattern=r"^[0-9a-f]{32}$")
    selected_hypothesis_id: StrictStr | None
    assessments: tuple[HypothesisAssessment, ...]
    claims: tuple[ClaimEvidence, ...]
    summary: StrictStr
    recommended_actions: tuple[StrictStr, ...]
    confidence: Annotated[StrictFloat, Field(ge=0.0, le=1.0)]
~~~

Define frozen PreparedToolCall, InvestigationState, and KernelOutcome:

~~~python
class PreparedToolCall(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    gap_id: StrictStr
    tool_name: StrictStr
    arguments: dict[StrictStr, StrictStr]
    fingerprint: Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]


class InvestigationState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["m6.investigation.v1"]
    incident_case_id: StrictStr
    run_id: StrictStr = Field(pattern=r"^[0-9a-f]{32}$")
    revision: Annotated[StrictInt, Field(ge=0)]
    allowed_root_cause_codes: tuple[StrictStr, ...]
    hypotheses: tuple[Hypothesis, ...]
    gaps: tuple[EvidenceGap, ...]
    assessments: tuple[HypothesisAssessment, ...]
    claims: tuple[ClaimEvidence, ...]
    evidence_inventory: tuple[StrictStr, ...]
    tool_fingerprints: tuple[StrictStr, ...]
    model_request_limit: Annotated[StrictInt, Field(gt=0)]
    model_requests_used: Annotated[StrictInt, Field(ge=0)]
    model_requests_remaining: Annotated[StrictInt, Field(ge=0)]
    tool_call_limit: Annotated[StrictInt, Field(gt=0)]
    tool_calls_used: Annotated[StrictInt, Field(ge=0)]
    tool_calls_remaining: Annotated[StrictInt, Field(ge=0)]
    final_status: KernelFinalStatus | None
    gate_reason: StrictStr | None


class KernelOutcome(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: KernelFinalStatus
    root_cause_code: StrictStr | None
    affected_assets: tuple[StrictStr, ...]
    evidence_ids: tuple[StrictStr, ...]
    summary: StrictStr
    recommended_actions: tuple[StrictStr, ...]
    confidence: StrictFloat
~~~

Model validators must reject duplicate ontology members, hypothesis IDs, gap IDs, assessment hypothesis IDs, claim kind/value pairs, claim evidence IDs, and evidence inventory IDs. tool_fingerprints is the deliberate exception: it is an ordered attempt ledger, so a rejected duplicate call appears a second time and consumes budget. KernelDecision must require empty selected_hypothesis_id and claims for INSUFFICIENT_EVIDENCE, and nonempty selected_hypothesis_id, assessments, claims, and actions for CONFIRMED.

- [ ] **Step 4: Add the stateful Module constructor and read-only projections**

Implement:

~~~python
class DiagnosticKernel:
    def __init__(
        self,
        *,
        incident_case_id: str,
        run_id: str,
        allowed_root_cause_codes: tuple[str, ...],
        model_request_limit: int,
        tool_call_limit: int,
    ) -> None:
        self._incident_case_id = incident_case_id
        self._run_id = run_id
        self._allowed_root_cause_codes = allowed_root_cause_codes
        self._model_request_limit = model_request_limit
        self._tool_call_limit = tool_call_limit
        self._revision = 0
        self._hypotheses: list[Hypothesis] = []
        self._gaps: list[EvidenceGap] = []
        self._assessments: tuple[HypothesisAssessment, ...] = ()
        self._claims: tuple[ClaimEvidence, ...] = ()
        self._records: list[EvidenceRecord] = []
        self._fingerprints: list[str] = []
        self._prepared_fingerprints: set[str] = set()
        self._final_status: KernelFinalStatus | None = None
        self._gate_reason: str | None = None

    @classmethod
    def start(
        cls,
        *,
        incident_case_id: str,
        run_id: str,
        allowed_root_cause_codes: tuple[str, ...],
        model_request_limit: int,
        tool_call_limit: int,
    ) -> DiagnosticKernel:
        if len(allowed_root_cause_codes) < 2:
            raise ValueError("Diagnostic Kernel requires at least two ontology members")
        if len(allowed_root_cause_codes) != len(set(allowed_root_cause_codes)):
            raise ValueError("ontology members must be unique")
        return cls(
            incident_case_id=incident_case_id,
            run_id=run_id,
            allowed_root_cause_codes=allowed_root_cause_codes,
            model_request_limit=model_request_limit,
            tool_call_limit=tool_call_limit,
        )

    @property
    def evidence_records(self) -> tuple[EvidenceRecord, ...]:
        return tuple(self._records)

    def snapshot(self, *, model_requests_used: int) -> InvestigationState:
        if model_requests_used < 0 or model_requests_used > self._model_request_limit:
            raise ValueError("model request usage exceeds Kernel budget")
        return InvestigationState(
            schema_version="m6.investigation.v1",
            incident_case_id=self._incident_case_id,
            run_id=self._run_id,
            revision=self._revision,
            allowed_root_cause_codes=self._allowed_root_cause_codes,
            hypotheses=tuple(self._hypotheses),
            gaps=tuple(self._gaps),
            assessments=self._assessments,
            claims=self._claims,
            evidence_inventory=tuple(record.evidence_id for record in self._records),
            tool_fingerprints=tuple(self._fingerprints),
            model_request_limit=self._model_request_limit,
            model_requests_used=model_requests_used,
            model_requests_remaining=self._model_request_limit - model_requests_used,
            tool_call_limit=self._tool_call_limit,
            tool_calls_used=len(self._fingerprints),
            tool_calls_remaining=self._tool_call_limit - len(self._fingerprints),
            final_status=self._final_status,
            gate_reason=self._gate_reason,
        )
~~~

- [ ] **Step 5: Run model-contract tests and Ruff**

~~~powershell
uv run pytest tests/unit/test_diagnostic_kernel.py -q
uv run ruff check src/data_incident_gym/diagnostic_kernel.py tests/unit/test_diagnostic_kernel.py
~~~

Expected: tests and Ruff pass; no database, filesystem, model, or tool adapter is used.

- [ ] **Step 6: Commit the Kernel Interface**

~~~powershell
git add src/data_incident_gym/diagnostic_kernel.py tests/unit/test_diagnostic_kernel.py
git diff --cached --name-only
git commit -m "feat: define diagnostic kernel state interface"
~~~

Expected: exactly the Kernel Module and its Interface test are committed.

### Task 6: Implement evidence-gap, provenance, duplicate, and budget transitions

**Files:**
- Modify: src/data_incident_gym/diagnostic_kernel.py
- Modify: tests/unit/test_diagnostic_kernel.py

- [ ] **Step 1: Write failing transition tests**

Use synthetic EvidenceRecord.create fixtures and test only through DiagnosticKernel:

~~~python
def test_gap_transition_records_hypotheses_and_current_run_evidence() -> None:
    kernel = _kernel()
    run_results = _run_results_record()
    prepared = kernel.prepare_tool(
        intent=InvestigationIntent(
            gap_id="g_failure",
            gap_kind=EvidenceGapKind.LOCATE_FAILURE,
            hypothesis_ids=(),
            new_hypotheses=(),
        ),
        tool_name="get_dbt_run_results",
        arguments={"run_id": RUN_ID},
    )
    accepted = kernel.record_tool_result(prepared, (run_results,))
    state = kernel.snapshot(model_requests_used=1)
    assert accepted == (run_results,)
    assert state.gaps[0].status == EvidenceGapStatus.CLOSED
    assert state.gaps[0].evidence_ids == (run_results.evidence_id,)
    assert state.evidence_inventory == (run_results.evidence_id,)
    assert state.tool_calls_used == 1
    assert state.tool_calls_remaining == 7


def test_schema_gap_can_register_two_competing_hypotheses() -> None:
    kernel = _kernel_with_failure_error_and_upstream_lineage()
    intent = InvestigationIntent(
        gap_id="g_schema",
        gap_kind=EvidenceGapKind.DISCRIMINATE_SCHEMA,
        hypothesis_ids=("h_rename", "h_type"),
        new_hypotheses=(
            Hypothesis(
                hypothesis_id="h_rename",
                root_cause_code="SOURCE_SCHEMA_COLUMN_RENAMED",
            ),
            Hypothesis(
                hypothesis_id="h_type",
                root_cause_code="SOURCE_SCHEMA_COLUMN_TYPE_CHANGED",
            ),
        ),
    )
    prepared = kernel.prepare_tool(
        intent=intent,
        tool_name="get_relation_schema",
        arguments={"relation_name": "raw_payments"},
    )
    kernel.record_tool_result(prepared, (_schema_record("text"),))
    state = kernel.snapshot(model_requests_used=4)
    assert tuple(item.hypothesis_id for item in state.hypotheses) == (
        "h_rename",
        "h_type",
    )
    assert state.gaps[-1].hypothesis_ids == ("h_rename", "h_type")


@pytest.mark.parametrize(
    ("tool_name", "gap_kind"),
    [
        ("get_dbt_node_error", EvidenceGapKind.LOCATE_FAILURE),
        ("get_relation_schema", EvidenceGapKind.MAP_IMPACT),
        ("get_dbt_lineage", EvidenceGapKind.DISCRIMINATE_SCHEMA),
    ],
)
def test_gap_kind_must_match_tool(tool_name: str, gap_kind: EvidenceGapKind) -> None:
    kernel = _kernel()
    with pytest.raises(KernelError, match="GAP_TOOL_MISMATCH"):
        kernel.prepare_tool(
            intent=InvestigationIntent(
                gap_id="g_wrong",
                gap_kind=gap_kind,
                hypothesis_ids=(),
                new_hypotheses=(),
            ),
            tool_name=tool_name,
            arguments={"run_id": RUN_ID},
        )
~~~

Add separate tests for:

- node-error node_id must be present in prior DbtRunResultsFact.failed_nodes;
- lineage node_id must be present in prior run-results or lineage evidence;
- relation_name must be present in prior upstream lineage as a seed/source name;
- exact duplicate fingerprint is rejected;
- each admitted invalid/duplicate attempt consumes budget, and the ninth wrapper attempt is rejected before EvidenceTools execution;
- hypotheses cannot be registered before DBT_NODE_ERROR evidence exists;
- ontology-unknown root code is rejected;
- cross-run or conflicting EvidenceRecord is rejected;
- record_tool_failure marks the gap BLOCKED with a fixed safe error code and no evidence IDs.

- [ ] **Step 2: Run transition tests and verify RED**

~~~powershell
uv run pytest tests/unit/test_diagnostic_kernel.py -q
~~~

Expected: failures point to missing prepare_tool, record_tool_result, record_tool_failure, KernelError, and provenance checks.

- [ ] **Step 3: Implement the canonical gap-to-tool map and fingerprint**

Add:

~~~python
_GAP_TOOL = {
    EvidenceGapKind.LOCATE_FAILURE: ("get_dbt_run_results", None),
    EvidenceGapKind.EXPLAIN_FAILURE: ("get_dbt_node_error", None),
    EvidenceGapKind.DISCOVER_SOURCE_RELATION: ("get_dbt_lineage", "upstream"),
    EvidenceGapKind.DISCRIMINATE_SCHEMA: ("get_relation_schema", None),
    EvidenceGapKind.MAP_IMPACT: ("get_dbt_lineage", "downstream"),
}


def _fingerprint(run_id: str, tool_name: str, arguments: dict[str, str]) -> str:
    payload = {
        "arguments": arguments,
        "run_id": run_id,
        "tool_name": tool_name,
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class KernelError(RuntimeError):
    def __init__(self, code: str, *, fingerprint: str | None = None) -> None:
        self.code = code
        self.fingerprint = fingerprint
        super().__init__(code)
        self.__cause__ = None
        self.__context__ = None
~~~

- [ ] **Step 4: Implement prepare, success, and failure transitions**

prepare_tool must perform this exact order:

1. reject a finalized Kernel;
2. compute the fingerprint;
3. reject a ninth attempt with KernelError("TOOL_CALL_LIMIT", fingerprint=fingerprint) before changing state;
4. append the fingerprint to the ordered attempt ledger and increment revision;
5. if the fingerprint is already in the internal prepared-fingerprint set, raise KernelError("DUPLICATE_TOOL_CALL", fingerprint=fingerprint) without executing EvidenceTools;
6. validate the gap/tool/direction mapping;
7. validate new hypothesis IDs/codes and require prior node-error evidence;
8. validate intent hypothesis references;
9. validate tool argument provenance from the current evidence inventory;
10. on any rejected post-admission validation, raise KernelError with the admitted fingerprint so the adapter writes a safe failed ToolTraceEvent;
11. add the fingerprint to the prepared-fingerprint set, append the OPEN gap, and return PreparedToolCall.

Every wrapper invocation admitted below the limit consumes one tool-call attempt, including a duplicate, gap mismatch, or provenance rejection. An intent/provenance rejection does not enter the prepared-fingerprint set, so the model may correct only its intent and retry the same evidence query within the remaining budget. Only a call that returns PreparedToolCall may reach EvidenceTools. The ninth call does not change the state and cannot reach EvidenceTools.

Except for the attempt ledger/revision, prepare_tool is atomic: stage new hypotheses and the OPEN gap locally, validate the complete transition, and publish them only in step 11. A rejected transition must not partially register hypotheses or a gap.

record_tool_result must require the matching latest OPEN gap, reject empty/cross-run/conflicting records, add only new records, replace the gap with CLOSED and its evidence IDs, increment revision, and return only newly accepted records.

record_tool_failure must require the matching latest OPEN gap, replace it with BLOCKED, store only an allowlisted code from:

~~~python
_SAFE_TOOL_ERRORS = {
    "EVIDENCE_TOOL_ERROR",
    "INVALID_ARTIFACT",
    "NODE_ERROR_NOT_FOUND",
    "NODE_NOT_FOUND",
    "READ_ONLY_DATABASE_ERROR",
    "RELATION_NOT_ALLOWED",
    "RELATION_NOT_FOUND",
    "RUN_CONTEXT_MISMATCH",
    "RUN_NOT_FOUND",
    "RUN_STATE_DRIFT",
}
~~~

Unknown error strings become EVIDENCE_TOOL_ERROR. No exception text, SQL, path, credential, prompt, or model output enters InvestigationState.

Add an explicit duplicate-budget assertion:

~~~python
def test_duplicate_attempt_is_audited_and_consumes_budget() -> None:
    kernel = _kernel()
    intent = InvestigationIntent(
        gap_id="g_first",
        gap_kind=EvidenceGapKind.LOCATE_FAILURE,
    )
    first = kernel.prepare_tool(
        intent=intent,
        tool_name="get_dbt_run_results",
        arguments={"run_id": RUN_ID},
    )
    kernel.record_tool_result(first, (_run_results_record(),))

    with pytest.raises(KernelError, match="DUPLICATE_TOOL_CALL") as captured:
        kernel.prepare_tool(
            intent=intent.model_copy(update={"gap_id": "g_duplicate"}),
            tool_name="get_dbt_run_results",
            arguments={"run_id": RUN_ID},
        )

    state = kernel.snapshot(model_requests_used=2)
    assert captured.value.fingerprint == first.fingerprint
    assert state.tool_fingerprints == (first.fingerprint, first.fingerprint)
    assert state.tool_calls_used == 2
    assert state.tool_calls_remaining == 6
~~~

- [ ] **Step 5: Run the Kernel transition suite**

~~~powershell
uv run pytest tests/unit/test_diagnostic_kernel.py -q
uv run ruff check src/data_incident_gym/diagnostic_kernel.py tests/unit/test_diagnostic_kernel.py
~~~

Expected: all Interface tests pass; the Module has no import from incidents, GroundTruth, lab, evaluation, artifacts, diagnostic_agent, psycopg, pathlib, or PydanticAI.

- [ ] **Step 6: Commit the transition engine**

~~~powershell
git add src/data_incident_gym/diagnostic_kernel.py tests/unit/test_diagnostic_kernel.py
git diff --cached --name-only
git commit -m "feat: enforce diagnostic investigation transitions"
~~~

Expected: exactly the same two Kernel paths are committed.

### Task 7: Implement hypothesis adjudication and the claim-evidence sufficiency gate

**Files:**
- Modify: src/data_incident_gym/diagnostic_kernel.py
- Modify: tests/unit/test_diagnostic_kernel.py

- [ ] **Step 1: Write a passing rename finalization test**

~~~python
def test_confirmed_rename_requires_supported_selected_and_refuted_alternative() -> None:
    kernel, records = _complete_investigation(fault_column_name="total_amount")
    by_kind = {record.evidence_type.value: record for record in records}
    lineage = by_kind["DBT_LINEAGE"]
    decision = KernelDecision(
        status="CONFIRMED",
        incident_case_id=CASE_ID,
        run_id=RUN_ID,
        selected_hypothesis_id="h_rename",
        assessments=(
            HypothesisAssessment(
                hypothesis_id="h_rename",
                verdict=HypothesisVerdict.SUPPORTED,
                evidence_ids=(
                    by_kind["DBT_NODE_ERROR"].evidence_id,
                    by_kind["RELATION_SCHEMA"].evidence_id,
                ),
            ),
            HypothesisAssessment(
                hypothesis_id="h_type",
                verdict=HypothesisVerdict.REFUTED,
                evidence_ids=(by_kind["RELATION_SCHEMA"].evidence_id,),
            ),
        ),
        claims=(
            ClaimEvidence(
                kind=ClaimKind.ROOT_CAUSE,
                value="SOURCE_SCHEMA_COLUMN_RENAMED",
                evidence_ids=(
                    by_kind["DBT_NODE_ERROR"].evidence_id,
                    by_kind["RELATION_SCHEMA"].evidence_id,
                ),
            ),
            ClaimEvidence(
                kind=ClaimKind.AFFECTED_ASSET,
                value="model.jaffle_shop.stg_payments",
                evidence_ids=(by_kind["DBT_NODE_ERROR"].evidence_id,),
            ),
            ClaimEvidence(
                kind=ClaimKind.AFFECTED_ASSET,
                value="orders",
                evidence_ids=(lineage.evidence_id,),
            ),
            ClaimEvidence(
                kind=ClaimKind.AFFECTED_ASSET,
                value="customers",
                evidence_ids=(lineage.evidence_id,),
            ),
        ),
        summary="Evidence supports a renamed source column.",
        recommended_actions=("Restore the source contract.",),
        confidence=0.9,
    )

    outcome = kernel.finalize(decision)

    assert outcome.status is KernelFinalStatus.CONFIRMED
    assert outcome.root_cause_code == "SOURCE_SCHEMA_COLUMN_RENAMED"
    assert outcome.affected_assets == (
        "model.jaffle_shop.stg_payments",
        "orders",
        "customers",
    )
    assert set(outcome.evidence_ids) == {
        claim_id
        for claim in decision.claims
        for claim_id in claim.evidence_ids
    }
~~~

- [ ] **Step 2: Write fail-closed finalization tests**

Parameterize mutations of the valid decision and assert KernelError codes:

~~~python
@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("one_hypothesis", "ALTERNATIVE_HYPOTHESIS_REQUIRED"),
        ("no_refuted_hypothesis", "REFUTED_HYPOTHESIS_REQUIRED"),
        ("selected_is_refuted", "SELECTED_HYPOTHESIS_NOT_SUPPORTED"),
        ("unknown_assessment_evidence", "ASSESSMENT_EVIDENCE_UNKNOWN"),
        ("open_gap", "EVIDENCE_GAP_OPEN"),
        ("root_claim_missing_schema", "ROOT_CLAIM_EVIDENCE_INCOMPATIBLE"),
        ("root_claim_wrong_code", "ROOT_CLAIM_MISMATCH"),
        ("asset_without_lineage", "ASSET_CLAIM_EVIDENCE_INCOMPATIBLE"),
        ("invented_asset", "ASSET_CLAIM_EVIDENCE_INCOMPATIBLE"),
        ("duplicate_claim", "DUPLICATE_CLAIM"),
        ("cross_run_decision", "DECISION_SCOPE_MISMATCH"),
    ],
)
def test_confirmed_finalization_fails_closed(
    mutation: str,
    code: str,
) -> None:
    kernel, decision = _valid_kernel_and_decision()
    mutated = _mutate_decision(kernel, decision, mutation)
    with pytest.raises(KernelError, match=code):
        kernel.finalize(mutated)
~~~

Add:

~~~python
def test_insufficient_evidence_preserves_open_gap_without_claims() -> None:
    kernel = _kernel_with_run_results_only()
    decision = KernelDecision(
        status="INSUFFICIENT_EVIDENCE",
        incident_case_id=CASE_ID,
        run_id=RUN_ID,
        selected_hypothesis_id=None,
        assessments=(),
        claims=(),
        summary="The source schema gap is still open.",
        recommended_actions=("Collect source schema evidence.",),
        confidence=0.2,
    )
    outcome = kernel.finalize(decision)
    state = kernel.snapshot(model_requests_used=2)
    assert outcome.status is KernelFinalStatus.INSUFFICIENT_EVIDENCE
    assert outcome.root_cause_code is None
    assert outcome.affected_assets == ()
    assert outcome.evidence_ids == ()
    assert state.final_status is KernelFinalStatus.INSUFFICIENT_EVIDENCE
    assert state.gate_reason == "INSUFFICIENT_EVIDENCE"


def test_model_error_terminates_with_one_fixed_safe_reason() -> None:
    kernel = _kernel_with_run_results_only()
    outcome = kernel.terminate_model_error("MODEL_TIMEOUT")
    state = kernel.snapshot(model_requests_used=2)
    assert outcome.status is KernelFinalStatus.MODEL_ERROR
    assert outcome.summary == "MODEL_TIMEOUT"
    assert outcome.root_cause_code is None
    assert outcome.affected_assets == ()
    assert outcome.evidence_ids == ()
    assert state.final_status is KernelFinalStatus.MODEL_ERROR
    assert state.gate_reason == "MODEL_TIMEOUT"

    with pytest.raises(KernelError, match="MODEL_ERROR_REASON_INVALID"):
        _kernel().terminate_model_error("raw exception text")
~~~

- [ ] **Step 3: Run finalization tests and verify RED**

~~~powershell
uv run pytest tests/unit/test_diagnostic_kernel.py -q
~~~

Expected: new tests fail because finalize has not been implemented.

- [ ] **Step 4: Implement finalization without Ground Truth**

finalize must:

- validate decision case/run;
- reject repeated finalization;
- preserve INSUFFICIENT_EVIDENCE with empty claims;
- for CONFIRMED, require at least two registered hypotheses and one assessment per hypothesis;
- require selected SUPPORTED and at least one REFUTED alternative;
- reject OPEN or BLOCKED gaps;
- require all assessment and claim evidence IDs in the current inventory and associated with a global or hypothesis-targeted closed gap;
- require exactly one ROOT_CAUSE claim equal to the selected hypothesis code;
- require that root claim evidence types include DBT_NODE_ERROR and RELATION_SCHEMA;
- require one or more unique AFFECTED_ASSET claims;
- accept a direct-failure asset only when a cited DbtNodeErrorFact has the exact node_id;
- accept a downstream asset only when a cited downstream DbtLineageFact contains the exact node_id or exact node name;
- require the union of claim evidence types to include DBT_NODE_ERROR, RELATION_SCHEMA, and DBT_LINEAGE;
- project root_cause_code, affected_assets, and the first-occurrence ordered evidence-ID union exclusively from model claims;
- save assessments, claims, final status, and gate reason in the Kernel snapshot.

Do not compare to expected assets, expected schema, expected root cause, case ID-specific names, or Ground Truth. Those remain evaluator responsibilities.

Implement terminate_model_error as the only non-model terminal path. It must accept exactly:

~~~python
_SAFE_MODEL_ERRORS = {
    "MODEL_DECLINED",
    "MODEL_REQUEST_LIMIT",
    "MODEL_TIMEOUT",
    "MODEL_PROTOCOL_ERROR",
    "MODEL_RUNTIME_ERROR",
}
~~~

It rejects repeated terminalization and unknown strings, preserves the partial hypotheses/gaps/evidence inventory, sets final_status to MODEL_ERROR, stores the fixed code as gate_reason, and returns a KernelOutcome with no root cause, assets, or evidence claims.

- [ ] **Step 5: Run Kernel tests and a forbidden-import scan**

~~~powershell
uv run pytest tests/unit/test_diagnostic_kernel.py -q
uv run ruff check src/data_incident_gym/diagnostic_kernel.py tests/unit/test_diagnostic_kernel.py
rg -n 'GroundTruth|load_ground_truth|IncidentVerifier|expected_schema|expected_assets|schema_rename_payment_amount|schema_type_change_payment_amount' src/data_incident_gym/diagnostic_kernel.py
~~~

Expected: tests and Ruff pass; forbidden scan has no output.

- [ ] **Step 6: Commit the final evidence gate**

~~~powershell
git add src/data_incident_gym/diagnostic_kernel.py tests/unit/test_diagnostic_kernel.py
git diff --cached --name-only
git commit -m "feat: gate diagnosis claims on explicit evidence"
~~~

Expected: exactly the two Kernel paths are committed.

### Task 8: Integrate PydanticAI with Kernel intent while preserving four evidence tools

**Files:**
- Modify: src/data_incident_gym/diagnostic_agent.py
- Modify: tests/unit/test_diagnostic_agent.py
- Modify: tests/unit/test_diagnosis.py

- [ ] **Step 1: Replace controller-materialization tests with Kernel-adapter tests**

Retain the existing tests for request limits, total timeout, safe errors, trace redaction, sequential execution, run scope, and FunctionModel/TestModel behavior. Remove tests whose sole contract is that _materialize_diagnosis invents affected_assets or evidence_ids; Task 7 now tests the replacement at the Kernel Interface.

Add:

~~~python
def test_prompt_exports_m6_gap_driven_contract() -> None:
    assert SYSTEM_PROMPT_VERSION == "m6.diagnosis.v1"
    assert hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest() == (
        SYSTEM_PROMPT_SHA256
    )
    assert "InvestigationIntent" in SYSTEM_PROMPT
    assert "SOURCE_SCHEMA_COLUMN_RENAMED" in SYSTEM_PROMPT
    assert "SOURCE_SCHEMA_COLUMN_TYPE_CHANGED" in SYSTEM_PROMPT
    assert "at least two candidate hypotheses" in SYSTEM_PROMPT
    assert "Ground Truth" not in SYSTEM_PROMPT
    assert "schema_rename_payment_amount" not in SYSTEM_PROMPT
    assert "schema_type_change_payment_amount" not in SYSTEM_PROMPT


def test_injected_model_requires_truthful_runtime_identity(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="model_identity"):
        DiagnosisRunner.for_run(
            RUN_ID,
            DiagnosticSettings(_env_file=None),
            tmp_path,
            model=FunctionModel(_minimal_model),
            tools=NarrowEvidenceTools(),
        )


@pytest.mark.asyncio
async def test_model_claims_not_controller_defaults_become_diagnosis(
    tmp_path: Path,
) -> None:
    runner = _kernel_scripted_runner(tmp_path, fault_kind="TYPE_CHANGE")
    result = await runner.diagnose(CASE_ID)
    assert result.diagnosis.root_cause_code == "SOURCE_SCHEMA_COLUMN_TYPE_CHANGED"
    assert result.diagnosis.affected_assets == (
        "model.jaffle_shop.stg_payments",
        "orders",
        "customers",
    )
    assert result.investigation_state.final_status.value == "CONFIRMED"
    assert result.trace[-1].event_type == "KERNEL_STATE"
~~~

- [ ] **Step 2: Run focused tests and verify RED**

~~~powershell
uv run pytest tests/unit/test_diagnosis.py tests/unit/test_diagnostic_agent.py -q
~~~

Expected: tests fail on the v7 prompt, old output model, missing runtime identity, and missing investigation state.

- [ ] **Step 3: Replace the prompt and model output contract**

Set:

~~~python
SYSTEM_PROMPT = """
Diagnose one verified data incident using only the four registered read-only evidence tools.
Every tool call must include an InvestigationIntent naming one observable EvidenceGap.
Tool arguments must come from the verified run context or prior structured evidence.

Maintain at least two candidate hypotheses before returning CONFIRMED. Use only this
versioned ontology:
- SOURCE_SCHEMA_COLUMN_RENAMED: a source column was renamed while a consumer still uses
  the former name.
- SOURCE_SCHEMA_COLUMN_TYPE_CHANGED: a source column kept its name but changed to an
  incompatible data type for a consumer.

Use gaps to locate the failure, inspect its error, discover the source relation,
discriminate competing schema hypotheses, and map downstream impact. The order is chosen
from the evidence already observed; do not make an unsupported or duplicate call.

For CONFIRMED, return KernelDecision with one supported selected hypothesis, at least one
refuted alternative, and explicit ClaimEvidence entries for the root cause and every
affected asset. Cite only current-run EvidenceRecord IDs. The Diagnostic Kernel validates
the claims but does not create claims or citations for you. If a required gap remains open,
return INSUFFICIENT_EVIDENCE instead of guessing. Never return hidden reasoning.
""".strip()

SYSTEM_PROMPT_VERSION = "m6.diagnosis.v1"
SYSTEM_PROMPT_SHA256 = hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()
~~~

Use KernelDecision as Agent output_type. Add InvestigationIntent as the final argument of each of the four registered wrapper tools. Do not add a fifth @agent.tool.

- [ ] **Step 4: Make DiagnosisRunner a thin Kernel adapter**

Replace _RunState's evidence, fingerprints, accepted attempts, and controller fields with:

~~~python
@dataclass
class _RunState:
    kernel: DiagnosticKernel
    started_at: float = field(default_factory=monotonic)
    trace: list[TraceEvent] = field(default_factory=list)
    usage: RunUsage = field(default_factory=RunUsage)
    successful_calls: int = 0
    outcome: KernelOutcome | None = None
~~~

The shared execute path must:

1. call kernel.prepare_tool before EvidenceTools;
2. if prepare_tool raises KernelError with a fingerprint, append a failed ToolTraceEvent with only that fingerprint/fixed code and raise ToolFailed(code); TOOL_CALL_LIMIT is traced but is not appended to the already-full Kernel attempt ledger;
3. call the existing M3 method only when prepare succeeds;
4. convert EvidenceToolError to kernel.record_tool_failure with its fixed code;
5. pass successful records to kernel.record_tool_result;
6. record the same safe ToolTraceEvent with the prepared fingerprint;
7. return only accepted current-run records to the model.

Use the existing @agent.output_validator as the output-retry seam. Kernel finalization must happen there, not after agent.run:

~~~python
@agent.output_validator
def validate_output(
    ctx: RunContext[_RunState],
    output: KernelDecision,
) -> KernelDecision:
    try:
        outcome = ctx.deps.kernel.finalize(output)
    except KernelError as error:
        ctx.deps.trace.append(
            EvidenceGateTraceEvent(
                event_type="EVIDENCE_GATE",
                reason_code=error.code,
                accepted=False,
            )
        )
        raise ModelRetry(error.code) from None
    ctx.deps.outcome = outcome
    ctx.deps.trace.append(
        EvidenceGateTraceEvent(
            event_type="EVIDENCE_GATE",
            reason_code=outcome.status.value,
            accepted=True,
        )
    )
    return output
~~~

finalize must be atomic on rejection: an invalid decision does not set final_status, assessments, or claims, so a bounded output retry can correct it. Preserve the existing agent.run retries={"tools": 1, "output": 2} setting. After agent.run returns, require state.outcome to be present and construct Diagnosis only from that KernelOutcome:

~~~python
outcome = state.outcome
if outcome is None:
    raise _ControllerInvariantError("MODEL_PROTOCOL_ERROR")
diagnosis = Diagnosis(
    status=DiagnosisStatus(outcome.status.value),
    incident_case_id=context.incident_case_id,
    run_id=context.run_id,
    root_cause_code=outcome.root_cause_code,
    summary=outcome.summary,
    affected_assets=outcome.affected_assets,
    evidence_ids=outcome.evidence_ids,
    recommended_actions=outcome.recommended_actions,
    confidence=outcome.confidence,
)
~~~

For timeout, usage, protocol, invariant, or runtime failures, map the failure to one existing fixed safe reason code, call kernel.terminate_model_error(reason_code), and create the MODEL_ERROR Diagnosis from that KernelOutcome. Preserve the partial Kernel snapshot. Never pass exception text into the Kernel, Diagnosis, trace, report, or model. Remove _materialize_diagnosis and the case-shape controller logic entirely.

- [ ] **Step 5: Add truthful model identity**

Add:

~~~python
@dataclass(frozen=True)
class ModelIdentity:
    provider: str
    model: str
~~~

DiagnosisRunner.for_run sets ModelIdentity("openai-compatible", settings.model_name) for its production model. When callers inject model, require a model_identity keyword argument. FunctionModel tests use:

~~~python
model_identity=ModelIdentity("pydantic-function", "scripted-kernel-model")
~~~

Build DiagnosisMetrics from this identity, never from settings when a model was injected.

- [ ] **Step 6: Persist the terminal Kernel snapshot in DiagnosisRunResult**

In _result:

~~~python
snapshot = state.kernel.snapshot(model_requests_used=state.usage.requests)
trace = (
    *state.trace,
    KernelStateTraceEvent(
        event_type="KERNEL_STATE",
        state=snapshot,
    ),
)
return DiagnosisRunResult(
    diagnosis=diagnosis,
    evidence_records=state.kernel.evidence_records,
    trace=trace,
    investigation_state=snapshot,
    metrics=metrics,
)
~~~

Define KernelStateTraceEvent in diagnostic_kernel.py so it contains only the frozen state and does not import diagnosis.py.

- [ ] **Step 7: Run Agent, Diagnosis, safety, and static-scope tests**

~~~powershell
uv run pytest tests/unit/test_diagnostic_kernel.py tests/unit/test_diagnosis.py tests/unit/test_diagnostic_agent.py -q
uv run ruff check src/data_incident_gym/diagnostic_kernel.py src/data_incident_gym/diagnosis.py src/data_incident_gym/diagnostic_agent.py tests/unit/test_diagnostic_kernel.py tests/unit/test_diagnosis.py tests/unit/test_diagnostic_agent.py
rg -n '@agent\.tool' src/data_incident_gym/diagnostic_agent.py
rg -n '_materialize_diagnosis|GroundTruth|load_ground_truth|expected_assets|expected_schema' src/data_incident_gym/diagnostic_agent.py
~~~

Expected: tests and Ruff pass; exactly four @agent.tool lines exist; the forbidden controller/Ground Truth scan has no output.

- [ ] **Step 8: Commit the PydanticAI adapter migration**

~~~powershell
git add src/data_incident_gym/diagnostic_kernel.py src/data_incident_gym/diagnosis.py src/data_incident_gym/diagnostic_agent.py tests/unit/test_diagnosis.py tests/unit/test_diagnostic_agent.py
git diff --cached --name-only
git commit -m "feat: run diagnosis through explicit kernel state"
~~~

Expected: only the five listed paths are committed; EvidenceTools, evaluator, artifacts, CLI, dependencies, and submodule are not part of this commit.

### Task 9: Make Kernel state independently evaluable and preserve the six-file artifact contract

**Files:**
- Modify: src/data_incident_gym/diagnosis.py
- Modify: src/data_incident_gym/evaluation.py
- Modify: src/data_incident_gym/artifacts.py
- Modify: src/data_incident_gym/templates/report.md.j2
- Modify: tests/unit/test_diagnosis.py
- Modify: tests/unit/test_evaluation.py
- Modify: tests/unit/test_artifacts.py

- [ ] **Step 1: Write failing run-result and evaluator mutations**

Extend tests/unit/test_diagnosis.py so the terminal snapshot is mandatory and identical in both projections:

~~~python
def test_run_result_requires_one_identical_terminal_kernel_snapshot() -> None:
    run = _valid_diagnosis_run()
    assert run.trace[-1].event_type == "KERNEL_STATE"
    assert run.trace[-1].state == run.investigation_state

    with pytest.raises(ValidationError, match="terminal Kernel state"):
        run.model_copy(update={"trace": run.trace[:-1]}).model_validate(
            run.model_copy(update={"trace": run.trace[:-1]}).model_dump()
        )


def test_run_result_rejects_inventory_or_identity_drift() -> None:
    run = _valid_diagnosis_run()
    wrong_state = run.investigation_state.model_copy(
        update={"evidence_inventory": ()}
    )
    with pytest.raises(ValidationError):
        DiagnosisRunResult.model_validate(
            run.model_copy(update={"investigation_state": wrong_state}).model_dump()
        )
~~~

In tests/unit/test_evaluation.py, freeze this exact canonical check order:

~~~python
CHECK_ORDER = (
    "ENVIRONMENT_VERIFIED",
    "INVESTIGATION_STATE_VALID",
    "ALTERNATIVE_HYPOTHESIS_REFUTED",
    "CLAIM_EVIDENCE_COVERAGE",
    "DIAGNOSIS_CONFIRMED",
    "ROOT_CAUSE_EXACT",
    "AFFECTED_ASSETS_EXACT",
    "EVIDENCE_IDS_EXIST",
    "EVIDENCE_RUN_SCOPE",
    "REQUIRED_EVIDENCE_TYPES_PRESENT",
    "EVIDENCE_CONTENT_COMPATIBLE",
    "TRACE_READ_ONLY_SAFE",
    "RECOVERY_HEALTHY",
)
~~~

Add one parametrized mutation per independent Kernel rule:

~~~python
@pytest.mark.parametrize(
    ("mutation", "failed_code"),
    (
        ("terminal_state_missing", "INVESTIGATION_STATE_VALID"),
        ("terminal_state_not_identical", "INVESTIGATION_STATE_VALID"),
        ("inventory_drift", "INVESTIGATION_STATE_VALID"),
        ("budget_drift", "INVESTIGATION_STATE_VALID"),
        ("selected_not_supported", "INVESTIGATION_STATE_VALID"),
        ("no_refuted_alternative", "ALTERNATIVE_HYPOTHESIS_REFUTED"),
        ("root_claim_missing_schema", "CLAIM_EVIDENCE_COVERAGE"),
        ("asset_claim_missing_lineage", "CLAIM_EVIDENCE_COVERAGE"),
        ("claim_not_projected_to_diagnosis", "CLAIM_EVIDENCE_COVERAGE"),
        ("foreign_run_claim_evidence", "CLAIM_EVIDENCE_COVERAGE"),
    ),
)
def test_kernel_mutations_fail_closed(
    valid_inputs: ValidInputs,
    mutation: str,
    failed_code: str,
) -> None:
    ground_truth, verification, diagnosis_run = _mutate_kernel(
        valid_inputs,
        mutation,
    )
    result = DeterministicEvaluator.evaluate(
        ground_truth,
        verification,
        diagnosis_run,
        recovery_succeeded=True,
    )
    assert result.status == EvaluationStatus.FAILED
    assert failed_code in {
        check.code.value for check in result.checks if not check.passed
    }
~~~

Do not change expected Ground Truth to make any mutation pass.

- [ ] **Step 2: Run the focused tests and verify RED**

~~~powershell
uv run pytest tests/unit/test_diagnosis.py tests/unit/test_evaluation.py -q
~~~

Expected: tests fail because DiagnosisRunResult has no investigation_state, TraceEvent has no KERNEL_STATE variant, and the three M6 checks do not exist.

- [ ] **Step 3: Wire the frozen Kernel state into DiagnosisRunResult without a circular import**

In diagnosis.py, import InvestigationState and KernelStateTraceEvent from diagnostic_kernel.py, extend the discriminated union, and add the field:

~~~python
TraceEvent = Annotated[
    ToolTraceEvent | EvidenceGateTraceEvent | KernelStateTraceEvent,
    Field(discriminator="event_type"),
]


class DiagnosisRunResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    diagnosis: Diagnosis
    evidence_records: tuple[EvidenceRecord, ...]
    trace: tuple[TraceEvent, ...]
    investigation_state: InvestigationState
    metrics: DiagnosisMetrics
~~~

Extend its validator with exact structural invariants:

~~~python
terminal_events = tuple(
    event for event in self.trace if isinstance(event, KernelStateTraceEvent)
)
if len(terminal_events) != 1 or self.trace[-1] != terminal_events[0]:
    raise ValueError("trace requires one terminal Kernel state")
if terminal_events[0].state != self.investigation_state:
    raise ValueError("terminal Kernel state must equal investigation_state")
if self.investigation_state.incident_case_id != self.diagnosis.incident_case_id:
    raise ValueError("Kernel case must match diagnosis")
if self.investigation_state.run_id != self.diagnosis.run_id:
    raise ValueError("Kernel run must match diagnosis")
if self.investigation_state.evidence_inventory != evidence_ids:
    raise ValueError("Kernel evidence inventory must match records exactly")
if self.investigation_state.model_requests_used != self.metrics.model_requests:
    raise ValueError("Kernel model budget must match metrics")
if self.investigation_state.tool_calls_used != self.metrics.tool_call_attempts:
    raise ValueError("Kernel tool budget must match metrics")
if self.investigation_state.final_status is None:
    raise ValueError("run result requires terminal Kernel status")
if self.investigation_state.final_status.value != self.diagnosis.status.value:
    raise ValueError("Kernel final status must match diagnosis")
~~~

diagnostic_kernel.py must not import diagnosis.py. This one-way dependency is mandatory:

~~~text
diagnostic_kernel.py -> evidence.py
diagnosis.py -> diagnostic_kernel.py
diagnostic_agent.py -> diagnosis.py + diagnostic_kernel.py
~~~

- [ ] **Step 4: Add three independent M6 evaluator checks**

Add these members to EvaluationCheckCode immediately after ENVIRONMENT_VERIFIED:

~~~python
INVESTIGATION_STATE_VALID = "INVESTIGATION_STATE_VALID"
ALTERNATIVE_HYPOTHESIS_REFUTED = "ALTERNATIVE_HYPOTHESIS_REFUTED"
CLAIM_EVIDENCE_COVERAGE = "CLAIM_EVIDENCE_COVERAGE"
~~~

Change EvaluationResult.schema_version to Literal["m6.evaluation.v1"]. Evaluate Kernel state before Ground Truth exactness:

1. INVESTIGATION_STATE_VALID:
   - identity and terminal snapshot are exact;
   - inventory order and identity equal evidence_records;
   - model/tool usage equals DiagnosisMetrics;
   - CONFIRMED has one selected SUPPORTED hypothesis and no OPEN or BLOCKED gap;
   - all hypotheses, assessments, gaps, claims, and citations are duplicate-free;
   - ontology contains only the two approved codes.
2. ALTERNATIVE_HYPOTHESIS_REFUTED:
   - at least two hypotheses exist;
   - exactly one selected hypothesis is SUPPORTED;
   - at least one different hypothesis is REFUTED with current-run evidence.
3. CLAIM_EVIDENCE_COVERAGE:
   - exactly one ROOT_CAUSE claim equals diagnosis.root_cause_code;
   - the root claim cites at least one compatible DbtNodeErrorFact and one RelationSchemaFact;
   - AFFECTED_ASSET claim values equal diagnosis.affected_assets exactly;
   - each direct-failure asset cites a compatible DbtNodeErrorFact or DbtLineageFact;
   - each downstream asset cites compatible downstream DbtLineageFact;
   - the union of claim citations equals diagnosis.evidence_ids;
   - every cited ID exists in the current-run inventory.

Use model-type checks against EvidenceRecord.content. Do not use a model summary, error-message substring, or Ground Truth to decide whether the Kernel's own claim matrix is structurally sufficient. Ground Truth remains only in the later exactness checks.

Return safe count/category markers in check.actual. Do not echo raw model text, tool arguments, relation paths, or secrets.

- [ ] **Step 5: Upgrade artifact schemas while keeping exactly six filenames**

In artifacts.py, change only the schema versions:

~~~python
class TraceEnvelope(BaseModel):
    schema_version: Literal["m6.trace.v1"]
    sequence: Annotated[StrictInt, Field(ge=1)]
    event: TraceEvent


class EvidenceArtifact(BaseModel):
    schema_version: Literal["m6.evidence.v1"]
    incident_case_id: CaseId
    run_id: RunId
    records: tuple[EvidenceRecord, ...]


class RunMetadata(BaseModel):
    schema_version: Literal["m6.metadata.v1"]
    prompt_version: Literal["m6.diagnosis.v1"]
~~~

Keep ARTIFACT_FILENAMES byte-for-byte equal to:

~~~python
(
    "metadata.json",
    "trace.jsonl",
    "evidence.json",
    "diagnosis.json",
    "evaluation.json",
    "report.md",
)
~~~

The existing atomic temporary-directory write, duplicate-key rejection, symlink checks, runtime-label redaction, exact one-newline rule, and full reread validation remain unchanged. Full-bundle validation must parse the KERNEL_STATE event and compare it with DiagnosisRunResult.investigation_state.

- [ ] **Step 6: Render the auditable state in report.md**

Extend report.md.j2 with deterministic sections in this order:

1. diagnosis and evaluation summary;
2. candidate hypotheses and verdicts;
3. evidence gaps and closure status;
4. claim-to-evidence matrix;
5. model/tool budgets and remaining counts;
6. final Kernel gate reason;
7. existing failed-check and recovery sections.

For example:

~~~jinja2
## 调查状态

| 假设 | 根因编码 | 判定 |
|---|---|---|
{% for hypothesis in investigation_state.hypotheses -%}
| {{ hypothesis.hypothesis_id | e }} | {{ hypothesis.root_cause_code | e }} | {{ verdict_by_id.get(hypothesis.hypothesis_id, "UNASSESSED") | e }} |
{% endfor %}

## 主张与证据

| 主张类型 | 值 | EvidenceRecord |
|---|---|---|
{% for claim in investigation_state.claims -%}
| {{ claim.kind.value | e }} | {{ claim.value | e }} | {{ claim.evidence_ids | join(", ") | e }} |
{% endfor %}
~~~

Continue escaping all model-controlled strings. Do not render chain-of-thought, prompts, credentials, raw tool arguments, Ground Truth, or arbitrary HTML.

- [ ] **Step 7: Add artifact round-trip and fail-closed tests**

In tests/unit/test_artifacts.py assert:

~~~python
assert {path.name for path in output.iterdir()} == set(ARTIFACT_FILENAMES)
assert len(tuple(output.iterdir())) == len(ARTIFACT_FILENAMES)
assert len(read(output / "trace.jsonl").splitlines()) == len(
    artifact_run.diagnosis_run.trace
)
assert TraceEnvelope.model_validate_json(
    read(output / "trace.jsonl").splitlines()[-1]
).event.state == artifact_run.diagnosis_run.investigation_state
assert RunMetadata.model_validate_json(
    read(output / "metadata.json")
).schema_version == "m6.metadata.v1"
assert EvaluationResult.model_validate_json(
    read(output / "evaluation.json")
).schema_version == "m6.evaluation.v1"
~~~

Add report assertions for both ontology codes, gap IDs, claim values, budget numbers, the final gate, HTML escaping, and one terminal newline. Mutate each cross-file identity and assert ArtifactWriter returns only ARTIFACT_WRITE_FAILED without publishing a partial directory.

- [ ] **Step 8: Run the evaluation and artifact slice**

~~~powershell
uv run pytest tests/unit/test_diagnosis.py tests/unit/test_evaluation.py tests/unit/test_artifacts.py -q
uv run ruff check src/data_incident_gym/diagnosis.py src/data_incident_gym/evaluation.py src/data_incident_gym/artifacts.py tests/unit/test_diagnosis.py tests/unit/test_evaluation.py tests/unit/test_artifacts.py
rg -n 'm5\.(trace|evidence|metadata|evaluation)|m5\.diagnosis' src\data_incident_gym tests\unit
~~~

Expected: tests and Ruff pass; the M5 schema/prompt scan has no output. ARTIFACT_FILENAMES still has exactly six members.

- [ ] **Step 9: Commit the independent evaluation and artifact contract**

~~~powershell
git add src/data_incident_gym/diagnosis.py src/data_incident_gym/evaluation.py src/data_incident_gym/artifacts.py src/data_incident_gym/templates/report.md.j2 tests/unit/test_diagnosis.py tests/unit/test_evaluation.py tests/unit/test_artifacts.py
git diff --cached --name-only
git commit -m "feat: evaluate and persist diagnostic kernel state"
~~~

Expected: exactly the seven listed paths are committed.

### Task 10: Close both cases through the FunctionModel workflow and provider-neutral CLI

**Files:**
- Modify: src/data_incident_gym/cli.py
- Modify: tests/unit/test_cli.py
- Modify: tests/integration/test_diagnostic_agent.py
- Modify: tests/integration/test_evaluation_runner.py

- [ ] **Step 1: Make the current single-case integrations fail for the type-change case**

Parameterize both integration files over:

~~~python
CASE_IDS = (
    "schema_rename_payment_amount",
    "schema_type_change_payment_amount",
)
~~~

Each parameter must execute a real IncidentLab mutation/build, the real four EvidenceTools, a FunctionModel, DiagnosticKernel, deterministic evaluator, atomic six-file writer, reset, and healthy verification. Assert the type-change case ends with:

~~~python
assert result.diagnosis.root_cause_code == "SOURCE_SCHEMA_COLUMN_TYPE_CHANGED"
assert result.investigation_state.final_status == KernelFinalStatus.CONFIRMED
assert len(result.investigation_state.hypotheses) >= 2
assert any(
    assessment.verdict == HypothesisVerdict.REFUTED
    for assessment in result.investigation_state.assessments
)
~~~

- [ ] **Step 2: Run both integrations and verify RED**

~~~powershell
uv run pytest tests/integration/test_diagnostic_agent.py tests/integration/test_evaluation_runner.py -q
~~~

Expected: the rename case reaches the new adapter or reveals fixture updates; the type-change case fails because the scripted model still assumes the rename answer and emits no intent/claim matrix.

- [ ] **Step 3: Replace the answer-shaped FunctionModel with one evidence-driven script**

Keep one shared script for both cases. It may receive the public incident case ID and run ID needed for protocol identity, but it must not branch on case ID. It may know the approved ontology but must not receive Ground Truth, expected assets, expected schema, or expected answer. Its behavior is:

~~~python
if not _has_run_results(messages):
    return _tool_call(
        "get_dbt_run_results",
        {
            "run_id": initial_run_id,
            "intent": {
                "gap_id": "g_failure",
                "gap_kind": "LOCATE_FAILURE",
                "hypothesis_ids": [],
                "new_hypotheses": [],
            },
        },
    )

if not _has_node_error(messages):
    return _tool_call(
        "get_dbt_node_error",
        {
            "node_id": _failed_node_id(messages),
            "intent": {
                "gap_id": "g_explain",
                "gap_kind": "EXPLAIN_FAILURE",
                "hypothesis_ids": [],
                "new_hypotheses": [],
            },
        },
    )

if not _has_upstream_lineage(messages):
    return _tool_call(
        "get_dbt_lineage",
        {
            "node_id": _failed_node_id(messages),
            "direction": "upstream",
            "intent": {
                "gap_id": "g_source",
                "gap_kind": "DISCOVER_SOURCE_RELATION",
                "hypothesis_ids": [],
                "new_hypotheses": [],
            },
        },
    )

if not _has_relation_schema(messages):
    return _tool_call(
        "get_relation_schema",
        {
            "relation_name": _upstream_relation_name(messages),
            "intent": {
                "gap_id": "g_schema",
                "gap_kind": "DISCRIMINATE_SCHEMA",
                "hypothesis_ids": ["h_rename", "h_type"],
                "new_hypotheses": [
                    {
                        "hypothesis_id": "h_rename",
                        "root_cause_code": "SOURCE_SCHEMA_COLUMN_RENAMED",
                    },
                    {
                        "hypothesis_id": "h_type",
                        "root_cause_code": "SOURCE_SCHEMA_COLUMN_TYPE_CHANGED",
                    },
                ],
            },
        },
    )
~~~

After schema discrimination, request downstream lineage with MAP_IMPACT and both hypothesis IDs. Read RelationSchemaFact:

~~~python
columns = _relation_columns(messages)
if any(column["name"] == "amount" and column["data_type"] == "text" for column in columns):
    selected = ("h_type", "SOURCE_SCHEMA_COLUMN_TYPE_CHANGED")
    refuted = "h_rename"
else:
    selected = ("h_rename", "SOURCE_SCHEMA_COLUMN_RENAMED")
    refuted = "h_type"
~~~

Return KernelDecision with both assessments and explicit claims. Derive every evidence ID from tool returns:

~~~python
return _final_response(
    {
        "status": "CONFIRMED",
        "incident_case_id": incident_case_id,
        "run_id": initial_run_id,
        "selected_hypothesis_id": selected[0],
        "assessments": [
            {
                "hypothesis_id": selected[0],
                "verdict": "SUPPORTED",
                "evidence_ids": [node_error_id, schema_id],
            },
            {
                "hypothesis_id": refuted,
                "verdict": "REFUTED",
                "evidence_ids": [schema_id],
            },
        ],
        "claims": [
            {
                "kind": "ROOT_CAUSE",
                "value": selected[1],
                "evidence_ids": [node_error_id, schema_id],
            },
            *[
                {
                    "kind": "AFFECTED_ASSET",
                    "value": asset,
                    "evidence_ids": [lineage_id],
                }
                for asset in _observed_model_assets(messages)
            ],
        ],
        "summary": "SOURCE_SCHEMA_CHANGE_CONFIRMED",
        "recommended_actions": ("RESTORE_SOURCE_SCHEMA_CONTRACT",),
        "confidence": 0.9,
    }
)
~~~

The script is an integration driver, not a second production diagnosis algorithm. It must branch only on returned EvidenceRecord content.

- [ ] **Step 4: Pass truthful FunctionModel identity**

Every injected DiagnosisRunner in integration tests must include:

~~~python
model=FunctionModel(
    partial(
        _scripted_diagnosis,
        incident_case_id=case_id,
        initial_run_id=run_id,
    )
),
model_identity=ModelIdentity(
    provider="pydantic-function",
    model="scripted-kernel-model",
),
~~~

Assert metadata.json contains this identity. Production EvaluationRunner.for_project continues to use the OpenAI-compatible settings identity.

- [ ] **Step 5: Make CLI help provider-neutral and list both fixed cases**

Keep the existing command grammar and positional incident_case_id. Do not add a custom incident file, arbitrary path, SQL, provider flag, or mutation flag. Update help and tests so:

~~~python
assert "schema_rename_payment_amount" in result.output
assert "schema_type_change_payment_amount" in result.output
assert "Ollama" not in result.output
assert "MiMo" not in result.output
~~~

Unknown cases still fail before mutation with the existing safe fixed error code.

- [ ] **Step 6: Run both deterministic vertical slices**

~~~powershell
uv run pytest tests/unit/test_cli.py tests/integration/test_diagnostic_agent.py tests/integration/test_evaluation_runner.py -q
uv run data-incident-gym --help
uv run data-incident-gym evaluate schema_rename_payment_amount --help
uv run ruff check src/data_incident_gym/cli.py tests/unit/test_cli.py tests/integration/test_diagnostic_agent.py tests/integration/test_evaluation_runner.py
rg -n 'GroundTruth|load_ground_truth|expected_assets|expected_schema|root_cause_code' tests\integration\test_diagnostic_agent.py
~~~

Expected: tests and Ruff pass; help lists only the two fixed cases and is provider-neutral. The script accepts incident_case_id only for output identity and has no case-ID conditional. In the FunctionModel script, root_cause_code may appear only in the two hypothesis declarations and evidence-derived final projection; GroundTruth and expected-value names have no matches.

- [ ] **Step 7: Commit the two-case deterministic workflow**

~~~powershell
git add src/data_incident_gym/cli.py tests/unit/test_cli.py tests/integration/test_diagnostic_agent.py tests/integration/test_evaluation_runner.py
git diff --cached --name-only
git commit -m "test: close two incidents through diagnostic kernel"
~~~

Expected: exactly the four listed paths are committed.

### Task 11: Gate the real model with exactly six retained samples

**Files:**
- Rename: tests/e2e/test_ollama_diagnosis.py -> tests/e2e/test_real_model_diagnosis.py
- Rename: tests/e2e/test_ollama_evaluation.py -> tests/e2e/test_real_model_evaluation.py
- Modify: tests/e2e/test_real_model_diagnosis.py
- Modify: tests/e2e/test_real_model_evaluation.py

This task has two separate gates. Writing and committing provider-neutral tests is authorized by plan execution. Running doctor or the real-model test is not authorized until the user explicitly approves the bounded request budget.

- [ ] **Step 1: Rename the provider-specific test files**

~~~powershell
Move-Item -LiteralPath 'tests\e2e\test_ollama_diagnosis.py' -Destination 'tests\e2e\test_real_model_diagnosis.py'
Move-Item -LiteralPath 'tests\e2e\test_ollama_evaluation.py' -Destination 'tests\e2e\test_real_model_evaluation.py'
~~~

Remove Ollama/M5 wording from module names, test names, skip reasons, and output labels. Keep test_real_model_diagnosis.py as an opt-in single-run troubleshooting probe; it is not part of the M6 acceptance command and must not be run during the bounded six-sample acceptance window.

- [ ] **Step 2: Write the exact two-case, three-sample acceptance test**

tests/e2e/test_real_model_evaluation.py must use these constants:

~~~python
from dataclasses import dataclass
from pathlib import Path

from data_incident_gym.evaluation_runner import EvaluationAttemptResult

CASE_IDS = (
    "schema_rename_payment_amount",
    "schema_type_change_payment_amount",
)
SAMPLES_PER_CASE = 3
MINIMUM_PASSES_PER_CASE = 2


@dataclass(frozen=True)
class SampleObservation:
    case_id: str
    sample_index: int
    status: str
    run_id: str | None
    artifact_dir: Path | None

    @classmethod
    def from_result(
        cls,
        case_id: str,
        sample_index: int,
        result: EvaluationAttemptResult,
    ) -> SampleObservation:
        return cls(
            case_id=case_id,
            sample_index=sample_index,
            status=result.status.value,
            run_id=result.run_id,
            artifact_dir=result.artifact_dir,
        )
~~~

Run all six samples even if one EvaluationRunner attempt raises, and keep failures in the denominator:

~~~python
@pytest.mark.asyncio
async def test_real_model_passes_two_of_three_for_each_incident() -> None:
    settings = Settings(_env_file=None)
    diagnostic_settings = DiagnosticSettings(_env_file=None)
    observations: list[SampleObservation] = []
    lab = IncidentLab(settings, PROJECT_ROOT)

    for case_id in CASE_IDS:
        try:
            for sample_index in range(1, SAMPLES_PER_CASE + 1):
                try:
                    result = await EvaluationRunner.for_project(
                        settings,
                        diagnostic_settings,
                        PROJECT_ROOT,
                    ).run(case_id)
                except Exception:
                    observations.append(
                        SampleObservation(
                            case_id=case_id,
                            sample_index=sample_index,
                            status="ERROR",
                            run_id=None,
                            artifact_dir=None,
                        )
                    )
                else:
                    observations.append(
                        SampleObservation.from_result(
                            case_id,
                            sample_index,
                            result,
                        )
                    )
        finally:
            lab.reset(case_id)

    assert len(observations) == len(CASE_IDS) * SAMPLES_PER_CASE
    for observation in observations:
        print(
            "M6_SAMPLE "
            f"case_id={observation.case_id} "
            f"sample={observation.sample_index} "
            f"status={observation.status} "
            f"run_id={observation.run_id or 'NONE'}"
        )
        if observation.artifact_dir is not None:
            assert observation.artifact_dir.is_dir()
            assert {
                path.name for path in observation.artifact_dir.iterdir()
            } == set(ARTIFACT_FILENAMES)

    for case_id in CASE_IDS:
        case_samples = tuple(
            item for item in observations if item.case_id == case_id
        )
        assert len(case_samples) == SAMPLES_PER_CASE
        assert sum(item.status == "PASSED" for item in case_samples) >= (
            MINIMUM_PASSES_PER_CASE
        )
~~~

SampleObservation is a frozen local test model. It may store only case ID, sample index, status, run ID, and artifact directory. Print one safe M6_SAMPLE line per observation. Successful attempts must retain exactly the canonical six artifact files; no artifact directory is deleted or overwritten. Do not synthesize a pass from separate doctor or diagnosis-probe calls.

- [ ] **Step 3: Prove all deterministic gates before requesting model-call authority**

~~~powershell
Remove-Item Env:DIG_RUN_REAL_MODEL_TESTS -ErrorAction SilentlyContinue
uv run ruff check .
uv run pytest -m 'not real_model' -q
uv run pytest tests/integration/test_diagnostic_agent.py tests/integration/test_evaluation_runner.py tests/e2e/test_incident_reproducibility.py -q
uv run data-incident-gym --help
git diff --check
~~~

Expected: every deterministic gate passes. Real-model tests are not collected by the explicit marker expression. If any deterministic gate fails, stop and fix it before asking for network/model authorization.

- [ ] **Step 4: Commit the real-model gate before running it**

~~~powershell
git add tests/e2e/test_real_model_diagnosis.py tests/e2e/test_real_model_evaluation.py
git diff --cached --name-status
git commit -m "test: gate real model across two incidents"
~~~

Expected: Git records two renames/modifications; no artifact, root Markdown file, .dig output, or credential is staged.

- [ ] **Step 5: Ask for one explicit bounded authorization**

Report the exact cost boundary before execution:

- doctor: at most 2 model requests;
- six evaluation attempts: at most 8 model requests each;
- total authorized ceiling: 50 model requests;
- acceptance denominator: exactly 3 attempts for each of 2 cases;
- no automatic retry, seventh sample, replacement sample, or diagnosis probe;
- artifacts from successful attempts are retained locally and remain ignored by Git.

Do not proceed on implied approval from earlier M5 work.

- [ ] **Step 6: After authorization, run doctor once and the acceptance test once**

~~~powershell
uv run data-incident-gym doctor
$env:DIG_RUN_REAL_MODEL_TESTS = '1'
uv run pytest tests/e2e/test_real_model_evaluation.py -q -s
Remove-Item Env:DIG_RUN_REAL_MODEL_TESTS -ErrorAction SilentlyContinue
~~~

Expected: doctor passes; the one pytest invocation produces exactly six M6_SAMPLE observations; each case has at least two PASSED observations. Do not rerun a failed case. If the gate fails, preserve all observations/artifacts, report the fixed denominator, and stop before push.

### Task 12: Run final regression, review the fixed diff, and close M6 without overclaiming

**Files:**
- Modify only if a verified blocker is found: files already owned by Tasks 1-11
- Do not commit: AGENT.md
- Do not commit: README.md
- Do not commit: mistake.md
- Do not commit: third_party/jaffle_shop/.dig
- Do not commit: artifacts

- [ ] **Step 1: Run the full deterministic regression from the M6 worktree**

~~~powershell
Remove-Item Env:DIG_RUN_REAL_MODEL_TESTS -ErrorAction SilentlyContinue
uv sync --locked
uv run ruff check .
uv run pytest tests/unit -q
uv run pytest tests/integration -q
uv run pytest tests/e2e -m 'not real_model' -q
uv run pytest -q
uv run data-incident-gym --help
uv run data-incident-gym doctor --help
git diff --check
~~~

Expected: dependency lock is unchanged, Ruff and all deterministic tests pass, the default full suite skips real-model tests, and help commands exit zero. Do not treat a skipped real-model test as real-model acceptance; report Task 11 separately.

- [ ] **Step 2: Run structural scope audits**

~~~powershell
rg -n '@agent\.tool' src\data_incident_gym\diagnostic_agent.py
rg -n 'GroundTruth|load_ground_truth|expected_assets|expected_schema|ground_truth_digest' src\data_incident_gym\diagnostic_kernel.py src\data_incident_gym\diagnostic_agent.py src\data_incident_gym\evidence_tools.py
rg -n 'SELECT|INSERT|UPDATE|DELETE|ALTER|CREATE|DROP' src\data_incident_gym\diagnostic_kernel.py src\data_incident_gym\diagnostic_agent.py
rg -n 'LangGraph|Airflow|OpenLineage|Marquez|custom incident|arbitrary SQL|write tool|auto.?repair' src tests docs\requirements.md
rg -n 'schema_rename_payment_amount|schema_type_change_payment_amount' src tests config\incidents docs\requirements.md
rg -n 'SOURCE_SCHEMA_COLUMN_RENAMED|SOURCE_SCHEMA_COLUMN_TYPE_CHANGED' src tests config\incidents docs\requirements.md
~~~

Expected:

- exactly four @agent.tool lines;
- no Ground Truth terms in Kernel/Agent/EvidenceTools;
- no SQL verbs in Kernel/Agent;
- no prohibited P1 expansion in executable code or accepted requirements;
- both cases and exactly two ontology members appear where intended.

Inspect any documentation match rather than blindly deleting it; historical roadmap exclusions may legitimately mention deferred systems.

- [ ] **Step 3: Verify the six-file contract and historical fixture**

~~~powershell
uv run python -c "from data_incident_gym.artifacts import ARTIFACT_FILENAMES; assert ARTIFACT_FILENAMES == ('metadata.json','trace.jsonl','evidence.json','diagnosis.json','evaluation.json','report.md')"
$historicBlob = git hash-object --path=config/incidents/schema_rename_payment_amount.json config/incidents/schema_rename_payment_amount.json
if ($historicBlob -ne 'eb2cca9026c778f25119660affa206ce1377f46d') { throw 'historic Ground Truth Git blob changed' }
git diff --exit-code c4c4cb820aa805976e77e6602e6b3f82b0ff5dfc -- config/incidents/schema_rename_payment_amount.json
uv run python -c "from data_incident_gym.incidents import CASE_ID, load_ground_truth; assert load_ground_truth(CASE_ID).digest() == 'c2fa0d97b603c37a21d07123481b2a9d09a34dbd3aab38e6e102a55d59ce4491'"
git diff --submodule=short c4c4cb820aa805976e77e6602e6b3f82b0ff5dfc..HEAD
~~~

Expected: six names are exact; both the historic committed Git blob and canonical semantic digest are unchanged; the submodule commit pointer is unchanged.

- [ ] **Step 4: Review the complete M6 diff against the fixed baseline**

Use the requesting-code-review skill with baseline c4c4cb820aa805976e77e6602e6b3f82b0ff5dfc. Review both axes:

1. approved M6 contract and requirements coverage;
2. repository standards, safety, determinism, and evidence provenance.

Classify findings:

- BLOCKER: violates an approved contract, leaks Ground Truth, permits writes/free SQL/free paths, breaks either case, corrupts six-file artifacts, or falsifies budgets/provenance;
- LOCAL: narrow defect within an M6-owned file;
- BACKLOG: useful P1/P2 work explicitly excluded from M6;
- DECISION: requires a product-contract change and must return to the user.

Fix only BLOCKER and LOCAL findings inside existing task ownership. Do not silently absorb BACKLOG or DECISION work.

- [ ] **Step 5: Allow at most one repair-and-review cycle**

For each accepted finding:

1. add or identify a deterministic failing test;
2. run the smallest RED test;
3. make the minimum change;
4. run GREEN and the impacted suite;
5. commit only the owned paths.

Then run one final review. If a blocker remains after that cycle, stop and report it; do not create an unbounded review loop.

- [ ] **Step 6: Audit commit and workspace boundaries**

~~~powershell
git log --oneline --decorate c4c4cb820aa805976e77e6602e6b3f82b0ff5dfc..HEAD
git diff --name-status c4c4cb820aa805976e77e6602e6b3f82b0ff5dfc..HEAD
git status --short
git diff --cached --name-only
git submodule status
~~~

Expected: staged output is empty; commits contain only planned M6 paths; root AGENT.md, README.md, mistake.md, generated .dig, artifacts, caches, and credentials are absent from commits. Report any user-owned dirty files without modifying them.

- [ ] **Step 7: Request separate push authority**

Before push, report:

- exact HEAD;
- all commit subjects since c4c4cb;
- deterministic test results;
- the fixed six-sample real-model denominator and per-case pass counts, or that real-model acceptance was not authorized/run;
- remaining dirty files;
- remote branch destination.

Only after explicit authorization:

~~~powershell
git push -u origin codex/m6-diagnostic-kernel-v1-20260829
~~~

Do not force-push and do not push to master.

- [ ] **Step 8: Verify remote Ubuntu CI for the exact pushed HEAD**

After an authorized push:

~~~powershell
$expectedHead = git rev-parse HEAD
$runs = gh run list --branch codex/m6-diagnostic-kernel-v1-20260829 --limit 5 --json databaseId,headSha,status,workflowName | ConvertFrom-Json
$run = $runs | Where-Object { $_.headSha -eq $expectedHead -and $_.workflowName -eq 'ci' } | Select-Object -First 1
if ($null -eq $run) { throw 'no CI run found for exact HEAD' }
$runId = [string]$run.databaseId
gh run watch $runId --exit-status
$runView = gh run view $runId --json headSha,status,conclusion,jobs | ConvertFrom-Json
if ($runView.headSha -ne $expectedHead) { throw 'CI head SHA mismatch' }
if ($runView.conclusion -ne 'success') { throw 'CI did not succeed' }
~~~

Expected: Ubuntu Ruff, unit, integration, and ordinary e2e jobs succeed for exact HEAD. Real-model acceptance remains a separately reported local observation unless an explicit secret-enabled workflow exists and was approved.

- [ ] **Step 9: Deliver the M6 closure report**

The final handoff must distinguish:

- implemented and deterministically verified;
- real-model six-sample result, including all six denominator entries;
- remote CI result for exact HEAD;
- deferred M7/P1 items;
- unchanged six-file artifact contract;
- preserved user-owned root files and generated outputs.

Do not claim M6 complete if either deterministic two-case closure, the approved real-model gate, or exact-HEAD CI is pending. Say precisely which gate remains.

## Plan self-review checklist

- [ ] **Requirements coverage:** every Approved M6 contract item maps to at least one task and an executable verification.
- [ ] **Task independence:** each task has owned paths, a RED observation, minimum GREEN implementation, focused verification, and commit boundary.
- [ ] **Deep Module boundary:** DiagnosticKernel owns state/gates; PydanticAI owns model adaptation; EvidenceTools own reads; evaluator alone reads Ground Truth.
- [ ] **No hidden seventh artifact:** InvestigationState appears only as a typed terminal trace event and a report rendering.
- [ ] **No fake agent result:** the model supplies hypotheses, selected root cause, affected assets, and citations; the Kernel rejects or projects but never invents them.
- [ ] **Two-case proof:** both deterministic cases receive lab, integration, evaluator, artifact, recovery, and reproducibility coverage.
- [ ] **Bounded real-model proof:** exactly six denominator observations, at most 50 authorized requests including doctor, no rerun or replacement.
- [ ] **Historic stability:** the schema-rename Ground Truth baseline Git blob and canonical GroundTruth.digest() are asserted separately, independent of checkout line endings.
- [ ] **Safety:** four read-only tools, fixed allowlists, no free SQL/path/write/repair, no Ground Truth leakage.
- [ ] **Workspace safety:** stale M6 branch is excluded; root Markdown, submodule pointer, .dig, artifacts, and unrelated dirt remain uncommitted.

Run this placeholder scan before executing the plan:

~~~powershell
$placeholderPattern = 'T[B]D|T[O]DO|implement[ ]later|fill[ ]in|Add[ ]appropriate|Write[ ]tests[ ]for[ ]the[ ]above|Similar[ ]to[ ]Task'
$matches = rg -n $placeholderPattern docs\superpowers\plans\2026-08-29-m6-diagnostic-kernel-v1.md
if ($LASTEXITCODE -eq 0) { $matches; throw 'plan contains placeholders' }
if ($LASTEXITCODE -ne 1) { throw 'placeholder scan failed' }
~~~

Run these type and path consistency checks:

~~~powershell
rg -n 'DiagnosticKernel|InvestigationState|InvestigationIntent|KernelDecision|KernelOutcome|KernelStateTraceEvent|ClaimEvidence|HypothesisAssessment' docs\superpowers\plans\2026-08-29-m6-diagnostic-kernel-v1.md
rg -n '^### Task [0-9]+:' docs\superpowers\plans\2026-08-29-m6-diagnostic-kernel-v1.md
rg -n 'src/data_incident_gym|tests/(unit|integration|e2e)|config/incidents|docs/requirements.md' docs\superpowers\plans\2026-08-29-m6-diagnostic-kernel-v1.md
~~~

Expected: type names are consistent, Tasks 1-12 exist once in ascending order, and every edited path is repository-relative.

## Execution handoff

After the user approves this plan, offer one execution mode:

1. **Subagent-driven execution (recommended):** stay in this conversation, use only the configured luna_worker agent for bounded task ownership, review between tasks, and preserve the explicit model-call/push gates.
2. **Inline execution:** execute tasks sequentially in this conversation with the same RED/GREEN, commit, review, model-call, and push boundaries.

Neither mode starts from codex/m6-20260829. Both create a new worktree from exact M5 commit c4c4cb820aa805976e77e6602e6b3f82b0ff5dfc and stop separately for real-model and push authorization.
