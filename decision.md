# M1 CI 推送决策记录

## 日期

2026-08-24

## 当前进度

M1 Task 1-9 已完成，本地 Windows/PowerShell 与真实 PostgreSQL/dbt 验证通过，Ubuntu CI 尚未验证，M2 未开始。

## 相关提交

- 当前 HEAD：`b69c79b`（`docs: document M1 baseline workflow`）
- 关键修复/阶段提交：
  - `6327a03`（`fix: suppress database password exception chain`）
  - `4fc7029b`（`test: verify healthy PostgreSQL dbt build`）
  - `47d582c`（`test: prove baseline reproducibility`）
  - `b69c79b`（`docs: document M1 baseline workflow`）

## 问题

M1 最终门槛要求 Ubuntu CI 结果，但当前未推送、未验证。

## 候选方案

1. 暂停在 M1。
2. 授权推送并等待 Ubuntu CI。
3. 修改/重新批准计划后在未验证 CI 时继续。

## 最终选择

用户授权推送到指定远程：`https://github.com/Makise0721/DataIncidentGym.git`。

## 选择理由

满足已批准 M1 的跨平台验证门槛；不擅自宣称 CI 通过。

## 决策后的理解/边界

仅推送当前 `master` 并等待 CI；CI 未返回成功前不开始 M2，不强推、不改代码范围。

## 2026-08-25：M1 完成事实与 M2 接口决策

- 用户确认 Ubuntu CI 已通过，这是人工观测事实；M1 完成门槛视为满足。
- M2 保留 `pipeline build` 的健康语义，新增 `lab build` 执行无 seed 的故障构建。
- 预期 dbt 失败通过 Ground Truth 独立验证时，`lab build` exit 0；非预期结果 exit 非零。
- M2 计划限制为最多 6 个 Task，计划路径使用实际仓库的 `docs/superpowers/plans/`。

## 2026-08-25：M2 Task 1 提交边界决策

### 当前进度

M2 Task 1 的 Ground Truth 合同已在当前 HEAD 前完成；本次仅修正实施计划中的治理与提交边界，不改代码和测试。计划修正后只提交非根目录计划文件；`mistake.md` 继续作为工作区决策记录保留未提交。

### 相关提交

- `ffdf28f`：`docs: define M2 incident contract`
- `ea03b331`：`test: harden M2 ground truth contract`
- 本次计划修正提交：待填（实际 hash 在交付结果中提供；本记录不回填到提交中）

### 问题

M2 Task 1 原计划要求更新 `mistake.md`，同时把它列入 `git add` 和提交预期；这与用户明确要求根目录 Markdown 暂不提交、暂不推送的规则冲突，也可能把既有 `AGENT.md` 风险带入提交边界。

### 候选方案

- A：继续更新 `mistake.md` 作为工作区决策记录，但将其排除在 `git add`、提交和推送之外；本次提交只包含非根目录文件。
- B：暂不更新 `mistake.md`，只修正计划并提交非根目录文件，待根目录 Markdown 解禁后补记。
- C：按原 Task 1 计划把 `mistake.md` 一并暂存并提交。

### 用户最终选择

用户最终选择方案 A。

### 为什么选择 A

既满足 AGENT.md 要求的决策记录必须及时追加，又严格遵守根目录 Markdown 暂不提交、暂不推送的明确规则；同时不改变 M2 Task 1 的代码、测试或后续 Task 方向。

### 决策后的理解和执行边界

`mistake.md` 必须更新但只能保持工作区未暂存、未提交；`AGENT.md` 必须保留现有修改且不得编辑。此次仅修正 `docs/superpowers/plans/2026-08-25-m2-incident-lab.md`，实际提交只允许包含该非根目录计划文件，不得使用 `git add .`，不得暂存、提交或推送任何根目录 Markdown；不扩大到 Task 2 及以后，也不改变代码和测试方向。

## 2026-08-25：M2 Task 3 严格 Schema 合同方案 A

### 当前进度

当前 HEAD 为 `2b1c41d`；M2 Task 3 审查阻塞已定位为 IncidentLab 只比较列名和行数，未比较固定 Ground Truth 的列类型、可空性和序号。本次修复已完成实现与单元测试，提交尚未创建。

### 相关提交

- 基线提交：`2b1c41d`（`feat: add guarded incident reset and injection`）
- 本次修复提交：待填（实际 hash 在交付结果中提供）

### 问题

Schema 漂移若只改变 `data_type`、`nullable` 或 `ordinal_position`，旧状态机仍可能判定为 `HEALTHY`/`INJECTED`；同时查询、改名、BaselineError 和后置校验路径必须保证错误消息及异常链不泄漏 PostgreSQL 密码。

### 候选方案

- A：在固定 Ground Truth 中记录健康/故障列的名称、`data_type`、`nullable`、`ordinal_position`，状态比较全量元数据和行数，并对未知漂移 fail closed；补齐各异常路径的消息、`__cause__`、`__context__` 脱敏测试。
- B：继续只比较列名和行数，依赖后续独立验证器发现元数据漂移。
- C：运行时从当前数据库推断预期元数据，不把类型、可空性和序号写入固定 Ground Truth。

### 用户最终选择

用户选择方案 A（严格 Schema 合同）。

### 为什么选择 A

`.dig/baseline-summary.json` 的 M1 真实 `analytics.raw_payments` 摘要已验证：四列类型依次为 `integer/integer/text/integer`，均可空，序号为 1–4，行数为 113；固定改名只改变最后一列名称。因此可以在不猜测、不扩大安全边界的前提下锁定全部可验证元数据。

### 决策后的理解和执行边界

只修改本次 Task 允许的配置、实现、测试及必要文档；保留固定 case allowlist、Identifier SQL、事务、reset/inject 语义，不修改 M3+、依赖、submodule 或真实安全边界。`AGENT.md` 不编辑；本记录继续追加但必须保持未暂存、未提交、未推送。

## 2026-08-25：Ground Truth 原始 JSON 类型严格拒绝决策

### 当前进度

M2 Task 3 的严格 Schema 比较已在 `92f71ac` 实现，相关边界测试已在 `b441281` 补齐。旧的 `2b1c41d` 仅是历史 checkpoint，不代表当前进度；当前 HEAD 已推进到 `b441281` 之后的本次修复提交 `77f6cb1`。本次已确认并修复 `nullable=1`、`ordinal_position="1"` 被 Pydantic coercion 后接受的问题，并补充 `row_count="113"`、错误类型的 `data_type/name` 拒绝测试。

### 相关提交

- `2b1c41d`：历史 checkpoint，`feat: add guarded incident reset and injection`。
- `92f71ac`：`fix: enforce strict incident schema state`，实现严格 Schema 合同比较。
- `b441281`：`test: cover strict incident drift boundaries`，补充严格漂移边界测试。
- `77f6cb1`：`fix: reject coerced Ground Truth types`，本次严格原始 JSON 类型修复。

### 问题

`ExpectedColumn` 的普通 `bool/int` 字段会把 JSON 原始值 `1` 和 `"1"` 转换后再参与固定合同校验，导致不符合 Ground Truth 原始类型的输入仍可能被接受。`row_count` 的固定 `Literal[113]` 已拒绝 `"113"`，错误类型的 `data_type/name` 也应继续明确拒绝。

### 候选方案

- A：仅将 `ExpectedColumn` 的 `name`、`data_type`、`nullable`、`ordinal_position` 改为 `StrictStr`、`StrictBool`、`StrictInt`，保留 `row_count: Literal[113]`、其它固定 Literal、`extra="forbid"` 和现有 contract 校验，并增加原始 JSON 类型回归测试。
- B：增加覆盖整个 Ground Truth 的自定义 `mode="before"` 原始 JSON 类型递归校验，或把所有字段改造成更广泛的严格别名，扩大校验改动面。

### 用户最终选择

用户最终选择方案 A。

### 为什么选择 A

A 直接修复已定位的 coercion 根因，变更最小，能够覆盖本次确认的字段，同时不改变已批准的真实 Ground Truth 值、固定 Literal 合同、额外字段拒绝规则或 IncidentLab 状态语义。B 的递归校验范围更广，存在不必要地改变既有合同和后续方向的风险。

### 决策后的执行边界

只允许修改并提交 `src/data_incident_gym/incidents.py`、`tests/unit/test_incidents.py`；本次提交为 `77f6cb1`。`mistake.md` 仅作为工作区决策记录追加，保持未暂存、未提交、未推送；`AGENT.md` 不修改。不得改变配置中的真实 Ground Truth 值、IncidentLab 状态语义、依赖、submodule 或 M3+ 方向，不得 push。

## 2026-08-25：M2 Task 3 严格 Ground Truth row_count checkpoint

### 当前进度

M2 Task 3 严格 Ground Truth 的最后一个局部缺口已修复并提交。`row_count` 现在只接受原生 JSON/Python `int` 且值为 `113`；`113.0`、`True` 和 `"113"` 均被拒绝。focused incidents/lab tests、全部 unit、Ruff、`uv lock --check` 和 `git diff --check` 均已通过。

### 相关提交

- `92f71ac`：`fix: enforce strict incident schema state`，实现严格 Schema 合同比较。
- `b441281`：`test: cover strict incident drift boundaries`，补充严格漂移边界测试。
- `77f6cb1`：`fix: reject coerced Ground Truth types`，修复列元数据字段的 coercion。
- `52d43d6`：`docs: align incident plan with strict types`，同步严格类型方案示例。
- `f4fbb25`：`fix: reject non-integer Ground Truth counts`，补齐 `row_count` 原始类型检查及回归测试。

### 问题

`row_count: Literal[113]` 仍可能把 JSON 数字 `113.0` 按 Pydantic 的类型/字面量解析语义接受为 `int`，导致严格原始 JSON 类型合同存在局部缺口。`True` 和 `"113"` 也必须明确保持拒绝。

### 修复方式

在 `ExpectedSchema.row_count` 上增加最小 `mode="before"` field validator，使用 `type(value) is int and value == 113` 检查，再交由原有 `Literal[113]`、`extra="forbid"` 和固定 contract 校验继续处理；新增 `113.0`、`True` 回归覆盖。未改变 Ground Truth 真实值、Lab 状态机或架构。

### 执行边界

本次只修改并提交 `src/data_incident_gym/incidents.py`、`tests/unit/test_incidents.py`。`mistake.md` 仅追加本 checkpoint，保持未暂存、未提交、未推送；`AGENT.md` 的既有修改保持不动。不得修改计划文件、third_party、依赖或其他架构方向，不 push，不使用 `git add .`。

## 2026-08-25：M2 Task 4 dbt 运行产物校验兼容性决策

### 当前进度

M2 Task 4 运行产物采集与校验已完成。最新代码提交为 `3b7959c`；之前相关提交包括 `377065a`、`deddc4a`、`089d9e8`、`20f73e3`。最近一次全量 unit 为 `112 passed`。真实 PostgreSQL、Docker/dbt、CI、集成/E2E、10 次复现仍未验证。

### 提交

- `3b7959c`：`fix: reject duplicate incident lineage references`。
- 之前相关提交：`377065a`（`feat: capture and verify incident artifacts`）、`deddc4a`（`fix: harden incident artifact verification`）、`089d9e8`（`fix: reject non-strict incident schema types`）、`20f73e3`（`fix: make incident artifact parsing fail closed`）。
- 本次方向决策本身只改工作区根目录 `mistake.md`；不修改源码或测试，不改 `AGENT.md`，不执行 `git add`、`commit` 或 `push`。

### 问题

dbt 原生 `manifest.json`/`run_results.json` 可能包含版本相关的未知扩展字段，当前校验是否应全面拒绝。

### 候选方案

- A（用户选择）：兼容优先，只校验本项目实际使用的字段、结构、重复键和关键关系，允许额外字段。
- B：严格白名单，固定支持的 dbt 版本，完整列出字段并拒绝未知字段。

### 为什么最终选择 A

当前计划没有固定单一 dbt 版本。选择 A 可保留版本兼容性，避免合法扩展字段导致误拒绝；同时继续对关键验收字段和结构做 fail-closed 校验。

### 结果/边界

A 不代表忽略必需字段、类型、重复键、路径、digest、schema、lineage、失败节点、退出码和日志校验。

## 2026-08-25：M3 Task 1 EvidenceRecord 合同提交

### 当前进度

M3 Task 1 已完成并提交。实现范围仅为四种 frozen、extra=forbid Evidence content 模型、EvidenceRecord 确定性 digest/ID 校验、类型化错误层级及其单元测试；未实现 EvidenceTools、数据库、诊断配置、CLI 或 M4 Agent。

### 实际验证命令

- `uv run pytest tests/unit/test_evidence.py -q`（RED collection，exit 1；`data_incident_gym.evidence` 尚不存在）
- `uv run pytest tests/unit/test_evidence.py -q`（18 passed，exit 0）
- `uv run ruff check src/data_incident_gym/evidence.py tests/unit/test_evidence.py`（exit 0）
- `git diff --check -- src/data_incident_gym/evidence.py tests/unit/test_evidence.py docs/superpowers/plans/2026-08-25-m3-evidence-tools.md`（exit 0）
- `uv run pytest tests/unit -q`（144 passed，exit 0）
- `git diff --cached --check`（exit 0）

### 审查前状态与提交边界

审查前保留用户已有 `AGENT.md`、`README.md`、`mistake.md` 工作区修改；显式暂存清单精确为 `docs/superpowers/plans/2026-08-25-m3-evidence-tools.md`、`src/data_incident_gym/evidence.py`、`tests/unit/test_evidence.py`，未暂存根目录 Markdown，未修改第三方子模块，未 push。

### 相关提交

- `18e7582`（`feat: define M3 evidence contract`）

提交后本记录继续保持 workspace-only，未暂存、未提交。

## 2026-08-25：M3 Task 2 固定 dbt evidence 工具提交

### 当前进度

M3 Task 2 已完成并提交 `849b831`（`feat: read dbt run evidence`）。新增独立 `DiagnosticSettings`、固定 run artifact reader、`get_dbt_run_results` 和 `get_dbt_node_error`；未实现 lineage、relation schema、数据库连接、只读角色、CLI 或 M4。Task 1 的 `evidence.py` 与测试无需修改。

### 实际验证命令与 exit code

- `uv run pytest tests/unit/test_diagnostic_config.py tests/unit/test_evidence_tools.py -q`（TDD RED collection，exit 1；两个模块不存在）
- `uv run pytest tests/unit/test_diagnostic_config.py tests/unit/test_evidence.py tests/unit/test_evidence_tools.py -q`（首次 GREEN 因 Windows symlink 权限 WinError 1314，exit 1；改为在公开 seam 模拟 resolved path escape 后重跑 45 passed，exit 0）
- `uv run pytest tests/unit/test_diagnostic_config.py tests/unit/test_evidence.py tests/unit/test_evidence_tools.py tests/unit/test_lab_verifier.py -q`（72 passed，exit 0）
- `uv run pytest tests/unit -q`（171 passed，exit 0）
- `uv run ruff check src tests`（exit 0；初次实现曾有 4 个 E501，已最小修正后通过）
- `git diff --check -- .gitignore .env.diagnostic.example src/data_incident_gym/diagnostic_config.py src/data_incident_gym/evidence.py src/data_incident_gym/evidence_tools.py tests/unit/test_diagnostic_config.py tests/unit/test_evidence.py tests/unit/test_evidence_tools.py`（exit 0）
- `git diff --cached --check`（exit 0）

### 审查前状态与提交边界

提交前保留既有 `AGENT.md`、`README.md`、`mistake.md` 工作区修改；显式暂存清单为 `.gitignore`、`.env.diagnostic.example`、`src/data_incident_gym/diagnostic_config.py`、`src/data_incident_gym/evidence_tools.py`、`tests/unit/test_diagnostic_config.py`、`tests/unit/test_evidence_tools.py`。缓存清单不含根目录 Markdown、实际 `.env.diagnostic` 或 `third_party`；未 push。`mistake.md` 本次追加后继续保持未暂存、未提交。

## 2026-08-25：M3 Task 3 固定 manifest 双向 lineage 提交

### 实现与边界

在 `EvidenceTools.get_dbt_lineage(node_id, direction)` 中固定读取当前 run 的 manifest，先对所选方向的可达 adjacency 做三色校验，再用 BFS 输出传递闭包；严格拒绝非法 direction、未知节点、重复/悬空引用和可达环（包含 test 节点形成的环），过滤 test，保留 model/seed/source，按 `(distance, node_id)` 排序。`observed_at` 使用 manifest `metadata.generated_at`，source 为 `dbt_artifact:manifest.json`。只修改 `src/data_incident_gym/evidence_tools.py` 与 `tests/unit/test_evidence_tools.py`；未修改 AGENT.md、README.md、第三方子模块，未 push。

### 实际验证命令与 exit code

- `uv run pytest tests/unit/test_evidence_tools.py -q -k lineage`（计划原始 RED，10 deselected，exit 1）
- `uv run pytest tests/unit/test_evidence_tools.py -q -k lineage`（新增测试后的 RED，11 failed、10 deselected，exit 1；方法尚未实现）
- `uv run pytest tests/unit/test_evidence_tools.py -q -k lineage`（GREEN，11 passed、10 deselected，exit 0）
- `uv run pytest tests/unit/test_evidence_tools.py -q -k 'not lineage'`（Task 2 focused，10 passed、11 deselected，exit 0）
- `uv run pytest tests/unit -q`（182 passed，exit 0）
- `uv run ruff check .`（exit 0）
- `git diff --check -- src/data_incident_gym/evidence_tools.py tests/unit/test_evidence_tools.py`（exit 0）
- `git add -- src/data_incident_gym/evidence_tools.py tests/unit/test_evidence_tools.py`（exit 0；缓存清单仅含这两个文件）
- `git commit -m "feat: expose dbt lineage evidence"`（exit 0）

### 提交与工作区

- `04f1dfb`：`feat: expose dbt lineage evidence`。
- 提交后本记录继续保持 workspace-only、未暂存、未提交；根目录 `AGENT.md`、`README.md`、`mistake.md` 的用户已有修改均保留。
- 未实现关系 Schema、实时数据库取证、M4 Agent、CLI 或其他 M3/M4 范围；这些不是本 Task 的边界。

## 2026-08-25：M3 Task 4 只读角色与实时 Schema 提交

### 当前进度

M3 Task 4 已完成并提交 `b75ec8f`（`feat: enforce read-only schema evidence`）。新增管理平面 `ReadOnlyRoleProvisioner`，健康 dbt artifact 校验成功后才 provision reader，再 inspect/write summary；`EvidenceTools.get_relation_schema` 使用诊断侧 `dig_reader` 的显式只读事务和固定参数化 catalog 查询，并执行 run snapshot 漂移门禁。未实现 Task 5 真实集成、M4 Agent、模型、CLI 或其他越界能力。

### 实际命令与 exit code

- `uv run pytest tests/unit/test_read_only_db.py tests/unit/test_baseline.py tests/unit/test_evidence_tools.py -q`（计划 RED，exit 1；文件不存在）
- `uv run pytest tests/unit/test_read_only_db.py tests/unit/test_baseline.py tests/unit/test_evidence_tools.py -q`（测试 RED，exit 1；`read_only_db` 尚不存在）
- `uv run pytest tests/unit/test_read_only_db.py tests/unit/test_baseline.py tests/unit/test_evidence_tools.py -q`（GREEN，64 passed，exit 0）
- `uv run pytest tests/unit/test_read_only_db.py tests/unit/test_baseline.py tests/unit/test_lab.py tests/unit/test_evidence_tools.py -q`（91 passed，exit 0）
- `uv run pytest tests/unit/test_evidence.py tests/unit/test_diagnostic_config.py tests/unit/test_lab_verifier.py tests/unit/test_dbt_runner.py -q`（67 passed，exit 0）
- `uv run pytest tests/unit -q`（192 passed，exit 0）
- `uv run ruff check .`（exit 0）
- `uv lock --check`（exit 0）
- `git diff --check -- src/data_incident_gym/read_only_db.py src/data_incident_gym/baseline.py src/data_incident_gym/evidence_tools.py tests/unit/test_read_only_db.py tests/unit/test_baseline.py tests/unit/test_evidence_tools.py`（exit 0）
- `git add -- src/data_incident_gym/read_only_db.py src/data_incident_gym/baseline.py src/data_incident_gym/evidence_tools.py tests/unit/test_read_only_db.py tests/unit/test_baseline.py tests/unit/test_evidence_tools.py`（exit 0；缓存清单精确为这 6 个文件）
- `git diff --cached --check`（exit 0）
- `git commit -m "feat: enforce read-only schema evidence"`（exit 0）

### 问题与边界

- 计划 RED 的首次运行因 `tests/unit/test_read_only_db.py` 不存在而未收集测试；随后按 TDD 先写测试后实现，未使用 skip/xfail。
- 仅使用合成哨兵 `TEST_REDACTED_VALUE`；未读取或输出机器真实凭据。
- 保留现有根目录 `AGENT.md`、`README.md`、`mistake.md` 修改；未修改 CLI、第三方子模块、依赖或 `uv.lock`，未 push。
- 停止规则未触发；Task 4 未通过真实 PostgreSQL 集成验证，留给 Task 5。

### 提交与工作区

- `b75ec8f`：`feat: enforce read-only schema evidence`。
- 提交后本记录继续保持 workspace-only、未暂存、未提交；cached list 不含根目录 Markdown、实际环境文件或 `third_party`。

## 2026-08-25：M3 Task 5 四工具真实集成与权限负面证明

### 实现与边界

新增 `tests/integration/test_evidence_tools.py`。module-scoped fixture 只执行一次真实 `reset → inject → build`，构造 `EvidenceTools.for_run(run_id, DiagnosticSettings(_env_file=None), PROJECT_ROOT)`，并在 `finally` 执行 `reset`。四个测试分别调用 run results、node error、relation schema 和 lineage；权限测试直接连接 `dig_reader`，不通过工具执行写 SQL。

真实证据精确符合计划：失败节点为 `model.jaffle_shop.stg_payments`，跳过节点包含 `orders`/`customers`；错误保留 `column "amount" does not exist` 且不含绝对路径或 `compiled code at`；`raw_payments` 有 `total_amount` 且无 `amount`；下游 model 为 `orders`/`customers`，上游包含 `seed.jaffle_shop.raw_payments`；重复 run-results/node-error 读取的 `EvidenceRecord` ID/digest 稳定。reader 的 `transaction_read_only` 为 `on`，catalog SELECT 成功，CREATE 被拒绝；管理连接证明 reader 的 superuser/createdb/createrole/replication/bypassrls 均为 false，回滚后 forbidden table 不存在。

### 实际验证命令与 exit code

- `uv run ruff check tests/integration/test_evidence_tools.py`（exit 0）
- `uv run pytest tests/integration/test_evidence_tools.py -q -s`（5 passed，exit 0；最终收紧路径断言后复跑 5 passed）
- `uv run data-incident-gym pipeline build`（exit 0；8 relations，fingerprint `e5c7848eb2b7af16ec37463650b63dbb5d1f52293adf456003517b51c496cb18`）
- `uv run data-incident-gym lab reset schema_rename_payment_amount`（exit 0；state `HEALTHY`，同一 fingerprint）
- `uv run ruff check .`（exit 0）
- `uv run pytest tests/unit -q`（192 passed，exit 0）
- `uv run pytest tests/integration -q`（7 passed，exit 0）
- `uv run pytest tests/e2e -q`（2 passed in 211.44s，exit 0；包含十次真实循环）
- `uv lock --check`（exit 0）
- `git -C third_party/jaffle_shop rev-parse HEAD`（`36bde6cba69d962b83be1d52fc65a0dce1cb4ebb`）
- `git -C third_party/jaffle_shop status --short`（无输出）
- `git diff --check`（exit 0；仅既有 Windows 换行提示）

### 文档、审查与提交边界

README 当前状态已更新为 M3 四个只读证据工具已实现、M4/M5 尚未实现，并增加 Python API 示例；README、AGENT.md、mistake.md 均保持根目录 workspace-only。未修改 CI、源代码、CLI、依赖、uv.lock、Ground Truth、Docker volume 或 third_party 子模块。

自审确认 fixture 生命周期、四工具独立调用、reader 负面证明、恢复和脱敏断言均在测试中可见；停止规则未触发。显式缓存清单仅为 `tests/integration/test_evidence_tools.py`，未 push。

