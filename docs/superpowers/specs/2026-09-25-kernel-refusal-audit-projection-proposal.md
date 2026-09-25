# 未来 Kernel 合同拒绝的最小审计投影（方案 2 已批准，待实施）

依据：[v31 七次拒绝的只读可行性报告](../reports/2026-09-25-v31-kernel-refusal-readonly-feasibility.md)。所有者于 2026-09-25 批准方案 2：为未来运行实施版本化安全投影与离线验收。**该批准不重写 v31，也不放行新冻结、真实模型测量或推送。**

## 已证明的缺口

`DiagnosticKernel.finalize` 先查 `decision.run_id == kernel.run_id`，再查状态与证据。当前 `RejectedDecisionSummary` 没有被拒 decision 的 run ID 或相等事实；因此 v31 的七次拒绝均不能独立排除 `DECISION_SCOPE_MISMATCH`，严格分类是 `INDETERMINABLE`。seq36 的两条水位声明另有主体脱敏：原始三元组是否重复也不能从两个 `null` 重算。六条事件在**假设 scope 匹配**时可推出归档所报后续错误码；这不是首错复核通过。

## 两种处置

1. **维持当前合同**：沿用 `refusal_review` 的 `INDETERMINABLE/KERNEL_CONTRACT_PATHWAY`；可在报告中补充上述具体阻断原因，但不宣称新增可判样本。不产生新身份，不改变历史结果。
2. **仅为未来运行增加安全投影**（建议在下一次已获准的新身份冻结前做）：将下列事实由 harness 从被拒 `KernelDecision` 与当时 Kernel 状态生成并归档。历史 v31 仍为 `INDETERMINABLE`，绝不补填或改判。

## 方案 2 的精确定义

在新的、严格分派的 `RejectedDecisionSummary` 版本中增加两个必填字段；模型不可填写、离线读取器不可凭错误码猜测：

- `decision_scope_matches_run: StrictBool`：在拒绝当刻直接比较 `decision.run_id == kernel.run_id`。只保存布尔值，不保存模型提交的 run ID。`False` 时离线首错应为 `DECISION_SCOPE_MISMATCH`（仍先核对 `KERNEL_FINALIZED`）；`True` 才继续后续判据。
- `unresolved_subject_equivalence: tuple[StrictInt, ...]`：长度恰等于**已保留**的 `unresolved_evidence` 数组；对原始 subject 按第一次出现顺序赋连续编号，例如 `("x", "y", "x") → (0, 1, 0)`。只暴露相等关系，不保存 subject 原文、哈希或可反查标识。离线读取器用 `(evidence_kind, equivalence_id, reason_code)` 重算去重；编号必须采用首次出现的规范形式。若投影截断了声明，仍为不可判，不用这段数组假装完整。

相等关系只用于**按原始规则重算**，不能代替其他字段的可见性检查；脱敏 subject 对后续证据绑定规则仍按现有可证明范围处理，证明不了即 `INDETERMINABLE`。上述两字段是针对 v31 已证实缺口的最小增量，不保证所有其他 Kernel 错误码都可判。

## 身份、兼容与验收边界

- 新投影必须有独立 schema 标记和严格读取分支；旧 `p1.rejected_decision.v1`、旧 `EVIDENCE_GATE`、`p1.trace.v1/v2`、旧聚合运行及评分输入的规范 JSON/摘要保持逐字不变。不得把新字段默认加到旧模型并让历史 JSON 重新序列化。新 trace、聚合运行与评分输入身份随之版本化；受影响的 controller 政策身份与 evaluator 源码摘要进入下一次新 manifest。具体版本号由实现设计一次性锁定，不能暗用既有冻结身份。
- 新版拒绝事件在 `KERNEL_FINALIZED`、scope、业务规则的原顺序下离线逐次复核，输出 `CORRECT` / `FALSE_REFUSAL` / `INDETERMINABLE`。首错码相同才算 `CORRECT`；投影缺失、截断、身份不符和状态无法回到拒绝时均为不可判。I1/I2 读取与评分语义不因这两个字段变化。
- 回归须覆盖 scope 相等与不等、两个脱敏 subject 相同与不同、规范编号、截断、拒绝后继续取证、旧归档严格加载与摘要恒等、新归档序列化重载。以合成 run 证明隐私边界：新字段不泄露提交的外来 run ID，既有投影判为未知的 subject 仍不以原文或其哈希出现。已允许公开的 subject 保持现有归档口径。v31 七次只读复核必须仍为 7 次不可判。
- 在方案 2 实施、集中验证、独立冻结与放行前，不运行新正式测量；本提案本身不批准任何真实模型请求、数据库实验、freeze 或 push。

**裁定**：实施方案 2。实现和验证完成后另行审计；新冻结及真实测量仍按独立授权流程。v31 的七次历史拒绝保持不可判。
