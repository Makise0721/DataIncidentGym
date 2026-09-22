# 阶梯第 3 步实施计划：身份升版 + 新身份测量（2026-09-21）

- 前提变更：429 定性从"待交涉配额"改为"上游瞬时不稳定"（所有者判断，与既有证据兼容）。
  因此本计划**不再以配额解决为前置**，改为把 429 当瞬时事件设计执行。
- 依据：提交门实施报告 §4（身份面清单）、有界真实验证 + 金丝雀复测及两份审计（O4 已部分
  关闭，第 3 步样本须保留 kernel 金丝雀）。
- 硬约束（已核实，设计前提）：
  1. benchmark ledger **拒绝重复终态格**——被 429 击穿的格在正式 suite 内无法原位补测，
     只能接受 RUN_ERROR 入档；补测只能走 `eval run` 旁路并标记实验性。
  2. 身份升版后历史清单（v23–v30）verify 将漂移于策略载荷摘要项——预期内，只记录不修复。
  3. 门已默认激活且不在身份载荷内——升版是补上这个口径，使带门身份可区分。

## 阶段 1（离线）：身份升版 + v31 冻结

### 1.1 改动范围（此外不改）

- `strategy_adapter.STRATEGY_PROTOCOL_VERSION` v1 → v2；`diagnostic_agent.CONTROLLER_PROTOCOL_VERSION`
  v19 → v20（控制器行为已变：新增两门与耗尽终态码）。
- `docs/requirements.md` M17/M18 相关文本：新增 `CLAIM_SUPPORT_REQUIRED` /
  `GAP_RECEIPT_REQUIRED` / `GATE_INTERNAL_ERROR` 与 `MODEL_OUTPUT_RETRY_EXHAUSTED` 的语义；
  总预算 8/8/2/300 不变。
- 钉值测试随批准演进（"拒绝下一个未批准身份"行前移到 v32，沿用先例）。
- `benchmark_manifest.APPROVED_MANIFEST_IDS` 追加 `p1-formal-v31`；冻结绑定
  `deepseek/deepseek-v4.1-flash` + commandcode 端点（与 v30 相同）。
- 推迟不动：协议 spec §4 拒绝词汇与 `examples/isolation_acceptance.py`（随 F6 一并）。
- **本计划不含代码改动**；但 §3.5 前置项 **P-1**（为策略拒绝分支补落被拒提交载荷，使误拒可事后
  判定）完成后 **需要改代码**，属 M3 可执行性的前置条件——**须另行授权**，本计划只登记范围与验收。

### 1.2 冻结与验收 V1–V4

| 编号 | 验收 |
| --- | --- |
| V1 | 升版前 v30 verify 通过；升版后 v23/v30 verify 仅漂移于 `controller_protocol_*` 两项（diff 逐项列出），其余逐字不变 |
| V2 | v31 冻结 + verify 通过；106/94/12 格数、赛程、预算与 v30 逐字相同；`formal_scenario_ids` 相同；T13 四场景不在其中 |
| V3 | v31 与 v30 的 policies 差异**仅**为两个协议版本字段及其摘要；`result_inputs` 与 v30 逐字相同（evaluator/schema 未变）；cells 差异仅新预分配 run_id |
| V4 | 全量单测、ruff、`uv lock --check`、`git diff --check` 通过；无数据库/模型/网络操作 |

两段式提交（批准 → 清单），沿用 v24 先例。

## 阶段 2（可选前置）：429 基础率探针

- 目的：测浅请求（单轮、无工具）429 基础率，校准阶段 3 的补测窗口数量。
- 方法：24 次单请求探针，跨 2 小时均匀分布（每 5 分钟 1 次），只记录成功/429/其他；
  不注入、不碰数据库。成本 < 0.01 USD。
- 判定：基础率 < 10% → 阶段 3 按 1 个补测窗口准备；≥ 10% → 2 个窗口并拉长批次时间。
- 若所有者认为不需要，可跳过，阶段 3 直接按 2 窗口准备。

## 阶段 3：v31 正式测量（一次 preflight + 一次 suite）

### 3.1 执行

```powershell
uv run data-incident-gym benchmark preflight --manifest config/benchmark/p1-formal-v31.json --confirm-sha256 <v31摘要>
uv run data-incident-gym benchmark run --manifest config/benchmark/p1-formal-v31.json --confirm-sha256 <v31摘要>
```

密钥映射与恢复同前批（User 作用域回退已验证）；`PYTHONIOENCODING=utf-8` 前置。
上界：94 × 8 = 752 次模型请求（合同上界，非 HTTP 总数）；按上批实测均值
（约 12.7k token/请求）估算裸模型成本约 1–3 USD，费用上限沿用 10 USD。

### 3.2 429 容错规则（本计划的核心新增）

1. runner 现有行为不改：429 击穿的格以终态入 ledger，**不当场重试**。
2. suite 结束后盘点：429/协议异常导致的 RUN_ERROR 格，进入**补测窗口**——间隔 ≥ 1 小时后
   以 `eval run` 旁路重测（每格至多 2 次补测，跨窗口分散），归档标记**实验性**，
   不进正式报告、不改 ledger。
3. kernel 格（请求深度大、暴露面高）优先排补测；kernel 金丝雀格必须出现在正式或补测
   样本中（O4 关闭的保留条件）。
4. 若 suite 内 429 命中率 > 20%，中止补测、报告并等所有者裁定（说明该时段上游不可用，
   不是测量问题）。