### 相关提交

- `ffd56a1`（`test: verify M3 evidence tools`）
- 提交后仍只保留根目录 `AGENT.md`、`README.md`、`mistake.md` 的未暂存工作区修改。

## 2026-08-25：M4 Task 1 Diagnosis/config RED

### 实际命令与结果

- `uv run pytest tests/unit/test_diagnosis.py tests/unit/test_diagnostic_config.py -q`（RED collection，`data_incident_gym.diagnosis` 尚不存在，exit 2）

### 工作区边界

本记录仅为 workspace-only 追加，保持未暂存、未提交；只使用合成测试值 `TEST_REDACTED_VALUE`，未读取或输出真实凭据。M4 Task 1 继续按计划执行依赖锁定与最小合同实现。

## 2026-08-25：M4 Task 1 `logfire-api` 传递依赖边界决策

### 当前进度

M4 Task 1 已实施并完成第一次独立审查，等待修复后的复审。当前 HEAD/相关提交为 `c6f893c feat: define M4 diagnosis contract`、`b2482ff fix: redact diagnostic settings validation inputs`。

### 问题

`pydantic-graph==2.34.0` 声明 `logfire-api>=3.14.1`，当前 uv 解析结果为 `logfire-api==4.41.0`。这不是代码启用 Logfire，也不等于安装完整 `logfire` 可观测套件或开启遥测，只是正常传递依赖。

### 候选方案

- A：接受该传递依赖并保持计划指定 PydanticAI 版本。
- B：严格禁止 lock 中出现 `logfire-api`，暂停并调查依赖方案。
- C：暂缓 M4 PydanticAI 方案，重新设计依赖。

### 用户最终选择

选择 A。

### 选择原因

该包由已批准的 `pydantic-ai-slim==2.34.0` 依赖链正常传递引入；代码未导入/使用 Logfire、未开启遥测。接受该传递依赖可以保持已批准版本和 OpenAI-compatible Agent 架构，避免无必要的依赖或方向变更。

### 决策后的理解和执行边界

继续 M4；将“无 Logfire”解释为不直接依赖/使用/启用完整 Logfire 能力，而不是禁止该正常传递包。继续对代码、静态审计和测试保持不使用、不泄露、不配置遥测；不修改 pydantic-ai 版本。

### 实际记录动作与文件状态

本次仅向根目录 `mistake.md` 追加本条合成文本记录，未修改代码、计划、`AGENT.md`、`README.md` 或 `third_party/jaffle_shop`，未暂存、未提交、未推送任何文件。当前 `git status --short` 为：`AGENT.md`、`README.md`、`mistake.md` 均已有未暂存修改；本次未声称远程 CI、Ollama 或 Task 2 已完成。

## 2026-08-25：M4 Task 2 active-run reset 顺序决策

### 当前进度

M4 Task 1 已完成并复审通过；M4 Task 2 实施提交 `1617cf6 feat: bind diagnosis agent to verified runs` 已完成，但独立审查未通过，等待 reset 顺序修复和补充测试。此前审查基线为 `b2482ff`。

### 问题

`src/data_incident_gym/lab.py` 的 reset 当前为“恢复/验证健康后清理 active pointer”；若恢复失败，旧 pointer 可能继续存在并被误用。

### 候选方案

- A：保持计划原顺序，恢复/验证健康后清理 pointer。
- B：先清理 active pointer，再执行 reset 的健康恢复/验证。
- C：reset 前清理并在结束时再次清理。

### 用户最终选择

用户最终选择 B。

### 为什么最终选择

reset 失败时也必须 fail closed，不能留下可能指向旧 run 的 active pointer，避免后续诊断误用旧证据；同时保持改动最小。

### 决策后的理解和执行边界

reset 开始的状态变更前先清除固定 active pointer/temp；然后执行原有健康恢复和验证。不删除 run、Ground Truth、Docker volume，不扫描最新目录，不改变 M2 Ground Truth 合同；只补对应测试和必要审查证据，未进入 Task 3。

本用户选择来自当前对话。本条仅追加到 workspace-only 的根目录 `mistake.md`，使用合成文本，不读取或输出真实凭据、环境值或敏感日志。

## 2026-08-25：M4 Task4 Ollama 探针三次停止证据

### 固定配置

- HEAD：`2279b4464df69f2ec988ec0c988948cd6079ad95`
- 命令：`uv run pytest tests/e2e/test_ollama_diagnosis.py -q -s`
- opt-in：`DIG_RUN_OLLAMA_TESTS=1`；每次 finally 清理该环境变量。
- provider：`openai-compatible`；base URL：`http://127.0.0.1:11434/v1`；model：`gemma4:e4b`。
- Ollama client：`0.20.7`；本地 `/api/version` 探针不可用。未读取或输出凭据、token、secret 或原始 provider 异常。

### 三次脱敏结果

1. 第一次：probe exit `1`；稳定公开终态码 `MODEL_ERROR`；`model_requests=0`、`tool_call_attempts=0`、`successful_tool_calls=0`；安全 trace 无工具调用、无异常原文、无敏感值；finally reset exit `0`，状态 `HEALTHY`。
2. 第二次：probe exit `1`；同一 `MODEL_ERROR` 和相同零请求/零工具调用摘要；安全 trace 无工具调用、无异常原文、无敏感值；finally reset exit `0`，状态 `HEALTHY`。
3. 第三次：probe exit `1`；同一 `MODEL_ERROR` 和相同零请求/零工具调用摘要；安全 trace 无工具调用、无异常原文、无敏感值；finally reset exit `0`，状态 `HEALTHY`。

### 停止决定

同一 HEAD、同一配置、同一命令连续三次失败，达到停止规则。未切换模型、未增加 JSON repair/regex、未增加模型专属循环、未放宽权限；Task4 半成品不提交，等待用户决定如何恢复本地 Ollama 服务或是否授权后续复现。

## 2026-08-26：M4 Task4 停止规则后的 Ollama 恢复决策

### 当前进度

M4 Task1-3 已完成并通过独立复审。Task4 的 CLI、FunctionModel integration、真实 M3 integration 和测试框架已实现但尚未提交。`gemma4:e4b` 探针此前在同一 HEAD、配置和命令下连续 3 次失败，已触发停止规则。

### 相关提交

当前已提交基线为 `2279b44`；Task4 改动尚未提交。

### 问题

此前 Ollama 客户端可见，但服务端 `/api/version` 不可用。三次探针均为 `MODEL_ERROR`，均为 `0` 模型请求、`0` 工具调用；每次 `finally reset` 均恢复 `HEALTHY`。本记录只保留脱敏合成证据，不读取或输出真实凭据、token 或敏感日志。

### 候选方案

- A：恢复/启动 Ollama 服务后，用相同模型、配置和命令重新探针。
- B：接受真实 `gemma4:e4b` 未验证，提交本地 Task4 但不把 M4 标为正式完成。
- C：暂缓 Task4，保留未提交改动。

### 用户最终选择

用户最终选择 A，并确认 Ollama 已启动。

### 为什么最终选择

先恢复计划要求的本地 Ollama 服务，再用原始模型和原始命令验证，保留可比性；不切换模型、不添加修补逻辑。

### 决策后的执行边界

重新检查服务，然后仅按原命令重跑 `gemma4:e4b`；失败继续保留脱敏证据；不切换模型、不加 regex/JSON repair/模型专属循环、不放宽权限。成功后再做 Task4 余下回归、显式提交和独立审查。根目录 Markdown 始终 workspace-only。本条不声称探针已经成功。

## 2026-08-26：M4 Task4 Docker 阻塞决策记录

当前 HEAD 为 `2279b44`。M4 Task1-3 已完成并审查通过；Task4 的 CLI、FunctionModel、M3 integration 与 e2e harness 已实现但未提交。Ollama 已恢复可用。此前重试因 Docker PostgreSQL named pipe 不存在而失败，`lab reset` exit 1，模型未请求。

候选方案：

- A：用户启动 Docker 后重新验证 `reset HEALTHY`，并重跑原始 `gemma4` 探针。
- B：授权代理启动或检查 Docker。
- C：接受真实模型未验证，并提交本地 Task4，但不正式完成 M4。

用户最终选择 A，并确认 Docker Desktop 已启动。理由是恢复真实 PostgreSQL/dbt 依赖后保持原模型、URL、命令可比，不切换模型、不加修补。

执行边界：先检查 Docker、执行 `lab reset`、运行原始探针；成功后再进行 Task4 回归、提交与复审；失败则保留脱敏证据并暂停。根目录 Markdown 为 workspace-only。本记录不声称 `reset` 或探针已经成功；仅使用合成文字，不读取真实凭据。

## 2026-08-26：M4 Task4 本地产物链路诊断（manifest 报错未复现）

### 实际命令与证据

- 受控真实 Docker 链路：PowerShell here-string 脚本经 `uv run python -` 执行 `reset → inject → build`，捕获 run 目录文件存在性，随后 finally 执行 `reset`。
- 受控 build exit：`1`；FaultRun：`True`；run_id：`e6d42d4dbb674f90941f98e06442d129`。
- 该 run 的 `metadata.json`、`dbt/target/manifest.json`、`dbt/target/run_results.json`、`dbt/logs/dbt.log`、`schema.json`、stdout/stderr 均存在；metadata run_id 匹配，dbt exit code 为 `1`。
- 受控链路开始和结束状态分别为 `HEALTHY` / `HEALTHY`，`PROJECT_ROOT` 与工作目录解析路径一致。
- 原始真实探针命令：`$env:DIG_RUN_OLLAMA_TESTS='1'; uv run pytest tests/e2e/test_ollama_diagnosis.py -q -s`；probe exit：`1`。
- 探针实际失败位置为 e2e 的 `lab.reset()` 健康 dbt build，稳定错误码为 `3221225477`（`0xC0000005`），模型请求数为 `0`；未进入 Ollama、未产生模型 metrics/trace。第一次 cleanup reset 的 dbt seed exit 为 `1`（`raw_orders` 出现 `<frozen _collections_abc>:894: unknown opcode 224`），第二次 cleanup reset exit `0`，状态 `HEALTHY`。

### 诊断结论与边界

本轮未复现“无法读取验证产物 `manifest.json`”：受控真实 build 已生成并由 `lab.build()` verifier 接受 manifest 及其余固定产物。因此没有证据支持 Task4 的 `project_root`、artifact 相对路径或 `reset → inject → build` 顺序是根因，也没有进行代码修复、verifier/reader 权限放宽、Ground Truth 修正或 M2 语义修改。

真实 gemma4:e4b 探针在 Ollama 请求前因 Windows/dbt 运行时异常停止；按停止边界不重跑探针、不切换模型、不添加 repair。Task4 改动仍未提交；本条为 workspace-only 追加，未暂存。

## 2026-08-26：M4 Task5 本地总门槛与 workspace-only 状态

### 基线与边界

- 当前 `HEAD` 为 `534dbbf`（`test: harden M4 diagnosis assertions`）。Task4 相关提交为 `d6dd111`（`test: verify M4 diagnosis agent`）和 `534dbbf`。
- 本阶段只允许更新根目录 `README.md`、`mistake.md`；两者均保持 workspace-only，不暂存、不提交、不推送。未修改 `AGENT.md`、源代码、测试、依赖、CI、requirements 或 `third_party/jaffle_shop`。

### 两轮独立审查结论

- 第一轮（`d6dd111` 后）：未通过。发现 e2e 断言允许 `MODEL_ERROR`、integration callback 的根因值硬编码、缺少 180 秒时限断言；审查代理未修改文件、未调用 Ollama。
- 第二轮（`534dbbf` 后）：PASS。确认两项测试强化已阻断 `MODEL_ERROR` 假通过、改为从 schema EvidenceRecord 推导 root code 并覆盖 case/run scope 与 180 秒时限；未发现新的阻断或局部问题。确认生产 Controller 仍只有四个只读工具，且提交未包含根 Markdown、`.dig` 或 third-party。

### 本阶段实际命令与结果

- `DIG_RUN_OLLAMA_TESTS` 进程环境变量检查：`<unset>`；未设置 opt-in。
- `uv run ruff check .`：exit `0`，`All checks passed!`。
- `uv run pytest tests/unit -q`：exit `0`，`263 passed, 3 skipped`。
- `uv run pytest tests/integration -q`：exit `1`，`6 passed, 2 failed`。失败均发生在真实集成的健康 reset/seed 运行阶段，属于 Windows/dbt 运行时环境失败；未修改代码，按停止规则终止后续门槛。
- 因上述非文档失败，以下总门槛命令未执行：`uv run pytest tests/e2e -q`、`uv lock --check`、`uv run data-incident-gym --help`、`uv run data-incident-gym diagnose --help`。文档更新后单独执行 `git diff --check`，exit `0`；最终 status 与 submodule 核对也已执行。未将未执行结果写成通过。
- 真实 Ollama：本阶段 skip，未设置 `DIG_RUN_OLLAMA_TESTS=1`，未发起真实模型请求。前置 Task4 已有一次真实 `gemma4:e4b` 探针成功记录，但本阶段未重复，因此不把它扩展为重复稳定性或 CI 结果。

### 静态审计与状态

- `diagnosis.py`、`run_context.py`、`diagnostic_agent.py` 未导入或调用 `GroundTruth`、`load_ground_truth`、`IncidentVerifier`、`lab_verifier` 或 `Settings`。
- 生产 `diagnostic_agent.py` 只注册 `get_dbt_run_results`、`get_dbt_node_error`、`get_relation_schema`、`get_dbt_lineage` 四个 allowlist 工具；未发现 shell、filesystem、HTTP、自由 SQL、写入或 repair 执行工具。SQL 相关命中仅为 trace 脱敏正则和测试中的禁止性断言/合成哨兵。
- M4 模块与 CLI 未发现 M5 evaluator/report/artifact writer、`eval run` 或 P1 hypothesis/EvidenceGap/claim-evidence/Skill baseline/ablation 实现标记；M5/P1 保持未实现。
- `d6dd111..534dbbf` 的提交路径只有 `tests/e2e/test_ollama_diagnosis.py` 和 `tests/integration/test_diagnostic_agent.py`；root Markdown 与 `third_party/jaffle_shop` 未进入代码提交。
- 当前工作区状态为仅 `AGENT.md`、`README.md`、`mistake.md` 未暂存；暂存区为空。submodule HEAD 为 `36bde6cba69d962b83be1d52fc65a0dce1cb4ebb`，status 无输出。

## 2026-08-26：用户重启 Docker 后重试 M4 Task5 本地验收

### 当前进度与实际命令

- 当前 `HEAD` 为 `534dbbf`；保留既有提交和用户改动。重启后的 `docker compose -f compose.yaml ps` 已执行，但未列出运行中的 Compose 容器。
- `uv run data-incident-gym lab reset schema_rename_payment_amount`：exit `0`，状态 `HEALTHY`。
- 按要求仅执行一次原命令 `uv run pytest tests/integration -q`：exit `1`，`4 passed, 4 errors`。

### 问题与脱敏证据

- 四个 integration 错误均发生在真实 fixture 的健康 baseline 固定 seeds 阶段；首个 seed 成功后，后续 seed 阶段以脱敏错误码 `3221225477` 失败。
- 按停止规则，未继续执行 unit、e2e、ruff、lock、help 命令或静态门槛；未设置 `DIG_RUN_OLLAMA_TESTS=1`，未调用真实 Ollama。

### 采用原因与执行边界

- 采用“用户重启 Docker 后重试”是为了恢复真实 PostgreSQL/dbt 依赖并保持与原验收命令可比；不切换模型、不猜测修复、不重复 integration。
- 本轮未修改源代码、测试、依赖、CI、requirements、`README.md` 或 `third_party/jaffle_shop`；仅追加本条 workspace-only 记录。未提交、未 push。

## 2026-08-26：M4 Task5 方案 A Windows/Python/dbt/Docker 只读运行环境诊断

### 当前进度与提交边界

- 当前进度：M4 Task5 进入 Windows/Python/dbt/Docker 只读运行环境诊断；未声称已找到根因。
- 当前 HEAD：`534dbbf`（`test: harden M4 diagnosis assertions`），父提交为 `d6dd111`。
- 根仓库现有未暂存修改为 `AGENT.md`、`README.md`、`mistake.md`；本节只追加到根目录 `mistake.md`，不暂存、不提交、不 push。
- `third_party` 子模块路径无根仓库变更，子模块工作区 clean，子模块 HEAD 为 `36bde6c`。

### 问题

- integration 重复失败，既有证据出现固定错误码 `3221225477`（十六进制 `0xC0000005`）以及 `unknown opcode` 文本。
- 现有 lab metadata 中有 1 次 `dbt_exit_code=3221225477`；该次关联文本有 `dbt build` 证据、没有 `dbt seed` 证据。
- 根 `.dig` dbt 日志有 8 次 `unknown opcode` 命中；没有直接出现 `3221225477` 或 `0xC0000005`。该日志为累积日志，虽有 seed 相关上下文，但本轮无法从明确命令边界确认这些命中发生在 `dbt seed`。

### 候选方案

- A：只读诊断 Windows/Python/dbt/Docker 运行环境，先隔离环境层问题。
- B：调整已批准的依赖锁定或运行时后重试。
- C：修改项目代码/测试或扩大真实 integration 诊断范围后重试。

### 用户最终选择 A

用户最终选择方案 A。选择理由：先隔离运行时，不改变已批准的依赖、代码和测试边界；在没有直接根因证据前，不扩大实现范围。

### 本轮只读命令及结果

- Git：`git rev-parse HEAD`、`git log`、`git status`、暂存区检查、根仓库 `third_party` 路径检查及子模块状态检查。确认 HEAD 为 `534dbbf`；根 Markdown 修改均未暂存；子模块 clean。
- Docker：`docker version`、`docker info`、`docker compose version`、`docker compose ps --all`。Docker Client/Server 为 `29.4.3`，Compose 为 `5.1.3`；Docker Desktop 可用，17 个容器中 1 个运行；compose 中 PostgreSQL 服务为 running。未启动、停止或重启容器。
- uv/Python/dbt：`uv --version`、`uv run python -c`、`uv run dbt --version`、`Get-Command`/`where` 检查。uv 为 `0.11.24`；实际 Python 为 `3.12.10`，来自项目 `.venv`；`dbt-core=1.12.3`、`dbt-postgres=1.11.0`。全局 PATH 未解析出 dbt，`uv run` 解析到项目环境的 `dbt.EXE`。
- 锁定一致性：`pyproject.toml` 为 Python `>=3.12,<3.13`、`dbt-core==1.12.3`、`dbt-postgres==1.11.0`；`uv.lock` 为 Python `==3.12.*`、dbt 版本分别为 `1.12.3` 和 `1.11.0`；与实际运行时一致。未发现本轮必须变更既有依赖的证据。
- 既有证据：读取 `.dig` dbt 日志、lab metadata 及本文件中的脱敏记录；确认固定错误码与 `unknown opcode` 的上述分布。未输出原始日志、provider 异常、凭据或敏感绝对路径。
- Windows Application Event Log：只读查询最近 14 天（`2026-08-12` 至 `2026-08-26`），事件日志可用；11 条事件可归类为 Python 进程 + Python/runtime 模块 + `0xC0000005`，时间范围为 `2026-08-14` 至 `2026-08-26`；未发现同筛选条件下可直接归类为 dbt 进程的事件。只保留脱敏进程/模块类别、时间范围、事件类别和错误码。

### 确认事实、推断与仍未知

- 确认事实：Docker 当前健康可读；Python/dbt 实际版本与 `pyproject.toml`/`uv.lock` 约束一致；Windows Application Log 存在近期 Python 访问冲突事件；既有 lab run 有固定 `dbt_exit_code=3221225477`。
- 推断：最可能的环境层分类是 Windows/Python 进程或其 native/runtime 边界，而不是当前已观察到的 Docker 不可用或锁定版本不一致。该推断不能定位具体模块，也不能证明事件由 dbt seed 触发。
- 仍未知：`unknown opcode` 的具体产生模块、`3221225477` lab run 的崩溃边界、Windows 事件与具体 integration/dbt 命令的时间关联，以及是否需要依赖或代码变更。

### 本轮未执行

- 未运行 pytest integration/e2e、lab reset、`dbt seed`、`dbt build` 或任何真实模型/数据库写入命令。
- 未设置 `DIG_RUN_OLLAMA_TESTS=1`，未调用 Ollama；未启动、停止或重启 Docker。
- 未修改 `src`、`tests`、`pyproject.toml`、`uv.lock`、`README.md`、`AGENT.md` 或项目依赖；未暂存、提交或 push。

## 2026-08-26：M4 Task5 环境恢复方案第 1 步：fresh dbt target/log 与禁用 partial parsing

### 当前进度与提交边界

- 当前进度：第 1 步已完成；健康 seed/build 均成功，按要求停止，等待下一阶段独立审查；不宣称 integration 通过。
- 当前提交：`534dbbf`（`test: harden M4 diagnosis assertions`）。
- 本节只追加到根目录 `mistake.md`，保持 workspace-only、未暂存、未提交、未 push；`AGENT.md`、`README.md` 的既有工作区修改保持不动。

### 问题

- 既有 `.dig/dbt/target`、`.dig/dbt/logs` 和 `.dig/lab/runs` 均存在；旧 `.dig/dbt/target/partial_parse.msgpack` 修改时间为 `2026-08-24T21:14:18.7895906+08:00`、大小 `570839` bytes。需要排除旧缓存对本地 dbt 崩溃证据的影响。
- 本次准备阶段曾出现两个脚本级问题：PowerShell 不接受用于建目录的参数名，随后版本文本的空匹配处理失败；这两次均未执行有效的 seed/build。参数错位曾在第三方子模块生成一个未跟踪诊断目录，已按创建时间和未跟踪状态确认只由本次失败产生后精确清理；子模块已恢复 clean。

### 候选方案

- A：先使用全新、带时间/随机后缀的 dbt target/log 目录，并显式 `--no-partial-parse`，以锁定现有 Python/dbt/数据库环境的可比诊断结果。
- B：先执行 `uv sync` 或调整 Python/dbt 版本后重试。
- C：先读取 WER，或切换 WSL/其他运行环境后重新决策。

### 用户最终选择及理由

- 用户选择 A，并明确本步不执行 `uv sync`、不改 Python/dbt 版本、不改源代码/测试/`pyproject.toml`/`uv.lock`，不调用 Ollama。
- 选择理由：先排除旧 target/log 与 partial parsing 缓存影响，保持当前锁定版本和固定 PostgreSQL 环境不变，避免把环境恢复问题与依赖/平台变化混在一起。

### 实际命令与脱敏结果

- 只读确认：`git rev-parse HEAD`、旧目录存在性/时间/大小/文件数快照、`partial_parse.msgpack` SHA-256 快照，以及运行后的同样快照比较。结果：旧缓存保持不变，`old_cache_unchanged=True`。
- 只读版本/CLI：`uv run --no-sync dbt --version` exit `0`，实际 `dbt-core 1.12.3`、`dbt-postgres 1.11.0`；`uv run --no-sync dbt --help` exit `0`，确认 CLI 提供 `--partial-parse / --no-partial-parse`。未修改项目配置，未依赖环境变量开关。
- 实际命令模板（路径均为脱敏占位符）：`uv run --no-sync dbt seed --full-refresh --project-dir [LOCKED_PROJECT] --profiles-dir [LOCKED_PROFILES] --target dev --target-path [FRESH_TARGET] --log-path [FRESH_LOGS] --no-use-colors --no-partial-parse`，seed exit `0`。
- 实际命令模板（路径均为脱敏占位符）：`uv run --no-sync dbt build --project-dir [LOCKED_PROJECT] --profiles-dir [LOCKED_PROFILES] --target dev --target-path [FRESH_TARGET] --log-path [FRESH_LOGS] --no-use-colors --no-partial-parse`，build exit `0`。
- 新诊断目录：`.dig/diagnostics/m4-task5-fresh-dbt/run-20260826-123729-9a1415e6/`，其下独立 `target/` 与 `logs/`；另有脱敏 `summary.json`。未使用旧 `.dig/dbt` 或 `.dig/lab/runs` 路径。
- seed 产物：`manifest.json`、`run_results.json`、`dbt.log` 均存在；`run_results.json` 可读，3 个结果均为 `success`。
- build 产物：`manifest.json`、`run_results.json`、`dbt.log` 均存在；`run_results.json` 可读，28 个结果状态仅为 `pass`/`success`。
- seed/build 输出与新日志均未见 `3221225477`、`0xC0000005` 或 `unknown opcode`；未保留或输出完整异常、原始 SQL、凭据或敏感绝对路径。
- 数据库健康状态可读：是；依据为固定 PostgreSQL 上 seed/build 成功且新 `run_results.json` 可解析。该结果不等同于 integration 门槛通过。

