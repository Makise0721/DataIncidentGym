# M3 Evidence Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不回归 M1/M2 的前提下，为某个固定故障 `run_id` 提供四个严格只读、返回稳定 `EvidenceRecord`、非法输入 fail closed 的 M3 证据工具闭环。

**Architecture:** 新增深模块 `EvidenceTools.for_run(run_id, diagnostic_settings)`，在构造时把无 `run_id` 参数的 Schema/血缘工具绑定到一个已验证的 M2 run；模块只从固定 artifact 路径和单独的 PostgreSQL 只读角色取证。`EvidenceRecord` 负责统一事实合同与确定性 ID，`ReadOnlyRoleProvisioner` 只存在于 M1/M2 管理平面，诊断工具既不接收也不导入管理密码。

**Tech Stack:** Python 3.12.10, uv 0.11.24, Pydantic 2.13.4, psycopg 3.3.4, dbt-core 1.12.3 artifacts, PostgreSQL 17.6 Alpine, pytest 9.1.1, Ruff 0.16.4, Docker Compose

---

## 批准状态与执行边界

- 用户于 2026-08-25 明确确认：远程 Ubuntu CI 对提交 `4e2d169` 已通过，M2 正式完成；这是本计划的人工观测基线。
- 本地 `HEAD` 与 `origin/master` 当前均为 `4e2d169546142f53bb4a0c6aca8cc4e430059481`。
- 根目录 `AGENT.md`、`README.md`、`mistake.md` 的现有修改是用户明确保留的 workspace-only 状态。本计划不得撤销它们；`AGENT.md` 不编辑，`README.md` 与 `mistake.md` 即使在实施中更新，也必须保持未暂存、未提交、未推送。
- 本文档获准编写不等于获准实施。只有用户另行批准本计划后，才允许开始 Task 1。
- 全部 Task 控制为 5 个。不得以“顺便实现”为由增加 M4/M5、自由查询或新用户 CLI。
- 所有提交使用显式路径，禁止 `git add .`、`git add -A` 或任何会带入根目录 Markdown 的宽范围暂存命令。

## M3 共识基线

| 决策 | 本计划采用的精确语义 |
|---|---|
| 四个公开工具 | 只实现 `get_dbt_run_results(run_id)`、`get_dbt_node_error(run_id, node_id)`、`get_relation_schema(relation_name)`、`get_dbt_lineage(node_id, direction)` |
| 固定 run 上下文 | 先调用 `EvidenceTools.for_run(run_id, ...)`；前两个工具传入的 `run_id` 必须与上下文一致，后两个工具隐式使用该 run |
| Schema 的时间一致性 | Schema 必须通过实时只读 PostgreSQL 查询获得，同时与该 run 固定的 `schema.json` 列元数据一致；数据库 reset/漂移后返回 `RUN_STATE_DRIFT`，不返回混时证据 |
| P0 relation 范围 | 只允许当前 run 的 `schema.json` 已记录的故障关系；首个案例即 `raw_payments`。实际存在但不属于该 run 的其他 relation 也返回 `RELATION_NOT_ALLOWED` |
| 血缘语义 | 使用该 run 的 `manifest.json`，`upstream`/`downstream` 均返回传递闭包；输出 model/seed/source 数据节点，过滤 test 节点，按距离再按 node ID 稳定排序 |
| EvidenceRecord 稳定性 | `content_digest` 只摘要 canonical content；`evidence_id` 由 `run_id + evidence_type + source + subject + content_digest` 确定性生成，排除 `observed_at` |
| 错误事实规范化 | 使用 `run_results.json` 的节点状态和 message；统一换行/路径分隔符并移除 `compiled code at <absolute path>` 行，不用自然语言正则猜根因 |
| 数据库权限 | 管理平面幂等创建/收敛 `dig_reader`；证据工具只接收独立模块的 `DiagnosticSettings`，使用只读事务和 SELECT，不导入管理配置模块、不读取共享 `.env` |
| 用户入口 | M3 是供 M4 注册的 Python 工具接口，不增加 `evidence`/`tools` CLI；现有 M1/M2 CLI 帮助保持不变 |

## M3 范围与验收映射

| 已批准要求 | M3 实现证据 |
|---|---|
| 运行状态、失败和跳过节点 | `get_dbt_run_results` 严格读取固定 `metadata.json`、`run_results.json` 与 manifest node IDs |
| 指定节点规范化错误 | `get_dbt_node_error` 只接受该 run 中实际失败的节点；成功、跳过或不存在节点均为类型化错误 |
| 实时 relation Schema | `get_relation_schema` 使用 `dig_reader` 查询 `information_schema.columns`，并与 run 快照做一致性门禁 |
| 固定 manifest 血缘 | `get_dbt_lineage` 只读固定 `manifest.json` 的 `parent_map`/`child_map`，验证重复、悬空引用和可达环 |
| 一个或多个 EvidenceRecord | 四个方法统一返回 `tuple[EvidenceRecord, ...]`；P0 每次成功调用返回恰好一条聚合证据 |
| 非法输入不伪造空结果 | 例外带稳定英文 `code`，单元测试覆盖 run/node/relation/direction/artifact/database 错误 |
| 无写、Shell、外网、任意路径 | 工具无路径参数，只解析 32 位 hex run ID；artifact 是固定映射且拒绝 symlink escape；唯一连接是 loopback PostgreSQL，只执行只读事务中的固定 SELECT |
| 真实集成 | 一次真实 `reset → inject → build` 生成 run；四个独立集成测试分别调用四个工具，finally 恢复健康 |

