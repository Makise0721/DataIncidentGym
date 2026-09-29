# T12 规划器实验第一道放行独立复核（2026-09-28）

结论：`p1-planner-compare-v1` 的**冻结身份成立**，但第一道放行的完整验收尚未收口；第二道真实执行暂不放行。本轮只读核对，未调用模型或数据库、未改清单与产物。

## 成立的部分

- `config/benchmark/p1-planner-compare-v1.json` 文件 SHA-256 为 `e4a09d16dd15b5b4433b5a25d1e5b8544a4b8c341cb97b793853fcd1d0378826`，与报告一致；绑定 X=`1fa04e9`，X→Y=`6643256` 的差异恰为该清单文件。
- Y 的 `../w-compare` 工作树 porcelain 为空；在该树实际调用 `BenchmarkRunner._verify_checkout`，结果 `CHECKOUT_GATE_PASSED`。当前主树的 `experiment verify` 通过：17 catalog、12 formal、108 cells/108 model-backed；模型配对和预算与方案相符。
- 定向 `test_experiment_cli.py`、`test_experiment_runner_regression.py`、`test_planner_probe.py`、`test_planner_comparison_manifest.py` 共 18 passed。主树原有 `README.md`、`.env.diagnostic.example` 修改未触碰。

## P1-1：规划器专用探针没有成为 run 的强制门

`cli.py` 的 `experiment_preflight` 先调用 `runner.preflight()`，写入通过的 `doctor.json`，随后才运行 `run_planner_compatibility_probe`；探针失败会使 CLI exit 1，但没有持久化、绑定到 manifest 的探针裁决。`experiment_run` 直接调用 `runner.run()`，后者只核对 doctor 回执。于是“doctor PASS → 规划器探针 FAIL → 直接调用 experiment run”仍能启动格，违反方案 §2.3 的“探针失败即停”。现有 `test_experiment_full_108_cell_suite_completes_offline` 已证明同一 runner 只经 `runner.preflight()`、完全没有专用探针也可 `run()` 成功；它是兼容回归，也是这个绕过的现成见证。

修复验收：实验 preflight 将专用探针结果写成不可替换的回执，绑定 manifest 摘要、修订、模型身份和 scope；无论探针失败、缺件、篡改、身份不符，实验 `run` 都须在任何 cell 启动前拒绝。成功回执才允许 run。失败回执原样保留，不删除后“重试当首次”。用正常 CLI 和直接 runner 两条入口做定向回归。

## P1-2：正式结果判读尚无符合预登记合同的实现

当前 `experiment` 命令只有 `freeze/verify/preflight/run/partial`，没有实验正式报告入口；既有 `BenchmarkReporter` 仍拒绝非 106/94/12 赛程，不能消费 108 格。冻结报告提到的 `C:\Users\29913\codex_space\planner-compare-analysis.py` 位于仓库外，不在 X/Y 中，也未随清单绑定或通过合成验收。该脚本有三处影响结论的具体问题：

1. `success = evaluation.get("status") == "PASSED"` 忽略适用 controller 门；既有 `_evaluation_passed` 明确要求两者同时通过，因此可能把合同门失败的格计为通过。
2. evaluator 重算只比较 `failed_check_codes`，且把不一致与恢复失败加入 `problems` 后仍继续输出主指标和 `SCREENING`；没有逐格比对完整 `EvaluationResult`、metadata 的 manifest 身份、ledger 终态及场景摘要。存在完整性问题时可能仍给出正向信号。
3. 方案 §4 要求 T07 的状态/弃答/逐 claim/引用指标、T08 完整组 `pass^1–pass^3` 和 STEP/CLOSE/STATE 计划指标；脚本只打印配对通过、少量用量与拒绝复核，`experiment partial` 也明确只是只读状态分析，不等于正式报告。

修复验收：在仓库内提供只读的实验报告入口及合成归档回归；严格加载六文件与 scoring-inputs，逐格校验 run/case/strategy/repeat、manifest/result-inputs/policy、ledger/恢复和 evaluator **完整对象相等**，任一失败整体拒绝正式筛选结论。成功判定复用 `_evaluation_passed`，不另写 `status == PASSED` 捷径；上述 T07/T08/计划指标按方案的分子分母输出，零分母与不完整组明确标记。报告源与测试须先进入实现修订，再冻结。

## 待确认的合同偏差

方案 §2 写“场景摘要与准入身份”，当前实验 manifest 只保存 scenario catalog 摘要，没有准入证书/记录身份，也未在冻结时校验其与现行场景摘要一致。场景摘要能防合同漂移，但不能单独证明 T06 准入成立。进一步只读核对本机 ignored 产物：`artifacts/admissions/all.json` 仅有 4 条，12 个正式场景均不在其中；`artifacts/certifications/catalog.json` 虽有 18 条，但 12 个正式场景的旧证书**没有**必填 `scenario_digest` 字段，按当前模型不能加载，更不能与当前合同摘要配对。修复前应明确：取得并验证当前合同的准入身份，或经 owner 修订方案中的准入要求；不能默认为已满足，也不能把旧证书当新证书。取得新证书若需真实 DB/dbt 认证，应另行裁定执行范围。

## 冻结处置

`v1` 清单与 Y 已冻结，保持原样；以上修复会改变实现修订。按项目的冻结纪律，修复验收后登记并冻结**新实验身份**（建议 `p1-planner-compare-v2`），重做 X→仅清单 Y、旧 v1 verify、不干净检出与真实检出门核对。新身份第二道是否启动，仍需 owner 另行放行。不得在现有 `v1` 干净 checkout 中临时换脚本或绕过探针门运行。

## 2026-09-29 离线整改复核

P1-1 与 P1-2 的实现已在 `2ef52a5` 落地，并经独立审读和定向复跑：规划器专用探针纳入 runner 的 preflight，写入绑定清单、检出修订、模型与 108 格作用域的回执；`run()` 在首格前严格读取回执，失败、缺失或篡改均拒绝。仓库内新增只读 `experiment report`，严格重载 108 格六文件及评分输入、核对完整 `EvaluationResult`、恢复和身份，复用既有整格成功、T07/T08 口径并报告 PLAN 轨迹。trace 中的 `GATE_INTERNAL_ERROR` 直接阻断正式报告。完整合成套件与拒绝分支均有回归。

审计又发现报告把单轮 `output_retry_used` 与提交门拒绝混算为精确重试总数；`414f968` 已把它改为不完整的协议观测值和独立的门拒绝事件数，缺失观测记 `null`，不再声称总次数。执行会话报告相关离线测试 184 passed / 1 skipped；本会话在整合到主树后独立重跑四个相关测试文件，**18 passed**，并核对 Ruff、`git diff --check` 与 `experiment verify` 均通过。`v1` 清单字节仍为上述 SHA-256，冻结 checkout 未动。

**第一道仍未收口**：T06 准入身份的合同偏差未决。正式 12 场景目前没有可按新 schema 加载的准入证书；取得当前合同的准入身份，或明确修订预登记方案后，才可冻结 `v2` 并复核最终检出门。以上离线整改不放行真实模型 preflight 或测量。
