# 离线诊断质量基线报告：v29 + v30 两批（2026-09-20，整改版）

- 依据：[spec v2](../specs/2026-09-20-offline-diagnostic-quality-baseline-spec.md) 与
  [独立复核报告](2026-09-20-quality-baseline-independent-audit.md)。本版按复核的四项缺陷整改后
  重新生成；**全部逐格表与汇总由同一结构化结果程序化生成（`render_markdown`），不再手抄**。
- 验证前置（本版实测）：两批 manifest 的 result_inputs 五项摘要与当前树逐字一致；
  **31/31 个终态格全部通过严格加载与冻结 evaluator 重算**（此前仅 26/31——MODEL_ERROR 分支
  绕过前置，已修复）；批次—manifest—run 身份绑定与归档/评分包平面一致性全部核对通过。
- 两批赛程前缀重叠、按阈值提前停止：**合计仅作描述性并列，非独立统计样本**。

## 1. 整改对照（复核四项缺陷）

| 缺陷 | 整改 | 验收证据 |
| --- | --- | --- |
| P1-1 MODEL_ERROR 绕过身份与重算 | 所有终态格走同一严格路径（严格 ledger → 绑定核对 → 严格 bundle → 平面一致性 → 逐格重算），RUN_ERROR 只是分类不是豁免；归档 diagnosis 与评分包 diagnosis 必须逐字段一致 | 重算调用计数 **31/31**；对抗复现（把某格 diagnosis.json 换成错误 run_id 的 MODEL_ERROR）→ `CORRUPT: archived diagnosis does not match the scoring bundle` |
| P1-2 重复终态重复计数 | 严格 ledger 校验：重复 STARTED / 重复终态 / 无 STARTED 的终态一律拒绝（批次级报错） | 对抗复现（内存追加重复 COMPLETED 行）→ `duplicate terminal entry for run …` |
| P1-3 历史摘要归因错误 | 实现**有测试的历史序列化兼容**：遗留包（trace 事件无 M23 字段）的摘要按"剥除该默认字段后的 canonical JSON"复算并与**原始记录摘要**比对；保留原摘要；真实不匹配与新字段包一律报错；复刻 loader 的 v2 诊断还原与 kernel_state 类型还原及还原后摘要稳定性检查 | v29 seq 7/10 与 v30 seq 1/4 四个包严格等价加载成功且记录摘要保持原值；篡改遗留包 → `legacy diagnosis digest mismatch`；新字段包错摘要 → 报错。**撤回**此前"写入缺损/宽松读取"与"v30 两 bundle 未归档"两项错误结论 |
| P1-4 逐格映射错误 | 报告全表由 `render_markdown(batches)` 从结构化结果生成，以 batch/run_id/sequence 绑定；渲染有对应性测试 | v30 STATUS_ERROR 逐格为 **10、14、18**（wrong_abstention），与复核一致；此前的第 12 格标注与后续错配作废 |

**撤回与更正（2026-09-20，I1/I2 spec 的独立审计发现）**：`_axis3` 的 trace 过滤曾误用
`event_type == "TOOL_TRACE"`（真实字面量为 `TOOL_CALL`），过滤恒为空并使未见证判定恒为 False；
修复后正确语义：v30 未见证为 **1（seq 15）**，seq 12 实际有真实收据（原"2（12/15）"作废）；
v29 未见证 0 不变。该缺陷由 I1/I2 实施 spec 的独立审计发现，分析器与本节已同步修复/更正；
其余数字不受影响（31/31 重算、类别分布、其余轴计数均不变）。

合同分离（按复核 §合同处理）：分析器输出同时包含**完整性清单**（CORRUPT / NOT_EXECUTED 显式列示）
与**验证通过的质量基线**；CORRUPT 剔除不用于制造"看似完整"的基线。本两批 CORRUPT = 0。

## 2. 逐格表（程序化生成）

## p1-formal-v29

