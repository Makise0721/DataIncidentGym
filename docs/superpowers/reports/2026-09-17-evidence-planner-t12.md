# T12 实施报告：公开证据义务规划器（离线部分）

- 日期：2026-09-17。范围：改进计划 T12 的离线交付（M20 合同）；**不含**真实模型测量、未见变体与新冻结排程。
- 依据：设计 [2026-09-17-evidence-obligation-planner-design.md](../specs/2026-09-17-evidence-obligation-planner-design.md)；
  需求 M20 与 §10.7。
- 交付（按切片提交）：`9353a7a` 计划校验层 → `4a0784f` 身份注册与 prompt → `ef779c5`/`27ac998`/`e95ec8b` 三轮审计修复 →
  `1f50026` 模型可见面与同源 schema → `81a5b4a` 描述入身份 → `efe3a68` runner 与规划器路径回归 →
  `b25bfb5` 关闭详情与完整义务状态入归档 → `8ba8651` 归档汇总只计被接受的关闭 → 本轮 benchmark 接入
  （`MODEL_STRATEGIES` 注册、决策面委派、工厂路由、runner 构造与客户端生命周期）。
- 验证：`ruff check .` 通过；`git diff --check` 通过；全量单测 **835 passed / 5 skipped**
  （规划器相关 54 项：`test_evidence_planner.py` 42、`test_planner_runner.py` 12；另有
  `test_diagnostic_agent.py`、`test_benchmark_runner.py` 与 `test_benchmark_manifest.py` 的接线
  回归）。未调用真实模型，未运行数据库。

## 1. 机制 → 路径 → 断言映射

| 机制 | 路径 | 断言（回归） |
| --- | --- | --- |
| 计划 → 取证 → 提交 | `plan_step`（动作工具）返回真实收据；`submit_diagnosis`（终态输出工具）结束 run | `test_the_planner_loop_submits_a_confirmed_diagnosis`：`CONFIRMED`、4 次工具尝试、每步一条 `TOOL_CALL` 轨迹、终态事件含证据清单、2 条义务 `SATISFIED`、2 条仍 `OPEN` |
| 工具调用预算 | 第 9 次 `plan_step` 只由计划层判定 | `test_the_tool_budget_stops_the_ninth_step`：`PLAN_TOOL_BUDGET_EXHAUSTED` verdict、尝试数停在 8、拒绝只记一次 |
| 计划拒绝预算 | 两次非法声明后第三次（含合法声明）一律被挡 | `test_the_refusal_budget_blocks_steps_but_the_submission_still_lands`：`plan_refusals_used=2`、`plan_operations_blocked=1`、0 次工具尝试、提交仍被接受 |
| 被拒提交（可重试） | 输出校验阶段交给会话判定 | `test_a_refused_submission_can_be_retried`：伪造引用先被拒（`output_retries_used=1`），随后合法弃答被接受 |
| 提交拒绝预算耗尽 | 一直提交伪造引用 | `test_a_run_of_refused_submissions_fails_closed`：`MODEL_ERROR`/`MODEL_PROTOCOL_ERROR`、`output_retries_used=2`、会话 `cancel("RUN_FAILED")`、`model_requests>=2` 且 `output_tokens>0` |
| 计划事件归档 | 计划拒绝/关闭/开放义务 | `test_plan_events_are_archived_without_counting_as_tool_calls`：`PLAN` 事件逐条保留且不进工具记录 |
| 归档可复核性 | 同一义务分别 SATISFIED / REVOKED | `test_the_archive_distinguishes_a_satisfied_from_a_revoked_close`：两条路径序列化后不同；经 `TraceEvent` 联合类型重载后，`plan_outcome_summary` 仍能重建满足/撤销/开放计数与「已满足义务是否被最终引用覆盖」，不依赖内存中的 controller |
| 被拒关闭不计入已关闭 | 关闭请求携带非本次调用返回的证据被拒后重试成功，归档重载 | `test_a_refused_close_does_not_count_as_closed_after_reload`：`closed_by_outcome` 只统计接受的关闭（2），请求次数单列于 `requested_by_outcome`（3），`refused_closes=1`，义务状态与计数一致 |
| 截止时间 | 脚本化模型超出 deadline | `test_a_deadline_overflow_ends_in_a_timeout_terminal`：终态 `MODEL_ERROR`/`MODEL_TIMEOUT`、会话 `cancel("STRATEGY_TIMEOUT")` |
| 校验拒绝不是收据 | 计划层全部拒绝路径 | `test_evidence_planner.py`：`PlanVerdict` 只带 `PLAN_*` 码、不携带证据、不消耗工具尝试；后端拒绝保留真实码且不属于 `PLAN_*` |
| 义务身份 | 全参数规范 JSON | 参数顺序无关、上游/下游分离、分隔符碰撞不可能、已关闭义务固定码 |
| 决策面与排程注册 | `policy_surface_for_strategy` 委派 `planner_policy_surface()`；`MODEL_STRATEGIES` 纳入该身份 | `test_the_standard_surface_path_returns_the_planner_surface`：身份与模型可见三工具载荷同源、Diagnosis 摘要与其他面一致；`test_the_planner_is_scheduled_but_not_frozen_into_old_identities`：不在 `MAIN_STRATEGIES`/`KERNEL_STRATEGIES`/`FROZEN_POLICY_STRATEGIES` |
| benchmark 工厂路由 | `diagnosis_factory` 按策略分派 | `test_benchmark_factory_routes_the_planner_strategy`：规划器 cell 走 `EvidencePlannerRunner.for_run`（run ID、绑定后的 settings 与项目根原样传递）；`test_diagnostic_agent.py`：`DiagnosisRunner.for_run` 对该身份显式拒收 |
| runner 构造与客户端生命周期 | `for_run` 无注入模型时按 settings 组装 OpenAI 兼容模型并持有客户端 | `test_for_run_without_a_model_builds_the_settings_model`：身份与申报一致、客户端被持有；`test_diagnose_closes_the_owned_settings_client`：run 结束即关闭；`test_injecting_a_model_requires_an_identity`：注入模型必须带身份 |

