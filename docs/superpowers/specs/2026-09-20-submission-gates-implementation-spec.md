# I1/I2 提交门实施 spec（v2，按独立审计修订；含待裁决 Fork）

- 日期：2026-09-20。依据：所有者选定 I1+I2；v1 经独立审计 **FAIL**（两 BLOCKER、三 MAJOR），
  本 v2 逐项修订。**实施前仍需所有者裁决 §2 的可重试性 Fork。**
- 边界（所有者既定）：不改评分、不改归档、不重跑、不联网、不动冻结清单；私有合同只在
  harness 侧执行与离线核对；模型可见面只有固定拒绝码与通用措辞。

## 1. 门语义（按审计重述）

**I1（拒绝码 `CLAIM_SUPPORT_REQUIRED`）**：对每条适用 claim（适用性 =
`APPLICABLE_CLAIM_KINDS_BY_EXPECTED_STATUS[scenario.expected_status]`），要求
**复刻 evaluator 的完整判定**：claim 的每个 evidence_id 都可解析到已登记证据（`len(resolved) ==
len(claim.evidence_ids)`），且 `claim_supported_by_records(scenario, claim, resolved,
all_records=全部已登记证据)` 为真。INSUFFICIENT_EVIDENCE 适用集为空 → 空洞通过（与 evaluator
一致）。detail 为正向通用指引（"cite evidence returned by tool calls that supports each claim"），
不含私有数据。

**I2（拒绝码 `GAP_RECEIPT_REQUIRED`）**：对每个**已声明**缺口三元组，若场景合同
`unresolved_gaps` 以同一三元组给出 `tool_name`，要求运行 trace（**runner 注入，见 §2**）中
`refusal_witnessed(tool_name, subject, reason_code)` 为真。detail 只给正向指引
（"probe the tool to record the refusal for this gap"），不出现"删除缺口"字样（避免诱导模型删掉
真实合同缺口）。合同未关联工具的三元组、不匹配合同三元组的自报缺口不在范围（属 I3）。

**精度定理（v1 结论保留，前提补齐）**：任何 evaluator PASSED 的提交必然通过两门。前提：
(a) `EVIDENCE_NOT_REGISTERED` 先行（已存在）；(b) I1 采用上述完整判定；(c) I2 用 runner trace；
(d) CONFIRMED/NO_INCIDENT 通过路径均以"claim 自身引用"过 evaluator 支撑规则（代码已证），
INSUFFICIENT_EVIDENCE 通过要求缺口集合精确相等且全部合同工具缺口有收据（代码已证）。
**覆盖缺口**：31 格语料无 NO_INCIDENT 终态，该分支只有代码级证明，须补合成测试。

**期望拒绝集（语料实测，v2 更正）**：
- I1 = {v29 seq 9, 13；v30 seq 9}（v1 一致；均 CONFIRMED、唯一失败检查
  `CLAIM_EVIDENCE_COMPATIBLE`）。
- I2 = **{v30 seq 8}**（v1 的 10 格为错误集合：其中 9 格在归档 runner trace 中有真实收据；
  且 v1 沿用了分析器的死过滤 bug——`quality_baseline._axis3` 曾用
  `event_type == "TOOL_TRACE"` 过滤，真实字面量为 `TOOL_CALL`，导致 `refusal_witnessed` 恒假。
  该缺陷已独立发现并修复（提交 `600da53`），基线报告未见证数同步更正为 v30 seq 15 一例）。
- **回归断言**：拒绝集与上述集合逐格相等；8 个 PASSED 格全部接受；4 个反事实变体全部接受
  （变体 13 按所有者完整配方重建：绑定**已采 schema 与根因 claim** 的引用——审计实测仅补
  根因引用**不足以**使其 PASSED；回归断言重建后的变体 evaluator PASSED。两批 seq 13 的事实：
  均为 `orphan_payment_coupon_a / STATIC_SKILL`，整改版报告生成表已按此正确显示）。

## 2. 可重试性 Fork（**待所有者裁决**）

独立审计实证：kernel/static 的提交点在 run 结束后（`_result → _submit_through_session`），
若门在 `session.submit` 拒绝，异常走 fail-closed 兜底 → **MODEL_ERROR、空 trace、证据被清空**；
模型拿不到任何反馈，拒绝码也不出现在归档 trace（planner 路径例外：它在输出 validator 内提交并
`ModelRetry`，天然可重试——但 planner 不在冻结清单）。

- **D1｜submit 级门（v1 设计）**：实现最小；但语料 4 个应触发格（3 个 STATIC + 1 个 KERNEL）
  会被转为 MODEL_ERROR 终态，**摧毁诊断证据**，真实验证指标"拒绝码出现率"不可观测。
