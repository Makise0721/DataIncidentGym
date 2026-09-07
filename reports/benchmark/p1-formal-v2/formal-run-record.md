# p1-formal-v2 正式批次结案记录

## 批次身份与执行事实

- Manifest: `p1-formal-v2`，SHA-256 `a14cd820f178e495027d6067d44653692998b065a2f294642b7004a9921a00e9`（106 格：94 model-backed + 12 FIXED_RULE；模型 `mimo-v2.5-pro`；预算 8/8/2/300）
- implementation revision: `3a245a6bd809569afc2abdcf9e02f3f1cd7d22cb`（功能分支 CI `34040445935`、main CI `34042635722` 全绿）
- 包装提交：`1b5b460`；工作分支 `codex/benchmark-formal-v2`；基线 fingerprint 与 v3–v8 一致
- preflight：无 selector 完整通过（doctor 13/13 PASSED，`model_probe_required: true`，`started_cells: 0`）
- 批次：唯一一次正式运行，`subset: false`；**终态 32/106 后 fail-stop**（`status: FAILED`，`cells: 32/106`）
- 归档：独占一次，`reports/benchmark/p1-formal-v2/`，来源聚合 SHA-256 `0c158240eeb3e8bbdb9552324a8b37542ae7289a47d889bcfaf7bd24cda9f951`（32 格 + suite 文件）
- 数据库终态：恢复健康基线（113/99），PostgreSQL 全程单次运行无重启（`RestartCount=0`）

## fail-stop 定位

- 触发格：sequence 32（`orphan_payment_coupon_b`，DIAGNOSTIC_KERNEL），ledger 终态 `RUN_SETUP_ERROR`。
- 该格 setup 链（reset/inject/build）成功，**diagnosis 阶段异常**：`ENVIRONMENT_VERIFIED` 门 `expected=[RUN_SETUP_COMPLETE] / actual=[DIAGNOSIS_FAILED]`，恢复门 HEALTHY。
- 依既有策略（setup/harness 异常 → 当前格终态化后 fail-stop，避免 harness 受损时继续消耗预算），runner 正确停止批次。这符合 postmortem 缺陷 #5 的修复语义，不是回归。

## 结论（按计划 Task 7 Step 4 处理表）

**结论固定为 `INVALID`**：终态格 32 < 106，批次不完整，不得生成正式报告（`benchmark report` 已按设计 fail-closed：`benchmark ledger does not contain exactly two entries per cell`），不产生 `RESULTS.md`。

本批次**不产生任何 Kernel 优势、准确率或模型质量结论**；32 格中的 7 个 COMPLETED 与 25 个 FAILED 仅作为归档证据存在，不进入任何分母表述。

## 已知边界与继承

- diagnosis 阶段的偶发异常（DIAGNOSIS_FAILED）在 v8 之前的 smoke 中未出现过；单次正式批次无法区分"模型/供应商偶发协议异常"与"环境瞬态"，且按一次性纪律**不重跑、不补跑、不重新冻结本 Manifest**。
- 判定门证据链（p1-formal-v5/v6/v7/v8，四个 subset smoke）保持有效；p1-formal-v2 身份已随本次执行消耗，永不复用。
- 若未来重启正式批次，需另立新的 Manifest 身份（`p1-formal-v9+`）、新的授权与新的判定前置，并先取证 `DIAGNOSIS_FAILED` 的触发条件（Trace 无 MODEL_PROTOCOL 事件，异常发生在 controller 诊断执行层）。
