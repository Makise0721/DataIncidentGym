# P1 正式批次结果（p1-formal-v9）

> 结论状态：**INVALID**（正式样本存在环境硬门失败，结果无效）。本文件所有数字仅为归档观察，
> 不构成准确率、Kernel 优势或任何模型质量结论。

## 批次

- Manifest `p1-formal-v9`（SHA-256 `698752e82f3a1865bfc25bd41a52a0f77210529ce50a94c76c944d7e5393cfba`），
  106 格 = 94 model-backed + 12 FIXED_RULE；模型 `mimo-v2.5-pro`；预算 8/8/2/300。
- implementation revision `0c3cc61489e7a3c59023090a810981ea82b3662f`；checkout `493f4af`（包装提交）。
- 执行形态：**两段式**——首段 86 格后 fail-stop（seq86 注入未生效 → BUILD_FAILED）；经用户明确豁免
  一次性纪律后，同 Manifest 同目录恢复，完成剩余 20 格。两段均为同一冻结 Manifest 与实现。

## 四态结论（reporter 原文）

**INVALID —— 正式样本存在环境或安全硬门失败，结果无效。**

- 唯一硬门失败：seq86（run `8fb1f219…`）`ENVIRONMENT_VERIFIED`
  （`expected=RUN_SETUP_COMPLETE / actual=BUILD_FAILED`：注入未在构建执行时生效）。
- 其余硬门全部通过：artifacts_complete、doctor_passed、fixed_rule_zero_model_usage、
  identity_aligned、kernel_state_valid、ledger_complete。

## 主矩阵观察（非结论；小样本 + 两段式执行）

| 指标 | DIAGNOSTIC_KERNEL | STATIC_SKILL |
|---|---|---|
| paired success | 0/15 | 1/15 |
| 状态准确率 | 33.3%（12/36） | 66.7% |
| 根因准确率 | 26.7%（4/15） | 66.7% |
| 无故障准确率（NO_INCIDENT 对照） | 6/6 | 2/6 |
| claim-evidence validity | 100%（18/18） | 21.6% |
| 不支持确认率 | 13.3% | 0% |
| affected-assets macro-F1 | 0.185 | 0.798 |

## 诚实边界

1. 结论为 INVALID：cell 86 的环境硬门失败使本批次按冻结规则整体无效；任何准确率数字不得外推。
2. 两段式执行（用户豁免）：86 格与 20 格分属两个进程日，segment 间存在时间差；严格一次性口径已偏离，
   偏离本身经用户明确授权并记录于 decision.md。
3. 样本量：每策略 15 个配对格，Wilson 区间极宽（如 Kernel paired success 上界 20.4%）。
4. seq86 的「注入未生效」根因未定位；在定位前，任何后续正式批次都存在同类风险。
5. 独占归档已在 86 格部分状态消耗（aggregate `0c158240…`，86 格）；完成态（106 格 + summary/report）
   的聚合见证为 suite 内 `summary.json` 与本文件，未再生成第二份归档。

## 证据位置

- Suite：`artifacts/benchmarks/p1-formal-v9/`（ledger 212 行、106×六文件、doctor receipt、summary.json、report.md）
- 部分归档（86 格时点）：`reports/benchmark/p1-formal-v9/`
- 历史链：p1-formal-v1（INVALID_HARNESS）、v3–v8（subset smoke）、v2/v9（正式尝试）
