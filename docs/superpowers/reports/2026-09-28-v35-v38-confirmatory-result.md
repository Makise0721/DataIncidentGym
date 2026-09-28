# Kernel v19 确认性测量结果（v35–v38 四身份分块，2026-09-28）

- 方案：`docs/superpowers/plans/2026-09-28-kernel-v19-confirmatory-measurement-proposal.md`
  v2（6edd1d4/719580b/4cf7d7c）；冻结证据 `2026-09-28-v35-v38-freeze.md`
  （d4b5142/bebcdf1）。owner 放行四段执行（B1→A1→A2→B2，最多 48 格，既定停止
  边界）。
- 执行：四段各恰一次 preflight/run，全部 **PASSED preflight → 18+10+14+14=48/48
  终态**，段内 stop_reason 均 NONE；A2 段子模块按装配步骤补初始化一次（无合同
  影响）。**GATE_INTERNAL_ERROR 四段 ledger 扫描均为 0**；传输错误 **0**；
  RUN_SETUP/恢复失败 0；每段后 F0 指纹逐字相等；跨段门三次检查（4/12、8/12、
  4/12）均未触发。ledger sha 前缀：B1 `102978e8…`、A1 `e57fc45c…`、
  A2 `87d1d0f0…`、B2 `7830219f…`。
- 严格核对：48 格 bundle 严格加载成功；recovery 全 HEALTHY；模型身份一致；
  **evaluator 重算与归档 failed_check_codes 逐格一致（48/48）**。完整性问题
  NONE。

## 1. 主指标（端到端整格通过）

| 分组 | A(v18) | B(v19) | both | onlyA | onlyB | neither |
| --- | --- | --- | --- | --- | --- | --- |
| 目标（12 对） | **6/12** | **4/12** | 1 | 5 | 3 | 3 |
| 对照（12 对） | **10/12** | **7/12** | 7 | 3 | 0 | 2 |
| 总计（24 对） | 16/24 | 11/24 | 8 | 8 | 3 | 5 |

辅助口径（成对传输排除）：本轮传输失败对为 **0**，辅助口径与主指标完全相同。

## 2. 分块与场景分解（A、B 各自早/晚块成功数）

| 场景 | 早 A | 早 B | 晚 A | 晚 B |
| --- | --- | --- | --- | --- |
| duplicate_payment_coupon_a（目标） | 2/3 | 2/3 | **3/3** | **0/3** |
| orphan_payment_coupon_a（目标） | 0/3 | 2/3 | **1/3** | **0/3** |
| duplicate_payment_coupon_b | 1/1 | 1/1 | 2/2 | **0/2** |
| orphan_payment_coupon_b | 1/1 | 0/1 | 0/2 | 0/2 |
| silent_payment_drop_partition_a | 1/1 | 1/1 | 2/2 | 2/2 |
| order_volume_within_sla | 1/1 | 1/1 | 2/2 | 2/2 |

块间差异（Δearly / Δlate 成功率差，含样本数）见分析输出：dup_a (0.00/−1.00)、
dup_b (0.00/−1.00)、orphan_a (+0.67/−0.33)——**三个场景触发预登记反转条件**。

## 3. 预登记规则逐项判定（方案 v2 §4）

1. 完整性：48/48 终态、24 对完整、严格核对 NONE → **可评判**。
2. transport-failed 对 = 0 ≤ 6 → 非"不可判"。
3. 晋级条件：目标组 B−A = −2（要求 ≥ +3）**不满足**；对照组 B 7 < A 10
   **不满足**；辅助口径与主指标同数（0 传输格）方向仍不一致；**三个场景触发
   反转条件**。→ **不晋级**。
4. 结论：**确认性测量未复现筛选结果，判定为负面**。v33/v34 筛选中 B 目标
   5/6 对 A 0/6 的优势在本轮分块设计下反转（早块 B 2/6、A 2/6 打平；晚块
   B 0/6、A 4/6）——当初的"改善"主要与执行顺序/时间因素纠缠，按方案 §6
   **停止扩大该规则，不修改 prompt，不宣称 v19 更优**。

## 4. 机制观察（描述性，佐证反转）

- **B(v19) 的目标 schema 采集在晚块崩塌**：目标格最终存在已接受同关系
  schema 的数量——早块 B 4/6、晚块 B **0/6**（A：早 2/6、晚 4/6）。晚块 B 的
  5 个目标失败全部是 `CONFIRMED` + `REQUIRED_EVIDENCE_TYPES_PRESENT`（提交
  时未采 schema），与 v32 五格的原始失败形态一致。
- 该崩塌发生在**同一 prompt（v19）跨块之间**，不可能是提示内容变化；结合
  三场景反转标记，指向执行顺序/时间段相关的行为漂移（模型侧或服务侧），
  机制超出本轮归因能力。
- 预算：两臂均 0 格触及 8/8 上限；B 采 schema 格的 history/lineage 未见系统性
  减少（逐格表见分析输出）；P-1 复核四段合计 1 次拒绝（A2）为 CORRECT。
- 用量：tokens A 716,920 in / 195,217 out；B 644,580 in / 194,040 out（48 格
  归档 metrics 合计，不含四次 preflight）；额外付费 0。

## 5. 边界

- 48 格均为 dev 场景 development smoke subset；未调用 `benchmark report`；
  未选择格未触碰；未 push；v33–v34 归档原样。
- 分析脚本 `codex_space/v35v38-blocked-analysis.py`（只读），逐格明细与
  P-1 输出均在其中可复现。
