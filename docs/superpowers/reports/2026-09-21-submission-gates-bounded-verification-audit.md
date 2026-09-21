# 有界真实验证报告 · 独立审计（2026-09-21）

- 审计对象：[结果报告](2026-09-21-submission-gates-bounded-verification.md) 与 9 格归档
  （run_id 表见其 §4）。审计方法：不采信报告文本，全部结论由归档（`artifacts/<run_id>/`
  6 件套）与 `.dig/baseline-summary.json` 独立重算。
- 结论：**PASS WITH FINDINGS**——门逻辑相关声明全部复核成立；报告在 provider 异常归因上
  有 1 处 P2 事实错误（高估 429 命中面），另有 2 处 P3。

## 1. 复核成立项（逐项独立重算）

| 项 | 审计实测 |
| --- | --- |
| 签署区 | 三项已填（夕沢 / 10 USD / 2026-09-21），含转录说明；§1 时间线显示前置检查先于转录 ✓ |
| HEAD / 工作树 | `87edc59dc4db16bb57ed5769f5102672a39e45db` 逐字一致；已跟踪文件干净（审计时复验仍干净）✓ |
| 9 格归档 | 6 件套齐全；`code_revision` 9/9 = 87edc59；模型/端点逐格正确；`recovery_status` 9/9 HEALTHY ✓ |
| 门事件总数 | 16 = 7 拒绝 + 9 接受 ✓ |
| 拒绝码分布 | `CLAIM_SUPPORT_REQUIRED`×4（全部 policy 层、`rejected_decision=null`）、`UNRESOLVED_EVIDENCE_UNBOUND`×2、`CLAIMS_INCOMPLETE`×1（kernel 合同层、带载荷）✓ 两层结构声明属实 |
| O2 | `GATE_INTERNAL_ERROR` = 0、`GAP_RECEIPT_REQUIRED` = 0 ✓ |
| O3 | run 2 耗尽（trace `output_retry_used=2` + `MODEL_OUTPUT_RETRY_EXHAUSTED`）✓；run 4 两次 kernel 合同拒绝后 `INSUFFICIENT_EVIDENCE` 被接受（唯一修复成功）✓ |
| O4 | 9 格 evaluator 全 FAILED，无 PASSED 格携带拒绝事件；run 7 零拒绝、唯一失败检查 `REQUIRED_EVIDENCE_TYPES_PRESENT` ✓；报告对"弱证据"的效力限定如实 ✓ |
| O5 | run 2/3 触发预期 I1 码 ✓；run 1 未触发（模型直接弃权）按规则不算不符 ✓ |
| O7 | 模型请求合计 29/72 ✓（逐格 6+5+3+6+3+3+3+0+0）；工具调用尝试合计 36/72 ✓；诊断耗时合计 430.8s ✓ |
| token | 282,859 输入 / 84,801 输出，与报告逐字一致 ✓ |
| O8 | `.dig/baseline-summary.json` 指纹逐字 == F0 ✓ |
| stdout 归档 | 9 文件；run 1/2 为 256 字节且首行带 U+FFFD，其余 246 字节 ✓（编码塌陷声明属实） |
| 密钥 | 9 格归档、stdout 文件与报告中无密钥形态字符串 ✓ |

## 2. 发现

### P2-1 §7 的 429 归因与归档不符（高估命中面）

报告 §7 称"6 次传输层异常全部为 HTTP_429"且"4 个 kernel 格（run 4/5/6/8）全部遭遇 429，
命中位置 index 4/4/4/1"。归档实测：

- **429 共 5 次**：run 3/5/6（index 4）、run 8/9（index 1）。run 2 的中断是
  `OUTPUT_SCHEMA_VALIDATION`（输出校验失败），**非** provider 异常——§7 表内该行已正确标注
  "非 429"，与表头句自相矛盾。
- **run 4 无任何协议异常**：trace 无任何 MODEL_PROTOCOL/429 事件，正常终态
  `INSUFFICIENT_EVIDENCE`。"kernel 格 4/4 命中"不成立，实际为 3/4（run 5/6/8）。

