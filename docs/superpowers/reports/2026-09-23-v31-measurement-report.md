# v31 真实测量报告:4/106 终态,`RUN_SETUP_ERROR` 停机(2026-09-23)

- 授权链:preflight 两次失败(环境侧根因 + 执行侧装配失误,回执均归档)→ 所有者裁定
  环境方案并放行 → **preflight PASSED(13/13)** → 一次 suite → 4 格后
  `RUN_SETUP_ERROR` 停机(规则内立即停)。本报告为终态产物,后续动作待裁定。
- 执行身份:worktree `91582e5`(porcelain 全程干净)、清单 sha256
  `f4196010e0b8ff3fdd9fdf26f2c877bb04bd4d9cb4db5d612a13541fca2b4c9a`、
  绑定修订 `e5d81d9`;隔离 uv 0.11.24 + Python 3.12.10 + worktree .venv dbt;
  结束后基线指纹复验恒等 F0 `e5c7848e…`。
- 套件终档:`artifacts/benchmarks/p1-formal-v31/` 下 doctor.json
  sha256 `103eca47…a805b`、ledger.jsonl sha256 `ae808bfa…5f2e60`(worktree 本地保留)。

## 1. 结果总表

| seq | 场景 | 策略 | 终态 | 评测失败项 | 恢复 | 模型请求 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | schema_type_change…_a | STATIC_SKILL | **COMPLETED(PASSED)** | — | HEALTHY | 4 |
| 2 | schema_type_change…_a | DIAGNOSTIC_KERNEL | FAILED | 5 项(诊断终态 MODEL_ERROR/MODEL_PROTOCOL_ERROR) | HEALTHY | 2 |
| 3 | schema_type_change…_b | DIAGNOSTIC_KERNEL | FAILED | REQUIRED_EVIDENCE_TYPES_PRESENT、INSUFFICIENCY_GAP_DECLARED | HEALTHY | 5 |
| 4 | schema_type_change…_b | STATIC_SKILL | FAILED(**RUN_SETUP_ERROR**) | ENVIRONMENT_VERIFIED:BUILD_FAILED | HEALTHY | 0 |

预算合计:**11 次模型请求,123,610 input / 30,041 output tokens,18 次工具调用**
(本地计量;费用以 provider 账目为准)。无 429、无 provider 传输异常。

## 2. 停机归因:环境侧已记录不稳定,非模型质量问题

格 4 的 dbt 构建在**编译期**失败:`internal error in regular expression engine`
(`.dig/lab/runs/<run4>/dbt/logs/dbt.log`,0.69s,解析阶段)。与本仓已记录的
本机 dbt 子进程不稳定(`0xC0000005` 家族,见 `2026-08-30-m7-development-smoke.md`)
同源,处置纪律不变:不据此修改产品、不加平台 workaround。停止规则
(RUN_SETUP_ERROR → 立即停)按设计触发;格 4 的恢复(HEALTHY)与该失败无冲突
——失败发生在恢复之后的本格构建。

## 3. 提交门与 P-1 复核结果(本轮核心问题)

对三格真实运行逐事件盘点(digest 校验的 scoring-inputs 重载后复核):

1. **`GATE_INTERNAL_ERROR` = 0**(M3 口径,观测样本内)。
2. **harness 门层拒绝(I1/I2)= 0**:三格未出现任何 `CLAIM_SUPPORT_REQUIRED` /
   `GAP_RECEIPT_REQUIRED` 事件——门在真实通路中零触发,也因此**无误拒可判**
   (门未拦截任何合法提交,同样未放过任何违例;样本过小,不作比率推断)。
3. **kernel 合同层拒绝 1 次(seq3)**:`UNRESOLVED_EVIDENCE_UNBOUND`(带
   `rejected_decision` 载荷)→ 模型随后提交修正后的 INSUFFICIENT_EVIDENCE →
   **accepted=True**。这正是 D2 设计的「拒绝 → 修复 → 接受」正面通路,与上批
   run 4 的观察同类。
4. **P-1 三分类复核**:seq3 的拒绝判为 **INDETERMINABLE
   (KERNEL_CONTRACT_PATHWAY)**——kernel 合同判据属另一复核通路(未实现,登记
   待办);如实记录,不判清白也不判违例。P-1 读取端在真实归档上端到端工作
   (重载、盘点、判定全链无错)。
5. lineage 候选转发修复(fc9cb74)的效力边界不变:本轮无
   `NODE_ARGUMENT_NOT_PROVEN` 拒绝出现,与「该缺陷丢失的是 lineage 起点维度」
   的定性相容,但不构成因果证明(样本 3 格)。

## 4. 本轮问题的回答(归因纪律:整套实现变化的比较)

**修复 lineage 候选转发并加入提交门后,真实运行的表现**:实现在真实通路中
**无内部错误、无误拒迹象,拒绝-修复通路按设计工作**;但本轮仅 3 个有效格,
**不构成任何比率的证据**。

与 v29/v30 的对比是**整套实现变化**的对比(协议身份 v20、两道提交门、门面修复、
P-1 归档同时进入),**不能把差异归因于提交门**。定性观察(样本过小,仅登记):

- seq3 的失败模式(gap 声明与合同不匹配)与 v29/v30 离线分析指出的
  「insufficiency-gap declaration」弱点**同类延续**,未见此维改善。
- seq2 的 kernel 协议终态(MODEL_PROTOCOL_ERROR,第 2 次请求即终止)与 v30
  观测过的协议/工具限流终态同类;本轮修复未见对此类终态的改善证据。
- seq1 静态格 PASSED,含 `p1.controller.v20` 身份下门全开的完整通路。

## 5. 后续待裁定

1. **套件粘滞**:ledger 拒绝重复终态格,本套件身份内 4 格不可原位补测;继续
   测量须**新身份**(延续机制),是否继续、何时继续由所有者裁定。
2. 登记待办不变:v29/v30 历史归档缺陷暴露面离线检查;kernel 合同拒绝的离线
   重算通路;`refusal_review` 的 CLI 入口(如需)。
3. 环境资产保留:隔离 uv 0.11.24(`tools-uv-0.11.24`)、launcher 装配方案
   (六键 + User 作用域 key,报告 `…preflight-failed-2.md` §4);临时脚本已删。
