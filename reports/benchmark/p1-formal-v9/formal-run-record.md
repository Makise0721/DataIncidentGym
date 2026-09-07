# p1-formal-v9 正式批次结案记录

## 批次身份与执行事实

- Manifest: `p1-formal-v9`，SHA-256 `698752e82f3a1865bfc25bd41a52a0f77210529ce50a94c76c944d7e5393cfba`（106 格；模型 `mimo-v2.5-pro`；预算 8/8/2/300）
- implementation revision: `0c3cc61489e7a3c59023090a810981ea82b3662f`（含 v2 批次后 hardening：`eb01a68` diagnose 顶层 fail-closed + `0c3cc61` 身份批准；功能分支 CI `34087082375`、main CI `34091362491` 全绿；本地 unit 373/integration 34/e2e 47 全绿）
- 包装提交：`493f4af`；工作分支 `codex/benchmark-formal-v9`；基线 fingerprint 与历史批次一致
- preflight：无 selector 完整通过（doctor 13/13，`started_cells: 0`）
- 批次：唯一一次正式运行，`subset: false`；**终态 86/106 后 fail-stop**（`status: FAILED`，`cells: 86/106`）
- 归档：独占一次，`reports/benchmark/p1-formal-v9/`，来源聚合 SHA-256 `ab6079fa97f725ca353b6f7ea97beeae0fa2bc10d947e701ef967acc97681d9b`
- 数据库终态：恢复健康基线（113/99）；容器全程单次运行（`RestartCount=0`）

## fail-stop 定位

- 触发格：sequence 86（`schema_type_change_order_customer_a`，KERNEL_NO_SCHEMA），ledger 终态 `RUN_SETUP_ERROR`。
- 该格 `ENVIRONMENT_VERIFIED` 门 `expected=[RUN_SETUP_COMPLETE] / actual=[BUILD_FAILED]`，恢复 HEALTHY。
- 现场 dbt 日志：故障构建 **PASS=25 / ERROR=0 全部成功**——即注入的类型变更在构建执行时未生效，verifier 对意外健康构建 fail-closed。这与 v2 批次的失效模式（seq32 diagnosis 阶段异常）**不同类**。
- 前序上下文：83–85 格（NO_TOOL×2 + KERNEL_NO_LINEAGE，同族场景）全部以 HEALTHY 恢复收尾；随后 86 的注入未在构建中生效。触发条件未知：可能是特定顺序上下文下的 inject 路径问题（全部 smoke 与 e2e 均未覆盖此序列上下文），也可能是瞬态；数据库状态已被恢复覆盖，无法事后区分。

## 与 v2 批次的对照（修复有效性证明）

- v2 批次：seq32（orphan_payment_coupon_b / KERNEL）diagnosis 阶段异常逃逸 → fail-stop。
- v9 批次：**同一格 seq32 顺利通过**，批次推进 54 格至 seq86——`eb01a68` 的 diagnosis 顶层 fail-closed 修复按设计生效。
- v9 的新失效点是另一类（BUILD_FAILED / 注入未生效），两批合计证明：fail-stop 机制本身工作正常，但当前环境/协议在 106 格长跑下存在约每 50–86 格一次的偶发异常率。

## 结论（按计划处理表）

**结论固定为 `INVALID`**：终态格 86 < 106，正式 report 已按设计 fail-closed（ledger 不完整拒绝），不产出 `RESULTS.md`。本批次不产生任何 Kernel 优势、准确率或模型质量结论；86 格中 14 个 COMPLETED 与 72 个 FAILED 仅作为归档证据。

## 后续约束

- p1-formal-v9 身份已消耗，永不复用；v1–v8 全部封存证据未动。
- 下一次正式批次（需新身份 `p1-formal-v10+` 与用户授权）前，必须先完成两项取证：seq86 型「注入未生效」（可用 FunctionModel + 真实 lab 按 83→86 顺序离线重放，确定性验证）与残余的偶发异常率评估。
