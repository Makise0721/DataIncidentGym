# Kernel 确认门禁的离线反例评估报告

日期：2026-09-08。
代码基线：`df225698360a2371ef154f1a488e85925c095bc9`（HEAD 未变）+ 共享工作区中未提交的职责拆分与自动记账两期改动。
对应计划：`docs/superpowers/plans/2026-09-08-kernel-gap-gate-assessment.md`。
验证入口：`tests/unit/test_kernel_gap_gate_assessment.py`（16 项，全部通过；全量 unit 402 passed, 3 skipped）。复核修订（同日）：按外部复核意见收窄结论边界、补充缺关键证据配对反例与 evaluator 实证（§3a、§4）。

## 结论（先行）

**建议本期保留现行严格 gap 门禁，不启动放宽或相关结构改造。** 本轮证实：额外 BLOCKED gap 会拦截原本可确认的三类案例；三组关键证据降级输入即使没有额外探针，也会被现有领域规则独立拒绝；REFUTED 的证据绑定可以在未验证反驳内容的情况下通过 kernel；生产 evaluator 的 gap check 会拒绝构造的 CONFIRMED + BLOCKED 终态。这些结果不足以建立放宽门禁的依据。

**边界声明**：C1 只按 subject 与 claim 引用的交集选择可忽略 gap，尚未提供这些 gap 不影响结论的证明，因此本期不采纳。现有测试没有构造 C1 加全部非 gap 检查仍放行错误结论的完整反例，不能称为已击破 C1。C2 等其他规则未构造、未检验。结论是“尚未建立可接受的放行规则”，不是“不存在这种规则”。

构造反例不等于获得更改生产门禁的授权；本报告不修改任何生产行为。

## 1. 现行门禁定位（Task 1）

gap 相关约束的全部代码位置：

| 层 | 位置 | 行为 |
|---|---|---|
| Kernel CONFIRMED | `diagnostic_kernel.py` `_validate_confirmed` 首检 | 任意非 CLOSED gap → `KernelError("EVIDENCE_GAP_OPEN")`，先于假设、claim 与领域检查 |
| Kernel NO_INCIDENT | `diagnostic_kernel.py` `_validate_health` 首检 | 同上，`EVIDENCE_GAP_OPEN` 是健康判定的第一个错误码 |
| Kernel INSUFFICIENT | `diagnostic_kernel.py` `finalize` | 反向门禁 `INSUFFICIENCY_GAP_REQUIRED`：无 OPEN/BLOCKED gap 且无 unresolved 声明时拒绝 |
| Evaluator | `evaluation.py` `_controller_checks`，`KERNEL_EVIDENCE_GAP_GATE`（L961-967） | CONFIRMED/NO_INCIDENT 时终态 `InvestigationState.gaps` 含 OPEN/BLOCKED 即 check 失败；INSUFFICIENT/MODEL_ERROR 豁免。独立于 kernel 复算 |
| 报告消费侧 | `benchmark_report.py` L260-310 | blocked gap 作为"decisive evidence 不可得"的观测信号进入 post-decisive 统计；trace 必须可暴露 blocked 尝试 |
| 提示词 | `prompts/diagnostic_kernel.md` | "Every opened evidence gap must close with a successful typed tool result before you confirm a diagnosis." |
| 需求合同 | `docs/requirements.md` §17 | kernel 验证、拒绝和投影模型声明；两个候选、受支持选中、被反驳替代假设 |

关键实现事实：evaluator 的 gate 从终态 `InvestigationState` 独立复算，不信任 kernel 的 finalize 决定；这意味着放宽 kernel 而不动 evaluator 会直接造成内部判定冲突（生产 CONFIRMED 被评估判 FAILED）。

## 2. 五类事故的证据覆盖矩阵（Task 1）

| 事故类型 | 最低公开证据（领域规则输入） | 领域检查 | 验证程度 |
|---|---|---|---|
| schema rename / type change | 失败节点 node error + 上游关系证据（schema/profile fact 且 relation ∈ upstream 名集）+ incident subject 归属 | `_require_incident_node_and_upstream_evidence`（generic 路径） | **形状级**：验证证据存在与归属，不验证失败消息内容或具体列差异 |
| required field NULL | 同上（prompt 另要求 null_count > 0 的 source profile） | 同 generic 路径 | **形状级**；kernel 不验证 null_count 内容，仅提示词约束 |
| 精确/语义重复支付 | run results、raw_payments profile（business key/fingerprint duplicate counts、payment_method group）、（EXACT 另需）incident node + upstream relation fact | `duplicate_root_supported` + EXACT 的 generic 检查 | **内容级**：验证重复计数值、run 状态、group 存在性 |
| 永久孤儿支付 | 成功 run、关系违反计数 > 0 的 profile、异关系 history（order_count_by_day + 可解析 watermark） | `orphan_root_supported` | **内容级**：验证边界条件（violation>0、watermark 可解析、history 非空） |
| 支付摄取丢失 | 公开观察四元组、双 profile、双 history、reverse relationship 计数=expected−current、watermark≥settled、downstream lineage | `silent_drop_root_supported` | **最强内容级**：全链条数值一致性 |

