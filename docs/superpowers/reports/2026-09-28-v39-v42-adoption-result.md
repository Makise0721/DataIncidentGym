# Kernel v19 扩大确认（采用决定轮）测量结果（v39–v42 四身份，2026-09-28）

- 授权：owner 2026-09-28 聊天放行第二道（「放行第二道」）；A1 首次 preflight
  失败后的修复路径（归档回执→三树 build→重新授权续跑）经 owner 裁定「同意」。
  方案 v2（`docs/superpowers/plans/2026-09-28-kernel-v19-expanded-confirmation-proposal.md`，
  `d34fdac`）；冻结证据 `2026-09-28-v39-v42-freeze.md`（`215acfd`）。
- 执行：B1(v39) → A1(v40) → A2(v41) → B2(v42) 各恰一次 preflight/run
  （A1 为归档失败回执后经重新授权的第二次 preflight）。四段 **52/52 终态**，
  段内 stop_reason 均 NONE；GATE_INTERNAL_ERROR 四段合计 0；传输错误 **0**；
  恢复失败 0；F0 指纹初始与每段后共五次核验全部恒等
  （`e5c7848e…`）；跨段人工门三次检查（5/12、3/12、5/12）均未触发。
  ledger sha 前缀：B1 `27bfc89d…`、A1 `5dcd730b…`、A2 `6e7b71a1…`、
  B2 `aee159d8…`。
- **A1 首次 preflight 失败记录**：PROFILE_SNAPSHOT/PROFILE_READ_ONLY=
  UNAVAILABLE（其余 13 检查全 PASS，含模型探针）；根因为执行侧流程次序
  错误——漏在全新 worktree w40 跑 `pipeline build`（快照是每树生成的
  `.dig/baseline/profile_snapshot.json`）。回执 sha256 `f0127c16…fa41d`
  归档于 `p1-formal-v40-failed-attempts/doctor.profile-unavailable-
  20260928T120403Z.json`；零 cell 启动，不计入质量结果。按停止纪律整体
  停止并经 owner 裁定后修复续跑；B1 的 13 格未重跑。
- 严格核对：臂身份守卫通过（A1/A2 全部 v18、B1/B2 全部 v19，归档
  policy_identity 断言）；52 格 bundle 严格加载成功；recovery 全 HEALTHY；
  evaluator 重算与归档 failed_check_codes 逐格一致（52/52）；完整性问题
  NONE。分析脚本 `codex_space/v39v42-blocked-analysis.py`（只读、预登记、
  含臂守卫与显式配对绑定）。

## 1. 主指标（端到端整格通过）

| 分组 | A(v18) | B(v19) | both | onlyA | onlyB | neither |
| --- | --- | --- | --- | --- | --- | --- |
| 目标（12 对） | **12/12** | **10/12** | 10 | 2 | 0 | 0 |
| 不足对照（9 对） | **2/9** | **2/9** | 1 | 1 | 1 | 6 |
| 无事件对照（3 对） | **3/3** | **3/3** | 3 | 0 | 0 | 0 |
| 锚点（2 对，仅描述） | 1/2 | 1/2 | 1 | 0 | 0 | 1 |
| 总计（26 对） | 18/26 | 16/26 | 15 | 3 | 1 | 7 |

辅助口径（成对传输排除）：本轮传输失败对为 **0**，与主指标完全相同。
目标族分解：stc_a 两臂均 6/6；**rn_a A 6/6 vs B 4/6**。

## 2. 分块与场景分解

| 场景 | 早 A | 早 B | 晚 A | 晚 B |
| --- | --- | --- | --- | --- |
| schema_type_change_order_customer_a（目标） | 3/3 | 3/3 | 3/3 | 3/3 |
| required_null_order_customer_a（目标） | 3/3 | 2/3 | 3/3 | 2/3 |
| schema_type_change_order_customer_b | 0/2 | 0/2 | 0/1 | 0/1 |
| required_null_order_customer_b | 0/1 | 0/1 | 0/2 | 0/2 |
| silent_payment_drop_partition_b | 0/1 | 1/1 | 2/2 | 1/2 |
| order_volume_pattern_a | 1/1 | 1/1 | 2/2 | 2/2 |
| duplicate_payment_coupon_a（锚点） | 1/1 | 1/1 | — | — |
| orphan_payment_coupon_a（锚点） | 0/1 | 0/1 | — | — |

