# 金丝雀格复测实施计划（2026-09-21）

- 目的：分离「模型波动」与「provider 限流」两个因素——上一批（9 格有界真实验证）中 3 个
  PASSED 对照格无一复现 PASSED（run 7 真实质量失败、run 8/9 首请求 429 空转），导致 O4 未能
  在真实 PASSED 格上取得证据。本计划以最小样本重测这 3 格。
- **修订（2026-09-21，误拒判定口径修正）**：C2 原写作「PASSED 格出现任何拒绝事件 → 立即停止」，
  该规则**不成立**（门本就允许模型收到反馈后修正成功，属预期通路；不能用最终成功倒推先前拒绝
  有误），已按 [阶梯第 3 步计划](2026-09-21-step3-identity-measurement-plan.md) §3.4 的判定纪律
  改为「逐次复核该次拒绝是否为**误拒**」。下 §2 C2 与 §3 矩阵末行同步。
- 依据：[有界真实验证报告](../reports/2026-09-21-submission-gates-bounded-verification.md)
  §9/§10 与 [审计报告](../reports/2026-09-21-submission-gates-bounded-verification-audit.md) §3(b)。
- 定位：验收阶梯第 2 步的补充观察，不是第 3 步；归档仍为实验性观察产物，不进评估集合。

## 0. 前置（全部满足才执行）

1. **429 缓解确认**：先与 CommandCode 侧确认配额/节流口径（上一批形态：重负载格第 4 请求、
   轻负载格首请求命中）。若无法缓解，退而求其次：3 格之间间隔 ≥ 5 分钟串行执行并记录间隔。
2. HEAD 为 `d5b30b1` 或直系后代；已跟踪工作树干净。
3. `.dig/baseline-summary.json` 指纹逐字 ==
   `e5c7848eb2b7af16ec37463650b63dbb5d1f52293adf456003517b51c496cb18`。
4. 密钥：`COMMANDCODE_API_KEY` 进程继承问题——若 harness 进程仍无此变量，按上批已验证的
   回退（User 作用域读取）执行并记录；建议重启 DSH 服务使其继承。
5. 本批起每条命令前置 `PYTHONIOENCODING=utf-8`（修掉上批 run 1/2 的捕获编码塌陷）。

## 1. 执行（3 次 eval run，串行，无重跑）

```powershell
uv run data-incident-gym eval run required_null_order_customer_a --strategy static-skill
uv run data-incident-gym eval run required_null_order_customer_a --strategy diagnostic-kernel
uv run data-incident-gym eval run silent_payment_drop_partition_a --strategy static-skill
```

环境映射同上一批（`DIG_DIAGNOSTIC_MODEL_BASE_URL` / `_NAME` / `_API_KEY`），进程级、
不落盘、执行后恢复。stdout/stderr 逐字落盘 `.dig/canary-retest-<case>-<strategy>.txt`。

上界：3 × 8 = 24 次模型请求；按上批实测 token 量级，裸模型成本约 0.01–0.02 USD。

## 2. 观察项

| 编号 | 观察项 | 判定 |
| --- | --- | --- |
| C1 | 3 格 evaluator 结果 | 全部 PASSED → 模型波动说成立，第 3 步可谈全样本 |
| C2 | 每格 `EVIDENCE_GATE` 事件，**逐次**判定该次拒绝是否为**误拒**（口径见 step3 计划 §3.4） | 某次 `accepted=false` 所针对的提交**本身满足**该 `reason_code` 判据（即门拒错了）→ 停止、设计层复盘。**通路内拒绝不构成违例**：模型收到可重试拒绝反馈后修正并最终成功属 D2 预期通路；**不得**用最终 PASSED/FAILED 倒推先前拒绝的对错 |
| C3 | 429/协议异常 | 任一格再遇 429 → 该格证据作废、如实记录；不自行重试 |
| C4 | `GATE_INTERNAL_ERROR` | 必须为零，非零即停 |
| C5 | 环境恢复 | 每格 recovery HEALTHY；结束后指纹 == F0 |

## 3. 结果解释矩阵（预先约定，避免事后解释）

| 结果 | 解释 | 下一步 |
| --- | --- | --- |
| 3/3 PASSED 且无 429 | 上批失败主因是限流+波动 | 可谈阶梯第 3 步（身份升版 + 新身份测量）；此时「无误拒」仍**只能**由 C2 逐次判定给出，不得由「零拒绝事件」直接推定 |
| 有 PASSED 有 FAILED，无 429 | 模型波动独立存在且显著 | 第 3 步样本量与预算按波动率重新估算 |
| 仍有 429 | 限流未解决 | 先解决配额，第 3 步冻结等待 |
| 任何 C2/C4 违例 | 门缺陷（C2 指**确认误拒**，非「出现过拒绝事件」） | 停止，设计层复盘，不点修复式重跑 |

## 4. 交付

- 报告 `docs/superpowers/reports/2026-09-21-canary-retest.md`：实际 HEAD、命令与 stdout
  归档、3 个 run_id、C1–C5 实测、token 用量、结论；一个 docs 提交入库。
- 完成后交审计侧独立复核归档。
