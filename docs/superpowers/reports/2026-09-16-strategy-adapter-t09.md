# T09 实施报告：统一策略接入协议

- 日期：2026-09-16。基线 `e664b8e` + 未提交工作树（T01–T08 交付之上）。
- 范围：[改进计划](../plans/2026-09-15-research-driven-improvement-plan.md) T09；依赖 T01、T04。
- 交付：
  - `src/data_incident_gym/strategy_adapter.py`：协议 `p1.strategy_adapter.v1`——
    `StrategyDeclaration`（申报）、`TaskContext`（公开任务上下文）、`ToolRequest`/`ToolReceipt`、
    `FinalSubmission`/`SubmissionReceipt`、`CancellationReceipt`、`ProtocolError`（统一错误信封）、
    `StrategySession`（harness 侧唯一入口）、`ProtocolTools`（内置薄门面）、`same_condition`
    （严格同条件判定）。
  - `examples/external_strategy_client.py`：外部脚本参考客户端，确认/弃答/健康三条路径。
  - 规范 `docs/superpowers/specs/2026-09-16-strategy-access-protocol.md`；需求 M17 修订行 +
    §10.5。
- 验证：`ruff check .` 通过；单测 **751 passed / 4 skipped**（T09 新增 26 项：22 项协议 +
  4 项客户端）；`uv run python examples/external_strategy_client.py` 三条路径全部
  `accepted=True`；`git diff --check` 通过（仅 AGENTS.md 既有 CRLF 提示）。全程无数据库、
  无真实模型。

## 职责边界（与计划一致）

harness 独占 run ID、白名单、预算 8/8/2/300、时限与证据登记；策略只有五个操作
（`task_context` / `call_tool` / `submit` / `cancel` / `report_usage`），自报计数仅记录为辅助
（`snapshot()` 明示"harness counters are authoritative"）。外部策略可以用自己的规划循环，
但引用只能来自本 run 已登记的证据——编造或替换 evidence ID 在 `submit` 处被拒
（`EVIDENCE_NOT_REGISTERED`），登记本身由 harness 完成，无法跳过。

## 关键规则与固定错误码

| 规则 | 行为 |
| --- | --- |
| 未知工具 / 越权工具 | `UNKNOWN_TOOL` / `TOOL_NOT_ALLOWLISTED`，在预算外拒绝（不消耗预算） |
| 参数集或 run 作用域不符 | `TOOL_ARGUMENT_INVALID`（run 作用域参数必须属于本会话） |
| 超出工具预算 | `TOOL_BUDGET_EXHAUSTED`；到达后端的每次调用都计入 harness 计数 |
| 后端拒绝 | **保留真实错误码**（如 `RELATION_NOT_ALLOWED`）作为收据，不用协议码覆盖 |
| 重复调用 | 返回同一 evidence ID，`duplicate=True`，不产生新事实 |
| 提交引用未登记 | `EVIDENCE_NOT_REGISTERED`（含编造与替换） |
| 终态结构违约 | `SUBMISSION_INVALID`，细节不泄露私有期望 |
| 二次提交 / 已取消 | `SUBMISSION_ALREADY_FINAL` / `SESSION_CLOSED` |
| 申报未授予的工具 | 构造会话即拒（`DECLARATION_INVALID`）——申报不等于授权 |

## 验收对照

- **内置等价**（`test_builtin_facade_is_equivalent_to_direct_tools`）：`FixedRuleRunner` 直连
  与经 `ProtocolTools` 门面在同一个确定性后端上运行，断言 `diagnosis`、`evidence_records`、
  轨迹形状（工具名/参数/指纹/evidence IDs/错误码）、metrics（除 elapsed）逐项相等，并用
  `DeterministicEvaluator` 对两个结果以同一场景+验证分别评分，断言
  `status`/`failed_check_codes`/`checks` 完全一致——"诊断、计数与检查结果等价"三项齐备。
  会话快照同时证明每次调用都走了协议（attempts == runner 计数）。
- **后端拒绝透传**（`test_builtin_facade_preserves_backend_refusals`）：门面重抛原始
  `RelationNotAllowedError`，runner 自身的错误分类不变；会话侧收据记录
  `RELATION_NOT_ALLOWED: 1`。
- **三条路径**（`examples/external_strategy_client.py` + 3 项测试）：确认（根因+资产 claim）、
  弃答（以真实 `RELATION_NOT_ALLOWED` 收据支撑缺口声明）、健康（health claim）；每条路径的
  attempts ≤ 8、单次提交、无取消。