- **D2｜回合内门（推荐）**：把门校验上移到模型输出回合的 validator（kernel 已有 rejection
  机制与重试提示可挂载；static 需要在终局提交流程加同类 validator 钩子）。真正可重试（消耗输出
  重试预算与模型请求）、证据保留、拒绝码可观测；代价：改动输出校验路径，范围更大。
- 门语义（§1）在两种方案下一致，差异只在触发点与终态后果。

### 2.1 所有者对 D2 的三条约束（实施时必须同时满足）

1. **validator 自身异常 fail-closed**：门校验代码内部任何异常都不得放行提交——异常转换为固定
   `GATE_INTERNAL_ERROR` 拒绝（可重试），耗尽后进入 §下方定义的终态；绝无"因门崩溃而通过"。
2. **重试耗尽终态明确且拒绝码可见**：输出重试预算（2 次）耗尽时必须产生定义明确的终态与可见
   拒绝码（`MODEL_OUTPUT_RETRY_EXHAUSTED` 语义，落入归档 summary/轨迹），不得静默接受；测试钉住。
3. **planner target_refusals 缺口随本次修复**：planner 归档 trace 未复制 `target_refusals`
   的既有缺口（`planner_agent.py:331-339` vs `strategy_adapter.py:252`）在本次一并修复，
   并以回归钉住（v2 batch 拒绝可被见证）。

## 3. 数据缝与接线（按审计唯一化）

- **trace 源唯一化**：不得由会话累积（kernel-prep 拒绝不经过工具门面、码表不同构，语料反例
  v30 seq 3 会被误拒）。采用 **runner 注入**：`for_run` 建立可变盒，`_diagnose_once` 建
  `_RunState` 后填入 `state.trace`（live list）；提交/校验发生在 `_result` 之后（D1）或输出
  validator 内（D2），届时 trace 已完整。planner 用 `controller.step_records()`；**既有缺口须
  一并修复**：planner 归档 trace 未复制 `target_refusals`（v2 batch 拒绝无法见证）。
- **scenario 加载**：evaluation_runner 将现有加载点**提前一次并缓存复用**（不重复 I/O）；
  加载失败仍只走既有 `SCENARIO_LOAD_FAILED` 评估路径、不中断诊断；缓存对象同时供
  `_write_scoring_inputs` 使用（同一实例，避免漂移）。
- **接线清单（完整）**：`benchmark_runner.for_project` 自建工厂（**测量路径，必须覆盖**）、
  `evaluation_runner.for_project` 默认工厂、`scenario_certification.reference_evaluation_runner`、
  `cli.py` 直调、测试工厂与集成测试；Harness 侧外部策略会话（`strategy_bridge` /
  `examples/isolation_acceptance.py`）必须装同一门（**MCP 侧更正**：`mcp_server.main` 拿不到
  case id，门应由持有会话的 harness 桥安装；装门后该示例的固定期望与 3 次提交/2 次预算序列
  需重核）。`fixed_rule` 确定性参考解不设门（None）。
- **身份面（下一冻结/发布时一并处置，本轮不动）**：`STRATEGY_PROTOCOL_VERSION` 与
  `controller_protocol_version` 载荷、requirements.md M17/M18 文本、协议 spec §4 拒绝词汇、
  `isolation_acceptance` 的固定码断言。
- **残余信息通道**：门的接受/拒绝本身构成有限 oracle（预算 2 次/run 有界）；记录为已接受边界。

## 4. 测试与回归

- 合成测试：两门触发/通过与拒绝码、I1 的"引用全部可解析"判定、I1 对 INSUFFICIENT_EVIDENCE
  空洞通过、I2 对无工具三元组放行、NO_INCIDENT 分支（补语料覆盖缺口）、重试计数（D2 下含
  耗尽语义）、拒绝码与措辞不含私有数据（含 planner 的 ModelRetry 路径）、`policy=None`
  既有行为零改动。
- 离线回归：31 格语料 + 4 变体重放；断言见 §1；确定性重复运行；私有合同只在脚本内核对
  （脚本不入库，入库的是合成测试）。
- 实现完成后按既有流程独立审计、提交；随后按验收阶梯第 2/3 步另行授权真实验证与测量。

## 5. 明确不做

不改提示词（I3 另行）、不改 evaluator/评分、不改归档、不重冻、不做真实验证与测量。

## 6. 整改记录（v2.1，按实施审计 FAIL 的逐项处置）

实施审计（`579d35f..53a4445`）结论 FAIL：两 BLOCKER 均在 kernel 路径。整改提交 `c24f650`：