M3 明确不做：M4 PydanticAI Agent、Ollama/TestModel、`Diagnosis`、prompt、模型预算、`diagnose` CLI、M5 evaluator/trace/report、自由 SQL、自由 artifact 路径、dbt log 全文搜索、生产数据库、外部 HTTP、自动修复、Airflow/OpenLineage/Marquez、Web UI 或新依赖。

## 公开接口与统一返回合同

最终只向 M4 暴露以下 facade：

```python
class EvidenceTools:
    @classmethod
    def for_run(
        cls,
        run_id: str,
        settings: DiagnosticSettings,
        project_root: Path = PROJECT_ROOT,
        *,
        db_connect: DatabaseConnect | None = None,
    ) -> EvidenceTools: ...

    def get_dbt_run_results(self, run_id: str) -> tuple[EvidenceRecord, ...]: ...

    def get_dbt_node_error(
        self,
        run_id: str,
        node_id: str,
    ) -> tuple[EvidenceRecord, ...]: ...

    def get_relation_schema(
        self,
        relation_name: str,
    ) -> tuple[EvidenceRecord, ...]: ...

    def get_dbt_lineage(
        self,
        node_id: str,
        direction: Literal["upstream", "downstream"],
    ) -> tuple[EvidenceRecord, ...]: ...
```

省略号只用于本节展示签名；各 Task 给出实现规则和测试，不允许留下 `pass`、`TODO` 或假实现。

`EvidenceRecord` 在需求规定的最小字段上增加顶层 `run_id`，内容使用四种判别联合：

```text
EvidenceRecord
├── run_id
├── evidence_id
├── evidence_type: DBT_RUN_RESULTS | DBT_NODE_ERROR | RELATION_SCHEMA | DBT_LINEAGE
├── source: dbt_artifact:run_results.json | dbt_artifact:manifest.json | postgres_catalog
├── subject
├── observed_at: timezone-aware datetime
├── content: one of four frozen Pydantic facts
└── content_digest: 64-char lowercase SHA-256
```

四种 content 精确字段如下：

- `DbtRunResultsFact`: `kind`, `run_id`, `run_status`, `dbt_exit_code`, `failed_nodes`, `skipped_nodes`。
- `DbtNodeErrorFact`: `kind`, `run_id`, `node_id`, `resource_type`, `status`, `message`。
- `RelationSchemaFact`: `kind`, `run_id`, `schema_name`, `relation_name`, `columns`；每列含 `name`, `data_type`, `nullable`, `ordinal_position`。
- `DbtLineageFact`: `kind`, `run_id`, `node_id`, `direction`, `related_nodes`；每个节点含 `node_id`, `resource_type`, `name`, `distance`。

`observed_at` 对 artifact 事实取对应 dbt artifact 的 `metadata.generated_at`；对实时 Schema 取只读数据库事务内的 `CURRENT_TIMESTAMP`。时间不参与 evidence ID，保证同一 run 内相同事实重复读取时 ID 稳定。

## 固定 artifact 与数据库边界

工具绝不接受文件路径。合法 `run_id` 只能映射到：

```text
.dig/lab/runs/<32-lowercase-hex>/metadata.json
.dig/lab/runs/<32-lowercase-hex>/schema.json
.dig/lab/runs/<32-lowercase-hex>/dbt/target/run_results.json
.dig/lab/runs/<32-lowercase-hex>/dbt/target/manifest.json
```

`metadata.json.artifacts` 必须仍精确匹配 M2 固定映射。读取前对 run root 和每个文件执行 strict resolve，并证明文件仍位于已解析的 run root 内；任何 symlink escape、缺失、非法 UTF-8、重复 JSON key、非 object 顶层、错误 schema version 或 run ID 不一致均返回 `INVALID_ARTIFACT`/`RUN_NOT_FOUND`，不回退到其他路径。

诊断数据库配置只包含 loopback host、port、database、schema 和 `dig_reader` 凭据。`EvidenceTools` 不导入管理 `Settings`，不接收管理 DSN，不调用管理面 provisioner。管理面在每次健康 baseline build 后幂等执行：

```text
CREATE/ALTER ROLE dig_reader LOGIN
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
ALTER ROLE dig_reader SET default_transaction_read_only = on
GRANT CONNECT ON DATABASE ...
GRANT USAGE ON SCHEMA analytics
GRANT SELECT ON ALL TABLES IN SCHEMA analytics
ALTER DEFAULT PRIVILEGES FOR ROLE dig_admin IN SCHEMA analytics
  GRANT SELECT ON TABLES TO dig_reader
```

用户名、数据库名和 schema 使用 `psycopg.sql.Identifier`，密码使用 `sql.Literal`；不拼接 SQL。若 reader 与 admin 用户同名，直接拒绝。现有 Docker volume 不删除，Compose 也不依赖只在首次初始化执行的脚本。

## 类型化错误合同

所有工具错误继承 `EvidenceToolError`，每个实例暴露稳定 `code`。P0 固定错误码：

```text
INVALID_RUN_ID
RUN_NOT_FOUND
RUN_CONTEXT_MISMATCH
INVALID_ARTIFACT
NODE_NOT_FOUND
NODE_ERROR_NOT_FOUND
INVALID_DIRECTION
RELATION_NOT_ALLOWED
RELATION_NOT_FOUND
RUN_STATE_DRIFT
READ_ONLY_DATABASE_ERROR
```

错误消息可用中文供人工排查，但测试和后续 M4 分支只依赖异常类型及英文 `code`，不依赖自然语言全文。

## 最终文件职责

