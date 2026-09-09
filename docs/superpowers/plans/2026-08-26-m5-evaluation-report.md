# M5 Deterministic Evaluation and Reporting Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 P0 的 M5 确定性评测与报告闭环，使一条 `eval run` 命令能独立完成故障重放、只读诊断、Ground Truth 隔离评分、健康恢复与六文件审计产物生成。

**Architecture:** 在 M4 已冻结的 `DiagnosisRunResult` 之后设置纯确定性 `DeterministicEvaluator` seam；它独占 Ground Truth，只评分冻结的 Diagnosis、EvidenceRecord 库存、trace 和 M2 verification，不在 reset 后重查数据库。`ArtifactWriter` 把一次运行原子写成六个固定文件，`EvaluationRunner.run(case_id)` 作为小 Interface/深 Implementation 隐藏 `Ground Truth preflight → reset → inject → build → diagnose → recover → evaluate → persist`。preflight 对象不进入 Agent 依赖图，只在 Diagnosis 冻结后传给 evaluator；这样既保持答案隔离，也确保 Ground Truth 无效时不会在已产生 Diagnosis 后破坏六文件合同。`doctor` 是独立的只读能力检查 Module，不与评测分数或 Agent 工具集混合。

**Tech Stack:** Python 3.12.10, uv 0.11.24, Pydantic 2.13.4, PydanticAI 2.34.0, Typer 0.27.1, Jinja2 3.1.6, psycopg 3.3.4, dbt-core 1.12.3, pytest 9.1.1, Ruff 0.16.4, PostgreSQL 17.6 Alpine

---

## 实施前基线与已批准决策

- M4 已在 `96ad13c062a031f79924de1c5212552011b64097` 完成并推送到 `origin/master`；用户已人工确认该 HEAD 的远程 Ubuntu CI 通过。
- 本次 M5 重新实施必须在新的隔离 worktree 中从精确 M4 提交 `96ad13c062a031f79924de1c5212552011b64097` 起步；不得 reset、rebase 或清理当前共享 `master`，也不得把当前本地 `master` 的 M5 提交当作新基线。
- 用户批准的 canonical 运行产物精确为 `metadata.json`、`trace.jsonl`、`evidence.json`、`diagnosis.json`、`evaluation.json`、`report.md`；不得改回四文件或把证据/评分塞入 metadata/trace 替代独立文件。
- 一次 `uv run data-incident-gym eval run schema_rename_payment_amount` 只产生一个独立样本。真实模型验收必须连续执行精确三个独立样本，成功数至少为 2；模型内部 retry 不是新样本，不得追加运行后挑选成功结果。
- M5 是 P0 的单案例 pass/fail 闭环，不实现 P1 的 Hypothesis、EvidenceGap、claim-evidence matrix、静态 Skill baseline、消融、Accuracy/F1 或跨变体泛化结论。
- 当前 M4 HEAD 的 `SYSTEM_PROMPT` 未落地 M4 计划已批准的通用根因 ontology 描述；M5 Task 2 只补回 `SOURCE_SCHEMA_COLUMN_RENAMED` 的一般语义并升版/hash，不写 case ID、节点、关系、列名、expected evidence ID 或正确影响集合。这是让模型使用稳定输出词汇，不是向 Agent 注入 Ground Truth。
- `AGENT.md`、`README.md`、`mistake.md` 是根目录 workspace-only 文件；保留用户已有修改，不得 stage、commit 或 push。`README.md`/`mistake.md` 只在最终阶段更新实际事实。
- 新 worktree 只从当前工作区带入本计划的修订版和 `docs/requirements.md` 中已获批的六文件合同、冻结评分及专用 diagnostic dbt profile 需求；Task 1 精确提交这些项目文档。根目录 `AGENT.md`、`README.md`、`mistake.md` 的当前用户内容也要复制到新 worktree，但始终保持 workspace-only，不进入提交。
- 当前 Windows 主机曾在长时间 Python/dbt 运行中观察到机器级 native 不稳定；长跑 integration/e2e 可使用已验证的、仅当前 PowerShell 进程生效的 P-core affinity，并必须在 `finally` 恢复。不得把 affinity 写入项目代码、CI、BIOS、注册表或全局设置。

## M5 重新实施与收敛协议（优先级高于后续 Task 文字）

本节是针对第一次 M5 实施出现长期“修复→开放式重审→再修复”循环后的冻结协议。若后续 Task 的旧措辞与本节冲突，以本节和根目录 `AGENT.md` 为准。

1. **实现历史隔离**：`96ad13c` 之后当前已有的 M5 源码、测试和提交是废弃的实现历史。新实施不得 cherry-pick、merge 或复制其中的 `src/`、`tests/` 代码；只能只读复盘其失败模式。唯一允许带入的是上一节列明的获批项目文档，以及三个用户拥有的 workspace-only 根目录文件。
2. **合同冻结**：M5 固定为六个 Task、六文件产物、M3 四个只读工具、三种 Diagnosis 终态、预算 `6/8/2/180`、`m5.diagnosis.v1` 和单案例精确评分。任何 `v2` prompt、额外输出字段、额外生产 Module、额外安全语法或 P1 Kernel 都是 `DECISION`，不得作为审查修复自动加入。
3. **文件冻结**：生产 Module 仅限下方“文件结构和 Module 责任”表。特别禁止自动新增 `output_safety.py`、通用敏感信息分类器、SQL/DSN/自然语言语法分析器、全局 publish lock manager、PID/owner/stale-lock 协议或审计框架；若现有合同确实无法在不新增它们的情况下满足，停止并请用户决策。
4. **验收语料冻结**：每个 Task 在编码前锁定正例、反例、允许路径和非目标。审查者新提出但不在冻结威胁模型/验收语料中的假想变体只能记为 `BACKLOG`；不得把无限输入空间逐轮转换为新正则和新测试。
5. **安全边界**：安全依靠“秘密不进入 Agent/产物数据流、类型化字段、固定错误码、最小化 trace、模板转义”实现。有限 fingerprint 扫描只覆盖本计划列出的高置信哨兵；P0 不承诺识别任意自然语言、任意编码、任意 SQL/DSN 或伪装指令。若需要这种强保证，必须先把相关自由文本改成枚举/模板字段并获得用户批准。
6. **并发边界**：P0 假定 `EvaluationRunner` 为每次运行生成唯一 run ID，并且一个 run ID 只有一个协作 writer；不同 run ID 可以并发。Writer 仍须在相同 run ID 竞争时 fail closed 且不覆盖已发布目录，但不实现跨进程全局锁、租约、进程存活探测或崩溃恢复协议。
7. **审查预算**：每个功能 Task 最多“1 次独立广度审查 + 1 次 BLOCKER 修复 + 1 次冻结 finding 定向复审”。复审只验证原 BLOCKER 是否关闭及修复是否直接引入回归，不得重新开放全量攻击面。仍有 BLOCKER 或出现 `DECISION` 时停止并向用户报告，不自动开始第二轮。
8. **阻断门禁**：只有同时具备冻结 P0 路径可达、确定性复现、明确 source→sink（安全问题适用）和具体合同违反的 finding 才是 `BLOCKER`。真实但非阻断的局部问题为 `LOCAL`，P1/P2 hardening、理论变体或无可达证据的推断为 `BACKLOG`；后两者不阻止进入下一 Task。
9. **规模预警**：若实现开始新增计划外 Module，或核心 Module/测试相对计划骨架显著膨胀以响应不断变化的审查样例，主代理必须先检查是否违反本协议；不得用更多抽象、锁状态或正则继续 patch stacking。

## 必须保持的边界

| 边界 | M5 实施要求 |
|---|---|
| Ground Truth 隔离 | `diagnostic_agent.py`、`diagnosis.py`、M3 tools 和 Agent prompt 不得导入、读取或接收 Ground Truth。只有 M2 verifier 与 M5 evaluator/orchestrator 可读。 |
| 冻结评分 | Evaluator 读取诊断结束时的 `DiagnosisRunResult` 和 `LabVerification`；reset 后的数据库状态只作为恢复成功/失败的运行事实，不参与根因或证据评分。 |
| 确定性判据 | 不使用 LLM judge、语义相似度或模型 confidence 作为主要判据。所有 check 均由类型化值和精确集合比较得出。 |
| 样本分母 | 只要 M4 已返回 `DiagnosisRunResult`，`CONFIRMED`、`INSUFFICIENT_EVIDENCE`、`MODEL_ERROR` 与评分失败均写齐六文件并计入真实三次分母。 |
| 只读 Agent | Agent 仍只有 M3 的四个工具；M5 编排的 reset/inject 是管理平面行为，不注册给 Agent。 |
| 轨迹边界 | 只保存工具名、脱敏参数、evidence ID、错误码、耗时、gate、usage 和终态；不保存 prompt completion、隐藏推理或原始 provider 异常。 |
| 失败语义 | `evaluation.status=PASSED` 只表示全部确定性 check 通过且恢复成功。环境在诊断前失败不得伪造 `Diagnosis`；它是 CLI/workflow 错误，不是模型样本。 |
| 模型边界 | 保持 OpenAI-compatible `gemma4:e4b`；只补齐已批准的通用根因 ontology，不添加案例答案、Ollama 私有生成调用、regex/JSON repair、模型专属重试或 fallback。 |
| 安全威胁模型 | 只保证秘密不进入数据流、结构化边界和冻结高置信哨兵；自由文本是需转义展示的不可信内容，不承诺完备语义分类。 |
| 并发模型 | 不同 run ID 可并发；相同 run ID 竞争必须至多一个发布成功且不覆盖。P0 不实现全局锁、租约、PID/token/stale-lock 恢复。 |
| 审查收敛 | finding 必须分为 BLOCKER/LOCAL/BACKLOG/DECISION；每 Task 最多一次广审、一次修复、一次定向复审，达到预算后必须停下来决策。 |

## 文件结构和 Module 责任

| 路径 | 责任 |
|---|---|
| `src/data_incident_gym/evaluation.py` | 严格评测合同与纯确定性 `DeterministicEvaluator` |
| `src/data_incident_gym/artifacts.py` | 六文件 schema、固定路径原子 writer、Jinja2 报告和写后回读校验 |
| `src/data_incident_gym/templates/report.md.j2` | 只消费结构化值的中文 Markdown 模板 |
| `src/data_incident_gym/evaluation_runner.py` | 一次独立实验的管理平面编排、finally 恢复和结果交接 |
| `src/data_incident_gym/doctor.py` | 只读环境/依赖/模型最小能力检查，返回类型化检查表 |
| `src/data_incident_gym/diagnosis.py` | 保留 M4 诊断合同，只为可观测性补充每次工具耗时 |
| `src/data_incident_gym/diagnostic_agent.py` | 在原 Controller 内采集工具耗时，公开版本化 prompt hash；不读 Ground Truth |
| `src/data_incident_gym/cli.py` | 新增有界的 `eval run CASE_ID` 和 `doctor`，保留既有 pipeline/lab/diagnose 语义 |
| `tests/unit/test_evaluation.py` | 逐项判据、严格 schema、反例和 Ground Truth 隔离 |
| `tests/unit/test_artifacts.py` | 六文件、原子写、回读、脱敏、模板与重复目录拒绝 |
| `tests/unit/test_evaluation_runner.py` | 编排顺序、失败分母、finally reset、不伪造诊断 |
| `tests/unit/test_doctor.py` | 全部检查 Adapter 的成功/失败与脱敏建议 |
| `tests/integration/test_evaluation_runner.py` | 真实 M2/M3 + FunctionModel + M5 evaluator/artifacts 单次闭环 |
| `tests/e2e/test_ollama_evaluation.py` | opt-in 的三次真实 `gemma4:e4b` 样本和 2/3 验收 |

---

### Task 1: 锁定确定性 Evaluator 合同与六文件需求基线

**Files:**

- Modify: `docs/requirements.md:135-140`
- Modify: `docs/requirements.md:423-446`
- Modify: `docs/requirements.md:559-566`
- Add without further content changes: `docs/superpowers/plans/2026-08-26-m5-evaluation-report.md`
- Create: `src/data_incident_gym/evaluation.py`
- Create: `tests/unit/test_evaluation.py`
- Verify without modification: `src/data_incident_gym/diagnosis.py`
- Verify without modification: `src/data_incident_gym/evidence.py`
- Verify without modification: `src/data_incident_gym/incidents.py`
- Verify without modification: `src/data_incident_gym/lab_verifier.py`

- [ ] **Step 1: 确认需求 diff 只是用户批准的诊断 profile 隔离与六文件合同**

```powershell
git diff -- docs/requirements.md
rg -n 'config/dbt/diagnostic/profiles.yml|DIG_DIAGNOSTIC_POSTGRES_|evidence.json|evaluation.json|冻结的 `DiagnosisRunResult`|六个产物' docs/requirements.md
```

Expected: 相对 `96ad13c` 的 diff 精确为三处：数据库权限章节增加独立 diagnostic dbt profile 且禁止回退管理 profile；第 13 节增加六文件与冻结评分；P0 checklist 改为六文件。没有其他需求漂移，也没有改动 P1–P3 范围。

- [ ] **Step 2: 先写严格合同和评分反例测试**

`tests/unit/test_evaluation.py` 用合成 EvidenceRecord 构造一个唯一通过样本，并以显式 `model_copy(update={"field": value})` 每次只破坏一项。核心测试结构固定为：

