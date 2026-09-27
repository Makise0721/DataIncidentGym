# p1-formal-v32 正式测量授权与执行计划（2026-09-27）

- 授权：owner 2026-09-27 原话「**授权正式测量**」。范围：p1-formal-v32 完整赛程一次
  （verify → preflight 一次 → run 一次 → report/partial）。按冻结计划
  （`2026-09-27-v32-freeze-plan.md`）执行完整赛程前**不做** `--only-sequence` 子集
  smoke；滚动停止、恢复失败即停与部分结果口径保持现有合同，不改代码。
- 执行位置：固定在冻结提交 `cc1e181`（Y）的干净独立 worktree；主工作树带遗留未跟踪
  文件，禁止从主树启动正式执行。绑定修订 `929b9c6`，清单
  `config/benchmark/p1-formal-v32.json`，
  sha256 `898747c0d17edf207095ca789b0fc04ea950484b94dc1a2ffc04124dd7f5f992`。

## 固定命令与顺序（经 launcher 装配环境后在 worktree 内执行）

1. `data-incident-gym benchmark verify --manifest config/benchmark/p1-formal-v32.json`
2. `data-incident-gym pipeline build`（前提核验：指纹恒等 F0）
3. `data-incident-gym benchmark preflight --manifest config/benchmark/p1-formal-v32.json --confirm-sha256 898747c0d17edf207095ca789b0fc04ea950484b94dc1a2ffc04124dd7f5f992`
4. 通过 → `data-incident-gym benchmark run --manifest config/benchmark/p1-formal-v32.json --confirm-sha256 898747c0…f992`（一次，完整赛程）
5. 终态后：COMPLETED → `benchmark report`；暂停/部分 → `benchmark partial`（只读汇总）

## 环境装配（沿用 v31 修正后配方，2026-09-23 实测有效组合）

- uv 0.11.24：`C:\Users\29913\tools-uv-0.11.24`（PATH 前置；doctor 硬门 `_EXPECTED_UV=0.11.24`）。
- 数据库六键 `DIG_DIAGNOSTIC_POSTGRES_{HOST,PORT,DATABASE,SCHEMA,USER,PASSWORD}`：
  由**仓库外** launcher（`C:\Users\29913\codex_space\v32-launch.py`）从主树 ignored 的
  `.env.diagnostic` 读取后进程级注入；不回显、不复制文件进 worktree。
- 模型 key：仅取 **User 作用域** `COMMANDCODE_API_KEY`（launcher 内经
  HKCU\Environment 读取）；**不**注入 `.env.diagnostic` 内的模型
  base_url/name/key——端点与模型由冻结清单接线（v31 401 教训）。
- `PYTHONIOENCODING=utf-8`；launcher 记录 START/END/EXIT_CODE/DURATION 并全量落盘日志。

## 前提核验（执行前逐项记录，任一不满足即停）

worktree HEAD == `cc1e181` 且跟踪文件 porcelain 干净；清单 sha256 == 上述值；
六键在 launcher 环境源齐备；`pipeline build` 指纹 == F0
`e5c7848eb2b7af16ec37463650b63dbb5d1f52293adf456003517b51c496cb18`；
Docker `data-incident-gym-postgres-1` healthy。

## 停止规则（现有合同，本阶段不改代码）

- 滚动窗口：最近 12 个模型 cell 中 ≥10 未通过 → run 自动暂停（product 内生效）。
- 恢复失败（ENVIRONMENT_OR_RECOVERY_FAILURE）即停。
- preflight 失败 → 失败即停：回执归档（双侧摘要一致后腾出活动路径）、如实记录、
  **重新授权前不重试**；回执存在本身阻塞再 preflight（既有机制）。
- 429/5xx：按 v24 授权计划 §3.2 口径——runner 无自动停机合同，不声称自动处置；
  transport diagnostics 如实记录；执行者手动熔断 = 终止 launcher 进程并在报告归属。
- 任何与本计划不匹配 → 停，记录，交 owner 裁定。

## 观察项（必录）

回执 sha256；ledger sha256；各段起止时间与 terminal cells；`stop_reason` 原文；
预算（model requests、in/out tokens、tool calls、transport 错误计数）；
GATE_INTERNAL_ERROR 计数；P-1 复核口径（I1/I2/kernel-contract 拒绝数与 verdict 分布，
含 INDETERMINABLE 明细）；结束指纹 == F0。

## 历史用量锚定（不做新预算断言）

v31 全段 79 cells 终态：260 requests、2.46M in / 697K out tokens、381 tool calls；
单 cell 结构上限 8 requests / 8 tool calls / 300s（8/8/2/300 预算不变，见冻结计划）。

## 边界

主树在本阶段只允许本授权记录等 docs 提交；不 push；不修改冻结身份、旧归档或历史
文件；不重跑 v31。