| 文件 | 单一职责 |
|---|---|
| `src/data_incident_gym/evidence.py` | EvidenceRecord、四种事实内容、确定性 digest/ID 与类型化错误 |
| `src/data_incident_gym/evidence_tools.py` | 固定 run artifact reader 与四工具 facade；只含读取逻辑 |
| `src/data_incident_gym/read_only_db.py` | 管理平面的只读角色 provisioner；不被 evidence tool 导入 |
| `src/data_incident_gym/diagnostic_config.py` | 只定义诊断侧 loopback PostgreSQL 设置；不导入管理 `Settings`，不读取共享 `.env` |
| `src/data_incident_gym/baseline.py` | 健康 dbt build 成功后调用 provisioner，保持原有 facade/CLI 语义 |
| `.env.diagnostic.example` | 单独记录本地虚构 reader 连接示例，不含管理身份或真实 secret |
| `.gitignore` | 忽略实际 `.env.diagnostic`，继续保留现有 `.env` 管理配置边界 |
| `tests/unit/test_evidence.py` | 证据合同、canonical digest、稳定 ID、不可变/拒绝路径 |
| `tests/unit/test_evidence_tools.py` | 四个工具的隔离单元测试、固定路径、规范化、漂移与图校验 |
| `tests/unit/test_read_only_db.py` | 角色 SQL allowlist、幂等、同名拒绝、密码脱敏 |
| `tests/unit/test_diagnostic_config.py` | 诊断设置使用独立环境前缀、不含管理身份、只允许 loopback |
| `tests/unit/test_baseline.py` | provisioner 调用时机和失败传播，不改变健康构建顺序 |
| `tests/integration/test_evidence_tools.py` | 真实 M2 run 上的四个独立工具集成测试与真实只读权限证明 |

不新增 `src/data_incident_gym/artifact_store.py`、repository interface、通用 SQL client 或 PydanticAI adapter；`EvidenceTools` 自身隐藏 M3 的全部读取复杂度。

## 每个 Task 的交付协议

1. 实施时遵循根目录 `AGENT.md`：主代理编排，确需委托时只使用 `luna_worker`；每个 Task 的实现者与独立审查者不得是同一个代理。
2. 每个 Task 严格执行 RED → 最小 GREEN → 相关测试 → Ruff → 显式路径提交。
3. 每个 Task 提交后做一次对抗性审查，覆盖需求、计划、diff、测试、提交边界、只读权限、路径限制和 secret 脱敏；通过前不进入下一 Task。
4. 只修复审查发现且有证据的问题，不清理相邻代码，不改第三方 submodule。
5. `mistake.md` 追加实际命令、exit code、审查结论和 commit hash，但始终 workspace-only。

---

### Task 1: 固定 EvidenceRecord 与类型化错误合同

**Files:**

- Create: `src/data_incident_gym/evidence.py`
- Create: `tests/unit/test_evidence.py`
- Track: `docs/superpowers/plans/2026-08-25-m3-evidence-tools.md`
- Modify (workspace-only, never stage or commit): `mistake.md`

- [x] **Step 1: 先写 EvidenceRecord RED 测试**

创建 `tests/unit/test_evidence.py`，至少包含以下独立测试：

```python
def test_same_run_and_content_have_stable_evidence_id() -> None:
    first = make_run_results_record(observed_at="2026-08-25T09:00:00Z")
    second = make_run_results_record(observed_at="2026-08-25T09:01:00Z")

    assert first.evidence_id == second.evidence_id
    assert first.content_digest == second.content_digest
    assert first.observed_at != second.observed_at


def test_content_or_run_change_changes_evidence_id() -> None:
    original = make_run_results_record()
    changed_content = make_run_results_record(skipped_nodes=())
    changed_run = make_run_results_record(
        run_id="fedcba9876543210fedcba9876543210"
    )

    assert original.evidence_id != changed_content.evidence_id
    assert original.evidence_id != changed_run.evidence_id


def test_tampered_digest_and_type_content_pair_are_rejected() -> None:
    record = make_run_results_record()
    payload = record.model_dump(mode="json")
    payload["content_digest"] = "0" * 64

    with pytest.raises(ValidationError):
        EvidenceRecord.model_validate(payload)

    with pytest.raises(ValidationError):
        EvidenceRecord.create(
            run_id=RUN_ID,
            evidence_type=EvidenceType.DBT_LINEAGE,
            source=EvidenceSource.DBT_RUN_RESULTS,
            subject=RUN_ID,
            observed_at=datetime.now(UTC),
            content=run_results_fact(),
        )
```

辅助构造器必须使用固定 32 位 hex `RUN_ID` 和 timezone-aware datetime；另测 naive datetime、额外字段、非法 run ID、对 frozen model 赋值，以及每个错误子类的稳定 `code`。

- [x] **Step 2: 运行测试并确认 RED**

```powershell
uv run pytest tests/unit/test_evidence.py -q
```

Expected: exit 非零，collection 因 `data_incident_gym.evidence` 尚不存在失败；不得用 skip、xfail 或空模型绕过。

- [x] **Step 3: 实现 frozen 严格内容模型与 deterministic factory**

创建 `src/data_incident_gym/evidence.py`。所有 Pydantic model 使用：

```python
model_config = ConfigDict(frozen=True, extra="forbid")
```

canonical bytes 只能由以下规则生成：

```python
def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
```

`EvidenceRecord.create()` 的算法固定为：

```python
content_payload = content.model_dump(mode="json")
content_digest = hashlib.sha256(_canonical_bytes(content_payload)).hexdigest()
identity = {
    "content_digest": content_digest,
    "evidence_type": evidence_type.value,
    "run_id": run_id,
    "source": source.value,
    "subject": subject,
}
evidence_id = "ev_" + hashlib.sha256(_canonical_bytes(identity)).hexdigest()
```

