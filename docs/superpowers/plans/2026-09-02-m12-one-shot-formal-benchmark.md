# M12 One-Shot Formal Benchmark Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在任何真实模型格开始前验证并冻结完整 harness，随后用唯一一次 106 格 P1 正式批次生成可独立复核的报告；若事后发现 harness 缺陷，只废止该批次，不补跑。

**Architecture:** 将正式 benchmark 分成不可混淆的四层：Manifest 定义样本，preflight receipt 证明执行前置条件，runner 只执行已授权 receipt 绑定的格，reporter 只读取并校验六文件产物。所有真实模型格在干净的 WSL Ubuntu checkout 中顺序执行；确定性 wire、策略表面和 synthetic 编排在真实批次前完成。

**Tech Stack:** Python 3.12、uv、Pydantic/PydanticAI、pytest/pytest-asyncio、Ruff、Typer、Docker Compose/PostgreSQL、dbt、WSL2 Ubuntu；模型端点使用 OpenAI-compatible HTTP API。

---

## 当前状态与硬边界

- 当前实现 revision 为 `f0c393f4e52d854bb75195238b86ec30af929c0c`；工作树包含并行代理改动，执行前必须先做只读 `git status`/`git diff`，不得还原或覆盖用户材料。
- `config/benchmark/p1-formal-v1.json` 的冻结内容保持 106 格、94 model-backed、12 fixed-rule；当前已知 SHA-256 为 `413a420c5040b182a15dff770cbded0a0dde342054bc4f38fe0aeac472c83c94`。实现完成后若 revision 改变，必须重新冻结并重新计算 SHA，不能把旧 hash 与新代码混用。
- M12 实施目标只到 “real-model release gate”：完成确定性 harness、CI、Manifest、preflight 和报告器后暂停。
- 本计划不授权 commit、push、真实模型调用、正式 benchmark 或删除既有 artifact；每项均需独立明确授权。
- 真实模型使用普通按量端点，不使用 Token Plan；Token Plan 不适合自动脚本和自定义 backend。[按量计费与缓存](https://mimo.mi.com/docs/en-US/price/pay-as-you-go)、[Token Plan 限制](https://mimo.mi.com/docs/en-US/price/token-plan)。

## 文件责任

- `docs/requirements.md`：一次性补充 M12 的 one-shot、harness invalidation、receipt 和报告合同。
- `src/data_incident_gym/benchmark_runner.py`：Manifest-bound preflight、receipt 校验、suite lock、ledger/artifact 一次性门禁与 resume 语义。
- `src/data_incident_gym/cli.py`：公开 `benchmark preflight`、`benchmark run`、`benchmark report` 命令，并保持 SHA 显式确认。
- `src/data_incident_gym/benchmark_report.py`：只读验证 106 格六文件并生成确定性 summary/report。
- `tests/integration/test_openai_wire_contract.py`：本地 fake OpenAI-compatible server 覆盖真实 provider/client wire 形状，不访问外网。
- `tests/e2e/test_p1_policy_matrix.py`：现有 34 个 Static/Kernel 格加四个最小辅助策略格，共 38 格确定性策略前置门禁。
- `tests/e2e/test_benchmark_synthetic_orchestration.py`：用 `FunctionModel`/`FixedRuleRunner` 走完整 106 格编排，验证顺序、ledger、artifact、恢复和零模型 fixed-rule。
- `tests/unit/test_benchmark_runner.py`、`tests/unit/test_benchmark_report.py`、`tests/unit/test_cli.py`：锁定公开 seam 和 fail-closed 行为。
- `config/benchmark/p1-formal-v1.json`：只在所有实现和 CI 门禁完成后重新冻结；不在正式结果产生后改写。

## Task 1: 一次性修订需求与正式合同

**Files:**
- Modify: `docs/requirements.md`
- Review: `config/benchmark/p1-formal-v1.json`

- [ ] 增加 M12 规则：106 个 Manifest cell 为唯一正式批次，94 个 model-backed cell 每个最多一次；同一 run ID 不得重跑、替换、补样本、提高预算或换 Manifest 追认。
- [ ] 明确正式运行前必须完成 checkout/Manifest 校验、空 ledger、106 个目标 artifact 不存在、完整 doctor 和绑定 receipt；`run()` 不能自行触发 live doctor。
- [ ] 明确中断恢复：已终态 cell 只跳过；遗留 `STARTED` cell 只物化一次 `RUN_SETUP_ERROR` 六文件失败，不重发模型请求。
- [ ] 明确任何正式模型格开始后发现 harness 缺陷，原批次结论必须标为 `INVALID_HARNESS`，允许修复代码但禁止再次运行该批次或用新批次拼接结果。
- [ ] 明确报告输入、指标分母、95% Wilson 区间、主比较与辅助策略隔离，以及禁止宣称生产能力或统计显著性。
- [ ] 审查完成后只提交需求 diff 的只读审查记录；本任务不因需求更新自动授权代码提交或正式运行。

## Task 2: 冻结真实 provider/client 的 wire contract

**Files:**
- Create or complete: `tests/integration/test_openai_wire_contract.py`

- [ ] 用本地 loopback fake HTTP server 拦截 `/chat/completions`，通过真实 `AsyncOpenAI`、`OpenAIProvider` 和 `OpenAIChatModel`，禁止 `FunctionModel` 替代此测试 seam。
- [ ] 覆盖至少一轮工具调用再返回结构化终态；响应包含 tool arguments、`reasoning_content`（若 provider 保留则只验证安全处理）、usage、模型名和错误响应映射。
- [ ] 断言请求使用 Manifest 绑定的 model/base URL 形状，工具参数为 JSON 字符串可回读，控制器能记录 token/请求数，且没有任何外网请求。
- [ ] 覆盖 malformed JSON、HTTP 错误和结构化终态拒绝各一条 fail-closed 路径；不得把 wire 错误伪装成业务 `CONFIRMED`。
- [ ] 默认 OpenAI client 必须显式关闭 SDK HTTP 自动重试，并在单格结束后关闭其连接池；用本地 HTTP 500 断言实际请求恰为一次，避免控制器预算之外的隐藏重发和 94 格连接泄漏。
- [ ] 运行：`uv run pytest tests/integration/test_openai_wire_contract.py -q`；期望全部通过且 fake server 收到的请求数可由测试断言确定。

## Task 3: 完成 runner preflight 与一次性 resume 门禁

**Files:**
- Modify: `src/data_incident_gym/benchmark_runner.py`
- Test: `tests/unit/test_benchmark_runner.py`
- Review: `src/data_incident_gym/cli.py`、`tests/unit/test_cli.py`

- [ ] 保持公开异步接口 `BenchmarkRunner.preflight() -> DoctorResult`：先执行 checkout/Manifest 校验，再在 `_ExclusiveSuiteLock` 内检查 doctor receipt 尚不存在、ledger 不存在或为空、所有 106 个 `artifacts/<run_id>` 均不存在。
- [ ] preflight 通过所有前置检查后才运行完整 doctor，并写入 `manifest_id`、Manifest SHA、implementation revision、实际 checkout revision、`result_inputs_sha256`、UTC 时间和完整 DoctorResult；doctor 失败也写 receipt，但不创建正式 cell。
- [ ] `run()` 只能读取已存在且字段完全匹配、状态为 `PASSED` 的 receipt；缺失、symlink、字段漂移、结果输入 hash 漂移或失败 receipt 均立即拒绝，不调用 live doctor、不调用模型。
- [ ] `run()` 在执行或恢复前检查每个从未出现在 ledger 中的 cell 的 artifact 目录；存在文件、目录或 symlink 时在第一个模型调用前拒绝。已终态 cell 仍跳过，`STARTED` 只物化一次六文件 setup error。
- [ ] 若 crash 发生在六文件原子发布后、terminal ledger 追加前，先把原六文件移动到 suite 内 `interrupted-artifacts/<run_id>` 保全，再在正式路径物化 `RUN_SETUP_ERROR`；恢复过程不得重发该格模型请求。
- [ ] 保持 ledger 的身份、顺序、双记录和 one-shot 约束；不改变 `ArtifactWriter` 的六文件合同。
- [ ] 先写缺失 receipt、失败 receipt、hash 漂移、非空 ledger、artifact 冲突和无模型调用测试使其 RED，再做最小 GREEN。
- [ ] 运行：`uv run pytest tests/unit/test_benchmark_runner.py tests/unit/test_cli.py -q`、`uv run ruff check src/data_incident_gym/benchmark_runner.py src/data_incident_gym/cli.py tests/unit/test_benchmark_runner.py tests/unit/test_cli.py`。

## Task 4: 建立 38 格六策略确定性前置门禁

**Files:**
- Modify: `tests/e2e/test_p1_policy_matrix.py`

- [ ] 保留现有 17 场景 × Static/Kernel 的 34 格，不修改其 Ground Truth 或分母。
- [ ] 增加且只增加四个代表格：
  - `order_volume_within_sla` + `NO_TOOL`：无业务工具，必须 fail-closed。
  - `silent_payment_drop_partition_a` + `KERNEL_NO_LINEAGE`：trace 不得出现 `get_dbt_lineage`。
  - `required_null_order_customer_a` + `KERNEL_NO_SCHEMA`：trace 不得出现 `get_relation_schema`。
  - `order_volume_within_sla` + `FIXED_RULE`：走 `FixedRuleRunner`，验证固定规则成功且零模型请求。
- [ ] 每格必须从真实 `IncidentLab.reset → prepare → build → diagnosis/FixedRule → evaluator → ArtifactWriter` 完成，并在测试中验证六文件、诊断终态、tool allowlist 和 `metadata.recovery_status == HEALTHY`。
- [ ] 三个受限模型策略在工具表面不满足时只允许 `MODEL_ERROR`/评测失败等安全终态，不得输出错误 `CONFIRMED`；fixed-rule 必须保持 evaluator 可判定。
- [ ] 先以 Docker/PostgreSQL 可用环境运行单格验证，再运行 38 格：`uv run pytest tests/e2e/test_p1_policy_matrix.py -m e2e -q`。Docker/dbt 原生崩溃只能记录为 environment-unverified，不能放宽断言或改产品代码。

## Task 5: 完成 106 格 synthetic orchestration 彩排

**Files:**
- Create or complete: `tests/e2e/test_benchmark_synthetic_orchestration.py`
- Review: `src/data_incident_gym/benchmark_runner.py`

- [ ] 构造与冻结 Manifest 相同的 106 格 runner，注入 fake passing doctor 和确定性 `EvaluationRunner` seam，但保留真实 `ArtifactWriter`；不连接 `https://api.xiaomimimo.com/v1`，不读取真实 key。Static/Kernel/No Tool/消融的业务控制策略由 Task 4 的 38 格真实 `FunctionModel` 流程覆盖，避免在本任务重复 106 次数据库/dbt 工作。
- [ ] synthetic evaluation 对各策略生成 schema 合法且可由六文件回读的受控终态；对 FIXED_RULE 断言 artifact 中模型请求、token 和工具调用均为零。
- [ ] 运行完整 `BenchmarkRunner.preflight()` 和 `run()`，验证 106 个 sequence 顺序、每格固定 run ID、ledger 恰为 212 行、每格恰有六文件和健康恢复状态；数据库实际 reset/inject/restore 由 Task 4 的 38 格门禁及完整 integration/E2E CI 证明。
- [ ] 在 synthetic 流程中模拟进程中断：保留一个 `STARTED` ledger entry 后恢复，断言该格只生成一次 `RUN_SETUP_ERROR`，其余未启动格各执行一次；禁止任何重复模型调用。
- [ ] 运行：`uv run pytest tests/e2e/test_benchmark_synthetic_orchestration.py -m e2e -q`；失败时先区分测试 fixture 缺陷、runner 缺陷和 Docker/dbt 环境故障，不修改正式分母。

## Task 6: 完成只读 suite reporter

**Files:**
- Create or complete: `src/data_incident_gym/benchmark_report.py`
- Modify: `src/data_incident_gym/cli.py`
- Test: `tests/unit/test_benchmark_report.py`

- [ ] 提供 `BenchmarkReporter(manifest, suite_root).write()`，只读取 doctor receipt、ledger 和六文件 artifact，不访问数据库、模型、evaluator 或外网。
- [ ] 校验 doctor 五元绑定（Manifest ID、Manifest SHA、implementation revision、实际 checkout revision、`result_inputs_sha256`）及 `PASSED`；校验 106 格 ledger 两条记录、顺序、身份、终态、artifact 六文件和 trace 终态；六文件 `code_revision` 必须等于 receipt 的实际 checkout revision，而不是误等同于实现提交。
- [ ] 校验 evidence ID/run scope、claim 引用、Kernel `InvestigationState` 类型化终态、工具 allowlist、恢复健康和 fixed-rule 零模型使用。
- [ ] 生成确定性文件：`artifacts/benchmarks/p1-formal-v1/summary.json` 与 `report.md`；不写 wall-clock `generated_at`。目标文件存在且字节不同必须拒绝覆盖，重复运行必须字节一致。
- [ ] 主指标只使用 Static/Kernel 主矩阵：paired success、根因准确率、不支持确认率、状态准确率、无故障准确率、affected-assets macro-F1、claim-evidence validity 和效率指标；辅助策略、No Tool、消融、fixed-rule 单独报告。
- [ ] 所有比例提供 95% Wilson 区间。claim-evidence validity 使用冻结 evaluator 的 run-level `CLAIM_EVIDENCE_COMPATIBLE` gate；gate 失败的整格 claims 计无效，不创建第二裁判。
- [ ] 结论优先级固定为：安全/协议 hard gate 失败 `INVALID`；Kernel 仅在 paired success 或证据有效率严格提高、根因不降、不支持确认不升且硬门全过时才为 `KERNEL_ADVANTAGE`；指标冲突为 `TRADEOFF`；否则 `NOT_PROVEN`，文本为“当前固定样本尚未证明 Diagnostic Kernel 优势。”
- [ ] CLI 必须要求 `--manifest` 和 `--confirm-sha256`，并提供：

```powershell
uv run data-incident-gym benchmark report `
  --manifest config/benchmark/p1-formal-v1.json `
  --confirm-sha256 413a420c5040b182a15dff770cbded0a0dde342054bc4f38fe0aeac472c83c94
```

- [ ] 先用全 synthetic fixture 覆盖成功与缺失/错配 fail-closed，再运行：`uv run pytest tests/unit/test_benchmark_report.py tests/unit/test_cli.py -q`。

## Task 7: 在真实运行前完成完整验证和 release review

**Files:**
- Review all tracked code/tests changed by Tasks 1–6

- [ ] 运行最小回归集：

```powershell
uv run ruff check .
uv run pytest tests/unit -q
uv run pytest tests/integration -q
uv run pytest tests/e2e -m 'not real_model' -q
uv lock --check
git diff --check
uv build
```

- [ ] 确认 real-model 测试仍明确 deselected，未有真实 endpoint 请求；确认 38 格确定性策略门禁和 106 格 synthetic orchestration 都通过。
- [ ] 在 Ubuntu CI 上验证 exact HEAD，包含 integration、非真实 E2E、wire contract、reporter 和 synthetic orchestration；CI 失败时停止，不进入 Manifest 重新冻结。
- [ ] 只读检查 `git diff --stat`、文件清单、子模块状态、是否存在 API key、是否混入生成 artifact；用户材料保持未暂存。
- [ ] 该任务结束状态为 `REAL_MODEL_RELEASE_GATE_READY`，不是 benchmark 完成；此处不提交、不推送、不运行真实模型。

## Task 8: 在实现 revision 稳定后重新冻结 Manifest

**Files:**
- Create: `config/benchmark/p1-formal-v1.json` only through the freeze command

- [ ] 在所有代码、测试和 CI 变更稳定后，记录实现 revision：

```powershell
$implementationRevision = (git rev-parse HEAD).Trim()
uv run data-incident-gym benchmark freeze `
  --implementation-revision $implementationRevision `
  --output config/benchmark/p1-formal-v1.json
uv run data-incident-gym benchmark verify `
  --manifest config/benchmark/p1-formal-v1.json `
  --confirm-sha256 (Get-FileHash config/benchmark/p1-formal-v1.json -Algorithm SHA256).Hash.ToLower()
```

- [ ] 验证 Manifest 仍为 106/94/12、预算仍为 8 requests/8 tools/2 output retries/300s，模型仍为 `mimo-v2.5`，settings overrides 为空，scenario/policy/result-input hashes 与当前代码一致。
- [ ] 记录新 SHA-256，并把它作为之后所有 preflight/run/report 命令的唯一确认值；不得手工编辑 JSON 或复用旧 SHA。
- [ ] Manifest 文件的提交和推送是独立授权门；没有授权时只保留工作树文件并报告 hash。

## Task 9: WSL Ubuntu 中执行 Manifest-bound preflight 并停在正式运行门

**Files:**
- No tracked file changes; generated receipt/artifact 仅写入 ignored output

- [ ] 在 WSL2 Ubuntu 原生 ext4 创建全新目录 `/home/makise/worktrees/DataIncidentGym-m12-formal-v1`；目录已存在则停止，不删除、不复用。
- [ ] 从 `origin/master` 获取并 checkout Task 8 的 exact packaging revision，初始化 pinned submodule，执行 `uv sync --frozen`；不要依赖远端默认 `main`。
- [ ] 在该 clone 中确认 clean checkout、数据库健康、目标 suite/run/artifact 不存在、没有并发 DataIncidentGym 进程；重新运行 `benchmark verify` 并确认 SHA。
- [ ] 用户恢复普通按量 key 至 `MIMO_API_KEY` 或 `DIG_DIAGNOSTIC_MODEL_API_KEY`；不得打印、读取、写入日志或 artifact。Manifest 绑定 endpoint/model，不接受命令行覆盖。
- [ ] 执行一次：

```bash
uv run data-incident-gym benchmark preflight \
  --manifest config/benchmark/p1-formal-v1.json \
  --confirm-sha256 "$MANIFEST_SHA256"
```

- [ ] 确认 doctor receipt 为 `PASSED`、ledger 不存在、106 个目标 artifact 仍不存在、任何模型请求计数为零；到此暂停，等待正式真实批次的独立授权。

## Task 10: 获得独立授权后执行唯一一次真实批次

**Files:**
- No code changes; generated ledger/artifacts are ignored

- [ ] 只在 Task 9 receipt 人工确认后执行一次：

```bash
uv run data-incident-gym benchmark run \
  --manifest config/benchmark/p1-formal-v1.json \
  --confirm-sha256 "$MANIFEST_SHA256"
```

- [ ] 106 格顺序执行：94 个 model-backed 格最多各发起一次模型运行，12 个 fixed-rule 格零模型请求；不重试、不提高预算、不替换样本、不改变 Manifest。
- [ ] 模型协议错误、模型 timeout、请求/工具预算耗尽均作为该格正式失败继续记录；不得改代码后在同一批次中补跑。
- [ ] 进程崩溃只允许同一目录、同一 Manifest、同一命令恢复；已终态格跳过，遗留 `STARTED` 格物化失败且不重发请求。
- [ ] 若发现 harness、evaluator、工具隔离、Ground Truth 泄漏、receipt/ledger 身份或 artifact 合同缺陷，立即停止并将全批次标为 `INVALID_HARNESS`；不删除现场、不拼接后续结果、不重新执行。

## Task 11: 终态审计、报告与受限交付

**Files:**
- Generated: `artifacts/benchmarks/p1-formal-v1/summary.json`, `artifacts/benchmarks/p1-formal-v1/report.md`
- Modify after review: `README.md` only if separately authorized

- [ ] 正式执行终态审计：106 格均为 terminal、ledger 212 行、636 个格级文件齐全、无重复/替换/STARTED，数据库恢复健康（payments 113、orders 99、M10 IDs 0、orphans 0）。
- [ ] 切换同一 WSL clone 至预先冻结的 reporter revision，保留 ignored artifact，运行两次 `benchmark report`；第二次必须验证 summary/report 字节完全一致。
- [ ] 报告 hard gates、主/辅助分母、失败原因、恢复状态、环境限制和结论；模型失败不能被描述为 harness 失败，harness 缺陷不能被描述为模型质量。
- [ ] 复制报告到主工作区 ignored `artifacts` 前先检查目标是否存在并比对 hash，不覆盖不同文件；不暂存生成 artifact、key、`AGENTS.md`、`decision.md` 或旧报告。
- [ ] README、正式结果提交和推送仍是独立授权门；最终交付只能使用报告中的有限结论，不把固定样本结果宣传为通用准确率、生产能力或统计显著性。

## 最终验收

- [ ] 需求合同完成一次修订并与实现一致。
- [ ] 本地 fake OpenAI wire contract 通过，且确认无真实 endpoint 请求。
- [ ] runner preflight、receipt hash、空 ledger、artifact 冲突和 no-live-doctor 门禁通过。
- [ ] 38 格六策略确定性门禁通过。
- [ ] 106 格 synthetic orchestration 通过，ledger/artifact/recovery/one-shot 语义通过。
- [ ] reporter 可独立校验并产生字节确定性报告。
- [ ] 全 unit/integration/非真实 E2E、Ruff、lock、diff、build 和 exact-head Ubuntu CI 通过。
- [ ] Manifest 已在最终实现 revision 上重新冻结并验证 hash。
- [ ] M12 停在 `REAL_MODEL_RELEASE_GATE_READY`，除非获得后续独立授权，否则没有真实模型调用、commit 或 push。
