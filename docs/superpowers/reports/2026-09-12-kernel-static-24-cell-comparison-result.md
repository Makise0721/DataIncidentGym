# Kernel 与 Static：24 格配对探索测量结果（一次性汇总）

日期：2026-09-12。协议：`2026-09-12-kernel-static-24-cell-comparison-protocol.md`（运行前定稿；判据与固定措辞未回改）。身份 `p1-formal-v22`（批准 `22d621d`、冻结 `40c7e82`，manifest SHA-256 `c1f8c5403394c245dc7dc8fcfb1dc0980eb931b4322cf4d89d33d0233845f730`），kernel = `p1.kernel.v18` / controller `p1.controller.v19`，static = `p1.static.v5`，模型 `mimo-v2.5-pro` @ `openai-compatible`（两策略 policy identity、预算、模型设置、场景摘要均与 v21 逐项一致，冻结后核对）。preflight 绑定 24 格 PASSED（模型探针单列，不入分母）；单次 run 串行完成 24/24 格，无补跑。

## 1. 主结论（固定措辞）

**12 对全部有效**（无已确认环境污染：唯一 MODEL_ERROR 为 seq18 的 300 秒诊断超时 `MODEL_TIMEOUT`，无 provider 错误记录，按 §6.1 计有效策略失败；24 格 recovery 均为 HEALTHY）。

- **K（kernel 通过）= 5/12；S（static 通过）= 2/12；通过率差 = 3/12 = 25 个百分点。**
- **B = 2，W（仅 kernel 通过）= 3，L（仅 static 通过）= 0，F（双败）= 7；恒等式 B+W+L+F=12，K−S=W−L=3 成立。**
- 按预先固定措辞：**"本次样本 kernel 整格通过更多"**（W>L）。不宣称总体概率优势、泛化优势或某项修复的因果效果；每格各一次，不测重复稳定性。

## 2. 配对结果表

| 对 | 场景 | 类别 | static 格 | static 终态 → 评分 | kernel 格 | kernel 终态 → 评分 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | schema_type_change_order_customer_a | **B** | S1 `28d954a5` | CONFIRMED → PASSED | K2 `e858ebb1` | CONFIRMED → PASSED |
| 2 | schema_type_change_order_customer_b | F | S4 `e03b8dae` | MODEL_ERROR → FAILED | K3 `e4df7d4e` | INSUFFICIENT_EVIDENCE → FAILED |
| 3 | required_null_order_customer_a | **W** | S5 `ad4aec77` | CONFIRMED → FAILED | K6 `f2da003c` | CONFIRMED → PASSED |
| 4 | required_null_order_customer_b | F | S8 `9201e756` | INSUFFICIENT_EVIDENCE → FAILED | K7 `d06dd4ce` | INSUFFICIENT_EVIDENCE → FAILED |
| 5 | duplicate_payment_coupon_a | F | S9 `a7232517` | CONFIRMED → FAILED | K10 `40e459aa` | CONFIRMED → FAILED |
| 6 | duplicate_payment_coupon_b | **W** | S12 `d411f1e9` | INSUFFICIENT_EVIDENCE → FAILED | K11 `27644871` | INSUFFICIENT_EVIDENCE → PASSED |
| 7 | orphan_payment_coupon_a | F | S13 `b9c04499` | CONFIRMED → FAILED | K14 `e470d90a` | CONFIRMED → FAILED |
| 8 | orphan_payment_coupon_b | F | S16 `992f777c` | INSUFFICIENT_EVIDENCE → FAILED | K15 `6f2f6941` | INSUFFICIENT_EVIDENCE → FAILED |
| 9 | silent_payment_drop_partition_a | F | S17 `6ad8d668` | CONFIRMED → FAILED | K18 `9ec68662` | MODEL_ERROR → FAILED |
| 10 | silent_payment_drop_partition_b | F | S20 `be9330d3` | INSUFFICIENT_EVIDENCE → FAILED | K19 `48fc3be0` | INSUFFICIENT_EVIDENCE → FAILED |
| 11 | order_volume_pattern_a | **B** | S21 `5991ca90` | NO_INCIDENT → PASSED | K22 `93d62382` | NO_INCIDENT → PASSED |
| 12 | order_volume_within_sla | **W** | S24 `4ce47ccc` | NO_INCIDENT → FAILED | K23 `2f0d4840` | NO_INCIDENT → PASSED |

