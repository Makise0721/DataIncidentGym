# v31 真实测量报告:4/106 终态,`RUN_SETUP_ERROR` 停机(2026-09-23)

- **修订(2026-09-23 第二版,所有者核对纠正)**:第一版存在一处关键事实错误与多处过度
  结论,均已更正并保留原版于提交历史(`2d97b33`):
  1. **seq2 是明确的 provider HTTP 520**(trace 第 6 条:`stage=PROVIDER_RESPONSE`、
     `model_request_index=3`、`error_type=MODEL_API_ERROR`、
     `transport_diagnostic=transport=HTTP_520`)。第一版「零传输异常」**不成立**;
     失败发生在**第 3 次模型请求尝试**(metrics 的 2 次请求是完成计量,不含失败
     尝试,不能替代失败序号);与 v30 的 `MODEL_TOOL_CALL_LIMIT`(工具预算)**
     不同类**,不构成策略维度信号。
  2. **结论收紧**:I1/I2 零拒绝只说明本批未触发,不能推出「没有放过任何违例」;
     seq3 不能称「诊断推理完整」(最终仍缺 RELATION_HISTORY、缺口矩阵不符),
     其 kernel 拒绝→修正→接受也不能作为 I1/I2 反馈修复能力的证据(该拒绝属
     kernel 合同层);seq4 正则引擎异常与历史 `0xC0000005` 的同源性**未证明**,
     降级为「疑似同家族」。
  3. **「继续必须新身份」不是当前代码结论**,已更正:`BenchmarkRunner.run()` 跳过
     ledger 中已有终态的格、启动时重建滚动窗口,同身份续跑未执行格(seq5 起)的
     代码路径存在(可行性核查见 §5);不可行的只是**重跑/覆盖前四格**(ledger
     append-only)。新冻结不是继续测量的必经步骤。
- 授权链:preflight 两次失败(环境侧根因 + 执行侧装配失误,回执均归档)→ 所有者裁定
  环境方案并放行 → **preflight PASSED(13/13)** → 一次 suite → 4 格后
  `RUN_SETUP_ERROR` 停机(规则内立即停)。
- 执行身份:worktree `91582e5`(porcelain 全程干净)、清单 sha256
  `f4196010e0b8ff3fdd9fdf26f2c877bb04bd4d9cb4db5d612a13541fca2b4c9a`、
  绑定修订 `e5d81d9`;隔离 uv 0.11.24 + Python 3.12.10 + worktree .venv dbt;
  结束后基线指纹复验恒等 F0 `e5c7848e…`。
- 套件终档:`artifacts/benchmarks/p1-formal-v31/` 下 doctor.json
  sha256 `103eca47…a805b`、ledger.jsonl sha256 `ae808bfa…5f2e60`(worktree 本地保留)。

## 1. 结果总表

| seq | 场景 | 策略 | 终态 | 评测失败项 | 恢复 | 完成计量请求 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | schema_type_change…_a | STATIC_SKILL | **COMPLETED(PASSED)** | — | HEALTHY | 4 |
| 2 | schema_type_change…_a | DIAGNOSTIC_KERNEL | FAILED | 5 项(终态 MODEL_ERROR;根因 **provider HTTP 520**,见修订 1) | HEALTHY | 2(失败在第 3 次尝试) |
| 3 | schema_type_change…_b | DIAGNOSTIC_KERNEL | FAILED | REQUIRED_EVIDENCE_TYPES_PRESENT、INSUFFICIENCY_GAP_DECLARED | HEALTHY | 5 |
| 4 | schema_type_change…_b | STATIC_SKILL | FAILED(**RUN_SETUP_ERROR**) | ENVIRONMENT_VERIFIED:BUILD_FAILED | HEALTHY | 0 |

预算合计:**11 次完成计量的模型请求,123,610 input / 30,041 output tokens,18 次工具
调用**(本地计量;失败尝试不计入 requests,费用以 provider 账目为准)。
传输异常:**1 次(seq2,HTTP 520)**;无 429。

## 2. 停机归因:构建阶段环境失败

格 4 的 dbt 构建在**编译期**失败:`internal error in regular expression engine`
(`.dig/lab/runs/<run4>/dbt/logs/dbt.log`,0.69s,解析阶段)。可排除为该格的
**模型质量**失败(失败发生在模型参与之前);该异常与本机历史记录的 dbt 子进程
故障(`0xC0000005` 家族)**疑似同家族,同源性未证明**——处置纪律不变:不据此
修改产品、不加平台 workaround。停止规则(RUN_SETUP_ERROR → 立即停)按设计触发;
格 4 恢复(HEALTHY)与该失败无冲突(失败在恢复之后的本格构建)。
seq4 的 dbt 完整日志已保留待环境侧调查。

## 3. 提交门与 P-1 复核结果(本轮核心问题)

对三格真实运行逐事件盘点(digest 校验的 scoring-inputs 重载后复核):