```python
CHECK_ORDER = (
    "ENVIRONMENT_VERIFIED",
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


def test_exact_grounded_diagnosis_passes_all_checks(valid_inputs) -> None:
    truth, verification, diagnosis_run = valid_inputs

    result = DeterministicEvaluator.evaluate(
        truth,
        verification,
        diagnosis_run,
        recovery_succeeded=True,
    )

    assert result.status == EvaluationStatus.PASSED
    assert tuple(check.code.value for check in result.checks) == CHECK_ORDER
    assert all(check.passed for check in result.checks)
    assert result.failed_check_codes == ()


@pytest.mark.parametrize(
    ("mutation", "failed_code"),
    [
        ("non_confirmed", "DIAGNOSIS_CONFIRMED"),
        ("wrong_root", "ROOT_CAUSE_EXACT"),
        ("missing_asset", "AFFECTED_ASSETS_EXACT"),
        ("extra_asset", "AFFECTED_ASSETS_EXACT"),
        ("invented_evidence", "EVIDENCE_IDS_EXIST"),
        ("cross_run_record", "EVIDENCE_RUN_SCOPE"),
        ("missing_schema_type", "REQUIRED_EVIDENCE_TYPES_PRESENT"),
        ("wrong_schema_subject", "EVIDENCE_CONTENT_COMPATIBLE"),
        ("contradictory_cited_schema", "EVIDENCE_CONTENT_COMPATIBLE"),
        ("wrong_lineage", "EVIDENCE_CONTENT_COMPATIBLE"),
        ("write_shaped_tool", "TRACE_READ_ONLY_SAFE"),
        ("inventory_not_referenced_by_trace", "TRACE_READ_ONLY_SAFE"),
        ("recovery_failed", "RECOVERY_HEALTHY"),
    ],
)
def test_each_gate_fails_closed(valid_inputs, mutation: str, failed_code: str) -> None:
    truth, verification, diagnosis_run, recovery = mutate(valid_inputs, mutation)
    result = DeterministicEvaluator.evaluate(
        truth,
        verification,
        diagnosis_run,
        recovery_succeeded=recovery,
    )

    assert result.status == EvaluationStatus.FAILED
    assert failed_code in {code.value for code in result.failed_check_codes}


def test_confidence_is_recorded_but_does_not_change_score(valid_inputs) -> None:
    truth, verification, diagnosis_run = valid_inputs
    low = with_confidence(diagnosis_run, 0.01)
    high = with_confidence(diagnosis_run, 0.99)

    assert DeterministicEvaluator.evaluate(
        truth, verification, low, recovery_succeeded=True
    ).status == EvaluationStatus.PASSED
    assert DeterministicEvaluator.evaluate(
        truth, verification, high, recovery_succeeded=True
    ).status == EvaluationStatus.PASSED
```

这里的测试构造器不是待实现占位符，必须在同一测试文件内按以下固定职责实现：`valid_inputs` 加载仓库中唯一的 Ground Truth，并用固定 `RUN_ID` 构造匹配的 `LabVerification`、`CONFIRMED` Diagnosis、三条 cited `EvidenceRecord`（direct-failure node error、fault relation schema、从 direct failure 出发的 downstream lineage）、对应四工具 trace 和 `DiagnosisMetrics`；`mutate` 只能用 `model_copy(update=...)` 完成参数表所命名的单一破坏，并返回该 case 的 recovery bool；`with_confidence` 只能复制 Diagnosis 的 confidence 后重组 `DiagnosisRunResult`，其余字段逐值相等。所有 EvidenceRecord 使用仓库现有 `EvidenceRecord.create(...)` 生成 ID/digest，禁止测试自行伪造通过规则。

同文件还必须测试：`EvaluationCheck`/`EvaluationResult` frozen + `extra="forbid"`；bool/int/string 不隐式 coercion；check 不得缺失、重复或乱序；`status` 必须与 `failed_check_codes` 一致；期望/实际值用排序后 tuple 保证跨平台稳定。

- [ ] **Step 3: 运行 RED，确认失败原因是 Module 尚不存在**

```powershell
uv run pytest tests/unit/test_evaluation.py -q
```

Expected: collection 失败，精确指向 `data_incident_gym.evaluation` 不存在；不得是 fixture 语法、Pydantic 构造或旧测试失败。

- [ ] **Step 4: 实现严格评测类型和纯函数 Evaluator**

`src/data_incident_gym/evaluation.py` 不读文件、数据库、环境变量或模型，对相同输入必须返回字节级稳定的值。公开 Interface 精确为：

```python
class EvaluationStatus(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"


class EvaluationCheckCode(StrEnum):
    ENVIRONMENT_VERIFIED = "ENVIRONMENT_VERIFIED"
    DIAGNOSIS_CONFIRMED = "DIAGNOSIS_CONFIRMED"
    ROOT_CAUSE_EXACT = "ROOT_CAUSE_EXACT"
    AFFECTED_ASSETS_EXACT = "AFFECTED_ASSETS_EXACT"
    EVIDENCE_IDS_EXIST = "EVIDENCE_IDS_EXIST"
    EVIDENCE_RUN_SCOPE = "EVIDENCE_RUN_SCOPE"
    REQUIRED_EVIDENCE_TYPES_PRESENT = "REQUIRED_EVIDENCE_TYPES_PRESENT"
    EVIDENCE_CONTENT_COMPATIBLE = "EVIDENCE_CONTENT_COMPATIBLE"
    TRACE_READ_ONLY_SAFE = "TRACE_READ_ONLY_SAFE"
    RECOVERY_HEALTHY = "RECOVERY_HEALTHY"


class EvaluationCheck(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: EvaluationCheckCode
    passed: StrictBool
    expected: tuple[StrictStr, ...]
    actual: tuple[StrictStr, ...]
    reason_code: StrictStr


class EvaluationResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["m5.evaluation.v1"]
    incident_case_id: CaseId
    run_id: RunId
    status: EvaluationStatus
    checks: tuple[EvaluationCheck, ...]
    failed_check_codes: tuple[EvaluationCheckCode, ...]

    @model_validator(mode="after")
    def validate_complete_check_set(self) -> Self:
        expected = tuple(EvaluationCheckCode)
        actual = tuple(check.code for check in self.checks)
        if actual != expected:
            raise ValueError("checks must contain every code exactly once in canonical order")
        for check in self.checks:
            suffix = "PASSED" if check.passed else "FAILED"
            if check.reason_code != f"{check.code.value}_{suffix}":
                raise ValueError("check reason_code must match result")
        failed = tuple(check.code for check in self.checks if not check.passed)
        if self.failed_check_codes != failed:
            raise ValueError("failed_check_codes must match checks")
        expected_status = EvaluationStatus.PASSED if not failed else EvaluationStatus.FAILED
        if self.status != expected_status:
            raise ValueError("status must match checks")
        return self


ALLOWED_DIAGNOSTIC_TOOLS = frozenset(
    {
        "get_dbt_run_results",
        "get_dbt_node_error",
        "get_relation_schema",
        "get_dbt_lineage",
    }
)
TRACE_FORBIDDEN_PATTERN = re.compile(
    r"(?i)(?:password|passwd|secret|token|api[_-]?key|authorization)\s*[:=]"
    r"|\bbearer\s+"
    r"|\b(?:select|insert|update|delete|alter|create|drop|grant|revoke)\b"
    r"|(?:[a-z]:[\\/]|\\\\|/)[^\s]*"
)


class DeterministicEvaluator:
    @staticmethod
    def evaluate(
        ground_truth: GroundTruth,
        verification: LabVerification,
        diagnosis_run: DiagnosisRunResult,
        *,
        recovery_succeeded: bool,
    ) -> EvaluationResult:
        diagnosis = diagnosis_run.diagnosis
        inventory = {record.evidence_id: record for record in diagnosis_run.evidence_records}
        cited = tuple(
            inventory[evidence_id]
            for evidence_id in diagnosis.evidence_ids
            if evidence_id in inventory
        )
        unknown_citations = tuple(
            evidence_id
            for evidence_id in diagnosis.evidence_ids
            if evidence_id not in inventory
        )
        trace_evidence_ids = tuple(
            evidence_id
            for event in diagnosis_run.trace
            if isinstance(event, ToolTraceEvent)
            for evidence_id in event.evidence_ids
        )

        environment_passed = (
            verification.status == "EXPECTED_FAILURE"
            and verification.incident_case_id == ground_truth.incident_case_id
            and diagnosis.incident_case_id == ground_truth.incident_case_id
            and verification.run_id == diagnosis.run_id
            and verification.failed_nodes == (ground_truth.direct_failure,)
            and tuple(sorted(verification.affected_assets))
            == tuple(sorted(ground_truth.affected_assets))
            and verification.error_category == ground_truth.expected_failure_category
            and verification.ground_truth_digest == ground_truth.digest()
        )
        scope_violations = tuple(
            sorted(
                {
                    record.evidence_id
                    for record in diagnosis_run.evidence_records
                    if record.run_id != diagnosis.run_id
                }
                | {
                    evidence_id
                    for evidence_id in trace_evidence_ids
                    if evidence_id not in inventory
                    or inventory[evidence_id].run_id != diagnosis.run_id
                }
            )
        )
        cited_types = tuple(sorted({record.evidence_type.value for record in cited}))
        required_types = tuple(sorted(ground_truth.required_evidence_types))

        asset_candidates: dict[str, set[str]] = {}
        for record in cited:
            if isinstance(record.content, DbtNodeErrorFact):
                node_id = record.content.node_id
                asset_candidates.setdefault(node_id, set()).add(node_id)
                asset_candidates.setdefault(node_id.rsplit(".", 1)[-1], set()).add(node_id)
            if isinstance(record.content, DbtLineageFact):
                asset_candidates.setdefault(record.content.node_id, set()).add(
                    record.content.node_id
                )
                asset_candidates.setdefault(
                    record.content.node_id.rsplit(".", 1)[-1], set()
                ).add(record.content.node_id)
                for node in record.content.related_nodes:
                    if node.resource_type == "model":
                        asset_candidates.setdefault(node.node_id, set()).add(node.node_id)
                        asset_candidates.setdefault(node.name, set()).add(node.node_id)
        canonical_assets: list[str] = []
        asset_resolution_failures: list[str] = []
        for asset in diagnosis.affected_assets:
            candidates = asset_candidates.get(asset, set())
            if len(candidates) != 1:
                asset_resolution_failures.append(asset)
            else:
                canonical_assets.append(next(iter(candidates)))
        canonical_asset_tuple = tuple(sorted(canonical_assets))
        expected_asset_tuple = tuple(sorted(ground_truth.affected_assets))
        affected_assets_exact = (
            not asset_resolution_failures
            and len(canonical_assets) == len(set(canonical_assets))
            and canonical_asset_tuple == expected_asset_tuple
        )

        expected_columns = tuple(
            (column.name, column.data_type, column.nullable, column.ordinal_position)
            for column in ground_truth.expected_schema.fault_column_metadata
        )
        node_error_records = tuple(
            record for record in cited if isinstance(record.content, DbtNodeErrorFact)
        )
        node_error_ok = bool(node_error_records) and all(
            record.content.node_id == ground_truth.direct_failure
            and record.content.status in {"error", "fail"}
            for record in node_error_records
        )
        schema_records = tuple(
            record for record in cited if isinstance(record.content, RelationSchemaFact)
        )
        schema_ok = bool(schema_records) and all(
            record.content.relation_name == ground_truth.injection.relation
            and tuple(
                (
                    column.name,
                    column.data_type,
                    column.nullable,
                    column.ordinal_position,
                )
                for column in record.content.columns
            )
            == expected_columns
            for record in schema_records
        )
        lineage_records = tuple(
            record for record in cited if isinstance(record.content, DbtLineageFact)
        )
        downstream_assets = {
            ground_truth.direct_failure,
            *(
                node.node_id
                for record in lineage_records
                for node in record.content.related_nodes
                if node.resource_type == "model"
            ),
        }
        lineage_ok = (
            bool(lineage_records)
            and all(
                record.content.node_id == ground_truth.direct_failure
                and record.content.direction == "downstream"
                for record in lineage_records
            )
            and downstream_assets == set(ground_truth.affected_assets)
        )
        compatible_actual = tuple(
            marker
            for marker, passed in (
                ("DBT_NODE_ERROR", node_error_ok),
                ("FAULT_RELATION_SCHEMA", schema_ok),
                ("DOWNSTREAM_MODEL_LINEAGE", lineage_ok),
            )
            if passed
        )

        trace_violations: list[str] = []
        for event in diagnosis_run.trace:
            if isinstance(event, ToolTraceEvent):
                if event.tool_name not in ALLOWED_DIAGNOSTIC_TOOLS:
                    trace_violations.append(f"tool:{event.tool_name}")
                if any(TRACE_FORBIDDEN_PATTERN.search(value) for value in event.arguments.values()):
                    trace_violations.append(f"arguments:{event.fingerprint}")
        inventory_ids = set(inventory)
        trace_inventory_ids = set(trace_evidence_ids)
        if inventory_ids != trace_inventory_ids:
            trace_violations.append(
                f"inventory_only_count:{len(inventory_ids - trace_inventory_ids)}"
            )
            trace_violations.append(
                f"trace_only_count:{len(trace_inventory_ids - inventory_ids)}"
            )
        trace_violations.extend(f"evidence:{value}" for value in scope_violations)
        canonical_trace_violations = tuple(sorted(set(trace_violations)))
        if not diagnosis.evidence_ids:
            citation_actual = ("NO_CITATIONS",)
        elif unknown_citations:
            citation_actual = unknown_citations
        else:
            citation_actual = ("ALL_CITED_IDS_EXIST",)

        def check(
            code: EvaluationCheckCode,
            passed: bool,
            expected: tuple[str, ...],
            actual: tuple[str, ...],
        ) -> EvaluationCheck:
            return EvaluationCheck(
                code=code,
                passed=passed,
                expected=expected,
                actual=actual,
                reason_code=f"{code.value}_{'PASSED' if passed else 'FAILED'}",
            )

        checks = (
            check(
                EvaluationCheckCode.ENVIRONMENT_VERIFIED,
                environment_passed,
                ("EXPECTED_FAILURE", ground_truth.digest()),
                (verification.status, verification.ground_truth_digest),
            ),
            check(
                EvaluationCheckCode.DIAGNOSIS_CONFIRMED,
                diagnosis.status == DiagnosisStatus.CONFIRMED,
                (DiagnosisStatus.CONFIRMED.value,),
                (diagnosis.status.value,),
            ),
            check(
                EvaluationCheckCode.ROOT_CAUSE_EXACT,
                diagnosis.root_cause_code == ground_truth.root_cause_code,
                (ground_truth.root_cause_code,),
                (() if diagnosis.root_cause_code is None else (diagnosis.root_cause_code,)),
            ),
            check(
                EvaluationCheckCode.AFFECTED_ASSETS_EXACT,
                affected_assets_exact,
                expected_asset_tuple,
                (
                    canonical_asset_tuple
                    if not asset_resolution_failures
                    else (f"UNRESOLVED_ASSET_COUNT:{len(asset_resolution_failures)}",)
                ),
            ),
            check(
                EvaluationCheckCode.EVIDENCE_IDS_EXIST,
                bool(diagnosis.evidence_ids) and not unknown_citations,
                ("ALL_CITED_IDS_EXIST",),
                citation_actual,
            ),
            check(
                EvaluationCheckCode.EVIDENCE_RUN_SCOPE,
                not scope_violations,
                (diagnosis.run_id,),
                ((diagnosis.run_id,) if not scope_violations else scope_violations),
            ),
            check(
                EvaluationCheckCode.REQUIRED_EVIDENCE_TYPES_PRESENT,
                set(required_types).issubset(cited_types),
                required_types,
                cited_types,
            ),
            check(
                EvaluationCheckCode.EVIDENCE_CONTENT_COMPATIBLE,
                node_error_ok and schema_ok and lineage_ok,
                ("DBT_NODE_ERROR", "FAULT_RELATION_SCHEMA", "DOWNSTREAM_MODEL_LINEAGE"),
                compatible_actual,
            ),
            check(
                EvaluationCheckCode.TRACE_READ_ONLY_SAFE,
                not canonical_trace_violations,
                ("READ_ONLY_TRACE",),
                (
                    ("READ_ONLY_TRACE",)
                    if not canonical_trace_violations
                    else canonical_trace_violations
                ),
            ),
            check(
                EvaluationCheckCode.RECOVERY_HEALTHY,
                recovery_succeeded,
                ("HEALTHY",),
                (("HEALTHY",) if recovery_succeeded else ("FAILED",)),
            ),
        )
        failed = tuple(item.code for item in checks if not item.passed)
        return EvaluationResult(
            schema_version="m5.evaluation.v1",
            incident_case_id=diagnosis.incident_case_id,
            run_id=diagnosis.run_id,
            status=EvaluationStatus.PASSED if not failed else EvaluationStatus.FAILED,
            checks=checks,
            failed_check_codes=failed,
        )
```