### 后续候选与执行边界

- 下一阶段候选仍为：继续 `uv sync`、读取 WER，或重新评估版本/WSL；本步不替用户选择。
- 本步只执行健康 seed/build，未执行故障注入、模型调用或 Ollama；未修改源代码、测试、依赖、版本配置、第三方子模块或既有证据目录；未暂存、提交或 push。

## 2026-08-26：M4 Task5 环境稳定后重新运行 integration

### 当前进度与提交边界

- 当前进度：第 1 步 fresh target/log 与禁用 partial parsing 已完成；本次 integration 通过，按用户要求停止，等待复审，不执行 unit/e2e/lock/help。
- 当前 `HEAD` 为 `534dbbf`（`test: harden M4 diagnosis assertions`）。本条只追加到根目录 `mistake.md`，保持 workspace-only、未暂存、未提交、未 push。
- 未修改源代码、测试、`pyproject.toml`、`uv.lock`、CI、`third_party` 或 `.venv`；未执行 `uv sync`；未调用 Ollama。

### 旧目录备份与 fresh 产物

- 执行前 `.dig/dbt` 含 `target/`、`logs/`；其中旧 `target/partial_parse.msgpack` 为既有缓存，旧日志也已存在。
- 旧目录整体移动至 `.dig/diagnostics/m4-task5-previous-dbt-20260826-124702899/`；移动前确认目标不存在且目标路径位于项目 `.dig` 内，移动后确认旧 `.dig/dbt` 不存在、备份仍含 `target/` 与 `logs/`。未删除或覆盖旧证据。
- 项目重新创建 `.dig/dbt/target` 与 `.dig/dbt/logs`。fresh `manifest.json`、`run_results.json`、`dbt.log` 均存在且可读；manifest 含 28 个 nodes，run results 含 28 个结果，日志非空。

### partial parsing 强制方式

- 本地 `.venv` 的 dbt 版本为 `dbt-core 1.12.3`、`dbt-postgres 1.11.0`；安装包参数定义确认 `DBT_PARTIAL_PARSE` 映射到 `--partial-parse/--no-partial-parse`。
- 本次只在同一 PowerShell 进程临时设置 `DBT_PARTIAL_PARSE=false`，并在命令结束后恢复为未设置；`DIG_RUN_OLLAMA_TESTS` 同样在测试期间保持未设置并恢复为未设置。
- 未创建临时 wrapper，因而无 wrapper 路径、无残留、无需清理；未修改 `DbtRunner`。fresh `dbt.log` 有 14 次脱敏的 `Partial parsing not enabled` 记录，证明 dbt 子进程按禁用配置执行。dbt 1.12.3 在全量解析后仍会写出 `partial_parse.msgpack`，因此该文件存在本身不是 partial parsing 已启用的证据。

### integration 结果与稳定错误 markers

- 脱敏命令：`$env:DBT_PARTIAL_PARSE='false'; Remove-Item Env:DIG_RUN_OLLAMA_TESTS; uv run pytest tests/integration -q; finally restore environment`。
- `uv run pytest tests/integration -q`：exit `0`，`8 passed in 93.62s`。
- fresh `.dig/dbt/logs/dbt.log` 中 `Database Error`、`Runtime Error`、`Compilation Error`、`unknown opcode`、`3221225477`、`0xC0000005` 均为 0 次；本次未重现既有 Windows/dbt 崩溃 markers。

### 恢复状态、数据库与候选方案

- integration fixture 完成自身 finally reset；随后只读数据库检查成功：`SELECT 1` 返回 1，固定表行数可读（raw_payments 113、stg_payments 113、orders 99、customers 100），连接已关闭。未额外执行 `lab reset`。
- 用户既定选择为方案 A：保留当前锁定依赖，先用 fresh target/log 与禁用 partial parsing 排除旧缓存影响；依赖不变因此跳过 `uv sync`。该选择本次得到 integration 通过的结果。
- 后续候选仍为：独立复审本次 fresh 证据；若未来再次出现 `3221225477`/`0xC0000005`/`unknown opcode`，按用户方案停止并进入后续环境诊断，不在本阶段追加重试或修改依赖。

## 2026-08-26：M4 Task5 integration 可审计证据补齐（本轮）

### 当前进度与问题

- 当前 `HEAD` 为 `534dbbf`；fresh `.dig/dbt` 与旧目录备份均保留。本轮只执行了一次精确命令 `uv run pytest tests/integration -q`。
- 本轮结果：pytest 最后一行 `1 failed, 7 passed in 73.23s (0:01:13)`，exit code `1`；公开错误码仅记录为 `1`，不在本记录或最终报告中保留完整异常。
- `DBT_PARTIAL_PARSE=false` 仅在本轮 PowerShell 进程内设置，命令结束后已清理恢复为未设置；`DIG_RUN_OLLAMA_TESTS` 执行前后均未设置。未调用 Ollama、未执行 `uv sync`，未重试，未执行 exit 非零分支之外的数据库健康核对。
- 脱敏持久化证据：`.dig/diagnostics/m4-task5-integration-20260826-125930309.txt`。Tee 的临时原始内容已覆盖为仅含命令、pytest 汇总、exit、计数和环境清理状态的脱敏汇总。

### 候选方案与沿用用户已选方案的原因

- 候选方案：A，继续保留锁定依赖，使用 fresh target/log 并禁用 partial parsing 做独立复审；B，修改依赖或环境后重试；C，先做更广泛的 WER/平台诊断。
- 仍沿用用户已选方案 A：本轮非零后按用户边界停止，不能用重试或环境变化掩盖当前 integration 失败；方案 B 会违反“不改环境/不执行 uv sync”，方案 C 超出本轮证据补齐范围。

### 修改与提交边界

- 未修改 `src`、`tests`、`pyproject.toml`、`uv.lock`、依赖、CI 或 `third_party`；仅追加本节并保存 `.dig/diagnostics` 下的脱敏输出。
- 未暂存、未提交、未 push；根 Markdown 变更保持 workspace-only。

## 2026-08-26：M4 Task5 第 2 步同步环境并复核 integration

### 当前进度与边界

- 当前 `HEAD` 为 `534dbbf`。执行前工作区仅有既存的 `AGENT.md`、`README.md`、`mistake.md` workspace-only 修改；`pyproject.toml` 与 `uv.lock` 无 diff。旧证据备份 `.dig/diagnostics/m4-task5-previous-dbt-20260826-124702899/` 保留，未删除或覆盖。
- 本阶段只执行环境动作 `uv sync --frozen`；exit `0`。未修改 `pyproject.toml`、`uv.lock`、Python/dbt 版本、源代码、测试、CI 或 `third_party`；未提交、未 push。

### sync 与版本一致性

- sync 前后 `pyproject.toml`、`uv.lock` 均无 diff，SHA-256 未变化。项目锁定约束为 Python `>=3.12,<3.13`、`dbt-core==1.12.3`、`dbt-postgres==1.11.0`；sync 后实际为 Python `3.12.10`、dbt Core `1.12.3`、postgres plugin `1.11.0`。`.venv` 的 Python 与 dbt 可运行。
- 未保留或输出完整安装日志、凭据或敏感绝对路径。

### integration 与脱敏证据

- 只运行一次：`DBT_PARTIAL_PARSE=false` 临时设置后执行 `uv run pytest tests/integration -q`；测试后变量已清理。`DIG_RUN_OLLAMA_TESTS` 执行前后均未设置。
- post-run pytest cache 显示 8 个 integration nodeid 且 `lastfailed={}`，据此记录为 `8 passed`、exit `0`。首次命令等待窗口未回传包装器 JSON 的原始 exit 字段，故该 exit 是基于完成会话与空失败缓存的间接证据；没有重试，也没有读取 WER。
- 脱敏记录保存于 `.dig/diagnostics/m4-task5-sync-integration-20260826-130821.txt`；该目录被 `.gitignore` 忽略。

### 恢复状态与下一步

- PostgreSQL Compose 状态为 `running healthy`；只读 `SELECT 1` 返回 `1`，固定关系行数可读：`raw_payments=113`、`stg_payments=113`、`orders=99`、`customers=100`。fresh `manifest.json` 与 `run_results.json` 可读，dbt 状态仅为 `success`/`pass`。
- 当前进度：第 2 步完成，按用户要求停止等待审查。未执行后续 unit/e2e/lock/help；未调用 Ollama；下一候选步骤留给新的阶段按用户方案第 3 步决定。

## 2026-08-26：M4 Task5 ProcessStartInfo integration exit/stdout 证据补齐

### 当前进度与问题

- 当前 `HEAD` 为 `534dbbf`；`uv sync --frozen` 已由用户确认 exit 0，锁文件与版本不变。本轮只补一次可靠的 integration exit/stdout 证据；未修改代码、测试、`pyproject.toml`、`uv.lock`、CI、`third_party`，未提交或 push；旧 dbt 备份保留。
- 待补问题是此前 integration 成功记录缺少直接的 `Process.ExitCode` 与重定向 stdout/stderr 证据；本轮使用 PowerShell `System.Diagnostics.ProcessStartInfo` 启动项目 `uv`，不依赖 pytest cache 推断。

### 候选方案与用户既定选择

- 候选方案：A，锁定现有依赖并补一次 ProcessStartInfo 的直接 exit/stdout 证据；B，修改依赖或环境后重试；C，读取 WER 或做更广泛诊断。
- 用户既定选择为方案 A：不再执行 `uv sync`，不读 WER，不调用 Ollama，不执行其他测试；若本次非零则记录公开 summary/exit 后停止。

### 本次证据结果

- 唯一一次进程运行使用 `Get-Command uv` 的实际来源、项目根 `WorkingDirectory`、参数 `run --no-sync pytest tests/integration -q`、`RedirectStandardOutput/RedirectStandardError=true`、`UseShellExecute=false`；`WaitForExit` 后直接读取 `Process.ExitCode`。
- `Process.ExitCode=0`，pytest summary 为 `8 passed in 91.68s (0:01:31)`，stdout 末行与 summary 一致，stderr 无保留内容；结果写入 `.dig/diagnostics/m4-task5-integration-process-20260826-131824407.txt`，仅保留脱敏 summary/末行。
- 子进程测试控制变量为 `DBT_PARTIAL_PARSE=false`，`DIG_RUN_OLLAMA_TESTS` 不存在；进程结束后父进程两者均未设置。健康、旧备份和既有 artifact 证据仍沿用既有记录限定结论。本轮不需要 WER，可跳过 WER。

## 2026-08-26：M4 Task5 用户方案第 3 步：只读 WER faulting module 关联

### 当前进度、提交与问题

- 当前进度：用户方案第 3 步完成；只读读取 Windows Application Error/WER 事件与可访问 WER 报告，未读取 dump 内容，未继续扩大扫描范围。
- 当前 HEAD：`534dbbf`。本阶段只追加本节到根目录 `mistake.md`，保持 workspace-only；不暂存、不提交、不 push。
- 问题：最近一次已有 e2e/lab run `0c9db381ff2544cfb4ba36d0146381ba` 的 metadata 记录 `dbt_exit_code=3221225477`（`0xC0000005`）。该 run 目录时间窗口为 `2026-08-26 08:20:30.850 +08:00` 至 `08:20:36.239 +08:00`；其 dbt 日志最后写入约 `08:20:32.556 +08:00`，与既有 e2e 失败记录相符。

### 用户既定选择与只读理由

- 用户既定选择：方案第 3 步，先读取 WER，不运行 pytest/dbt/`uv sync`，不调用 Ollama，不修改代码、依赖、锁、CI、`third_party`、注册表/ACL/WER 配置，不删除 dump，不提交或 push。
- 选择只读 WER 的理由：在不改变 Python/dbt/数据库环境且不重复崩溃的前提下，获取 Windows 记录的 faulting application/module、异常码和偏移，区分 Python runtime/native 边界与 dbt/依赖线索；faulting module 只作为崩溃现场事实，不自动等同于根因。

### 脱敏 WER 结果

- WER 可读：Application 日志可读；`Microsoft-Windows-WER-Diag/Operational`、`WER-PayloadHealth/Operational`、`WerKernel/Operational` 可查询但本窗口没有对应 1000/1001 记录。`ProgramData` 下 WER `ReportArchive` 可读，共发现 141 个 `Report.wer`；`ReportQueue` 可读但为空；当前用户 `LocalAppData` 下对应目录不可访问。未读取 dump。
- 直接事件事实（与最近 e2e/dbt run 强关联）：
  - `2026-08-26 08:20:33.080 +08:00`，Application Error / Event ID 1000：faulting application basename=`python.exe`；faulting module basename=`python312.dll`；exception=`0xc0000005`；fault offset=`00000000000896be`；integrator/report id=`8abe19b1-3f68-4869-a391-2a9c3455f342`。
  - `2026-08-26 08:20:35.888 +08:00`，WER / Event ID 1001 / `APPCRASH`：report id=`8abe19b1-3f68-4869-a391-2a9c3455f342`。对应 archive 报告为 `APPCRASH`，application=`python.exe`、module=`python312.dll`、exception=`0xc0000005`、offset=`00000000000896be`，archive report identifier=`fe444c9d-6048-4409-9e1d-d7e1a85acf16`，integrator identifier 与事件一致。
  - 关联强度：强。WER 1000 发生在 lab run 约 2.2 秒后，1001/Archive 报告又在约 5 秒内落盘；模块、异常码、偏移与 `dbt_exit_code=3221225477` 一致。但这仍只能证明同一时间窗口的 Python 崩溃现场，不能证明 `python312.dll` 是根因。
- 时间相关但未证实：
  - `2026-08-26 08:24:13.628 +08:00`，1000：`python.exe` / `python312.dll` / `0xc0000005` / offset=`00000000000866b8` / id=`5ff1d95d-60dc-494d-a5d9-c9b46aa15c6e`；`08:24:16.468` 的 1001 `APPCRASH` 与 Archive 报告相符，archive identifier=`65708530-0c57-4727-9703-ae5a327d2691`。当前 `.dig/lab/runs` 未找到与该时间相符的 e2e/dbt run，故不绑定到本次 e2e。
  - `2026-08-26 11:23:43.669 +08:00`，1000：`python.exe` / `python312.dll` / `0xc0000005` / offset=`00000000000896be` / id=`e70b3120-2d54-4d1b-a728-6f623aa6a66f`；Archive 的 1001 报告在 `11:23:48.525`，archive identifier=`465fdad6-f7a7-4aa4-b21e-78dc7f70ccb3`。没有匹配的 e2e/dbt 运行记录。
  - `2026-08-26 13:27:42.476 +08:00` 的 Python 1000 事件为 `python.exe` / `unknown` / `0xc0000005` / offset=`0000000000000000` / id=`b3f99a1a-9b29-4bb5-9d25-7cef1ec3bdfb`；Archive 报告分类为 `BEX64`、module=`StackHash_ac46`，未找到匹配 e2e/dbt 记录，且其报告字段形态不同，不能与本次故障合并。
- 仍未知：WER 证据不能区分 Python runtime 本身、dbt dependency 触发的运行时路径、其他 native DLL/扩展，或更上游环境因素；也不能从 faulting module 单独推出根因。`unknown opcode` 与 WER faulting module 的因果关系仍未证实。

### 候选解释与边界核对

- Python runtime：当前最直接候选，因直接事件事实稳定指向 `python312.dll`，但仍未证明是根因。
- dbt dependency：可能通过调用路径触发 Python/native 崩溃；当前 WER 不提供 dbt 作为 faulting application，不能直接确认。
- 其他 native DLL：仍可能存在；本次 faulting module 现场未显示其他模块，不能据此排除。
- 无足够 WER：对最近一次 e2e/dbt 已有可读且一致的 1000/1001/Archive 证据；但对根因定位仍然不足，不能把“WER 可读”误写成“根因已知”。

### Git 与禁止项核对

- 根仓库当前 HEAD 为 `534dbbf`；根仓库已有未暂存修改为 `AGENT.md`、`README.md`、`mistake.md`。`pyproject.toml` 与 `uv.lock` 无 diff。
- `third_party/jaffle_shop` 当前 HEAD 为 `36bde6c`，工作区 clean；本阶段未修改 third-party。
- 本阶段未运行 pytest/dbt/`uv sync`，未调用 Ollama，未读取 dump，未修改代码/依赖/锁/CI/third-party/注册表/ACL/WER 配置，未删除 dump，未暂存、提交或 push。

### 下一步决策

- 第 3 步的只读证据已足以支持“最近一次 e2e/dbt 与 Python `python312.dll` 访问冲突强关联”；不建议在没有用户明确选择前重跑崩溃或修改环境。
- 下一步是否进入 Python/dbt/native 边界的进一步诊断、改用其他环境，或保持证据不足，需由用户决定。

## 2026-08-26：M4 CI 失败后 PostgreSQL/Docker 诊断采集（用户选择 B）

### 当前进度、基线与问题

- 当前进度：已按用户选择 B 为 GitHub Actions 增加一次失败后的 PostgreSQL/Docker 诊断采集，并完成 workflow YAML、脚本语法、差异和提交范围核验；未运行本机 integration/e2e、Ollama 或 dbt 长测试。
- 当前基线提交：`7a693c6`；生成的新 CI 提交：`96ad13c062a031f79924de1c5212552011b64097`，提交信息为 `ci: collect postgres diagnostics on failure`，已推送到 `origin/master`。
- 问题：Ubuntu CI 的 integration 曾出现 `127.0.0.1:55432 server closed unexpectedly`；现有测试输出缺少 PostgreSQL 容器日志、容器 ID、健康状态、退出码、OOM 与重启证据，因此不能仅凭现有日志确认 OOM、崩溃、重启或 Docker 层根因。

### 候选方案与选择理由

- 候选方案 A：只保留现有 CI 全部日志并先做证据审查；候选方案 B：在上游测试失败后增加 PostgreSQL/Docker 诊断采集，再用下一次 integration 失败取得容器现场。
- 用户选择 B。理由：当前证据缺口正是 PostgreSQL 服务日志和容器状态；B 只增加失败后采集，不改变 Ruff、unit、integration、e2e 命令、顺序、测试语义、资源、超时、重试或依赖方向，能直接补齐下一次失败所需证据。

### 诊断采集边界与安全处理

- Compose 项目为 `data-incident-gym`，服务为 `postgres`；采集 `docker compose -f compose.yaml ps -a`、无颜色且带时间戳的 `postgres` 日志、对应容器 ID，以及白名单状态字段：`State.Status`、`Health.Status`、`Health.Log`、`ExitCode`、`OOMKilled`、`RestartCount`、`StartedAt`、`FinishedAt`。
- 不输出完整 `docker inspect`、环境变量或凭据；即使容器不存在，也会生成说明文件和容器 ID/状态结果文件。采集和 artifact 上传均不掩盖原始测试失败；artifact 名称为 `postgres-diagnostics`，保留 7 天。
- 根目录 `AGENT.md`、`README.md`、`mistake.md` 均为 workspace-only，仍未暂存、未提交、未进入该 push。

## 2026-08-26：窄范围 WER 诊断复核（workspace-only）

### 当前进度、提交与问题

- 当前 `HEAD` 为 `534dbbf`。本次仅读取最近 48 小时 Application 日志、限定的 WER 目录和现有日志时间；未运行测试/dbt/`uv sync`/Ollama，未修改代码、依赖、锁、CI、`third_party`、注册表、ACL 或 WER 配置，未删除 dump，未提交或 push。
- Application 日志按 Event ID `1000/1001`、最多 200 条并按关键词筛选，共得到 12 条匹配事件；未输出或保留原始 message/XML。`.dig/dbt/logs/dbt.log` 最后修改时间为 `2026-08-26 13:27:42.185 +08:00`。
- WER `ReportArchive`/`ReportQueue` 的限定目录枚举分别看到 20/12 个近期目录；读取 `Report.wer` 时遇到 `ReportArchive` 受保护目录的 Access denied，按边界停止，不再扩大扫描。此前同一限定尝试打开 2 个报告，但本轮未获得可可靠提取的报告字段；不把不可读报告写成已验证事实。

### 脱敏结果与时间关联

- 直接 WER 事件：
  - `08:20:33.080`，1000，Application Error：`python.exe` / `python312.dll` / `0xc0000005` / `00000000000896be`；`08:20:35.888`，1001，WER，report id=`8abe19b1-3f68-4869-a391-2a9c3455f342`。
  - `08:24:13.628`，1000：`python.exe` / `python312.dll` / `0xc0000005` / `00000000000866b8`；`08:24:16.468`，1001，WER，report id=`5ff1d95d-60dc-494d-a5d9-c9b46aa15c6e`。
  - `11:23:43.669`，1000：`python.exe` / `python312.dll` / `0xc0000005` / `00000000000896be`；`11:23:48.525`，1001，WER，report id=`e70b3120-2d54-4d1b-a728-6f623aa6a66f`。
  - `13:27:42.476`，1000：`python.exe` / `unknown` / `0xc0000005` / `0000000000000000`；`13:27:45.732`，1001，WER，report id=`b3f99a1a-9b29-4bb5-9d25-7cef1ec3bdfb`。
- 时间相关但未证实：既有记录的 e2e 失败窗口为 `08:20:30.850–08:20:36.239 +08:00`，dbt exit 为 `3221225477`（`0xc0000005`）；上述 `08:20` Python WER 事件落在该窗口内，构成时间/异常码/模块的强关联，但不是根因证明。`08:24`、`11:23`、`13:27` 没有在本次限定查询中绑定到已记录 e2e/dbt run；13:27 的 WER 时间接近 `dbt.log` 最后修改时间，但“最后修改”本身不证明因果。
- 仍未知：Python runtime、dbt/依赖调用路径、其他 native DLL 或环境因素谁触发了崩溃；faulting module 是崩溃现场，不自动等于根因。由于 WER 报告目录受权限限制，本轮不作更强结论。

### 候选解释与采用用户第 3 步的理由

- 候选解释：`python312.dll` 运行时/native 边界；dbt 或依赖触发的 Python 路径；其他 native DLL/环境因素。当前证据只支持排序线索，不支持定因。
- 采用用户第 3 步（只读 WER 与时间关联）的理由：在不重跑崩溃、不改变 Python/dbt/数据库环境、不读取 dump 的前提下，补充 Windows 侧 application/module、异常码、偏移和时间证据；权限阻断后立即停止，避免再次扩大扫描范围。

## 2026-08-26：M4 Task5 Windows/Python native runtime 根因收敛与本地解锁

### 原始失败复现与运行时对照

- 在当前 `HEAD=534dbbf` 上重跑 Task5 原始命令 `uv run --no-sync pytest tests/e2e -q -s`，Ollama opt-in 未设置。健康 baseline 10/10 通过，事故段前 8 轮通过，第 9 轮 dbt 子进程以 `3221225477` (`0xc0000005`) 退出，未生成可读 `manifest.json`/`run_results.json`；pytest 为 `1 failed, 1 passed, 1 skipped`。
- 同一时间窗口 WER 记录 `python.exe` / `python312.dll` / `0xc0000005`，与该 run 的 exit code 强关联。
- 在 `.dig/diagnostics/m4-runtime/` 内创建全新 uv-managed Python 3.12.10 及独立 venv，使用同一 `uv.lock` 后仍复现访问冲突与 `impossible<bad format char>` 类型的不可能 Python 语义错误；因此排除“原全局 Python 安装文件损坏”。
- 再使用 uv-managed Python 3.12.13 及同一锁定依赖，仍复现 `python312.dll` 访问冲突与 `os.environ.items()` 的 key 被解释成 generator 的不可能状态；因此 Python 3.12 补丁版本不是解法。

### native 边界二分结果