model-level validator 必须重新计算 digest 和 ID，并验证 `content.run_id == record.run_id`、`evidence_type` 与 content kind 一一对应、`source` 合法、`observed_at` 有 timezone。不能用 UUID、Python `hash()`、本地路径、当前时间或字段插入顺序生成 ID。

- [x] **Step 4: 实现固定异常层级**

在同一文件定义 `EvidenceToolError` 及本计划“类型化错误合同”中的子类。异常构造后清除 `__cause__`/`__context__` 的 helper 与 M2 风格一致；错误对象不包含密码、SQL 或绝对 artifact 路径。

- [x] **Step 5: 跑 GREEN、格式检查并显式提交**

```powershell
uv run pytest tests/unit/test_evidence.py -q
uv run ruff check src/data_incident_gym/evidence.py tests/unit/test_evidence.py
git diff --check -- src/data_incident_gym/evidence.py tests/unit/test_evidence.py docs/superpowers/plans/2026-08-25-m3-evidence-tools.md
git add src/data_incident_gym/evidence.py tests/unit/test_evidence.py docs/superpowers/plans/2026-08-25-m3-evidence-tools.md
git diff --cached --name-only
git commit -m "feat: define M3 evidence contract"
```

Expected: tests/Ruff/diff check exit 0；cached list 精确为上述 3 个文件，不含根目录 Markdown。

---

### Task 2: 建立固定 run 边界并实现 run results/node error

**Files:**

- Modify: `.gitignore`
- Create: `.env.diagnostic.example`
- Create: `src/data_incident_gym/diagnostic_config.py`
- Create: `src/data_incident_gym/evidence_tools.py`
- Create: `tests/unit/test_diagnostic_config.py`
- Create: `tests/unit/test_evidence_tools.py`
- Modify: `src/data_incident_gym/evidence.py`
- Modify: `tests/unit/test_evidence.py`
- Modify (workspace-only, never stage or commit): `mistake.md`

- [ ] **Step 1: 写诊断配置、固定路径和两个 artifact 工具的 RED 测试**

在 `tests/unit/test_diagnostic_config.py` 写独立前缀、管理变量隔离和 loopback 限制测试。在 `tests/unit/test_evidence_tools.py` 建立一个只含固定 M2 结构的 `tmp_path` run fixture。fixture 必须写入：

- exact `metadata.json` artifact 映射与非零 `dbt_exit_code`；
- 带 `metadata.generated_at` 的 `run_results.json`；
- 含 model/test/seed nodes、`parent_map`、`child_map` 的 `manifest.json`；
- 当前故障 `schema.json`。

至少覆盖：

```text
test_diagnostic_settings_expose_no_admin_user_or_password
test_diagnostic_settings_reject_non_loopback_host
test_get_dbt_run_results_returns_failed_and_skipped_nodes
test_get_dbt_run_results_is_stable_when_called_twice
test_get_dbt_node_error_returns_normalized_message_without_absolute_path
test_get_dbt_node_error_rejects_successful_or_missing_node
test_toolset_rejects_invalid_missing_or_mismatched_run
test_artifact_reader_rejects_duplicate_keys_and_tampered_mapping
test_artifact_reader_rejects_resolved_path_outside_run_root
test_artifact_reader_rejects_invalid_generated_at_and_duplicate_node_ids
```

`get_dbt_run_results` 对 fixture 应返回一条 `DBT_RUN_RESULTS`，`run_status == "FAILED"`，失败节点精确为 `model.jaffle_shop.stg_payments`，跳过节点稳定排序。`get_dbt_node_error` 的 message 必须保留 `column "amount" does not exist`，但不得包含 fixture 绝对目录或 `compiled code at` 行。

- [ ] **Step 2: 确认 RED**

```powershell
uv run pytest tests/unit/test_diagnostic_config.py tests/unit/test_evidence_tools.py -q
```

Expected: exit 非零，`DiagnosticSettings` 与 `data_incident_gym.evidence_tools` 尚不存在。

- [ ] **Step 3: 先建立不含管理身份的 `DiagnosticSettings`**

创建 `diagnostic_config.py`，不导入 `data_incident_gym.config`，也不继承管理 `Settings`：

```python
class DiagnosticSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DIG_DIAGNOSTIC_",
        env_file=PROJECT_ROOT / ".env.diagnostic",
        extra="ignore",
    )

    postgres_host: Literal["127.0.0.1", "localhost", "::1"] = "127.0.0.1"
    postgres_port: int = 55432
    postgres_database: str = "data_incident_gym"
    postgres_schema: str = "analytics"
    postgres_user: Literal["dig_reader"] = "dig_reader"
    postgres_password: SecretStr = SecretStr("dig_reader")
```

该模块自己用 `Path(__file__).resolve().parents[2]` 定义 `PROJECT_ROOT`，不得导入管理配置、声明 admin DSN 或 subprocess environment。它只读取单独的 `.env.diagnostic` 并消费 `DIG_DIAGNOSTIC_` 前缀变量，因此不会打开共享管理 `.env`，也不会把 `DIG_POSTGRES_USER/PASSWORD` 解释为诊断身份；单元测试用 `TEST_REDACTED_VALUE` 证明管理前缀不会改变 reader 配置。

创建 `.env.diagnostic.example`，内容精确为：

```dotenv
DIG_DIAGNOSTIC_POSTGRES_HOST=127.0.0.1
DIG_DIAGNOSTIC_POSTGRES_PORT=55432
DIG_DIAGNOSTIC_POSTGRES_DATABASE=data_incident_gym
DIG_DIAGNOSTIC_POSTGRES_SCHEMA=analytics
DIG_DIAGNOSTIC_POSTGRES_USER=dig_reader
DIG_DIAGNOSTIC_POSTGRES_PASSWORD=dig_reader
```

