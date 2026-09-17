# 统一策略接入协议 `p1.strategy_adapter.v1`

- 日期：2026-09-16。范围：[改进计划](../plans/2026-09-15-research-driven-improvement-plan.md) T09；
  依赖 T01（评分输入合同）、T04（公开参考解）。
- 实现：`src/data_incident_gym/strategy_adapter.py`；外部参考客户端
  `examples/external_strategy_client.py`；回归 `tests/unit/test_strategy_adapter.py`、
  `tests/unit/test_external_strategy_client.py`。
- 状态：进程内协议已实现并验证；传输层（T10 MCP）与隔离执行（T11）不在本协议内。

## 1. 职责边界

harness 独占保管：run ID、工具白名单、预算（8 model 请求 / 8 工具调用 / 2 重试 / 300 秒）、
时限与证据登记。策略（内置或第三方）只能通过五个操作行动，自报成本与计数一律只是辅助信息，
权威计数来自 harness。

## 2. 五个操作与信封

| 操作 | 请求 | 应答/信封 |
| --- | --- | --- |
| `task_context` | — | `TaskContext`：协议版本、run ID、公开 incident brief、关系白名单、工具白名单、预算、申报回显。**不含**私有场景合同、期望答案或 case ID。 |
| `call_tool` | `ToolRequest`（request_id、工具名、参数） | `ToolReceipt`：accepted、evidence_ids、**evidence（本次返回的公开证据记录，含事实内容）**、duplicate 标记、错误信封（code/detail/request_id）、已用工具调用数。 |
| `acquire_model_request` | — | 模型请求配额：调用模型前必须先领取一个槽位；超限返回 `MODEL_REQUEST_BUDGET_EXHAUSTED`。 |
| `submit` | `FinalSubmission`（终态、summary、根因、资产、引用、claims、缺口、置信度；run ID 由 harness 填写） | `SubmissionReceipt`：accepted + 构造好的 `Diagnosis`，或错误信封。 |
| `cancel` | 固定原因（`STRATEGY_CANCELLED` / `STRATEGY_TIMEOUT` / `RUN_FAILED` / `HARNESS_SHUTDOWN`；`RUN_FAILED` 用于 MODEL_ERROR 类异常终态） | `CancellationReceipt`；会话随即关闭。 |
| `report_usage` | 任意自报计数 | 仅记录，标注为辅助；权威计数在 `snapshot()`。 |

## 3. 工具调用规则

- 未知工具 → `UNKNOWN_TOOL`；不在白名单 → `TOOL_NOT_ALLOWLISTED`；两者在预算之外被拒。
- 参数集与工具签名不符，或 run 作用域参数（`run_id`）不属于本会话 → `TOOL_ARGUMENT_INVALID`；
  这类"工具名合法但请求非法"的调用仍计一次尝试（与超预算拒绝一样进入 harness 计数），只有未知
  工具与越权工具在门外免费拒绝。
- 超出工具预算 → `TOOL_BUDGET_EXHAUSTED`；每次到达后端的调用（含被拒绝的调用）都计入
  harness 计数。
- 后端拒绝**保留真实错误码**（如 `RELATION_NOT_ALLOWED`）作为收据，不用协议码覆盖。
- 成功调用的记录由 harness 登记；重复调用返回同一 evidence ID 并标记 `duplicate`，不产生新事实。
- 策略不能跳过登记或更换 evidence ID：`submit` 只接受已登记的引用。
- **8/8/2/300 全部由 harness 执行**：工具调用在边界处计数并拒绝；模型请求经 `acquire_model_request` 配额门；被拒的 `submit`（`EVIDENCE_NOT_REGISTERED`、`SUBMISSION_INVALID`）消耗输出重试预算，耗尽后提交返回 `OUTPUT_RETRY_EXHAUSTED`；所有公开操作先检查截止时间（`DEADLINE_EXCEEDED`，时钟可注入以便测试）。
- **诚实边界**：外部进程里的模型调用本身不可被 harness 观测，因此 v1 强制的是领取门；自报 `model_requests` 超过已领取槽位在 `snapshot.usage_violation` 标记为违规，而不是被当作已受控计数。内置模型策略的请求数由现有 controller 观测。

## 4. 最终提交规则

- 引用了任何工具未返回的 evidence ID → `EVIDENCE_NOT_REGISTERED`（含编造与替换）。
- 终态结构不满足 `Diagnosis` 合同（如 CONFIRMED 需唯一根因 claim 与资产 claim 投影一致）→
  `SUBMISSION_INVALID`，错误细节不泄露私有期望。
- 提交成功后再次提交 → `SUBMISSION_ALREADY_FINAL`；已取消的会话 → `SESSION_CLOSED`。
- 一个会话最多一次最终提交或一次取消。

## 5. 内置接入与等价

**生产工厂即接线点**：`FixedRuleRunner.for_run`（含 `ReferenceAnalystRunner`）与 `DiagnosisRunner.for_run` 在构造时自行建立会话并把工具替换为门面。**两类 runner 的终态都经过会话**：正常终态（CONFIRMED / INSUFFICIENT_EVIDENCE / NO_INCIDENT）经 `session.submit` 落账并使用会话返回的 Diagnosis（拒绝即 fail-closed，转 MODEL_ERROR 兜底）；异常终态（超时→`STRATEGY_TIMEOUT`、协议/运行时错误与兜底→`RUN_FAILED`）经 `session.cancel` 关闭会话。任何终态之后工具调用返回 `SESSION_CLOSED`。回归通过正常入口（`for_run`）验证 static 与 kernel 两条路径的结果与会话终态一致。

