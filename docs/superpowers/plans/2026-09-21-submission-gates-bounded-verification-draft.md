# I1/I2 提交门 · 有界真实验证授权书（草案，待所有者审定）

- 日期：2026-09-21。起草人：审计侧；授权人：项目所有者（**待签署，签署前不得执行其中任何操作**）。
- 授权依据：提交门实施终审 PASS（离线证据见
  [实施报告](../reports/2026-09-20-submission-gates-implementation.md) 与审计终审结论）；
  本草案对应验收阶梯第 2 步（有界真实验证），不包含第 3 步（新身份测量）。
- 执行基线：`main`，HEAD 必须含 `87edc59`。开始前核实完整 SHA 并记录；工作树不干净即停止。

## 0. 已核实前提（审计侧逐一验证，构成本草案的设计约束）

1. **门已在 HEAD 默认激活**：`EvaluationRunner` 对每个 run 装配 `SubmissionPolicy`
   （`3c5639f`），kernel/static/planner 三路径均装门；确定性参考路径无门。真实验证不需要
   任何代码改动。
2. **载体为 `eval run` 而非 benchmark run**：`eval run <case> --strategy <s>` 每次生成全新
   run_id，不经过清单预分配 run_id——因此不存在与 v29/v30 已归档运行的冲突，不写 subset
   标记、不触碰任何 suite 根（本工作区 `artifacts/` 下当前无 benchmark ledger 残留，已核实）。
   benchmark run 在本授权中**不被使用**。
3. **归档身份缺口（如实声明）**：门不在政策身份载荷内，本批归档的
   `controller_protocol_version` 仍为 `p1.controller.v19`、`STRATEGY_PROTOCOL_VERSION` 仍为
   `p1.strategy_adapter.v1`。即：带门运行的归档身份与不带门运行不可区分。这是阶梯第 2 步的
   已知且被接受的缺口——身份升版（`STRATEGY_PROTOCOL_VERSION` v2 等，实施报告 §4 清单）
   属阶梯第 3 步，本次不动。本批归档标记为**实验性观察产物**，不进入任何正式评估集合、
   不与 v29/v30 基线混算。
4. **模型切换零代码改动**：`DiagnosticSettings` 经环境变量覆盖
   （`DIG_DIAGNOSTIC_MODEL_BASE_URL` / `DIG_DIAGNOSTIC_MODEL_NAME` /
   `DIG_DIAGNOSTIC_MODEL_API_KEY`），CommandCode/DeepSeek 配对在代码中已有专门兼容档案
   （`openai_compatibility_kwargs`）。
5. **基线事实**：v30 部分运行中，拟验证的 3 个触发期望格（seq 8/9/13 对应的 STATIC 组合）
   全部 QUALITY FAILED 且会被门拒；3 个 PASSED 对照格（seq 5/6/17 组合）全部通过且门接受；
   3 个 kernel 对照格中 seq 7/10 在基线为 RUN_ERROR（provider 错误）、seq 14 未执行——
   kernel 对照组基线不完整，本次同时是 kernel 投影路径的首次线上观察。

## 1. 授权范围（一次性执行，共 9 次 `eval run`）

| 组 | case_id | strategy | 基线（v29/v30 语料） | 验证意图 |
| --- | --- | --- | --- | --- |
| 触发期望 | required_null_order_customer_b | STATIC_SKILL | QUALITY_FAILED，I2 拒（GAP_RECEIPT_REQUIRED） | 观察拒绝→修复或耗尽 |
| 触发期望 | duplicate_payment_coupon_a | STATIC_SKILL | QUALITY_FAILED，I1 拒（CLAIM_SUPPORT_REQUIRED） | 同上 |
| 触发期望 | orphan_payment_coupon_a | STATIC_SKILL | QUALITY_FAILED，I1 拒 | 同上 |
| kernel 对照 | required_null_order_customer_b | DIAGNOSTIC_KERNEL | RUN_ERROR（provider） | kernel 投影路径线上行为 |
| kernel 对照 | duplicate_payment_coupon_a | DIAGNOSTIC_KERNEL | RUN_ERROR（provider） | 同上 |
| kernel 对照 | orphan_payment_coupon_a | DIAGNOSTIC_KERNEL | STATUS_ERROR（wrong_abstention） | 同上 |
| PASSED 对照 | required_null_order_customer_a | STATIC_SKILL | PASSED，门接受 | 误拒金丝雀 |
| PASSED 对照 | required_null_order_customer_a | DIAGNOSTIC_KERNEL | PASSED，门接受 | 误拒金丝雀 |
| PASSED 对照 | silent_payment_drop_partition_a | STATIC_SKILL | PASSED，门接受 | 误拒金丝雀 |

每次 `eval run` 内含完整 lab 生命周期（inject → dbt build → 诊断 → 评测 → restore），
本授权覆盖其全部本地 PostgreSQL/dbt 操作。**不包含**：benchmark run/preflight/freeze、
manifest 或身份变更、T13 四场景、EVIDENCE_PLANNER、真实模型以外的任何外部调用、push。

模型请求合同上界：9 × 8 = **72 次**（输出重试消耗同一预算）；SDK/传输层重试另计。
72 不是 HTTP 请求总数或费用上界。

## 2. 执行前置条件（任一不成立即停止，均为只读检查）

1. HEAD 完整 SHA 记录，且为 `87edc59` 或其直系后代；工作树已跟踪文件干净。
2. `pipeline build` 只读指纹 == `e5c7848eb2b7af16ec37463650b63dbb5d1f52293adf456003517b51c496cb18`；不等即停止。
3. `uv run pytest tests/unit -q` 与 `uv run ruff check .` 在执行前最近一次全绿（终审已记录
   1089 passed / 5 skipped；若执行前代码有变动需先复跑）。
