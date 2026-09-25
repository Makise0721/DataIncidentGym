# Kernel 合同拒绝审计投影实现报告

日期：2026-09-25

实现分支：`codex/kernel-refusal-audit-projection-v2`，基于 `d5d2f00aeca9be54071e74ed1bcd093ba2ae5370`。
方案依据：[v31 七次拒绝只读可行性报告](2026-09-25-v31-kernel-refusal-readonly-feasibility.md)。

## 结论

已完成未来 Kernel 合同拒绝的安全投影、严格版本分派、离线首错复核和评分输入兼容。新版本为 `p1.rejected_decision.v2`、`p1.evidence_gate.v2`、`p1.trace.v3`、`p1.diagnosis_run.v3`、`p1.evaluation_inputs.v3`、`p1.evaluator.v5` 和 `p1.controller.v22`。

审计投影只保存 harness 计算的 `decision_scope_matches_run` 和保留声明的 subject 首见等价编号；不保存被拒 decision 的 run ID、subject 原文或 subject 哈希。builder 必须读取 `kernel.run_id`；若 Kernel 未提供该作用域，投影显式失败，不再回退到 decision 自带的 run ID。

离线 reviewer 按 `KERNEL_FINALIZED`、作用域、状态和 unresolved 声明的原顺序复核。等价编号会先重算重复项；截断保持不可判。可证明不能通过 validator 绑定的脱敏声明判为合同拒绝成立；若隐藏 relation subject 仍可能匹配拒绝时的 BLOCKED gap，则保持不可判。可见 subject 与等价编号须双向一致。

旧 trace、拒绝摘要和评分输入保留原模型及序列化。I1/I2 review 和确定性评分规则未更改。没有修改 v31 冻结归档，也没有运行真实模型请求、正式基准测量、新冻结或推送。

## 实现范围

- `diagnosis.py`、`diagnostic_agent.py`、`diagnostic_kernel.py`：增加严格 v2 拒绝摘要、v2 gate、v3 run/trace 形状、作用域布尔值和等价编号；Kernel 的 run ID 是必需输入。
- `refusal_review.py`：新增只适用于 v3 Kernel 运行的首错复核；旧 v1/v2 归档仍走既有路径。对脱敏 subject 仅在拒绝不能成立可被证明时作 `CORRECT` 判定。
- `artifacts.py`、`benchmark_runner.py`、`benchmark_report.py`、`evaluation_inputs.py`、`evaluation.py`、`quality_baseline.py`：新增严格身份配对、v3 scoring bundle 装载和 evaluator v5 身份。
- 回归测试覆盖作用域前置、Kernel finalized 顺序、隐私与首次出现等价编号、subject/编号一致性、缺少 Kernel run ID 时显式失败、脱敏 subject 去重与可绑定性、截断、拒绝后的额外工具调用、旧 bundle 兼容以及 v3 round-trip。

## 验证

- `uv run ruff check .`：通过。
- `uv lock --check`：通过，105 个依赖解析完成。
- `git diff --check`：通过。
- `uv run pytest tests/unit -q`：**1149 passed, 5 skipped**（98.09 秒）。
- `uv run pytest tests/e2e/test_benchmark_synthetic_orchestration.py -q`：**3 passed**（22.13 秒）。
- `uv run pytest tests/e2e -m 'not real_model' -q`：未完成。该集合包含多轮 dbt 重放；在长时间 dbt 子流程中手动停止，没有把中断结果作为通过或失败。受本次 schema/artifact 变更直接影响的 synthetic orchestration E2E 已单独通过。

## v31 只读兼容核对

冻结工作树仍为 detached HEAD `91582e588e90156236040b87c18d0ecc2dccfbd2`，manifest SHA-256 为 `f4196010e0b8ff3fdd9fdf26f2c877bb04bd4d9cb4db5d612a13541fca2b4c9a`。冻结归档声明 106 个 cells；只读 ledger 仍为 79 个连续终态项，归档源聚合为 476 个文件、SHA-256 `41cd9261567dfdb7582f701ea45dca4e6fb459146fd7c6539889161391b867a0`。

使用当前实现严格加载了 v31 的 **78 个 scoring-input bundles**，并复核其中 21 个 refusal verdict。属于原始 Kernel 合同路径的七次拒绝仍全部为 `INDETERMINABLE/KERNEL_CONTRACT_PATHWAY`；新 v3 分支没有重解释旧摘要。冻结工作树的 Git 状态干净，未写入归档。

## 边界

此实现为后续另行批准的新运行提供能力。它不改变 v31 的 0 CORRECT / 0 FALSE_REFUSAL / 7 INDETERMINABLE 结论，不构成新的 manifest freeze、真实模型测量、正式 benchmark 或 push 授权。