在 `.gitignore` 的 `.env` 后增加 `.env.diagnostic`。不得创建或提交实际 secret 文件；本地默认值足以运行固定 P0 容器。

- [ ] **Step 4: 实现 `_RunArtifacts` 固定读取边界**

`src/data_incident_gym/evidence_tools.py` 内部定义私有 `_RunArtifacts`；它只接收 `project_root` 和已验证的 `run_id`，不暴露 path 参数。固定映射精确为：

```python
_RUN_FILES = {
    "metadata": Path("metadata.json"),
    "schema": Path("schema.json"),
    "run_results": Path("dbt/target/run_results.json"),
    "manifest": Path("dbt/target/manifest.json"),
}
```

为避免导入含管理设置的 `config.py`，`evidence_tools.py` 自己以 `Path(__file__).resolve().parents[2]` 定义默认 `PROJECT_ROOT`；构造时仍允许测试显式传入 `tmp_path`，但工具方法永远不接受路径。

读取规则：

1. run ID 必须匹配 `^[0-9a-f]{32}$`，否则 `INVALID_RUN_ID`。
2. 先解析固定 `.dig/lab/runs` base；候选 run root 必须存在、是目录、不是 symlink，且 resolved parent 精确等于 resolved base，否则 `RUN_NOT_FOUND`/`INVALID_ARTIFACT`。
3. `resolve(strict=True)` 后每个 artifact 必须 `is_relative_to(resolved_run_root)`，且不能是 symlink；否则 `INVALID_ARTIFACT`。
4. JSON 使用 `object_pairs_hook` 拒绝重复 key；只接受 UTF-8 object 顶层。
5. metadata keys、schema version、run ID、artifact mapping 继续精确符合 `m2.run.v1`；绝不根据 metadata 中的字符串拼路径。
6. manifest/run_results 允许不影响 M3 读取的未来扩展字段，但所有被读取字段必须做严格类型、重复 ID 和引用存在性验证。

- [ ] **Step 5: 实现 bound facade 和两个工具**

`EvidenceTools.for_run()` 先打开 `_RunArtifacts` 并验证 metadata；实例保存 `run_id`，不保存或复制任何管理 credential。

`get_dbt_run_results(run_id)`：

- 先验证参数是合法 run ID 且与 bound run 一致；
- 用 metadata `dbt_exit_code` 得出 `FAILED`/`SUCCEEDED`；
- `status in {"error", "fail"}` 进入 `failed_nodes`，`status == "skipped"` 进入 `skipped_nodes`；
- node IDs 必须唯一、存在于 manifest，两个 tuple 均排序；
- artifact `metadata.generated_at` 作为 observed_at；
- 返回恰好一条 `EvidenceRecord`。

`get_dbt_node_error(run_id, node_id)`：

- run context 校验相同；
- node 必须存在于 manifest 和 run results，且 status 是 `error` 或 `fail`；
- message 必须是非空字符串；统一 CRLF/CR 为 LF、反斜杠为 `/`、去除首尾空白和以 `compiled code at ` 开头的行；
- 不从 message 提取根因码、列名或修复建议；
- 返回恰好一条 `DBT_NODE_ERROR`。

- [ ] **Step 6: GREEN、M1/M2 verifier 回归并提交**

```powershell
uv run pytest tests/unit/test_diagnostic_config.py tests/unit/test_evidence.py tests/unit/test_evidence_tools.py tests/unit/test_lab_verifier.py -q
uv run ruff check src/data_incident_gym/diagnostic_config.py src/data_incident_gym/evidence.py src/data_incident_gym/evidence_tools.py tests/unit/test_diagnostic_config.py tests/unit/test_evidence.py tests/unit/test_evidence_tools.py
git diff --check -- .gitignore .env.diagnostic.example src/data_incident_gym/diagnostic_config.py src/data_incident_gym/evidence.py src/data_incident_gym/evidence_tools.py tests/unit/test_diagnostic_config.py tests/unit/test_evidence.py tests/unit/test_evidence_tools.py
git add .gitignore .env.diagnostic.example src/data_incident_gym/diagnostic_config.py src/data_incident_gym/evidence.py src/data_incident_gym/evidence_tools.py tests/unit/test_diagnostic_config.py tests/unit/test_evidence.py tests/unit/test_evidence_tools.py
git diff --cached --name-only
git commit -m "feat: read dbt run evidence"
```

Expected: 全部 exit 0；M2 verifier 不被重写或调用，工具读取不会写 `verification.json`。

---

### Task 3: 实现固定 manifest 的双向传递血缘

**Files:**

- Modify: `src/data_incident_gym/evidence_tools.py`
- Modify: `tests/unit/test_evidence_tools.py`
- Modify (workspace-only, never stage or commit): `mistake.md`

- [ ] **Step 1: 写 lineage RED 测试**

在已有 fixture 上新增独立测试：

```text
test_get_dbt_lineage_returns_stable_transitive_downstream_models
test_get_dbt_lineage_returns_upstream_seed
test_get_dbt_lineage_returns_record_for_valid_leaf_with_no_descendants
test_get_dbt_lineage_rejects_unknown_node_and_direction
test_get_dbt_lineage_rejects_duplicate_dangling_and_cyclic_edges
```

固定期望：

```python
assert tuple(node.node_id for node in downstream.content.related_nodes) == (
    "model.jaffle_shop.orders",
    "model.jaffle_shop.customers",
)
assert tuple(node.distance for node in downstream.content.related_nodes) == (1, 2)
assert tuple(node.node_id for node in upstream.content.related_nodes) == (
    "seed.jaffle_shop.raw_payments",
)
```