| seq | scenario | strategy | category | direction | axis1 | axis2 | axis3 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | schema_type_change_order_customer_a | STATIC_SKILL | PASSED |  |  |  |  |
| 2 | schema_type_change_order_customer_a | DIAGNOSTIC_KERNEL | PASSED |  |  |  |  |
| 3 | schema_type_change_order_customer_b | DIAGNOSTIC_KERNEL | QUALITY_FAILED |  | RELATION_HISTORY |  | missing: extra:TRANSFORMATION_DEFINITION/model.jaffle_shop.customers/NOT_OBSERVABLE unwitnessed: |
| 4 | schema_type_change_order_customer_b | STATIC_SKILL | RUN_ERROR |  |  |  |  |
| 5 | required_null_order_customer_a | STATIC_SKILL | PASSED |  |  |  |  |
| 6 | required_null_order_customer_a | DIAGNOSTIC_KERNEL | QUALITY_FAILED |  | RELATION_SCHEMA |  |  |
| 7 | required_null_order_customer_b | DIAGNOSTIC_KERNEL | RUN_ERROR |  |  |  |  |
| 8 | required_null_order_customer_b | STATIC_SKILL | QUALITY_FAILED |  |  |  | missing:TRANSFORMATION_DEFINITION/model.jaffle_shop.stg_orders/NOT_OBSERVABLE extra:TRANSFORMATION_DEFINITION/model.jaffle_shop.orders/NOT_OBSERVABLE unwitnessed: |
| 9 | duplicate_payment_coupon_a | STATIC_SKILL | QUALITY_FAILED |  |  | citation_insufficient_collected_sufficient |  |
| 10 | duplicate_payment_coupon_a | DIAGNOSTIC_KERNEL | RUN_ERROR |  |  |  |  |
| 11 | duplicate_payment_coupon_b | DIAGNOSTIC_KERNEL | QUALITY_FAILED |  | DBT_LINEAGE |  |  |
| 12 | duplicate_payment_coupon_b | STATIC_SKILL | QUALITY_FAILED |  |  |  | missing: extra:RELATION_HISTORY/raw_payments/RELATION_NOT_ALLOWED,TRANSFORMATION_DEFINITION/model.jaffle_shop.stg_payments/NOT_OBSERVABLE unwitnessed: |
| 13 | orphan_payment_coupon_a | STATIC_SKILL | QUALITY_FAILED |  | RELATION_SCHEMA | citation_insufficient_collected_sufficient |  |

## p1-formal-v30