`evaluate` 的实现一次生成全部 10 个 check，不 short-circuit：

1. `ENVIRONMENT_VERIFIED`：`verification.status/case/run/affected_assets/ground_truth_digest` 与 Ground Truth 和 Diagnosis run 精确一致。
2. `DIAGNOSIS_CONFIRMED`：只有 `DiagnosisStatus.CONFIRMED` 通过；其他两个终态保留原始产物但评分失败。
3. `ROOT_CAUSE_EXACT`：大小写敏感的精确字符串相等，不做别名、编辑距离或语义相似。
4. `AFFECTED_ASSETS_EXACT`：M4 允许 Diagnosis 使用 dbt unique_id 或模型名。Evaluator 只能用已 cited 的 node-error/lineage records 建立 `name/unique_id → canonical node_id` 映射；每个输出值必须唯一解析，否则失败。解析后对 canonical unique_id 集合精确比较，既不漏报也不过报；这不是别名或模糊匹配。
5. `EVIDENCE_IDS_EXIST`：Diagnosis 引用的每个 ID 都在冻结 inventory 中，且至少引用一条。
6. `EVIDENCE_RUN_SCOPE`：inventory、引用和 trace 中的 evidence IDs 全部属于同一 run，不允许跨 run 事实。
7. `REQUIRED_EVIDENCE_TYPES_PRESENT`：只在 cited records 中检查 Ground Truth 规定的三类证据，未引用的 inventory 不能替 Agent 补分。
8. `EVIDENCE_CONTENT_COMPATIBLE`：P0 只实现当前案例的最小类型化兼容规则：每条 cited DBT node error 都指向 `direct_failure` 且为 error/fail；每条 cited Schema 证据都指向 injection relation 且列元数据精确等于 fault schema；每条 cited lineage 都是从 `direct_failure` 出发的 downstream 查询，所有 lineage model node 与直接失败点合并后精确等于 affected assets。任一同类型 cited 反证都会 fail closed；不建通用 claim matrix。
9. `TRACE_READ_ONLY_SAFE`：`TOOL_CALL` 名称必须属于现有四工具 allowlist，trace evidence ID 必须存在且其集合与冻结 inventory 精确相等，事件 schema 不得包含 prompt/completion/reasoning 字段。
10. `RECOVERY_HEALTHY`：只记录编排器 finally reset 的成功事实；它影响“完整通过”，但不改写前 9 项 Agent 评分。

上述 `ALLOWED_DIAGNOSTIC_TOOLS` 与 M3 四工具注册名逐字一致；`TRACE_FORBIDDEN_PATTERN` 只应用于四个类型化工具的 trace 参数，并只识别冻结语料中的未脱敏 credential label/Bearer、SQL 动词和 Windows/UNC/POSIX 绝对路径。该 pattern 只产生 fingerprint 级违规码，不把原参数复制到 `evaluation.json`。实现文件必须显式 import `re`，测试必须逐一覆盖四个允许工具、一个未知工具、每类冻结禁止模式和正常 relation/node/run 参数。不得把它扩展成 quote-aware SQL parser、DSN grammar、自然语言 instruction/answer 分类器或任意编码检测器。

- [ ] **Step 5: GREEN、静态边界和提交**

```powershell
uv run pytest tests/unit/test_evaluation.py tests/unit/test_diagnosis.py tests/unit/test_evidence.py -q
uv run ruff check src/data_incident_gym/evaluation.py tests/unit/test_evaluation.py
rg -n 'OpenAI|Agent\(|OpenAIChatModel|ModelRequest|psycopg|subprocess|urlopen|resolve_active_run' src/data_incident_gym/evaluation.py
git diff --check -- docs/requirements.md docs/superpowers/plans/2026-08-26-m5-evaluation-report.md src/data_incident_gym/evaluation.py tests/unit/test_evaluation.py
git add docs/requirements.md docs/superpowers/plans/2026-08-26-m5-evaluation-report.md src/data_incident_gym/evaluation.py tests/unit/test_evaluation.py
git diff --cached --check
git diff --cached --name-only
git commit -m "feat: add deterministic M5 evaluator"
```

Expected: tests/Ruff/diff exit 0；`rg` 无输出；cached list 精确为 requirements、M5 plan、evaluator 和其 unit test 四个路径，不含根目录 Markdown；commit 成功。

- [ ] **Step 6: 对 Task 1 做一次独立广度审查并按冻结预算收敛**

审查必须从 Task 1 的父提交到当前 HEAD 检查：六文件需求是否与用户决策一致；所有 check 是否只读冻结输入；是否把未 cited 证据错当成支持；是否泄露 Ground Truth 给 Agent；集合、排序、reason code 和 status 是否确定；是否偷做 P1 通用 claim matrix。审查只能使用合成哨兵值，不读取任何真实凭据。

Expected: reviewer 给出 `PASS`，或把带文件/行号/复现命令的 finding 明确分类为 `BLOCKER`、`LOCAL`、`BACKLOG`、`DECISION`。没有 BLOCKER/DECISION 即可进入 Task 2；若有 BLOCKER，只允许一次最小修复和一次针对原 finding 清单的定向复审。达到预算后仍未关闭则停止并请用户决策，不更换 reviewer 扩大审查面。

---

### Task 2: 补齐版本化诊断 ontology，并实现六文件原子 ArtifactWriter 和确定性中文报告

**Files:**

- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `src/data_incident_gym/diagnosis.py`
- Modify: `src/data_incident_gym/diagnostic_agent.py`
- Modify: `tests/unit/test_diagnosis.py`
- Modify: `tests/unit/test_diagnostic_agent.py`
- Create: `src/data_incident_gym/artifacts.py`
- Create: `src/data_incident_gym/templates/report.md.j2`
- Create: `tests/unit/test_artifacts.py`
- Verify without modification: `.gitignore`

- [ ] **Step 1: 写 trace 耗时、prompt hash 和六文件 RED 测试**

`tests/unit/test_diagnosis.py` 和 `tests/unit/test_diagnostic_agent.py` 先把 `ToolTraceEvent` 的新合同锁定为：

```python
event = ToolTraceEvent(
    event_type="TOOL_CALL",
    tool_name="get_dbt_run_results",
    arguments={"run_id": "a" * 32},
    fingerprint="b" * 64,
    evidence_ids=(),
    error_code=None,
    elapsed_ms=0,
)
assert event.elapsed_ms == 0

with pytest.raises(ValidationError):
    ToolTraceEvent.model_validate({**event.model_dump(), "elapsed_ms": -1})
with pytest.raises(ValidationError):
    ToolTraceEvent.model_validate({**event.model_dump(), "elapsed_ms": 1.0})

assert SYSTEM_PROMPT_VERSION == "m5.diagnosis.v1"
assert SYSTEM_PROMPT_SHA256 == hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()
assert "SOURCE_SCHEMA_COLUMN_RENAMED" in SYSTEM_PROMPT
assert "source relation column was renamed" in SYSTEM_PROMPT
for case_specific_value in (
    "schema_rename_payment_amount",
    "raw_payments",
    "stg_payments",
    "orders",
    "customers",
    "amount",
    "total_amount",
):
    assert case_specific_value not in SYSTEM_PROMPT
```

`tests/unit/test_artifacts.py` 用 `tmp_path`、合成哨兵 `TEST_REDACTED_VALUE` 和 Task 1 的 valid result 覆盖：

```python
EXPECTED_FILES = {
    "metadata.json",
    "trace.jsonl",
    "evidence.json",
    "diagnosis.json",
    "evaluation.json",
    "report.md",
}


def test_writer_publishes_exactly_six_round_trippable_files(tmp_path, artifact_run) -> None:
    writer = ArtifactWriter(
        tmp_path,
        run_command=fake_clean_git("1" * 40),
    )

    output = writer.write(artifact_run)

    assert output == tmp_path / "artifacts" / artifact_run.run_id
    assert {path.name for path in output.iterdir()} == EXPECTED_FILES
    assert RunMetadata.model_validate_json(read(output / "metadata.json")).run_id == (
        artifact_run.run_id
    )
    assert EvidenceArtifact.model_validate_json(read(output / "evidence.json")).records == (
        artifact_run.diagnosis_run.evidence_records
    )
    assert Diagnosis.model_validate_json(read(output / "diagnosis.json")) == (
        artifact_run.diagnosis_run.diagnosis
    )
    assert EvaluationResult.model_validate_json(read(output / "evaluation.json")) == (
        artifact_run.evaluation
    )
    assert [TraceEnvelope.model_validate_json(line).sequence for line in lines(output)] == list(
        range(1, len(artifact_run.diagnosis_run.trace) + 1)
    )


def test_failed_evaluation_is_persisted_without_being_filtered(
    tmp_path, failed_artifact_run
) -> None:
    output = ArtifactWriter(tmp_path, run_command=fake_clean_git("2" * 40)).write(
        failed_artifact_run
    )

    stored = EvaluationResult.model_validate_json(read(output / "evaluation.json"))
    assert stored.status == EvaluationStatus.FAILED
    assert {path.name for path in output.iterdir()} == EXPECTED_FILES
```

这里的 helper/fixture 必须在 `tests/unit/test_artifacts.py` 内落地：`read(path)` 固定执行 `path.read_text(encoding="utf-8")`；`lines(output)` 读取 `trace.jsonl` 后返回非空 `splitlines()`；`fake_clean_git(revision)` 只接受 writer 规定的两个 Git argv，并分别返回该 revision 与空 porcelain stdout；`artifact_run` 用 Task 1 的通过样本、固定 UTC 起止时间、`RecoveryStatus.HEALTHY` 和固定安全模型配置构造；`failed_artifact_run` 只把 evaluator 结果及 recovery status 改为失败。禁止 monkeypatch 全局 `subprocess.run` 或用真实 Git 状态使断言依赖当前工作树。

同文件显式实现并命名以下测试：

```text
test_trace_jsonl_preserves_order_duration_errors_and_evidence_references
test_metadata_contains_revision_dirty_flag_safe_config_prompt_hash_and_metrics
test_report_is_deterministic_chinese_and_contains_every_failed_check
test_writer_refuses_existing_run_directory_instead_of_overwriting
test_writer_rejects_artifact_root_or_target_symlink
test_validation_failure_never_publishes_partial_final_directory
test_concurrent_different_run_ids_publish_independently
test_competing_same_run_id_has_at_most_one_success_and_keeps_one_valid_bundle
test_pre_redacted_trace_and_error_sentinels_never_reappear_in_non_diagnosis_surfaces
test_report_escapes_diagnosis_text_as_inert_preformatted_content
test_template_uses_strict_undefined_and_is_available_through_importlib_resources
```

安全哨兵测试只把哨兵放入本合同禁止持久化的 trace/error/provider 输入，并检查 metadata、trace、evaluation 和 report 的对应非 Diagnosis surface；不得把哨兵塞入合法的 Diagnosis summary/actions 后再要求 writer 猜测语义并删除。报告转义测试使用含 HTML 标签、Markdown link 和竖线的合成文本，证明它们只作为转义后的 `<pre>` 文本显示，不形成可执行 HTML、链接或表格结构。

- [ ] **Step 2: 运行 RED**

```powershell
uv run pytest tests/unit/test_diagnosis.py tests/unit/test_diagnostic_agent.py tests/unit/test_artifacts.py -q
```

Expected: `elapsed_ms`、版本化 ontology/prompt hash 和 `data_incident_gym.artifacts` 缺失导致失败；旧 M4 诊断语义测试仍可 collection。

- [ ] **Step 3: 把已锁定的 Jinja2 3.1.6 升格为直接依赖**

Jinja2 3.1.6 已存在当前 `uv.lock`（由既有依赖引入），报告 Module 直接 import 它时必须在 `pyproject.toml` 明示声明：

