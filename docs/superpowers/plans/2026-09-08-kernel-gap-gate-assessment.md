# Kernel 后续计划：确认门禁的离线反例评估

状态：已执行完毕（2026-09-08）。结论：建议保留现行严格 gap 门禁，本期不放宽。执行记录见文末"执行状态"节；完整报告见 docs/superpowers/reports/2026-09-08-kernel-gap-gate-assessment.md。

## 审计结论与起点

2026-09-08，已复核自动记账计划后的白名单说明修复：

- `_kernel_state_summary` 现在明确工具的 `provable_relations` 是完整清单，证据里出现的关系不会扩展它。
- `test_ledger_text_and_kernel_rejection_agree_on_relation_allowlist` 构造已接受血缘中出现 `raw_orders`、schema 工具只允许 `raw_payments` 的场景，同时检查账本文本和实际 `RELATION_NOT_ALLOWED` 拒绝。
- 本轮 `uv run pytest tests/unit/test_diagnostic_agent.py tests/unit/test_kernel_auto_binding.py -q`：23 passed，4.43 秒。
- Ruff 和 `git diff --check` 通过；检查中的 CRLF 提示不是失败。
- 上一轮自动记账审计的聚焦回归为 75 passed；该数字属于修复前检查。本轮未重跑全量或服务测试。

已知审计阻塞解除，可以进入本计划。代码仍位于包含前两期未提交修改的共享工作区；执行前重新核对 HEAD/diff，不重置或覆盖已有修改。

## 本阶段要回答的问题

职责拆分和自动记账已处理最明确的维护负担与模型机械字段。下一步不继续机械拆文件，而是验证当前确认门禁是否有可以安全缩小的适用范围。

核心问题：模型已有足够公开证据支持结论时，一个额外探索留下的 BLOCKED gap 是否仍应阻止 CONFIRMED？如果存在可放行情况，能否用公开事实给出可重算、可被反例推翻的规则？

本阶段输出一份证据报告和明确建议，可以得出“保留现状”。构造出反例不等于获得更改生产门禁的授权；实际协议或判分变更需要在报告中形成具体合同后再安排实施。

## 当前实现约束

1. `DiagnosticKernel._validate_confirmed` 与 `_validate_health` 在领域验证前拒绝任意非 CLOSED gap。
2. `evaluation._controller_checks` 的 `KERNEL_EVIDENCE_GAP_GATE` 也拒绝带 OPEN/BLOCKED gap 的 CONFIRMED/NO_INCIDENT。只改 kernel 会造成内部判定冲突。
3. gap 的 `hypothesis_ids` 由模型绑定，可以为空或不完整。不能把“未绑定选中假设”直接当作“与结论无关”。
4. claim 的 evidence_ids 是模型选择的引用。不能把“没被引用”直接当作“非决定性”，否则缺失证据可被省略规避。
5. 部分现有根因校验使用通用节点错误与上游关系证据，另一些事故有更具体的领域充分性规则。不能假设所有根因的现有验证器都已经证明证据完备。
6. 业务 fingerprint 拒绝重复调用。BLOCKED gap 不一定能通过“再查一次”关闭；本期不增加重试、缓存或 gap 合并功能。
7. OPEN 表示调用尚未正常完成，与已经失败并留下事实记录的 BLOCKED 不同；前者始终阻止确认，不纳入放宽候选。

## 固定边界

- 只做离线设计和确定性反例验证。生产 kernel、controller、领域验证器、evaluator、prompt、预算、版本和 schema 保持不变。
- 不运行真实模型、doctor 模型探针、正式 benchmark，不生成或重新冻结 Manifest，不改写历史结果或历史策略身份。
- 两个候选、证据反驳替代假设、作用域、跨 run 引用和预算门禁全部保留。
- NO_INCIDENT 本期保持严格关闭要求；健康声明涉及更广泛排除，不随 CONFIRMED 的候选规则一起放宽。
- 实验规则只读取公开 EvidenceRecord、模型声明、调查状态及公开观察上下文。预期答案只供独立测试断言，不传入候选判定器。
- 不增加模型输出的 `optional`/`decisive` 布尔字段，不把忽略 gap 的决定直接交回模型。

