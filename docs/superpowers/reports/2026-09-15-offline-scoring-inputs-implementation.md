# T01–T03 实施报告：评分输入归档与离线重评闭环

- 日期：2026-09-15（含同日第一轮审计整改）。
- HEAD：`e664b8e254af5864650f501f19d442d229253c36`（工作树含既有未提交文件；本次未提交、未推送）。
- 范围：[改进计划](../plans/2026-09-15-research-driven-improvement-plan.md) 阶段 A 的 T01、T02、T03。
- 合同依据：[评分输入归档与离线重评合同（T01）](../specs/2026-09-15-offline-scoring-inputs-contract.md)
  与 `docs/requirements.md` 的 M14 修订 / §9 / §13.1。
- 交付三分离：**实现完成**（第 1–3 节）；**环境验证已完成**（第 4 节：真实 PostgreSQL/dbt
  integration 全套与 e2e 非真实模型套件通过，剩余未跑项在节内逐条注明）；**真实模型行为未测量**
  （本轮不涉及模型调用）。

## 1. 交付物

| 文件 | 内容 | 任务 |
| --- | --- | --- |
| `docs/superpowers/specs/2026-09-15-offline-scoring-inputs-contract.md` | 字段级合同与 ADR：公开/私有面、原始/派生结果、版本策略、缺失处理、历史分类、缓存完整性与差异口径 | T01 |
| `docs/requirements.md` | M14 修订记录、§9 两条只读命令、§13.1 附件与离线重评权威合同 | T01 |
| `src/data_incident_gym/evaluation_inputs.py` | `EvaluationInputBundle`、索引、严格加载器、分类、导出/导入、evaluator 身份摘要、恢复证明 | T02 |
| `src/data_incident_gym/evaluation_rescore.py` | `score_run_offline`、`compare_offline_scores`、派生评分写出与完整性校验 | T03 |
| `src/data_incident_gym/evaluation_runner.py` | 归档接入：artifact 写出后写评分附件；捕获恢复返回的 case/state/fingerprint；`EvaluationAttemptResult.scoring_inputs_dir` | T02 |
| `src/data_incident_gym/cli.py` | `eval score` 与 `eval compare-scores`；无法对照时输出"无法比较（原因）" | T03 |
| `src/data_incident_gym/benchmark_runner.py` | 向 `EvaluationRunner` 传递 `project_root`，附件写入运行根目录 | T02 |
| `tests/unit/test_offline_scoring.py` | 28 项：round-trip、拒绝用例、kernel 重评、缓存篡改、跨 run 绑定、依据变化、不可对照、恢复证明、服务链路 | T02/T03 |
| `tests/integration/test_evaluation_runner.py` | 真实闭环扩展：真实 DB/dbt 运行后断言附件生成、恢复指纹、分类与离线重评逐项一致 | T02/T03 |
| `tests/unit/test_cli.py` | `eval score`/`eval compare-scores` 装配、分类错误路径、未知 scorer、不可对照输出 | T03 |
| `tests/unit/test_m7_contracts.py` | 运行顺序合同适配：fake verification 变为真实冻结对象，断言附件目录生成 | T02 |
| `tests/unit/test_p1_isolation.py` | 诊断平面禁词新增 `scoring-inputs` | T02 |

## 2. 实现要点

**附件（T02）**：`eval run`（以及 benchmark 单元格，两者共用 `EvaluationRunner`）在写出六文件后，
把评分输入固化到 `.dig/scoring-inputs/<run_id>/`：`evaluation_inputs.json`（私有 `ScenarioSpec`
快照、冻结 `ScenarioVerification`、完整 `DiagnosisRunResult`、恢复证明、预算、原 evaluator 身份、
六个产物的摘要表，各带自摘要）与 `index.json`（`created_at`、`inputs_digest`、文件原始字节
sha256）。写入为临时目录 + rename，拒绝覆写与符号链接。

**严格加载**：重复 JSON 键、未知 schema 版本、文件摘要不符、`inputs_digest` 不符、目录/索引/
附件三方 `run_id` 不一致、场景/验证/诊断/恢复摘要不符、路径与符号链接逃逸、未知 evaluator
版本，全部以固定代码拒绝。kernel 策略的终态从归档 trace 的 `KERNEL_STATE` 事件恢复为类型化
`InvestigationState` 并重新校验整份 `DiagnosisRunResult`（不从前端 diagnosis 重造）。