```powershell
uv add "jinja2==3.1.6"
uv lock --check
git diff -- pyproject.toml uv.lock
```

Expected: `pyproject.toml` 新增精确直接依赖；`uv.lock` 只更新根 project 的 dependency metadata，不升级、新增或删除任何 resolved package。若 `uv` 试图改变 Jinja2 或其他版本，立即停止并审查命令/锁文件，不接受顺带升级。

- [ ] **Step 4: 在 M4 Controller 内采集工具耗时并补齐版本化 ontology/hash**

`src/data_incident_gym/diagnosis.py` 对现有 event 只添加一个必填字段：

```python
class ToolTraceEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_type: Literal["TOOL_CALL"]
    tool_name: NonBlankStr
    arguments: dict[NonBlankStr, NonBlankStr]
    fingerprint: Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
    evidence_ids: tuple[EvidenceId, ...]
    error_code: NonBlankStr | None = None
    elapsed_ms: Annotated[StrictInt, Field(ge=0)]
```

`src/data_incident_gym/diagnostic_agent.py` 保持原调查规则，只增加 M4 计划已批准但当前实现缺失的通用 ontology 语义；完整 prompt 必须精确为：

```python
SYSTEM_PROMPT = """
You diagnose one data incident using only the four registered read-only evidence tools.
Choose evidence based on the facts returned by tools; do not infer a root cause from the
incident identifier. Do not repeat an identical tool call. Return the required Diagnosis
object and cite only evidence IDs returned by tools. If evidence is insufficient, return
INSUFFICIENT_EVIDENCE without guessing.

Use the versioned root-cause ontology below only when compatible evidence supports it:
- SOURCE_SCHEMA_COLUMN_RENAMED: a source relation column was renamed while a dbt consumer
  still references the former column.
""".strip()

SYSTEM_PROMPT_VERSION = "m5.diagnosis.v1"
SYSTEM_PROMPT_SHA256 = hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()


def _elapsed_ms(started_at: float) -> int:
    return max(0, int((monotonic() - started_at) * 1000))
```

该 ontology 只定义跨案例可复用的 code 语义，不声明当前案例命中该 code；不得出现 case ID、`raw_payments`、`stg_payments`、`amount`/`total_amount`、affected-assets 列表或 Ground Truth digest。未来 P1 新故障类型通过版本化 taxonomy 扩展，不用自然语言相似度替代 code。

`execute` helper 入口立即记录 `tool_started_at = monotonic()`，并在 tool limit、duplicate、typed tool error、controller invariant、unknown error 和 success 的每个 `_record_trace` 调用中传入 `elapsed_ms=_elapsed_ms(tool_started_at)`。`_record_trace` 本身必须要求 `elapsed_ms: int`，禁止用默认值掩盖漏记分支。这一步除上述 ontology 合同补齐外，不改工具顺序、去重、预算、gate 或 Diagnosis 内容。

- [ ] **Step 5: 实现严格 artifact schema、原子 writer 和报告模板**

`src/data_incident_gym/artifacts.py` 的公开输入/输出合同固定为：

```python
ARTIFACT_FILENAMES = (
    "metadata.json",
    "trace.jsonl",
    "evidence.json",
    "diagnosis.json",
    "evaluation.json",
    "report.md",
)


def _strict_aware_datetime(value: object) -> datetime:
    if type(value) is not datetime:
        raise ValueError("timestamp must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value


class RecoveryStatus(StrEnum):
    HEALTHY = "HEALTHY"
    FAILED = "FAILED"


class ArtifactWriteError(RuntimeError):
    code = "ARTIFACT_WRITE_FAILED"

    def __init__(self) -> None:
        super().__init__(self.code)
        self.__cause__ = None
        self.__context__ = None


class BudgetSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    model_request_limit: Literal[6]
    tool_call_limit: Literal[8]
    output_retry_limit: Literal[2]
    timeout_seconds: Literal[180]


class TraceEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["m5.trace.v1"]
    sequence: Annotated[StrictInt, Field(ge=1)]
    event: TraceEvent


class EvidenceArtifact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["m5.evidence.v1"]
    incident_case_id: CaseId
    run_id: RunId
    records: tuple[EvidenceRecord, ...]

    @model_validator(mode="after")
    def validate_record_scope(self) -> Self:
        if any(record.run_id != self.run_id for record in self.records):
            raise ValueError("evidence records must match artifact run")
        return self


class RunMetadata(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["m5.metadata.v1"]
    incident_case_id: CaseId
    run_id: RunId
    code_revision: Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{40}$")]
    workspace_dirty: StrictBool
    provider: StrictStr
    model: StrictStr
    model_base_url: StrictStr
    budget: BudgetSummary
    prompt_version: Literal["m5.diagnosis.v1"]
    prompt_sha256: Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
    started_at: Annotated[datetime, BeforeValidator(_strict_aware_datetime)]
    finished_at: Annotated[datetime, BeforeValidator(_strict_aware_datetime)]
    elapsed_ms: Annotated[StrictInt, Field(ge=0)]
    diagnosis_metrics: DiagnosisMetrics
    evaluation_status: EvaluationStatus
    recovery_status: RecoveryStatus
    artifact_files: tuple[StrictStr, ...]

    @model_validator(mode="after")
    def validate_metadata_contract(self) -> Self:
        if self.finished_at < self.started_at:
            raise ValueError("finished_at must not precede started_at")
        if self.artifact_files != ARTIFACT_FILENAMES:
            raise ValueError("artifact_files must match the canonical six files")
        expected_elapsed = int((self.finished_at - self.started_at).total_seconds() * 1000)
        if self.elapsed_ms != expected_elapsed:
            raise ValueError("elapsed_ms must match timestamps")
        return self


class ArtifactRun(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    incident_case_id: CaseId
    run_id: RunId
    started_at: Annotated[datetime, BeforeValidator(_strict_aware_datetime)]
    finished_at: Annotated[datetime, BeforeValidator(_strict_aware_datetime)]
    recovery_status: RecoveryStatus
    model_base_url: StrictStr
    diagnosis_run: DiagnosisRunResult
    evaluation: EvaluationResult

    @model_validator(mode="after")
    def validate_cross_file_identity(self) -> Self:
        diagnosis = self.diagnosis_run.diagnosis
        parsed_base_url = urlsplit(self.model_base_url)
        if (
            parsed_base_url.scheme not in {"http", "https"}
            or not parsed_base_url.hostname
            or parsed_base_url.username is not None
            or parsed_base_url.password is not None
            or parsed_base_url.query
            or parsed_base_url.fragment
        ):
            raise ValueError("model_base_url must be a safe HTTP(S) URL")
        if self.finished_at < self.started_at:
            raise ValueError("finished_at must not precede started_at")
        if diagnosis.incident_case_id != self.incident_case_id:
            raise ValueError("diagnosis case must match artifact run")
        if diagnosis.run_id != self.run_id:
            raise ValueError("diagnosis run_id must match artifact run")
        if self.evaluation.incident_case_id != self.incident_case_id:
            raise ValueError("evaluation case must match artifact run")
        if self.evaluation.run_id != self.run_id:
            raise ValueError("evaluation run_id must match artifact run")
        recovery_check = next(
            check
            for check in self.evaluation.checks
            if check.code == EvaluationCheckCode.RECOVERY_HEALTHY
        )
        if recovery_check.passed != (self.recovery_status == RecoveryStatus.HEALTHY):
            raise ValueError("recovery status must match evaluation")
        return self


class ArtifactWriter:
    def __init__(
        self,
        project_root: Path = PROJECT_ROOT,
        *,
        run_command: RunCommand = subprocess.run,
    ) -> None:
        self._project_root = project_root
        self._run_command = run_command

    def write(self, run: ArtifactRun) -> Path:
        artifact_root = self._validated_artifact_root()
        final = artifact_root / run.run_id
        temporary = artifact_root / f".{run.run_id}.{uuid4().hex}.tmp"
        self._reject_existing_or_symlink(final)
        temporary.mkdir(exist_ok=False)
        try:
            payloads = self._build_payloads(run)
            self._write_payloads(temporary, payloads)
            self._validate_complete_bundle(temporary, run)
            temporary.rename(final)
        except Exception:
            self._remove_owned_temporary_directory(artifact_root, temporary)
            raise ArtifactWriteError() from None
        return final
```

实现文件显式 `from uuid import uuid4`。唯一临时目录避免不同 writer 争用固定 `.<run_id>.tmp`；`rename` 不使用会表达“替换”的 `replace`。对同一 run ID 的竞争，至多一个完整的非空目录成为 final，其余 writer 必须返回 `ARTIFACT_WRITE_FAILED`，且不得改变已发布六文件；不同 run ID 必须互不阻塞。该合同不要求全局锁、PID/token owner、lease、stale-lock 回收或非协作外部进程对 artifact root 的恶意并发篡改。

`RunMetadata` 严格包含：`schema_version="m5.metadata.v1"`、case/run、Git `code_revision`、`workspace_dirty`、provider/model/base URL（不含 API key 或任何数据库密码）、固定 6/8/2/180 预算摘要、`SYSTEM_PROMPT_VERSION`、`SYSTEM_PROMPT_SHA256`、UTC start/end/elapsed、完整 `DiagnosisMetrics`、evaluation status、recovery status 和六文件名列表。Git 只执行固定 `git -C <root> rev-parse HEAD` 和 `git -C <root> status --porcelain`；只保存 hash 和 bool，不把 dirty 路径写入产物。

`_build_payloads` 精确生成：

```python
payloads = {
    "metadata.json": metadata.model_dump_json(indent=2) + "\n",
    "trace.jsonl": "".join(
        TraceEnvelope(
            schema_version="m5.trace.v1",
            sequence=index,
            event=event,
        ).model_dump_json() + "\n"
        for index, event in enumerate(run.diagnosis_run.trace, start=1)
    ),
    "evidence.json": EvidenceArtifact(
        schema_version="m5.evidence.v1",
        incident_case_id=run.incident_case_id,
        run_id=run.run_id,
        records=run.diagnosis_run.evidence_records,
    ).model_dump_json(indent=2) + "\n",
    "diagnosis.json": run.diagnosis_run.diagnosis.model_dump_json(indent=2) + "\n",
    "evaluation.json": run.evaluation.model_dump_json(indent=2) + "\n",
    "report.md": self._render_report(run, metadata),
}
```

`_validate_complete_bundle` 必须检查文件集精确相等、UTF-8、JSON duplicate key 拒绝、五个结构化文件/JSONL 逐行 Pydantic 回读、case/run 交叉一致和报告末尾单一换行。仅当全部通过才把临时目录 rename 成最终目录；目标存在或为 symlink 时 fail closed，不覆盖历史 run。清理只能针对本次 writer 创建、已 resolve 且直接位于已 resolve artifact root 内的 `.<run_id>.<uuid>.tmp`，禁止对不明路径递归删除。普通异常应 best-effort 清理自己的临时目录；进程被强制终止后遗留的唯一临时目录不构成锁，也不得阻塞其他 run，P0 不实现自动 stale-temp 回收。

`src/data_incident_gym/templates/report.md.j2` 使用 `Environment(undefined=StrictUndefined, autoescape=True)`，精确包含：案例/run/模型/代码版本、总判定、10 项 check 表、Diagnosis 完整字段、cited evidence 的 ID/type/source/subject 表、usage/耗时、恢复状态和固定边界声明“单案例只证明 P0 工程闭环”。报告不插入 Ground Truth 原文、prompt、completion、隐藏推理、原始工具日志或 provider 异常；Diagnosis 本身的 summary/actions 是不参与评分的模型自由文本，模板必须把它们放进静态 `<pre>` 元素并由 Jinja HTML-escape 后展示，不能作为 Markdown/HTML/链接/指令解释。`diagnosis.json` 仍保持原始类型化输出，writer 不静默改写它。P0 不对该自由文本做 SQL/DSN/凭据/指令语义分类；如果产品要求其内容具有更强语义保证，必须先经用户 `DECISION` 改为枚举/模板字段。

- [ ] **Step 6: GREEN、wheel 资源验证、提交和独立审查**

```powershell
uv run pytest tests/unit/test_diagnosis.py tests/unit/test_diagnostic_agent.py tests/unit/test_artifacts.py -q
uv run ruff check src/data_incident_gym/diagnosis.py src/data_incident_gym/diagnostic_agent.py src/data_incident_gym/artifacts.py tests/unit/test_diagnosis.py tests/unit/test_diagnostic_agent.py tests/unit/test_artifacts.py
uv lock --check
uv build --out-dir .dig/build/m5
$wheel = Get-ChildItem -LiteralPath '.dig/build/m5' -Filter '*.whl' |
    Sort-Object LastWriteTimeUtc -Descending |
    Select-Object -First 1
uv run python -c "import sys, zipfile; z = zipfile.ZipFile(sys.argv[1]); assert 'data_incident_gym/templates/report.md.j2' in z.namelist()" $wheel.FullName
git diff --check -- pyproject.toml uv.lock src/data_incident_gym/diagnosis.py src/data_incident_gym/diagnostic_agent.py src/data_incident_gym/artifacts.py src/data_incident_gym/templates/report.md.j2 tests/unit/test_diagnosis.py tests/unit/test_diagnostic_agent.py tests/unit/test_artifacts.py
git add pyproject.toml uv.lock src/data_incident_gym/diagnosis.py src/data_incident_gym/diagnostic_agent.py src/data_incident_gym/artifacts.py src/data_incident_gym/templates/report.md.j2 tests/unit/test_diagnosis.py tests/unit/test_diagnostic_agent.py tests/unit/test_artifacts.py
git diff --cached --check
git diff --cached --name-only
git commit -m "feat: persist auditable M5 artifacts"
```