## 2. 实现要点

- **模型可见面**：`plan_step` 与 `close_obligation` 是动作工具（返回收据/`PlanVerdict`，不结束 run），
  `submit_diagnosis` 是唯一终态输出工具。三者的**注册 schema 与描述**都由注册后的 agent 读回并计入
  `evidence_planner_policy_identity()` 的 controller 载荷。
- **单一权威计数**：工具调用由 `PlannerController`（校验门）与 `StrategySession`（登记、计数、错误码）执行；
  SDK 只守模型回合数（`request_limit=8`）。此前用 SDK 的 `tool_calls_limit` 会让第 9 次调用以
  `UsageLimitExceeded` 结束整个 run，使 `PLAN_TOOL_BUDGET_EXHAUSTED` 不可达——现已由计划层判定，run 继续。
- **终态**：提交在 **agent 输出校验阶段**经 `session.submit`，被拒即作为可重试反馈交回模型（预算由会话自己的
  提交拒绝计数 `output_retry_limit` 约束，与计划拒绝计数互不影响）；重试耗尽后才 fail-closed 转 `MODEL_ERROR`
  并取消会话。超时/请求上限/协议/运行时异常一律转固定码的 `MODEL_ERROR` 并 `cancel`（`STRATEGY_TIMEOUT` / `RUN_FAILED`）。
- **用量**：整个 run 使用同一个 `RunUsage` 累加器，成功与失败出口都读它——失败运行报告的是真实发生的请求与
  token，不再出现"已请求但记 0"。
- **计划事件**：新增 `PlanTraceEvent`（`kind` = `STEP`/`CLOSE`/`STATE`）独立记录计划拒绝、义务关闭与提交时仍
  开放的义务，**不计入** `ToolTraceEvent`。
- **轨迹**：每个已执行步骤一条 `ToolTraceEvent`（指纹、证据 id、错误码、耗时来自计划层账本），末尾一条
  `DIAGNOSIS_TERMINAL`；权威计数仍取会话快照。

## 3. 未验证与边界

- **未做真实测量**：没有真实模型调用，因此没有能力收益、成本或 `pass^k` 数据；本报告只证明软件行为。
- **排程可见但未被排程**：规划器已进入 `MODEL_STRATEGIES`，benchmark 工厂按策略路由到
  `EvidencePlannerRunner`，`policy_surface_for_strategy` 返回规划器自己的决策面。但它不在
  `MAIN_STRATEGIES`，也**不在 `FROZEN_POLICY_STRATEGIES`**；回归逐文件确认落盘 manifest 的
  cells/policies 均不含规划器（`test_no_frozen_manifest_file_schedules_the_planner`）。规划器真正
  进入排程需要新的 manifest 身份获批冻结（届时才决定它在排程与报告中的位置），尚未实施。
- **历史 manifest 与当前源码不整体兼容（既有状态，实测口径）**：本轮实测全部 15 个落盘 manifest，
  在当前 HEAD 上 `verify_manifest` 均不通过——v1 因 schema 演进（formal model 字面量）载入即失败；
  v9–v20 连策略面也与当前源码漂移；v21/v22 的六策略面仍与当前源码逐项一致，仅 `result_inputs` 自
  T02/T03 将 evaluator 升至 `p1.evaluator.v3`（`57960ad`，晚于 v22 冻结、早于 T12）后漂移，且
  v22 冻结修订（`40c7e82`）上的 evaluator 摘要与其冻结值逐字节吻合。这是 README「正式基准与证据
  边界」描述的设计内保护（防止把新实现误记为旧批次），与规划器接入无关；历史结果的重算与校验
  必须在各自冻结修订上执行。
- **未见变体未物化**：新变体仍是设计里的 dev 扩展回归集方案，需要一次有界的 Docker/PostgreSQL 实跑与单独授权。
- **T05 回放**：既有 9 条回放未因本轮改动而改变（全量单测含其回归）；规划器路径的机制覆盖见 §1，不代表
  回放库已覆盖规划器。
- 改动未提交到远程，未冻结新 manifest。
