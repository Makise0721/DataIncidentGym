# v31 第三执行时段报告:79/106 滚动窗口暂停,全套件核对与 P-1 首次大规模复核(2026-09-23)

- 本文件记录同一套件(p1-formal-v31)的**第三个执行时段**及全套件终态核对。
  前两时段见 `2026-09-23-v31-measurement-report.md`(修订 `06d2b15`)与
  `2026-09-23-v31-segment2.md`(`348c428`)。
- 授权边界:同身份再次续跑(seq6 起),前五格终态原样保留,固定 `91582e5` /
  清单 `f4196010…` / 既有回执,不重新 preflight,累计滚动窗口,任何停止条件触发
  即停,套餐内执行、额外付费 0、不重冻、不追加探针。

## 1. 时段与终态

| 项 | 值 |
| --- | --- |
| 第二时段中断 | 2026-09-23T08:37:25Z |
| 第三时段启动前核对 | HEAD `91582e5`(porcelain 0)、摘要一致、ledger 5 终态;`pipeline build` 重建基线成功(指纹恒等 F0,**残留 NULL 清零**——dbt 本次未崩,印证第二时段恢复失败为间歇性) |
| 第三时段 | 2026-09-23T08:59:17Z → 滚动窗口暂停 |
| 终态 | **79/106 终态,36 COMPLETED / 43 FAILED**;`stop_reason: ROLLING_WINDOW_UNPASSED_PAUSE`(窗口尾 12 中 10 败,按设计暂停,与 v29@13 / v30@18 同机制) |
| 结束后基线 | 指纹恒等 F0 `e5c7848e…`;ledger.jsonl sha256 `b71c0f63…beab07` |

失败构成:42 格 `EVALUATION_FAILED` + seq4 `RUN_SETUP_ERROR`(第一时段旧账)。
36 个 `COMPLETED` 的策略分布为 `STATIC_SKILL` 16/36、`DIAGNOSTIC_KERNEL` 20/36、
`NO_TOOL` 0/7。它们是 79 格暂停前缀的原始计数；该前缀不是完整套件，不据此给出总体过率。

## 2. 身份与恢复核对(79 格全量)

- 身份:`code_revision=91582e5…`、清单摘要 `f4196010…`、协议 `p1.controller.v20`、
  端点 commandcode、`workspace_dirty=False`——**零违例**(模型格均为
  deepseek/deepseek-v4.1-flash;setup 格 model=setup-error 属预期)。
- 恢复:78 格 HEALTHY;唯一非健康为 seq5(第二时段已归因的恢复期 dbt 崩溃)。
- 传输异常:**HTTP 520 共 2 次**(seq2 KERNEL、seq65 STATIC,两格均 FAILED,
  计入正式结果);无 429。

## 3. 预算(本地计量)

全时段累计(三时段,含失败尝试前的完成计量):**260 次模型请求,2,461,498
input / 697,071 output tokens,381 次工具调用**。费用以 provider 账目为准。

## 4. 提交门与 P-1 复核(本轮核心交付,P-1 首次大规模实战)

门事件共 99 次(含接受),**拒绝 21 次**:

| reason_code | 层 | 载荷 | 次数 | P-1 复核 |
| --- | --- | --- | --- | --- |
| `CLAIM_SUPPORT_REQUIRED`(I1) | harness | audit | 12 | **10 CORRECT;2 INDETERMINABLE** |
| `GAP_RECEIPT_REQUIRED`(I2) | harness | audit | 2 | **2 CORRECT** |
| `UNRESOLVED_EVIDENCE_UNBOUND` | kernel 合同 | decision | 6 | 7 次 kernel 合同拒绝全部 INDETERMINABLE(KERNEL_CONTRACT_PATHWAY,登记待办) |
| `EVIDENCE_GAP_OPEN` | kernel 合同 | decision | 1 | 同上 |

- **harness 门层共 14 次拒绝**:12 次可判且均为 `CORRECT`(I1 10 + I2 2),另有
  2 次 I1 `INDETERMINABLE`(均在 seq38);12 个可判事件中 `FALSE_REFUSAL` = 0。
  `GATE_INTERNAL_ERROR` = 0。
- 2 次 I1 不可判均在 seq38,basis 为
  `CLAIM_NOT_RECOMPUTABLE:NOT_RECOMPUTABLE_HEALTH_CLAIM_FIELDS`——HEALTH_STATE
  声明的支撑字段是投影结构性不保留(P-1 设计已声明的不可判面,如实分类,
  非数据缺陷)。
- 拒绝-修复通路:经历 harness 拒绝的 8 格中,1 格修复后最终 COMPLETED,
  7 格终 FAILED(修复后仍败于证据/合同,或预算内未完成修复)。
- **边界**:零误拒只适用于 12 个可判事件;另有 2 个 harness 拒绝不可判,不能表述为
  14 次拒绝全部复核正确。未被触发的违例(模型未提交的)不在可观测面内,不能推断
  「门未放过任何违例」。

## 5. 失败项分布(42 个评测失败格)

`REQUIRED_EVIDENCE_TYPES_PRESENT` 28 / `INSUFFICIENCY_GAP_DECLARED` 20 /
`STATUS_EXACT` 16 / `CLAIM_EVIDENCE_COMPATIBLE` 13 / `ROOT_CAUSE_ACCEPTED` 9 /
`AFFECTED_ASSETS_EXACT` 9 / `POSITIVE_HEALTH_EVIDENCE` 4 /
`RECOVERY_HEALTHY` 1(seq5,环境)。**证据采集完整性与 gap 声明仍是主导弱点**,
与 v29/v30 离线分析指出的弱点同类延续(整套比较,不归因单一改动)。

## 6. 对本轮主问题的回答(更新)

**本轮只能作暂停前缀的描述性比较**:同序号前缀中,v31 seq1–13 为 4/13 `COMPLETED`,
v29 seq1–13 为 3/13;v31 seq1–18 为 7/18,v30 seq1–18 为 5/18。
这些是受赛程与停止规则影响的部分前缀计数,只作描述,不作总体推断或归因。
v31 与历史批次还包含协议身份 v20、两道门、门面修复、P-1 归档等整套实现差异,
不能把前缀差异归因于单一改动。主导失败维度(证据完整性、gap 声明)与历史弱点同类,
未见结构性改善。门的行为面:`GATE_INTERNAL_ERROR` 为 0;14 次 harness 拒绝中
12 次可判且为 `CORRECT`,2 次不可判;拒绝-修复通路工作(1 例修复成功)。
两次 HTTP 520(seq2/seq65)是 provider 瞬时故障,与策略维度无关。

## 7. 后续与待办

1. 套件处于滚动窗口暂停(粘滞):续跑 seq80 起需新授权(同身份路径仍存在,
   但窗口条件未变,恢复执行大概率立即再暂停——按 v29 先例,新身份是取新样本
   的路径;由所有者裁定)。
2. 登记待办:kernel 合同拒绝离线重算通路(7 次样本已归档);v29/v30 历史暴露面
   检查;seq4/seq5 两次 dbt 崩溃环境调查(日志保留,「未复现」不作根因关闭);
   `refusal_review` CLI(如需)。
3. 环境资产保留(隔离 uv 0.11.24、launcher 配方);临时脚本已删。
