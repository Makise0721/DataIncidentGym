# p1-formal-v7 Kernel v8 同源化修订 smoke 报告

## 批次身份

- Manifest: `p1-formal-v7`，SHA-256 `f16348bc422ff7a1f86279c64e161e531b747e12ae0dd38fdbdf4093a07cdb4e`
- implementation revision: `abeca0a0a0613e24d7798660b7132f090187d6a0`（分支 CI `34029807455` 与 main CI `34031722449` 全绿；前一 SHA `99dbfdc` 的 CI run `34029515535` 因身份批准补入实现而被取代）
- 包装提交：`9ff672a`；工作分支 `codex/benchmark-smoke-v7`
- 基线 fingerprint：`e5c7848eb2b7af16ec37463650b63dbb5d1f52293adf456003517b51c496cb18`（与 v3–v6 一致）
- 归档：独占一次，`reports/benchmark/p1-formal-v7/`，来源聚合 SHA-256 `b92c924051b10d751b1c862a6f60a2e02c8716a682da436ff49b41e45da6ed7e`

## 判定门结论

**两项指标首次一过一崩、综合门仍未同时达标，按计划不进入 Task 7。**

| 指标 | 门槛 | v7 实测 | 历史轨迹 |
|---|---|---|---|
| Kernel 工具成功率 | ≥ 80% | 28/32 = **87.50% ✓** | 75.00% → 66.67% → 87.50% |
| Kernel 非 MODEL_ERROR 终态 | ≥ 2/4 | **0/4 ✗** | 1/4 → 2/4 → 0/4 |

## 逐格结果（8/8 终态，0 RUN_SETUP_ERROR；ENVIRONMENT/RECOVERY 8/8 PASS）

| seq | 策略 | 诊断终态 | 评测 | 请求 | 工具尝试/成功 | 终态原因 |
|---|---|---|---|---|---|---|
| 1 | STATIC | MODEL_ERROR | FAILED | 3 | 7/7 | MODEL_TOOL_CALL_LIMIT |
| 2 | KERNEL | MODEL_ERROR | FAILED | 6 | 8/8 | MODEL_TOOL_CALL_LIMIT（零工具错误） |
| 3 | KERNEL | MODEL_ERROR | FAILED | 8 | 11/8 | MODEL_REQUEST_LIMIT（假设记账 3 错） |
| 4 | STATIC | MODEL_ERROR | FAILED | 4 | 8/8 | MODEL_TOOL_CALL_LIMIT |
| 5 | STATIC | CONFIRMED | PASSED | 5 | 7/7 | — |
| 6 | KERNEL | MODEL_ERROR | FAILED | 6 | 7/6 | OUTPUT_SCHEMA_REJECTED(final_result) |
| 7 | KERNEL | MODEL_ERROR | FAILED | 5 | 6/6 | OUTPUT_SCHEMA_REJECTED(final_result) |
| 8 | STATIC | INSUFFICIENT_EVIDENCE | FAILED | 7 | 8/7 | 缺 REQUIRED_EVIDENCE_TYPES + GAP_DECLARED |

安全与环境门全部通过；4 个 Kernel 格协议门全 PASS；数据库恢复健康基线（113/99）。

## 核心发现

1. **同源化修复彻底解决关系纪律问题**：`RELATION_NOT_ALLOWED`（工具层）与 kernel 门拒绝在 v6 中合计 9+ 次，v7 中 **0 次**。Kernel 工具成功率 87.50% 首次跨过 80% 门槛。三批轨迹证明瓶颈链条为：意图信封（v5）→ 关系纪律（v6）→ 已全部修复。
2. **剩余瓶颈转移至终态机制与预算算术，均属冻结合同下的模型能力边界**：
   - seq2：8/8 工具全部成功、零错误，调查设计需要第 9 次调用——8/8/2/300 预算下的方案规划失败；
   - seq6/7：调查干净完成，但最终 `KernelDecision` 被 finalize 门/输出校验拒绝（OUTPUT_SCHEMA_REJECTED），2 次输出重试耗尽——假设评估、claim 绑定与声明完整性是 M6 需求级要求；
   - seq3：假设记账错误（DUPLICATE_HYPOTHESIS ×1、HYPOTHESIS_REFERENCE_UNKNOWN ×2）——模型对"先注册后引用"纪律仍不稳定。
3. **STATIC 对照臂**：seq8 对 customer_b 给出正确弃权状态（INSUFFICIENT_EVIDENCE）但缺声明完整性；seq1 在 customer_a 上 7/7 工具后死于工具预算。2/4 STATIC 格死于 MODEL_TOOL_CALL_LIMIT，与 Kernel seq2 同构——8 次工具预算对这类调查而言偏紧是两臂共同 observations，但预算属需求锁定。
4. 三轮修订后的净结论：**harness 已完全干净（连续三批 0 RUN_SETUP_ERROR、环境/恢复满分），工具层与调查纪律已修复，剩余差距全部集中在"在 8/8/2/300 内产出满足 M6 合同的终态决策"这一模型能力项上。** 继续提示词/契约迭代的边际收益递减；进一步提升需要预算/合同层面的变更（超出 P0/P1 现有边界）。

## 探针与预算

- cell 模型请求合计 42（3+6+8+4+5+6+5+7）；preflight 一次通过（≤2 POST + 1 `/models`）；预算 8/8/2/300 未动。

## 过程记录

- 初版实现 `99dbfdc` 推送后启动 CI；冻结 Manifest 时发现 v8 计划中的身份批准（`p1-formal-v7`）漏入实现，随即补入提交 `abeca0a` 并重跑双 CI（`34029515535` 被取代，未取消成功但已无意义）。smoke worktree 重置至 `abeca0a` 后重新冻结（Manifest SHA 随之更新为当前值）。
- 沿用既知环境对策：keep-alive setsid 会话、`.env.diagnostic` 预复制、本地 submodule 克隆、bundle+Windows 侧推送。本轮 preflight 一次通过，无环境异常。

## 边界声明

- subset smoke（`subset.json` 在案），正式 reporter 已按预期拒绝；不进入任何正式分母，不构成模型质量结论。
- v1–v6 封存证据未动；v2 身份仍未冻结。
- 87.5% 工具成功率是 8 格 subset 上的观察，不外推为准确率；0/4 终态同样受小样本方差影响，但与三批轨迹一致。
