# M4 Diagnosis Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不泄露 Ground Truth、不扩大诊断权限且不提前实现 M5/P1 的前提下，交付一个由 PydanticAI 驱动、能自主调用 M3 四个只读工具、受确定性 Controller 约束并输出固定 `Diagnosis` 的 M4 单 Agent 闭环。

**Architecture:** 新增深模块 `DiagnosisRunner.for_run(run_id, settings).diagnose(case_id)` 作为唯一应用接口；PydanticAI/OpenAI-compatible 模型只是内部 Adapter，M3 `EvidenceTools` 是只读取证 Adapter。Controller 在 prompt 外维护 run-scoped evidence inventory、调用指纹、预算、可观察 trace 和终态门禁：模型负责选择工具与提出结构化诊断，Controller 负责允许哪些动作、证据是否属于本 run、何时拒绝确认以及何时返回固定终态。M2 管理平面仅新增一个“当前已验证故障 run”交接指针，诊断平面不读取 Ground Truth，也不获得任何写工具。

**Tech Stack:** Python 3.12.10, uv 0.11.24, Pydantic 2.13.4, PydanticAI Slim 2.34.0 with OpenAI extra, OpenAI-compatible Chat Completions, Ollama `gemma4:e4b`, pytest 9.1.1, pytest-asyncio 1.4.0, Ruff 0.16.4, M3 `EvidenceTools`

---

## 批准状态与执行边界

- 用户于 2026-08-25 明确确认 M3 已完成；本地实际观察到 `HEAD` 与 `origin/master` 均为 `ffd56a1cd724557177a316422dc2827ee9ee3dba`，提交主题为 `test: verify M3 evidence tools`。
- 用户确认远程 Ubuntu CI 已通过属于人工观测事实；计划执行者不得把本地等价测试冒充新的远程 CI 结果。
- 根目录 `AGENT.md`、`README.md`、`mistake.md` 当前已有用户保留的未提交修改。不得撤销或覆盖它们；`AGENT.md` 不编辑，`README.md` 与 `mistake.md` 即使在实施中追加记录，也保持未暂存、未提交、未推送。
- 本文档获准编写不等于获准实施。只有用户另行批准本计划后，才允许开始 Task 1；开始时先使用 `superpowers:using-git-worktrees` 建立隔离工作区，再按根目录 `AGENT.md` 的 `luna_worker` 实施/独立审查规则执行。
- 全部实施严格控制为 5 个 Task。每个 Task 都先 RED、再最小 GREEN、再相关回归、再独立对抗性审查、最后显式路径提交。
- 不执行 push、不创建 PR、不切换模型；这些都需要用户单独授权。不得使用 `git add .`、`git add -A` 或其他宽范围暂存命令。

## M4 共识基线

| 决策 | 本计划采用的精确语义 |
|---|---|
| 项目重心 | M4 的主体是诊断 Agent；Incident Lab 是可复现考场，M3 是只读取证层，不把 Agent 退化成固定数据管道脚本。 |
| 单 Agent | 只定义一个 PydanticAI Agent；不引入多 Agent、LangGraph、planner/executor 团队或自由聊天。 |
| 深模块接口 | 应用只依赖 `DiagnosisRunner.for_run(run_id, settings).diagnose(case_id)`；PydanticAI、工具注册、状态、预算、门禁和错误映射全部隐藏在实现内。 |
| 模型与 Controller 分工 | 模型自主选择下一项只读证据并生成 `Diagnosis`；Controller 确定性执行 allowlist、run scope、去重、预算、证据登记和最终门禁。 |
| 最小 Diagnostic Kernel seam | M4 只加入 evidence inventory、exact-call fingerprint、预算、trace 和终态门禁，为 P1 深化留出真实 seam，但不提前实现完整 Kernel。 |
| run 选择 | `diagnose CASE_ID` 使用 M2 在验证成功后发布的固定 active-run 指针；`--run-id` 可显式选择。绝不按 mtime、目录名或“最新”猜测 run。 |
| Ground Truth 隔离 | `diagnostic_agent.py`、`diagnosis.py`、`run_context.py` 不得导入 `GroundTruth`、`load_ground_truth`、`IncidentVerifier` 或 `lab_verifier`。正确根因/精确影响集合留给 M5 Evaluator。 |
| 模型配置 | 使用 `OpenAIChatModel` + `OpenAIProvider(base_url, api_key)`，默认 `http://127.0.0.1:11434/v1` 与 `gemma4:e4b`；不调用 Ollama 私有 API。 |
| 公开工具 | 模型恰好看到 M3 的四个工具；没有 Shell、任意文件、自由 SQL、HTTP、数据库写、源码修改或修复执行工具。 |
| 终态 | 只允许 `CONFIRMED`、`INSUFFICIENT_EVIDENCE`、`MODEL_ERROR`。Controller 不认可的确认必须 fail closed。 |
| 固定预算 | 每 run 最多 6 次模型请求、8 次已接纳工具调用尝试、2 次结构化输出校验重试、总时限 180 秒；不暴露成可由环境变量放宽的配置。 |
| trace 边界 | 只记录工具名、规范化参数、fingerprint、证据 ID、稳定错误码、门禁结果、token/次数/耗时；不记录或展示隐藏推理。 |
| M5 边界 | M4 只返回内存中的 `DiagnosisRunResult`；不写 `artifacts/<run_id>`、不生成报告、不执行 Ground Truth 评分、不实现 `eval run`。完整 `doctor` 命令也留到 M5/P0 集成任务复用本 runner 的能力探针。 |
| P1 边界 | 不实现多假设 ledger、EvidenceGap 排序、claim-evidence compatibility matrix、静态 Skill baseline、消融或跨变体指标；这些在 P1 有多个故障类型后实现。 |

## 为什么必须有 prompt 外的 Controller

单纯把 M3 四个函数注册给模型，仍然接近“诊断数据管道 Skill”：安全、预算、证据充分性和引用真实性都只靠提示词。M4 的最小 Controller 把这些规则变成代码事实：

```text
case_id + verified run_id
          │
          ▼
  DiagnosisRunner（唯一 Interface）
          │
          ├── PydanticAI Adapter ──► OpenAI-compatible model
          │           │
          │           ▼
          │    仅四个 M3 工具请求
          │           │
          ▼           ▼
  run-scoped Controller State
  ├── evidence inventory: evidence_id → EvidenceRecord
  ├── exact tool fingerprints
  ├── admitted tool-attempt budget
  ├── observable trace
  └── shared RunUsage / deadline
          │
          ▼
  deterministic evidence gate
  ├── scope/citation invalid → output retry；耗尽为 MODEL_ERROR
  ├── 证据类型不足 → INSUFFICIENT_EVIDENCE
  ├── affected asset 无证据支持 → INSUFFICIENT_EVIDENCE
  └── 支持充分 → CONFIRMED
```