### 3.3 必录验收 M1–M5

| 编号 | 验收 |
| --- | --- |
| M1 | ledger 106/106 终态齐全；中断则报告实际完成量，不判完整通过 |
| M2 | 94 个 model-backed 格归档模型/端点与 v31 一致；12 个 fixed-rule 格按确定性身份检查 |
| M3 | 拒绝码观测：`GATE_INTERNAL_ERROR` 为 0；拒绝码分布落在已定义集合内 |
| M4 | 环境：每格 recovery HEALTHY，结束后指纹 == F0，HEAD 未变 |
| M5 | 报告含 429 命中分布（index 直方图）、补测窗口执行记录、token 与费用（以 provider 账目为准） |

### 3.4 误拒判定口径（M3 的判定纪律，2026-09-21 修正）

**修正原因**：原 M3 写作「任何 PASSED 格携带门拒绝事件 → 停止并设计层复盘」，该规则**不成立**。
门的设计就是发出可重试的 `ModelRetry`（固定码 + 通用文案，`diagnostic_agent.py:2759`/`:2848`），
模型收到反馈后修正并最终提交成功，属**预期通路**，不是违例。用「最终 PASSED 却曾出现拒绝」倒推
先前拒绝有误，是错误推理；反之，用「最终 FAILED」推定先前拒绝正确，同样错误。

**判定对象改为「该次拒绝是否正确」**：

1. **可重试拒绝 ≠ 违例**：`accepted=false` 后同格出现修复提交并被接受，属 D2 通路的**正面证据**
   （已有一例：上批 run 4 连续 2 次 kernel 合同拒绝 → 修复 → `accepted=true`）。
2. **违例 = 误拒**：某次 `accepted=false` 所针对的提交**本身满足**该 `reason_code` 的判据。
   即：门拒错了，而不是模型提交错了。
3. **逐次独立复核**：对每一次拒绝事件，独立复核被拒提交是否真的违该码；**不得**因该格最终 PASSED
   而推定先前拒绝有误，也**不得**因最终 FAILED 而推定先前拒绝正确。
4. **可判定性分级**（依归档实测）：
   - **kernel 合同层**（`UNRESOLVED_EVIDENCE_UNBOUND` / `CLAIMS_INCOMPLETE` 等）：拒绝事件**带**
     `rejected_decision` 载荷，**必须逐次重算判据**，可判定；
   - **harness 门层**（`CLAIM_SUPPORT_REQUIRED` / `GAP_RECEIPT_REQUIRED`）：拒绝事件该字段为
     `null`，归档**不含**被拒提交内容，**事后不可判定**——须记为**能力缺口**，不得表述为
     「零误拒」或「已证清白」。
5. **停止时机相应调整**：单格内 429/协议异常仍按 runner 行为停机（§3.2 不变）；suite **不因出现
   拒绝事件而中止**（那是正常通路）；仅在**确认误拒**时停止并进入设计层复盘。
   推论：PASSED 格若出现**通路内**拒绝（模型修正后通过），**不构成违例**。

### 3.5 归档能力缺口与前置项（P-1）

M3 的逐次复核要可执行，归档须能回答「那次拒绝是否正确」。现状**不能**：

- `EvidenceGateTraceEvent` 字段为 `event_type` / `reason_code` / `accepted` / `rejected_decision`
  （`diagnosis.py:520`），而 `rejected_decision` **只在 kernel `finalize` 抛 `KernelError` 的
  拒绝分支**被构造（`diagnostic_agent.py:2781`）。
- 两处 **harness 策略拒绝分支**（kernel 投影 `:2738`、静态 `:2838`）构造事件时**不带**该载荷。
- 实测：上批 7 次拒绝事件中，3 次带载荷（kernel 合同层）、**4 次 `CLAIM_SUPPORT_REQUIRED` 为 null**
  （harness 门层）。
- 且 `_rejected_decision_summary`（`diagnostic_agent.py:1360`）入参是 `KernelDecision`；
  静态路径的被拒对象是 `Diagnosis`，**连投影构造器都缺**，须新增平行投影。

**前置项 P-1（本计划不实施代码改动，只登记）**：在 stage 3 正式测量**之前**，为两处策略拒绝分支补落
带被拒提交的 `rejected_decision` 载荷——
1. kernel 投影路径：复用 `_rejected_decision_summary`（`:2781` 已有调用形态）；
2. 静态路径：为 `Diagnosis` 新增平行投影（claims / unresolved）。
3. **边界约束**：新载荷必须与既有 `RejectedDecisionSummary` 同构——只保留已注册 hypothesis ID、
   公开 node/relation 标识与**已接收**证据引用，未知内容降为计数，自由文本与原始值一律不入档。
4. **验收**：载荷 + 归档 `evidence.json` 已接收记录，足以独立重算
   `claim_supported_by_records`（I1）与 `refusal_witnessed`（I2）判据，从而对每次策略拒绝判定
   正确/误拒；否则须在报告中明说不可判定及原因。

P-1 未落地时，stage 3 对 harness 门层拒绝只能给出**不可判定**结论（如实记录，不判违例、不判清白）。

## 交付与审计

- 各阶段完成后出报告（阶段 1 冻结报告 / 阶段 3 测量报告），docs 提交入库。
- 每阶段报告落盘后交审计侧独立复核归档；阶段 1 → 3 之间不等配额、只等审计通过。
