# 离线诊断质量基线报告：v29 + v30 两批（2026-09-20）

- 依据：[spec v2](../specs/2026-09-20-offline-diagnostic-quality-baseline-spec.md)（所有者补齐
  可执行定义后实施）。分析器：`src/data_incident_gym/quality_baseline.py`（只读；严格加载 +
  冻结 evaluator 逐格重算；合成夹具单元测试 15 项，不依赖本机忽略目录）。
- **验证前置全部成立**：两批 manifest 的 result_inputs 五项摘要与当前树逐字一致；31 个终态格
  逐格重算与归档 EvaluationResult 完全相等（零漂移）；批次—manifest—run 身份绑定全部核对通过。
- 两批赛程前缀重叠、按阈值提前停止：**合计仅作描述性并列，非独立统计样本**。

## 1. 总表（描述性并列）

| 类别 | v29（13 终态） | v30（18 终态） | 合计（描述） |
| --- | --- | --- | --- |
| PASSED | 3 | 5 | 8 |
| QUALITY_FAILED | 7 | 8 | 15 |
| STATUS_ERROR（全部 `wrong_abstention`：应确认却弃答） | 0 | 3 | 3 |
| RUN_ERROR（MODEL_ERROR） | 3（2 provider + 1 工具预算） | 2（均工具预算） | 5 |
| CORRUPT / NOT_EXECUTED | 0 / 93 | 0 / 88 | 0 / 181 |
| 用量 | 55 请求；504,647 / 123,137 tok | 86 请求；1,114,362 / 257,371 tok | 141 请求 |

## 2. v29 逐格质量轴（复现所有者人工分析——独立验证通过）

| seq | 场景（截断） | 策略 | 类别 | 轴 1 | 轴 2 | 轴 3 |
| --- | --- | --- | --- | --- | --- | --- |
| 3 | schema_type_change_.._b | KERNEL | QUALITY_FAILED | 未采集 RELATION_HISTORY | — | 多报 customers 转换缺口 |
| 6 | required_null_.._a | KERNEL | QUALITY_FAILED | 未采集 RELATION_SCHEMA | — | — |
| 7 | required_null_.._b | KERNEL | RUN_ERROR | — | — | provider_failure(MODEL_API_ERROR, req 5, transport 无*) |
| 8 | required_null_.._b | STATIC | QUALITY_FAILED | — | — | 缺 stg_orders 转换缺口；多报 orders 缺口 |
| 9 | duplicate_payment_.._a | STATIC | QUALITY_FAILED | — | ROOT_CAUSE 当前引用不足（已采证据足以支撑） | — |
| 11 | duplicate_payment_.._b | KERNEL | QUALITY_FAILED | 未采集 DBT_LINEAGE | — | — |
| 12 | duplicate_payment_.._b | STATIC | QUALITY_FAILED | — | — | 多报 raw_payments history 与 stg_payments 转换缺口 |
| 13 | orphan_payment_.._a | STATIC | QUALITY_FAILED | 采而未引 RELATION_SCHEMA | ROOT_CAUSE 当前引用不足（已采证据足以支撑） | — |

\* v29 运行先于 M23，传输字段当时不存在——provider 错误仍不可归因到具体 HTTP 状态。
轴 1/2/3 与所有者人工分析逐格一致（3/6/11 未采集、13 采而未引、9/13 引用不足、8/12 缺口
多报、3 缺口多报+漏采 history）。

## 3. v30 逐格质量轴（独立计算，不预填）

| seq | 场景（截断） | 策略 | 类别 | 轴 1 | 轴 2 | 轴 3 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | schema_type_change_.._a | STATIC | RUN_ERROR | — | — | —（工具预算 MODEL_TOOL_CALL_LIMIT；无协议事件；bundle 未归档） |
| 2 | schema_type_change_.._a | KERNEL | PASSED | — | — | — |
| 3 | schema_type_change_.._b | KERNEL | QUALITY_FAILED | 未采集 RELATION_HISTORY | — | 多报 customers 转换缺口 |
| 4 | schema_type_change_.._b | STATIC | RUN_ERROR | — | — | —（工具预算；无协议事件；bundle 未归档） |
| 5 | required_null_.._a | STATIC | PASSED | — | — | — |
| 6 | required_null_.._a | KERNEL | PASSED | — | — | — |
| 7 | required_null_.._b | KERNEL | QUALITY_FAILED | — | — | 缺 customers 转换缺口声明 |
| 8 | required_null_.._b | STATIC | QUALITY_FAILED | 未采集 RELATION_DATA_PROFILE | — | — |
| 9 | duplicate_payment_.._a | STATIC | QUALITY_FAILED | — | — | 缺 1 项声明（含 1 项未见证收据） |
| 10 | duplicate_payment_.._a | KERNEL | QUALITY_FAILED | 未采集 RELATION_HISTORY | — | 多报 1 项 |
| 11 | duplicate_payment_.._b | KERNEL | QUALITY_FAILED | 未采集 DBT_LINEAGE | — | — |
| 12 | duplicate_payment_.._b | STATIC | STATUS_ERROR | — | — | —（wrong_abstention） |
| 13 | duplicate_payment_.._b | STATIC | PASSED | — | — | — |
| 14 | orphan_payment_.._a | KERNEL | QUALITY_FAILED | 未采集 RELATION_SCHEMA、DBT_LINEAGE | — | — |
| 15 | orphan_payment_.._a | KERNEL | QUALITY_FAILED | 未采集 DBT_LINEAGE | — | 多报 1 项 |
| 16 | orphan_payment_.._a | STATIC | QUALITY_FAILED | — | ROOT_CAUSE 当前引用不足 | — |
| 17 | orphan_payment_.._b | STATIC | PASSED | — | — | — |
| 18 | orphan_payment_.._b | KERNEL | QUALITY_FAILED | 未采集 RELATION_SCHEMA | — | — |

v30 新增信息：**3 格 `wrong_abstention`**（应确认却弃答——v29 无此形态）；零传输层失败
（86 请求，传输诊断在位、无对象）；2 个工具预算 RUN_ERROR 格的 scoring-inputs bundle 未归档
（写入侧已知 seam，M23 记录），其传输归因因此不可得——已如实标注。

## 4. 按格重叠矩阵（QUALITY_FAILED 格；1=有缺陷）

| 轴组合 (1,2,3) | v29 | v30 |
| --- | --- | --- |
| 100（仅采集） | 2 | 2 |
| 010（仅引用） | 1 | 0 |
| 001（仅缺口） | 2 | 3 |
| 101（采集+缺口） | 1 | 2 |
| 110（采集+引用） | 1 | 0 |
| 000 | 0 | 1 |

多缺陷重叠普遍存在：单格常同时表现为采集缺失与缺口声明缺陷。

## 5. 结论与边界

- 三轴在两批上独立复现同一主导形态：**采集完整性（尤其 DBT_LINEAGE / RELATION_SCHEMA /
  RELATION_HISTORY）与缺口声明精度是最大缺陷面**；引用绑定缺陷为"已采证据足以支撑、当前
  引用不足"的形态（可修复形态），非证据不足。
- v30 独立新增：`wrong_abstention` 状态错误 3 格（v29 为 0）——批次间形态有漂移，
  两批前缀重叠不构成独立样本，不做能力水位推断。
- 本报告不改评分、不改归档、不重跑；反事实未实施（按 spec §5，仅输出可修复形态）。
