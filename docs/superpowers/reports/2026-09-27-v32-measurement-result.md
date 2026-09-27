# p1-formal-v32 正式测量结果：滚动窗口暂停于 74/106（2026-09-27）

- 授权链：owner「授权正式测量」（计划 `2026-09-27-v32-measurement-authorization.md`，
  提交 `b7f8fef`）→ 首次 preflight FAILED（`2026-09-27-v32-preflight-failed.md`，
  `bf03c6f`）→ owner 重新授权 → 二次 preflight **PASSED**（回执 sha256
  `290907cd8aadc51a4463ffdf3e208a3fdb0a8b87a7d39a5fbe8d6d1051e2c396`）→ suite 一次。
- 执行位置：`C:\Users\29913\codex_space\v32-exec-worktree` 固定 `cc1e181`
  （porcelain 0，子模块 `36bde6c`），launcher 装配（uv 0.11.24 / 六键 /
  `DIG_DIAGNOSTIC_MODEL_API_KEY`=User 作用域 commandcode key）。
- 赛前核验：清单 sha256 `898747c0…f992`、`benchmark verify` 17/12/106/94、
  `pipeline build` 指纹 == F0。

## 1. 终态（逐字）

```
status: FAILED
cells: 74/106
subset: False
model_probe_required: True
stop_reason: ROLLING_WINDOW_UNPASSED_PAUSE
ledger: C:\Users\29913\codex_space\v32-exec-worktree\artifacts\benchmarks\p1-formal-v32\ledger.jsonl
END=2026-09-27T15:35:35+0800
EXIT_CODE=1
DURATION_SECONDS=7884
```

墙钟 13:24:11→15:35:35（+08:00），2:11:24；`EXIT_CODE=1` 为非 COMPLETED 的既定
CLI 退出码。subset=False：完整赛程口径，可出正式报告；暂停为 sticky（同身份续跑
窗口不变会立即再暂停——新身份才是新样本路径，沿用 v29/v31 先例）。

## 2. 格级结果

- 74 终态：**31 COMPLETED / 43 FAILED**；43 个失败全部 `EVALUATION_FAILED`
  （reason 口径），**零** RUN_SETUP_ERROR / ENVIRONMENT_OR_RECOVERY_FAILURE /
  STATIC 侧失败。宏观汇总（`benchmark partial`，缺失格不进分母）：
  macro pass^1 **0.397**（26 groups / 74 trials）、pass^2 0.292、pass^3 0.250；
  missing cells 32。
- 终态诊断分布：CONFIRMED 22、INSUFFICIENT_EVIDENCE 29、NO_INCIDENT 9、
  MODEL_ERROR 14。
- 对照口径提醒：v32 相对 v29–v31 是**整体变更对照**（evaluator v3→v5、controller
  v20→v22、代码含 P-1 v2 投影与 I1/I2 闸门），不可归因到单项变更；两次运行的
  停止位置不同，非同位对比；本段通过率 41.9%（31/74，raw cell 口径）。

## 3. 预算与传输（74 格合计）

model requests **261**；输入 **2,463,870** / 输出 **727,667** tokens；tool call
attempts **388** / 成功 **340**。传输诊断：`CONNECTION_ERROR ×2`（provider 侧，
可归属），HTTP_4xx/5xx **0**，TIMEOUT 0，**429 = 0**。单格结构上限 8 requests /
8 tool calls / 300s 未改。

## 4. P-1 复核（离线只读，冻结 worktree 自带 venv 运行 `v32-p1-analysis.py`）

95 个 gate 事件，**21 次拒绝**，复核结论：

- I1 `CLAIM_SUPPORT_REQUIRED` ×11 → **CORRECT ×11**
- kernel-contract `EVIDENCE_GAP_OPEN` ×2 → **CORRECT ×2**
- kernel-contract `UNRESOLVED_EVIDENCE_UNBOUND` ×8 → **CORRECT ×8**
- **FALSE_REFUSAL = 0；INDETERMINABLE = 0；GATE_INTERNAL_ERROR = 0；
  recomputability 分歧 = 0；74 格 bundle 全部可严格重载（0 加载失败）。**

v31 中 kernel-contract 路径 7 次全部 INDETERMINABLE（v2 投影之前）；本次 10 次
kernel-contract 拒绝全部可判定且全部 CORRECT。I2 `GAP_RECEIPT_REQUIRED` 本批
0 次触发——仅"未触发"，不得推断"放行无误"。

## 5. 证据与产物

- ledger sha256：`1768abdac9953ea7038ca7924aaca9dc5b21645a01b02d3ec9e749bf815522ab`。
- 结束后 `pipeline build` 指纹 == F0 `e5c7848e…cb18`（relations: 8）——DB 回到健康基线。
- 逐字终端输出与全量日志：`C:\Users\29913\codex_space\v32-benchmark.log`；
  launcher 源 `v32-launch.py`、离线分析脚本 `v32-p1-analysis.py`（均在仓库外，
  保留供复现）。
- 主树本阶段仅 docs 提交；未 push；v31 归档与历史文件未触碰。

## 6. 边界与后续

- 74/106 暂停为本身份的正式部分结果；seq75–106 未执行。
- 后续路径（沿用先例）：新身份实验需另行登记/冻结 + 放行；同身份 resume 不推荐
  （窗口 sticky）。离线策略回归焦点不变：证据完整性、per-claim 引用绑定、
  gap 声明（v32 的 43 个质量失败仍以该族为主，待逐格归因后另报）。