- `dbt_extractor.py_extract_from_source` 单进程 500,000 次压测 exit `0`。
- 默认 static parser 的独立 `dbt parse` 使用 30 个 fresh target/log 连续 30/30 exit `0`。
- `DBT_STATIC_PARSER=false` 下 baseline 10/10 曾通过，但事故段第 3 轮仍以 `3221225477` 失败；因此 static parser 不是根因，一次绿灯只是概率性表现。
- `psycopg2-binary 2.9.12` 独立压测使用 4 线程、100 个独立连接任务、2,000 次临时表 COPY/查询/回滚，exit `0`；因此数据库驱动单独路径也未复现。

### 当前机器级证据与结论边界

- 重新读取当前 Windows 事件：近 30 天有 8 次 BugCheck（含 `0x50`/`0x0a`/`0x18`/`0x1e`/`0xf7`），以及 103 次分布于 18 个无关应用的 `0xc0000005`。
- `2026-08-23` 仍有 WHEA-Logger Event 19：CPU `APIC ID 41` / `Internal parity error`。该新证据与此前内核 dump 及跨应用访问冲突一致。
- Microsoft Sysinternals Coreinfo 4.02 显示逻辑处理器 0–15 为 8 个 P-core 超线程，16–31 为 E-core；下载 zip SHA-256 为 `DA7D48956D66CFC2AC3766C30E7F46209CDC5824EB25B1BBD4CB10F124B2D7A4`。
- 结论：证据支持“DataIncidentGym/dbt 是机器级 CPU/cache/内存控制器/RAM 不稳定的高频触发器”，不支持“M4 业务代码、某个 Python 安装或单一 dbt 依赖是根因”。尚不能仅凭 APIC/WHEA 区分 CPU core、cache、内存控制器或 RAM 的最终硬件责任。

### P-core-only 临时解锁与完整门槛

- 只在测试 PowerShell 进程内临时设置 `ProcessorAffinity=65535` (`0xFFFF`)，使 uv/Python/dbt 子进程继承逻辑处理器 0–15；命令结束后用 `finally` 恢复原 affinity `4294967295`。未修改 BIOS、服务、注册表、项目代码或依赖。
- 第一次原始 e2e 对照：`2 passed, 1 skipped in 227.21s`，exit `0`，baseline 10/10、事故复现 10/10 与 finally 恢复全部通过。
- 第二次完整 Task5 门槛：Ruff exit `0`；unit `263 passed, 3 skipped`；integration `8 passed in 74.81s`；e2e `2 passed, 1 skipped in 237.51s`；`uv lock --check`、两个 CLI help、submodule HEAD/clean 与 `git diff --check` 均 exit `0`。
- 第二次门槛期间新增 Python WER 事件数为 `0`；submodule 仍固定在 `36bde6cba69d962b83be1d52fc65a0dce1cb4ebb` 且 clean。
- 最终 Git 状态仍只有根目录 `AGENT.md`、`README.md`、`mistake.md` 既定 workspace-only 修改；未修改或提交 `src`、`tests`、`pyproject.toml`、`uv.lock`、CI 或 `third_party`。
- 该 affinity 只是当前机器的临时验收绕行，不应写入项目生产路径或 Ubuntu CI；永久修复仍需在用户参与下停用超频/降压、恢复 BIOS 稳定默认值，并继续做 CPU/RAM 分步隔离。

## 2026-08-26：M4 Ubuntu integration PostgreSQL 异常断开调查决策（上一阶段记录）

> 本节记录上一阶段用户选择 A 及其 `m1` 日志取证；当前阶段已选择 B，并生成 CI 诊断提交 `96ad13c062a031f79924de1c5212552011b64097`，详见上文。

### 上一阶段进度、提交与问题

- 当前进度：M4 已推送到 `7a693c6`；unit `266 passed`；integration `1 failed/7 passed`。失败发生在 `IncidentLab.reset` 的初始 PostgreSQL 读取阶段。
- 提交：`7a693c6`。本次没有新代码提交，仅追加本条文档记录。
- 根目录 Markdown 仅保持 workspace-only，不暂存、不提交、不推送；本记录不进入 `7a693c6` 提交。
- 问题：`127.0.0.1:55432 server closed unexpectedly`。

### 上一阶段候选方案与当时选择

- 候选方案：A，提供现有 CI 全部日志并先做证据审查；B，增加 CI 失败后的 `docker compose logs`/`ps`/`inspect` 诊断采集后重跑。
- 当时选择及原因：用户选择 A，因为先审查现有日志成本低且不改变项目。调查后仍缺 PostgreSQL 容器日志/状态，不能确认 OOM、崩溃或重启，也不应猜测性修改；该选择不代表当前阶段决策。

### 证据位置与当前结论

- 证据位于 `C:\Users\29913\OneDrive\桌面\m1`，共 11 个日志文件，特别是 integration 日志；记录 Ubuntu 24.04、commit `7a693c6`、unit `266 passed`、integration `1 failed/7 passed`。
- 当前结论：证据不足，暂停代码/CI 修复；若继续，需要用户决定是否采用 B。

## 2026-08-26：M4 CI 远程门禁验收结果

- 当前进度：M4 CI 远程门禁已通过。
- 提交：`96ad13c` 已推送 `origin/master`；根目录 Markdown 仍 workspace-only。
- 问题/警告：`actions/checkout@v4`、`astral-sh/setup-uv@v6` 目标 Node.js 20，但 runner 强制使用 Node.js 24，产生 Node.js 20 deprecated runtime warning；这不是测试失败，未影响 CI 正确性。
- 候选方案：A，暂不修改，继续观察 GitHub action 迁移要求；B，另开决策升级 checkout/setup-uv action 大版本以消除警告。
- 最终选择：本轮不升级，因为 CI 已通过且升级 action 属于兼容性/版本方向决策，需用户单独授权。
- 证据：用户提供的 CI 全绿结果和 warning 文本。
- 当前结论：M4 验收可以结束，警告作为后续可选项；该记录不暂存、不提交、不推送。

## 2026-08-26：M5 Task 2 ArtifactWriter 并发发布修复决策（用户选择 A）

### 当前进度与独立审查结论

- 当前进度：M5 Task 2 已完成初次实现并提交；当前提交为 `8c11ca7`（`feat: persist auditable M5 artifacts`）。
- 独立审查结论：实现方向和既定边界基本符合，但发现以下四个本地问题，需在不进入 Task 3 的前提下做最小修复并重新验证：ArtifactWriter 的最终目标存在性检查到 `replace` 之间存在并发覆盖竞态；`tests/unit/test_evaluation.py` 的既有 helper 与新增 `elapsed_ms` 合同不兼容；`diagnostic_agent.py` 中 `_ControllerInvariantError` 分支抛出前未记录 trace；ArtifactWriter 临时目录清理失败被静默吞掉。

### 问题

- 已确认的问题是：两个遵守同一 ArtifactWriter 发布协议的进程可同时通过目标不存在检查，随后一个发布者的 `replace` 可能覆盖另一个发布者已发布的目录，破坏“不覆盖”契约。
- 其余三个问题均为本地兼容性、可审计性或错误可诊断性问题：evaluation 测试 helper 未适配 `elapsed_ms`；Controller invariant 失败缺少工具调用 trace；临时目录清理失败未按现有错误模型显式报告。
- 本记录区分实现状态：此时四项均为待修复/待验证；不把后续测试结果预先记录为已通过。

### 候选方案

- A：保持 ArtifactWriter 的“不覆盖”契约，在项目内使用无新增依赖、Windows/Ubuntu 均可用的协作式跨进程独占发布锁，覆盖最终目标存在性检查至原子发布的整个区间，并安全释放锁；锁只约束遵守本项目协议的写入者。
- B：仅在 `replace` 前再次检查目标，继续依赖检查与发布之间的时间窗口。
- C：改为覆盖式发布或引入平台/第三方锁机制，以减少竞态但改变“不覆盖”语义或依赖/平台边界。

### 用户最终选择与原因

- 用户最终选择 A。
- 选择原因：A 保持“不覆盖”契约，采用 Windows/Ubuntu 均可用的项目内协作式跨平台独占发布锁，避免修改产品契约；同时明确该锁只约束遵守本项目协议的写入者，不宣称能够阻止绕过协议的外部程序。

### 决策后的理解和执行边界

- 本轮仅修复上述四个本地问题并增加最小确定性回归测试；保留临时目录原子发布、禁止覆盖、脱敏、四个 M3 只读工具和现有错误模型，不扩大 P1，不增加 Ground Truth、Shell、任意文件系统、数据库、网络或修复能力。
- 代码/测试修复和相关验证完成后只提交计划内代码与测试文件；根目录 `AGENT.md`、`README.md`、`mistake.md` 保持未暂存、未提交、未推送，`third_party/jaffle_shop` 保持 clean；不进入 Task 3，不 push。

## 2026-08-27：M5 Task 2 Windows PID 探测与 owner 发布决策

### 当前进度

M5 Task 2；最终审查发现 Windows PID 探测存在安全阻断。

### 相关提交

- 当前相关提交：`013d62a`

### 问题

Windows 上使用 `os.kill(pid, 0)` 进行存活探测可能调用 `TerminateProcess`，存在误杀持锁进程的风险；owner 部分写入也存在清理缺口，可能留下本实例自锁或让部分内容可见。

### 候选方案

1. 方案 1：保留既有方案 A 的项目内跨平台协作独占发布锁；Windows 使用标准库 `ctypes` 调用 `OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)`、`GetExitCodeProcess` 和 `CloseHandle` 进行非破坏性判断，POSIX 保留现有 `os.kill(pid, 0)` 探测。
2. 方案 2：增加 `psutil` 或其他锁/进程探测依赖，统一封装跨平台 PID 存活判断。
3. 方案 3：放宽/移除 PID 存活探测或改用其他平台特定机制，扩大锁语义或改变 stale-lock 回收策略。

### 用户最终选择

用户选择方案 1。

### 为什么选择 1

不增加依赖，保留方案 A 以及 no-overwrite/stale-lock 语义；Windows 使用 `OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)` 与 `GetExitCodeProcess` 做非破坏性判断，POSIX 使用现有探测。

### 决策后的理解和执行边界

已明确理解该决策：Windows PID 存活探测不得调用 `os.kill`，权限或系统错误等不确定情况必须 fail closed 保留锁；实现必须避免误杀持锁进程，并通过实例私有临时 owner 文件和原子替换避免部分 owner 内容可见。仅修改 `src/data_incident_gym/artifacts.py` 与 `tests/unit/test_artifacts.py`，不增加依赖、不改变 ontology/Agent/Runner、不进入 Task 3；`mistake.md` 保持未暂存、未提交、未推送。

## M5 Task 4 凭据边界决策记录

### 当前进度

当前进度为 M5 Task 4；独立审查发现 doctor 存在凭据边界问题。

### 提交

相关提交：`603dd14`。

### 问题

`Settings.subprocess_environment` 含 `DIG_POSTGRES_PASSWORD`，被传给 uv/docker/dbt；doctor 还使用管理数据库凭据；另有 Agent 初始化异常未捕获。

### 候选方案

1. 方案 1：doctor 的数据库与 dbt 诊断统一使用 `DiagnosticSettings` 的只读连接配置，为 dbt 子进程构造不含管理凭据的最小环境，并补齐 Agent 初始化异常捕获。
2. 方案 2：继续沿用管理配置及其子进程环境传播，仅补充局部异常处理或其他较宽松的兼容方案。

### 用户选择

用户选择方案 1。

### 为什么选择 1

方案 1 遵守只读 doctor 与最小凭据边界，不把管理密码暴露给子进程，保持 P0 安全契约；不改变 doctor 管理面定义。

### 理解确认

已明确理解 doctor 不应读取或传播管理连接串。

## M5 Task 4 凭据边界决策记录：拆分诊断 profile

### 当前进度

当前进度为 M5 Task 4，当前提交为 `11790aa`；独立审查发现 `profiles.yml` 管理凭据回退问题。

### 问题

doctor/dbt debug 可能读取 `DIG_POSTGRES_USER`/`DIG_POSTGRES_PASSWORD` 和默认 `dig_admin`。

### 候选方案

1. 方案 1：拆分管理 profile 与专用诊断 profile，doctor 只使用后者；管理 profile 继续供 IncidentLab/lab 流程使用。
2. 方案 2：继续共用管理 profile，仅通过环境变量覆盖或局部清理 doctor 的凭据来源。
3. 方案 3：移除 dbt debug 或放宽 profile/凭据边界，改用更宽泛的兼容路径。

### 用户选择

用户选择方案 1。

### 为什么选择 1

拆分管理 profile 与专用诊断 profile，doctor 只使用后者，保留实验室管理流程且不放宽只读诊断安全边界。

### 理解确认

已明确理解 doctor 不读取、不传播管理 profile 凭据；诊断缺失时固定返回不可用状态，不回退到管理 profile。

### M5 Task 5 本次决策记录（只读环境诊断）

- 当前进度：M5 Task 5。
- 当前提交：`f9ae741`。
- 前置检查结果：0/3；样本尚未开始，且无产物。
- 问题：`COMPOSE_POSTGRES`、`POSTGRES_CONNECTION`、`DBT_PROFILE_CONNECTION` 检查失败。
- 候选方案：1）只读诊断；2）用户修复后重跑；3）暂停。
- 用户选择：方案 1。
- 原因：先确定缺失/错误配置再决定修复，避免无授权环境修改。
- 理解确认：本次仅执行严格只读、脱敏诊断；不自行修复、不重试成样本、不把诊断当验收，不写入真实值或秘密。

## 2026-08-27：M5 Task 5 方案 1 reset 单次诊断结果

### 当前进度与提交

- 当前进度：M5 Task 5 的唯一真实评测命令已按规则停止；首个 `EvaluationRunner.run` 阶段为 `INITIAL_RESET_FAILED`，实际样本为 `0-of-3`，未产生 `run_id`、模型请求或评测 artifacts。
- 当前 commit：`f9c212661be85f471504df202605276e154e90cd`。
- 当前工作树已有根目录 `AGENT.md`、`README.md`、`mistake.md` 未提交改动，以及 `tests/e2e/test_ollama_evaluation.py` 未跟踪文件；本次均保留。

### 问题与候选方案

- 问题：`INITIAL_RESET_FAILED` / `0-of-3`；本轮需要读取一次底层 reset 异常和数据库状态，不重新评测。
- 候选方案：1）只做一次受控、非 Ollama 的 `IncidentLab.reset(CASE_ID)` 诊断；2）直接执行 `pipeline build` 后重跑真实评测；3）暂停并等待后续人为决策。

### 用户选择、原因与执行边界

- 用户选择方案 1。原因：先取得脱敏异常和数据库/Schema 现场，区分 reset 启动链路问题与数据库状态问题，同时不扩大真实模型请求或评测失败样本的影响范围。
- 本次严格只调用一次 `IncidentLab.reset("schema_rename_payment_amount")`；未运行 pytest、eval、`EvaluationRunner.run`、`pipeline build`，未调用 Ollama 或其他模型。
- 受控包装器返回脱敏 `IncidentExecutionError`：健康 baseline 的“加载固定 seeds”无法执行，底层为 Windows `[WinError 2]`（系统找不到指定的文件）。只读环境核对显示直接 Python 包装器继承的当前 PATH 找不到 `dbt`，而 `uv run --no-sync` 环境能够发现项目 `.venv\\Scripts\\dbt.EXE`；因此本次可确认该包装器启动环境的 `dbt` 发现失败，但不能把它单独定性为原始 `uv run eval` 失败的完整根因。
- reset 前后 `analytics.raw_payments` 均为 `amount` 列、113 行，`analytics.stg_payments` 均为 `amount` 列、113 行；`orders`/`customers` 行数仍为 99/100，Schema 仍为健康状态。`.dig/baseline-summary.json`、既有 `.dig/dbt` target/log 文件 SHA-256 与时间戳未改变；active-run 指针仍不存在，未新建 lab run。
- 明确结论：未重新评测、未调用 Ollama、未提交、未推送；本次 `mistake.md` 追加也保持 workspace-only。

### 下一步

- 是否在保持当前工作树和数据库边界的前提下，用正确的 `uv run` 启动环境继续诊断，或转为直接 `pipeline build` 后重跑，或暂停，需人为决策；本代理不自行修复、不再次调用 reset。

## 2026-08-27：M5 Task 5 方案 1 正确 uv run 环境的单次 reset 诊断

### 当前进度、实际 commit 与问题

- 当前进度：M5 Task 5 真实评测此前在首个 `EvaluationRunner.run` 的 `INITIAL_RESET_FAILED` 停止，结果为 `0-of-3`；没有继续样本，也没有模型请求或评测产物。
- 当前实际 commit：`f9c212661be85f471504df202605276e154e90cd`。
- 问题：前次方案 1 只使用直接 Python 包装器，包装器继承的 PATH 找不到 `dbt`，因此只能说明前次包装器启动环境的发现失败，不能解释原始 `uv run` 评测失败。

### 候选方案

1. 使用正确的 `uv run --no-sync` 环境启动窄 Python 包装器，仅做一次 `IncidentLab.reset("schema_rename_payment_amount")` 的非 Ollama 诊断。
2. 执行 `pipeline build` 后重跑真实评测。
3. 暂停并等待后续人为决策。

### 用户选择与选择理由

- 用户选择方案 1。
- 选择理由：先在与原始评测一致的 `uv run` 环境确认 `dbt` 发现和 reset 的真实失败阶段，取得脱敏异常并比较数据库/文件现场；避免未经修复就扩大模型请求、评测样本或环境变更。

### 执行边界与结果待填项

- 执行边界：先做 Git、commit、Docker/Compose、`.venv\\Scripts\\dbt.EXE` 和 `uv run` 环境检查；随后只调用一次指定 `IncidentLab.reset`。不运行 pytest、eval、`EvaluationRunner.run`、doctor、`pipeline build`，不启动 Ollama，不修复代码/测试，不修改依赖、CI、Compose 或 `third_party`，不提交、不推送。
- 结果待填项（执行前）：是否恰好一次 reset、`uv run` 是否找到 dbt、reset 失败阶段及脱敏异常、reset 前后数据库 Schema/行数、Docker、active-run 和本地文件变化。

### 实际结果

- 恰好执行一次 reset：是；包装器输出 `reset_calls=1`，未重试、未调用等价重置。
- `uv run --no-sync` 找到 dbt：是；使用项目 `.venv\\Scripts\\python.exe`，发现 `C:\\Users\\29913\\codex_space\\DataIncidentGym\\.venv\\Scripts\\dbt.EXE`。
- reset 结果：失败，异常类型为脱敏后的 `IncidentExecutionError`；失败阶段为健康 baseline 的“加载固定 seeds”，dbt exit `1`。dbt 已被正确启动，失败发生在 dbt 导入阶段，底层脱敏异常为 `TypeError: 'suppress' object is not iterable`。因此本次可归类为 dbt/Python 依赖或运行时启动问题，不是前次 PATH 找不到 dbt；不能据此归因于 Compose 或数据库。
- reset 前后数据库未变化：`analytics.raw_payments` 和 `analytics.stg_payments` 均为 `amount` 列、113 行；`orders` 为 99 行、`customers` 为 100 行，关键 Schema 均保持健康。
- reset 前后 Docker 未变化：Postgres 容器继续为 healthy；active-run 指针和临时指针均不存在；lab run 目录仍为 148 个，未新建评测或 lab run。
- reset 前后本地 `.dig` 相关子树均为 4,407 个文件，aggregate SHA-256 仍为 `CAC58A28B870E6E53A1B96BFC90DA33B63C7BA6DB984A37C93A3E6E0471223A5`；`baseline-summary.json`、既有 dbt log/manifest/run_results 的大小、时间戳和 SHA-256 未变化。未产生评测 artifacts。
- Git status：仍仅有用户既有的 `AGENT.md`、`README.md`、`mistake.md` 修改，以及 `tests/e2e/test_ollama_evaluation.py` 未跟踪文件；本条记录继续 workspace-only，未提交、未推送。
- 当前结论：正确 `uv run` 环境排除了“包装器 PATH 找不到 dbt”这一局限，但 reset 仍在 dbt/Python 导入阶段失败；本次未证明数据库或 Compose 是根因，也未授权自行修复。

### 后续人为决策

是否允许修复/更换 dbt 或相关 Python 依赖后再验证，或选择执行 `pipeline build` 后重跑真实评测，或继续暂停，仍需要人为决策；本次诊断不作自动选择。

## 2026-08-27：M5 方案 1 dbt/Python 依赖兼容性只读调查

### 当前进度、实际 commit 与问题

- 当前进度：M5 真实评测在唯一一次 `INITIAL_RESET` 固定 seeds 阶段失败后按规则停止，当前评测结果仍为 `0/3`；本次只读调查不重试评测、不新增样本。
- 当前实际 commit：`f9c212661be85f471504df202605276e154e90cd`。
- 调查开始时工作树已有 `AGENT.md`、`README.md`、`mistake.md` 修改，以及未跟踪的 `tests/e2e/test_ollama_evaluation.py`；均未回退、未修改。
- 问题：正确 `uv run` 环境下的单次 reset 在健康 baseline 加载固定 seeds 的 dbt 导入阶段 exit `1`，脱敏异常为 `TypeError: 'suppress' object is not iterable`；对抗审查所指的链路为 `dbt-core -> mashumaro.core.meta.helpers.is_unpack`。

### 候选方案与用户决策

1. 只读调查 dbt/Python 依赖、元数据、导入链和官方一手兼容性说明，不修改环境。
2. 在取得人为授权后修改/重锁 dbt、mashumaro、typing-extensions 或相关依赖，再做单变量验证；风险是改变锁定环境、引入新兼容性或影响后续评测可比性。
3. 切换 WSL/Linux 或其它受支持运行时后再验证；风险是改变操作系统、Python/二进制运行时和现有评测基线，不能直接证明当前 Windows 锁定环境已修复。
4. 暂停，保留 `0/3` 和现有证据，等待下一步人为决策。

- 用户选择方案 1。
- 选择理由：先区分“声明依赖冲突”与“特定 dbt 子进程/运行时故障”，不扩大真实模型请求、评测失败样本或环境变更；当前规则也禁止在 `0/3` 后重试。
- 执行边界：仅读取 HEAD、status、`pyproject.toml`、`uv.lock`、安装元数据、相关源码和既有日志/决策记录；仅执行 `uv run --no-sync dbt --version`、Python 版本/metadata/import 查询、包完整性查询和 `uv pip check`。不运行 `dbt debug/seed/run/build/test`、pipeline build、lab reset、pytest、doctor、EvaluationRunner、Ollama/其它模型请求，不连接或写数据库；不运行 `uv sync`、`uv lock`、安装/升级/降级依赖，不切换 Python，不改 `pyproject.toml`、`uv.lock`、源码、CI、compose 或 `third_party`。本次只追加本文件，不提交、不推送。

### 本地调查结果

- `pyproject.toml` 实际要求 Python `>=3.12,<3.13`，直接固定 `dbt-core==1.12.3`、`dbt-postgres==1.11.0`；`uv.lock` 的 requires-python 为 `==3.12.*`，锁定 `mashumaro==3.17`、`typing-extensions==4.16.0`。
- `uv run --no-sync` 实际使用 `.venv\Scripts\python.exe`，Python 为 `3.12.10`；metadata 查询得到 `dbt-core 1.12.3`、`dbt-postgres 1.11.0`、`dbt-adapters 1.24.5`、`dbt-common 1.39.0`、`mashumaro 3.17`、`typing-extensions 4.16.0`、`pydantic 2.13.4`。
- `uv run --no-sync dbt --version` exit `0`，输出 Core `1.12.3`、Postgres `1.11.0`；仅因当前联网查询不可用而不能查询 latest。精确导入检查 `dbt.cli.main`、`dbt_common.dataclass_schema`、`mashumaro.core.meta.helpers.is_unpack`、`mashumaro.jsonschema.models.JSONObjectSchema` exit `0`，输出 `imports OK`。
- `is_unpack` 在当前 Python 3.12 的 `typing.Unpack`、`typing_extensions.Unpack` 和普通类型上均返回预期布尔值；当前 `mashumaro/core/meta/helpers.py` 中 `is_unpack` 的实现使用 `for module in (typing, typing_extensions)` 和 `with suppress(AttributeError)`，直接导入无异常。
- `uv pip check --python .venv\Scripts\python.exe` 报告 `Checked 91 packages`、`All installed packages are compatible`。安装记录中 `mashumaro/core/meta/helpers.py`、`mashumaro/jsonschema/models.py`、`typing_extensions.py`、`dbt_common/dataclass_schema.py` 的 SHA-256 均与各自 `RECORD` 记录一致；未发现这些关键文件被本地改写的证据。
- 因此，在允许的版本/导入探针中不能复现该 TypeError：`dbt --version` 和上述完整导入链均成功。该结果不能否定此前 reset 子进程曾报告的异常，只能说明它不是当前同一 `uv run --no-sync` 探针下的稳定导入失败。