Expected: tests/Ruff/lock/build/resource/diff exit 0；wheel 只写入已 ignored 的 `.dig/build/m5/`，并真实包含 Jinja2 模板；cached list 只含列出的 Task 2 路径。随后做一次独立广度审查：ontology 只含通用语义且无案例答案；原子性和本节冻结并发模型；symlink/path containment；六文件完整性、JSONL 可回读、有限脱敏、时间、prompt hash、Jinja2 `StrictUndefined`、依赖 diff 和提交范围。finding 必须按总协议分类；BLOCKER 最多一次修复和一次原清单定向复审，不因新假想编码/锁故障逐轮扩大实现。

---

### Task 3: 实现单样本 EvaluationRunner 和 `eval run` 一键入口

**Files:**

- Create: `src/data_incident_gym/evaluation_runner.py`
- Create: `tests/unit/test_evaluation_runner.py`
- Create: `tests/integration/test_evaluation_runner.py`
- Modify: `src/data_incident_gym/cli.py`
- Modify: `tests/unit/test_cli.py`
- Verify without modification: `src/data_incident_gym/lab.py`
- Verify without modification: `src/data_incident_gym/diagnostic_agent.py`
- Verify without modification: `src/data_incident_gym/incidents.py`

- [ ] **Step 1: 写编排顺序、恢复和失败样本 RED 测试**

`tests/unit/test_evaluation_runner.py` 使用手写的窄 Adapter（FakeLab、Diagnosis factory、clock、Ground Truth loader、ArtifactWriter）而不自建通用 workflow/fake framework。锁定以下顺序和失败语义：

```python
@pytest.mark.asyncio
async def test_successful_attempt_is_fresh_recovered_evaluated_and_persisted(deps) -> None:
    runner, calls, output_path = deps.successful_runner()

    result = await runner.run(CASE_ID)

    assert calls == [
        "load_ground_truth",
        "reset:initial",
        "inject",
        "build",
        "diagnosis_factory",
        "diagnose",
        "reset:recovery",
        "evaluate",
        "write_artifacts",
    ]
    assert result.run_id == RUN_ID
    assert result.status == EvaluationStatus.PASSED
    assert result.artifact_dir == output_path


@pytest.mark.asyncio
@pytest.mark.parametrize("diagnosis_status", ["INSUFFICIENT_EVIDENCE", "MODEL_ERROR"])
async def test_nonpassing_diagnosis_is_still_evaluated_and_persisted(
    deps, diagnosis_status: str
) -> None:
    runner, writer = deps.runner_with_diagnosis_status(diagnosis_status)

    result = await runner.run(CASE_ID)

    assert result.status == EvaluationStatus.FAILED
    assert writer.received.diagnosis_run.diagnosis.status.value == diagnosis_status
    assert writer.received.evaluation.status == EvaluationStatus.FAILED


@pytest.mark.asyncio
async def test_recovery_failure_is_saved_after_a_real_diagnosis(deps) -> None:
    runner, writer = deps.runner_with_recovery_failure()

    result = await runner.run(CASE_ID)

    assert result.status == EvaluationStatus.FAILED
    assert writer.received.recovery_status == RecoveryStatus.FAILED
    assert EvaluationCheckCode.RECOVERY_HEALTHY in result.evaluation.failed_check_codes


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stage", ["ground_truth_load", "initial_reset", "inject", "build"]
)
async def test_prediagnosis_failure_never_fabricates_diagnosis_or_sample(deps, stage: str) -> None:
    runner, writer, diagnosis_factory = deps.runner_failing_at(stage)

    with pytest.raises(EvaluationWorkflowError) as captured:
        await runner.run(CASE_ID)

    assert captured.value.code == f"{stage.upper()}_FAILED"
    assert writer.calls == []
    assert diagnosis_factory.calls == []
```

另外显式测试：Ground Truth preflight 发生在首次 reset 之前，失败时不改变 lab、不创建 Diagnosis/样本；初始 reset 成功后 inject/build 失败也会尝试一次 recovery reset；primary error 不被 recovery error 覆盖；预加载的 Ground Truth 对象不传给 diagnosis factory/runner 或任何 model callback，只在 `diagnose` 返回和 recovery 尝试后传给 evaluator；writer 失败映射为稳定 `ARTIFACT_WRITE_FAILED`；所有 workflow 异常断开 cause/context，不保留 `TEST_REDACTED_VALUE`、绝对路径或原始 provider 文本。

`tests/unit/test_cli.py` 先锁定：

```python
def test_eval_run_is_one_bounded_attempt(monkeypatch) -> None:
    calls: list[str] = []

    class FakeEvaluationRunner:
        async def run(self, case_id: str) -> EvaluationAttemptResult:
            calls.append(case_id)
            return passing_attempt_result()

    monkeypatch.setattr(cli, "create_evaluation_runner", lambda: FakeEvaluationRunner())
    result = runner.invoke(cli.app, ["eval", "run", CASE_ID])

    assert result.exit_code == 0
    assert calls == [CASE_ID]
    assert "PASSED" in result.stdout
    assert f"artifacts/{passing_attempt_result().run_id}" in result.stdout


def test_eval_run_failed_score_keeps_artifact_path_and_exits_nonzero(monkeypatch) -> None:
    monkeypatch.setattr(cli, "create_evaluation_runner", fake_failed_evaluation_runner)

    result = runner.invoke(cli.app, ["eval", "run", CASE_ID])

    assert result.exit_code == 1
    assert "FAILED" in result.stdout
    assert "artifacts" in result.stdout
    assert "Traceback" not in result.stdout + result.stderr
```

这里的测试 double 必须是文件内窄实现：`deps` 分别注入 FakeLab、Diagnosis factory、Ground Truth loader、Evaluator、ArtifactWriter 和可控 UTC/monotonic clock，并由每个 fake 在共享 list 追加上述精确调用名；`passing_attempt_result()` 每次返回同一个固定 case/run、PASSED evaluation 和相对项目根解析出的 artifact path；失败 CLI fake 只把 evaluation/status 改为 FAILED，仍返回存在的 artifact path。不得调用 Docker、dbt、数据库、Git 或模型。

CLI help 测试必须拒绝 `--repeat`、`--runs`、`--run-id`、`--model`、`--base-url`、`--prompt`、`--path`、`--sql`、`--table`、`--repair`；P0 只有一个 `CASE_ID` argument。
再增加 factory/settings 抛出含 `TEST_REDACTED_VALUE` 和绝对路径的合成异常用例；CLI 必须只输出 `EVALUATION_SETUP_FAILED`、exit 1 且无 traceback/原文。

- [ ] **Step 2: 运行 RED**

```powershell
uv run pytest tests/unit/test_evaluation_runner.py tests/unit/test_cli.py -q
```

Expected: `evaluation_runner` 和 `eval` group 尚不存在导致失败；现有 pipeline/lab/diagnose 测试仍能 collection。

- [ ] **Step 3: 实现小 Interface/深 Implementation 编排器**

`src/data_incident_gym/evaluation_runner.py` 公开合同固定为：

```python
class EvaluationWorkflowError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)
        self.__cause__ = None
        self.__context__ = None


class EvaluationAttemptResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    incident_case_id: CaseId
    run_id: RunId
    status: EvaluationStatus
    evaluation: EvaluationResult
    artifact_dir: Path

    @model_validator(mode="after")
    def validate_result_identity(self) -> Self:
        if self.status != self.evaluation.status:
            raise ValueError("attempt status must match evaluation")
        if self.evaluation.incident_case_id != self.incident_case_id:
            raise ValueError("attempt case must match evaluation")
        if self.evaluation.run_id != self.run_id or self.artifact_dir.name != self.run_id:
            raise ValueError("attempt run_id must match evaluation and artifact directory")
        return self


class EvaluationRunner:
    def __init__(
        self,
        *,
        lab: IncidentLab,
        diagnostic_settings: DiagnosticSettings,
        diagnosis_factory: Callable[[str], DiagnosisRunner],
        ground_truth_loader: Callable[[str], GroundTruth],
        evaluator: Callable[..., EvaluationResult],
        artifact_writer: ArtifactWriter,
        clock: Callable[[], datetime],
    ) -> None:
        self._lab = lab
        self._diagnostic_settings = diagnostic_settings
        self._diagnosis_factory = diagnosis_factory
        self._ground_truth_loader = ground_truth_loader
        self._evaluator = evaluator
        self._artifact_writer = artifact_writer
        self._clock = clock

    @classmethod
    def for_project(
        cls,
        settings: Settings,
        diagnostic_settings: DiagnosticSettings,
        project_root: Path = PROJECT_ROOT,
    ) -> EvaluationRunner:
        lab = IncidentLab(settings, project_root)
        writer = ArtifactWriter(project_root)

        def diagnosis_factory(run_id: str) -> DiagnosisRunner:
            return DiagnosisRunner.for_run(run_id, diagnostic_settings, project_root)

        return cls(
            lab=lab,
            diagnostic_settings=diagnostic_settings,
            diagnosis_factory=diagnosis_factory,
            ground_truth_loader=lambda case_id: load_ground_truth(case_id, project_root),
            evaluator=DeterministicEvaluator.evaluate,
            artifact_writer=writer,
            clock=lambda: datetime.now(timezone.utc),
        )

    async def run(self, incident_case_id: str) -> EvaluationAttemptResult:
        started_at = self._clock()
        try:
            ground_truth = self._ground_truth_loader(incident_case_id)
        except (IncidentCaseError, OSError, ValueError):
            raise EvaluationWorkflowError("GROUND_TRUTH_LOAD_FAILED") from None

        mutation_started = False
        fault_run: FaultRun | None = None
        diagnosis_run: DiagnosisRunResult | None = None
        primary_error_code: str | None = None
        recovery_succeeded = False
        stage = "INITIAL_RESET"

        try:
            self._lab.reset(incident_case_id)
            mutation_started = True
            stage = "INJECT"
            self._lab.inject(incident_case_id)
            stage = "BUILD"
            fault_run = self._lab.build(incident_case_id)
            stage = "DIAGNOSIS_SETUP"
            diagnosis_runner = self._diagnosis_factory(fault_run.run_id)
            stage = "DIAGNOSIS"
            diagnosis_run = await diagnosis_runner.diagnose(incident_case_id)
        except (LabError, IncidentCaseError, RunContextError):
            primary_error_code = f"{stage}_FAILED"
        except Exception:
            primary_error_code = f"{stage}_FAILED"
        finally:
            if mutation_started:
                try:
                    self._lab.reset(incident_case_id)
                    recovery_succeeded = True
                except Exception:
                    recovery_succeeded = False

        if diagnosis_run is None or fault_run is None:
            raise EvaluationWorkflowError(primary_error_code or "WORKFLOW_FAILED")

        try:
            evaluation = self._evaluator(
                ground_truth,
                fault_run.verification,
                diagnosis_run,
                recovery_succeeded=recovery_succeeded,
            )
        except (TypeError, ValueError, ValidationError):
            raise EvaluationWorkflowError("EVALUATION_FAILED") from None
        artifact_run = ArtifactRun(
            incident_case_id=incident_case_id,
            run_id=fault_run.run_id,
            started_at=started_at,
            finished_at=self._clock(),
            recovery_status=(
                RecoveryStatus.HEALTHY if recovery_succeeded else RecoveryStatus.FAILED
            ),
            model_base_url=self._diagnostic_settings.model_base_url,
            diagnosis_run=diagnosis_run,
            evaluation=evaluation,
        )
        try:
            artifact_dir = self._artifact_writer.write(artifact_run)
        except ArtifactWriteError:
            raise EvaluationWorkflowError("ARTIFACT_WRITE_FAILED") from None
        return EvaluationAttemptResult(
            incident_case_id=incident_case_id,
            run_id=fault_run.run_id,
            status=evaluation.status,
            evaluation=evaluation,
            artifact_dir=artifact_dir,
        )
```

阶段码只由代码位置设置，不得从异常文本猜测；固定映射为 `INITIAL_RESET_FAILED`、`INJECT_FAILED`、`BUILD_FAILED`、`DIAGNOSIS_SETUP_FAILED`、`DIAGNOSIS_FAILED`、`GROUND_TRUTH_LOAD_FAILED`、`EVALUATION_FAILED`、`ARTIFACT_WRITE_FAILED`。M4 `diagnose()` 返回的 `MODEL_ERROR` 是有效样本，不能被 exception path 吞掉。

`ground_truth_loader` 是 mutation 前的 fail-fast preflight：读取或校验失败时不注入故障，也不产生模型样本。隔离依靠显式依赖边界而不是延迟文件读取：返回对象只保存在 orchestrator 局部变量中，在 `diagnose` 返回和 recovery 尝试后才传给 `DeterministicEvaluator`；不得传给 DiagnosisRunner、prompt、tools、ArtifactWriter 的 Diagnosis 部分或任何 model callback。静态扫描和 fake 的参数记录共同证明这一点。

- [ ] **Step 4: 实现有界 `eval run` CLI**

`src/data_incident_gym/cli.py` 新增：

```python
eval_app = typer.Typer(help="运行确定性评测与报告闭环。")
app.add_typer(eval_app, name="eval")


def create_evaluation_runner() -> EvaluationRunner:
    return EvaluationRunner.for_project(Settings(), DiagnosticSettings())


@eval_app.command("run")
def eval_run(case_id: str) -> None:
    """对一个固定案例执行一次独立的完整评测。"""
    try:
        result = asyncio.run(create_evaluation_runner().run(case_id))
    except EvaluationWorkflowError as error:
        typer.echo(f"评测运行失败 [{error.code}]。", err=True)
        raise typer.Exit(code=1) from None
    except Exception:
        typer.echo("评测运行失败 [EVALUATION_SETUP_FAILED]。", err=True)
        raise typer.Exit(code=1) from None

    typer.echo("评测通过。" if result.status == EvaluationStatus.PASSED else "评测未通过。")
    typer.echo(f"status: {result.status.value}")
    typer.echo(f"run_id: {result.run_id}")
    typer.echo(f"artifacts: artifacts/{result.run_id}")
    if result.status != EvaluationStatus.PASSED:
        raise typer.Exit(code=1)
```

CLI 不输出全部 evidence/trace；机器读取以六文件为准。对于评分失败，必须先输出 run ID 和 artifact path 再 exit 1。