这条 seam 是真实的：生产 Adapter 使用 OpenAI-compatible 模型，自动测试 Adapter 使用 PydanticAI `TestModel`/`FunctionModel`；M4 不创建自有通用 fake-model 框架。

## PydanticAI 2.34.0 已核对的实现事实

计划以 2026-08-25 的官方接口为准：

- [`OpenAIChatModel` 与 `OpenAIProvider`](https://pydantic.dev/docs/ai/models/openai/) 支持自定义 OpenAI-compatible `base_url`/`api_key`。
- [`TestModel` 与 `FunctionModel`](https://pydantic.dev/docs/ai/guides/testing/) 是官方无真实模型测试方式；常规测试应设置 `pydantic_ai.models.ALLOW_MODEL_REQUESTS = False`。
- [`UsageLimits`](https://pydantic.dev/docs/ai/api/pydantic-ai/usage/) 的 `request_limit` 限制模型请求，`tool_calls_limit` 只统计成功工具调用；因此 M4 另以 Controller 统计失败和重复在内的已接纳调用尝试。
- [`ToolFailed`](https://pydantic.dev/docs/ai/tools-toolsets/tools-advanced/#tool-errors) 表示工具已失败、由模型改换调查路径；`ModelRetry` 用于可纠正的输出 scope/引用错误。
- [output validator](https://pydantic.dev/docs/ai/core-concepts/output/#output-validators) 的 `ModelRetry` 消耗独立 output retry budget；M4 固定为 2。
- 工具默认可能并行执行；M4 在每次 run 外包裹 `agent.parallel_tool_call_execution_mode("sequential")`，使状态、预算和 trace 顺序确定。

若实施时锁定版本的真实 API 与上述事实不一致，停止 Task 1，保留解析/测试证据并请求用户决定版本；不得用兼容 shim 或模型专属补丁掩盖。

## 最终公开接口

`DiagnosisRunner` 是 M4 唯一的应用 Interface。`model` 和 `tools` 是两个真实 Adapter seam：前者有生产模型/TestModel，后者有真实 M3/隔离测试实现。

公开签名固定为：`DiagnosisRunner.for_run(cls, run_id: str, settings: DiagnosticSettings, project_root: Path = PROJECT_ROOT, *, model: Model | None = None, tools: EvidenceTools | None = None) -> DiagnosisRunner`，以及 `await runner.diagnose(incident_case_id: str) -> DiagnosisRunResult`。Task 实施不得留下未实现分支或空返回。

CLI 只做同步适配和展示：

```powershell
uv run data-incident-gym diagnose schema_rename_payment_amount
uv run data-incident-gym diagnose schema_rename_payment_amount --run-id 0123456789abcdef0123456789abcdef
```

第一条只读取 `.dig/lab/active_fault_run.json`；第二条读取显式 run 的固定 `metadata.json` 并验证 `incident_case_id` 一致。两种路径最终都由 `EvidenceTools.for_run` 做 M3 的完整 artifact/run-state 门禁。

## `Diagnosis` 与运行结果合同

`src/data_incident_gym/diagnosis.py` 定义严格、冻结、`extra="forbid"` 的合同：

```text
Diagnosis
├── status: CONFIRMED | INSUFFICIENT_EVIDENCE | MODEL_ERROR
├── incident_case_id
├── run_id
├── root_cause_code: UPPER_SNAKE_CASE | null
├── summary
├── affected_assets[]
├── evidence_ids[]
├── recommended_actions[]
└── confidence: strict float, 0.0..1.0

DiagnosisRunResult
├── diagnosis: Diagnosis
├── evidence_records[]: 本 run 实际收集的去重 EvidenceRecord
├── trace[]: ToolTraceEvent | EvidenceGateTraceEvent
└── metrics: model/provider + requests/tokens/tool attempts/successes/elapsed_ms
```

Schema 自身固定以下不变量：

1. 所有 tuple 字段拒绝重复值，字符串拒绝空白值。
2. `CONFIRMED` 必须有非空 `root_cause_code`、`affected_assets`、`evidence_ids` 和 `recommended_actions`。
3. `INSUFFICIENT_EVIDENCE` 与 `MODEL_ERROR` 必须令 `root_cause_code=null` 且 `affected_assets=[]`，不得保留未证实主张；它们可以引用已真实收集且通过 scope 校验的证据。
4. `MODEL_ERROR` 只能由 Controller 接受或构造，错误摘要只含固定 code，不拼接 provider 异常、路径、凭据、SQL 或 traceback。
5. `confidence` 仅展示，不参与 M4 门禁和之后的主要评分。

## run-scoped Controller 状态与动作合同

内部 `_InvestigationState` 每次 `diagnose()` 新建，不跨 run 复用：

```text
run_id
incident_case_id
evidence_by_id: insertion-ordered dict
seen_tool_fingerprints: set
tool_call_attempts: int
successful_tool_calls: int
trace: list
started_monotonic: float
```

工具 fingerprint 固定为以下 canonical JSON 的 SHA-256：

```json
{
  "arguments": {"按参数名排序": "严格字符串值"},
  "run_id": "绑定的 32 位小写 hex",
  "tool_name": "四个 allowlist 名称之一"
}
```

执行顺序必须是：

1. 若已接纳尝试数等于 8，记录 `TOOL_CALL_LIMIT` 并中止 Agent run；第 9 个请求绝不进入 M3。
2. 把本次请求计入已接纳尝试。
3. 计算 fingerprint；若已存在，记录 `DUPLICATE_TOOL_CALL`，不再次调用 M3，以 `ToolFailed` 告知模型改换路径。
4. 调用对应 M3 方法；只把 `EvidenceToolError.code` 返回模型，不返回自然语言异常全文。
5. 验证每个返回记录的 `run_id` 与绑定 run 一致；同 ID 若内容冲突，按 Controller invariant failure 中止为 `MODEL_ERROR`。
6. 将真实记录按首次观察顺序登记，把 `evidence_id` 和规范化记录返回模型并写入 trace。

PydanticAI 同时使用 `UsageLimits(request_limit=6, tool_calls_limit=8)` 做第二层保护，并传入共享 `RunUsage()`，这样即使 run 异常退出也能形成 metrics。整个 `agent.run()` 包在 `asyncio.timeout(180)` 中；所有工具执行用 sequential mode。

## 确定性最终门禁

模型输出先经过 Pydantic Schema；框架最多重试 2 次。随后 output validator/Controller 按固定顺序处理：

1. `incident_case_id` 或 `run_id` 与上下文不一致：trace 记录 `OUTPUT_SCOPE_MISMATCH`，抛 `ModelRetry`；重试耗尽映射为 `MODEL_ERROR`。
2. 任一 `evidence_id` 不在当前 inventory，或记录不属于当前 run：记录 `UNKNOWN_EVIDENCE_ID`，抛 `ModelRetry`；重试耗尽映射为 `MODEL_ERROR`。
3. 合法的 `INSUFFICIENT_EVIDENCE`：直接接受；它已由 Schema 保证不包含根因/影响主张。
4. 模型返回 `MODEL_ERROR`：Controller 保留状态但用固定 `MODEL_DECLINED` code 和安全摘要重建，不能信任模型提供的错误细节。
5. `CONFIRMED` 引用的证据必须至少覆盖 `DBT_NODE_ERROR`、`RELATION_SCHEMA`、`DBT_LINEAGE`。缺任一类型时，确定性降级为 `INSUFFICIENT_EVIDENCE`，理由 `EVIDENCE_TYPES_INCOMPLETE`。
6. `affected_assets` 的每一项必须由已引用证据支持：直接失败节点由 `DbtNodeErrorFact.node_id` 的 node name 支持；间接节点只由已引用、`direction="downstream"` 的 `DbtLineageFact.related_nodes[].name` 支持。存在无支持项时降级为 `INSUFFICIENT_EVIDENCE`，理由 `AFFECTED_ASSET_UNSUPPORTED`。
7. 门禁不判断 `root_cause_code` 是否等于某案例 Ground Truth，也不判断影响集合是否完整；这些精确正确性检查只由 M5 Evaluator 完成。

降级不是静默修补：trace 必须保留原门禁 reason code；返回的不足证据诊断使用固定安全摘要，并只保留模型已引用且真实存在的 evidence IDs。

## Agent prompt 边界

系统 prompt 使用英文、版本受 Git 管理，并固定包含：

- 只能依据四个工具返回的 `EvidenceRecord` 调查，不得依据 case 名猜答案。
- 先识别失败节点，再主动选择能验证根因与下游影响的证据；不规定死板的固定脚本。
- 不得重复完全相同的工具调用；工具失败后应改变参数、工具或返回不足证据。
- 只有收集到 dbt error、relation schema 和 downstream lineage 的相容证据时才可 `CONFIRMED`。
- 所有引用必须来自工具真实返回的 evidence ID；建议仅为文本，不能执行。
- 根因编码使用受版本控制的诊断 ontology；P0 只定义 `SOURCE_SCHEMA_COLUMN_RENAMED` 的一般语义“来源字段已改名而消费者仍引用旧字段”，不在 prompt 中写案例节点、列名、关系名或正确影响集合。
- 证据不足、工具不可用或事实冲突时输出 `INSUFFICIENT_EVIDENCE`，不得补猜。

用户 prompt 由代码固定构造，仅含 `incident_case_id`、`run_id` 和“调查这个运行”的指令；CLI 不接收自由文本。

## 固定错误与终态 reason codes

M4 新增并测试以下稳定 code；代码分支不得依赖自然语言异常全文：

```text
ACTIVE_RUN_NOT_FOUND
INVALID_RUN_CONTEXT
RUN_CASE_MISMATCH
DUPLICATE_TOOL_CALL
TOOL_CALL_LIMIT
MODEL_REQUEST_LIMIT
MODEL_TIMEOUT
MODEL_PROTOCOL_ERROR
MODEL_RUNTIME_ERROR
MODEL_DECLINED
OUTPUT_SCOPE_MISMATCH
UNKNOWN_EVIDENCE_ID
EVIDENCE_TYPES_INCOMPLETE
AFFECTED_ASSET_UNSUPPORTED
CONFIRMED
MODEL_RETURNED_INSUFFICIENT_EVIDENCE
```

M3 的错误 code 原样作为工具失败 code 进入 trace/model-visible `ToolFailed`，但不把 M3 的自然语言消息、cause/context 或敏感值送给模型。

## 最终文件职责

| 文件 | 单一职责 |
|---|---|
| `src/data_incident_gym/diagnosis.py` | `Diagnosis`、状态、trace、metrics 与 `DiagnosisRunResult` 严格合同 |
| `src/data_incident_gym/run_context.py` | active-run 指针合同，以及固定路径的 case/run 解析与严格校验；不读取 Ground Truth |
| `src/data_incident_gym/diagnostic_agent.py` | 深模块 `DiagnosisRunner`、PydanticAI Adapter、四工具桥、run state、预算、trace、门禁与终态映射 |
| `src/data_incident_gym/diagnostic_config.py` | 在现有只读 PostgreSQL 设置上增加 OpenAI-compatible 模型设置；不含管理凭据 |
| `src/data_incident_gym/lab.py` | M2 验证成功后原子发布 active-run；reset/inject/新 build 清除旧指针，这是唯一回触 M2 的实现 |
| `src/data_incident_gym/cli.py` | `diagnose CASE_ID [--run-id]` 同步入口、中文消息、JSON 展示与退出码 |
| `tests/unit/test_diagnosis.py` | 严格输出/trace/metrics 合同测试 |
| `tests/unit/test_run_context.py` | active/explicit run 固定路径、scope、JSON/symlink/tamper 测试 |
| `tests/unit/test_diagnostic_agent.py` | TestModel/FunctionModel、四工具、状态、去重、预算、门禁、错误和 trace 测试 |
| `tests/integration/test_diagnostic_agent.py` | 官方 FunctionModel + 真实 M2 run + 真实 M3 只读工具集成 |
| `tests/e2e/test_ollama_diagnosis.py` | 显式 opt-in 的真实 `gemma4:e4b` 工具调用和结构化输出验证 |

不创建通用 workflow engine、repository 层、模型 fallback、JSON repair、regex parser、RAG、memory、hypothesis graph、自动修复器或 M5 artifact writer。

## 每个 Task 的交付协议

1. 依照根目录 `AGENT.md`：实现只委托 `luna_worker`；同一 Task 的实现者与独立审查者不得是同一个代理。
2. 每个 Task 严格执行 RED → 最小 GREEN → 相关 tests → Ruff → lock/diff check → 显式路径 commit。
3. 每个 Task 提交后，由新鲜 `luna_worker` 对计划、需求、diff、测试、权限、secret、提交边界做对抗性审查；审查通过前不进入下一 Task。
4. 只修复有证据且在 M4 范围内的问题；不清理相邻代码，不修改 third-party submodule，不升级无关依赖。
5. `mistake.md` 追加实际命令、exit code、审查结论与 commit hash，但始终 workspace-only。

---

### Task 1: 锁定依赖、模型设置与 Diagnosis 合同

**Files:**

- Create: `src/data_incident_gym/diagnosis.py`
- Create: `tests/unit/test_diagnosis.py`
- Modify: `src/data_incident_gym/diagnostic_config.py`
- Modify: `tests/unit/test_diagnostic_config.py`
- Modify: `.env.diagnostic.example`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Track: `docs/superpowers/plans/2026-08-25-m4-diagnosis-agent.md`
- Modify (workspace-only, never stage or commit): `mistake.md`

- [x] **Step 1: 先写严格 Diagnosis 与配置 RED 测试**

`tests/unit/test_diagnosis.py` 至少覆盖：

```python
def test_confirmed_requires_root_assets_evidence_and_actions() -> None:
    payload = confirmed_payload()
    payload["evidence_ids"] = []
    with pytest.raises(ValidationError):
        Diagnosis.model_validate(payload)


@pytest.mark.parametrize("status", ["INSUFFICIENT_EVIDENCE", "MODEL_ERROR"])
def test_nonconfirmed_status_rejects_unproven_claims(status: str) -> None:
    payload = nonconfirmed_payload(status)
    payload["root_cause_code"] = "SOURCE_SCHEMA_COLUMN_RENAMED"
    with pytest.raises(ValidationError):
        Diagnosis.model_validate(payload)


def test_diagnosis_rejects_extra_fields_duplicate_ids_and_coercion() -> None:
    assert_invalid({"unexpected": "value"})
    assert_invalid({"evidence_ids": [EVIDENCE_ID, EVIDENCE_ID]})
    assert_invalid({"confidence": "0.9"})
```

另测：冻结、run/evidence/root-code pattern、空白字符串、重复 assets/actions、confidence 边界；`DiagnosisRunResult` 只能包含当前合同的 evidence/trace/metrics，序列化不产生任意额外字段。

`tests/unit/test_diagnostic_config.py` 新增：

- 默认 `model_base_url == http://127.0.0.1:11434/v1`、`model_name == gemma4:e4b`、dummy local API key 是 `SecretStr`。
- `DIG_DIAGNOSTIC_MODEL_BASE_URL`、`DIG_DIAGNOSTIC_MODEL_NAME`、`DIG_DIAGNOSTIC_MODEL_API_KEY` 能精确覆盖。
- URL 拒绝非 HTTP(S)、userinfo、query、fragment；空 model name/key 拒绝。
- `repr`、validation error 和 `model_dump()` 不泄露 API key；既有管理环境变量仍不能覆盖只读诊断身份。
- 预算字段不存在于设置中，不能通过环境变量放宽。

- [x] **Step 2: 确认 RED**

```powershell
uv run pytest tests/unit/test_diagnosis.py tests/unit/test_diagnostic_config.py -q
```

Expected: exit 非零；`diagnosis.py` 与新模型设置尚不存在。

- [x] **Step 3: 用 uv 锁定最小依赖**

```powershell
uv add "pydantic-ai-slim[openai]==2.34.0"
uv add --dev "pytest-asyncio==1.4.0"
uv lock --check
```

只接受由这两条命令产生的 `pyproject.toml`/`uv.lock` 变化。检查解析结果仍使用 Python 3.12、Pydantic 2.13.4，且没有意外引入 Logfire、MCP、Web UI、embedding/vector 依赖或第二个 Agent 框架。

- [x] **Step 4: 实现严格合同与模型设置**

`DiagnosisStatus` 使用 `StrEnum`。所有 Pydantic 模型统一 `ConfigDict(frozen=True, extra="forbid")`；tuple 去重和状态相关不变量由 `model_validator(mode="after")` 完成，禁止在序列化后修补。

`DiagnosticSettings` 继续只读 `.env.diagnostic` 与 `DIG_DIAGNOSTIC_` 前缀；新增：

```text
model_base_url = http://127.0.0.1:11434/v1
model_name = gemma4:e4b
model_api_key = SecretStr("ollama-local")
```

`.env.diagnostic.example` 使用虚构 local 值，不加入任何真实 key。固定预算只作为 `diagnostic_agent.py` 常量，不进入设置。

- [x] **Step 5: GREEN、静态检查并显式提交**

```powershell
uv run pytest tests/unit/test_diagnosis.py tests/unit/test_diagnostic_config.py -q
uv run ruff check src/data_incident_gym/diagnosis.py src/data_incident_gym/diagnostic_config.py tests/unit/test_diagnosis.py tests/unit/test_diagnostic_config.py
uv lock --check
git diff --check -- pyproject.toml uv.lock .env.diagnostic.example src/data_incident_gym/diagnosis.py src/data_incident_gym/diagnostic_config.py tests/unit/test_diagnosis.py tests/unit/test_diagnostic_config.py docs/superpowers/plans/2026-08-25-m4-diagnosis-agent.md
git add pyproject.toml uv.lock .env.diagnostic.example src/data_incident_gym/diagnosis.py src/data_incident_gym/diagnostic_config.py tests/unit/test_diagnosis.py tests/unit/test_diagnostic_config.py docs/superpowers/plans/2026-08-25-m4-diagnosis-agent.md
git diff --cached --name-only
git commit -m "feat: define M4 diagnosis contract"
```

Expected: tests/Ruff/lock/diff exit 0；cached list 精确等于上述 8 个实现/测试/计划路径，不包含根目录 Markdown、CLI、lab、CI 或 third_party。

---

### Task 2: 建立 verified-run 交接与单 Agent 深模块

**Files:**

- Create: `src/data_incident_gym/run_context.py`
- Create: `tests/unit/test_run_context.py`
- Create: `src/data_incident_gym/diagnostic_agent.py`
- Create: `tests/unit/test_diagnostic_agent.py`
- Modify: `src/data_incident_gym/lab.py`
- Modify: `tests/unit/test_lab.py`
- Modify (workspace-only, never stage or commit): `mistake.md`

- [ ] **Step 1: 写 active-run 生命周期与 Agent 注册 RED 测试**

新增测试必须证明：

```text
test_build_publishes_active_run_only_after_expected_failure_verification
test_failed_verification_never_publishes_active_run
test_reset_and_inject_clear_stale_active_run_before_state_change
test_active_run_write_is_atomic_and_rejects_symlink
test_resolve_active_run_requires_matching_case_and_metadata
test_explicit_run_id_rejects_case_mismatch_and_invalid_metadata
test_runner_registers_exactly_the_four_m3_tools
test_testmodel_calls_registered_tools_and_returns_strict_diagnosis
test_default_adapter_is_openai_chat_completions_without_making_a_request
```

TestModel 测试使用 `custom_output_args` 生成合法 Diagnosis，使用一个只实现四个 M3 方法的窄 fake tools 对象；然后检查 `last_model_request_parameters.function_tools` 的名字精确为：

```python
{
    "get_dbt_run_results",
    "get_dbt_node_error",
    "get_relation_schema",
    "get_dbt_lineage",
}
```

并证明不存在第五个工具、native tool、自由 prompt、Shell/HTTP/file/SQL 参数。

- [ ] **Step 2: 确认 RED**

```powershell
uv run pytest tests/unit/test_run_context.py tests/unit/test_diagnostic_agent.py tests/unit/test_lab.py -q
```

Expected: exit 非零；active-run 合同和 `DiagnosisRunner` 尚不存在。

- [ ] **Step 3: 实现固定 active-run 交接，不扫描“最新”目录**

固定文件为 `.dig/lab/active_fault_run.json`，内容只有：

```json
{
  "incident_case_id": "schema_rename_payment_amount",
  "run_id": "0123456789abcdef0123456789abcdef",
  "schema_version": "m4.active_fault_run.v1",
  "verification_status": "EXPECTED_FAILURE"
}
```

`IncidentLab` 的顺序精确调整为：

```text
reset: 验证/恢复健康 → 清除 fixed active pointer → 返回
inject: 确认健康 → 清除 fixed active pointer → 注入 → 验证已注入 → 返回
build: 确认已注入 → 清除 fixed active pointer → 创建 run/执行 dbt/写产物
       → IncidentVerifier.verify 成功 → 原子 publish active pointer → 返回 FaultRun
```

publish 使用同目录固定 `.tmp` + `Path.replace()`；目标或 parent 为 symlink、case/run/status 不合法、写入/replace 失败时抛脱敏 `IncidentExecutionError`。不得删除 run 目录、Ground Truth 或 Docker volume；clear 只允许 unlink 这个固定文件/固定 temp 文件。

`run_context.py` 只读取固定 active pointer 或 `.dig/lab/runs/<run_id>/metadata.json`。它严格拒绝重复 JSON key、额外/缺失 key、非法 UTF-8、symlink、escape、schema/status/run/case 不一致；不导入 `incidents.py` 或 `lab_verifier.py`。显式 run 只绕过 active pointer，不绕过 metadata case scope 与 M3 artifact 验证。

- [ ] **Step 4: 实现 `DiagnosisRunner` facade、模型 Adapter 与四工具桥**

`DiagnosisRunner.for_run`：

1. 接收固定 run、诊断设置、project root，以及可选 PydanticAI `Model`/M3 `EvidenceTools` Adapter。
2. 无 model 时构造 `OpenAIChatModel(settings.model_name, provider=OpenAIProvider(base_url=str(settings.model_base_url), api_key=settings.model_api_key.get_secret_value()))`；构造不发请求。
3. 无 tools 时调用 `EvidenceTools.for_run`；永不构造/导入管理 `Settings`。
4. 每个 `diagnose` 新建 state 和 PydanticAI Agent，`deps_type` 是该 run state，`output_type` 是 `Diagnosis`。
5. 注册恰好四个同名、同参数语义工具；首两个的 `run_id` 必须等于绑定 run，后两个由绑定 run 的 M3 facade 取证。
6. 使用固定 system/user prompt；不接受调用者自由文本，不在 prompt 中嵌入 Ground Truth。

Task 2 先完成真实调用/结构化输出的最小路径；Task 3 再补齐全部预算、去重、门禁和错误映射。不得为了过测试加入通用 fake model、硬编码 P0 证据或预先调用固定工具序列。

- [ ] **Step 5: GREEN、M2 回归并显式提交**

```powershell
uv run pytest tests/unit/test_run_context.py tests/unit/test_diagnostic_agent.py tests/unit/test_lab.py tests/unit/test_evidence_tools.py -q
uv run ruff check src/data_incident_gym/run_context.py src/data_incident_gym/diagnostic_agent.py src/data_incident_gym/lab.py tests/unit/test_run_context.py tests/unit/test_diagnostic_agent.py tests/unit/test_lab.py
uv lock --check
git diff --check -- src/data_incident_gym/run_context.py src/data_incident_gym/diagnostic_agent.py src/data_incident_gym/lab.py tests/unit/test_run_context.py tests/unit/test_diagnostic_agent.py tests/unit/test_lab.py
git add src/data_incident_gym/run_context.py src/data_incident_gym/diagnostic_agent.py src/data_incident_gym/lab.py tests/unit/test_run_context.py tests/unit/test_diagnostic_agent.py tests/unit/test_lab.py
git diff --cached --name-only
git commit -m "feat: bind diagnosis agent to verified runs"
```

Expected: tests/Ruff/lock/diff exit 0；M2 build 仍只有独立 verifier 成功才返回；cached list 不含 config、CLI、root Markdown、requirements 或 third_party。

---

### Task 3: 实现最小 Diagnostic Controller 的状态、预算、门禁与 trace

**Files:**

- Modify: `src/data_incident_gym/diagnostic_agent.py`
- Modify: `src/data_incident_gym/diagnosis.py`
- Modify: `tests/unit/test_diagnostic_agent.py`
- Modify: `tests/unit/test_diagnosis.py`
- Modify (workspace-only, never stage or commit): `mistake.md`

- [ ] **Step 1: 用 FunctionModel 写 Controller RED 测试矩阵**

继续使用 PydanticAI 官方 `FunctionModel` 精确控制多轮 response，不创建模型协议替身。至少覆盖：

```text
test_exact_duplicate_call_is_blocked_before_second_m3_execution
test_different_arguments_are_not_false_positive_duplicates
test_ninth_tool_request_is_rejected_without_entering_m3
test_sixth_model_request_is_allowed_and_seventh_is_not_sent
test_output_validation_retries_exactly_twice_then_model_error
test_total_timeout_returns_model_error_with_partial_usage_and_trace
test_evidence_tool_error_exposes_only_stable_code_to_model_and_trace
test_testmodel_tool_error_path_is_structured_and_safe
test_testmodel_invalid_output_uses_output_retry_budget
test_cross_run_evidence_is_controller_invariant_failure
test_unknown_evidence_id_retries_then_fails_closed
test_incomplete_evidence_types_downgrade_confirmed_to_insufficient
test_unsupported_affected_asset_downgrades_confirmed_to_insufficient
test_valid_error_schema_and_downstream_lineage_pass_confirmed_gate
test_model_returned_insufficient_contains_no_root_or_assets
test_trace_contains_no_prompt_completion_hidden_reasoning_secret_path_or_sql
test_tool_calls_are_executed_sequentially_in_model_emission_order
```

测试用稳定 sentinel，例如 `TEST_REDACTED_VALUE`、`C:\\synthetic-secret\\probe.txt` 和虚构 SQL 文本；不得使用机器真实 secret/path。

- [ ] **Step 2: 确认 RED**

```powershell
uv run pytest tests/unit/test_diagnostic_agent.py tests/unit/test_diagnosis.py -q
```

Expected: exit 非零；Task 2 的最小 runner 尚未满足去重、预算、门禁和完整 trace。

- [ ] **Step 3: 实现调用 Controller 和可观察 trace**

每个四工具 wrapper 共用一个私有执行函数，严格按“预算 → fingerprint → duplicate → M3 → run scope → inventory → trace”顺序。正常 M3 失败使用 `ToolFailed(error.code)`；duplicate 使用 `ToolFailed("DUPLICATE_TOOL_CALL")`；第 9 个请求抛内部预算异常中止 run，不能让模型通过无限失败绕开 `tool_calls_limit`。

trace 的参数只能是四工具 Schema 已接收的规范化字符串；不记录 provider request/response body、system prompt、model completion、exception repr、数据库连接信息或隐藏推理。M3 返回内容保存在 `evidence_records`，trace 只引用 evidence IDs。

- [ ] **Step 4: 实现预算、output validator、终态映射**

每次 run 使用：

```python
usage = RunUsage()
limits = UsageLimits(request_limit=6, tool_calls_limit=8)
with agent.parallel_tool_call_execution_mode("sequential"):
    async with asyncio.timeout(180):
        result = await agent.run(
            user_prompt,
            deps=state,
            usage=usage,
            usage_limits=limits,
            retries={"tools": 1, "output": 2},
        )
```

异常只在 Agent boundary 映射：timeout → `MODEL_TIMEOUT`；PydanticAI request usage limit → `MODEL_REQUEST_LIMIT`；输出/协议/重试耗尽 → `MODEL_PROTOCOL_ERROR`；其他 provider/runtime 异常 → `MODEL_RUNTIME_ERROR`。所有分支都返回合法 `DiagnosisRunResult`，不泄露异常全文；只有 runner 构造期的非法 run/artifact/config 保留其类型化 preflight error。

output validator 精确执行本计划“确定性最终门禁”。对证据类型不足或资产无支持，返回由 Controller 重建的 `INSUFFICIENT_EVIDENCE`；对 scope/unknown citation 使用稳定 `ModelRetry`，让两次 output retry 有真实作用。不得解析或修补模型 JSON 文本。

- [ ] **Step 5: GREEN、全量 M4 unit 与显式提交**

```powershell
uv run pytest tests/unit/test_diagnosis.py tests/unit/test_diagnostic_agent.py tests/unit/test_run_context.py tests/unit/test_evidence.py tests/unit/test_evidence_tools.py -q
uv run ruff check src/data_incident_gym/diagnosis.py src/data_incident_gym/diagnostic_agent.py tests/unit/test_diagnosis.py tests/unit/test_diagnostic_agent.py
uv lock --check
git diff --check -- src/data_incident_gym/diagnosis.py src/data_incident_gym/diagnostic_agent.py tests/unit/test_diagnosis.py tests/unit/test_diagnostic_agent.py
git add src/data_incident_gym/diagnosis.py src/data_incident_gym/diagnostic_agent.py tests/unit/test_diagnosis.py tests/unit/test_diagnostic_agent.py
git diff --cached --name-only
git commit -m "feat: enforce diagnostic controller gates"
```

Expected: unit/Ruff/lock/diff exit 0；cached list 只含四个列出路径；8 次尝试/6 次 request/2 次 output retry/180 秒 deadline 都由代码和测试证明，不靠 prompt 声明。

---

### Task 4: 接入 diagnose CLI、真实 M3 证据与 gemma4 探针

**Files:**

- Modify: `src/data_incident_gym/cli.py`
- Modify: `tests/unit/test_cli.py`
- Modify: `tests/conftest.py`
- Create: `tests/integration/test_diagnostic_agent.py`
- Create: `tests/e2e/test_ollama_diagnosis.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock` only if marker/config normalization changes it; dependency graph must not change
- Modify (workspace-only, never stage or commit): `mistake.md`

- [ ] **Step 1: 写 CLI、真实工具链与 opt-in 模型 RED 测试**

CLI 单元测试至少证明：

- 顶层帮助新增且只新增要求内的 `diagnose`；`lab` 子命令仍只有 reset/inject/build。
- `diagnose CASE_ID` 解析 active pointer；`--run-id` 走显式固定 run，不能接收 path/SQL/table/free prompt/model/base-url/budget 选项。
- `CONFIRMED` 输出中文完成消息和严格 Diagnosis JSON，exit 0。
- `INSUFFICIENT_EVIDENCE` 仍输出结构化 JSON，exit 2；`MODEL_ERROR` 与 preflight error exit 1。
- stderr/stdout 不含 traceback、API key、reader password、绝对路径或 provider 原始异常。

集成测试使用一次真实：

```python
lab.reset(CASE_ID)
try:
    lab.inject(CASE_ID)
    run = lab.build(CASE_ID)
    runner = DiagnosisRunner.for_run(
        run.run_id,
        DiagnosticSettings(_env_file=None),
        model=FunctionModel(scripted_diagnosis),
    )
    result = await runner.diagnose(CASE_ID)
    assert result.diagnosis.status == DiagnosisStatus.CONFIRMED
finally:
    lab.reset(CASE_ID)
```

FunctionModel 的固定多轮顺序为：run results → node error → upstream lineage → 同一 response 中请求 relation schema 与 downstream lineage → structured Diagnosis。它必须从前一工具真实返回值提取下一参数，不能从测试 callback 硬编码 Ground Truth 结果；最终只断言 Controller 接受 `CONFIRMED`、必需 evidence types/IDs 来自真实 inventory、tools 只读，不在 M4 断言 root code 与 Ground Truth 精确匹配。

- [ ] **Step 2: 确认 RED**

```powershell
uv run pytest tests/unit/test_cli.py tests/integration/test_diagnostic_agent.py -q
```

Expected: exit 非零；CLI 与集成文件尚不存在。

- [ ] **Step 3: 实现 CLI 与默认禁止真实模型请求的测试保险**

`cli.py` 使用 `asyncio.run(runner.diagnose(case_id))`，不把 Typer 参数直接变成模型自由 prompt。输出 Diagnosis 的 `model_dump_json(indent=2)`；不在 M4 写 artifact/report。

`tests/conftest.py` 在未设置 `DIG_RUN_OLLAMA_TESTS=1` 时令 `pydantic_ai.models.ALLOW_MODEL_REQUESTS=False`。unit/integration 即使错误构造生产 model 也不得向本机或外部发请求。`pyproject.toml` 新增 `ollama: requires explicitly enabled local Ollama and gemma4:e4b` marker。

- [ ] **Step 4: 运行真实 FunctionModel + M3 集成并恢复健康**

```powershell
uv run pytest tests/integration/test_diagnostic_agent.py -q -s
uv run data-incident-gym lab reset schema_rename_payment_amount
```

Expected: 两条命令 exit 0；测试使用真实 dbt artifacts、真实 `dig_reader` Schema 和真实 manifest lineage；最终 reset 恢复健康。失败时不得用 fake artifact 替代、放宽 reader 权限或读取 Ground Truth 修正输出。

- [ ] **Step 5: 真实运行默认 gemma4:e4b，一次成功即满足 M4 探针**

`tests/e2e/test_ollama_diagnosis.py` 同样自行 reset/inject/build/finally reset，并带 `@pytest.mark.e2e`、`@pytest.mark.ollama` 与环境 skip。测试只证明：

1. `DiagnosisRunResult.metrics` 观察到的 provider/model 是配置的 OpenAI-compatible `gemma4:e4b`；
2. 至少一次真实 M3 工具调用成功，且 Controller 接受严格结构化输出；
3. 若为 `CONFIRMED`，引用通过三类证据和资产支持门禁；
4. 运行未超过预算且无写工具；
5. 不以此单次结果宣称一般准确率。

执行：

```powershell
$env:DIG_RUN_OLLAMA_TESTS = '1'
uv run pytest tests/e2e/test_ollama_diagnosis.py -q -s
$probeExit = $LASTEXITCODE
Remove-Item Env:DIG_RUN_OLLAMA_TESTS
if ($probeExit -ne 0) { exit $probeExit }
```

Expected: exit 0，真实工具调用与结构化 Diagnosis 可从测试断言和安全 trace 证明，finally 恢复健康。

若同一 HEAD、同一模型、同一命令连续出现 3 次可复现失败：立即停止；把三次 exit code、结构化结果、工具 trace、usage、版本和脱敏错误码记录到 ignored `artifacts/` 与 workspace-only `mistake.md`。不得添加 regex/JSON repair、模型专属重试循环或偷偷切换模型；请求用户决定。

- [ ] **Step 6: GREEN、提交 CLI/集成/真实探针**

```powershell
uv run pytest tests/unit/test_cli.py tests/unit/test_diagnostic_agent.py tests/integration/test_diagnostic_agent.py -q
uv run ruff check src/data_incident_gym/cli.py tests/conftest.py tests/unit/test_cli.py tests/integration/test_diagnostic_agent.py tests/e2e/test_ollama_diagnosis.py
uv lock --check
git diff --check -- pyproject.toml uv.lock src/data_incident_gym/cli.py tests/conftest.py tests/unit/test_cli.py tests/integration/test_diagnostic_agent.py tests/e2e/test_ollama_diagnosis.py
git add pyproject.toml src/data_incident_gym/cli.py tests/conftest.py tests/unit/test_cli.py tests/integration/test_diagnostic_agent.py tests/e2e/test_ollama_diagnosis.py
git diff --quiet -- uv.lock
if ($LASTEXITCODE -eq 1) { git add uv.lock } elseif ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
git diff --cached --name-only
git commit -m "test: verify M4 diagnosis agent"
```

Expected: tests/Ruff/lock/diff exit 0；cached list 只含列出的 M4 CLI/test/config 文件；`uv.lock` 只有真实配置命令产生差异时才加入，不包含 root Markdown、CI、requirements 或 third_party。

---

### Task 5: 完成本地总门槛、独立安全审查与远程验收等待

**Files:**

- Modify (workspace-only, never stage or commit): `README.md`
- Modify (workspace-only, never stage or commit): `mistake.md`
- Verify without modification: `AGENT.md`
- Verify without modification: `docs/requirements.md`
- Verify without modification: `.github/workflows/ci.yml`
- Verify without modification: `third_party/jaffle_shop`

- [ ] **Step 1: 更新 workspace-only 状态说明**

`README.md` 只把 M4 标记为已实现，并说明 M5/evaluator/report 尚未实现；增加真实命令、默认模型、`--run-id`、三个终态和 real-model opt-in 说明。不得写“P0 完成”“通用准确”“生产可用”或虚构 CI/模型结果。

`mistake.md` 记录各 Task 的真实命令、exit code、commit、审查、Ollama 探针结果与任何停止规则。两文件保持未暂存；`AGENT.md` 保持用户当前内容。

- [ ] **Step 2: 执行完整本地回归**

```powershell
uv run ruff check .
uv run pytest tests/unit -q
uv run pytest tests/integration -q
uv run pytest tests/e2e -q
uv lock --check
uv run data-incident-gym --help
uv run data-incident-gym diagnose --help
git -C third_party/jaffle_shop rev-parse HEAD
git -C third_party/jaffle_shop status --short
git diff --check
git status --short --branch
```

Expected:

- Ruff、unit、integration、常规 e2e、lock、diff exit 0；常规 e2e 明确 skip opt-in Ollama test，不发真实模型请求。
- submodule HEAD 仍为 `36bde6cba69d962b83be1d52fc65a0dce1cb4ebb`，status 无输出。
- 顶层有 `diagnose`，但没有 `eval`、自由聊天、工具调试入口或修复命令。
- M1/M2/M3 全量回归通过；active pointer 的新增生命周期不改变 M2 故障事实或 M3 证据合同。
- `git status` 只保留根目录 `AGENT.md`、`README.md`、`mistake.md` 的既定未暂存修改；没有未提交实现文件。

- [ ] **Step 3: 执行静态边界审计**

```powershell
rg -n 'GroundTruth|load_ground_truth|IncidentVerifier|lab_verifier|subprocess|os\.system' src/data_incident_gym/diagnosis.py src/data_incident_gym/run_context.py src/data_incident_gym/diagnostic_agent.py
rg -n 'from data_incident_gym\.config import Settings|\bSettings\(' src/data_incident_gym/diagnostic_agent.py src/data_incident_gym/diagnosis.py src/data_incident_gym/run_context.py
rg -n 'shell|filesystem|http|sql|write|repair' src/data_incident_gym/diagnostic_agent.py tests/unit/test_diagnostic_agent.py
```

Expected: 前两条无输出；第三条只允许 prompt/测试中的明确禁止性断言和 OpenAI-compatible 模型说明，不得出现被注册的危险工具。再用 TestModel introspection 证明 function tools 精确为四个，native tools 为空。

- [ ] **Step 4: 对 M4 全 diff 做独立对抗性审查**

由未参与实现的 `luna_worker` 从 M3 基线 `ffd56a1` 审查到 M4 HEAD，至少覆盖：

```text
requirements 7.4 / 9 / 10 / 11.2 / 13 / 14 / 15
单 Agent 与 OpenAI-compatible Adapter
TestModel/FunctionModel 是否为真实 PydanticAI 执行路径
active-run 是否只在 verifier 成功后发布
四工具 allowlist 与 M3 只读边界
run/case/evidence scope
exact duplicate 与失败调用能否绕预算
6/8/2/180 的真实强制
INSUFFICIENT_EVIDENCE 是否确定性拒答
MODEL_ERROR 是否脱敏且仍返回结构化结果
trace 是否不含隐藏推理或 secret
是否泄露/读取 Ground Truth
是否偷做 M5/P1 或模型专属修补
git staged/committed 边界与 root Markdown 保留
```

审查发现必须给出文件/行号/复现命令；只修复确认问题，并对修复再跑相关测试与复审。若需要修复，使用显式路径提交，例如 `fix: harden M4 controller boundary`；不 amend 已审查提交，不重写用户历史。

- [ ] **Step 5: 等待用户 push 授权与远程 Ubuntu CI 事实**

本地全部通过后报告精确 HEAD、提交列表、测试结果、真实 Ollama 结果、workspace-only 文件和未完成的 M5。只有用户明确授权后才 push。push 后必须实际观察 Ubuntu CI 对该 M4 HEAD 成功，才能把 M4 标记为正式完成；远程未观察到时只能报告“本地通过，等待 CI”。

---

## M4 最终完成门槛

- [ ] 用户已批准本计划并明确授权开始实施。
- [ ] `Diagnosis` 严格、冻结、拒绝额外字段/隐式 coercion/非证实状态中的根因与影响主张。
- [ ] active-run 只在 M2 独立 verifier 成功后原子发布；reset/inject/新 build 不保留旧指针。
- [ ] `DiagnosisRunner` 是小 Interface/深 Implementation，调用者不需要理解 PydanticAI 图、状态、预算或门禁。
- [ ] 生产模型通过 `OpenAIChatModel`/`OpenAIProvider` 使用统一接口，默认 `gemma4:e4b`；没有 Ollama 私有调用。
- [ ] TestModel 证明恰好注册四个工具、真实调用工具、返回严格输出并覆盖工具错误路径。
- [ ] FunctionModel 证明确定的多轮工具选择、duplicate、预算、output retry、timeout 与 gate 分支。
- [ ] 模型没有 Shell、任意文件、自由 SQL、外部查询、数据库写、源码修改或修复执行工具。
- [ ] Controller 对所有真实 EvidenceRecord 做本 run inventory 登记；unknown/cross-run citation 不可能成为确认。
- [ ] exact duplicate 不二次执行 M3；失败/重复不能绕过 8 次已接纳工具尝试上限。
- [ ] 模型 request ≤6、tool attempt ≤8、output retry=2、总 timeout=180 秒均由测试证明。
- [ ] 证据类型不足或 affected asset 无证据支持时确定性返回 `INSUFFICIENT_EVIDENCE`，不得猜测。
- [ ] runtime/budget/protocol/timeout 失败返回脱敏 `MODEL_ERROR` 和部分 metrics/trace，不抛用户可见 traceback。
- [ ] trace 只含可观察动作和证据引用，不保存 prompt completion 或隐藏推理。
- [ ] Agent 生产模块不读取/import Ground Truth；精确根因与完整影响集合未在 M4 内评分。
- [ ] 真实 M2 run + M3 tools + FunctionModel 集成通过并恢复健康。
- [ ] `gemma4:e4b` 至少一次真实 OpenAI-compatible 调用完成工具访问和严格结构化输出；失败三次则按规则暂停而非伪造通过。
- [ ] M1/M2/M3 unit、integration、e2e 全量回归通过，submodule 固定且 clean。
- [ ] 每个 Task 的独立审查和最终对抗性审查通过。
- [ ] 根目录三份 Markdown 保持既定未暂存、未提交、未推送状态。
- [ ] 用户授权 push 后，Ubuntu CI 对 M4 HEAD 实际成功。
- [ ] 未开始 M5 evaluator/artifacts/report/`eval run`，未提前实现 P1 完整 Diagnostic Kernel。

## 实施停止规则

遇到以下任一情况，保留完整脱敏证据并暂停当前 Task，向用户请求决策：

1. PydanticAI 2.34.0 与 Python/Pydantic 锁定版本不兼容，或只能升级既有核心依赖/引入第二个 Agent 框架才能继续。
2. 只能使用 Ollama 私有 API、regex JSON repair、自由文本 parser 或模型专属循环才能得到结构化输出。
3. 只能向 Agent 暴露 Shell、任意文件、自由 SQL、外部网络查询、管理连接或写能力才能完成调查。
4. 只有读取 Ground Truth、`ground_truth.json`、`IncidentVerifier` 或硬编码案例节点/列/影响集合才能让 Agent 通过。
5. active-run 交接无法在 verifier 成功之后原子发布，或需要扫描/猜测“最新”目录、删除历史 runs、修改 M2 Ground Truth 合同。
6. exact duplicate/失败调用/并行工具调用能绕过预算，且无法在现有 Controller seam 内确定性修复。
7. 真实模型、工具或 trace 暴露 API key、reader/admin 密码、管理 DSN、绝对敏感路径、原始 SQL 或隐藏推理。
8. `gemma4:e4b` 在同一 HEAD/配置/命令下连续 3 次可复现失败；不得自行切换其他模型。
9. Windows 与 Ubuntu 对 structured output、canonical fingerprint、gate 或 run scope 得出不同结论，需要改变已批准合同。
10. M4 只有提前实现 M5 evaluator/report/artifact writer 或 P1 hypothesis/EvidenceGap/claim matrix 才能继续。
11. 任何变更会覆盖/提交根目录 `AGENT.md`、`README.md`、`mistake.md` 的用户内容，或需要修改 third-party submodule。
12. push、PR、远程操作或模型切换尚未得到用户明确授权。