### 依赖关系、官方资料与证据边界

- dbt-core v1.12.3 官方 `core/pyproject.toml` 明确要求 `mashumaro[msgpack]>=3.9,<3.18`、`typing-extensions>=4.4`，并声明支持 Python 3.10–3.14：<https://github.com/dbt-labs/dbt-core/blob/v1.12.3/core/pyproject.toml>。
- mashumaro v3.17 官方 `pyproject.toml` 明确要求 Python `>=3.9`、`typing_extensions>=4.14.0`，并列出 Python 3.12–3.14：<https://github.com/Fatal1ty/mashumaro/blob/v3.17/pyproject.toml>。其官方 v3.17 发布说明写明新增 Python 3.14 支持：<https://github.com/Fatal1ty/mashumaro/releases/tag/v3.17>。
- dbt-core 官方 v1.12.3 release notes 记录的是 SQL parser 安全版本约束等发布内容，没有记录本次精确的 `suppress` TypeError：<https://github.com/dbt-labs/dbt-core/releases/tag/v1.12.3>。
- dbt-core 官方 issue #12098 记录了另一类 Python 3.14 与旧 mashumaro 上界导致的 `UnserializableField` 导入失败，并提到 mashumaro 3.17 的修复；该 issue 的 Python 版本、异常类型和当前环境不同，只能作为“dbt 深度依赖 mashumaro、历史上存在运行时兼容边界”的旁证，不能作为本次 TypeError 的根因证明：<https://github.com/dbt-labs/dbt-core/issues/12098>。
- 综上，最可能的冲突关系仍是 dbt-core 深层导入与 mashumaro/typing 运行时之间的兼容 seam，但当前证据不支持把它表述为已确认的声明版本冲突：当前 Python 3.12.10、mashumaro 3.17、typing-extensions 4.16.0 均落在官方约束内，解析器检查通过，关键文件完整，且允许的导入探针成功。更窄的工作假设是：失败 reset 子进程存在本探针未捕获的进程级运行时/环境差异或瞬态状态；需要获得新授权后才能通过变更依赖或切换运行时验证，不能靠本轮静态证据定性。

### 当前文件、数据库与评测状态

- 本轮只追加 `mistake.md`；未修改 `pyproject.toml`、`uv.lock`、源码、CI、compose、`third_party`，未提交、未推送。现有 `AGENT.md`、`README.md` 和未跟踪测试文件保持原样。
- 本轮未连接数据库，因此没有数据库写入。最近一次已记录的单次 reset 现场仍为健康 Schema：`analytics.raw_payments`/`analytics.stg_payments` 使用 `amount` 列且各 113 行，`orders` 99 行、`customers` 100 行；该现场未因本轮只读探针改变。
- 评测状态仍为 `0/3`，无新增模型请求、无新增 run_id、无新增评测 artifacts；既有 reset 失败及其脱敏异常仍是当前失败证据。

### 结论与下一步

- 结论：在 `uv run --no-sync dbt --version` 和对应 dbt/mashumaro 导入链中，`TypeError: 'suppress' object is not iterable` 均未复现；当前锁定组合没有被官方约束判定为不兼容，也没有找到官方对该精确异常的说明。
- 最小候选修复（本次不执行）：在新的、人为批准的验证轮次中只改变一个依赖变量，优先评估 dbt-core 官方允许范围内的 mashumaro/typing 组合或 dbt-core patch/minor 组合，并重新生成/审核锁文件后做最小启动验证；任何具体版本选择、是否修改依赖以及是否切换 WSL/Linux 都需先由人决定。降级 Python、绕过 dbt 导入、放宽解析或重试真实评测均不属于本轮授权。
- 下一步必须人为决策：允许修改/重锁依赖后验证，或批准切换 WSL/Linux 运行时后验证，或继续暂停；在决策前不重试评测、不再次 reset、不修改依赖。

## 2026-08-27：M5 方案 1 第一阶段健康基线 pipeline build

### 当前进度、实际 commit 与问题

- 当前进度：M5 真实评测此前在健康 baseline 的 dbt seeds 阶段失败，结果为 `0/3`；失败异常为脱敏后的 `TypeError: 'suppress' object is not iterable`。此前只读调查未能在 `dbt --version`/导入探针中稳定复现。
- 当前实际 commit：`f9c212661be85f471504df202605276e154e90cd`。
- 执行开始前工作树已有用户改动：`AGENT.md`、`README.md`、`mistake.md`，以及未跟踪的 `tests/e2e/test_ollama_evaluation.py`；本轮未回退或改写这些内容。

### 候选方案与用户决策

1. 保持当前锁定依赖，使用当前 `uv` 环境执行一次健康基线 `pipeline build`。
2. 修改/重锁 dbt 或相关 Python 依赖后再验证。
3. 切换 WSL/Linux 或其他环境，或暂停等待后续决策。

- 用户选择方案 1。
- 选择理由：先在不改变依赖和运行环境的前提下确认健康 baseline pipeline 是否能完整通过，再决定是否具备进入三样本评测的前置条件；避免在原因未定时扩大真实模型请求或改变评测可比性。

### 执行边界与实际结果

- 执行边界：先只读检查 Git status、HEAD、Docker/Compose 和诊断配置；随后仅执行一次 `uv run --no-sync data-incident-gym pipeline build`。不执行第二次 pipeline build、lab reset、pytest、eval、`EvaluationRunner`、doctor、额外 dbt 命令、Ollama/model 请求或任何 M5 评测；不修改 Python、依赖、lock、CI、Compose、`third_party`、测试文件或系统配置，不使用 affinity 绕行，不清理 artifacts/logs，不提交、不推送。
- pipeline build 恰好执行一次：是。exit code：`0`。
- 结果：健康基线构建成功。项目 pipeline 的 PostgreSQL 启动、`seed --full-refresh`、`dbt build`、只读角色 provision、健康校验和 baseline summary 写入均完成；未出现本轮脱敏错误。
- 成功摘要：schema 为 `analytics`，relations 为 `8`，fingerprint 为 `e5c7848eb2b7af16ec37463650b63dbb5d1f52293adf456003517b51c496cb18`。现有 `.dig/dbt/target/run_results.json` 显示 28 个结果，其中 `pass=20`、`success=8`。
- baseline/artifacts：`.dig/baseline-summary.json` 已写入/更新并保留；`.dig/dbt/logs/dbt.log`、`.dig/dbt/target/run_results.json` 及其他已有 `.dig` artifacts/logs 均保留，未清理。
- 数据库：Compose 中的 PostgreSQL 容器仍为固定 digest，`docker compose ps` 显示 `Up` 且 `healthy`；pipeline 健康校验成功。未执行额外数据库查询。
- Git status：仍仅为 `AGENT.md`、`README.md`、`mistake.md` 修改，以及 `tests/e2e/test_ollama_evaluation.py` 未跟踪；未修改依赖、源代码、lock、CI、Compose、`third_party` 或测试文件。本记录仅追加于 `mistake.md`，未提交、未推送。

### 结论与下一步

- 健康 baseline pipeline 已成功完成，因此已满足“健康数据库/基线产物可用”这一进入三样本评测的前置条件；本轮本身没有执行三样本评测，也没有证明此前 `0/3` 的 dbt seeds TypeError 已在评测流程中消失。
- 是否进入三样本评测仍需后续人为决策；若后续选择不评测或要修改依赖/切换环境，也必须重新确认边界。本轮不自行修复、不自行评测。

## 2026-08-27：M5 Task 5 严格三样本真实 Ollama 评测停止记录

### 当前进度、实际 commit 与问题

- 当前进度：健康 pipeline build 已按前置条件恰好执行一次并 exit `0`，fingerprint 为 `e5c7848eb2b7af16ec37463650b63dbb5d1f52293adf456003517b51c496cb18`；随后执行 M5 Task 5 的唯一一次启用真实评测 pytest，首个 workflow 在 `INITIAL_RESET` 阶段失败，按停止规则停止。
- 当前实际 commit：`f9c212661be85f471504df202605276e154e90cd`。
- 问题与此前 `0/3`：本次 pytest 只调用了一个 `EvaluationRunner.run()`，在 reset 尚未成功时抛出 `EvaluationWorkflowError: INITIAL_RESET_FAILED`；未进入 inject、build 或 diagnose，未产生可计入分母的 `DiagnosisRunResult`。因此实际完成的可审计真实样本为 `0/3`，不能声称三样本完成；此前 reset/dbt 阶段的 `0/3` 失败记录仍保留，本次未通过重试掩盖。

### 候选方向与用户决策

1. 保持当前锁定依赖，在健康基线 pipeline build 成功后执行严格三个真实 `gemma4:e4b` 独立样本。
2. 修改或重锁 dbt/Python 依赖后再验证；风险是改变既定评测环境和可比性。
3. 切换 WSL/Linux 或其他运行时后再验证；风险是改变操作系统和运行时边界。
4. 暂停，保留当前失败证据并等待后续人为决定。

- 用户选择方向 1：保持依赖不变，pipeline build 成功后进行严格三样本评测。
- 选择理由：先证明健康数据库、Schema、PostgreSQL 和 dbt 产物满足前置条件，同时保持锁定依赖和评测可比性；只在此前提下执行固定模型、固定样本数的真实验收，避免未经授权扩大依赖变更或模型请求。

### 严格三样本边界与实际结果

- 静态边界核对通过：`tests/e2e/test_ollama_evaluation.py` 使用生产 `EvaluationRunner.for_project(...).run(CASE_ID)`，循环固定为 `range(3)`，配置和 metadata 固定 `gemma4:e4b`，无 retry、重复、第四次、模型切换、Fake、TestModel 或其他评测入口；测试要求三个唯一 run ID、六文件完整性和至少 `2/3` 通过。
- 唯一真实命令：临时设置 `DIG_RUN_OLLAMA_TESTS=1` 后执行 `uv run --no-sync pytest tests/e2e/test_ollama_evaluation.py -q -s`，pytest 恰好执行一次，exit `1`；命令输出完整记录 `EvaluationWorkflowError: INITIAL_RESET_FAILED` 和 `1 failed in 23.69s`。未设置 affinity。
- 实际启动次数：`EvaluationRunner.run()` workflow 启动 `1` 次；可审计样本 `0` 次。由于首个 reset 失败，没有 `run_id`、`EvaluationStatus`、`DiagnosisStatus`、root cause 或 evidence 可记录，也不能宣称启动了三次真实样本。
- attempt artifacts：评测后 `artifacts/` 目录不存在，因此没有可清理、覆盖或审计的 attempt artifact；没有生成六文件 bundle。不存在的 artifact 不伪造路径或完整性结果。
- 外层测试 `finally lab.reset(CASE_ID)` 完成环境清理；`DIG_RUN_OLLAMA_TESTS` 已恢复为未设置。未运行第二/第三次 pytest、单独 eval run、doctor、pipeline build、lab reset、额外 pytest、dbt 写命令或任何模型替代调用。
- 按停止规则：不修复、不重试、不换模型、不放宽 `2/3` 门槛、不修改分母；保留本次完整 pytest 输出和现有工作树证据，等待人为决定。

### Git 与文件边界

- 本条仅追加于 workspace-only `mistake.md`，不得 stage、commit 或 push。
- `AGENT.md`、`README.md` 的用户修改和未跟踪 `tests/e2e/test_ollama_evaluation.py` 均保留；不提交依赖、CI、Compose、`third_party`、`.dig` 或 artifacts。

### 唯一 pytest 的完整输出

```text
F
================================== FAILURES ===================================
__ test_default_ollama_passes_at_least_two_of_three_independent_evaluations ___

    @pytest.mark.asyncio
    async def test_default_ollama_passes_at_least_two_of_three_independent_evaluations() -> None:
        settings = Settings(_env_file=None)
        diagnostic_settings = DiagnosticSettings(
            _env_file=None,
            model_base_url="http://127.0.0.1:11434/v1",
            model_name="gemma4:e4b",
        )
        attempts = []
        lab = IncidentLab(settings, PROJECT_ROOT)
        try:
            for _ in range(3):
>               result = await EvaluationRunner.for_project(
                    settings,
                    diagnostic_settings,
                    PROJECT_ROOT,
                ).run(CASE_ID)

tests\e2e\test_ollama_evaluation.py:138:
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

self = <data_incident_gym.evaluation_runner.EvaluationRunner object at 0x00000157890C7B60>
incident_case_id = 'schema_rename_payment_amount'

    async def run(self, incident_case_id: str) -> EvaluationAttemptResult:
        started_at = self._clock()
        ground_truth_error = False
        try:
            ground_truth = self._ground_truth_loader(incident_case_id)
            if not isinstance(ground_truth, GroundTruth):
                raise ValueError("invalid Ground Truth")
        except Exception:
            ground_truth_error = True
        if ground_truth_error:
            raise EvaluationWorkflowError("GROUND_TRUTH_LOAD_FAILED") from None

        mutation_started = False
        fault_run: FaultRun | None = None
        diagnosis_run: DiagnosisRunResult | None = None
        primary_error_code: str | None = None
        recovery_succeeded = False
        stage = "INITIAL_RESET"

        try:
            self._lab.reset(incident_case_id)
            mutation_started = True
            stage = "INJECT"
            self._lab.inject(incident_case_id)
            stage = "BUILD"
            fault_run = self._lab.build(incident_case_id)
            stage = "DIAGNOSIS_SETUP"
            diagnosis_runner = self._diagnosis_factory(fault_run.run_id)
            stage = "DIAGNOSIS"
            diagnosis_run = await diagnosis_runner.diagnose(incident_case_id)
        except (LabError, IncidentCaseError, RunContextError):
            primary_error_code = f"{stage}_FAILED"
        except Exception:
            primary_error_code = f"{stage}_FAILED"
        finally:
            if mutation_started:
                try:
                    self._lab.reset(incident_case_id)
                    recovery_succeeded = True
                except Exception:
                    recovery_succeeded = False

        if diagnosis_run is None or fault_run is None:
>           raise EvaluationWorkflowError(primary_error_code or "WORKFLOW_FAILED") from None
E           data_incident_gym.evaluation_runner.EvaluationWorkflowError: INITIAL_RESET_FAILED

src\data_incident_gym\evaluation_runner.py:140: EvaluationWorkflowError
=========================== short test summary info ============================
FAILED tests/e2e/test_ollama_evaluation.py::test_default_ollama_passes_at_least_two_of_three_independent_evaluations
1 failed in 23.69s
REAL_EVAL_EXIT=1
```

## M5 Task 5 reset 失败修复决策（2026-08-27）

- 当前进度：两次真实 M5 评测均在第一个 `EvaluationRunner.run` 的 `INITIAL_RESET_FAILED` 停止，结果为 0/3；未产生 Ollama 请求或 artifacts。本次修复后未重新执行真实评测。
- 当前实际 commit：`fd38de85b955855818deea14c938e68e58f70b54`。
- 问题：`EvaluationRunner` 只保留阶段码并吞掉 reset 底层异常；未跟踪 e2e 测试的 `finally` 无条件独立 `lab.reset`，可能覆盖初始失败现场。现有 `mutation_started` 机制已能在确实进入注入流程后恢复。
- 候选方案：方案 1，先做最小代码/测试修复并重新获得可观测性；方案 2，直接重跑真实三样本或扩大环境/依赖调查。
- 用户选择方案 1：因为当前失败发生在评测前置 reset，先修复安全诊断和 cleanup 语义，避免在根因不可区分、现场可能被覆盖时增加评测样本或改变统计口径。
- 修复边界：新增固定白名单映射的 `diagnostic_code`，仅由异常类型/稳定错误码生成；保留现有阶段 `.code`、状态语义、真实样本数量、模型、重试规则和数据库安全边界。CLI 只显示阶段码与安全原因码，不显示异常文本、stderr、路径、密码、环境变量或 traceback。e2e 移除独立 finally reset，依赖 `EvaluationRunner` 的 `mutation_started` 恢复。
- 结果：相关 unit tests 40 项通过，相关 Ruff 与 diff 检查通过；未运行 reset、真实 DB/模型命令、pipeline build、doctor、e2e 或 eval。`AGENT.md`、`README.md`、`mistake.md` 未进入修复 commit。

## 2026-08-27：M5 方案 1 恢复协议与 diagnostic_code 修复完成

- 当前进度：已执行用户选择的方案 1。初始 reset 失败现在也只尝试一次明确 recovery；恢复失败不覆盖主阶段错误或安全诊断码，不产生评测 retry、第四次样本或模型调用。本次未运行真实评测，既有真实 M5 状态仍为 0/3、无新增模型请求或 artifacts。
- 当前实际 commit：`43a9266`（`fix: harden evaluation recovery protocol`）。提交仅包含 `src/data_incident_gym/evaluation_runner.py`、`tests/unit/test_evaluation_runner.py`、`tests/unit/test_cli.py`；`AGENT.md`、`README.md`、`mistake.md` 保持未提交。
- P0：`IncidentLab.reset` 可能先完成字段改名、再在健康 baseline 失败；旧 runner 在 reset 返回前不设置 mutation 标志，可能跳过恢复并留下部分 mutation。已将 runner 标志改为进入 reset 调用前即要求 recovery，且 finally 只执行一次。
- P1：`EvaluationWorkflowError` 原先接受任意 diagnostic_code 并由 CLI 原样输出。已在公共入口按固定白名单归一化未知、超长、含秘密/路径或其他任意字符串为 `UNEXPECTED_ERROR`；合法诊断码保持兼容，不输出原始异常、stderr、路径、traceback 或环境变量。
- 候选方案：方案 1 是 runner 在 reset 前置 recovery 标志；候选方案 2 是由 IncidentLab 暴露细粒度 mutation progress。选择方案 1 是因为它是当前公共接口下最小改动，覆盖 reset 内部任意部分 mutation 失败，不改变 Lab 接口或评测语义。
- 实施边界：未修改 `tests/e2e/test_ollama_evaluation.py`（当前已无独立外层 reset/finally）、依赖/lock、CI、compose、third_party 或其他源文件；未执行 reset、pipeline build、doctor、e2e、eval、真实 EvaluationRunner、dbt 写操作或 Ollama。
- 测试结果：相关 runner/CLI unit `47 passed`；全量 unit `403 passed, 6 skipped`；`uv run --no-sync ruff check src tests` 通过；`git diff --check` 通过。覆盖初始 reset 部分 mutation 模拟、recovery failure 主错误保留、各失败阶段 recovery 次数、恢复失败导致 FAILED、恶意 diagnostic_code 异常/CLI 脱敏和合法码兼容。
- 提交时 Git 报告一次已有 refs/codex checkpoint 的 `bad object`/geometric repack 警告，但 commit 已成功创建；需留意仓库对象维护状态。未 push。
- 后续按“不 amend 旧提交”要求创建测试增强提交：`666927f`（`test: model partial reset mutation failure`），最终 HEAD 为 `666927f`；该提交只补充 fake lab 的合成 `partial_mutation` 事件及断言，未改变运行时代码。

## 2026-08-27：严格恢复安全语义修复（用户确认）

- 当前进度：已完成用户确认的严格恢复安全语义修复；当前实际 commit 为 `def9ac5`（`fix: enforce healthy evaluation recovery`），HEAD 已从 `666927f` 前进到该提交。既有 `AGENT.md`、`README.md` 和本文件的未提交修改均保留，未纳入修复 commit。
- 问题：此前 runner 虽在初始 `reset` 调用前设置 recovery-needed，但只按 reset 是否抛异常判定恢复成功，未验证返回的最终 `state`；因此非 `HEALTHY` 返回可能被当作成功。`EvaluationWorkflowError.code` 仍可由公共构造入口原样注入，且三样本 e2e 只统计诊断 `2/3`，没有把每个 attempt 的 recovery 健康作为总验收硬门槛。
- 候选方案：方案 1，在现有 `IncidentLab.reset`/`ResetResult.state` 公共接口上由 runner 精确验证 `HEALTHY`，并在 e2e 保留诊断 `2/3` 的同时强制所有 attempt 的 recovery 为 `HEALTHY`；方案 2，扩展 IncidentLab 暴露更细粒度 mutation-progress 或新增多样本聚合公共架构。
- 用户选择严格语义及原因：采用方案 1。`2/3` 只用于诊断质量；任意 recovery 失败或最终状态非 `HEALTHY` 都使总验收失败。方案 1 是当前接口下最小、可审计且不扩大公共架构的实现，可覆盖 reset 部分 mutation 后失败和“无异常但最终状态非健康”两类路径。
- 实施边界：runner 在每次初始 reset 调用开始前进入 recovery-needed；初始 reset 失败最多执行一次 recovery，recovery 失败不覆盖 primary stage/error；Ground Truth preflight 失败仍不 reset；inject/build/diagnosis setup/diagnosis 失败仍只执行一次 recovery；不新增样本、retry、第四次样本或模型请求。新增 recovery state 精确检查和 evaluation recovery-check 一致性检查；`EvaluationWorkflowError.code` 与 `diagnostic_code` 均限制到固定安全集合，未知/超长/含密码、路径、stderr 或 traceback 的输入只归一化为固定安全值，保留合法既有码兼容。e2e 断言每个 bundle 的 `RecoveryStatus.HEALTHY`，同时保留 `2/3` 诊断质量断言。未修改 `lab.py`、依赖/lock、CI、compose、third_party、AGENT.md 或 README.md；未清理 `.dig/artifacts`。
- 测试结果：相关 runner/CLI unit `53 passed`；全量 unit `409 passed, 6 skipped`；`uv run --no-sync ruff check src tests` 通过；`git diff --check` 通过。覆盖 partial mutation 后 reset 失败恢复、初始失败/recovery 失败各一次、primary error 优先、Ground Truth 不 reset、inject/build/diagnosis 失败恢复、恶意 code/diagnostic_code 脱敏、诊断质量不能掩盖 recovery failure、最终非 `HEALTHY` 失败及真实状态转换。按本轮边界未运行真实 reset、DB/dbt 写操作、pipeline build、doctor、e2e/eval、Ollama 或其他模型请求。
- 未验证内容：本轮没有运行真实三样本评测，因此仍不能声称真实 `gemma4:e4b` 评测达到 `2/3`，也没有验证真实数据库在 reset 部分 mutation 后的线上恢复表现。提交时 Git 另报告既有 `refs/codex/... bad object`/geometric-repack 警告，但 commit 已成功创建；未处理该无关仓库维护问题。

## M5 Task 5 严格三样本真实 Ollama 评测结果（2026-08-27）

- 当前进度：已按用户选择执行一次严格三样本真实评测；恰好启动并完成 3 次独立生产 `EvaluationRunner.run`，未重试、未换模型、未执行第四次，所有 artifacts 保留。总验收失败，诊断质量为 `0/3`，低于要求的 `2/3`。
- 当前实际 commit：`def9ac5`（`fix: enforce healthy evaluation recovery`）。评测前确认工作树已有的 `AGENT.md`、`README.md`、`mistake.md` 用户改动均未覆盖；本条记录仅追加到 `mistake.md`，不提交、不推送。
- 此前问题：修复前两次真实评测均在第一个 `INITIAL_RESET_FAILED` 停止，结果为 `0/3`，未产生模型请求或 artifacts。本次为修复后的首次新评测，样本未与旧样本混淆。
- 候选方案：方案 1，按用户授权立即执行固定 `gemma4:e4b` 的严格三样本真实评测；方案 2，继续静态调查或修改代码后再评测。
- 用户选择方案 1：要求现在获得修复后的真实证据，固定模型、恰好三个独立样本、不重试/不换模型/不执行第四次；任一 recovery 失败或最终状态非 `HEALTHY` 均失败，并保留全部 artifacts。
- 实际命令与结果：
  ` $env:DIG_RUN_OLLAMA_TESTS = '1'; uv run --no-sync pytest tests/e2e/test_ollama_evaluation.py -q -s `
  exit code=`1`，pytest 报 `1 failed in 308.73s`，失败断言为 `0 >= 2`。