反转检查（仅目标+不足+无事件场景）：silent_b Δearly=+1.00（n=1）、
Δlate=−0.50（n=2）→ **触发预登记反转条件**，标记时间顺序嫌疑；其余场景
未触发（rn_a 两块均为 −0.33，不满足 Δearly≥0）。

## 3. 预登记规则逐项判定（方案 v2 §5）

1. 完整性：52/52 终态、26 对完整、严格核对 NONE → **可评判**。
2. transport-failed 对 = 0 ≤ 6 → 非"不可判"。
3. 晋级条件：目标组 B−A = **−2**（要求 ≥ +3）**不满足**；stc_a 不降
   （6=6）但 **rn_a 退化（B 4/6 < A 6/6）不满足**；不足对照 B 2 ≥ A 2
   满足；无事件 B 3 ≥ A 3 满足；辅助口径与主指标一致但方向为负**不满足**；
   silent_b 触发反转条件**不满足**。→ **不满足晋级条件**。
4. 结论：按预登记判据，本轮为**质量负面结果：不建议将 v19 作为默认策略
   采用**，且不为改判追加样本。与 9-28 轮（24 对、coupon-join 目标
   B−A=+6）合并解读：v19 的收益集中在 coupon-join 机制族，在新扩的
   schema 型变与必填空值机制上无增益且 rn_a 退化——机制特异性明显，
   不支持默认采用。是否回 prompt 开发线迭代（M25 纪律）由 owner 决定。
   本结论仍限于两轮设计（dev 场景、50 对合计、BAAB 分块）之内，不构成
   生产或泛化表述。

## 4. 机制观察（描述性）

- **rn_a 两臂败格同型**：B 的两个败格（seq45 早、seq6 晚）均为
  CONFIRMED 终态 + `REQUIRED_EVIDENCE_TYPES_PRESENT` 失败 + **未采同关系
  schema**（A 六格全部采集 raw_orders schema）。v19 的"接受 profile 后
  核对 schema"条件在 rn_a 上未促成采集——该族 profile 被接受后规则没有
  转化为 schema 采集。
- **stc_a**：两臂 6/6 且全部采集 raw_customers+raw_orders schema——该族
  不依赖 v19 规则也稳定。
- schema 接受格数（各臂 26 格）：A 19、B 18——本轮 v19 未增加 schema
  采集量（与 9-28 轮 coupon 目标上 B 15 > A 6 形成对照）。
- 不足对照组"错误确认"计数：两臂均为 0（b 变体败格均为应弃未弃的
  INSUFFICIENCY_EVIDENCE 终态 + 检查失败，非错误确认）。
- 预算：两臂均 0 格触及 8/8 请求上限；tool 触顶各 1 格。
- P-1 复核（52 格）：拒绝事件 7（B1 1、A1 2、A2 3、B2 1），裁决
  6 CORRECT + 1 INDETERMINABLE（A1），**零 FALSE_REFUSAL**。
- 用量：tokens A 1,152,410 in / 304,232 out；B 1,143,365 in /
  295,549 out（52 格归档 metrics 合计，不含五次 preflight 探针）；
  额外付费 0。
- 锚点：dup_a 两臂同过、orphan_a 两臂同败且同型
  （CONFIRMED + REQUIRED_EVIDENCE_TYPES_PRESENT）——与 9-28 早块同格
  结果并排一致，无单次差异可归因漂移。

## 5. 边界

- 52 格均为 dev 场景；未调用 `benchmark report`；未选择格未触碰；未
  push；v33–v38 及更早归档原样；A 谱系分支仍停 Y_41 不并入 main。
- 执行侧事故一件（A1 首次 preflight，流程次序错误）已按纪律归档-停机-
  裁定-修复，未影响任何测量格。
- 本轮不自动补测；v19 不采用为默认策略后，后续迭代（若启动）回 prompt
  开发线重新走 M25 纪律；规划器测量的对照 Kernel 版本以本结论为准
  （默认对照维持 v18）。
