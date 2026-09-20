# I1/I2 提交门实施 spec（草案 v1，待审计）

- 日期：2026-09-20。依据：所有者选定 I1+I2（[回归方案](2026-09-20-strategy-regression-proposal.md) §2）。
  本 spec 细化拒绝码集、预算/重试语义、数据缝与回归语料；审计通过后实施。
- 边界（所有者既定）：本轮不改评分、不改归档、不重跑、不联网、不动冻结清单；私有合同只在
  harness 侧执行与离线核对，模型可见面永远只有固定拒绝码与通用措辞。

## 1. 门语义（提交级，session.submit 内、EVIDENCE_NOT_REGISTERED 之后、Diagnosis 构造之前）

**I1 `CLAIM_SUPPORT_REQUIRED`（可重试）**：对每条"适用"claim（适用性 = evaluator 的
`APPLICABLE_CLAIM_KINDS_BY_EXPECTED_STATUS[scenario.expected_status]`），要求
`claim_supported_by_records(scenario, claim, 已引用且已登记的证据, all_records=全部已登记证据)` 为真。
任一适用 claim 不被支撑 → 拒绝；detail 为通用措辞（"cite evidence returned by tool calls that
supports each claim"），不揭示私有期望。INSUFFICIENT_EVIDENCE 合同下适用集为空 → 空洞通过
（与 evaluator 一致）。

**I2 `GAP_RECEIPT_REQUIRED`（可重试）**：对每个已声明缺口三元组
`(evidence_kind, subject, reason_code)`，若场景合同 `unresolved_gaps` 以同一三元组给出
`tool_name`，则要求运行 trace 中 `refusal_witnessed(tool_name, subject, reason_code)` 为真
（复用 evaluator 同一规则与同一 trace 事件源）。无真实拒绝收据 → 拒绝；detail 通用措辞
（"probe the tool to record the refusal, or drop the unsupported gap claim"）。
合同未关联工具的三元组、以及不匹配合同三元组的自报缺口不在本门范围（属 I3 的选择判断）。

**预算/重试语义**：两门拒绝均消耗一次输出重试预算（`_output_retries_used += 1`），语义与现有
`EVIDENCE_NOT_REGISTERED`/`SUBMISSION_INVALID` 一致；预算耗尽 → 既有 `OUTPUT_RETRY_EXHAUSTED`。
拒绝不改变工具预算、模型请求预算、时限；不泄露私有数据（拒绝码与措辞为固定串，经断言测试）。

**精确性定理（回归验证）**：任何 evaluator 判定 PASSED 的提交必然通过两门——CONFIRMED 通过蕴含
claim 支撑成立；INSUFFICIENT_EVIDENCE 通过蕴含缺口集合精确相等且合同工具缺口全部有真实收据。
门因此**不可能拒绝本会通过的提交**；语料回归以 8 个 PASSED 格 + 4 个反事实变体全接受来钉住。

## 2. 数据缝与接线

私有 scenario 目前在诊断阶段不可得（evaluation runner 在诊断完成后才加载）。因此：

- 新增 `submission_policy.py`：`SubmissionPolicy(scenario)`，`check(submission, registered,
  tool_trace) -> ProtocolError | None`（含 I1+I2 两段，I1 在前）。
- `StrategySession` 新增可选 `submission_policy`（默认 None，向后兼容）：submit 内调用
  `policy.check(submission, self._registered, self._live_trace)`。
- **trace 来源**：会话在工具门面调用点累积**与 runner 同构的 ToolTraceEvent**（复用同一构造
  路径/规则，不另写见证规则）；若门面数据不足，则由 runner 注入 trace provider 闭包
  （for_run 建立可变盒，diagnose 内填充）。实现取其一，审计确认其同构性。
- **接线清单（等价性）**：evaluation_runner 在诊断前额外加载一次 scenario 用于构造 policy
  （加载失败 → policy=None，门关闭，既有 SCENARIO_LOAD_FAILED 评估路径不变），经
  diagnosis_factory → `DiagnosisRunner.for_run(..., submission_policy_factory=...)`、
  `EvidencePlannerRunner` 同参数；`mcp_server` 路径同样加载 scenario 并安装同一 policy
  （M18 进程内/MCP 等价性：外部策略进程不得绕过两门）；`fixed_rule` 确定性参考解不设门（None）。
- **身份**：本变更改变 controller 行为 → 下一身份冻结时须升 `controller_protocol_version` 并
  绑定载荷（审计时复核）；本轮不动任何冻结清单，不重冻。

## 3. 离线回归（语料重放，零模型调用）

- **语料**：两批 31 个终态格的评分包 + 4 个反事实变体（按所有者表格以构造器重建：8 主体改
  stg_orders；9/13 绑定根因引用；12 删除两个多报缺口；记录来源与变换，不改原归档）。
- **期望拒绝集（语料实测，写入回归脚本为数据而非假设）**：
  I1 = {v29 seq 9, 13；v30 seq 9}（均 CONFIRMED、引用不足）；
  I2 = {v29 seq 3, 8, 11, 12；v30 seq 3, 7, 8, 11, 16}（已声明缺口匹配合同工具三元组且
  无真实收据）。**更正**：回归方案中"I2 期望 2 例"系分析器 missing 过滤计数，作为提交门语义
  应取上述 10 例；以本实测为准。
- **精确性要求**：8 个 PASSED 格与 4 个变体**全部接受**；拒绝集与上述集合逐格相等；篡改样本
  （如伪造收据、错主体）被拒；重复运行确定性（同一输入同输出）。
- 私有合同只在回归脚本内核对；脚本不入库（本地验收），入库的是合成测试。

## 4. 测试（入库合成夹具）

`CLAIM_SUPPORT_REQUIRED`/`GAP_RECEIPT_REQUIRED` 的触发与通过、重试计数与耗尽语义、拒绝码与
措辞不含私有数据（断言）、I1 对 INSUFFICIENT_EVIDENCE 的空洞通过、I2 对无工具三元组的放行、
门在 EVIDENCE_NOT_REGISTERED 之后的顺序、`submission_policy=None` 的既有行为不变。

## 5. 明确不做

不改提示词（I3 另行）、不改 evaluator/评分、不改归档、不重冻、不做真实验证与测量（阶梯第
2/3 步待另行授权）。