- 样本明细（模型请求均已发生；每个 artifact 目录恰有六个规范文件 `metadata.json`、`trace.jsonl`、`evidence.json`、`diagnosis.json`、`evaluation.json`、`report.md`）：
  - `run_id=d8213aa2a495452f88f6d5b250285c37`；目录 `artifacts/d8213aa2a495452f88f6d5b250285c37`；`EvaluationStatus=FAILED`；`DiagnosisStatus=MODEL_ERROR`；root cause 无；evidence IDs=`ev_0272f6c54f7a74499e74863bf5d8561840db15e7adc84f59f02266b6e5042599`,`ev_e80d765963cfec7e300ab34e501e069d617445f84bff9c9d221712e78f916765`,`ev_9314e139f0db84ce920ca36f0c575302d0f61631fc649bbc09d2bd96dd4ab0a6`；摘要=`MODEL_REQUEST_LIMIT`；模型请求=6。
  - `run_id=85c23b7d973a4b32b8c4497aaccde067`；目录 `artifacts/85c23b7d973a4b32b8c4497aaccde067`；`EvaluationStatus=FAILED`；`DiagnosisStatus=MODEL_ERROR`；root cause 无；evidence IDs=`ev_00c3daff985c09700e66ec5680b38d91a026220aeacf297feb0bd899be0c000f`,`ev_1c2da255fb8f1a654b84f4f234387097b652062a409e911a6cff7135bdd1ebe2`,`ev_8d22b2c04a41949661c1dce570036a44832c12fa137909b6831ab3c809a230e0`；摘要=`MODEL_REQUEST_LIMIT`；模型请求=6。
  - `run_id=13efbf6c5d0343a2a0d02589d1ae017b`；目录 `artifacts/13efbf6c5d0343a2a0d02589d1ae017b`；`EvaluationStatus=FAILED`；`DiagnosisStatus=MODEL_ERROR`；root cause 无；evidence IDs=`ev_a2eb15f3f726881813678b4319ec5446768a15122732ba22853e6aa45d518dd0`,`ev_3afb9ce242a9f22ad29d811f247b4922a247d1baa89f38be47eca6e36d74f170`；摘要=`MODEL_PROTOCOL_ERROR`；模型请求=6。
- recovery/最终健康门禁：三次 artifact 的 `recovery_status=HEALTHY`，`RECOVERY_HEALTHY=True`，最终健康门禁全部通过；没有 recovery failure 或最终非 `HEALTHY`。但每次 `DIAGNOSIS_CONFIRMED=False`，三次评测均 FAILED，严格总验收仍失败；诊断质量为 `0/3`。
- 未验证内容：未运行 doctor、pipeline build/reset 单独命令、其他 pytest/e2e/eval、ruff 或额外静态检查；未修改代码、未清理 artifacts、未提交或推送。本次结果不能证明真实 Ollama 评测达到 `2/3`，也未进一步定位模型请求上限/协议错误的根因。

## 2026-08-27：M5 通用工具接口、参数语义与安全协议审计修复

- 当前进度：已执行用户确认的 M5 修复方向。当前实际 commit（修复前）为 `def9ac5`（`fix: enforce healthy evaluation recovery`）；本轮未调用真实模型，未执行真实 EvaluationRunner、Ollama、doctor、e2e、pipeline、reset 或数据库写操作。
- 0/3 问题与边界：严格三样本真实评测仍保留为诊断质量 `0/3`；两次 `MODEL_REQUEST_LIMIT` 与一次 `MODEL_PROTOCOL_ERROR`、recovery/最终健康通过的旧结果不修改、不覆盖，旧版三个 artifact 目录永久保留。本轮不新增真实样本，不改变评分口径，不以内部 retry 替代样本。
- 候选方案：方案 1，在既有四个 M3 只读工具和三终态合同内修复公开参数语义、稳定错误协议、通用 v2 prompt，并加入仅保存边界元数据的请求/终态审计；方案 2，新增工具、fallback、模型专属行为或直接重跑真实评测。
- 用户选择方案 1 及原因：只修复通用工具 Interface、参数语义和协议/请求审计，保持真实模型 `gemma4:e4b` 与 6/8/2 请求/工具/结构化输出预算，避免把当前案例名称、列名、资产答案、expected evidence ID 或 Ground Truth 注入 Agent。
- 实施内容：四工具描述明确通用调用顺序；node/relation 参数要求来自先前结构化结果，relation 使用精确未限定名；非法关系返回稳定可行动错误码/消息；provider 异常不进入工具错误、trace 或 metadata。prompt 版本升级为 `m5.diagnosis.v2`，只保留跨案例工具协议与通用 root-cause ontology。
- 审计内容：trace 增加无 prompt/completion/隐藏推理/原始 provider 异常/原始 SQL/敏感路径的模型请求序号、耗时、usage、稳定错误码事件，以及固定预算、tool retry=1、output retry=2、实际 retry 计数和终态摘要；metadata 同步记录 v2 prompt hash、预算快照和扩展后的安全 metrics。审计不扩大 6/8/2，也不改变三终态。
- 测试结果：相关 unit 与新增静态约束通过；全量 unit 为 `410 passed, 6 skipped`；`uv run ruff check src tests/unit` 通过；`git diff --check` 通过。覆盖四工具注册/参数描述、非法关系与稳定错误、v2 prompt 禁止案例常量/Ground Truth、模型请求序号、预算快照、tool/output retry 审计和敏感哨兵不进入结果。本轮未对旧 artifact 三目录执行任何写操作。
- 未验证内容：本轮不证明真实 `gemma4:e4b` 诊断质量已从 `0/3` 提升，也不改变真实复验授权要求；后续真实复验必须另行授权并使用新的 prompt 版本。`AGENT.md`、`README.md` 与本文件既有用户修改保持未提交；本记录只追加，不提交、不推送。

- 提交结果：本轮直接相关修复已提交为 `c8d65b0`（`fix: harden diagnosis tool protocol audit`），未 amend、未 push；`AGENT.md`、`README.md`、`mistake.md` 仍保持未提交。提交时 Git 报告既有 `refs/codex/...` 坏对象/geometric-repack 警告，但 commit 已成功创建，本轮未处理该无关仓库维护问题。

## 2026-08-27：M5 c8d65b0 独立审查后的通用最小修复

- 当前 checkout 基线：`c8d65b0`；保留根 `AGENT.md`、`README.md`、本文件既有未提交修改，未修改依赖/lock/CI/compose/third_party。
- 独立审查结论：无 P0；本轮只处理工具重试上限与审计计数、node error 通用安全归一化、输出重试与 evaluator audit-field 安全扫描；参数 provenance 和 evaluator 异常时 artifact 语义按用户要求保留，等待决策。
- 自动修复范围：新增模型工具请求与实际执行计数；第 9 个模型工具请求只记录审计并不执行；重复工具请求最多保留一次内部 retry，超限保持 `MODEL_ERROR`/`MODEL_REQUEST_LIMIT` 终态；结构化输出解析 retry 纳入 2 次计数；四工具 node error 使用统一安全消息归一化；evaluator 扫描 trace audit/metrics 字段且不回显不安全原文。未调用真实模型、Ollama、doctor、e2e、pipeline、reset 或数据库写操作。
- 验证：全量 unit `415 passed, 6 skipped`；`ruff check src tests/unit` 与 `git diff --check` 已通过。修复只提交 `src/tests`，不 push。

## 2026-08-27：M5 69877d9 独立审查后的最小安全协议修复

- 当前进度：基于实际 `HEAD=69877d9d87bcdcec1f2e54c67f5d84b0b5778ece`、计划和独立审查报告完成本轮修复；保留根 `AGENT.md`、`README.md` 与本文件既有未提交修改，未修改依赖/lock、CI、compose、third_party。
- 问题：evaluator 允许 metrics/audit 同步伪造模型请求与 token，对 `RUN_AUDIT` 只校验唯一不校验尾部，对 retry index 不校验同 fingerprint 的前序 0；工具上限与模型请求上限审计终态混淆；四个只读工具仍可能把恶意 artifact/catalog 标识符带入结构化 EvidenceRecord/trace。
- 修复选择：采用当前既有接口下的最小修复；直接按 trace 事件计数和 token 求和、强制唯一尾部 audit、验证 retry fingerprint 前序、保留 `DiagnosisStatus` 三终态及 6/8/2 预算；工具输出采用稳定 `UNSAFE_*` 分类并继续保留安全标识符，工具上限审计码使用既有 `TOOL_RETRY_LIMIT`/`TOOL_CALL_LIMIT`，模型请求上限保持 `MODEL_REQUEST_LIMIT`。
- 执行边界：仅修改必要 `src/tests`，未调用真实模型、Ollama、doctor、e2e、pipeline、reset 或数据库写操作；参数 provenance 与 evaluator 异常时六件 artifact 持久化仍留待用户决定；本记录仅追加，不纳入修复提交、不 push。
- 验证：相关 evaluator/evidence-tools/diagnostic-agent unit `105 passed`；全量 unit `434 passed, 6 skipped`；`uv run --no-sync ruff check src tests/unit` 通过；`git diff --check` 通过。

## 2026-08-27：M5 9f99904 后安全协议最小修复

- 当前基线：实际 `HEAD=9f99904`，根 `AGENT.md`、`README.md`、`mistake.md` 已有未提交修改；已核对 M5 计划、根文档和此前 69877d9 后独立审查结论。本轮未运行真实模型/Ollama/doctor/e2e/pipeline/reset、数据库写操作或真实评测。
- evaluator 修复：模型请求与 token 总量继续从原始 `MODEL_REQUEST` 事件重算；工具请求/执行/成功/重试从原始 `TOOL_CALL` 事件重算；retry 需要同 fingerprint、同工具和同参数的前序连续序列；禁止重复 retry index；`output_retry_count` 必须与 trace 中有前后模型请求的 `OUTPUT_RETRY` 事件相符；终态码由原始终态事件/请求预算推导并与 DiagnosisStatus、metrics、RUN_AUDIT 绑定；`RUN_AUDIT` 唯一且最后保持。
- 工具安全修复：四个只读工具对 node/relation/schema/column/type/name 等输出字段采用结构规则和通用 instruction-vocabulary 归一化为 `UNSAFE_*`，不新增案例答案、Ground Truth、relation/column/asset/evidence denylist；新增 `expected_answer`、`golden_answer`、`ignore_previous_instructions`、`reveal_system_prompt` 等合成哨兵覆盖结构化字段和 trace 旁路。
- 提交：`69184ae`（`fix: close M5 audit and identifier injection gaps`），仅含 3 个必要 `src` 文件和 3 个必要 `tests/unit` 文件，未 push；`git commit` 同时报告既有 `refs/codex/... bad object`/geometric-repack 警告，但提交成功。
- 验证：全量 unit `451 passed, 6 skipped`；相关 evaluator/evidence-tools/diagnostic-agent unit `122 passed`；`uv run --no-sync ruff check src tests/unit` 通过；`git diff --check` 通过。根 `mistake.md` 本条只追加、未纳入提交。
- 未改变：`gemma4:e4b` 环境覆盖策略、6/8/2、四工具 allowlist、DiagnosisRunResult 三终态、恢复/外层重试语义；参数 provenance、evaluator 异常时 artifact 语义仍等待用户决策。

## 2026-08-27：M5 69184ae 后因果与结构化字段安全修复

- 当前基线：实际 `HEAD=69184aeaca9033e0fd3e187b1f1841b5da244d2a`；根 `AGENT.md`、`README.md`、`mistake.md` 的既有未提交修改保持不动。本轮未运行真实模型/Ollama/doctor/e2e/pipeline/reset、数据库写操作或真实评测。
- evaluator 修复：`retry_index=1` 现在必须有同 fingerprint/tool/args 且带错误码的前序 TOOL_CALL；成功后的重复调用使用非 retry 的 `retry_index=0`，后续 retry-limit 仍使用既有稳定错误码；终态 gate 之后禁止普通事件；OUTPUT_RETRY 必须关联既有 `MODEL_PROTOCOL_ERROR` 失败标记、前后真实 `MODEL_REQUEST` 事件且不能只由 metrics 伪造。RUN_AUDIT 唯一且最后、原始事件重算和 6/8/2 保持。
- 结构化字段修复：artifact/catalog/provider 输出的标识符和类型统一增加大小写、驼峰、下划线及拼接形式的通用指令/凭据词法拦截，继续输出稳定 `UNSAFE_*` 分类；普通 `customer_orders`、`stg_payments`、`payment_amount` 等 dbt 标识符保留。trace 参数和 audit/metrics 字段复用同一安全判定，未添加案例常量、Ground Truth 或答案 denylist。
- 文件与提交：仅修改必要的 `src/data_incident_gym/{diagnostic_agent.py,evaluation.py,evidence_tools.py}` 和对应三个 unit 测试文件；`mistake.md` 本条仅追加、不得纳入提交；本轮修复提交待交付时填写，未 push。
- 验证：相关 unit `158 passed`；全量 unit `487 passed, 6 skipped`；`uv run ruff check .` 和 `git diff --check` 通过。现有 trace schema 没有原始模型输出内容/hash，故无法独立证明输出文本本身；本轮不扩展不兼容 schema，改由既有 `MODEL_PROTOCOL_ERROR` 事件标记和前后请求结构 fail-closed，残余限制在交付中说明。

## 2026-08-27：M5 安全修复交付 checkpoint

- 交付提交：`ecfe325`（因果 trace、终态顺序、结构化字段安全与合成测试）及 `29bf1ad`（保留合法 verified run_id trace 参数）；均未 push。
- 复核：两次提交均只包含必要 src/tests 文件；根 `AGENT.md`、`README.md`、`mistake.md` 仍为 unstaged，index 为空。提交时 Git 仍报告既有 `refs/codex/... bad object`/geometric-repack 警告，但提交成功；本轮未处理该无关仓库维护问题。

## 2026-08-27：M5 8b599c3 后 Diagnosis 最终文本安全修复

- 当前基线：实际 `HEAD=8b599c3`，根 `AGENT.md`、`README.md`、`mistake.md` 保留既有未提交修改；本轮未运行真实模型/Ollama/doctor/e2e/pipeline/reset、数据库写操作或真实评测。
- 修复提交：`7076b65`（`fix: audit final diagnosis text safety`），仅修改 `src/data_incident_gym/evaluation.py`、`tests/unit/test_evaluation.py`、`tests/unit/test_artifacts.py`。evaluator 的 `TRACE_READ_ONLY_SAFE` 现在独立扫描最终 Diagnosis 的 `summary` 与 `recommended_actions`，对 SQL/DSN/凭据/token/绝对路径/Instruction/Ground-Truth-like 文本 fail closed，只输出稳定字段标记，不回显原文；provider 精确 allowlist 语义保持不变。
- 测试：相关 unit `156 passed, 3 skipped`；全量 unit `610 passed, 6 skipped`；`uv run --no-sync ruff check src tests/unit` 与 `git diff --check` 通过。安全哨兵均为合成值，并验证现有 ArtifactWriter 路径仍保留正常通用诊断文本。
- 未改变：6/8/2、`gemma4:e4b` 默认值、三种 DiagnosisStatus、recovery、外层三样本、四工具 allowlist、参数 provenance、evaluator 异常六件 artifact 语义；未 push。提交时 Git 仍报告既有 `refs/codex/... bad object`/geometric-repack 警告，但提交成功。

## 2026-08-28：M5 b68bc65 后 P0/P1 安全修复

- 当前基线：实际 `HEAD=b68bc6507b9e4348dd986371c3d4f23cb39f6169`；根 `AGENT.md`、`README.md`、`mistake.md` 的既有未提交修改已保留，未执行破坏性回滚。已核对 M5 计划、根文档和 b68bc65 后独立审查结论。
- 修复提交：`f9ace90`（`fix: close M5 safety and retry boundaries`），只包含必要的 3 个 `src` 文件和 4 个 `tests/unit` 文件，未 push；本条仅追加到根 `mistake.md`，不提交。
- 修复范围：共享安全判定增加 quote-aware SQL comment/keyword 归一化，覆盖拆词、表达式/占位符、COPY、UPDATE ONLY/USING、DDL、COMMENT/RENAME/POLICY、GRANT/REVOKE 和拼接，并保留普通否定说明；凭据/instruction/answer 识别覆盖裸词、空格、camelCase、下划线和前后缀，同时允许正常否定说明。EvidenceRecord.create 在构造边界安全化 node error message；evaluator 对不可信资产、证据引用/范围和根因实际值只输出稳定占位或计数；输出 retry 恰好两次后合法第三次输出可成功，超限仍 fail-closed；评估环境只输出稳定完整性分类，不输出 Ground Truth digest。
- 验证：相关安全/evaluator/diagnostic/artifact unit `421 passed, 3 skipped`；全量 unit `827 passed, 6 skipped`；`uv run --no-sync ruff check .` 通过；`git diff --check` 通过。未调用真实模型/Ollama/doctor/e2e/pipeline/reset、数据库写操作或真实评测。
- 未改变：6/8/2、三种 `DiagnosisStatus`、既有终态集合、recovery、四工具 allowlist、外层三样本和默认 `gemma4:e4b`；未处理参数 provenance、模型硬约束、evaluator 异常时 artifact 语义、计划文本矛盾。提交时 Git 仍报告既有 `refs/codex/... bad object`/`geometric-repack` 警告，但提交成功。

## 2026-08-28：M5 通用安全协议最小修复

- 当前基线：实际 `HEAD=f9ace904a2a64e96573b050e1ec5d027494281b2`；保留根 `AGENT.md`、`README.md` 与本文件既有未提交修改，未修改依赖/lock、CI、compose、third_party。
- 修复范围：`output_safety` 识别任意长度的常见 DSN 多词 key、quoted key 和空格形式，并要求 DSN pair 位于结构化边界以避免普通叙述误判；以通用否定语法允许正常安全说明，同时对否定语句中的 credential/instruction/answer payload 继续 fail-closed；evaluator 对 gate reason、tool/model error 和 terminal code 使用现有稳定 ontology，未知但格式合法的 code 一律 fail-closed。
- 测试：新增 DSN/否定语义/绕过攻击及四类未知 trace code 合成测试；全量 unit `855 passed, 6 skipped`；相关 artifact/output-safety/evaluator unit `367 passed, 3 skipped`；`uv run --no-sync ruff check .` 与 `git diff --check` 通过。
- 边界：未调用真实模型/Ollama/doctor/e2e/pipeline/reset、数据库写操作或真实评测；未改变 6/8/2、三种 `DiagnosisStatus`、recovery、四工具 allowlist、外层三样本、GT digest 隔离及 EvidenceRecord/ArtifactWriter/report 共享边界；本条只追加到根 `mistake.md`，不得纳入修复提交、不得 push。

## 2026-08-30：M7 实施接手基线与执行边界

### 接手事实

- 当前 checkout：`master`，`HEAD=d4898229457084fd710a81d42d6638a3e1d99503`，远端跟踪为 `origin/main`。
- M7 实施计划：`docs/superpowers/plans/2026-08-30-m7-benchmark-foundation.md`；M7 需求变更仍在工作区的 `docs/requirements.md`，本次不回滚、不覆盖。
- 当前实现仍是 M6 seam：`incidents.py`、`m2.run.v1`、`EXPECTED_FAILURE`、四个证据工具和 Kernel 专用工具参数；本次按 M7 计划做替换式迁移，不建立 M6/M7 双轨兼容层。
- 接手前工作区已有用户文件：`AGENTS.md`、`decision.md`、`docs/superpowers/` 以及 `docs/requirements.md` 修改；全部保留，提交时只显式暂存 M7 计划允许的文件。

### 本次授权范围

- 用户明确要求直接在 `master` 实施 M7；允许修改 M7 计划列出的源码、测试、配置和必要 README/报告，并按计划执行本地确定性验证。
- 本次不执行真实模型调用，不执行 Git push；Task 12 的八次开发 smoke 和远端 CI 仍需单独明确授权。
- 不实现 M8-M12、正式 94 次 benchmark、最终 Manifest、策略胜负结论、自由 SQL、原始行访问、写操作、自动修复或生产集成。

## 2026-08-30：M7 P0 观测边界与 Kernel 产物校验

### 关键决策

- `schema_rename_payment_amount` 的公开观测合同只暴露 `raw_payments` schema；profile/history 不属于该场景的可观测证据，不为 P0 增加场景特例画像或第二套 ProfileSpec。
- 候选方案：A，按场景 evidence contract 生成空的 profile/history 公开快照；B，继续强行读取固定 profile/history 并把改名场景当作画像缺失；C，为 P0 单独增加一套含 renamed column 的 profile 配置。
- 选择 A：与 M7 计划的 P0 回归边界一致，保持 ProfileSpec 中立，避免把不可观测事实伪装成证据，也不增加双轨配置。

### 实施校正

- Kernel `KERNEL_STATE` 在 JSON 回读校验时按 JSON 规范化后的模型内容比较；此前直接比较内存中的 Pydantic 对象与回读字典，会误报合法产物为 `ARTIFACT_WRITE_FAILED`。
- Windows dbt 验证使用进程级临时 CPU affinity 与 `DBT_PARTIAL_PARSE=false`，命令结束后恢复；不写入全局环境或项目配置。

## 2026-08-30：Windows 长循环 E2E 验证边界

### 观察与候选

- 候选 A：把 `3221225477`、`3221226505`、`too many values to unpack` 当作 M7 代码回归，修改 DbtRunner、缓存路径或测试循环。
- 候选 B：保留产品实现，使用进程级 affinity、临时 `DBT_PARTIAL_PARSE=false`，必要时只在验证 PATH 中追加官方 `--no-partial-parse`；若长循环仍触发 dbt native 异常，则将该部分标为环境未验证。

### 选择与理由

- 选择 B。单次健康 `pipeline build`、unit/integration、策略矩阵 8 格和 payment 十周期复现均可通过；长循环失败均发生在 dbt seed/build 的 native/解析阶段，且显式 `--no-partial-parse` 仍在第 6 次左右触发，未出现 M7 断言或业务合同失败。
- 不把临时平台 workaround 写入仓库源码、全局环境或测试逻辑；保留失败原始错误作为环境证据，不宣称完整十周期套件已通过。

## 2026-08-30：M7 收口门槛重分类

- 当前状态为“功能候选已落地，但尚不具备提交收口条件”：完整 unit 从 M7 前的 426 项缩至 118 项，不能以当前绿灯替代原有安全与失败恢复覆盖。
- 不机械恢复 M6 测试，不建立兼容层；优先迁移仍跨版本成立的 artifact 原子发布、模型预算/协议失败、跨 run/脱敏、evaluator fail-closed，以及 Lab reset/recovery/未知漂移不变量。
- 在回归与两项计划内的 P1 隔离/策略公平审计完成、完整 unit/integration 和独立审查完成前，冻结 real-model smoke、commit、push 与 M8。

## 2026-08-30：P1 健康事实与评测失败产物语义

- 选择 A：`RelationHistoryFact` 固定承载 ProfileSpec 声明的 ingestion watermark、观测时点与适用 SLA；Kernel 和 evaluator 只基于这些公开、run-bound EvidenceRecord 判断 NO_INCIDENT，不隐式读取或重新查询 ProfileSpec。
- 选择 A：只要已得到 `DiagnosisRunResult`，即使私有 Scenario、Verification 或 evaluator 阶段失败，也必须写入六件 artifact；`evaluation.json` 使用完整、fail-closed 的稳定阶段失败码，不能因内部失败直接跳过产物。

## 2026-08-30：M7 独立 Spec 审查收口校正

- 健康声明的公开锚点采用 `IncidentBrief` 中 `CURRENT_PERIOD_COUNT` 的完整 `relation/series/bucket`，不接受同一历史序列的其他 bucket；Kernel 与 evaluator 均只使用 run-bound、公开投影的证据验证该锚点。
- 确认根因的决定性证据要求失败节点命中公开告警，schema/profile 关系来自该节点的上游 lineage；evaluator 进一步按私有 mutation 校验变更后的列名或类型，拒绝不相关证据组合。
- Kernel 的内部调查 gap 只将真正的 `DISCRIMINATE_SCHEMA` 投影为 `RELATION_SCHEMA`；transformation gap 继续由公开的 `UnresolvedEvidence` 声明，不把定位、解释或影响阶段误映射为 transformation gap。