fixture 的 `customers` 必须通过 `orders` 到达，使测试真正证明传递闭包，而不是只读一层。合法叶节点的 `related_nodes == ()`，但方法仍返回一个 EvidenceRecord；只有非法 node 才返回类型化错误。

- [ ] **Step 2: 确认 RED**

```powershell
uv run pytest tests/unit/test_evidence_tools.py -q -k lineage
```

Expected: exit 非零，现有 facade 尚未实现 `get_dbt_lineage`。

- [ ] **Step 3: 实现验证后 BFS**

实现规则：

- `direction` 只允许 exact `upstream`/`downstream`，分别选择 `parent_map`/`child_map`；
- node catalog 合并 manifest `nodes` 与 `sources`，ID 不得重复；
- 从起点遍历前，对可达 adjacency 验证 list 类型、同一列表无重复、引用 node 存在；
- 用 visiting/visited 或等价三色算法拒绝可达环，不截断、不猜测；
- 遍历所有资源以保持距离正确，只把 `resource_type in {"model", "seed", "source"}` 放入输出；
- 起点不出现在 `related_nodes`；同距离按 node ID 排序，最终 key 为 `(distance, node_id)`；
- node `name` 与 `resource_type` 必须来自 manifest 且为字符串；
- observed_at 使用 manifest `metadata.generated_at`，source 固定为 `dbt_artifact:manifest.json`。

不得读取当前 `.dig/dbt/target/manifest.json`、第三方项目 target 或另一个 run。

- [ ] **Step 4: GREEN、交叉平台稳定性检查并提交**

```powershell
uv run pytest tests/unit/test_evidence_tools.py -q
uv run ruff check src/data_incident_gym/evidence_tools.py tests/unit/test_evidence_tools.py
git diff --check -- src/data_incident_gym/evidence_tools.py tests/unit/test_evidence_tools.py
git add src/data_incident_gym/evidence_tools.py tests/unit/test_evidence_tools.py
git diff --cached --name-only
git commit -m "feat: expose dbt lineage evidence"
```

Expected: exit 0；测试断言排序后的精确 tuple，不依赖 set iteration、OS path separator 或 manifest JSON 原始字段顺序。

---

### Task 4: 建立独立只读角色并实现实时 Schema 工具

**Files:**

- Create: `src/data_incident_gym/read_only_db.py`
- Modify: `src/data_incident_gym/baseline.py`
- Modify: `src/data_incident_gym/evidence_tools.py`
- Create: `tests/unit/test_read_only_db.py`
- Modify: `tests/unit/test_baseline.py`
- Modify: `tests/unit/test_evidence_tools.py`
- Modify (workspace-only, never stage or commit): `mistake.md`

- [ ] **Step 1: 写配置、provisioner 和 Schema 工具 RED 测试**

新增测试必须分别证明：

```text
test_provisioner_rejects_reader_equal_to_admin
test_provisioner_rejects_admin_and_diagnostic_location_mismatch
test_provisioner_rejects_reader_with_role_membership_or_owned_objects
test_provisioner_uses_identifiers_and_redacts_both_passwords
test_baseline_provisions_reader_after_successful_dbt_validation
test_baseline_does_not_inspect_database_when_provisioning_fails
test_get_relation_schema_uses_reader_and_returns_live_columns
test_get_relation_schema_rejects_relation_not_in_run_snapshot
test_get_relation_schema_rejects_missing_relation
test_get_relation_schema_rejects_live_schema_different_from_run_snapshot
test_evidence_tools_module_does_not_import_admin_settings_or_subprocess
```

fake DB cursor 只允许以下证据工具语句：`SET TRANSACTION READ ONLY`、`SHOW transaction_read_only`、固定参数化 `information_schema.columns`、`SELECT CURRENT_TIMESTAMP`。若代码执行 INSERT/UPDATE/DELETE/DDL、接收调用方 SQL 或使用字符串插值，测试必须失败。

- [ ] **Step 2: 确认 RED**

```powershell
uv run pytest tests/unit/test_read_only_db.py tests/unit/test_baseline.py tests/unit/test_evidence_tools.py -q
```

Expected: exit 非零，provisioner 和 Schema 工具尚不存在。

- [ ] **Step 3: 实现管理平面的幂等 `ReadOnlyRoleProvisioner`**

`read_only_db.py` 可以导入管理 `Settings` 与 `DiagnosticSettings`。它用管理连接查询 `pg_roles`，不存在则 CREATE，存在则 ALTER/收敛属性和密码；然后执行本计划“固定 artifact 与数据库边界”列出的 grants/default privileges。

限制：

- reader 名必须与 admin 名不同；
- admin 与 diagnostic 的 host、port、database、schema 必须精确一致，否则在连接或改角色前 fail closed；
- 已存在 reader 若属于任何其他 role，或拥有目标 database/schema/relation，则 fail closed；不得继承或保留隐藏写权限；
- 所有 identifier 用 `psycopg.sql.Identifier`，密码用 `sql.Literal`；
- transaction 中任何一步失败则整体回滚并抛出脱敏 `ReadOnlyProvisioningError`；
- 不删除/recreate role，不 revoke 其他用户，不 drop object，不删 volume；
- 先撤销固定 reader 在目标 database、analytics schema、其中 tables/sequences 和对应 default table privileges 上的直接权限，再只授予 CONNECT、USAGE、SELECT；不修改其他用户的 grants。

给 `BaselineBuilder` 注入一个可替换的 provisioner，并把健康 build 顺序固定为：

```text
validate_upstream_fixture
start_postgres
run_dbt
validate_dbt_artifacts
provision_read_only_role
inspect_database
write_summary
```

