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