- [ ] **Step 5: 写真实 M2/M3 + FunctionModel 单次集成测试并 GREEN**

`tests/integration/test_evaluation_runner.py` 必须使用真实 `IncidentLab`、真实 M2 run artifacts、真实 M3 tools 和 PydanticAI `FunctionModel`。固定 callback 只能根据前一步工具返回的 dbt error、Schema 和 lineage 构造 Diagnosis；不得 import/load Ground Truth 或读 `ground_truth.json`。测试断言：

```python
@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_lab_function_model_evaluator_and_artifacts_close_one_attempt() -> None:
    runner = evaluation_runner_with_function_model(PROJECT_ROOT)

    result = await runner.run(CASE_ID)

    assert result.status == EvaluationStatus.PASSED
    assert result.evaluation.failed_check_codes == ()
    assert {path.name for path in result.artifact_dir.iterdir()} == EXPECTED_FILES
    assert EvaluationResult.model_validate_json(
        (result.artifact_dir / "evaluation.json").read_text(encoding="utf-8")
    ).status == EvaluationStatus.PASSED
    lab = IncidentLab(Settings(_env_file=None), PROJECT_ROOT)
    try:
        assert lab.inject(CASE_ID).state == "INJECTED"
    finally:
        assert lab.reset(CASE_ID).state == "HEALTHY"
```

最后两个断言通过公开 `inject/reset` seam 证明 runner 返回前已恢复健康：如果 recovery 未生效，新 inject 会因非健康状态失败。

```powershell
uv run pytest tests/unit/test_evaluation_runner.py tests/unit/test_cli.py -q
uv run pytest tests/integration/test_evaluation_runner.py -q -s
uv run data-incident-gym lab reset schema_rename_payment_amount
```

Expected: unit 和单次真实 integration exit 0；六文件存在；最后 reset exit 0。集成测试生成的 ignored artifact 保留供审计，不放入 Git。

- [ ] **Step 6: 静态隔离、提交和独立审查**

```powershell
uv run ruff check src/data_incident_gym/evaluation_runner.py src/data_incident_gym/cli.py tests/unit/test_evaluation_runner.py tests/unit/test_cli.py tests/integration/test_evaluation_runner.py
rg -n 'GroundTruth|load_ground_truth|ground_truth|IncidentVerifier|lab_verifier' src/data_incident_gym/diagnosis.py src/data_incident_gym/diagnostic_agent.py src/data_incident_gym/evidence_tools.py
rg -n 'eval_app|@eval_app.command\("run"\)|def eval_run' src/data_incident_gym/cli.py
git diff --check -- src/data_incident_gym/evaluation_runner.py src/data_incident_gym/cli.py tests/unit/test_evaluation_runner.py tests/unit/test_cli.py tests/integration/test_evaluation_runner.py
git add src/data_incident_gym/evaluation_runner.py src/data_incident_gym/cli.py tests/unit/test_evaluation_runner.py tests/unit/test_cli.py tests/integration/test_evaluation_runner.py
git diff --cached --check
git diff --cached --name-only
git commit -m "feat: orchestrate one-shot M5 evaluations"
```

Expected: tests/Ruff/diff exit 0；第一条隔离 `rg` 无输出；cached list 只含五个 Task 3 路径。随后做一次独立广度审查：阶段顺序、一命令一样本、Ground Truth 依赖隔离与 preflight fail-fast、finally reset、MODEL_ERROR 保存、前置失败不伪造 Diagnosis、CLI exit code、冻结脱敏边界和提交范围。finding 按总协议分类；只有 BLOCKER 进入一次最小修复和原清单定向复审。

---

### Task 4: 实现只读 `doctor` 与真实模型工具/结构化输出最小探针

**Files:**

- Create: `src/data_incident_gym/doctor.py`
- Create: `tests/unit/test_doctor.py`
- Modify: `src/data_incident_gym/cli.py`
- Verify without modification: `config/dbt/profiles.yml` (继续作为纯管理 profile)
- Create: `config/dbt/diagnostic/profiles.yml`
- Modify: `tests/unit/test_cli.py`
- Verify without modification: `src/data_incident_gym/diagnostic_config.py`

- [ ] **Step 1: 写固定顺序、无副作用和脱敏 RED 测试**

`tests/unit/test_doctor.py` 使用注入的 `run_command`、`db_connect`、`url_open`、`TestModel` 和临时目录 factory，禁止真实 subprocess、Docker、数据库、HTTP 或模型请求。检查顺序固定为：

```python
CHECK_ORDER = (
    "PYTHON",
    "UV",
    "DOCKER",
    "COMPOSE_POSTGRES",
    "POSTGRES_CONNECTION",
    "DBT_PROFILE_CONNECTION",
    "OLLAMA_ENDPOINT",
    "MODEL_PRESENT",
    "MODEL_TOOL_STRUCTURED_OUTPUT",
)


@pytest.mark.asyncio
async def test_doctor_passes_only_when_every_read_only_check_passes(doctor) -> None:
    result = await doctor.run()

    assert result.status == DoctorStatus.PASSED
    assert tuple(check.code.value for check in result.checks) == CHECK_ORDER
    assert all(check.passed for check in result.checks)
    assert doctor.model.last_model_request_parameters is not None
    assert {tool.name for tool in doctor.model.last_model_request_parameters.function_tools} == {
        "read_probe_value"
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_code", CHECK_ORDER)
async def test_each_failed_check_returns_fixed_recommendation_without_raw_error(
    doctor_factory, failed_code: str
) -> None:
    doctor = doctor_factory(failed_code, raw_error="password=TEST_REDACTED_VALUE C:\\secret\\x")

    result = await doctor.run()

    failed = next(check for check in result.checks if check.code.value == failed_code)
    serialized = result.model_dump_json()
    assert result.status == DoctorStatus.FAILED
    assert failed.reason_code == f"{failed_code}_FAILED"
    assert failed.recommendation_code in EXPECTED_RECOMMENDATIONS
    assert "TEST_REDACTED_VALUE" not in serialized
    assert "C:\\secret\\x" not in serialized
```

`doctor` fixture 注入所有成功的固定 subprocess/数据库/URL/TestModel adapter；`doctor_factory(failed_code, raw_error)` 只能翻转所指定的一项，并让依赖于该项的后续检查以固定 `UNAVAILABLE` 失败，不得抛出到测试外；`EXPECTED_RECOMMENDATIONS` 精确为 `set(RECOMMENDATION_BY_CHECK.values())`。每个 fake 记录 argv、timeout、SQL、URL 和调用次数，使下一段只读断言可以直接核验，禁止真实 I/O。

另外必须断言：所有 subprocess 均为 argument list、`shell=False`、超时有界；不出现 `docker compose up/down/restart`、dbt build/seed/run、数据库写 SQL、Agent 的 M3 工具或 Ground Truth；dbt debug 的 target/log 位于 OS temp 并在检查后回收，项目工作树不新增文件；Ollama/model 检查只 GET 已配置 OpenAI-compatible `/v1/models`；模型探针工具只返回合成常量 `DOCTOR_TOOL_OK`。

`tests/unit/test_cli.py` 新增 doctor 成功/失败表格、exit 0/1、中文建议和无 traceback/原始错误断言；factory/settings 在 runner 建立前失败时只输出 `DOCTOR_SETUP_FAILED`，不暴露异常文本。

- [ ] **Step 2: 运行 RED**

```powershell
uv run pytest tests/unit/test_doctor.py tests/unit/test_cli.py -q
```

Expected: `data_incident_gym.doctor` 和 CLI command 不存在导致失败；没有发出真实模型请求。

- [ ] **Step 3: 实现严格 DoctorResult 和固定建议表**

`src/data_incident_gym/doctor.py` 定义：

```python
class DoctorStatus(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"


class DoctorCheckCode(StrEnum):
    PYTHON = "PYTHON"
    UV = "UV"
    DOCKER = "DOCKER"
    COMPOSE_POSTGRES = "COMPOSE_POSTGRES"
    POSTGRES_CONNECTION = "POSTGRES_CONNECTION"
    DBT_PROFILE_CONNECTION = "DBT_PROFILE_CONNECTION"
    OLLAMA_ENDPOINT = "OLLAMA_ENDPOINT"
    MODEL_PRESENT = "MODEL_PRESENT"
    MODEL_TOOL_STRUCTURED_OUTPUT = "MODEL_TOOL_STRUCTURED_OUTPUT"


class DoctorCheck(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: DoctorCheckCode
    passed: StrictBool
    observed: StrictStr
    reason_code: StrictStr
    recommendation_code: StrictStr | None


class DoctorResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: DoctorStatus
    checks: tuple[DoctorCheck, ...]

    @model_validator(mode="after")
    def validate_complete_checks(self) -> Self:
        if tuple(check.code for check in self.checks) != tuple(DoctorCheckCode):
            raise ValueError("doctor checks must be complete and ordered")
        for check in self.checks:
            suffix = "PASSED" if check.passed else "FAILED"
            expected_reason = f"{check.code.value}_{suffix}"
            if check.reason_code != expected_reason:
                raise ValueError("doctor reason code must match check")
            if not check.observed.strip():
                raise ValueError("doctor observed value must not be blank")
            expected_recommendation = (
                None if check.passed else RECOMMENDATION_BY_CHECK[check.code]
            )
            if check.recommendation_code != expected_recommendation:
                raise ValueError("doctor recommendation must match failed check")
        expected = (
            DoctorStatus.PASSED
            if all(check.passed for check in self.checks)
            else DoctorStatus.FAILED
        )
        if self.status != expected:
            raise ValueError("doctor status must match checks")
        return self
```

实现精确的 `RECOMMENDATION_BY_CHECK` 映射：`PYTHON → USE_PYTHON_3_12`、`UV → INSTALL_UV_0_11_24`、`DOCKER → START_DOCKER_DESKTOP`、`COMPOSE_POSTGRES → START_POSTGRES_COMPOSE`、`POSTGRES_CONNECTION → CHECK_POSTGRES_SETTINGS`、`DBT_PROFILE_CONNECTION → CHECK_DBT_PROFILE`、`OLLAMA_ENDPOINT → START_OLLAMA`、`MODEL_PRESENT → PULL_GEMMA4_E4B`、`MODEL_TOOL_STRUCTURED_OUTPUT → CHECK_MODEL_TOOL_CALLING`。`observed` 只保存经正则验证的版本/模型名或固定状态码；失败时为 `UNAVAILABLE`。不把 exception message、stdout/stderr、DSN、密码、API key 或路径放入 result。

- [ ] **Step 4: 实现九个只读检查和 PydanticAI 最小探针**

`DoctorRunner.run()` 不 short-circuit；即使前置检查失败也返回完整有序表，但有显式依赖的检查（例如 endpoint 不可达时的 model presence/probe）直接返回稳定失败码，不重试网络。具体操作固定为：

```python
class DoctorRunner:
    def __init__(
        self,
        diagnostic_settings: DiagnosticSettings,
        project_root: Path,
        *,
        run_command: RunCommand,
        db_connect: DatabaseConnect,
        url_open: UrlOpen,
        model: Model,
        temporary_directory: TemporaryDirectoryFactory,
    ) -> None:
        self._diagnostic_settings = diagnostic_settings
        self._project_root = project_root
        self._run_command = run_command
        self._db_connect = db_connect
        self._url_open = url_open
        self._model = model
        self._temporary_directory = temporary_directory

    @classmethod
    def for_project(
        cls,
        diagnostic_settings: DiagnosticSettings,
        project_root: Path = PROJECT_ROOT,
    ) -> DoctorRunner:
        provider = OpenAIProvider(
            base_url=str(diagnostic_settings.model_base_url),
            api_key=diagnostic_settings.model_api_key.get_secret_value(),
        )
        model = OpenAIChatModel(diagnostic_settings.model_name, provider=provider)
        return cls(
            diagnostic_settings,
            project_root,
            run_command=subprocess.run,
            db_connect=psycopg.connect,
            url_open=urlopen,
            model=model,
            temporary_directory=TemporaryDirectory,
        )


    def _commands(self) -> dict[DoctorCheckCode, list[str]]:
        return {
            DoctorCheckCode.UV: ["uv", "--version"],
            DoctorCheckCode.DOCKER: [
                "docker", "version", "--format", "{{.Server.Version}}"
            ],
            DoctorCheckCode.COMPOSE_POSTGRES: [
                "docker",
                "compose",
                "-f",
                str(self._project_root / "compose.yaml"),
                "ps",
                "--status",
                "running",
                "--services",
            ],
        }
```

`RunCommand`、`DatabaseConnect`、`UrlOpen` 和 `TemporaryDirectoryFactory` 是窄 Callable type alias，只为测试替换现有真实 Adapter；不建通用 plugin registry 或新抽象层。`commands` 在实际方法内使用 `self._project_root`，不从 CLI 接收任意路径。

- Python 检查 `platform.python_version() == "3.12.10"`；uv 输出必须精确解析为 `0.11.24`，不把任意 stdout 透传到 `observed`。Docker 只接受数字点分 server version，Compose 列表必须精确包含 `postgres`。
- PostgreSQL 使用 `DiagnosticSettings` 只读连接配置执行唯一 `SELECT 1`，不 provision role、不 commit 写操作；缺少显式诊断数据库配置时固定返回不可用，不回退到管理配置。
- 管理 profile `config/dbt/profiles.yml` 仅供管理平面使用，包括 pipeline、IncidentLab/lab；doctor 和 `dbt debug` 使用独立的 `config/dbt/diagnostic/profiles.yml`，其中只引用必需的 `DIG_DIAGNOSTIC_POSTGRES_*` 环境变量，不含管理凭据、默认凭据或回退表达式。dbt 使用由 `DiagnosticSettings` 构造的最小、无管理 secret 的 subprocess 环境（不调用 `Settings.subprocess_environment()`，不传 `DIG_POSTGRES_PASSWORD` 或其他管理 secret）；非 dbt subprocess 不注入数据库凭据。命令 argv 精确为 `dbt debug --project-dir <root>/third_party/jaffle_shop --profiles-dir <root>/config/dbt/diagnostic --target dev --log-path <OS-temp>/logs --connection --no-write-json --no-partial-parse --no-use-colors --no-send-anonymous-usage-stats --no-upload-to-artifacts-ingest-api`。`--connection` 只验证诊断 profile 和连接；这些 artifact/telemetry 开关禁止 target artifact、partial parse、匿名统计和 artifact 上传；临时目录退出即回收，不改 `.dig/`。
- endpoint 只读 GET `DiagnosticSettings.model_base_url.rstrip('/') + '/models'`，timeout 固定 5 秒，最多读取 1 MiB + 1 byte 并对超限 fail closed；严格解析形如 `{"data": [{"id": "gemma4:e4b"}]}` 的结构并精确查找 `model_name`。该 GET 不发送 API key，不保存 body 或原始错误。
- model probe 继续使用 `OpenAIChatModel` + `OpenAIProvider`，而不调 Ollama 私有生成接口；只注册一个工具：