因此 `lab reset` 自动 provision；随后的 `amount → total_amount` rename 保留表 grants，default privileges 覆盖故障 build 新建的视图。不得修改 `IncidentLab` 的 reset/inject/build 对外语义。

- [ ] **Step 4: 实现 `get_relation_schema` 与 run-state 门禁**

`EvidenceTools` 只接收 `DiagnosticSettings` 并由它构造 reader connection kwargs。查询必须在显式只读事务中执行并验证 `transaction_read_only == "on"`。

流程固定为：

1. 从固定 `schema.json` 严格读取 relation 列表；relation 不在列表中返回 `RELATION_NOT_ALLOWED`。
2. 用 `(configured_schema, relation_name)` 参数执行固定 `information_schema.columns` SELECT，按 ordinal position 排序。
3. 无 rows 返回 `RELATION_NOT_FOUND`。
4. 实时 `(name, data_type, nullable, ordinal_position)` 与 run 快照不完全相等时返回 `RUN_STATE_DRIFT`。
5. 在同一只读事务读取 `CURRENT_TIMESTAMP`，生成一条 `RELATION_SCHEMA` EvidenceRecord。
6. 捕获数据库异常，移除 cause/context 并脱敏 reader password，返回 `READ_ONLY_DATABASE_ERROR`。

工具不得执行 row count、`SELECT *`、任意 relation SQL 或把 relation 字符串插入 SQL identifier；只查询参数化 catalog。

- [ ] **Step 5: GREEN、M1/M2 回归并显式提交**

```powershell
uv run pytest tests/unit/test_read_only_db.py tests/unit/test_baseline.py tests/unit/test_lab.py tests/unit/test_evidence_tools.py -q
uv run ruff check src/data_incident_gym/read_only_db.py src/data_incident_gym/baseline.py src/data_incident_gym/evidence_tools.py tests/unit/test_read_only_db.py tests/unit/test_baseline.py tests/unit/test_evidence_tools.py
uv lock --check
git diff --check -- src/data_incident_gym/read_only_db.py src/data_incident_gym/baseline.py src/data_incident_gym/evidence_tools.py tests/unit/test_read_only_db.py tests/unit/test_baseline.py tests/unit/test_evidence_tools.py
git add src/data_incident_gym/read_only_db.py src/data_incident_gym/baseline.py src/data_incident_gym/evidence_tools.py tests/unit/test_read_only_db.py tests/unit/test_baseline.py tests/unit/test_evidence_tools.py
git diff --cached --name-only
git commit -m "feat: enforce read-only schema evidence"
```

Expected: tests/Ruff/lock/diff check exit 0；`uv.lock` 无 diff；cached list 不含 config、env、compose、CLI、root Markdown 或 third_party。

---

### Task 5: 完成四工具真实集成、安全审查和双平台门槛

**Files:**

- Create: `tests/integration/test_evidence_tools.py`
- Modify (workspace-only, never stage or commit): `README.md`
- Modify (workspace-only, never stage or commit): `mistake.md`
- Verify without modification: `AGENT.md`
- Verify: `.github/workflows/ci.yml`
- Verify: `third_party/jaffle_shop`

- [ ] **Step 1: 写共享一次真实 run 的四个独立集成测试**

创建 module-scoped fixture；不要注入现有 function-scoped `project_root` fixture，直接使用 `data_incident_gym.config.PROJECT_ROOT`，执行一次：

```text
project_root = PROJECT_ROOT
lab.reset(CASE_ID)
lab.inject(CASE_ID)
run = lab.build(CASE_ID)
tools = EvidenceTools.for_run(run.run_id, DiagnosticSettings(_env_file=None), project_root)
yield run, tools
lab.reset(CASE_ID)  # finally
```

四个测试函数必须独立对应四个工具：

```text
test_real_get_dbt_run_results_reports_expected_failure
test_real_get_dbt_node_error_is_normalized_and_path_free
test_real_get_relation_schema_observes_total_amount_with_reader
test_real_get_dbt_lineage_finds_upstream_seed_and_downstream_models
```

精确事实：

- failed nodes 包含且在 P0 中精确等于 `model.jaffle_shop.stg_payments`；skipped nodes 至少包含 `model.jaffle_shop.orders` 和 `model.jaffle_shop.customers`。
- node error 包含 `column "amount" does not exist`，不包含 `project_root` 绝对路径；同一调用两次 ID/digest 相同。
- `raw_payments` columns 包含 `total_amount` 且不包含 `amount`，source 为 `postgres_catalog`。
- downstream model 集合精确为 `orders`/`customers`，upstream 数据节点包含 `seed.jaffle_shop.raw_payments`。

- [ ] **Step 2: 加入真实权限负面证明**

在同一集成文件另写测试，通过 `DiagnosticSettings` 直接连接 `dig_reader`：

1. `SHOW transaction_read_only` 为 `on`；
2. `SELECT` `information_schema.columns` 成功；
3. `CREATE TABLE analytics.m3_forbidden_write(id integer)` 失败；
4. 管理连接查询 `pg_roles`，证明 reader 的 `rolsuper`、`rolcreatedb`、`rolcreaterole`、`rolreplication`、`rolbypassrls` 全为 false；
5. finally/transaction rollback 后确认 `analytics.m3_forbidden_write` 不存在。

负面测试不通过工具执行写 SQL；它只验证底层数据库角色，即使未来工具层出现缺陷也没有持久化写权限。

- [ ] **Step 3: 运行 M3 真实集成并确认最终恢复**

```powershell
uv run pytest tests/integration/test_evidence_tools.py -q -s
uv run data-incident-gym pipeline build
uv run data-incident-gym lab reset schema_rename_payment_amount
```

