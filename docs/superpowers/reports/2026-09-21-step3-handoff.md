# 交接文档：I1/I2 提交门 · 阶梯第 3 步（2026-09-21）

- 交接时 HEAD：`3109db7e294a10f3cd729dd081d934ebba21df13`（分支 `main`，未 push）。
- 工作树：**唯一未提交改动** `config/benchmark/p1-formal-v31.json`（有意保留的中间产物，见 §4）。
- 用途：让下一位执行者不必重读全过程即可接手；所有结论均指到归档/提交，可独立复核。

---

## 1. 任务是什么

验收阶梯第 2 步（有界真实验证）已完成并出报告；当前处于**第 3 步：身份升版 + 新身份测量**。
第 3 步计划：`docs/superpowers/plans/2026-09-21-step3-identity-measurement-plan.md`。
权威用户裁定与追加要求，见该计划 §3.4 / §3.5 与本文 §6。

## 2. 已完成（按提交顺序）

| 提交 | 内容 |
| --- | --- |
| `ed04be9` | **身份升版 + M24**：`STRATEGY_PROTOCOL_VERSION` v1→**v2**、`CONTROLLER_PROTOCOL_VERSION` v19→**v20**；`requirements.md` 追加 M24（两门语义、约束 1/2、身份口径、已知缺口）；钉值测试随批准演进（`p1-formal-v31`→`v32` 边界前移）；`APPROVED_MANIFEST_IDS` 追加 v31 |
| `bae3063` | v31 清单冻结（`chore: freeze …`，仅清单）。**绑定错误**，见 §4 |
| `fea2d09` | v31 冻结报告 `reports/2026-09-21-v31-freeze.md` |
| `a8729eb` `0330a92` | 补服务依赖测试发现与既有 e2e 基线 |
| `0dc2157` | **P1 更正**（重绑）与 **P2 撤回**（e2e 归因） |
| `193eb41` | P-1 授权边界精确化 |
| `ee21b57` | 回填 pre-gate 逐例对照 |
| `fc9cb74` | **门面缺陷修复**（关键，见 §3） |
| `893d398` | 报告记录修复与最终冻结顺序 |
| `0a5a7e2` | 门面接缝的**小型离线回归** + 报告两处收紧 |
| `3109db7` | **P-1 归档核心**：schema + 双路径接线（见 §5） |

**V1–V4 离线验收成立**（详见冻结报告 §3）：V1 升版前 v30 verify 通过、升版后 v23/v30 **仅**漂移
`controller_protocol_version` + `controller_protocol_sha256` 两项（`FIXED_RULE` 完全不动）；
V2/V3 v31 与 v30 逐字段对照，**唯一差异是 `implementation_revision`**；V4 单测/ruff/lock/diff 全绿。

## 3. 关键缺陷与修复：`ProtocolTools` 门面漏转发（`fc9cb74`）

**症状**：`tests/e2e/test_p1_policy_matrix.py` 中 8 例 `DIAGNOSTIC_KERNEL-*` 全部 `MODEL_REQUEST_LIMIT`。

**根因（已逐事件定位）**：`get_dbt_lineage` 被连续拒绝 `NODE_ARGUMENT_NOT_PROVEN` 7 次 → 预算耗尽。
拒绝原因是 kernel 的 `_validate_argument_provenance`（`diagnostic_kernel.py:323`）接受的集合
`_known_failed_nodes() | _known_lineage_nodes() | _lineage_node_candidates` 中第三项被清空——
`DiagnosisRunner.for_run` 未显式注入 `tools` 时（`diagnostic_agent.py:2588–2600`）改用
`session.tools_facade()`，而门面 `ProtocolTools`（`strategy_adapter.py:647`）**只实现六个协议工具
方法，未转发 `lineage_node_candidates`**，导致 `getattr(..., None)` 退化为空元组。

**精确表述（勿再简化）**：候选集为空**不等于**「任何节点都被拒」——已知失败节点与已发现 lineage
节点**仍可被接受**。该缺陷丢失的是「运行目录中可作为 lineage 起点的公开节点」这一维，
**当另两者都不覆盖目标起点时**才拒绝合法调用。本案例恰是（`failed_nodes` 为空 + 首次调用前无已发现节点）。

**修复**：在门面补只读转发（该投影**不是协议工具**：不耗预算、不登记证据、不产生收据，故不走 `_call`）。
**回归**：`tests/unit/test_strategy_adapter.py::test_facade_forwards_lineage_node_candidates_without_spending_budget`
断言候选与后端逐字一致、读取后 `tool_call_attempts` 不变、后端无解析器时退化为空元组。

**验收**：单例 FAILED→passed；全量矩阵 `8 failed, 30 passed` → **`38 passed`**；单测 1089→**1090**；ruff clean。

**效力边界（不得外推）**：
1. 八例转绿只证明这八条路径被覆盖，**不能**推断历史真实模型运行不受影响（真实模型可改道或先取
   run-results 建立失败节点集合）。
