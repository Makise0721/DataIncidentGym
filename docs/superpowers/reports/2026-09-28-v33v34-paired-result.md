# Kernel v18/v19 配对测量结果（2026-09-28）

- 审计更正（2026-09-28）：更正 preflight 次数、对照失败分类与成对传输敏感性
  分析，收紧因果措辞；原始主指标及“不自动晋级”裁定不变。未改任何归档或评分。
- 方案：`docs/superpowers/plans/2026-09-27-kernel-v18-v19-paired-measurement-proposal.md`；
  冻结证据 `2026-09-27-v33-v34-freeze.md`（dcedef5）；执行中断与恢复
  `2026-09-28-v33v34-partial-execution.md`（28b27a0）。
- 执行：B(v34, v19) @ `0dc1572` 先行一次 preflight/run；A(v33, v18) @ `3b97353`
  因 provider 探针 TIMEOUT 停机一次，owner 裁定「1」后重新授权 preflight
  （PASSED，回执 `60333595…`）并完成 run。preflight 共三次（B 一次、A 两次），
  run 两臂各一次，无重试 run、无补样本。
- 终态：两臂均 **18/18 全终态、stop_reason: NONE**（B 2392s ≈ 39.9 分钟；
  A 2144s ≈ 35.7 分钟）；A 臂结束后 `pipeline build` 指纹 == F0。B ledger sha
  前缀 `9f32a997…`，A `7ef55473…`。

## 1. §7 严格核对（先于一切判读）

36 格 scoring-inputs 全部严格加载成功；recovery 全部 HEALTHY；模型身份一致
（deepseek/commandcode，manifest 接线）；**evaluator 重算与归档 evaluation.json
的完整 EvaluationResult 逐格相等（36/36，审计以只读分析器复核）**；六文件摘要
逐一匹配。GATE_INTERNAL_ERROR 经两臂 trace 扫描均为 0（不能仅扫描 ledger
代替事件核对）。完整性问题清单：**NONE**。

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
4. 结论：**混合结果（本样本目标组净增五格、对照端到端结果下降），不自动晋级、
   不宣称 v19 更优**；
   保留负面/混合结果，不追加样本。

## 4. 对照失败分类与传输敏感性分析

B 臂对照共 **6 个失败格**，不是 3 个：

| seq | 场景 | B 失败类别 | A 对应结果 |
| --- | --- | --- | --- |
| 15 | orphan_b | REQUIRED_EVIDENCE_TYPES_PRESENT + INSUFFICIENCY_GAP_DECLARED | FAILED（缺口声明） |
| 28 | duplicate_b | REQUIRED_EVIDENCE_TYPES_PRESENT | FAILED（证据类型） |
| 32 | orphan_b | INSUFFICIENCY_GAP_DECLARED | FAILED（缺口声明） |
| 40 | within_sla | MODEL_ERROR / CONNECTION_ERROR | PASSED |
| 67 | duplicate_b | MODEL_ERROR / CONNECTION_ERROR | PASSED |
| 71 | orphan_b | MODEL_ERROR / CONNECTION_ERROR | FAILED（缺口声明） |

seq40/67/71 的 trace 均记录 `PROVIDER_PROTOCOL_FAILURE`，
`transport_diagnostic=transport=CONNECTION_ERROR`。可确定为 provider 调用路径的
传输失败，但不能单凭该分类确定服务商、代理、本地网络等具体故障源；不能把中断
后的诊断质量当作已观察结果。seq40 在第 1 次请求尝试即中断，metrics 的已完成
请求计数为 0，不代表没有发起请求。

原始端到端结果保留这三格失败：A 8/12、B 6/12。两个“仅 A 成功”的配对恰为
seq40/67；seq71 双方均未通过，但失败类别不同。

**辅助敏感性分析**：成对移除 seq40/67/71（同时移除 A 对应格），剩余 9 对的
结果为 **A 6/9、B 6/9**，四格表为 both=6、onlyA=0、onlyB=0、neither=3。
原“剔除后 B 6/A 8 仍下降”的说法混用了不同样本集合，现撤回。
这项事后分析不能替代主指标、改变预登记筛选规则或证明策略无副作用；只能说明
本次未受传输失败影响的配对中，没有观察到整格通过结果的退化。

两臂按 B→A 分块执行，中间还发生一次 preflight 超时及重新授权。时间混杂仍在，
本报告仅给出本次配对样本差异，不排除服务端随时间变化，不作统计显著性判断。

## 5. 次指标

- **目标关系 schema 接受**：A 5/18 → B 11/18；目标组内 A 0/6、B 5/6——新提示
  的补采行为真实发生且与目标改善同向；5 个 B 目标成功格中 schema 均已接受。
- **原失败码消失**：A 目标 6 格全部为 `REQUIRED_EVIDENCE_TYPES_PRESENT`；
  B 目标仅 seq70 仍失败（同码，schema 亦未采——机会存在但模型未执行，按 §7
  属合规预算取舍或未执行，不作自动判定）。
- **决定性 history/lineage 缺失**：两臂目标格均无 history/lineage 缺失记录
  （缺失以失败码形态体现，见上）；预算压力场景 silent_a 两臂 3/3 且均未受损。
- **错误弃答/误确认**：对照组两臂均未观察到错误确认（A 0 格、B 0 格）；
  B 三格 MODEL_ERROR 单列，不据此判断若传输正常会给出何种终态。
- **预算耗尽**：两臂均 0 格触及 8/8 上限。
- **传输错误**：A 0、B 3（CONNECTION_ERROR，见 §4）。
- **P-1 复核**：A 3 次 kernel-contract 拒绝（UNRESOLVED_EVIDENCE_UNBOUND）
  复核全部 CORRECT；B 0 次拒绝。
- **用量**：36 格归档 metrics 合计，A 45 次已计量模型请求、71 次工具调用，
  B 41 次已计量模型请求、72 次工具调用；tokens A 486,228 in / 146,654 out，
  B 455,028 in / 129,909 out。以上不含 preflight，也不代表全部失败请求尝试。
  本轮另执行三次结构化 preflight（含 A 的失败探针）；其请求与 token 用量应独立
  核算，未并入上述格级数字。执行记录声明均在现有套餐内、额外付费 0，本报告
  不将 token 换算为账单。
- **身份/停止**：两臂 X/Y 与 sha256 见冻结报告；本实验唯一停止事件为 A 首次
  preflight TIMEOUT（已归档 `1f92fd11…`，owner 裁定后重新授权通过），无其它
  停止、缺失或补跑；未选择的 88 格未触碰。

## 6. 结论

按预登记规则：目标组净增 5/6 且双场景不降（规则 2 满足），但对照组退化触发
规则 3 的"需解释的退化，不自动晋级"。**本轮筛选结果仍为混合**：目标组
5/6 对 0/6，schema 采集与成功改善同向，是保留候选的正面样本证据；不是统计
显著性或泛化结论。对照端到端下降的两对均对应 B 的连接失败；成对排除传输
失败后的辅助比较为 6/9 对 6/9，不支持把本次通过率下降直接归因为提示缺陷，
也不证明没有副作用。后续如开展确认性测量，需独立设计并授权，控制执行顺序、
记录传输影响，不重跑或替换本轮失败样本；本轮不自动晋级或宣称 v19 更优。