覆盖矩阵的另外两维：

- **被引用 ≠ 已覆盖**：claim 的 evidence_ids 是模型选择；kernel 的 `_closed_records` 只验证引用绑定（存在且来自 CLOSED gap），不验证"该引用足以支撑结论"。
- **REFUTED = 结构性**：`HypothesisAssessment` 仅要求 evidence_ids 非空、去重、绑定 CLOSED 证据。kernel 与 evaluator 都不存在"反驳内容成立"的验证。`test_assessments_are_structural_not_content_validated` 用最薄的反驳（一条无关 schema 证据）实证了 CONFIRMED 仍可通过——这正是评估的核心事实。

按计划判据：schema rename/type 与 required NULL 连支持证据的内容充分性都未建立，**直接排除出任何放宽候选**；三类内容级根因进入 Task 2/3。

## 3. 配对确定性反例（Task 2）

方法：以三个已通过的 CONFIRMED unit 场景为对照（`test_diagnostic_kernel.py` 的语义重复、永久孤儿、摄取丢失 fixture，经模块加载原样复用，关键证据与决策逐字节不变），在 finalize 前通过真实公开路径注入一次额外调查失败，观察现行门禁。额外失败走两条真实路径：

- `prepare_tool` 白名单外关系 → kernel 自动记录 BLOCKED（`RELATION_NOT_ALLOWED`）；
- `prepare_tool` 白名单内备用关系 + `record_tool_failure`（`PROFILE_SNAPSHOT_MISMATCH`）→ BLOCKED。备用关系通过扩展白名单的配对专用 kernel 提供（`_duplicate_kernel_with_extra_allowance`），主线证据与决策不受影响。

结果表：

| # | 场景 | 对照（无额外失败） | 处理（+1 次额外失败） | 现行门禁结果 |
|---|---|---|---|---|
| 1 | 语义重复 | CONFIRMED | +BLOCKED(raw_orders, RELATION_NOT_ALLOWED) | `EVIDENCE_GAP_OPEN` 拒绝；blocked gap 完整记录 gap_id/subject/error_code/tool_name |
| 2 | 永久孤儿 | CONFIRMED | +BLOCKED(raw_orders) | `EVIDENCE_GAP_OPEN` 拒绝 |
| 3 | 摄取丢失 | CONFIRMED | +BLOCKED(raw_customers) | `EVIDENCE_GAP_OPEN` 拒绝 |
| 4 | 语义重复（tool_failure 路径） | CONFIRMED | +BLOCKED(PROFILE_SNAPSHOT_MISMATCH) | `EVIDENCE_GAP_OPEN` 拒绝；证据库存逐字节不变（失败探针不登记证据） |
| 5 | OPEN gap（prepare 后未回填） | — | 未完成调用 | `EVIDENCE_GAP_OPEN` 拒绝（即使引用证据完备） |
| 6 | NO_INCIDENT + BLOCKED | NO_INCIDENT 可达 | +BLOCKED | `EVIDENCE_GAP_OPEN` 拒绝（健康门保持严格） |
| 7 | 跨 run 证据 | — | 外来 run 的 profile 回填 | `RUN_CONTEXT_MISMATCH` 拒绝且不登记；尝试残留 OPEN gap，随后 finalize 仍被 `EVIDENCE_GAP_OPEN` 拒绝 |
| 8 | 未知 claim 引用 | — | claims 引用不存在的 evidence_id | `CLAIM_EVIDENCE_UNBOUND` 拒绝 |

计划要求的"关键证据不变，仅额外失败导致拒绝"案例成立：#1–#4 中处理组与对照组的证据集与决策完全一致，唯一差异是一次额外 BLOCKED gap，现行门禁因此拒绝。所有失败均保留，未生成任何模型成功率指标。