2. **v29/v30 既有结果原样保留**，不重跑、不改写、不据此重新定性。
3. 该修复改动实现修订，**必须纳入最终冻结**。

## 4. 当前唯一未提交改动：v31 重绑清单（中间产物）

- 已提交的 `bae3063` 绑定 `115299e`（批准提交 `ed04be9` 的**父**）。该绑定**无法启动正式运行**：
  `_verify_checkout`（`benchmark_runner.py:627–655`）要求 HEAD == 绑定修订，或绑定修订为祖先且
  `diff --name-only 绑定修订..HEAD` **恰为清单文件**；绑定 `115299e` 时该 diff 含 9 条路径。
- 工作树已重绑为 `ed04be9`，sha256 `1f7b20675bfaa3ff0719b654c6996929670539db64650917d740aecc7ffc253c`，
  并已用**真实** `_verify_checkout` 在独立干净 worktree 中验证 **PASSED**（同 checkout 上换回旧绑定
  复现 FAILED，具因果性）。逐字段比较确认**唯一变化是 `implementation_revision`**。
- **但这不是最终执行身份**（用户裁定）：P-1 与门面修复都改实现修订，须在 P-1 收口后绑定
  **含全部最终代码与合同变更**的提交重新冻结，随后**仅提交清单**，并在该清单提交的干净 worktree
  中**再次**验证检出门。
- **注意**：主分支在 `ed04be9` 之后已有多个报告提交，此刻提交清单会让
  `diff 绑定修订..HEAD` 含这些路径而**再次触发同一拒绝**。正确顺序见 §7。

## 5. P-1 进度：核心已落地，读取端与验收未做

**目标**：归档每次策略门拒绝对应的候选提交，使「是否误拒」可离线复核。

**已实现（`3109db7`）**：

- `diagnosis.py` 新增 `RefusalAudit`（`p1.refusal_audit.v1`）、`AuditClaimSummary`、
  `AuditUnresolvedSummary`；`EvidenceGateTraceEvent` 新增**加性可选**字段 `refusal_audit`。
- **逐 claim 计数显式完整**：`total_evidence_refs` / `unregistered_evidence_refs` /
  `truncated_evidence_refs` **无默认值**（必填 `Field(ge=0)`），满足「不能用默认零掩盖缺失」；
  恒等式 `total = len(evidence_ids) + unregistered + truncated`。
- **`recomputable` 由确定性代码判定 + 固定原因码**，四种：
  `RECOMPUTABLE_UNREGISTERED_REF`（未登记引用——逐 claim 计数即足以验证该拒绝条件，**不重建 claim**）、
  `RECOMPUTABLE_PROJECTED_CLAIM`、`NOT_RECOMPUTABLE_REDACTED_CLAIM_VALUE`（投影脱敏了 value）、
  `NOT_RECOMPUTABLE_TRUNCATED_REFS`（引用被上限截断）。
- **static 与 kernel 双路径均已接线**：静态 `output`（`Diagnosis`）与 kernel
  `_ProjectedKernelSubmission` 走同一 `_refusal_audit()` 构造器。
- `SubmissionPolicy` 新增只读 `scenario` 与 `applicable_claim_kinds` 属性。
- 未登记 ID 原文、自由文本、原始值**均未入档**；v1 归档字节与摘要未改写。

**踩过的两个坑（勿重犯）**：
1. 最初在诊断平面直接 import `APPLICABLE_CLAIM_KINDS_BY_EXPECTED_STATUS` 并读
   `scenario.expected_status` → `test_p1_isolation` 立即拦截（P1 隔离禁止诊断平面出现该字面量）。
   已改为知识留在 harness 侧 `SubmissionPolicy.applicable_claim_kinds`。
2. 审计构造器对 policy stub 不防御 → `AttributeError` 被泛化捕获、终态错成 `MODEL_RUNTIME_ERROR`。
   已改 `getattr(policy, "applicable_claim_kinds", None) or frozenset()`——**审计绝不能成为拒绝记录
   失败的原因**。

**尚未完成（下一轮的全部内容）**：

1. **读取端**：对 `recomputable` 做**再验证**（不得信任该布尔值）；历史记录缺字段 → **不可判定**；
   序列化重载后能正确复核。
2. **验收三分类**：**可复核且正确／可复核但误拒／不可判定**，且**禁止把不可判定算作正确**；
   收口标准是**分类准确、双路径完整留痕、历史兼容成立**，**不是**零不可判定。
3. **五类回归**：未登记引用、已登记但不支撑、混合引用、合法提交、序列化重载。
4. **历史兼容**：确认严格加载对 v1 归档仍成立、缺字段**不得默认成零**。
5. **离线复核的前提**（勿用后来补采的证据倒推当时判定）：使用**拒绝发生时**的证据集合与
   **工具轨迹前缀**；`ScenarioSpec`（含 expected status 与 observable evidence contract）取自归档
   `.dig/scoring-inputs/<run_id>/evaluation_inputs.json`（已确认存在），非 trace。