**恢复证明**：附件记录 `lab.restore` 实际返回的 case、state 与 baseline fingerprint；case 必须与
bundle 一致，fingerprint 必须是 64-hex（可缺失），缺失或格式不符即拒绝，不允许用布尔值加固定
标签代替来源。

**离线评分（T03）**：`eval score <run_id>` 只读加载附件、复用 `DeterministicEvaluator`，把派生结果
写到 `artifacts/rescores/<run_id>/<score_id>/`：`provenance.json`（`inputs_digest`、scorer 身份、
原 evaluator 身份、原评分状态、三个派生产物的原始字节摘要表）、`evaluation.json`、`diff.json`
（逐项差异：适用性/通过状态、`details_changed` 与 before/after expected/actual；不可对照时
`available=false` + 固定原因）、`report.md`。`score_id` 由 `inputs_digest` + scorer 源码/依赖摘要 +
评分配置共同派生；同一输入重复评分返回既有结果，且先校验缓存完整性（摘要 + run_id/score_id +
`diff.after_status` 与 evaluation 状态一致），不一致即拒绝，不重写、不重跑、不原地改分。
`eval compare-scores` 对同一 run 的两个派生评分输出逐项差异。

**历史产物分类**：`RE_SCORABLE`（附件齐全且校验通过）/ `PARTIAL_ANALYSIS`（六文件可读但缺附件，
只读分析不产出评分）/ `NOT_RE_SCORABLE`（缺失、损坏、篡改、跨 run、未知版本）。CLI 失败路径打印
分类与原因；不从当前 `config/scenarios` 或旧 `PASSED` 布尔值补造私有验证事实。

## 3. 第一轮审计整改（2026-09-15）

审计报告 5 项发现全部确认并修复，每项都有对应回归测试：

| 发现 | 根因 | 修复 | 回归 |
| --- | --- | --- | --- |
| P1 kernel 离线重评崩溃 | `_check_change` 对 `ControllerCheck` 读取不存在的 `applicability` | 改为 `getattr` 判定，仅两类检查都有该字段时比较适用性；controller 差异不填 applicability | `test_kernel_run_rescore_reports_controller_checks`（构造带原始评分的 kernel 夹具，断言三类 controller check 全部 UNCHANGED 且与直接评测逐项一致） |
| P1 缓存评分被修改后仍被接受 | 命中既有 `score_id` 时未做完整性校验 | `provenance.files` 记录 evaluation/diff/report 原始字节摘要；加载时先验摘要，再核 run_id、score_id、`diff.after_status` 与 evaluation 状态一致 | `test_cached_score_rejects_status_flip`、`test_cached_score_rejects_tampered_derived_files`（三文件参数化） |
| P1 附件加载允许跨 run 错配 | 只校验索引 `run_id`，未校验 bundle `run_id` | 加载器增加 `bundle.run_id == 请求 run_id` 检查，目录/索引/附件三方绑定 | `test_loader_rejects_bundle_copied_to_another_run_with_patched_index`（复制附件 + 只改索引 run_id → 拒绝且分类 NOT_RE_SCORABLE） |
| P2 检查内容变化被报告为"无变化" | 差异只比较适用性与通过状态 | `CheckChange` 增加 `details_changed` 与 before/after expected/actual；`changed_check_codes` 同时计入依据变化；报告逐项标注"expected/actual 变化" | `test_details_only_change_is_reported`（受控 scorer 只改一条 check 的 actual，断言 `change=UNCHANGED`、`details_changed=True`、仍进入变更列表） |
| P2 无法对照时仍宣称"与原评分一致" | 报告与 CLI 把空差异直接当一致 | 报告在 `available=false` 时输出"无法与归档评分比较（原因）"；CLI 输出"无法比较（原因）" | `test_unavailable_diff_is_reported_as_unavailable`、`test_eval_score_reports_unavailable_diff` |

验收缺口同步补齐：

- **T01 权威需求同步**：`docs/requirements.md` 新增 M14 修订记录、§9 两条命令要求与 §13.1 附件/
  离线重评合同；T01 设计文档状态同步为"已按本文件同步"。