Expected: 三条命令 exit 0；四工具事实精确，写入被 PostgreSQL 拒绝，finally 和显式 reset 都恢复健康。失败时不得删 Docker volume、扩大 grants 或改 Ground Truth。

- [ ] **Step 4: 更新 workspace-only 文档状态**

在 `README.md` 的当前状态中把 M3 改为已实现，并明确 M4/M5 尚未实现；新增一个“作为 Python API 使用 M3”小节，只展示 `EvidenceTools.for_run(...)` 和四个方法签名，不添加不存在的 CLI。

在 `mistake.md` 追加 M3 决策、实际验证、审查和 commit 记录。两者保持未暂存、未提交、未推送；`AGENT.md` 保持用户当前修改不变。

- [ ] **Step 5: 完成本地总门槛**

```powershell
uv run ruff check .
uv run pytest tests/unit -q
uv run pytest tests/integration -q
uv run pytest tests/e2e -q
uv lock --check
git -C third_party/jaffle_shop rev-parse HEAD
git -C third_party/jaffle_shop status --short
git diff --check
git status --short
```

Expected:

- Ruff、unit、integration、e2e、lock 和 diff check 全部 exit 0。
- submodule HEAD 仍为 `36bde6cba69d962b83be1d52fc65a0dce1cb4ebb`，status 无输出。
- 现有 M1/M2 CLI 行为和帮助无变化；M2 十次复现与恢复测试仍通过。
- `git status --short` 中，待提交项只有本 Task 的 integration test；根目录 3 个 Markdown 仍是未暂存修改。
- `uv.lock`、`compose.yaml`、`cli.py`、requirements 和 third_party 无变更。

- [ ] **Step 6: 提交测试、独立最终审查并等待远程授权**

```powershell
git add tests/integration/test_evidence_tools.py
git diff --cached --name-only
git commit -m "test: verify M3 evidence tools"
git status --short --branch
```

Expected: cached list 只含 integration test；提交后 status 仍只显示根目录 `AGENT.md`、`README.md`、`mistake.md` 的既定 workspace-only 修改。

提交后由未参与实现的 `luna_worker` 对从 M2 基线 `4e2d169` 到 M3 HEAD 的全部 diff 做最终对抗性审查。只有用户明确授权 push 后才推送；必须实际观察 Ubuntu CI 对 M3 HEAD 成功，才能把 M3 标记为正式完成。本地等价命令不能写成远程 CI 已通过。

---

## M3 最终完成门槛

- [ ] 用户已批准本实施计划并明确授权开始执行。
- [ ] `EvidenceTools.for_run` 拒绝非法、缺失、映射篡改和跨 run 上下文。
- [ ] 四个公开方法签名与需求一致，每次成功调用返回至少一条严格 `EvidenceRecord`。
- [ ] 同一 run、同一事实的 `evidence_id`/`content_digest` 跨重复调用稳定；内容或 run 改变时 ID 改变。
- [ ] run results 精确报告运行状态、失败节点和跳过节点。
- [ ] node error 来自真实失败节点，规范化后不含绝对 compiled path，不用自然语言猜根因。
- [ ] relation schema 由实时 `dig_reader` catalog 查询获得，并拒绝不属于 run 或已 reset/漂移的状态。
- [ ] lineage 只来自该 run manifest，双向传递结果、排序和 graph 错误均有测试。
- [ ] reader 与 admin 分离，默认只读且无 superuser/createdb/createrole/replication/bypassrls；真实 DDL 写入被拒绝。
- [ ] 工具没有 Shell、外部网络、任意路径、任意 SQL、数据库写、源文件修改能力。
- [ ] 每个工具均有独立单元测试和真实 artifact/PostgreSQL 集成测试。
- [ ] M1/M2 unit、integration、e2e 全量回归通过，submodule 固定且 clean。
- [ ] 不新增依赖，`uv.lock` 无变化；Ruff、tests、lock、diff check 全部通过。
- [ ] 每个 Task 的独立审查和 M3 最终对抗性审查通过。
- [ ] 根目录 3 个 Markdown 保持既定未暂存、未提交、未推送状态。
- [ ] 用户授权推送后，Ubuntu CI 对 M3 HEAD 实际成功。
- [ ] 未开始 M4 Agent、模型或 Diagnosis 工作。

## 实施停止规则

遇到以下任一情况，保留完整脱敏证据并暂停当前 Task，向用户请求决策：

1. 真实 dbt artifact 缺少稳定 `generated_at`、node status/message 或 parent/child map，无法在不猜测的前提下满足合同。
2. 只有读取 M2 固定目录以外的任意路径或扫描 dbt log 全文，才能实现任一工具。
3. Schema 工具只有使用 admin 连接、任意 SQL、非 loopback 数据库或放宽 relation 范围才能工作。
4. PostgreSQL reader 只有获得写权限、角色成员关系、superuser-like 属性或删除现有 volume 才能通过测试。
5. reader grants 导致 M1/M2 build、reset/inject/build 语义变化，或无法在不修改 third_party 源码的情况下维持。
6. 需要新增/升级依赖、修改 `uv.lock`、固定 Python/dbt/PostgreSQL 版本或 CI runner。
7. Windows 与 Ubuntu 对 message normalization、lineage order、canonical digest/ID 得出不同结果。
8. 任一异常、测试输出或 artifact 暴露 admin/reader 密码、管理 DSN 或未脱敏绝对路径。
9. 需求必须扩大到 M4/M5、自由 SQL、任意 artifact、生产连接或新 CLI 才能继续。
10. push/远程操作尚未获得用户明确授权，或远程 Ubuntu CI 与本地结果不一致。
