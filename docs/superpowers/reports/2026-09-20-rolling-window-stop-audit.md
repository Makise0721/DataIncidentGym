# 滚动窗口停止机制审计报告（2026-09-20）

- 审计对象：停止机制未提交改动（工作树 vs `main` @ `1904dda`），涉及
  `src/data_incident_gym/benchmark_runner.py`、`src/data_incident_gym/cli.py`、
  `tests/unit/test_benchmark_runner.py`、`tests/unit/test_cli.py`。
- 审计依据：所有者 2026-09-20 指令的四项重点——适用 controller 门、跨运行窗口重建、
  产物身份校验、暂停前恢复是否完成。
- 审计方式：主代理自查 + 独立审计代理全量复核（读码、运行 69 项单测、在仓库外临时目录对
  真实 `BenchmarkRunner` 实证执行两个未覆盖场景后清理）。发现与处置如下。

## 1. 四项重点结论

| 重点 | 结论 | 证据要点 |
| --- | --- | --- |
| 适用 controller 门 | 通过 | 通过判定 = `state == "COMPLETED"`（⟺ 评测 PASSED，`EvaluationAttemptResult` 校验器强制一致）且 `controller_checks` 全过。kernel 策略恒有三门（缺 kernel state 时产出**失败**的 KERNEL_STATE_VALID，不可能静默为空）；非 kernel 策略为空元组，空洞通过，符合"适用"语义。setup 物化与 evaluator 回退路径的评测均为 FAILED，一律计未通过。 |
| 跨运行窗口重建 | 通过 | 窗口按 ledger 文件顺序重建；执行严格串行，终态条目按完成顺序追加（中断格在恢复时物化、即其真实终态时刻落账）。FAILED 条目直接计未通过、不读产物；COMPLETED 条目重读 evaluation.json。范围校验（`_read_ledger`、所选 scope、subset marker 一致性）先于重建执行。 |
| 产物身份校验 | 通过 | `run_id` 为 `manifest_id:sequence:case:strategy:repeat` 的 sha256 前 32 位，唯一绑定清单格；重读时校验 `EvaluationResult` 严格 schema + `run_id` + `incident_case_id` + status==PASSED 与 ledger COMPLETED 一致。缺件、符号链接（根/格目录/文件）、非法 JSON、重复键、schema 不符、身份不符、状态不符全部抛 `BenchmarkRunnerError`（fail-closed），无静默计通过路径。 |
| 暂停前恢复完成 | 通过 | 窗口检查与暂停 break 只发生在整格生命周期（含环境恢复与 RECOVERY_HEALTHY 评定、终态落账）之后；循环顶部 break 先于任何 STARTED 写入。暂停不打断在飞格。审计修复后，恢复进入已触发窗口时先物化 STARTED 残留格再决定暂停，暂停现场 ledger 完全终态化。 |

## 2. 发现与处置

| # | 来源/级别 | 发现 | 处置 |
| --- | --- | --- | --- |
| 1 | 自查/MAJOR | 同一格既操作停止（环境/恢复/setup）又触发窗口时，`stop_reason` 被窗口原因覆盖，误导"环境或端点故障保持停止"的判定 | 已修复：窗口原因仅在 `stop_reason == "NONE"` 时写入；环境失败与 setup 错误两个变体均补真触发测试 |
| 2 | 自查/MINOR | 产物重读只核对 `run_id`，未核对 `incident_case_id` | 已修复：两者均校验 |
| 3 | 独立/MINOR | 优先级测试空转（第 10 格时窗口仅 10 条目，从未真正触发），同一格优先级逻辑未被钉住 | 已修复：`_window_precedence_actions` 使窗口满 12 条且第 12 条即操作停止格；另补 `RUN_SETUP_ERROR` 变体 |
| 4 | 独立/MINOR | 恢复进入已触发窗口时，循环顶部 break 先于中断格物化，STARTED 残留格永远不落终态、不计入 `terminal_cells` | 已修复：改为两阶段——先物化所有 STARTED 残留格，再重建窗口决定暂停；补测试断言残留格获得 FAILED 终态（reason RUN_SETUP_ERROR）且零新格执行 |
| 5 | 独立/MINOR | `_completed_model_backed_window_pass` 未拒绝符号链接的 `artifacts/<run_id>` 目录（同级代码均拒绝） | 已修复：补 `artifact_dir.is_symlink()` 检查 |
| 6 | 独立/NOTE | evaluation.json 重读未拒绝重复 JSON 键（ledger/manifest 读取均拒绝） | 已修复：改用 `object_pairs_hook=_reject_duplicate_json_keys` |
| 7 | 独立/NOTE | 伪造/远古产物的 `controller_checks` 缺省 `()` 会被计为通过 | 接受并记录：本库内不可达——evaluator 对 kernel 策略恒产出三门，全部回退路径评测为 FAILED；fail-open 仅存在于外部伪造产物的威胁模型，不为此在 runner 内耦合策略语义 |
| 8 | 独立/NOTE | `_terminal_model_backed_window` 松散重解析 ledger 原始字典 | 接受并记录：同一锁下 `_read_ledger` 已先做全量校验，不可达状态已排除 |

## 3. 修复后验证（全部离线）

```powershell
uv run ruff check .                      # All checks passed!
uv run pytest tests/unit -q              # 1010 passed, 5 skipped
uv lock --check                          # Resolved 105 packages [ok]
git diff --check                         # exit 0（benchmark_runner.py 有 CRLF→LF 提示，非错误）
```

新增/重写测试：`test_operational_stop_reason_wins_when_window_triggers_on_same_cell`、
`test_setup_error_stop_reason_wins_when_window_triggers_on_same_cell`、
`test_runner_resume_materializes_stale_cell_before_rolling_window_pause`、
`test_runner_resume_rejects_completed_cell_with_foreign_evaluation`、
`test_runner_resume_rejects_completed_cell_with_corrupt_evaluation`。无数据库、无 doctor、
无模型探针、无 benchmark run。

## 4. 审计结论

**通过**。所有 BLOCKER/MAJOR/MINOR 发现已修复并有回归测试钉住；两项 NOTE 按上述理由接受并记录。
停止机制满足所有者 2026-09-20 批准的滚动窗口停止规则，具备进入"单独提交 → v25 冻结"流程的条件。