6. **登记待办**：v29/v30 等历史归档的**缺陷暴露面离线检查**——逐一核对是否出现
   `NODE_ARGUMENT_NOT_PROVEN` 且其后耗尽或改道。只作登记，不改写既有结果。

**已裁定不采用**：把被拒 claim 原样保存（方案 b）——不因追求全覆盖而扩大归档内容范围；
且 (b) 也不能保证每次可复核（仍需拒绝时的证据集合、轨迹前缀、合同与判定代码身份一致）。

## 6. 用户裁定与纪律（务必遵守）

1. **修门面，不改脚本绕过合法节点**；成功断言保留——不得把预期改成接受 `MODEL_REQUEST_LIMIT`。
2. **不静默改历史结论**：修订须在报告头部加修订记录（已有先例格式），正文标注撤回/更正。
3. **密钥**：只经进程环境映射，不落盘、不输出、不入报告；本机 `COMMANDCODE_API_KEY` 不在 harness
   进程内，须用 User 作用域回退（上批已验证）。`PYTHONIOENCODING=utf-8` 从首条命令就带。
4. **provider 异常**：按 runner 行为停机、如实归因，**不自行换端点或重试**。
5. **红线**：不 push；不改写历史归档；不重跑失败格；manifest 未获批不冻结；真实模型测量单独放行。
6. **`git stash` 教训**（本会话真实事故）：多暂存共存时 `stash@{0}` 未必是你的条目。我做 e2e 对照时
   第三次 `push`/`pop` **误 pop 了所有者的 `stash@{0}`**，污染工作树（`README.md` 被写入 +58 行、
   `AGENT.md`/`mistake.md` 冲突）。已完全恢复：`README.md` 还原 HEAD、两个未跟踪文件内容保留、
   **所有者暂存仍完整**（`stash@{0}` = `pre-main-sync-20260830 user workspace files`，
   提交 `e8b1584b4f37fa153c481e8a52d30e0d82592565`）。**后续探针一律用 `git worktree`**，
   用完 `git worktree remove` + `prune`（本会话留有先例，探针 worktree 已全部清理）。

## 7. 下一步顺序（用户给定，不再展开）

**P-1 收口（读取端 + 三分类验收 + 五类回归）→ 集中验证 → 最终冻结与检出门验证 → 真实模型测量单独放行。**

最终冻结要点：
- 绑定**含 P-1 与门面修复**的提交（`_verify_checkout` 的约束见 §4）；
- **仅提交清单**，`diff 绑定修订..HEAD` 必须恰为该清单文件；
- 在该清单提交的**独立干净 worktree** 中跑**真实** `_verify_checkout` 验证通过
  （可复用本会话的验证脚本形态，见冻结报告 §3.6）。

## 8. 常用命令与验证基线

```powershell
uv run pytest tests/unit -q                 # 基线：1090 passed, 5 skipped
uv run ruff check .                         # 基线：All checks passed!
uv lock --check
git diff --check
uv run pytest tests/e2e/test_p1_policy_matrix.py -q   # 基线：38 passed
uv run data-incident-gym benchmark verify --manifest config/benchmark/p1-formal-v31.json
```

- `tests/integration` 有**间歇性**失败（失败集合漂移；dbt 子进程 `3221225477`/`0xC0000005` 是本仓
  **已记录的已知本机不稳定**，见 `reports/2026-08-30-m7-development-smoke.md`；处置纪律是
  **不据此修改产品或加平台 workaround**）。单例重跑通常通过。数据库现已启动（PG 容器 55432）。
- `pipeline build` 只读，会刷新 `.dig/baseline-summary.json`（指纹须恒等于
  `e5c7848eb2b7af16ec37463650b63dbb5d1f52293adf456003517b51c496cb18`）。

## 9. 关键文件

| 路径 | 说明 |
| --- | --- |
| `docs/superpowers/plans/2026-09-21-step3-identity-measurement-plan.md` | 第 3 步计划；§3.4 误拒判定纪律、§3.5 P-1 授权边界 |
| `docs/superpowers/reports/2026-09-21-v31-freeze.md` | v31 冻结报告；§3.6 绑定更正、§3.7.2 门面缺陷与效力边界 |
| `docs/superpowers/reports/2026-09-21-canary-retest.md` | 金丝雀复测（2/2 PASSED 且零拒绝） |
| `docs/superpowers/reports/2026-09-21-submission-gates-bounded-verification.md` | 9 格有界验证 |
| `src/data_incident_gym/diagnosis.py` | `RefusalAudit` / `EvidenceGateTraceEvent` |
| `src/data_incident_gym/diagnostic_agent.py` | `_refusal_audit()` 与两处接线 |
| `src/data_incident_gym/submission_policy.py` | `scenario` / `applicable_claim_kinds` |
| `src/data_incident_gym/strategy_adapter.py` | `ProtocolTools.lineage_node_candidates` |
| `config/benchmark/p1-formal-v31.json` | v31 清单（工作树为 ed04be9 重绑版，未提交） |
