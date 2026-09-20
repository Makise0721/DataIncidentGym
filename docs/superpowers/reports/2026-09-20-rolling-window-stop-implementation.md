# 滚动窗口停止机制实施与离线验证报告（2026-09-20）

- 授权依据：所有者 2026-09-20 指令。授权范围：停止机制实现与离线验证；不授权提前调用模型、
  运行数据库、重冻 manifest、提交或 push。本报告只记录已执行内容。
- 执行基线：`main` @ `1904dda`（v24 阶段 B 费用与窗口申请提交）。开始前核对工作区：已有改动为
  `docs/superpowers/plans/2026-09-19-v24-phase-b-cost-proposal.md`（未提交，内容保留）及若干未跟踪
  历史文档、`.zcode/`、`skills-lock.json`，均未触碰。
- 规则来源：所有者 2026-09-20 批准的大面积未通过停止规则（见放行申请 §3）。

## 1. 实现内容（未提交，等待所有者处置）

改动文件：`src/data_incident_gym/benchmark_runner.py`、`src/data_incident_gym/cli.py`、
`tests/unit/test_benchmark_runner.py`、`tests/unit/test_cli.py`。

- `rolling_window_stop_due`（benchmark_runner.py）：累计终态 model-backed 格不足 12 时不触发；
  达到后在最近 12 格中数未通过数，≥10 即触发。固定规则格从不进入窗口。
- 通过判定：ledger COMPLETED（等价评测 PASSED）**且** `controller_checks` 全部通过。会话内直接取
  内存中的 attempt 结果；续跑（resume）时从 `artifacts/<run_id>/evaluation.json` 重读并经
  `EvaluationResult` 校验，缺件、损坏或与 ledger 不一致一律抛 `BenchmarkRunnerError`（fail-closed，
  不猜测通过）。
- 检查时机：每个 model-backed 格终态 ledger 落账之后、下一格启动之前；`run()` 启动时先按 ledger
  文件顺序（真实完成顺序）重建窗口再检查——暂停后的续跑会立即再次暂停，不会继续执行剩余格。
- 触发后行为：保留 ledger、归档与恢复结果原样；不重跑、不替换失败格、不自动续跑（runner 既有
  语义本就不复跑终态格，本次未改动）。
- 恢复失败立即停止：沿用既有 `stop_after_cell`（适用 `ENVIRONMENT_VERIFIED` / `RECOVERY_HEALTHY`
  失败或 setup 工作流错误即停），语义未变，新增测试覆盖。
- `BenchmarkRunResult` 新增 `stop_reason` 字段，取值 `NONE` / `RUN_SETUP_ERROR` /
  `ENVIRONMENT_OR_RECOVERY_FAILURE` / `ROLLING_WINDOW_UNPASSED_PAUSE`，默认 `NONE`（向后兼容，
  该对象为 CLI 瞬态返回值，非持久化产物 schema）；`benchmark run` 输出新增一行 `stop_reason:`。

## 2. 定向离线回归

新增（tests/unit/test_benchmark_runner.py）：

| 测试 | 覆盖 |
| --- | --- |
| `test_rolling_window_helper_threshold_and_recency` | 阈值边界与窗口滑动（旧的 10 失败滑出窗口后不触发） |
| `test_rolling_window_pauses_before_next_cell_at_ten_of_twelve` | 第 12 个 model-backed 格终态后、下一格启动前暂停；ledger 不含下一格 |
| `test_rolling_window_does_not_pause_at_nine_failures` | 9/12 未通过不触发，整套跑完 106 格 |
| `test_rolling_window_counts_completed_cell_with_failed_controller_gate` | COMPLETED 但 controller 门失败的格计为未通过（不能仅看 COMPLETED） |
| `test_rolling_window_ignores_fixed_rule_cells` | 12 个固定规则格全部未通过也不触发 |
| `test_runner_resume_reconstructs_window_and_pauses_without_new_cells` | 续跑时从 ledger+evaluation.json 重建窗口，立即暂停、零新格 |
| `test_runner_resume_rejects_completed_cell_without_readable_evaluation` | COMPLETED 格缺 evaluation 产物即 fail-closed 报错 |

新增（tests/unit/test_cli.py）：`test_benchmark_run_prints_stop_reason_for_rolling_window_pause`。
调整：`test_runner_reuses_terminal_ledger_without_retry` 为 COMPLETED model-backed 格补写
evaluation.json 产物（第二次 run() 现在会重建窗口并读取产物）。既有停止行为测试补充了
`stop_reason` 断言。

## 3. 验证命令与结果（全部离线，无数据库、无 doctor、无模型探针、无 benchmark run）

```powershell
uv run ruff check .                      # All checks passed!
uv run pytest tests/unit -q              # 1005 passed, 5 skipped
uv lock --check                          # Resolved 105 packages [ok]
git diff --check                         # 通过（benchmark_runner.py 有 CRLF→LF 提示，非错误）
```

integration / e2e 未运行：需要数据库环境，本轮未获授权；改动限于 benchmark 编排逻辑与一行 CLI
输出，单测已覆盖。

## 4. v24 冻结身份核对

- 字节不变：`config/benchmark/p1-formal-v24.json` sha256 =
  `a4e59d2fe533bc9773da8881328551537b1755dc88e4d9d24c43aefb89cdaa67`，与放行申请一致。
- 当前实现上 `benchmark verify` 通过：v24 与 v23 均通过（17 catalog；12 formal；106/94/12）。
  改动文件不在 `verify_manifest` 的重算输入内（catalog、profile spec、ScenarioSpec/Diagnosis
  schema、evaluator.py 摘要、policies、budget 均未触碰）。
- **影响点（需所有者裁定）**：`_verify_checkout`（preflight/run 前置）要求干净检出，且 HEAD 等于
  v24 的 `implementation_revision`（`a89f8d1cd63a20ad410af11c29643368cc45173e`）或差异仅为清单
  路径。当前工作区含未提交改动即会被拒绝；本机制一旦提交，HEAD 将超出该修订且差异包含 `src/`、
  `tests/`、`docs/` 路径，阶段 B 的 preflight/run 将无法在现冻结身份下启动。按指令：报告并停止，
  不自行重冻。处理方式（另行冻结授权或修订检出合同）由所有者裁定。

## 5. 状态

- 代码与测试改动已通过全部离线门禁；随后按所有者 2026-09-20 裁定完成独立审计（发现全部修复或
  记录，见 [审计报告](2026-09-20-rolling-window-stop-audit.md)）并单独提交为
  `4e485f1642dbccab0f4f1cf5db7bec4987691e86`。
- 冻结身份按裁定选 (a) 处理：保留 v24，另建 `p1-formal-v25` 绑定本提交（经批准提交
  `a798758b…`），见 [v25 冻结报告](2026-09-20-v25-freeze.md)。
- 阶段 B 未放行：未预检、未联网、未动数据库。等待所有者确定开始时间并明确放行。