- **恢复证明保留真实返回值**：`RecoveryProof` 由 `{source, state}` 扩展为
  `{source, incident_case_id, state, fingerprint}`；runner 捕获 `lab.restore` 返回的 case 与
  fingerprint，格式不符即拒绝。回归：`test_bundle_records_recovery_proof_with_fingerprint`、
  `test_bundle_rejects_malformed_recovery_fingerprint`。
- **真实产物 → 离线重评服务链路**：新增 `test_runner_service_chain_writes_attachment_then_rescores`
  与 `tests/integration/test_evaluation_runner.py` 的真实闭环用例，断言附件记录恢复指纹、分类为
  `RE_SCORABLE`、离线重评 `created=True` 且重算与原评分逐项一致（Docker 可用后的完整结果见第 4 节）。

## 4. 验证

已运行（2026-09-15，Windows，Python 3.12；Docker Desktop 与 PostgreSQL 17.6 容器可用）：

| 检查 | 结果 |
| --- | --- |
| `uv run ruff check .` | 通过 |
| `uv run pytest tests/unit -q` | 611 passed, 4 skipped（其中 `test_offline_scoring.py` 28 项） |
| `uv run pytest tests/integration -q` | **36 passed**（39 分 45 秒，真实 PostgreSQL + dbt） |
| `uv run pytest tests/e2e -m 'not real_model' -q` | **47 passed**, 10 deselected（1 小时 22 分 50 秒） |
| `uv lock --check` | 通过 |
| `git diff --check` | 通过（仅 `AGENTS.md` 既有的 LF/CRLF 提示，该文件未被本次修改） |
| `uv run data-incident-gym eval --help` | 新命令已注册 |
| `uv run data-incident-gym eval score <历史 run_id>` | 按设计拒绝：`PARTIAL_ANALYSIS`（`SCORING_INPUTS_MISSING, LEGACY_ARTIFACTS_READABLE`），exit 1，不产出评分 |
| `uv run data-incident-gym eval score <非法 id>` | 按设计拒绝，exit 1 |
| `uv run data-incident-gym eval score <本次真实 run_id>` | 命中集成测试已生成的派生评分并返回既有结果（PASSED、`changed_checks: 无`），exit 0；验证真实数据上的幂等与缓存完整性路径 |

真实产物 → 离线重评链路：`tests/integration/test_evaluation_runner.py` 在真实数据库与 dbt 上跑通
完整闭环（reset → inject → build → FunctionModel 诊断 → 评测 → 六文件 → 附件），并断言附件记录的
恢复指纹非空、分类为 `RE_SCORABLE`、离线重评 `created=True`、`diff.available=True`、
`changed_check_codes=()`，且重算结果与运行期评分逐项一致。本轮 e2e 套件另外产生了 40 个真实评分
附件，全部通过严格加载与重评校验，无失败。

未运行及原因：

- 带 `real_model` marker 的 e2e 用例（`-m 'not real_model'` 下 10 个 deselected）：需要显式
  授权、模型预算与冻结身份，本轮不涉及。
- 正式 benchmark 执行、manifest 冻结与 git 提交：按各自授权单独执行，本轮不涉及。

## 5. 不变量核对

- 原始六文件、manifest、ledger 与既有报告未改动；单测在重评前后对 `.dig` 附件与 `artifacts/<run_id>`
  全量摘要做相等断言。
- 诊断平面隔离：`test_p1_isolation.py` 新增 `scoring-inputs` 禁词，诊断模块与 prompts 不得引用。
- 权威合同同步（M14/§9/§13.1）只增加新合同条文，未修改诊断平面、工具权限、预算、evaluator
  判定规则、scenario 与既有冻结 manifest。
- 未提交 git、未生成新 manifest、未调用模型、未执行任何正式批次操作。
- 离线评分路径不依赖模型、数据库、dbt 与网络；`test_offline_score_needs_no_network` 在
  `socket.socket` 被打桩抛错的条件下完成评分。

## 6. 后续（T04 起的前置）

T04（公开参考解）与 T05（回放库）按计划依赖 T02 的附件与加载器；T07（弃答/引用指标）依赖 T03
的离线重放入口；T08（重复可靠性协议）复用现有 `repeat_index` 维度。阶段 B 启动前的
integration/e2e 端到端复评验证已完成（第 4 节）。
