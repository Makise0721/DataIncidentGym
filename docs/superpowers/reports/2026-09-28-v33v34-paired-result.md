# Kernel v18/v19 配对测量结果（2026-09-28）

- 方案：`docs/superpowers/plans/2026-09-27-kernel-v18-v19-paired-measurement-proposal.md`；
  冻结证据 `2026-09-27-v33-v34-freeze.md`（dcedef5）；执行中断与恢复
  `2026-09-28-v33v34-partial-execution.md`（28b27a0）。
- 执行：B(v34, v19) @ `0dc1572` 先行一次 preflight/run；A(v33, v18) @ `3b97353`
  因 provider 探针 TIMEOUT 停机一次，owner 裁定「1」后重新授权 preflight
  （PASSED，回执 `60333595…`）并完成 run。两臂各恰一次 preflight 与 run，无重试
  run、无补样本。
- 终态：两臂均 **18/18 全终态、stop_reason: NONE**（B 2392s ≈ 39.9 分钟；
  A 2144s ≈ 35.7 分钟）；A 臂结束后 `pipeline build` 指纹 == F0。B ledger sha
  前缀 `9f32a997…`，A `7ef55473…`。

## 1. §7 严格核对（先于一切判读）

36 格 scoring-inputs 全部严格加载成功；recovery 全部 HEALTHY；模型身份一致
（deepseek/commandcode，manifest 接线）；**evaluator 重算与归档 evaluation.json
的 failed_check_codes 逐格一致（36/36）**；GATE_INTERNAL_ERROR 两臂 ledger
全文扫描均为 0。完整性问题清单：**NONE**。

## 2. 主指标与四格表（成功 = 证据检查 PASSED 且无门失败）

| 分组 | A(v18) | B(v19) | both | onlyA | onlyB | neither |
| --- | --- | --- | --- | --- | --- | --- |
| 目标（6 对） | **0/6** | **5/6** | 0 | 0 | 5 | 1 |
| 对照（12 对） | **8/12** | **6/12** | 6 | 2 | 0 | 4 |
| 总计（18 对） | 8/18 | 11/18 | 6 | 2 | 5 | 5 |

按场景（A/3、B/3）：

| 场景 | A | B | 变化 |
| --- | --- | --- | --- |
| duplicate_payment_coupon_a（目标） | 0 | 3 | +3 |
| orphan_payment_coupon_a（目标） | 0 | 2 | +2 |
| duplicate_payment_coupon_b（对照） | 2 | 1 | **−1** |
| orphan_payment_coupon_b（对照） | 0 | 0 | 0 |
| silent_payment_drop_partition_a（预算压力） | 3 | 3 | 0 |
| order_volume_within_sla（健康对照） | 3 | 2 | **−1** |

## 3. 预登记筛选规则逐项判定

1. 36 格全部终态、18 对完整、严格核对通过 → **满足**，可评判。
2. 目标组 B 较 A 净增 ≥2（5−0=5）且两个目标场景分别不下降（0→3、0→2）→
   **满足**。
3. 对照组 B 成功数不低于 A（6 < 8）→ **不满足**；B 预算耗尽数（0）与错误确认
   数（0）均不高于 A（0/0）✓，但 within_sla 3→2 与 duplicate_b 2→1 两个对照
   场景下降 → 按规则标记**需解释的退化，不自动晋级**。
4. 结论：**混合结果（目标显著改善、对照退化），不晋级、不宣称 v19 更优**；
   保留负面/混合结果，不追加样本。

## 4. 退化归因边界（如实、不洗白）

B 臂对照的 3 个失败格（seq40 within_sla、seq67 duplicate_b、seq71 orphan_b）
全部为 `MODEL_ERROR` 且 trace 记录 `PROVIDER_PROTOCOL_FAILURE
transport=CONNECTION_ERROR`——**provider 侧连接错误，传输可归属**，非策略质量
信号；其中 seq40 的 run 在首个请求即中断（req=0）。按 §7 它们保留在端到端
结果中（上表已计入），同时在此单列传输归类。两臂分块顺序（B 先 A 后）的
时间混杂按方案 §5 明确保留：本报告只能给出"本次配对样本差异"，不能排除
服务端随时间变化；若剔除 3 个传输格，对照为 B 6/A 8 仍下降（A 无传输格），
方向性结论不变，但样本极小、不构成统计判断。

## 5. 次指标

- **目标关系 schema 接受**：A 5/18 → B 11/18；目标组内 A 0/6、B 5/6——新提示
  的补采行为真实发生且与目标改善同向；5 个 B 目标成功格中 schema 均已接受。
- **原失败码消失**：A 目标 6 格全部为 `REQUIRED_EVIDENCE_TYPES_PRESENT`；
  B 目标仅 seq70 仍失败（同码，schema 亦未采——机会存在但模型未执行，按 §7
  属合规预算取舍或未执行，不作自动判定）。
- **决定性 history/lineage 缺失**：两臂目标格均无 history/lineage 缺失记录
  （缺失以失败码形态体现，见上）；预算压力场景 silent_a 两臂 3/3 且均未受损。
- **错误弃答/误确认**：对照组无错误确认（0/0）；弃答面见逐格表。
- **预算耗尽**：两臂均 0 格触及 8/8 上限。
- **传输错误**：A 0、B 3（CONNECTION_ERROR，见 §4）。
- **P-1 复核**：A 3 次 kernel-contract 拒绝（UNRESOLVED_EVIDENCE_UNBOUND）
  复核全部 CORRECT；B 0 次拒绝。
- **成本**：tokens A 486,228 in / 146,654 out；B 455,028 in / 129,909 out；
  36 格 + 两次结构化 preflight，均在现有套餐内，额外付费 0。
- **身份/停止**：两臂 X/Y 与 sha256 见冻结报告；本实验唯一停止事件为 A 首次
  preflight TIMEOUT（已归档 `1f92fd11…`，owner 裁定后重新授权通过），无其它
  停止、缺失或补跑；未选择的 88 格未触碰。

## 6. 结论

按预登记规则：目标组净增 5/6 且双场景不降（规则 2 满足），但对照组退化触发
规则 3 的"需解释的退化，不自动晋级"。**本轮筛选结果为混合**：v19 的 schema
核对提示在其目标形态上效果明确（5/6 对 0/6，且补采行为可观察），对照退化
3 格全部为 provider 连接错误的传输可归属单点；是否晋级需在传输可控的条件下
由更广的确认性测量（独立方案）决定，本轮不宣称生产策略变更。