## 2026-08-30：Ubuntu CI E2E 超时收口方案

- 现象：`3c1acce` 的 Ubuntu CI run `33304745288` 中 unit `137 passed`、integration `11 passed`，完整 `pytest tests/e2e -m "not real_model" -q` 在 job 的 20 分钟上限下被取消；没有 E2E 通过或失败结论。
- 候选方案：A，保持完整非 real-model E2E 命令，仅将 job timeout 提高到 45 分钟；B，将 E2E 拆成独立 job；C，只运行 M7 新增 E2E 并延后既有回归。
- 选择 A：保留完整回归和四个 M7 场景各十周期验证，改动最小；本次只修改 `.github/workflows/ci.yml` 的 job timeout。`decision.md` 为工作区决策记录，不纳入提交。

## 2026-08-30：M7 development smoke 精确八格结果边界

- 前置：Ubuntu exact-HEAD CI run `33307002248` 已通过；harness collection 恰好 8 项，隔离/公平审计通过；用户授权一次 Doctor 与精确 8 个 smoke cell。
- 执行：Doctor 只执行一次；随后 smoke 严格执行 8 格，无重试、替换样本或第九次调用。7 格返回 `EvaluationAttemptResult` 并各自写出六件 artifact，首格在 `INITIAL_RESET_FAILED` 阶段失败。
- 选择：保留全部八格及失败事实，报告为“已执行但不具备 smoke 通过/收口条件”；不重跑首格，不根据模型输出调场景或策略，不开始 M8，不把结果并入正式 94-run 分母。
- 边界：报告 `docs/superpowers/reports/2026-08-30-m7-development-smoke.md` 与本记录均为工作区审计材料；`decision.md`、`AGENTS.md`、`docs/requirements.md`、`docs/superpowers/` 不纳入提交。

## 2026-08-30：M7 smoke 审计修复与新八格执行边界

- 只读审计结论：四个 Kernel 格均在任何证据工具执行前重复触发 `KERNEL_INTENT_INVALID` / `KERNEL_INTENT_SHAPE_INVALID`；冻结的隐藏 intent 协议未在策略提示中公开枚举、工具映射、hypothesis 结构或合法样例。两个 Static 格在 4/6 次模型请求时被统一标成 `MODEL_REQUEST_LIMIT`，与 PydanticAI 实际可能触发的 `tool_calls_limit` 不可区分。smoke 还以 `_env_file=None` 绕过 `.env.diagnostic`，与 CLI/Doctor 的有效配置不一致。
- 最小修复：Kernel 提示补全通用协议并将策略版本升为 `p1.kernel.v2`；新增稳定终态码 `MODEL_TOOL_CALL_LIMIT`，不改变 8/8/2/300；smoke 改用与 CLI/Doctor 相同的 `Settings()` / `DiagnosticSettings()`。不增加 case ID 分支、私有答案、重试、兼容层或新工具。
- 模型配置：批准需求仍固定 `mimo-v2.5`。本机忽略文件仍指向历史 `gemma4:e4b`，因此新的精确八格在进程级显式覆盖 endpoint/model 为批准的 Mimo 配置；不修改用户的 `.env.diagnostic`，不额外执行 Doctor 模型探针。
- Windows 边界：本地非真实模型 E2E 为 `10 passed, 4 failed, 10 deselected`；失败均落在 reset/build/recovery，缺失 manifest 的 run 明确记录 `dbt_exit_code=3221225477`。不把平台 workaround 写入产品，也不重跑失败样本；以修复提交的 Ubuntu exact-HEAD CI 作为确定性收口门槛。
- 新八格门槛：仅在 exact-HEAD CI 全绿后执行用户授权的一套新 8 格；严格一次、无补位、无第九格，不进入正式 94-run 分母。

## 2026-08-30：M7 修复后新精确八格结果边界

- 前置已满足：`c166caa` 的 Ubuntu exact-HEAD CI run `33314966099` 全绿；本地确定性 baseline build 成功；有效配置固定为批准的 `mimo-v2.5`。
- 执行结果：唯一一次矩阵命令为 `8 passed in 825.23s`，表示 8/8 cell 均完成协议检查并产出 run；无重试、补位或第九格。8 个 run 均精确六文件、恢复 `HEALTHY`，安全/环境硬门禁全部通过；evaluator 正确性为 `0/8`。
- 修复效果：Kernel v2 的 payment、customer A、control 从第一套的 0 个成功证据工具提升为 5、5、3 个；customer B 仍未越过 intent 形状。Static 的工具预算耗尽现已准确记录为 `MODEL_TOOL_CALL_LIMIT`。
- 结论选择：不继续做产品补丁。剩余失败是冻结 8/8/2/300 下的模型协议、工具选择与结构化终态能力结果；控制器和 evaluator 均按合同 fail closed，未发现确定性产品错误或安全绕过。禁止额外模型调用、JSON 修补、预算放宽、场景定向调参或把失败样本替换为成功。
- 阶段边界：M7 仍不得标记 smoke 通过，不开始 M8，不启动正式 94-run benchmark。新旧两套结果均保留在工作区报告与 ignored artifacts；本记录和报告不纳入提交。

## 2026-08-31：M7 smoke 二次只读审计与 Static 决策合同最小修复

- 只读审计证据：修复后八格中三个 Static 运行均在 `final_result` 输出 Schema/决策校验处终止；现有 artifact 没有保存被拒绝的原始模型载荷，因此不能把全部 `0/8` 追认为同一个产品根因，也不能据此追加真实模型调用。
- 已确认的产品缺陷：M7 计划要求模型决策 Schema 只允许 `CONFIRMED`、`INSUFFICIENT_EVIDENCE`、`NO_INCIDENT`，并由 controller 独占生成 `MODEL_ERROR`。当前 Kernel 符合该合同，Static 却直接向模型暴露公共 `Diagnosis` Schema，其中包含 `MODEL_ERROR`，随后 output validator 又拒绝模型选择该状态，形成确定性的跨层矛盾。
- 候选范围：A，新增私有 Static decision Schema，仅收窄模型可选状态并继续投影到现有公共 `Diagnosis`；B，继续调整提示词、添加 JSON repair、放宽预算或重跑真实模型；C，修改 evaluator 接受模型生成的 `MODEL_ERROR`。
- 选择 A：它直接修复已证明的合同缺陷，不改变公共 Diagnosis/artifact Schema、六工具、8/8/2/300、evaluator、安全门禁、场景或策略公平边界。B 无确定性根因支撑且会扩大真实模型/验收口径，C 违反 controller-generated error 规则。
- RED/GREEN：新增一个 runner seam 回归，修复前确认 Static 模型 Schema 含 `MODEL_ERROR`，修复后只含三个业务终态，并保留 controller 生成安全 `MODEL_REQUEST_LIMIT` 的路径。
- 验证：全量 unit `141 passed`；`uv run ruff check .`、`uv lock --check`、`git diff --check` 通过；确定性 policy matrix collection 精确 8 项。Docker Desktop daemon 未运行且当前会话无服务启动权限，相关 integration 未进入产品路径并保留为环境未验证。
- 边界：本轮没有真实模型调用、smoke 重试、第九格、预算调整、M8、正式 94-run、commit 或 push。该修复不能被表述为真实模型 smoke 已通过；后续是否重新授权新的真实样本仍是独立决策。

## 2026-08-31：数据库诊断、第三套精确八格与 artifact 投影修复

- 数据库诊断：Docker Engine `29.4.3` 可用，Compose PostgreSQL 为 `running/healthy`，`pg_isready`、直接 SQL round-trip 和 `dbt debug` 均通过；一次数据库集成复验为 `1 passed`。同一窗口也观测到 dbt/Python 的 `3221225477 / 0xc0000005`、dbt 异常清洗遍历 `os.environ.items()` 时的 `ValueError`，以及确定性 policy matrix 主进程在 Pydantic schema 生成处的 `0xc0000005`。因此服务端数据库健康，本机 Windows Python/dbt 长链路仍属环境不稳定；不把平台 workaround 写入产品。
- 执行边界：按用户授权只调用一次完整 `4×2` harness，命令为 `uv run pytest tests/e2e/test_real_model_m7_smoke.py -m real_model -q -s`；结果 `4 passed, 4 failed in 402.24s`。没有 rerun、补位或第九格，不进入正式 94-run 分母。
- 八格结果：3 格在 `INITIAL_RESET_FAILED` 前未进入模型路径；4 格产出 canonical 六文件且 evaluator 全部 `FAILED`；Static/control 的 `run_id=3ea05dd5ebe94f11b9566f81c9da264d` 已完成场景、诊断与私有验证，但在 `ARTIFACT_WRITE_FAILED` 阶段未发布 bundle。故诊断质量仍为 `0/8`，不能标记 smoke 通过。
- 四个正式 bundle：`54c19fffe19b4682adf20fcb9bae77f1`、`50217be4e196474a8f79421a6c611524`、`835915a89d9c4c6c85818ccbba4aae74`、`100cad3bfc334a898ea6af9a0d6ab39e`；均记录 `HEAD=4b2c1d4a670a81d670a725b85a1d65ca31b9cb0b`、`workspace_dirty=true`、`mimo-v2.5`。其中 recovery 为两次 `HEALTHY`、两次 `FAILED`；smoke 后直接 SQL 复核八个关系的行数与三张 raw 表类型均恢复为健康基线。
- 新发现的确定性产品缺陷：私有 `_StaticDecision` 的合法输出被原样放入公共 `DiagnosisRunResult`，而 `ArtifactWriter` 按公共 `Diagnosis` 反序列化后执行结构化等值校验；Pydantic 不同模型类即使字段相同也不相等，导致合法 Static 决策必然无法通过 artifact round-trip。最小复现为 `_StaticDecision != Diagnosis`，与 `3ea05d...` 的 artifact 阶段失败一致。
- 候选方案：A，在 runner 输出边界把合法 `_StaticDecision` 显式投影为现有公共 `Diagnosis`；B，放宽 ArtifactWriter 等值校验以接受私有子类；C，修改/补写失败 artifact 或重新执行模型样本。
- 选择 A：保持私有模型决策 Schema、公共 Diagnosis/artifact Schema 和 ArtifactWriter 严格原子校验不变，只阻止私有类型越过 runner 边界。B 会削弱公共持久化合同，C 会篡改审计事实或消耗未授权样本。
- RED/GREEN：新增 runner seam 回归，修复前稳定得到 `Diagnosis(...) != _StaticDecision(...)`；修复后 focused `1 passed`，相关 runner/artifact/evaluation-runner `15 passed`，完整 unit `142 passed`，Ruff、lock check、diff check 通过。
- 最终边界：本次 smoke 发生在该第二项最小修复之前，因此当前最终工作区没有新的真实模型验收证据；不重跑 smoke，不 commit/push，不开始 M8 或正式 benchmark。

## 2026-08-31：M7 smoke 持续授权与运行节流

- 用户将 M7 阶段的 smoke 全部授权，不再要求每一套 4×2 smoke 单独确认；唯一附加约束是不得连续运行多套。
- 执行解释：每次仍只启动一个完整 8-cell harness，不对单格 rerun、补位或制造第九格；只有相关代码发生变化且确定性门禁通过后，才允许执行下一套。一次 harness 结束后必须先完成产物审计、数据库恢复核对和必要修复，禁止为了追求绿灯直接循环重跑。
- 正式 94-run benchmark、M8、commit 和 push 不因该 smoke 授权自动获得许可，继续保持独立边界。

## 2026-08-31：artifact 投影修复后的单套八格验证

- 节流前置：当前 PostgreSQL 为 `running/healthy`，健康关系检查为 `8 / 100 / 99 / 113`，完整 unit `142 passed`，harness 精确收集 8 项；因此只启动一套新的 4×2 smoke，没有连续运行第二套。
- 唯一执行结果：`5 passed, 3 failed in 374.42s`。Static/customer A 与 Static/control 为 `INITIAL_RESET_FAILED`；Static/customer B 在 `BUILD_FAILED` 终止，留下 lab run `59838233013c4f88bb16c320276c12ce`，但未进入诊断/模型阶段。其余 5 格均发布 canonical 六文件，recovery 全部 `HEALTHY`。
- artifact 修复的真实路径证据：Static/payment `run_id=1bc56ae93f5443cd8d2f9dee4fc03123` 返回合法 `CONFIRMED`，使用修复后的 Static controller hash `b0307abb...`，成功通过公共 `Diagnosis` round-trip 并发布六文件；上一套的 `ARTIFACT_WRITE_FAILED` 未复现。由此确认私有 `_StaticDecision` 到公共 `Diagnosis` 的投影修复覆盖了真实模型路径。
- 质量结果：5 个 evaluator 结果均为 `FAILED`，总质量仍为 `0/8`。Static/payment 虽状态正确，但使用非合同 root cause `SCHEMA_TYPE_MISMATCH`、错误 affected-assets 集合且缺少 lineage 证据；evaluator 按 `ROOT_CAUSE_ACCEPTED`、`AFFECTED_ASSETS_EXACT`、`REQUIRED_EVIDENCE_TYPES_PRESENT` 和 `CLAIM_EVIDENCE_COMPATIBLE` fail closed。四个 Kernel 格分别为三个 `MODEL_REQUEST_LIMIT` 和一个 `MODEL_PROTOCOL_ERROR`。
- 环境边界：本次窗口 Windows Application log 记录两次 `python.exe 0xc0000005` 崩溃；失败 build 的 stdout 在 dbt adapter 注册后中断。smoke 后 PostgreSQL 仍为 healthy，八个关系和 raw schema 均为健康基线，临时模型/DBT 环境变量与 affinity 已恢复。
- 停止条件：未发现需要继续修改的确定性产品缺陷；本轮不连续启动下一套 smoke，不 commit/push，不开始 M8 或正式 benchmark。

## 2026-08-31：Static 共享声明合同与 Lab 恢复幂等性修复

- Static/payment 的冻结 artifact 证明模型已找到真实类型故障，但提示词没有暴露 Kernel 已获得的三项 M7 root-cause ontology，也没有说明 affected assets 是直接失败节点加 downstream model、上游 source 只是因果输入。选择只修版本化通用提示合同并升为 `p1.static.v2`；不收紧 Static 输出 Schema、不增加 controller retry、不放宽 evaluator，避免改变 baseline 的计分机会。
- RED/GREEN：在批准的 `load_strategy_prompt(STATIC_SKILL)` seam 增加共享 ontology 与 direct/downstream claim 语义回归；修复前失败，提示补全后通过。内容不含 case ID、Ground Truth、variant role、预期答案或 evaluator 规则。
- 非 real-model E2E 进一步暴露恢复缺陷：若 `reset()` 已通过 full-refresh seed 恢复健康 Schema、随后 dbt/Python 原生崩溃，外层 `restore()` 会盲目再次反向 mutation；rename 因 `total_amount` 已不存在而失败并掩盖原始错误。
- 候选范围：A，按当前列状态区分已健康、已注入和未知漂移；B，只调用 baseline reset；C，吞掉重复 mutation 的数据库错误。选择 A：已健康跳过、已注入按声明逆序恢复、双列/缺列/第三种类型/异常 distractor 继续 `InvalidIncidentState` fail closed。B 可能绕过依赖清理，C 会掩盖真实漂移。
- RED/GREEN：新增公共 `restore()` seam 回归，修复前在已健康 rename Schema 上仍调用反向 rename，修复后不再调用；原有注入态逆序恢复测试继续通过。
- 确定性验证：完整 unit `144 passed`；完整 integration `11 passed`；M7 FunctionModel 精确矩阵 `8 passed`；Ruff、lock check、diff check 全绿。前一套完整非 real-model E2E 的 `10 passed, 4 failed` 中，三个失败为 Windows/dbt native/runtime，Static/control 的业务与 evaluator 检查全过、仅 recovery native 失败；不据此修改产品平台逻辑。
- 新 smoke 前置：PostgreSQL `running/healthy`，八个关系行数为健康基线，`raw_orders.user_id` 与 `raw_payments.amount` 均为 integer；real-model harness 精确收集 8 项，Mimo key 仅确认存在，临时模型/DBT 环境变量均未残留。

## 2026-08-31：第五套精确八格结果与停止判断

- 唯一一次 harness 为 `8 passed in 774.29s`，8/8 格均发布 canonical 六文件且 recovery 全部 `HEALTHY`；无 rerun、补位、第九格或连续第二套。smoke 后数据库、关键列类型、临时环境变量与 affinity 均恢复，窗口内无新的 `python.exe` Application Error。
- 质量结果为 `1/8`。Static/payment `1a1a8a4dd5124fda9743546e9b59b556` 使用 `p1.static.v2`，精确给出 `SOURCE_SCHEMA_COLUMN_TYPE_CHANGED`、直接失败节点与两个 downstream model，并绑定 node error/schema/lineage Evidence IDs；全部 evaluator 检查通过。这是通用提示合同修复覆盖真实模型路径的正向证据，但不是正式 benchmark 或普遍准确率证据。
- 其余七格保持原始失败：两个 Static 为超出 8 business-call 批次的 `MODEL_TOOL_CALL_LIMIT`；Static/control 为越界 schema 请求后 final schema 失败；Kernel/payment 缺少替代假设；其他 Kernel 为公开 intent JSON/shape 失败或请求耗尽。PydanticAI 本地实现证明 output tool 不计入 `tool_calls_limit`，因此 7 条 trace 后批次原子拒绝不是预算 off-by-one。
- 停止选择：不继续根据单套模型样本调优调查顺序、预算、Schema、controller 或 evaluator，不运行第六套 smoke。M7 当前仍只能表述为“功能候选与确定性门禁已落地，real-model smoke 质量 1/8，尚未达到提交收口条件”；不 commit/push，不开始 M8 或正式 94-run benchmark。

## 2026-08-31：M7 exact-HEAD CI 收口状态

- CI 证据：Ubuntu GitHub Actions run `33364542658` 的 `headSha` 为 `f502458a5b90ae73e5808b40a03b2bb2df0b03a4`，`m1` job 全部成功；Ruff、unit、integration 以及 `pytest tests/e2e -m "not real_model" -q` 均通过。
- 提交边界：`master` 已推送同一 SHA；本轮只提交五个产品/测试文件，`AGENTS.md`、本记录、审计报告、`docs/requirements.md` 和其他工作区材料仍未提交。
- M7 状态：标记为“基础设施完成，模型质量观察为 1/8”。该 1/8 是开发期精确八格观察，不是正式 94-run benchmark，也不是普遍准确率结论。
- M8 决策：当前不开始 M8；M7 状态记录完成后，M8 仍需单独的范围与实施授权。

## 2026-09-01：M11 exact-HEAD CI 超时门

- 修复提交：`e62c15f` 已推送到 `origin/master`，只包含 `tests/unit/test_lab.py` 的 hermetic fixture 修复；本地进程级隔离下完整 unit 为 `304 passed`，Ruff、lock 和 diff check 通过。
- CI 证据：run `33502820304` 的 `headSha` 精确为 `e62c15f056f6d0992ffc31195131666f12fc7111`；Ubuntu unit 与 integration 均通过，完整 `pytest tests/e2e -m "not real_model" -q` 在 job `45m17s` 被 `timeout-minutes: 45` 取消，未得到 E2E 通过或失败断言。GitHub 注解读取的 `403 checks:read` 不是 job 失败原因。
- 候选方案：A，将 CI job timeout 从 45 分钟提高到 60 分钟，保留完整 E2E；B，拆分 E2E job；C，缩减 E2E 收集范围。
- 当前边界：在用户选择前不修改 workflow、不新增提交或推送；不冻结正式 `config/benchmark/p1-formal-v1.json`，不运行真实模型/正式 benchmark，不开始 M12。`decision.md` 继续仅作为工作区记录。

## 2026-09-01：M11 CI timeout 与后续并行治理分阶段决策

- 当前选择：将 `.github/workflows/ci.yml` 的 M11 job timeout 从 45 分钟提高到 75 分钟；保持完整 `pytest tests/e2e -m "not real_model" -q` 验收命令、收集范围和冻结合同不变。
- 后续选择：M11 release gate 通过后，再治理 CI，将 34 格策略矩阵与十周期复现拆为使用独立数据库的并行 jobs，并用 `--durations` 固定记录耗时；本轮不提前拆分、不缩减验收。
- 边界：本次只修改 timeout 和工作区决策记录；尚未 commit/push，不运行真实模型、正式 benchmark 或 M12。

## 2026-09-01：M11 Ubuntu unit 隔离缺陷修复决策

- CI 证据：Ubuntu exact-HEAD run `33502308064` 在 `pytest tests/unit -q` 失败，唯一失败为 `test_silent_payment_drop_routes_exact_batch_and_restores_in_reverse_order`；测试调用 `_ensure_healthy_for_prepare()` 时，场景中的 `ADD_NULLABLE_COLUMN` 干扰 mutation 触发真实 PostgreSQL 连接，导致 `127.0.0.1:55432` connection refused。
- 候选方案：A，仅在该单元测试提供内存 `RelationSummary`，保持产品 schema 健康门禁不变；B，让 CI 在 unit 前启动 PostgreSQL；C，修改产品代码跳过该准备阶段的 schema 检查。
- 选择 A：根因是测试未隔离其余 mutation，不是产品逻辑或服务端故障；A 改动最小，保持 unit hermetic，同时保留产品的 schema 门禁和 CI 分层语义。B 扩大 CI 环境职责，C 会削弱恢复前状态校验。
- 边界：只修改该测试并重跑确定性 unit/static 检查；不运行真实模型、正式 benchmark 或 M12。`decision.md` 继续仅作为工作区记录，不暂存、提交或推送。

## 2026-09-01：M11 75 分钟 exact-HEAD CI 通过

- CI 证据：run `33507348034` 的 `headSha` 为 `f0c393f4e52d854bb75195238b86ec30af929c0c`，job `m1` 在 `48m14s` 成功完成。
- 结果：unit `304 passed`、integration `29 passed`、非 `real_model` E2E `40 passed, 10 deselected`；完整冻结命令保持原样，75 分钟 timeout 覆盖了此前 45 分钟取消点。
- 阶段结论：M11 实现与确定性 CI 验收门通过；正式 106 格 Manifest 的独立冻结提交仍是后续 release packaging 步骤。34 格矩阵与十周期复现的独立数据库并行治理按用户要求延后到 M11 完整收口之后。
- 边界：本记录继续仅作为工作区材料；本轮不运行真实模型/正式 benchmark，不开始 M12。

## 2026-09-01：M11 Manifest 生成待独立 packaging 授权

- 生成事实：在通过 Ubuntu exact-HEAD CI 的实现 revision `f0c393f4e52d854bb75195238b86ec30af929c0c` 上生成 canonical `config/benchmark/p1-formal-v1.json`；共 `106` 格，其中 `94` 个模型格、`12` 个 `FIXED_RULE` 格。
- 验证事实：Manifest SHA-256 为 `413a420c5040b182a15dff770cbded0a0dde342054bc4f38fe0aeac472c83c94`；显式 `benchmark verify` 与 Manifest/CLI 单测 `13 passed`。
- 候选方案：A，按冻结规则只提交该 Manifest 文件并推送；B，暂缓 packaging，使 M11 保持 CI 通过但 Manifest 未冻结。选择 A 时不得合并 timeout、实现代码或其他工作区材料。
- 当前边界：Manifest 目前仅在工作区生成，未暂存、未 commit、未 push；不运行 `benchmark run`、真实模型、正式 benchmark 或 M12。并行 CI 治理继续延后到 M11 完整收口之后。

## 2026-09-02：M12 唯一正式批次永久作废