- **申报与对照**（`test_same_condition_requires_identical_capabilities`）：模型、可见上下文或
  授予白名单任一不同即不构成严格同条件对照。

## 审计整改（第二轮，2026-09-16）

- **模型 runner 终态未经过会话（P1）**：`DiagnosisRunner._result` 直接返回诊断、异常路径也不关闭会话，
  复现为"返回 INSUFFICIENT_EVIDENCE 后 `session.final_diagnosis` 为 None，且会话工具仍 `accepted=True`"。
  现：正常终态经 `_submit_through_session` 落账（用会话返回的 Diagnosis，拒绝即 fail-closed 转
  MODEL_ERROR 兜底）；异常终态经 `_close_session` 关闭——超时 `STRATEGY_TIMEOUT`、协议/运行时错误与
  兜底 `RUN_FAILED`（`CancellationReason` 新增该码）。终态之后工具调用返回 `SESSION_CLOSED`。
  回归（正常 `for_run` 入口，离线 `FunctionModel`）：
  `test_model_runner_normal_terminal_goes_through_the_session`（static）、
  `test_kernel_normal_terminal_goes_through_the_session`（kernel，含联合接口所需的决定性 profile 尝试）、
  `test_model_runner_failure_terminals_close_the_session`（static/kernel 的 MODEL_ERROR 与预算耗尽路径）。
  三条路径都断言"返回结果与会话终态一致 + 终态后工具调用被拒"。

## 审计整改（第一轮，2026-09-16）

- **外部策略拿不到证据内容（P1）**：`ToolReceipt` 原先只返回 evidence ID，客户端只能"提交预设
  答案"。现收据携带本次返回的 `EvidenceRecord`（**公开事实内容**：类型、主题、事实载荷、
  内容摘要），且客户端重写为**依据事实诊断**——从 run 事实读 `run_status`、从 profile 事实读
  空值列、从 lineage 事实取下游模型；决定性事实改变（run FAILED→SUCCEEDED、决定性 profile
  被拒）后，同一条代码路径给出不同终态。回归：
  `test_receipt_carries_the_public_evidence_content`、
  `test_client_conclusion_follows_the_decisive_fact`（三种事实组合给出三种不同终态）。
- **8/8/2/300 未完整执行（P1）**：原先只执行工具调用预算。现补齐：模型请求经
  `acquire_model_request` 配额门（第 9 次领取返回 `MODEL_REQUEST_BUDGET_EXHAUSTED`）；
  被拒提交消耗输出重试预算（复现"5 次无效提交后仍能成功提交"已不可行：2 次后返回
  `OUTPUT_RETRY_EXHAUSTED`）；所有公开操作检查截止时间（可注入时钟，300 秒后返回
  `DEADLINE_EXCEEDED`）。**诚实边界**：外部进程内的模型调用无法被 harness 观测，v1 强制的是
  领取门，自报请求数超过已领取槽位在 `snapshot.usage_violation` 标记为违规，不写作"已受控"。
  回归：`test_model_request_budget_is_enforced_at_the_gate`、
  `test_invalid_submissions_consume_the_retry_budget`、`test_deadline_is_enforced_with_the_harness_clock`。
- **内置 runner 未实际接入（P1）**：生产代码原先不引用协议。现 `FixedRuleRunner.for_run`
  （`ReferenceAnalystRunner` 继承）与 `DiagnosisRunner.for_run` 在构造时自行建立会话、把工具替换
  为 `ProtocolTools` 门面；固定规则族的最终提交经 `session.submit` 落账并使用会话返回的
  Diagnosis（拒绝即 fail-closed）。回归通过**正常入口**验证：
  `test_production_for_run_routes_tools_and_submission_through_the_session`
  （`for_run` → `diagnose()`：`_tools` 是门面、`session.final_diagnosis == result.diagnosis`、
  会话计数与 runner 计数一致）、
  `test_model_runner_factory_attaches_the_session`（模型 runner 的申报带真实模型身份）。
  `safe_error_code` 上移到 `evidence.py` 以消除 fixed_rule ↔ strategy_adapter 的循环依赖。

## 边界与未做

- 本协议是进程内接口；跨进程传输（T10 MCP）与隔离/防泄漏验收（T11）另行交付，T10 不得改变
  本协议语义与错误码。
- 等价回归覆盖 FIXED_RULE 内置路径；REFERENCE_ANALYST 与 kernel 策略的接入沿用同一门面机制，
  其真实链路验证随 T10/T11 的端到端进行（需要数据库与外部进程）。
- 真实重复可靠性测量与新 manifest 冻结保持独立（T08 结论仍为"离线实现已验证"）。
- 改动未提交 git。