## 任务与完成判据

### Task 1：建立现行门禁和覆盖矩阵

- [x] 定位 kernel、evaluator、产物校验与提示词中涉及 gap 的约束，记录源文件及函数，不仅依赖旧计划。
- [x] 为 schema rename/type、required NULL、重复支付、永久孤儿、支付摄取丢失整理“最低公开证据 → 现有领域检查 → 独立测试覆盖”。
- [x] 标记哪些规则只能确认引用形状，哪些验证具体内容；对证据充分性未建立的类型，不提出放宽。
- [x] 区分已注册假设的完整 assessment 与真正经过内容验证的反驳，避免将结构通过当成语义已证明。

完成判据：报告清楚说明现有门禁保护了什么，以及它可能额外拒绝什么；没有未经代码支持的“已充分验证”结论。

### Task 2：构造配对的确定性反例

- [x] 优先复用现有 unit fixture 的语义重复支付、永久孤儿和摄取丢失案例，每种选一个已通过的 CONFIRMED 输入作为对照。
- [x] 对每个对照增加一次真实走 `prepare_tool`/`record_tool_failure` 路径的额外调查失败，保留原有效公开证据和原结论；观察旧门禁的拒绝。
- [x] 每个拟放行例都增加一个邻近反例：缺少关键 profile、历史边界/watermark、公共比较或影响血缘。必须证明不能通过“未引用/未绑定”隐藏这些缺口。
- [x] 加入 OPEN、跨 run 证据、未知引用、其他未排除假设、NO_INCIDENT 的对照，不把它们列为可忽略情况。
- [x] 不直接篡改 kernel 私有列表来造 gap；只有配对场景确需重放固定公开事实时使用现有测试工厂，明确构造依据。

完成判据：至少有一个可复现的“关键证据不变，仅额外失败导致拒绝”的案例，或明确记录当前无法建立这样的案例。所有失败保留，不生成模型成功率指标。

### Task 3：评估最小候选规则

候选规则首先限定为：仍拒绝任意 OPEN；只考虑 CONFIRMED 的 BLOCKED；仅对 Task 1 已建立内容充分性检查的事故类型研究例外；所有原有非 gap 检查仍须通过。

- [x] 对每个拟忽略的 gap 提供具体证明依据：哪些成功证据已经覆盖它可能影响的根因区分与资产声明，而不是仅说明它未被引用。
- [x] 将“缺失必需证据”和“额外查询失败”分开；没有覆盖证明时仍拒绝。
- [x] 候选判定使用测试范围内的纯函数或小型评估脚本，不接入生产 finalize，也不通过 monkeypatch 隐藏原有校验失败。
- [x] 候选评估的输出至少包含现行结果、候选结果、gap_id、可核查的证据引用和理由；不新增生产状态字段。
- [x] 用 Task 2 的邻近反例攻击规则，并记录拒绝原因。如果规则必须增加大量事故例外、重复领域验证或信任模型标记，建议保留现状。

完成判据：拟放行的例子有公开证据解释，所有选定危险反例被拒绝；若不满足，结论是“不建议放宽”，不能删除反例来获得通过。

### Task 4：形成可审查的最终建议

- [x] 输出配对案例结果表、证据覆盖矩阵、候选规则和未解决问题。
- [x] 如果建议保留现状：说明决定性依据，结束本轮减重，不继续安排无证据支持的结构重构。
- [x] 如果建议放宽：给出精确的适用事故/状态/错误类别、所需证明、kernel 与 evaluator 的独立实现责任，以及版本和报告兼容影响。
- [x] 对拟放宽设计明确追踪方式：被忽略的 BLOCKED gap 必须保留原始记录，不能伪装 CLOSED；消费者应如何理解新终态必须写进合同。
- [x] 任何后续实现都需将新规则与历史 strict gate 区分，不能让旧 Manifest 或历史报告静默按新规则重判。

完成判据：交付“保留”或“建议修改”的明确结果；不以“测试全绿”替代产品语义决定。后者应附具体实施草案，当前评估阶段不执行该草案。

## 文件与验证范围

建议新增：