影响：结论方向不变（kernel 完整成功链路未充分观察、限流是本批主要混淆因素），但错述
削弱了 run 4 的证据权重——它是**唯一完整跑通 kernel 门链路的格**（含 2 次合同拒绝 +
修复成功 + 门最终接受），是 D2 通路的最强正面单点。处置：修订 §7 表头句与命中面描述、
§10.4 及摘要同步更正；不改结论。

### P3-1 O6 表 run 3 证据清单计数

报告写 4 条，归档（`evidence.json` records 与 `DIAGNOSIS_TERMINAL` 清单）一致为 **5 条**。

### P3-2 授权书与报告未入库

两份文档当前均为未跟踪文件；按 T13 以来惯例（授权书随报告入库），应提交留档。

## 3. 对执行侧两项裁定请求的审计意见

- **(a) 429 与阶梯第 3 步**：修订后的形态是"重负载格在第 4 模型请求处命中（3 例）、
  轻负载格首请求命中（2 例）"，仍支持"先解决配额/节流再进第 3 步"的建议；但 run 4 完整
  跑通证明限流非必然阻断，第 3 步可在降并发/错峰下先行试预检。
- **(b) 金丝雀复现**：run 7 的失败为真实质量失败（仅缺一顶引用检查），与门无关、与
  provider 无关，说明模型波动独立存在。建议 429 缓解后做一次仅含 3 个金丝雀格的小样本
  复测授权，分离两因素后再定第 3 步样本量。

---

# 附：金丝雀复测独立复核（2026-09-21 追记）

- 复核对象：[金丝雀复测报告](2026-09-21-canary-retest.md)（提交 `983dfb4`，含
  [实施计划](../plans/2026-09-21-canary-retest-plan.md)）与 3 格归档
  （`970042d0…` / `9bbafbcf…` / `8a69125a…`）。
- 结论：**PASS（一处 P3）**——报告全部关键声明经归档独立重算一致。

## A. 复核成立项

- run 1/3 均 PASSED、门各仅 1 个 `accepted=true/CONFIRMED` 事件、**零拒绝**——O4 在真实
  PASSED 格上取得 2 次直接证据（上批为零）；run 2 FAILED、429 于 `model_request_index=4`、
  零拒绝事件 ✓
- token 85,499 / 16,690 逐字一致；预算 请求 11/24、工具 19/24（run 1 工具 8/8 达上界属实）✓
- recovery 3/3 HEALTHY；code_revision 3/3 = `d5b30b1`；指纹逐字 == F0；提交恰含计划+报告
  两文件；已跟踪工作树干净 ✓
- 降级方案（≥5 min 间隔）执行属实，且**未能缓解** 429——"仍有 429"主判定成立，
  阶梯第 3 步冻结等待配额解决。

## B. 发现

### P3（须更正）：§7(b) 的 429 位置分布沿用旧数

报告称"合计 6 次 429 中，5 次落在 index=4、1 次落在 index=1"。按已整改口径：上批为
3 次 @index4（run 3/5/6）+ 2 次 @index1（run 8/9），本批 1 次 @index4——**实为 4 次
@index4、2 次 @index1**。不影响"第 4 请求深度命中稳定复现"的结论方向。

## C. §8 mtime 疑点归因（可关闭）

`lab.reset()`（每次 run 的 restore 必经路径）→ `_build_healthy_baseline()` →
`BaselineBuilder.build()` → `write_summary()`（lab.py:829 → baseline.py:371）：**每次
restore 都会重写 `.dig/baseline-summary.json`**，内容锚定故指纹恒为 F0。属运行器带内写入，
非外部进程。设计观察留档：reset 每次全量重建健康基线并重写受信摘要文件，该写入面宜在
后续设计复盘时记录。

## D. 对报告两点复核请求的裁定

- **(a) O4 部分关闭成立**：门语义为路径无关的共享模块，kernel 差异仅在投影层，而投影层
  已在上批 run 4 取得线上正确行为直接证据；kernel 格的 PASSED 复现属模型表现缺口，不属
  门逻辑缺口，不阻塞 O4 关闭；第 3 步样本保留 kernel 金丝雀。
- **(b) 同意**按"单 run 请求深度配额"方向与 CommandCode 交涉（4/6 稳定落在第 4 模型
  请求、错峰 300s 无效），交涉时附两批 run_id 与 index 分布。