4. **不运行 `doctor`**；前置检查不触碰数据库写入。

## 3. 密钥与模型配置

密钥只经进程环境映射，不输出、不写 `.env.diagnostic`、不进入命令记录或报告；执行结束后在
`finally` 中恢复原环境（原不存在则删除）。格式示例（未执行）：

```powershell
$env:DIG_DIAGNOSTIC_MODEL_BASE_URL = "https://api.commandcode.ai/provider/v1"
$env:DIG_DIAGNOSTIC_MODEL_NAME = "deepseek/deepseek-v4.1-flash"
$env:DIG_DIAGNOSTIC_MODEL_API_KEY = $env:COMMANDCODE_API_KEY
```

## 4. 执行命令（按下表顺序串行；任一停止条件触发即终止后续）

```powershell
uv run data-incident-gym eval run required_null_order_customer_b --strategy static-skill
uv run data-incident-gym eval run duplicate_payment_coupon_a --strategy static-skill
uv run data-incident-gym eval run orphan_payment_coupon_a --strategy static-skill
uv run data-incident-gym eval run required_null_order_customer_b --strategy diagnostic-kernel
uv run data-incident-gym eval run duplicate_payment_coupon_a --strategy diagnostic-kernel
uv run data-incident-gym eval run orphan_payment_coupon_a --strategy diagnostic-kernel
uv run data-incident-gym eval run required_null_order_customer_a --strategy static-skill
uv run data-incident-gym eval run required_null_order_customer_a --strategy diagnostic-kernel
uv run data-incident-gym eval run silent_payment_drop_partition_a --strategy static-skill
```

每次 run 的 stdout/stderr 逐字捕获落盘（不入库敏感值）；9 个 run_id 全部记录。
无重跑、不替换失败格、不更换模型或样本；中断保留全部归档并报告实际完成量。

## 5. 必录观察项 O1–O8

| 编号 | 观察项 |
| --- | --- |
| O1 | 每格归档 trace 的 `EVIDENCE_GATE` 事件：拒绝码、时序位置（回合内）、是否最终接受 |
| O2 | 拒绝码分布：`CLAIM_SUPPORT_REQUIRED` / `GAP_RECEIPT_REQUIRED` / `GATE_INTERNAL_ERROR` 各自出现次数——**`GATE_INTERNAL_ERROR` 必须为零** |
| O3 | 拒绝后结局：重试后被接受（修复成功）vs 耗尽为 `MODEL_OUTPUT_RETRY_EXHAUSTED`，逐格记录 |
| O4 | **误拒为零**（**2026-09-21 修订**）：对每一次门拒绝事件，**逐次复核该次拒绝是否为误拒**。门允许模型收到可重试拒绝反馈后修正成功，属 D2 预期通路，**不构成违例**；**不得**用最终 PASSED 倒推先前拒绝有误。原规则「任何最终 evaluator PASSED 的格出现过门拒绝事件即违例」**已废止**（理由见下注） |
| O5 | 触发期望格是否实际触发预期门码；**模型本次直接做对而不触发属正常结果**，如实记录，不算不符 |
| O6 | 耗尽终态格的归档完整性：trace 与证据保留（D2 设计初衷），终态码可见 |
| O7 | 每格预算实测（模型请求/工具调用/输出重试/时长）对照合同上界 8/8/2/300 |
| O8 | 每次 run 后活库恢复；全部结束后只读指纹仍 == §2 的 F0 |

> **修订（2026-09-21，O4 判定口径修正；草案留档，仅作过程记录）**：O4 原写作「任何最终 evaluator
> PASSED 的格出现过门拒绝事件即违例」，该规则**不成立**——门的设计就是发出可重试的
> `ModelRetry`，模型收到反馈后修正成功属预期通路，用最终成功倒推先前拒绝有误是错误推理。
> 本文为草案（已被
> [正式授权书](2026-09-21-submission-gates-bounded-verification-authorization.md) 取代），
> 此注仅保持两份文本口径一致，不改变草案的历史留档性质。

## 6. 判定规则

- O4 任一违例、或 O2 中 `GATE_INTERNAL_ERROR` 非零 → **立即停止**，进入设计层复盘；
  本授权之后不自动发放点修复式重跑授权。
- O8 指纹不符 → 先恢复数据库，再停止并报告。
- provider 异常（429/5xx/协议错误）按既有 runner 行为停机并如实归因；不自行换端点重试。
- 其余结果（触发/未触发、修复/耗尽、PASSED/QUALITY_FAILED 分布）均为观察数据，不构成不符。

## 7. 费用上限（所有者签署时填写）

- 金额上限及币种：**【待所有者填写】**；计价来源、输入/输出 token 假设、请求上界 72 次的
  换算一并记录在放行回复中。未填写不得执行。

## 8. 明确不包含

- 身份升版、manifest 冻结、benchmark run、正式报告（阶梯第 3 步，另行授权）。
- 代码任何改动；若执行中暴露缺陷，停机记录，修复与重跑另行裁定。
- T13 场景、规划器策略、MCP/harness 侧装门（F6 推迟项）。
- 本批归档不进入评估集合，不与 v29/v30 基线混算；后续比较须单列"实验性装门批次"。

## 9. 产出

- 结果报告 `docs/superpowers/reports/2026-09-21-submission-gates-bounded-verification.md`：
  实际 HEAD、逐条命令与逐字 stdout、9 个 run_id、O1–O8 全量实测值、费用与用量、结论。
- 本授权书签署版随报告一并入库；草案阶段本文档仅为待审文本。