（run_id 为缩写；完整 32 位 ID 以 `artifacts/benchmarks/p1-formal-v22/ledger.jsonl` 与各 bundle 为准。）

**失败检查码明细**（主指标之外的事实记录）：

- kernel 败 7 格：K3 `REQUIRED_EVIDENCE_TYPES_PRESENT`；K7/K15/K19 `INSUFFICIENCY_GAP_DECLARED`；K10 `REQUIRED_EVIDENCE_TYPES_PRESENT`；K14 `REQUIRED_EVIDENCE_TYPES_PRESENT`；K18 MODEL_ERROR（先前 `HEALTH_WATERMARK_NOT_PROVEN` 拒绝后 `MODEL_TIMEOUT`，另报 STATUS_EXACT/ROOT_CAUSE_ACCEPTED/AFFECTED_ASSETS_EXACT/REQUIRED_EVIDENCE_TYPES_PRESENT/CLAIM_EVIDENCE_COMPATIBLE）。
- static 败 10 格：S4 STATUS_EXACT+REQUIRED+GAP_DECLARED；S5/S16 REQUIRED；S8 GAP_DECLARED；S9 REQUIRED+CLAIM；S12 REQUIRED+GAP_DECLARED；S13 REQUIRED+CLAIM；S17 CLAIM；S20 GAP_DECLARED；S24 REQUIRED+CLAIM+POSITIVE_HEALTH_EVIDENCE。
- 共性事实：`REQUIRED_EVIDENCE_TYPES_PRESENT` 出现在 static 10 败中的 8 格、kernel 7 败中的 5 格；`INSUFFICIENCY_GAP_DECLARED`（缺口矩阵）只出现在弃答格。这些是描述性分组，不改主结论。

## 3. 成本次指标（不抵消正确性差异）

| 策略 | 请求合计 | 工具调用合计 | 输入 token | 输出 token | 诊断耗时合计 | 请求中位 | 耗时中位 | 输入 token 中位 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| kernel（12 格） | 46 | 61 | 560,406 | 84,771 | 2,400,377 ms（40.0 min） | 4 | 176.9 s | 48,964 |
| static（12 格） | 57 | 71 | 393,764 | 70,692 | 2,165,427 ms（36.1 min） | 4.5 | 179.2 s | 34,287 |

- kernel 输入 token 高于 static（+42%），请求与工具调用更少；耗时合计略高（+11%）。无 provider 计费数据，不虚构金额。
- 双方均成功的 2 对（B，分母=2）：kernel 输入 78,420 / 输出 11,110；static 输入 69,220 / 输出 12,175。
- 固有差异披露：kernel 三个输出工具、static 单输出工具，输出重试按工具累计——有效输出重试总量不同，本批未触发任何终止性协议事件（kernel 侧无 `MODEL_PROTOCOL` 终止；seq18 的超时属诊断时限）。比较对象是两个完整策略，不把差异单独归因于门禁。

## 4. 纪律与边界

- 无补跑、无按表现停机、无运行中修改；用户取消/外部中断未发生。
- 历史定向批次与本批分开；本批不并入任何总体通过率；不与 v15–v21 的结果混算。
- 若需重复验证 kernel 领先，另设固定预算与协议；本批不自动追加重跑或新场景。
- L=0 意味着本样本中 static 的通过格全部被 kernel 覆盖；此为描述事实，不外推。

## 5. 复核入口

- suite：`artifacts/benchmarks/p1-formal-v22/`（doctor.json、subset.json、ledger.jsonl）
- 24 格 bundle：`artifacts/<run_id>/`（run_id 见 ledger）
- 协议：`2026-09-12-kernel-static-24-cell-comparison-protocol.md`（含模型配置决议补记）
- 本文件为一次性比较报告；子集运行不出正式报告。
