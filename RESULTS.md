# 实验结果与研究结论

更新时间：2026-09-29。本文件是项目当前结果入口；以下汇总依据各批次报告，文档更新没有重新运行模型或重算所有历史评分。原始运行产物保存在本地忽略目录，克隆仓库不会自动获得这些产物。

## 当前结论

**尚未建立 Diagnostic Kernel 相对 Static Skill 稳定、可重复的总体优势。** 项目已实现可复现事故、受限证据工具、确定性评测与离线审计，但这些工程能力不能替代策略效果证据，也不能证明生产诊断收益。

Kernel v19 提示在部分开发机制上通过筛选，在扩大场景的采用决定轮未满足条件，因此当前实现恢复 v18。规划器的工具目录修复减少了已执行前缀中的名称/参数拒绝，端到端通过仍未提供采用依据。本阶段停止继续扩大真实测量与围绕失败逐轮追加调参，保留实现和证据供独立研究。

## 关键结果索引

以下按实验设计分别报告，不把不同模型、evaluator、场景或停止位置的数字拼成排名。COMPLETED/FAILED 是对应批次的 ledger 终态，涉及环境/恢复失败时须看原报告。

| 实验 | 样本与观察 | 可支持的结论/来源 |
| --- | --- | --- |
| MiMo Kernel / Static 开发配对 | Kernel 5/12、Static 2/12 | 特定 24 格开发样本的局部正信号，不能外推；[原报告](docs/superpowers/reports/2026-09-12-kernel-static-24-cell-comparison-result.md) |
| v31 最终前缀 | 79/106 终态，36 COMPLETED / 43 FAILED，含环境/恢复问题 | v31 不止首次的 4 格；不能作为完整比较；[第三时段报告](docs/superpowers/reports/2026-09-23-v31-segment3-final.md) |
| v32 前缀 | 74/106 终态，31 COMPLETED / 43 FAILED | 31/74 是该停止前缀的描述比例，不是能力上限；[原报告](docs/superpowers/reports/2026-09-27-v32-measurement-result.md) |
| v35–v38 提示确认筛选 | 24 对，v18 10/24、v19 17/24 | 本轮通过筛选；报告已更正初版 A/B 晚块对调错误，应使用更正数字；[原报告](docs/superpowers/reports/2026-09-28-v35-v38-confirmatory-result.md) |
| v39–v42 提示采用决定 | 26 对，v18 18/26、v19 16/26；目标组 12/12 对 10/12 | 扩大确认未满足采用条件，v19 不作为默认策略；[原报告](docs/superpowers/reports/2026-09-28-v39-v42-adoption-result.md) |
| planner-compare v2 前缀 | 29/108；Planner 0/10、Kernel 6/10、Static 4/9 | 暂停前缀，不是完整三策略比较；[原报告](docs/superpowers/reports/2026-09-29-t12-planner-compare-v2-preflight-and-paused-run.md) |
| planner-compare v3 前缀 | 12/108；Planner 0/4、Kernel 1/4、Static 1/4 | 工具目录修正后，Planner 的 25 次 STEP 无计划层拒绝，但整格 0/4，未建立质量收益；身份及来源说明见下 |

v3 绑定实现 `d7cc56a`，执行检出 `154ab4b`；[清单](config/benchmark/p1-planner-compare-v3.json) SHA-256 为 `649171eca4b964a8734ca04f05ec2b0da5aaf2ba108561f87b6626a93ad24af7`。本地执行记录的 ledger SHA-256 为 `40e7373e032ebea2d5b8378668ddbbf5f6a1b7ee106263bbaa865b4c07eb616a`，12 格为 2 COMPLETED / 10 FAILED，触发滚动暂停。原始报告和产物本地留存，清单本身不是得分证据。v2/v3 的停止前缀与执行条件不构成严格配对，不能把拒绝数量差异或总体失败比例直接解释成单一修复的因果效果。

MODEL_ERROR 可能来自输出校验、预算、运行时异常或 provider 请求，不能统称端点故障；v3 前缀没有已归档传输类诊断，不能据此断言某个端点发生了协议故障。

## 阅读限制

- 多数场景已用于提示或规则开发；dev 集结果不是泛化或未见故障能力证明。
- 滚动停止产生受停止规则影响的前缀；未执行格不计失败，前缀比例不当作完整赛程准确率。
- 不能写“从未跑完过 106 格”：历史 v9 曾到 106/106 终态，但因环境硬门失败判 INVALID，见下方保留记录。
- 确定性测试验证实现路径，真实模型结果验证特定冻结条件下的表现；两者不互相替代。
- 历史报告按各自日期和身份解读，其中的“当前”或“下一步”不是今日执行授权。后续进展以此总览和[交接文档](docs/HANDOVER.md)为入口。

## 历史保留：P1 正式批次 p1-formal-v9

以下为原 v9 结案记录，不代表项目后续所有批次的状态。

> 结论状态：**INVALID**（正式样本存在环境硬门失败，结果无效）。本文件所有数字仅为归档观察，
> 不构成准确率、Kernel 优势或任何模型质量结论。

## 批次

- Manifest `p1-formal-v9`（SHA-256 `698752e82f3a1865bfc25bd41a52a0f77210529ce50a94c76c944d7e5393cfba`），
  106 格 = 94 model-backed + 12 FIXED_RULE；模型 `mimo-v2.5-pro`；预算 8/8/2/300。
- implementation revision `0c3cc61489e7a3c59023090a810981ea82b3662f`；checkout `493f4af`（包装提交）。
- 执行形态：**两段式**——首段 86 格后 fail-stop（seq86 dbt 子进程崩溃[宿主硬件故障] → BUILD_FAILED）；经用户明确豁免
  一次性纪律后，同 Manifest 同目录恢复，完成剩余 20 格。两段均为同一冻结 Manifest 与实现。

## 四态结论（reporter 原文）

**INVALID —— 正式样本存在环境或安全硬门失败，结果无效。**

- 唯一硬门失败：seq86（run `8fb1f219…`）`ENVIRONMENT_VERIFIED`
  （`expected=RUN_SETUP_COMPLETE / actual=BUILD_FAILED`）。根因更正：宿主硬件故障导致 dbt 子进程崩溃
  （非「注入未生效」；该硬件故障现已解决）。
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
   偏离本身经用户明确授权并记录于 decision.md。恢复段**只补齐终态数量（106/106），并未消除 cell 86 的硬门失败**；
   reporter 按冻结规则判 INVALID 是正确行为。
3. 样本量：每策略 15 个配对格，Wilson 区间极宽（如 Kernel paired success 上界 20.4%）。
4. seq86 根因已更正为**宿主硬件故障导致 dbt 子进程崩溃**（非「注入未生效」），该硬件故障现已解决；
   此前「根因未定位、后续批次存在同类风险」的表述作废。
5. 独占归档已在 86 格部分状态消耗（aggregate `0c158240…`，86 格）；完成态（106 格 + summary/report）
   的聚合见证为 suite 内 `summary.json` 与本文件，未再生成第二份归档。

## 证据位置

- Suite：`artifacts/benchmarks/p1-formal-v9/`（ledger 212 行、106×六文件、doctor receipt、summary.json、report.md）
- 部分归档（86 格时点）：`reports/benchmark/p1-formal-v9/`
- 历史链：p1-formal-v1（INVALID_HARNESS）、v3–v8（subset smoke）、v2/v9（正式尝试）