- `docs/superpowers/reports/2026-09-08-kernel-gap-gate-assessment.md`：审计依据、配对矩阵、候选规则和最终建议。
- `tests/unit/test_kernel_gap_gate_assessment.py`：现行生产行为的特征测试；仅保存有长期价值的回归。
- 如候选评估需要独立代码，放在上述测试模块的私有辅助函数中，明确标记为评估用途，不新增运行时 API。

测试预期以当前 strict gate 为准；候选算法单独调用、单独断言，不让正式测试要求尚未授权的生产行为。不要复制整份 kernel 或 evaluator。

本阶段主要运行新增用例及 kernel/agent/evaluation 的相关单元测试、Ruff 和 `git diff --check`。仅新增离线测试/报告时，不重复运行耗时服务测试；如果执行范围发生变化，先更新计划和必要验证范围。本轮没有理由触发完整服务或模型流程。

交付报告标明代码基线和工作区相关文件摘要，所有结果使用本轮真实输出；清理本次无用临时文件。不提交、不推送、不改既有共享工作。

## 执行状态（2026-09-08）

### 实际文件清单

- 新增 `tests/unit/test_kernel_gap_gate_assessment.py`：12 项测试——8 项配对/对照特征（三类内容级根因各一组"已通过 CONFIRMED + 真实路径额外失败"、tool_failure 路径、OPEN gap、NO_INCIDENT、BLOCKED 记录保留、跨 run/未知引用）+ 4 项候选规则评估（C1 纯函数、两个击破断言、REFUTED 结构性实证）。
- 新增 `docs/superpowers/reports/2026-09-08-kernel-gap-gate-assessment.md`：门禁定位表、五类事故覆盖矩阵、配对结果表、C1/C2 评估、保留现状建议及未解决问题。
- fixture 经 importlib 从 `test_diagnostic_kernel.py` 原样加载，配对证据与决策逐字节一致；配对专用 kernel 仅扩展一个白名单备用关系供失败探针使用。
- 生产文件零改动（kernel、controller、验证器、evaluator、prompt、预算、版本、schema）。

### 验证结果

- `uv run pytest tests/unit/test_kernel_gap_gate_assessment.py -q` → 12 passed。
- `uv run pytest tests/unit -q` → 398 passed, 3 skipped（含新增 12 项）。
- `uv run ruff check .` 通过；`git diff --check` 通过（CRLF 提示非失败）。
- 无真实模型请求、无 doctor 探针、无服务测试、无 benchmark；按计划未重复耗时服务验证。

### 结论与剩余限制

- 结论：**保留现行严格 gap 门禁**。决定性依据为 REFUTED 判定仅有结构验证（测试 `test_assessments_are_structural_not_content_validated` 实证），任何只依赖公开事实的放行规则无法完成反驳完备性判定；C1 在配对上被击破，C2 未实现并记录了理由。
- 重开评估的前提条件已留档（REFUTED 内容级验证建立之后；gap 记录粒度富化属生产协议变更）。
- 本轮全部产物未提交，随共享工作区统一处置。

### 复核修订（2026-09-08，以本节及报告现文为准）

- 新增三组关键证据降级测试和一项真实调用生产 `_controller_checks` 的测试；本次复核运行评估文件为 **16 passed in 0.45s**。完整 unit **402 passed, 3 skipped** 属实施方记录，本次未重跑全量。
- 三组降级在无额外探针时仍被领域规则拒绝；这支持保留非 gap 检查，不证明所有支持证据缺口都被覆盖，也不构成 C1 加其他检查仍错误放行的反例。
- evaluator 实证限于单独 `KERNEL_EVIDENCE_GAP_GATE`，不是完整 evaluator 的端到端结果。
- 结论维持“本期保留现状”。前述“任何公开事实规则不可行”“C1 已被击破”的表述撤回，准确结论是当前未建立可接受的放行依据。C1 完整错误放行反例和 C2 等候选仍未验证。
- `EvidenceGap.hypothesis_ids` 已有假设绑定字段，其语义充分性尚未被内容检查证明；不据此新增字段。本轮只修订报告与计划措辞，不修改生产行为。