| 审计项 | 处置 |
| --- | --- |
| F1 BLOCKER：I1 无法评估 `KernelDecision` 的 `ClaimEvidence`（合法 CONFIRMED 决策必被拒为内部错误） | 新增 `_ProjectedKernelSubmission`：把候选决策投影为诊断 claim 形状（`_claim_evidence_to_diagnosis_claim`，与终局 `_claims_to_diagnosis_claims` 同源）；门在投影上评估 |
| F2 BLOCKER：门在 `kernel.finalize` 之后（内核已锁、重试不可能 → 静默接受或清空归档） | 门移到 `finalize` **之前**；拒绝 → 可重试（内核仍开放）；耗尽 → `MODEL_OUTPUT_RETRY_EXHAUSTED` 终态且内核证据保留（`diagnostic_kernel._SAFE_MODEL_ERRORS` 同步加码） |
| F3 MAJOR：三个工厂未更新（e2e 矩阵、两个集成） | 全部更新并传入 policy（e2e 工厂同步加参） |
| F4 MAJOR：planner 门拒绝无 trace 留痕、耗尽映射缺失 | `PlannerDeps.gate_refusals` 台账 + `_plan_events` 渲染 `EvidenceGateTraceEvent`；耗尽按门标记映射新码；planner 码表加码 |
| F5 MINOR：缺 NO_INCIDENT/HEALTH_STATE 与 kernel 门用例 | 新增：CONFIRMED 合同跳过 HEALTH_STATE、NO_INCIDENT 合同检查 HEALTH_STATE、kernel 投影达 claim 规则（非内部错误）、kernel 门前置+耗尽可见+内核证据保留 |
| F7 MINOR：早加载失败后二次装载成功 = 静默无门 | 单次装载语义：早期装载失败即走既有 `SCENARIO_LOAD_FAILED`，不再重试装载 |
| F6/F9/F10/F11/F12 NOTE | F6：外部 harness 会话（`strategy_bridge`/`isolation_acceptance`）装门**推迟并登记于"明确不做"**（非测量路径）；F9：门标记粒度为 validator 调用，记录为已接受边界；F10：§1 措辞更正——I1 三格中 v29 seq 13 还失败 `REQUIRED_EVIDENCE_TYPES_PRESENT`，拒绝仍由支撑规则驱动；F11：planner 门先于 `session.submit` 的顺序偏离已记录（方向安全：仍是可重试拒绝）；F12：语料回归增加 KernelDecision 形状重放（经同一投影），与 Diagnosis 重放结果逐格一致、零内部错误 |

整改后证据：单测 **1089 passed / 5 skipped**；语料回归拒绝集逐格精确（v29 I1={9,13}；v30 I1={9}、I2={8}），8 个 PASSED 格与 4 个反事实变体全部接受，kernel 形状重放零不一致、零 `GATE_INTERNAL_ERROR`。

## 7. 明确不做（补充）

- 外部 harness 会话（`strategy_bridge`、`examples/isolation_acceptance.py`）的门安装推迟（F6）：两者不在冻结测量路径；实施时须同步核对其固定码断言与提交预算序列。
- `PlanTraceEvent` 合同未动；planner 门留痕复用既有 `EvidenceGateTraceEvent`（run 结果层，无冻结 schema 漂移）。

### 6.1 复审记录（第二次独立审计：PASS WITH FINDINGS）

两 BLOCKER 以 A/B 复现证明修复（无会话静默接受 → 已消失；有会话证据清空 → 已消失且证据保留）；F3（集成测试实跑通过：evaluation 1 passed、planner 4 passed）、F4、F7、语料回归与 kernel 形状重放（15 个 kernel 格结果与 Diagnosis 重放逐格相同、零 GATE_INTERNAL_ERROR）全部确认。复审发现与处置：

| 项 | 级别 | 处置 |
| --- | --- | --- |
| M1：e2e 工厂加参未透传（该路径静默无门） | MINOR | 已修复：透传 `submission_policy` 至 `DiagnosisRunner.for_run` |
| N1：planner 门事件排在 plan 事件之前（非时序）；`_plan_events` 返回类型放宽为 `list[Any]` | NOTE | 已修复：门事件移至 plan 事件之后（提交发生在动作之后），保持时间序；宽松注解保留（混合事件类型） |
| N2：`SCENARIO_LOAD_FAILED` 评估与健康恢复的产物校验冲突（既有耦合，F7 使其在瞬时装载失败时可达） | NOTE | 记录：方向 fail-closed（中止而非无门通过）；若后续需要该组合，另行小修 `_failed_evaluation` 的 recovery 组合 |
| N3：门致耗尽的 `MODEL_PROTOCOL` 事件归因为 `error_origin=UNKNOWN` | NOTE | 记录：拒绝码经 `EVIDENCE_GATE` 事件与终态 summary 可见，归因字段不额外改动 |