1. **`GATE_INTERNAL_ERROR` = 0**(观测样本内)。
2. **harness 门层拒绝(I1/I2)= 0**:本批三格未出现任何 `CLAIM_SUPPORT_REQUIRED` /
   `GAP_RECEIPT_REQUIRED` 事件。**这只说明门未触发**:不能推出「没有放过任何
   违例」,也不能据 3 格作任何比率或漏放推断。
3. **kernel 合同层拒绝 1 次(seq3)**:`UNRESOLVED_EVIDENCE_UNBOUND`(带
   `rejected_decision` 载荷)→ 模型随后提交修正后的 INSUFFICIENT_EVIDENCE →
   **accepted=True**。这是 kernel 合同层「拒绝 → 修复 → 接受」通路的一次真实
   观测(与上批 run 4 的观察同类);**它不构成 I1/I2 门反馈修复能力的证据**
   (拒绝层级不同),且该格最终仍因缺 RELATION_HISTORY 证据、声明缺口矩阵与
   合同不符而评测失败——拒绝后修正的是合同违约,不是证据完整性。
4. **P-1 三分类复核**:seq3 的拒绝判为 **INDETERMINABLE
   (KERNEL_CONTRACT_PATHWAY)**——kernel 合同判据属另一复核通路(未实现,登记
   待办);如实记录,不判清白也不判违例。P-1 读取端在真实归档上端到端工作
   (重载、盘点、判定全链无错)。
5. lineage 候选转发修复(fc9cb74)的效力边界不变:本轮无
   `NODE_ARGUMENT_NOT_PROVEN` 拒绝出现,与「该缺陷丢失的是 lineage 起点维度」
   的定性相容,但不构成因果证明(样本 3 格)。

## 4. 本轮问题的回答(归因纪律:整套实现变化的比较)

**修复 lineage 候选转发并加入提交门后,真实运行的表现**:实现在真实通路中**无
内部错误**(GATE_INTERNAL_ERROR=0),门的拒绝-修复机制在被触发的层面
(kernel 合同)按设计工作;但本轮仅 3 个有效格,**不构成任何比率证据**,门层
I1/I2 在本批未获触发样本。

与 v29/v30 的对比是**整套实现变化**的对比(协议身份 v20、两道提交门、门面修复、
P-1 归档同时进入),**不能把差异归因于提交门**。定性观察(样本过小,仅登记):

- seq3 的失败模式(缺 RELATION_HISTORY、缺口声明与合同不匹配)与 v29/v30 离线
  分析指出的「insufficiency-gap declaration」与证据采集完整性弱点**同类延续**,
  未见此维改善。
- seq2 是 **provider HTTP 520 瞬时故障**(修订 1),属传输维度,与 v30 的
  `MODEL_TOOL_CALL_LIMIT` 终态不同类,不能用于评价策略改善或退化。
- seq1 静态格 PASSED,含 `p1.controller.v20` 身份下门全开的完整通路。

## 5. 续跑可行性核查(不发请求,真代码 + 真状态)

所有者更正(修订 3)成立。以真实 `BenchmarkRunner.for_project` 实例对当前
worktree 状态做只读核查,结论:

| 前提 | 结果 |
| --- | --- |
| ledger 状态 | 4 条全终态;run() 将 `continue` 跳过全部 4 格,从 seq5 起 |
| 前四格重跑/覆盖 | **不可行**(ledger append-only,无覆盖路径)——如需重测前四格只能新身份新实验 |
| `assert_receipt_scope` | OK(现有 doctor receipt 对全套件范围有效) |
| `is_receipt_acceptable` | True(PASSED + probe_required) |
| 滚动窗口现状 | model-backed 终态标志 [T,F,F,F],4/12,**未触发暂停** |
| 未启动格归档残留 | 无(不触发 `artifact already exists`) |
| `_verify_checkout`(run 启动) | worktree 仍在 `91582e5`,检出门此前已实证 PASSED |

即:**同身份续跑未执行格(seq5–106)当前无代码或状态阻断**;是否执行、以及选择
「同身份续跑」还是「新身份新实验」属所有者裁定。新冻结**不是**必经步骤。

## 6. 后续与登记待办

1. 待裁定:同身份续跑(seq5 起,滚动窗口与停止规则照旧)或新身份新实验;
   两者都不改产品、不重冻(前者完全复用现清单与回执)。
2. 环境侧调查(登记):seq4 dbt 正则引擎异常——日志保留于
   `.dig/lab/runs/a175df…/dbt/logs/dbt.log`;建议单例复现(dbt build 于同
   worktree)以界定故障面,属环境调查,不触产品。
3. 登记待办不变:v29/v30 历史归档缺陷暴露面离线检查;kernel 合同拒绝的离线
   重算通路;`refusal_review` 的 CLI 入口(如需)。
4. 环境资产保留:隔离 uv 0.11.24(`tools-uv-0.11.24`)、launcher 装配方案
   (报告 `…preflight-failed-2.md` §4);临时脚本已删。
