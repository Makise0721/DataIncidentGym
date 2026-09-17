# T12 实施报告：公开证据义务规划器（离线部分）

- 日期：2026-09-17。范围：改进计划 T12 的离线交付（M20 合同）；**不含**真实模型测量、未见变体与新冻结排程。
- 依据：设计 [2026-09-17-evidence-obligation-planner-design.md](../specs/2026-09-17-evidence-obligation-planner-design.md)；
  需求 M20 与 §10.7。
- 交付（按切片提交）：`9353a7a` 计划校验层 → `4a0784f` 身份注册与 prompt → `ef779c5`/`27ac998`/`e95ec8b` 三轮审计修复 →
  `1f50026` 模型可见面与同源 schema → `81a5b4a` 描述入身份 → `efe3a68` runner 与规划器路径回归。
- 验证：`ruff check .` 通过；`git diff --check` 通过；全量单测 **825 passed / 5 skipped**
  （规划器相关 46 项：`test_evidence_planner.py` 41、`test_planner_runner.py` 5）。未调用真实模型，未运行数据库。

## 1. 机制 → 路径 → 断言映射

| 机制 | 路径 | 断言（回归） |
| --- | --- | --- |
| 计划 → 取证 → 提交 | `plan_step`（动作工具）返回真实收据；`submit_diagnosis`（终态输出工具）结束 run | `test_the_planner_loop_submits_a_confirmed_diagnosis`：`CONFIRMED`、4 次工具尝试、每步一条 `TOOL_CALL` 轨迹、终态事件含证据清单、2 条义务 `SATISFIED`、2 条仍 `OPEN` |
| 工具调用预算 | 第 9 次 `plan_step` 只由计划层判定 | `test_the_tool_budget_stops_the_ninth_step`：`PLAN_TOOL_BUDGET_EXHAUSTED` verdict、尝试数停在 8、拒绝只记一次 |
| 计划拒绝预算 | 两次非法声明后第三次（含合法声明）一律被挡 | `test_the_refusal_budget_blocks_steps_but_the_submission_still_lands`：`plan_refusals_used=2`、`plan_operations_blocked=1`、0 次工具尝试、提交仍被接受 |
| 被拒提交 | 伪造引用进入 `session.submit` | `test_a_refused_submission_fails_closed`：终态 `MODEL_ERROR`/`MODEL_PROTOCOL_ERROR`、会话 `cancel("RUN_FAILED")`、无证据记录 |
| 截止时间 | 脚本化模型超出 deadline | `test_a_deadline_overflow_ends_in_a_timeout_terminal`：终态 `MODEL_ERROR`/`MODEL_TIMEOUT`、会话 `cancel("STRATEGY_TIMEOUT")` |
| 校验拒绝不是收据 | 计划层全部拒绝路径 | `test_evidence_planner.py`：`PlanVerdict` 只带 `PLAN_*` 码、不携带证据、不消耗工具尝试；后端拒绝保留真实码且不属于 `PLAN_*` |
| 义务身份 | 全参数规范 JSON | 参数顺序无关、上游/下游分离、分隔符碰撞不可能、已关闭义务固定码 |

## 2. 实现要点

- **模型可见面**：`plan_step` 与 `close_obligation` 是动作工具（返回收据/`PlanVerdict`，不结束 run），
  `submit_diagnosis` 是唯一终态输出工具。三者的**注册 schema 与描述**都由注册后的 agent 读回并计入
  `evidence_planner_policy_identity()` 的 controller 载荷。
- **单一权威计数**：工具调用由 `PlannerController`（校验门）与 `StrategySession`（登记、计数、错误码）执行；
  SDK 只守模型回合数（`request_limit=8`）。此前用 SDK 的 `tool_calls_limit` 会让第 9 次调用以
  `UsageLimitExceeded` 结束整个 run，使 `PLAN_TOOL_BUDGET_EXHAUSTED` 不可达——现已由计划层判定，run 继续。
- **终态**：提交经 `session.submit`；被拒即 fail-closed 转 `MODEL_ERROR` 并取消会话；超时/用量/协议/运行时异常
  一律转固定码的 `MODEL_ERROR` 并 `cancel`（`STRATEGY_TIMEOUT` / `RUN_FAILED`）。
- **轨迹**：每个已执行步骤一条 `ToolTraceEvent`（指纹、证据 id、错误码、耗时来自计划层账本），末尾一条
  `DIAGNOSIS_TERMINAL`；权威计数仍取会话快照。

## 3. 未验证与边界

- **未做真实测量**：没有真实模型调用，因此没有能力收益、成本或 `pass^k` 数据；本报告只证明软件行为。
- **未接入既有排程**：规划器**未**进入 `MAIN_STRATEGIES`/`MODEL_STRATEGIES`，因此 `benchmark` 排程、报告分区与
  冻结 manifest 目前看不到它。接入需要在 `policy_surface_for_strategy` 路径上为该身份提供决策面（由规划器
  自己的 surface builder 承担），属新身份冻结前的最后一步，尚未实施。
- **未见变体未物化**：新变体仍是设计里的 dev 扩展回归集方案，需要一次有界的 Docker/PostgreSQL 实跑与单独授权。
- **T05 回放**：既有 9 条回放未因本轮改动而改变（全量单测含其回归）；规划器路径的机制覆盖见 §1，不代表
  回放库已覆盖规划器。
- 改动未提交到远程，未冻结新 manifest。