内置策略通过 `ProtocolTools` 薄门面接入：门面实现六个只读工具的 `EvidenceTools` 形状，把
runner 的每次调用路由进会话。门面对合规 runner 是透明的——成功调用返回相同记录、后端拒绝
重抛原异常——因此内置 runner 经门面运行与直连运行产生相同的诊断、证据、计数、轨迹形状与
evaluator 检查结果（回归：`test_builtin_facade_is_equivalent_to_direct_tools`，
其中 evaluator 用同一场景与验证对两个结果分别评分并逐项比对）。

## 6. 申报与严格同条件对照

策略开工前必须 `StrategyDeclaration`：框架、框架版本、模型提供方与名称、是否确定性、额外
工具申报、可见上下文申报。额外工具申报不等于授权——harness 只授予六个只读工具，申报未授予
的工具直接 `DECLARATION_INVALID` 拒绝。两个会话只有在申报、授予的白名单、预算与可见上下文
完全一致时才可标记为严格同条件对照（`same_condition`）；能力不同的策略只能作为能力差异
比较，不得写入同条件结论。

## 8. MCP 入口（T10）

`src/data_incident_gym/mcp_server.py` 把六个只读证据工具接到 MCP stdio 传输上；`mcp==1.20.0`
是锁定的 SDK 版本（`pyproject.toml`），构建与调用只使用该版本已核对的 API
（`FastMCP.tool` / `run_stdio_async`、`mcp.client.stdio.stdio_client`、`ClientSession`）。

- **只暴露六工具**：`tools/list` 恰好返回六个业务工具，参数名与证据工具一致；**没有提交工具**，
  最终提交仍走本文档 §4 的协议（进程内或 T11 的隔离安排）。
- **能力只收窄**：`--tools a,b` 只允许从六工具中取子集，未知工具直接拒绝启动；场景/策略的
  关系白名单仍由后端证据工具强制，MCP 不会放宽它。
- **同一会话、同一规则**：每次 `tools/call` 经同一个 `StrategySession`，白名单、run 作用域、
  预算门、截止时间、证据登记、错误码与证据载荷与进程内门面逐项一致（回归
  `test_receipts_match_the_in_process_facade`）。
- **参数原样投递**：服务端用 SDK 低层 `Server`（`validate_input=False`）注册工具，`tools/list`
  只声明契约（规范参数名、`string` 类型、`required`、`additionalProperties: false`），但入参不做
  校验、清洗或补默认值——缺参、多参、类型错误、外来 run 都由会话判定，与进程内调用得到**同一个**
  `TOOL_ARGUMENT_INVALID` 收据并**同样计一次尝试**。让 SDK 的 schema 层先拒绝或丢弃参数会绕过统一
  收据与预算，因此显式关闭；未知工具名仍按"门外免费拒绝"返回 MCP 工具错误（不计尝试、无收据）。
- **单 run 串行化**：一个进程只服务一个 run 的一个会话，`ToolGateway` 用 asyncio 锁串行执行
  调用，请求顺序、登记顺序与计数确定；不因吞吐引入跨 run 状态。
- **真实拒绝收据**：被拒调用保留后端错误码（如 `RELATION_NOT_ALLOWED`），收据携带真实返回的
  证据记录。
- **无额外能力**：不提供 dbt CLI、不执行 SQL、无管理命令、无通用文件系统访问。
- 断连不改变 harness 侧状态：会话归 harness 所有，传输关闭后仍可继续在其上提交或取消。

## 7. 隔离进程的桥接与提交通道（T11）

- **harness 独占会话**（`strategy_bridge.serve_child_session`）：策略进程（容器或本地解释器）的
  stdio 承载 MCP，桥接在 harness 进程内把每次 `tools/call` 送进**唯一**的会话，随后把本次收到的
  请求与收据追加到 harness 权威日志（`ToolGateway.recorder`）。日志写在沙箱之外：客户端既看不到也
  改不了它。客户端自己的日志只是辅助：丢失、裁剪或写入假数字都不改变 harness 的计数与登记，
  被拒调用（含 `TOOL_BUDGET_EXHAUSTED`）同样留痕。
- **答案只走文件**：MCP 面上没有 submit/cancel，隔离客户端把 `submission.json` 写进 harness 指定的
  输出目录，由 harness 按 `FinalSubmission` 严格解析（多一个 `expected_status`/`incident_case_id`
  即整文件拒收，不触达会话），再交给**同一会话**的 `submit`。因此"客户端说它提交了"不构成证据，
  只有会话的终态与计数算数。
- **闭环验收**：合规路径必须被接受，攻击路径必须在伪造引用、超预算、重复提交、终态后调用上全部被拒；
  本地进程配置下私有平面可达，闭环如实记录该事实，但隔离结论只由容器配置给出。

## 8. 边界

- 本协议只定义进程内接口；跨进程传输（stdio/MCP）由 T10 另行交付，T10 不得改变本协议的
  语义与错误码；隔离桥接（T11）复用同一语义，不新增第二套规则。
- 隔离与防泄漏验收归 T11；同仓库可读答案的外部 Agent 属可信开发模式。
- 本协议不修改诊断平面、工具实现、预算 8/8/2/300、evaluator 判定规则与既有冻结 manifest。
