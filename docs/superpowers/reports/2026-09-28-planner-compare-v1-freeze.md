# p1-planner-compare-v1 实验身份实现与冻结报告（2026-09-28，第一道放行）

- 授权：owner 2026-09-28 批准 T12 方案第一道（方案
  `docs/superpowers/plans/2026-09-28-t12-planner-real-model-measurement-proposal.md`，
  `f8d6349`；聊天原话「批准」）。本阶段零模型请求、零数据库测量；实验
  preflight（含规划器探针）属第二道，另行放行。
- 冻结：X=`1fa04e9`（包含全部最终代码/测试；提交链 e7e1293 合同、ca521b6
  runner 分派、CLI 命令组、传输诊断、兼容探针、探针接线、108 格回归）、
  Y=`6643256`（仅清单文件，`git diff X..Y` 恰为
  `config/benchmark/p1-planner-compare-v1.json` 一个）。manifest sha256
  `e4a09d16dd15b5b4433b5a25d1e5b8544a4b8c341cb97b793853fcd1d0378826`；
  绑定 deepseek/deepseek-v4.1-flash → commandcode，空 overrides；
  budget 8/8/2/300；108 格全部 model-backed。

## 1. 先决实现（方案 §2）

- **独立合同** `p1.planner_comparison_manifest.v1`（`planner_comparison_manifest.py`）：
  命名空间 `p1-planner-compare-vN`，注册表 `APPROVED_EXPERIMENT_IDS`；三策略
  （EVIDENCE_PLANNER/DIAGNOSTIC_KERNEL/STATIC_SKILL）三份完整政策面；12 正式
  dev 场景 ×3 重复=108 格，场景位移 0/4/8、场景内策略次序按 repeat 轮换
  （P→K→S / K→S→P / S→P→K，各策略先/中/后各 12 次）；v1 的
  `generate_cells`/loader/`APPROVED_MANIFEST_IDS` 未动，新旧 schema 显式
  分派、互拒对方 ID 与文件（含重复 JSON 键拒绝）。
- **runner 窄适配**（`benchmark_runner.py`）：`_manifest_identity_approved`
  /`_verify_manifest_for`/`_manifest_relpath_for` 按 schema 分派；
  `__init__` 与 `_verify_checkout` 改用分派；`for_project` 类型放宽为联合。
  v1 行为逐字不变（全部既有 runner 测试原样通过）。
- **CLI `experiment` 命令组**（`cli.py`）：freeze/verify/preflight/run/partial，
  无子集选项；canonical 路径白名单只认实验 ID。
- **规划器传输诊断**（`planner_agent.py`，方案 §2.4）：provider 起源失败在
  v1 trace 内追加 `ModelProtocolTraceEvent`（stage=PROVIDER_RESPONSE、
  transport=M23 词表、只计固定类别/状态码，无异常文本）；MODEL_TIMEOUT
  （run 级看门狗）不附传输诊断；非可分类失败不加事件。合成覆盖四类：
  HTTP_429/503（FunctionModel 直抛）、CONNECTION_ERROR 与 SDK 读超时
  （真实 SDK MockTransport 链）、TIMEOUT 臂（APITimeoutError）、非传输
  （ValueError→None）；全部脱敏断言（DO_NOT_LEAK/secret 不出现）+ 严格
  重载（dump→validate_json 往返相等）。
- **规划器兼容探针**（`planner_probe.py`，方案 §2.3）：`experiment preflight`
  在 doctor 通过后运行 `run_planner_compatibility_probe`——真实规划器机制
  （控制器 verdict/收据/终态校验）+ 绑定清单的 settings 模型 + 确定性只读
  后端；PASS=至少一个被接受的 plan_step 收据且经 submit_diagnosis 终态；
  失败即 exit 1、不生成测量格。离线测试覆盖通过环/无动作工具/传输失败。

## 2. 冻结验收（方案 §5 第一道）

- **检出门**：Y 的独立干净 detached worktree `../w-compare`（porcelain 0，
  子模块 36bde6c）运行真实 `BenchmarkRunner._verify_checkout`：
  **PASSED**（分派到 `verify_experiment_manifest`）。
- **清单验证**：`experiment verify` PASSED（17 catalog / 12 formal /
  108 cells / 108 model-backed）；builder/verify 冻结时与当前树重算一致。
- **离线回归**（§2.2）：真实 runner + 脚本化评估器驱动 108 格——全绿完成
  （108/108 终态、ledger 216 行、策略各 36、run_id 序与清单一致）与滚动
  窗口暂停（前 12 格 10 败 → 第 13 格前暂停，stop_reason
  ROLLING_WINDOW_UNPASSED_PAUSE）均按合同生效；`analyze_partial_suite`
  partial 入口在暂停套上可用。
- **v1 不退化**：全量单元 **1192 passed / 5 skipped**（基线 1165 + 新增 27），
  ruff 全清洁；v1 manifest/cli/runner/report 既有测试原样通过；冻结文件
  入库后复跑目标集 120 passed。
- **预登记分析脚本** `codex_space/planner-compare-analysis.py`：策略身份
  守卫（三策略 prompt+controller 版本逐格断言）、格表绑定冻结清单、显式
  (case, repeat) 配对、§4 主/分/敏感性口径与筛选判据（净胜 ≥4/36、三层
  不降、错误确认不升、>9 传输对不可判）逐项判定、P-1 复核与逐格明细；
  空归档集干跑语义正确（报 EMPTY、守卫待执行，不输出汇总）。

## 3. 边界与待决

- 本阶段零真实请求；未 push；v1 冻结清单与历史归档原样；main 工作树
  owner 的 README/.env.diagnostic.example 未提交改动不属于任何提交。
- 执行 checkout 固定为 `../w-compare` @ Y=`6643256`。**第二道待 owner
  放行**：Y 干净树内准备 PG/dbt/F0（指纹须 `e5c7848e…`）与六键+User-scope
  模型键环境 → `experiment preflight`（doctor+规划器探针，各一次）→
  通过后 `experiment run`（一次完整 108 格）→ 结束后严格加载、逐格重算、
  F0 复核、预登记脚本出结果报告。墙钟预算按 v39–v42 实测 ~2.5 分钟/格
  估算 108 格 ≈ 4.5 小时 + preflight，建议整段预留 5 小时。