| seq | scenario | strategy | category | direction | axis1 | axis2 | axis3 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | schema_type_change_order_customer_a | STATIC_SKILL | RUN_ERROR |  |  |  |  |
| 2 | schema_type_change_order_customer_a | DIAGNOSTIC_KERNEL | PASSED |  |  |  |  |
| 3 | schema_type_change_order_customer_b | DIAGNOSTIC_KERNEL | QUALITY_FAILED |  | RELATION_DATA_PROFILE+RELATION_HISTORY |  |  |
| 4 | schema_type_change_order_customer_b | STATIC_SKILL | RUN_ERROR |  |  |  |  |
| 5 | required_null_order_customer_a | STATIC_SKILL | PASSED |  |  |  |  |
| 6 | required_null_order_customer_a | DIAGNOSTIC_KERNEL | PASSED |  |  |  |  |
| 7 | required_null_order_customer_b | DIAGNOSTIC_KERNEL | QUALITY_FAILED |  | DBT_LINEAGE |  | missing:TRANSFORMATION_DEFINITION/model.jaffle_shop.stg_orders/NOT_OBSERVABLE extra: unwitnessed: |
| 8 | required_null_order_customer_b | STATIC_SKILL | QUALITY_FAILED |  |  |  | missing: extra:TRANSFORMATION_DEFINITION/model.jaffle_shop.orders/NOT_OBSERVABLE unwitnessed: |
| 9 | duplicate_payment_coupon_a | STATIC_SKILL | QUALITY_FAILED |  |  | citation_insufficient_collected_sufficient |  |
| 10 | duplicate_payment_coupon_a | DIAGNOSTIC_KERNEL | STATUS_ERROR | wrong_abstention |  |  |  |
| 11 | duplicate_payment_coupon_b | DIAGNOSTIC_KERNEL | QUALITY_FAILED |  | DBT_LINEAGE |  |  |
| 12 | duplicate_payment_coupon_b | STATIC_SKILL | QUALITY_FAILED |  |  |  | missing:RELATION_DATA_PROFILE/raw_payments/RELATION_NOT_ALLOWED extra:RELATION_DATA_PROFILE/analytics.raw_payments/RELATION_NOT_ALLOWED,RELATION_HISTORY/analytics.raw_payments/RELATION_NOT_ALLOWED unwitnessed:RELATION_DATA_PROFILE/raw_payments/RELATION_NOT_ALLOWED |
| 13 | orphan_payment_coupon_a | STATIC_SKILL | PASSED |  |  |  |  |
| 14 | orphan_payment_coupon_a | DIAGNOSTIC_KERNEL | STATUS_ERROR | wrong_abstention |  |  |  |
| 15 | orphan_payment_coupon_b | DIAGNOSTIC_KERNEL | QUALITY_FAILED |  | DBT_LINEAGE |  | missing:RELATION_HISTORY/raw_orders/RELATION_NOT_ALLOWED extra:RELATION_HISTORY/raw_payments/RELATION_NOT_ALLOWED unwitnessed:RELATION_HISTORY/raw_orders/RELATION_NOT_ALLOWED |
| 16 | orphan_payment_coupon_b | STATIC_SKILL | QUALITY_FAILED |  |  |  | missing: extra:RELATION_HISTORY/raw_payments/RELATION_NOT_ALLOWED unwitnessed: |
| 17 | silent_payment_drop_partition_a | STATIC_SKILL | PASSED |  |  |  |  |
| 18 | silent_payment_drop_partition_a | DIAGNOSTIC_KERNEL | STATUS_ERROR | wrong_abstention |  |  |  |

## 3. 描述性汇总

| 类别 | v29（13 终态） | v30（18 终态） | 合计（描述） |
| --- | --- | --- | --- |
| PASSED | 3 | 5 | 8 |
| QUALITY_FAILED | 7 | 8 | 15 |
| STATUS_ERROR（全部 `wrong_abstention`） | 0 | 3（seq 10/14/18） | 3 |
| RUN_ERROR | 3（2 provider + 1 工具预算） | 2（均工具预算） | 5 |
| NOT_EXECUTED | 93 | 88 | 181 |

轴聚合（按格/按类型/按 claim/按缺口，见 spec §3 单位定义）：v29 采集缺陷
RELATION_HISTORY×1、RELATION_SCHEMA×2、DBT_LINEAGE×1；引用缺陷 ×2；缺口缺陷 3 格
（missing 1 / extra 4 / 未见证 0）。v30 采集缺陷 RELATION_DATA_PROFILE×1、RELATION_HISTORY×1、
DBT_LINEAGE×3；引用缺陷 ×1；缺口缺陷 5 格（missing 3 / extra 5 / 未见证 **1，seq 15**）。
按 run 的重叠矩阵与逐格明细见 §2（重叠组合含 100/010/001/101/110 等，单格可同时有采集与
缺口缺陷）。

## 4. 边界

- provider 错误归因：v29 两例（MODEL_API_ERROR，第 5 请求，transport=None——当时 M23 字段
  不存在）；v30 零协议事件。v30 工具预算 RUN_ERROR 格的评分包存在且严格加载，其 trace 无
  协议事件。
- 反事实未实施（按 spec §5 仅输出"可修复形态"）；本报告不改评分、不改归档、不重跑。
- 与历史 mimo 的比较未做（M4 前置未满足）。