- 正式身份：最终 `p1-formal-v1` Manifest SHA-256 为 `61983bef07cbaa8e97c51dac5b5b05c58d26a7bc98667f2db744c26d55a49c15`，绑定 implementation revision `8c09c216ddb1f82a1294b75a377c6ead759522b4` 与执行 checkout `2faaf31651cd84f8cf464c755e417951da95c305`；Manifest-bound doctor receipt 为 `PASSED`。
- 批次事实：106 格均写入终态，ledger 共 212 条；98 格 `FAILED`、8 格 `COMPLETED`，其中 56 个 `RUN_SETUP_ERROR`、42 个 `EVALUATION_FAILED`。94 个 model-backed 格中 41 个到达 MIMO、53 个在 harness/setup 阶段失败；没有重试、补位或替换。
- 失效根因：fixed-rule 工具语义、空 Evidence ID、setup placeholder applicability、初始 reset 后恢复传播和 setup 异常后继续运行等 harness 缺陷共同破坏正式报告语义与预算保护。56 个 setup placeholder 的工具、scope、trace 等检查不是实际业务越界。
- 选择：按已批准的一次性规则将整个批次永久封存为 `INVALID_HARNESS`。P1 不产生有效真实模型质量结论；不重跑同一 Manifest、不生成新 Manifest 补跑、不重算或追认旧报告。
- 现场：原 Manifest、doctor receipt、ledger、106 格六文件、`summary.json` 和 `report.md` 保持不可变；正式 artifact 树 640 个文件的聚合 SHA-256 为 `d6f94642d878e6501f0682c12fd4c65c3c5caffe3956b32c94779bccf6f4df1c`。

## 2026-09-02：post-M12 hardening 与 P1 收尾口径

- 最小修复：evaluator 升级为 `p1.evaluator.v2`；fixed-rule 报告语义、Evidence ID 判定、恢复状态传播、setup artifact applicability 和 fail-stop 已修复。修复只服务后续阶段，不追溯旧批次。
- 发布证据：hardening 提交 `b636c6f2a1f6a5928b182442d3262c7ff4cad24c` 的 Ubuntu exact-HEAD CI run `33625447982` 全绿：unit `327 passed`、integration `34 passed`、非真实模型 E2E `47 passed, 10 deselected`。
- 用户确认的关闭状态：工程生命周期 `CLOSED`、正式实验 `INVALID_HARNESS`、模型质量结论 `NOT_ESTABLISHED`。不能把该状态缩写为“P1 benchmark 成功”或宣称 Kernel 优势。
- 本地整理：旧的未跟踪 Manifest `413a420c...c83c94` 已按原哈希移动到 ignored `.dig/p1-closure-backup/`；本地 `master` 已快进到 `b636c6f`，现有用户材料保持未暂存。快进期间再次出现已知的无效 checkpoint ref/geometric-repack 警告，但 HEAD 更新成功；不越权修理或删除内部 ref。
- 发布边界：当前只准备 `docs/requirements.md`、P1 closure report 与本工作区记录，停在 reviewed diff；不 commit、不 push、不调用 MIMO、不执行任何 benchmark 命令。`decision.md` 继续作为 workspace-only 材料，不纳入提交。

## 2026-09-06：Kernel v6 契约修订与 p1-formal-v5 smoke 判定

- 用户授权整链执行（提交/CI/合并/Task5 smoke）并指示"大胆使用子代理、非必要不停"。v6 传输契约（绑定参数化、多调用打包、状态账本经工具 prepare 逐请求注入、p1.kernel.v6 + p1.controller.v5）以提交 `bdecd21` 落地：unit 370、integration 34、e2e 47 全绿，分支 CI `34006120877` 与 main CI `34008232005` 全绿，main 快进至 `bdecd21`。
- Task5 唯一一次 8 格真实模型 smoke（Manifest `e0b615f4…`、包装提交 `6a86f01`、worktree `DataIncidentGym-smoke-v5`）：8/8 终态、0 RUN_SETUP_ERROR、环境/恢复门 8/8 PASS、数据库恢复 113/99。Kernel 工具成功率 21/28=75.00%（v4 68.97%），Kernel 非异常终态 1/4（v4 0/4，seq6 首次真实模型 CONFIRMED）。判定门（≥80% 且 ≥2/4）未达标，按计划不进 Task 7。
- 失败模式迁移：意图信封错误归零；剩余为调查纪律类（RELATION_ARGUMENT_NOT_PROVEN ×5、RELATION_NOT_ALLOWED ×2），2 格 MODEL_PROTOCOL_ERROR。STATIC 对照 3/4 格 MODEL_TOOL_CALL_LIMIT（schema/prompt 逐字节未变，判为采样方差）。
- Preflight 4 次尝试：前 3 次环境失败（容器被宿主空闲回收 exit 0；worktree 缺显式 `.env.diagnostic` 触发 doctor 安全门 `doctor.py:624`），receipt 保全于 `.dig/kernel-v6/`；按 v3/v4 先例复制 Windows `.env.diagnostic`（mode 600）后 attempt4 PASSED 13/13。探针上限 8 POST + 4 /models，cell 模型请求合计 44。
- 独占归档 aggregate `23e00f28…`（51 文件）经独立子代理以字节正确 NUL 算法重算 MATCH；审计更正报告分母笔误（21/29→21/28），其余数字全部与 artifacts 一致。证据提交 `6e05be7` 已推送 `origin/codex/benchmark-smoke-v5`（经 bundle 通道：WSL 的 github.com DNS 劫持 127.0.0.1，Windows 侧 Steam++ 加速器可推送）。
- 修复性操作披露：删除两仓库中指向 tree 对象的非法 checkpoint ref（`refs/codex/turn-diffs/.../aed8c632-…`，对象本就不可达，阻塞一切 fetch）；Windows main 快进至 origin。v2 身份未冻结；是否再立契约修订由用户决定。

## 2026-09-06：Kernel v7 provenance 修订与 p1-formal-v6 smoke 判定

- 用户授权"再立一轮契约修订"。v7（提交 `4ac9779`：kernel 新增 `provable_relations_by_tool()`、账本注入 provable_relations、`RELATION_ARGUMENT_NOT_PROVEN` 重试消息携带实际可查列表、提示词 v7、`p1.kernel.v7`+`p1.controller.v6`、`p1-formal-v6` 身份一次性批准）全量验证绿（unit 371/integration 34/e2e 47），分支 CI `34015314640` 与 main CI `34017422530` 全绿，main 快进至 `4ac9779`。
- p1-formal-v6 唯一一次 8 格 smoke（Manifest `5fee6571…`、包装提交 `c3c1788`、preflight 一次 13/13）：8/8 终态、0 RUN_SETUP_ERROR、环境/恢复门 8/8 PASS、数据库恢复。Kernel 工具成功率 18/27=66.67%（v5 75.00%，门≥80% 未过）；Kernel 非 MODEL_ERROR 终态 2/4 首次达标（seq6 CONFIRMED 仅差一类必需证据、seq7 弃权方向正确但缺 GAP_DECLARED）。综合门未同时达标，不进 Task 7。
- 确定性发现：RELATION_NOT_ALLOWED 从 v5 的 2 次涨至 9 次，全部为 lineage 证据派生关系名被工具层拒绝——kernel provenance 门（observable ∪ 证据派生 ∪ incident subjects）与证据工具层场景 allowlist 是两套不一致合同。v7 白名单以 kernel 门为源放大了暴露。下一轮正确杠杆是把 kernel 白名单数据源与工具层 allowlist 对齐（确定性产品修复）；是否继续由用户决定。
- 归档 aggregate `4c96f7b5…` 经独立子代理审计全项 PASS 零偏差（含 gate-matrix 逐字节复算）；证据 `cefacb0` 已推送 `origin/codex/benchmark-smoke-v6`（bundle 通道）。cell 模型请求 46；preflight 一次通过（≤2 POST）。v1–v5 封存证据未动，v2 未冻结。

## 2026-09-06：Kernel v8 同源化修订与 p1-formal-v7 smoke 判定

- 用户授权"同源化修复"。v8（`99dbfdc`：kernel 构造去除 brief_subjects 过滤、`_validate_argument_provenance` 删除证据派生并集与 incident_subjects 特例、关系拒绝改判 RELATION_NOT_ALLOWED 并镜像工具层语义——记录 fingerprint/注册 intent 假设/直接落 BLOCKED gap 以保全弃权声明绑定；provable_relations 白名单即工具层精确 allowlist；`p1.kernel.v8`+`p1.controller.v7`）+ 身份批准（`abeca0a`，p1-formal-v7）。
- 过程事故：初版实现漏入身份批准，冻结 Manifest 被拒，补入实现后重跑双 CI（取代 run 34029515535）；smoke worktree 重置重新冻结。教训：身份批准属实现提交的一部分。
- p1-formal-v7 唯一一次 8 格 smoke（Manifest `f16348bc…`、包装提交 `9ff672a`、preflight 一次 13/13）：8/8 终态、0 RUN_SETUP_ERROR、环境/恢复 8/8 PASS、数据库恢复 113/99。**Kernel 工具成功率 28/32=87.50% 首次达标（轨迹 75.00→66.67→87.50）；关系纪律错误 0 次（v6 为 9 次）——同源化按设计生效。** 但 Kernel 非 MODEL_ERROR 终态 0/4（v6 为 2/4），综合门未同时达标，不进 Task 7。
- 剩余瓶颈收敛到冻结 8/8/2/300 下的模型能力项：seq2 干净 8/8 工具后需第 9 次（TOOL_CALL_LIMIT）；seq6/7 调查干净但 final_result 被 finalize 门拒绝（OUTPUT_SCHEMA_REJECTED，重试耗尽）；seq3 假设记账 3 错。STATIC 2/4 格死于 TOOL_CALL_LIMIT，seq8 对 customer_b 弃权方向正确但缺声明完整性。三轮瓶颈迁移链：意图信封→关系纪律→终态机制/预算算术；继续提示词迭代边际收益递减。
- 归档 aggregate `b92c9240…` 独立审计全项 PASS 零偏差；证据 `2be81dc` 已推送 `origin/codex/benchmark-smoke-v7`（bundle 通道）。cell 模型请求 42。v1–v6 封存证据未动，v2 未冻结。是否继续（需预算/合同层变更授权）或收尾，由用户决定。

## 2026-09-06：P1 Kernel 修订轮次收尾（用户选择：维持现状待更强模型）

- 用户在三选一决策中明确选择 3：冻结当前实现与合同，不进行预算/合同层变更；未来更换更强模型时，仅需按既有流程以新身份冻结 Manifest 并执行一次 smoke 即可重测判定门。
- 最终状态：main = `abeca0a`（含 v6/v7/v8 三轮 Kernel 修订与 v5/v6/v7 身份批准），默认分支 CI 全绿。判定门证据链：p1-formal-v5（工具成功率 75.00%、终态 1/4）→ p1-formal-v6（66.67%、2/4，发现 kernel 门与工具层 allowlist 合同错位）→ p1-formal-v7（**87.50% 首次达标**、终态 0/4；关系纪律错误归零，同源化按设计生效）。综合门（≥80% 且 ≥2/4）始终未同时满足，Task 7 未执行，v2 身份未冻结。
- 冻结时的结论口径：harness 连续三批完全干净（0 RUN_SETUP_ERROR、环境/恢复门满分、独立审计零偏差）；工具层与调查纪律缺陷已全部修复并闭环；剩余差距是 mimo-v2.5 在冻结 8/8/2/300 预算下产出满足 M6 合同终态决策的能力项。不得把 87.50% 表述为准确率或普遍结论（8 格 subset）。
- 证据保全：五个证据分支（codex/benchmark-smoke-v3/v4/v5/v6/v7）与其 Manifest、归档、smoke-report 均已推送或本地封存；smoke-v5/v6/v7 worktree 保留在 WSL；v1–v4 身份及历史批次未动。
- 本地整理：keep-alive 会话已清理；WSL main 快进至 `abeca0a`；Windows main 与远端一致。计划文档（2026-09-05/2026-09-06）已记录全部三轮执行状态。

## 2026-09-06：模型切换 mimo-v2.5-pro（M5.3）与 p1-formal-v8 判定门首次通过

- 用户授权换用同供应商模型 `mimo-v2.5-pro`。切换提交 `3a245a6`（`DEFAULT_FORMAL_MODEL`、Manifest Literal、DiagnosticSettings 默认、doctor 建议文案、5 个测试文件、README；`p1-formal-v8` 身份一次性批准），需求文档 M5.3 修订：历史默认记录 gemma4:e4b/qwen3.5:9b/mimo-v2.5，mimo-v2.5 的三批 smoke 不计入新模型分母。全量验证绿（unit 371/integration 34/e2e 47），功能分支 CI `34040445935` 与 main CI `34042635722` 全绿，main 快进至 `3a245a6`。
- p1-formal-v8 唯一一次 8 格 smoke（Manifest `d5f62da0…`、包装提交 `9d405af`、preflight 一次 13/13 含 pro 探针）：8/8 终态、0 RUN_SETUP_ERROR、环境/恢复 8/8 PASS、数据库恢复 113/99。**判定门两项首次同时达标：Kernel 工具成功率 24/24 = 100.00%（≥80%）；Kernel 非 MODEL_ERROR 终态 2/4（seq2 CONFIRMED 全过评测 + seq6 CONFIRMED 仅缺 1 门）。** STATIC 3/4 方向正确（1 全过、1 状态正确缺声明、1 工具预算）。
- 关键对照：同一冻结合同与实现下，v7 中死于终态机制的场景在新模型下工具全部正确；意图信封与关系纪律错误零复发（前几轮修订价值被对照确认）。剩余常见失分点为 INSUFFICIENCY_GAP_DECLARED（弃权声明绑定完整性），两臂均受影响。
- 归档 aggregate `31e3906f…`（51 文件）独立审计 PASS 零偏差（报告 prose 中 revision 后缀笔误已按审计更正）；证据 `f63d4a5` 已推送 `origin/codex/benchmark-smoke-v8`。cell 模型请求合计 44。
- **判定门通过按计划解锁 Task 7 申请权**：正式 106 格（p1-formal-v2）仍需用户单独授权；v1–v7 封存证据未动；87.5%/100% 等数字均为 8 格 subset 观察，不得表述为准确率或普遍结论。

## 2026-09-07：Task 7 正式批次 p1-formal-v2 执行与 fail-stop 结案（结论 INVALID）

- 用户授权 Task 7。从实现 SHA `3a245a6bd809…` 新建 worktree `DataIncidentGym-formal-v2`（分支 `codex/benchmark-formal-v2`），Manifest `p1-formal-v2` 首次冻结（SHA `a14cd820f178…`，106 格/94 模型格/12 固定规则，模型 mimo-v2.5-pro），包装提交 `1b5b460`，完整 preflight 一次通过（doctor 13/13、无 selector）。
- 唯一一次正式批次（`subset: false`）在 **32/106 终态后 fail-stop**。触发格 sequence 32（orphan_payment_coupon_b / DIAGNOSTIC_KERNEL）：setup 链成功，diagnosis 阶段异常（ENVIRONMENT_VERIFIED expected=RUN_SETUP_COMPLETE / actual=DIAGNOSIS_FAILED），恢复 HEALTHY；runner 按既有 fail-stop 语义正确终态并停止。终态构成 7 COMPLETED / 25 FAILED（24 EVALUATION_FAILED + 1 RUN_SETUP_ERROR）；数据库恢复 113/99，容器全程无重启。
- 按计划 Task 7 处理表：终态 < 106 → 正式 report 命令已按设计 fail-closed（`benchmark ledger does not contain exactly two entries per cell`），不产出 summary/report/RESULTS.md，**结论固定为 INVALID**；独占归档一次（32 格，aggregate `0c158240…`，194 文件）。
- 独立审计 PASS 零偏差：聚合重算 MATCH、ledger 32 对完整、32 格六文件齐全且 recovery 全 HEALTHY、fail-stop 证据（唯一 RUN_SETUP_ERROR、无 32 号后终态、33 号无 ledger/artifact）、receipt 五元绑定、文件位置合同（无 subset.json、无 summary/report）。
- 证据 `74363e3` 已推送 `origin/codex/benchmark-formal-v2`（bundle 通道）。p1-formal-v2 身份已消耗永不复用；本批次不产生任何准确率/Kernel 优势/模型质量结论。DIAGNOSIS_FAILED 异常未留存原始堆栈（符合脱敏合同），触发条件未知；未来重启正式批次需新身份（p1-formal-v9+）、新授权，并先取证该异常。

## 2026-09-07：DIAGNOSIS_FAILED 取证修复与 p1-formal-v9 第二次正式批次 fail-stop（86/106，结论 INVALID）

- 用户选择取证后再战。取证结论：v2 批次 seq32 的逃逸点为 `DiagnosisRunner.diagnose()` 顶层（run-state 构造前/owned-client 关闭），FunctionModel 离线复现未能触发（构造干净），判定为真实 provider 路径的 teardown/构造瞬态。修复 `eb01a68`：`diagnose()` 顶层 fail-closed——构造期/teardown 异常一律转化为安全 `MODEL_RUNTIME_ERROR` 终态（Kernel 策略合成零用量 InvestigationState + KERNEL_STATE 事件满足产物合同），owned-client close 抑制异常；两个新回归测试钉住。身份批准 `0c3cc61`（p1-formal-v9）。unit 373/integration 34/e2e 47 全绿，功能分支 CI `34087082375` 与 main CI `34091362491` 全绿，main 快进至 `0c3cc61`。
- p1-formal-v9 唯一一次正式批次（Manifest `698752e8…`、包装提交 `493f4af`、preflight 一次 13/13）：**86/106 终态后 fail-stop**——`eb01a68` 修复经实跑验证有效（同 seq32 顺利通过，批次推进 54 格），新失效点为 seq86（schema_type_change_order_customer_a / KERNEL_NO_SCHEMA）`BUILD_FAILED`：故障构建 PASS=25/ERROR=0 全部成功，注入的类型变更在构建执行时未生效，verifier 对意外健康构建 fail-closed。前序 83–85 格全部 HEALTHY 恢复。触发条件未知（特定顺序上下文的 inject 路径问题或瞬态；全部 smoke/e2e 未覆盖此序列上下文；数据库已恢复无法事后区分）。
- 结案：正式 report 按设计 fail-closed；独占归档一次（86 格，aggregate `ab6079fa…`，518 文件）；独立审计 PASS 零偏差；证据 `c516f4d` 已推送 `origin/codex/benchmark-formal-v9`；结论固定 `INVALID`，无 RESULTS.md。数据库恢复 113/99。
- 累计画像：两次正式尝试、两种 fail-stop 模式（seq32 diagnosis 逃逸[已修复]、seq86 注入未生效[未定位]），偶发异常率约每 50–86 格一次。p1-formal-v2/v9 身份均已消耗。未来重启正式批次（p1-formal-v10+）前须先完成 seq86 型「注入未生效」取证（可 FunctionModel + 真实 lab 按 83→86 顺序离线重放验证确定性），并评估是否需要批次级韧性设计（属需求层变更，需重新批准）。

## 2026-09-07：p1-formal-v9 恢复完成（106/106 终态）与正式报告（结论 INVALID）——用户豁免一次性纪律

- 用户明确指令"重新从失败的地方开始，不要改任何东西"：豁免一次性纪律，同 Manifest（`698752e8…`）同目录恢复批次。恢复被 `_verify_checkout` 拒绝一次（证据提交 c516f4d 引入 manifest 之外路径）；将 worktree 切回包装提交 `493f4af`（detached，证据保存在分支）后恢复成功。全程未改任何代码/Manifest。
- 恢复段完成剩余 20 格，**106/106 全部终态**。正式 report 首次运行崩溃：`benchmark_report.py:264` 对 `KernelStateTraceEvent.state`（`Any` 类型，反序列化为 dict）直接 `.gaps` 属性访问——真实完成批次上首次触达该路径的只读报告层缺陷（不影响判分门）。最小修复 `df22569`：`InvestigationState.model_validate` 后访问（对齐同文件既有模式）；unit 373 绿。历史处理披露：恢复期间 worktree 处于 detached HEAD，最终两个提交（`90f1b7f` 结果 + `b115ac7` 修复）最初落在游离 HEAD 且 bundle 按分支引用只带出 `c516f4d`；已在 WSL 侧建分支 `codex/formal-v9-final` 并 rebase reconcile（`cf4572d`+`df22569`）后推送，main 快进至 `df22569`，main CI `34120037221` 全绿。
- **正式报告结论（reporter 四态规则原文）：INVALID**——唯一硬门失败 seq86 `ENVIRONMENT_VERIFIED`（注入未生效），其余硬门（artifacts/doctor/fixed-rule 零模型/identity/kernel-state/ledger）全部通过。报告字节确定性已验证（重复运行 SHA-256 一致）。数据库恢复 113/99。
- 主矩阵归档观察（INVALID 下仅为归档数字，禁止外推）：Kernel paired 0/15、状态准确率 33.3%、根因 26.7%、无故障 6/6、claim-validity 100%、不支持确认 13.3%、assets-F1 0.185；STATIC paired 1/15、状态 66.7%、根因 66.7%、无故障 2/6、claim-validity 21.6%、assets-F1 0.798。
- 证据：`RESULTS.md`（INVALID 措辞红线执行）+ `final-summary.json`/`final-report.md` 已提交并推送 `codex/formal-v9-final` 与 main；部分归档（86 格时点 aggregate `0c158240…`）保留原样（独占归档已消耗，完成态见证为 suite 内 summary/report + RESULTS.md）。
- 最终项目状态：正式结论 **NOT_ESTABLISHED（INVALID）**；harness 全链（冻结/preflight/resume/report/archive/审计）经两次正式批次实跑验证；剩余未定位项为 seq86 型注入未生效（低频，触发条件未知）。后续任何正式结论需新身份批次 + 本记录所列取证前置。

## 2026-09-07：交接文档落盘

- 交接文档写入 `docs/HANDOVER.md`（Windows 工作区材料，docs/ 为 ignored，不纳入提交）：位置地图（Windows/WSL checkout 与 9 个 worktree，含另一会话的 `relation-policy`）、Manifest 身份台账（v1–v9 全部消耗状态、v10+ 起步）、实现要点、两项未定位问题（seq86 注入未生效、宿主硬件偶发）、9 条环境已知坑（DNS 劫持/bundle 通道、wsl.exe 传参陷阱、容器空闲回收、doctor 配置门、detached HEAD 陷阱等）、标准操作卡与判定门定义。
- 收尾整理：WSL 主 checkout 已从 `codex/formal-v9-hardening`@`0c3cc61`+本地修复副本 同步至 `main`=`df22569`（干净）；formal-v9 worktree 位于 `codex/formal-v9-final`@`df22569`。

## 2026-09-07：seq86 根因更正（硬件故障 → dbt 子进程崩溃）与停止真实模型运行

- 用户更正：v9 批次 seq86（`schema_type_change_order_customer_a` / KERNEL_NO_SCHEMA）的 `ENVIRONMENT_VERIFIED` 硬门失败（expected=RUN_SETUP_COMPLETE / actual=BUILD_FAILED），现有证据指向**宿主硬件故障导致 dbt 子进程崩溃**，不支持此前记录的「故障注入未生效」。当时「构建 PASS=25/ERROR=0 但注入类型变更未反映」是崩溃现场表征，非 inject 路径缺陷。本文件 2026-09-07 两条 v9 记录（86/106 fail-stop 与恢复完成）中「注入未生效 / 触发条件未知 / 需 83→86 离线重放取证」的归因**作废**；该硬件故障**现已解决**。
- 恢复语义澄清：用户豁免一次性纪律后从 86 格恢复跑完剩余 20 格，**只补齐终态数量（106/106），未消除 seq86 这一格的硬门失败**；reporter 按冻结规则（环境硬门失败 → 整批无效）判 **INVALID** 是正确行为，结论不变。
- standing 约束：**后续不再运行任何真实模型，包括 doctor 模型探针**；因此不再启动 v10+ 正式批次（含 94 个 model-backed 格必然触发真实模型请求），正式评测线封闭在 INVALID / NOT_ESTABLISHED，剩余工作为文档与留痕整理。
- 文档纠正范围（手写叙述文档已改）：`docs/HANDOVER.md` §6、`RESULTS.md`（硬门括注 + 诚实边界 2/4）、`reports/benchmark/p1-formal-v9/formal-run-record.md`（fail-stop 定位 + 对照 + 后续约束）。生成类字节确定性见证文件（`final-report.md` / `final-summary.json` / `ledger.jsonl` / `gate-matrix.csv` / `doctor.json` / `aggregate_sha256.json`）**未改**。
- 留痕边界变更：按用户指令将 `docs/` 移出 `.gitignore` 并加入 Git 跟踪（此前 2026-09-02 的「ignore local documentation」决定被推翻）；`AGENTS.md`、`decision.md` 仍为 workspace-only 未跟踪材料。本条目仅为工作区记录，未 commit/push。