**3a. 关键证据降级的配对邻近反例（复核补充）**：参数化测试覆盖语义重复 fingerprint 重复数归零、永久孤儿 watermark 缺失（`None`）、摄取丢失 expected==current。各组带额外 BLOCKED gap 时被 `EVIDENCE_GAP_OPEN` 拒绝；在新 kernel 中保留同一降级条件但不执行额外探针时，被 `ROOT_CLAIM_EVIDENCE_INCOMPATIBLE` 拒绝。这证明这三种降级条件由领域检查独立拦截，候选放宽必须保留这些检查；没有证明所有支持证据缺口都能被拦截，也没有把全部剩余风险限定到反驳面。

## 4. 候选规则评估（Task 3）

按计划限定：仍拒绝任意 OPEN；只考虑 CONFIRMED 的 BLOCKED；仅对内容级三类研究例外；全部非 gap 检查保留。候选以纯函数实现于测试模块（`_candidate_c1_ignorable`），未接入生产 finalize。

**C1（subject 无交集即忽略）**：BLOCKED gap 的 subject 未被任何 claim 引用的证据触及时可忽略。

- 配对上的表现：#1 的 `g_extra_probe`（raw_orders）确实未被引用 → C1 判定可忽略（测试实证）。
- 评估结果：C1 能识别部分未被 claim 引用的失败查询，但“未引用”不足以证明该查询与结论无关。REFUTED 缺少内容验证是尚未排除的风险，不是本轮已构造错误放行的证据。
- 边界声明：三组降级测试证明现有领域规则仍有独立保护作用；它们不构成 C1 加全部其他检查的错误放行反例。C1 本期因放行依据不足而不采纳，而非被证明普遍不成立。
- 连带冲突（测试实证）：测试构造 CONFIRMED + BLOCKED 终态，调用生产 `_controller_checks`，断言 `KERNEL_EVIDENCE_GAP_GATE` 失败；干净终态对照组该 check 通过。这是单个生产 gate 的实证，不是完整 evaluator 对整份诊断的端到端验收。只改 kernel 会与该现行 gate 冲突。
- 结论：**本期不采纳 C1**。

**C2（按根因限定 gap kind/数量的白名单式例外）**：未构造、未检验。是否能基于明确的公开领域合同建立更窄的例外，本轮没有证据。通用领域知识或公开事故证据合同不等同于私有 Ground Truth；未来规则可以研究前者，但不得读取当前案例的私有答案或预期评分。

## 5. 最终建议（Task 4）

**本期保留现状，不启动放宽或相关结构改造。** 依据与限制：

1. 已验证现行 strict gate 会保留额外调查失败的阻断效果；这是一项保守约束，不等于它已证明反驳可靠性或诊断正确性。
2. 本轮未建立 C1 忽略 gap 的充分依据，也未构造 C1 加全部非 gap 检查会放行错误结论的完整反例。选择不采纳不要求先证明所有候选都不可能成立。
3. 生产 evaluator 的单独 gap check 与放宽终态存在已验证冲突。未来若放宽，需要分别评估 kernel、evaluator 和报告消费语义，不能静默重判历史记录。
4. schema rename/type 与 required NULL 的 kernel 根因检查偏重证据形状和归属，本轮未为其建立内容充分性的额外证明，不纳入放宽候选。

未解决问题（留档，不构成本轮行动项）：

- 更复杂的放行规则、C1 的完整错误放行反例及内容级反驳验证均未完成；是否存在可接受规则仍未决。
- 若未来有可独立复算的证据覆盖与反驳规则，可重新评估 BLOCKED gap；本轮不将该方向作为新的自动实施任务。
- `EvidenceGap.hypothesis_ids` 已记录模型声明的假设绑定，但可以为空，且关联本身不证明该查询是否足以支持或反驳假设。不能将现有字段描述为不存在，也不据此直接提出新增字段。

## 6. 工作区文件摘要与验证

新增：`tests/unit/test_kernel_gap_gate_assessment.py`（16 项：8 项配对/对照特征 + 4 项候选规则评估 + 复核补充的 3 组缺关键证据配对反例与 1 项 evaluator 实证）、本报告。
未修改：kernel、controller、领域验证器、evaluator、prompt、预算、版本、schema、历史报告。
本轮实际运行：`uv run pytest tests/unit -q` → 402 passed, 3 skipped；`uv run ruff check .` 通过；`git diff --check` 通过（CRLF 提示非失败）。无真实模型请求、无服务测试、无 benchmark。