```python
class DoctorProbeOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tool_value: Literal["DOCTOR_TOOL_OK"]


@dataclass
class ProbeState:
    tool_called: bool = False


agent = Agent(model, deps_type=ProbeState, output_type=DoctorProbeOutput)


@agent.tool
def read_probe_value(ctx: RunContext[ProbeState]) -> str:
    ctx.deps.tool_called = True
    return "DOCTOR_TOOL_OK"


@agent.output_validator
def require_real_tool_call(
    ctx: RunContext[ProbeState], output: DoctorProbeOutput
) -> DoctorProbeOutput:
    if not ctx.deps.tool_called or output.tool_value != "DOCTOR_TOOL_OK":
        raise ModelRetry("DOCTOR_TOOL_REQUIRED")
    return output
```

探针用 `asyncio.timeout(60)`、`UsageLimits(request_limit=2, tool_calls_limit=1)`、output retry 1；失败只返回 `MODEL_TOOL_STRUCTURED_OUTPUT_FAILED`。不保存 prompt/completion，不把该探针算成 P0 评测样本。

- [ ] **Step 5: 接入 CLI，保持中文可操作输出**

`src/data_incident_gym/cli.py` 新增顶层 `doctor`：

```python
def create_doctor_runner() -> DoctorRunner:
    return DoctorRunner.for_project(DiagnosticSettings())


@app.command("doctor")
def doctor() -> None:
    """只读检查 P0 环境、依赖和模型最小能力。"""
    try:
        result = asyncio.run(create_doctor_runner().run())
    except Exception:
        typer.echo("doctor 失败 [DOCTOR_SETUP_FAILED]。", err=True)
        raise typer.Exit(code=1) from None
    for check in result.checks:
        state = "通过" if check.passed else "失败"
        typer.echo(f"[{state}] {check.code.value}: {check.observed}")
        if check.recommendation_code is not None:
            typer.echo(DOCTOR_RECOMMENDATIONS_ZH[check.recommendation_code])
    typer.echo("说明：doctor 通过不代表 P0 评测通过。")
    if result.status == DoctorStatus.FAILED:
        raise typer.Exit(code=1)
```

`DOCTOR_RECOMMENDATIONS_ZH` 是 CLI 的固定中文映射，不插入原始异常。

- [ ] **Step 6: GREEN、无副作审计、提交和独立审查**

```powershell
uv run pytest tests/unit/test_doctor.py tests/unit/test_cli.py -q
uv run ruff check src/data_incident_gym/doctor.py src/data_incident_gym/cli.py tests/unit/test_doctor.py tests/unit/test_cli.py
rg -n 'compose.*(up|down|restart)|dbt.*(build|seed|run)|INSERT|UPDATE|DELETE|ALTER|CREATE|DROP|GroundTruth|load_ground_truth' src/data_incident_gym/doctor.py
git diff --check -- src/data_incident_gym/doctor.py src/data_incident_gym/cli.py tests/unit/test_doctor.py tests/unit/test_cli.py
git add src/data_incident_gym/doctor.py src/data_incident_gym/cli.py config/dbt/diagnostic/profiles.yml tests/unit/test_doctor.py tests/unit/test_cli.py
git diff --cached --check
git diff --cached --name-only
git commit -m "fix: split diagnostic dbt profile"
```

Expected: tests/Ruff/diff exit 0；安全 `rg` 无输出（测试中的禁止文本不在扫描路径）；cached list 只含五个 Task 4 路径。随后做一次独立广度审查：九项覆盖、无系统/项目/数据库持久变更、subprocess allowlist/超时、loopback/configured endpoint、真工具调用证明、冻结脱敏边界、“doctor ≠ eval”说明和提交范围。finding 按总协议分类；只有 BLOCKER 进入一次最小修复和原清单定向复审。

---

### Task 5: 固化三次真实 `gemma4:e4b` 样本并执行 2/3 P0 验收

**Files:**

- Create: `tests/e2e/test_ollama_evaluation.py`
- Verify without modification: `tests/conftest.py`
- Verify without modification: `tests/e2e/test_ollama_diagnosis.py`
- Modify (workspace-only, never stage or commit): `mistake.md`
- Runtime artifacts only, ignored: `artifacts/<run_id>/`

- [ ] **Step 1: 写精确三样本、唯一 run ID 和失败保留测试**

`tests/e2e/test_ollama_evaluation.py` 不接收 repeat 参数、不内嵌 retry，每次循环都新建 production `EvaluationRunner`：

```python
from __future__ import annotations

import os

import pytest

from data_incident_gym.artifacts import ARTIFACT_FILENAMES
from data_incident_gym.config import PROJECT_ROOT, Settings
from data_incident_gym.diagnostic_config import DiagnosticSettings
from data_incident_gym.evaluation import EvaluationStatus
from data_incident_gym.evaluation_runner import EvaluationRunner
from data_incident_gym.incidents import CASE_ID
from data_incident_gym.lab import IncidentLab

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.ollama,
    pytest.mark.skipif(
        os.getenv("DIG_RUN_OLLAMA_TESTS") != "1",
        reason="set DIG_RUN_OLLAMA_TESTS=1 to enable three real M5 samples",
    ),
]


@pytest.mark.asyncio
async def test_default_ollama_passes_at_least_two_of_three_independent_evaluations() -> None:
    settings = Settings(_env_file=None)
    diagnostic_settings = DiagnosticSettings(_env_file=None)
    attempts = []
    lab = IncidentLab(settings, PROJECT_ROOT)
    try:
        for _ in range(3):
            result = await EvaluationRunner.for_project(
                settings,
                diagnostic_settings,
                PROJECT_ROOT,
            ).run(CASE_ID)
            attempts.append(result)
            print(
                "M5_SAMPLE "
                f"run_id={result.run_id} status={result.status.value} "
                f"artifacts=artifacts/{result.run_id}"
            )
    finally:
        lab.reset(CASE_ID)

    assert len(attempts) == 3
    assert len({attempt.run_id for attempt in attempts}) == 3
    assert all(
        {path.name for path in attempt.artifact_dir.iterdir()} == set(ARTIFACT_FILENAMES)
        for attempt in attempts
    )
    assert all(attempt.artifact_dir.is_dir() for attempt in attempts)
    assert sum(attempt.status == EvaluationStatus.PASSED for attempt in attempts) >= 2
```

测试不在 assertion 前删除 artifact，因此 0/3、1/3、2/3、3/3 的全部样本均可审计。`finally lab.reset` 只恢复环境，不重跑、不修改分数。

- [ ] **Step 2: 先证明默认测试不发真实请求，再提交验收测试**

```powershell
Remove-Item Env:DIG_RUN_OLLAMA_TESTS -ErrorAction SilentlyContinue
uv run pytest tests/e2e/test_ollama_evaluation.py -q
uv run ruff check tests/e2e/test_ollama_evaluation.py
git diff --check -- tests/e2e/test_ollama_evaluation.py
git add tests/e2e/test_ollama_evaluation.py
git diff --cached --check
git diff --cached --name-only
git commit -m "test: enforce M5 real-model acceptance"
```

Expected: pytest exit 0 且精确 `1 skipped`，没有模型请求或新 artifact；Ruff/diff exit 0；cached list 只含新 e2e 文件。

- [ ] **Step 3: 执行 TestModel/确定性全门槛，确认真实模型前的工程状态**

```powershell
uv run ruff check .
uv run pytest tests/unit -q
uv run pytest tests/integration -q
uv lock --check
uv run data-incident-gym eval run --help
uv run data-incident-gym doctor --help
```

Expected: Ruff/unit/integration/lock/help 全部 exit 0；unit 中 PydanticAI `TestModel` 相关测试 100% 通过；integration 产生的 FunctionModel M5 样本通过且恢复健康。本步不设置 `DIG_RUN_OLLAMA_TESTS`。

- [ ] **Step 4: 只执行一次真实 doctor 最小探针**

```powershell
uv run data-incident-gym doctor
```

Expected: exit 0，九项全部通过，并明确输出“doctor 通过不代表 P0 评测通过”。该命令的一次合成工具探针不计入真实三样本；若失败，记录失败 check/recommendation/exit 后停止，不进入 Step 5，不重试模型。

- [ ] **Step 5: 在当前 Windows 主机上执行唯一一次、精确三样本验收**

由于当前机器的已知 native 不稳定，仅长跑本地验收使用进程级 affinity；环境变量和 affinity 都必须恢复原值：

```powershell
$shellProcess = Get-Process -Id $PID
$originalAffinity = $shellProcess.ProcessorAffinity
$hadOllamaOptIn = Test-Path Env:DIG_RUN_OLLAMA_TESTS
$originalOllamaOptIn = if ($hadOllamaOptIn) { $env:DIG_RUN_OLLAMA_TESTS } else { $null }
$realEvalExit = 1
try {
    $shellProcess.ProcessorAffinity = [IntPtr]0xFFFF
    $env:DIG_RUN_OLLAMA_TESTS = '1'
    uv run pytest tests/e2e/test_ollama_evaluation.py -q -s
    $realEvalExit = $LASTEXITCODE
}
finally {
    if ($hadOllamaOptIn) {
        $env:DIG_RUN_OLLAMA_TESTS = $originalOllamaOptIn
    }
    else {
        Remove-Item Env:DIG_RUN_OLLAMA_TESTS -ErrorAction SilentlyContinue
    }
    $shellProcess.ProcessorAffinity = $originalAffinity
}
if ($realEvalExit -ne 0) { exit $realEvalExit }
```

Expected: 命令只运行一次 pytest node，内部精确三个新 run ID；2/3 或 3/3 `PASSED`；三个 artifact 目录均有六文件；最终数据库健康；环境变量和 affinity 恢复。不同时运行既有 `test_ollama_diagnosis.py`，避免额外第四个诊断样本。

若结果为 0/3 或 1/3，或任一非 Diagnosis 产物字段出现冻结的安全哨兵/原始异常：保留三个完整 artifact 目录，在 workspace-only `mistake.md` 记录 HEAD、命令、exit、三个 run ID、逐项 failed check、Diagnosis status、usage 和恢复事实，然后停止并请用户决策。不得：

- 再执行第四/第五次来改变分母。
- 把 Ground Truth、案例标识/资产答案或超出已版本化通用 ontology 的 case-specific root 提示注入 prompt/schema/tool。
- 新增 regex/JSON repair、模型专属循环、隐藏 retry、fallback 或换模型。
- 放宽 exact root/affected/evidence 判据或从统计中剔除失败 run。

- [ ] **Step 6: 审计三样本与 Task 5 提交**

```powershell
$latestRuns = Get-ChildItem -LiteralPath 'artifacts' -Directory |
    Sort-Object LastWriteTimeUtc -Descending |
    Select-Object -First 3
$latestRuns | ForEach-Object {
    $files = Get-ChildItem -LiteralPath $_.FullName -File | Select-Object -ExpandProperty Name
    [pscustomobject]@{
        RunId = $_.Name
        FileCount = $files.Count
        Files = ($files | Sort-Object) -join ','
    }
} | Format-Table -AutoSize
git status --short --branch
git diff --cached --name-only
```

Expected: 最新三个 run 各 6 文件；Git 中无 artifacts；Task 5 commit 只含 `tests/e2e/test_ollama_evaluation.py`；根目录三文件仍 unstaged。随后做一次独立广度审查：“精确三次”、样本独立性、分母、六文件失败保留、真实 provider/model metadata、无 Ground Truth 泄漏、无 Agent 写工具、恢复健康和 Git 边界。Reviewer 只读 artifact 中已脱敏字段，不读真实凭据、secret store 或原始 provider 日志；finding 按总协议分类，最多一次 BLOCKER 修复和原清单定向复审。

---

### Task 6: 完成 P0 总回归、最终对抗性审查与远程 Ubuntu CI 门禁

**Files:**

- Modify (workspace-only, never stage or commit): `README.md`
- Modify (workspace-only, never stage or commit): `mistake.md`
- Verify without modification: `AGENT.md`
- Verify without modification: `docs/requirements.md`
- Verify without modification: `.github/workflows/ci.yml`
- Verify without modification: `third_party/jaffle_shop`
- Verify ignored runtime outputs: `artifacts/`

- [ ] **Step 1: 仅根据真实结果更新 workspace-only 状态文档**

`README.md` 在不覆盖 M1–M4 已有内容的前提下，增加中英双语 P0/M5 摘要、`doctor`、`eval run`、六文件树、exit code、opt-in 真实模型说明和实际 3-run 结果。只有 Task 5 已通过才能写“M5/P0 已完成”；只能表述“单案例工程闭环”，禁止写通用准确率、优于 baseline、生产可用或未实施的 P1 Kernel。

`mistake.md` 追加 M5 各 Task 的真实 commit、命令、exit/summary、每次独立审查结果、doctor 结果、三个 run ID/分数/失败 check、恢复和 Ubuntu CI 状态。不复制原始 provider 文本或任何凭据。

```powershell
git diff -- README.md mistake.md
git status --short --branch
```

Expected: 两个文档仍为 unstaged；`AGENT.md` 的用户内容未被 Task 6 修改；没有将 root Markdown 加入 index。

- [ ] **Step 2: 在默认禁止真实模型的状态下执行完整本地回归**

先确认 opt-in 未设置，然后用当前 Windows 主机已验证的进程级 affinity 包裹长跑 integration/e2e：

```powershell
Remove-Item Env:DIG_RUN_OLLAMA_TESTS -ErrorAction SilentlyContinue
uv run ruff check .
uv run pytest tests/unit -q
$shellProcess = Get-Process -Id $PID
$originalAffinity = $shellProcess.ProcessorAffinity
$longTestExit = 1
try {
    $shellProcess.ProcessorAffinity = [IntPtr]0xFFFF
    uv run pytest tests/integration -q
    if ($LASTEXITCODE -ne 0) { $longTestExit = $LASTEXITCODE }
    else {
        uv run pytest tests/e2e -q
        $longTestExit = $LASTEXITCODE
    }
}
finally {
    $shellProcess.ProcessorAffinity = $originalAffinity
}
if ($longTestExit -ne 0) { exit $longTestExit }
uv lock --check
uv run data-incident-gym --help
uv run data-incident-gym pipeline --help
uv run data-incident-gym lab --help
uv run data-incident-gym diagnose --help
uv run data-incident-gym eval run --help
uv run data-incident-gym doctor --help
git diff --check
```

Expected: Ruff/unit/integration/e2e/lock/help/diff 全部 exit 0；常规 e2e 中两个 Ollama 测试明确 skip，不发任何真实模型请求；affinity 恢复原值。本步不再执行 `doctor` 实体或 `eval run`，不增加 Task 5 之外的真实请求/样本。

- [ ] **Step 3: 执行 P0 安全、范围和产物静态审计**

```powershell
rg -n 'GroundTruth|load_ground_truth|ground_truth|IncidentVerifier|lab_verifier' src/data_incident_gym/diagnosis.py src/data_incident_gym/diagnostic_agent.py src/data_incident_gym/evidence_tools.py
rg -n 'shell|filesystem|arbitrary.*file|free.*sql|write|repair|http_request' src/data_incident_gym/diagnostic_agent.py
rg -n 'Hypothesis|EvidenceGap|ClaimEvidence|accuracy|f1|ablation|baseline skill|LangGraph' src/data_incident_gym tests -g '*.py'
rg -n 'metadata.json|trace.jsonl|evidence.json|diagnosis.json|evaluation.json|report.md' docs/requirements.md src/data_incident_gym/artifacts.py tests/unit/test_artifacts.py tests/e2e/test_ollama_evaluation.py
git -C third_party/jaffle_shop rev-parse HEAD
git -C third_party/jaffle_shop status --short
git status --short --branch
git diff --cached --name-only
```

Expected:

- 前三条安全/P1 扫描无生产实现命中；只允许 tests 中的禁止性断言。
- 六文件在需求、writer 和测试中一致，没有第七个 canonical 文件。
- submodule HEAD 仍精确为 `36bde6cba69d962b83be1d52fc65a0dce1cb4ebb`，status 无输出。
- index 为空；working tree 只有根目录 `AGENT.md`、`README.md`、`mistake.md` 三个既定 unstaged 修改。所有 M5 src/tests/requirements/dependency 变更都已在 Task 1–5 提交。

- [ ] **Step 4: 由独立 reviewer 对 M4 基线到 M5 HEAD 做一次最终广度审查**

审查范围从 `96ad13c062a031f79924de1c5212552011b64097` 到当前 HEAD，至少覆盖：

```text
requirements 7.5 / 9 / 10 / 11 / 12 / 13 / 14 / 15 / 16 / 18 / 20
DeterministicEvaluator 是否只读冻结结果，并且不是 LLM judge
root/affected/evidence ID/required types/schema/error/lineage 是否精确且 fail closed
confidence 是否被排除在主要评分外
Ground Truth 是否只在 Agent 完成后进入 evaluator
reset/inject 是否仅在管理平面，finally recovery 是否真实强制
一条 eval run 是否恰好一样本，三次是否独立且失败保留
六文件是否原子、不覆盖、可回读、跨文件 case/run 一致
trace/metadata/error surfaces 是否按冻结 source→sink 边界排除 prompt completion、隐藏推理、secret、原始工具 SQL、敏感路径或 provider exception；report 是否只展示结构化 Diagnosis 而未混入原始工具/provider 内容；不得要求对 Diagnosis 自由文本做无限语义分类
doctor 是否只读、有界、不以成功替代 eval
是否严格保持 OpenAI-compatible 模型和 M3 四工具 allowlist
是否偷做 P1 Kernel/baseline/消融/多案例或放宽 P0 分数
Jinja2 是否仅升格已锁定 3.1.6，无依赖漂移
Task 1–5 提交、tests、CI discovery、third_party 和 root Markdown 边界
```

Reviewer 必须给出文件/行号/复现命令、分类和 priority。只有满足冻结可达路径、复现证据及合同违反的 `BLOCKER` 才阻止交付；`LOCAL`/`BACKLOG` 记录后继续，`DECISION` 停止请用户选择。最终审查也只允许一次 BLOCKER 修复和一次针对原 finding 清单的定向复审；若仍有 BLOCKER，停止并报告，不派另一个 reviewer 重新开放全量审查。不 amend、不改写既有提交。

- [ ] **Step 5: 冻结本地交付证据并等待 push 授权**

```powershell
git log --oneline 96ad13c..HEAD
git status --short --branch
git diff --check
git diff --cached --name-only
git -C third_party/jaffle_shop status --short
```

向用户交付：精确 HEAD、Task 1–5 和审查/fix 提交列表、本地 Ruff/unit/integration/e2e/lock/help 结果、doctor 事实、三个真实 run ID 及 2/3 或 3/3 结果、产物路径、恢复状态、final review 结论、submodule 状态和 root Markdown 的 unstaged 边界。

Expected: 此时不 push。只有用户对 M5 HEAD 明确授权后才能推送；不得把 M4 的旧 push 授权当作 M5 授权。

- [ ] **Step 6: 用户授权后 push，并等待该精确 HEAD 的 Ubuntu CI**

```powershell
git push origin master
git rev-parse HEAD
git rev-parse origin/master
```

Expected: push exit 0，两个 hash 精确一致。必须实际观测 GitHub Actions Ubuntu 对该 HEAD 的 Ruff、unit、integration、常规 e2e 全部成功，才可把 M5/P0 标记为正式完成。CI 未完成或无法观测时只能报告“本地门槛通过，等待 Ubuntu CI”；CI 失败时保留原始失败事实并回到相关 Task，不用 retry 掩盖。

---

## M5/P0 最终完成门槛

- [ ] 用户已批准本计划并另行明确授权开始实施。
- [ ] `DeterministicEvaluator` 只读冻结 `DiagnosisRunResult` + `LabVerification` + Ground Truth + recovery fact，不调模型、工具、数据库或文件系统。
- [ ] 根因 code 和 affected asset 集合精确匹配，不同义词/模糊匹配/多报/漏报。
- [ ] cited evidence ID 全部存在、属于当前 run，三类必需证据已引用，且 P0 的 error/schema/lineage 内容与当前案例类型兼容。
- [ ] confidence 只展示，不改变主要评分；没有 LLM judge。
- [ ] Agent/Controller/M3 不 import/read/receive Ground Truth；Ground Truth 在 Diagnosis 冻结后才交给 Evaluator。
- [ ] `EvaluationRunner.run(case_id)` 是小 Interface/深 Implementation，每次独立执行 reset/inject/build/diagnose/recovery/evaluate/persist。
- [ ] 前置环境失败不伪造 Diagnosis；凡 M4 已返回 result，即使是 `INSUFFICIENT_EVIDENCE`、`MODEL_ERROR`、评分失败或 recovery 失败，六文件均完整保存。
- [ ] ArtifactWriter 使用每次 writer 唯一临时目录，只写固定 `artifacts/<run_id>`，原子发布、不覆盖、拒绝 symlink/path escape，写后全量回读校验；不同 run 可并发、同 run 至多一个成功，不实现全局锁协议。
- [ ] metadata/trace/evidence/diagnosis/evaluation/report 六文件合同与 requirements 一致，全部 case/run 交叉匹配。
- [ ] prompt 以 `m5.diagnosis.v1` 记录/hash，只补齐通用 root-cause ontology；不含 case ID、关系/列名、资产答案、expected evidence ID 或 Ground Truth digest。
- [ ] metadata 有 code revision、dirty bool、脱敏 model/config、prompt version/hash、UTC 时间、M4 metrics、evaluation/recovery 状态，不含凭据。
- [ ] trace 有有序工具事件、脱敏参数、evidence refs、每调用耗时和 error/gate，不含 prompt completion 或隐藏推理。
- [ ] 安全实现保持在冻结类型化边界与高置信哨兵内；没有新增 `output_safety.py`、SQL/DSN/指令自然语言分类器或 PID/token/stale-lock 管理器。
- [ ] Jinja2 3.1.6 是直接锁定依赖，报告模板通过 wheel resource 可读、`StrictUndefined`、确定性中文输出。
- [ ] `eval run CASE_ID` 恰好一样本，参数无自由 prompt/path/SQL/model/repeat/repair 扩展，失败先输出 artifact path 再非零退出。
- [ ] `doctor` 九项检查只读、有界、脱敏，真实最小探针证明一次工具调用 + strict output，并明确不代表 eval 通过。
- [ ] TestModel/unit 全部通过；真实 M2/M3 + FunctionModel + M5 integration 通过并恢复健康。
- [ ] `gemma4:e4b` 精确三个独立样本中至少两个完整通过；三个 run ID 唯一，所有失败保留且全部进入分母。
- [ ] 默认 CI/常规测试不请求 Ollama，opt-in 只用于本地明确真实验收。
- [ ] Agent 仍只有四个 M3 只读工具，没有 Shell、任意文件、自由 SQL、外部查询、数据库写、修复执行或 Ground Truth 工具。
- [ ] M1–M4 全量回归通过，submodule 固定且 clean，无 P1–P3 提前实现。
- [ ] 每个功能 Task 完成一次独立广度审查；若有 BLOCKER，至多一次修复和一次原清单定向复审。最终 M5 全 diff 审查无未关闭 BLOCKER/DECISION。
- [ ] `AGENT.md`、`README.md`、`mistake.md` 保持既定 unstaged/uncommitted/unpushed，运行产物仍 ignored，M5 计划已在 Task 1 作为跟踪文档提交。
- [ ] 用户明确授权 push 后，已实际观测远程 Ubuntu CI 对 M5 精确 HEAD 成功。
- [ ] README/简历表述只声称单案例 P0 工程闭环，不声称通用准确率、泛化、优于 Skill 或生产能力。

## 实施停止规则

遇到以下任一情况，保留脱敏证据、停止当前 Task 并向用户提供候选方案；不得擅自改变需求、依赖、安全边界或统计口径：

1. 六文件无法在不改动 Diagnosis 三终态或伪造诊断的前提下保存已产生的 M4 result。
2. Evaluator 只有在 reset 后重查数据库、调用模型或使用模糊语义评分才能通过。
3. 只有把 Ground Truth、唯一答案、expected evidence IDs 或 case 特有节点/列表注入 Agent prompt/schema/tools 才能达到 2/3。
4. 需要改动 `DiagnosisRunResult`、EvidenceRecord、M2 Ground Truth/verifier 或 M3 tools 的已批准语义，或需要超出 Task 2 已批准通用 ontology 补齐的其他 M4 行为变化。
5. 不引入新通用 workflow/Agent 框架、第二个 Agent 框架或升级已锁定核心依赖就无法继续。
6. Jinja2 直接依赖升格导致任何 resolved package 版本漂移。
7. ArtifactWriter 无法在 Windows/Ubuntu 同时实现固定路径、symlink containment、原子发布和不覆盖。
8. trace、metadata、evaluation、doctor 或报告的非 Diagnosis 部分暴露 API key、reader/admin 密码、DSN、原始工具 SQL、敏感绝对路径、provider 原始错误或隐藏推理；writer 不得以“脱敏”为名静默篡改 `diagnosis.json`。
9. `doctor` 只有启动/重启服务、修改 profile/config、写数据库或使用外部网络才能完成。
10. 真实三样本为 0/3 或 1/3，或三次未能全部产生可审计结果；不得自行增加第四次、换模型、改分母或放宽 evaluator。
11. Windows 与 Ubuntu 对 JSON/JSONL、排序、时区、原子 rename、Jinja 渲染或评分得出不同结论。
12. 任何修改会覆盖/stage/commit/push 根目录 `AGENT.md`、`README.md`、`mistake.md` 的用户内容，或修改 third-party submodule。
13. 独立审查产生 `DECISION`，或一次 BLOCKER 修复和原清单定向复审后仍有可复现 BLOCKER；不得自动开启第二轮开放式修复/重审。
14. push、远程操作、模型切换、全局/BIOS/注册表修改尚未得到用户对当前操作的明确授权。
15. reviewer 要求新增计划外安全分类器、锁/租约/崩溃恢复协议、生产 Module、prompt v2 或未冻结攻击语料；先归类为 BACKLOG/DECISION，不直接实现。

## 实施交接

本计划获批后仍需用户另行授权开始实施。主代理对冻结基线、Task 边界、finding 分类和停止决策负责；可直接完成只读调查、文档、局部低风险实现/修复、验证和提交，也可把边界清楚且值得独立执行的工作委托给 `luna_worker`。不要求为每次微小修复、提交或定向复审创建全新代理。

1. **新任务执行（本次重启推荐）**：在新隔离 worktree 从 `96ad13c` 开始，带入获批文档，按 Task 1–6 顺序推进。每阶段先冻结父提交/允许文件/验收/非目标，再选择主代理直接实现或委托 `luna_worker`。
2. **审查安排**：每个功能 Task 只安排一次独立广度审查；修复后的定向复审优先由同一 reviewer 依据原 finding 清单完成。达到预算后必须向用户交接，不得继续代理接力循环。
