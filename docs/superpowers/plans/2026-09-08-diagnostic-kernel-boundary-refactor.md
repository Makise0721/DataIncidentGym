# Diagnostic Kernel 职责拆分计划

状态：已实施并完成全部验证（2026-09-08，单线逐任务完成 Task 1–4；unit/integration/非真实模型 e2e 全绿）。实施记录见文末"执行状态"节。

## 目标与范围

将当前 DiagnosticKernel 拆成管理调查生命周期的内核和无状态的领域验证器，减少新增事故类型时对内核的修改。首期要求行为等价，不以减少总行数或提高模型成功率作为验收指标。

采用单线、逐任务实施。无需引入插件框架、动态注册机制、新依赖或新的 Agent。

本计划默认先完成结构拆分。模型记账简化和门禁调整留作后续独立设计，不随本次重构实施。当前范围没有必须等待回答才能完成计划的问题。

## 当前依据

- 检查基线 HEAD：`df225698360a2371ef154f1a488e85925c095bc9`。工作区已有暂存及未暂存改动，实施前重新核对，不覆盖或重置已有工作。
- `diagnostic_kernel.py` 当前 1492 行，包含数据模型、调查状态、工具来源检查、证据登记、事故类型判定、健康判定及终态投影。
- `_duplicate_root_supported`、`_orphan_root_supported`、`_silent_drop_root_supported` 是领域规则；`_validate_confirmed` 混合通用门禁、领域规则、影响范围校验和状态提交。
- `_validate_health` 同时进行调查门禁、历史区间/watermark/SLA 判定和状态提交。
- `docs/requirements.md` 第 17 节明确要求：至少两个候选假设、受支持的选中假设、被反驳的替代假设；模型声明根因、影响资产与证据 ID，kernel 验证、拒绝和投影；evaluator 独立读取 Ground Truth。
- `diagnostic_agent.py`、`evaluation.py`、报告及测试通过原模块导入 kernel 类型。原导入路径属于本次兼容面。
- 当前检查只支持“职责集中”的判断，没有性能测量或有效实验支持延迟、token 消耗、准确率的改善承诺。

## 必须保持的行为

1. `DiagnosticKernel.start`、构造参数、公开方法签名和原有类型导入路径保持可用。
2. Pydantic 模型配置、字段、schema version、JSON Schema 与序列化形状不变；保留 `KernelError` 类型、code 和 fingerprint 行为。
3. 工具 schema、prompt 内容及版本、模型/工具预算、重试规则、策略与消融边界不变。
4. CONFIRMED 的候选/反驳/全部 gap 关闭条件，以及 NO_INCIDENT 和 INSUFFICIENT_EVIDENCE 的条件不变。
5. 保留校验顺序和首个错误码；被拒绝时的状态与 revision 行为不变。领域验证器不得提前提交部分结果。
6. EvidenceRecord 顺序、证据 ID 去重顺序、claims/assessments、终态 trace 位置、六文件产物字段及报告语义不变。
7. 继续只使用本次 run 的公开事实。不得读取 Ground Truth、私有事故配置、预期答案或 evaluator 的评分结果。
8. 不修复重构途中发现的独立业务缺陷；记录具体案例，另行判断是否修改合同，避免把语义修复混入等价验收。

## 目标边界

| 模块 | 责任 |
|---|---|
| `diagnostic_kernel.py` | 公开入口及兼容导出；调查状态、预算、调用指纹、参数来源、证据登记、通用门禁、终态提交与投影 |
| `diagnostic_contracts.py`（新） | 现有 kernel 枚举、Pydantic 数据模型、KernelOutcome、KernelError；作为内核与验证器共同依赖 |
| `diagnostic_validation.py`（新） | 显式的只读验证上下文、根因验证分派、影响资产验证、健康判定、领域相关缺失证据声明检查 |
| `diagnostic_payment_rules.py`（新） | 重复支付、永久孤儿支付、支付摄取丢失的现有规则及其私有辅助函数 |
| `diagnostic_agent.py` | 继续负责模型循环、工具桥接和账本呈现，本期不调整协议 |
| `evaluation.py` | 保持独立评估，不调用新增的领域验证器作为评分答案来源 |

依赖方向：kernel → validation → payment_rules；这些模块可依赖 contracts、现有 evidence/profile 类型。contracts 保留必需的既有基础类型依赖，不反向导入 kernel/validator。实施前核对 diagnosis 依赖，防止形成循环。

验证上下文使用冻结的数据结构和 tuple，显式传递所需公开事实：引用证据、全量已登记证据、incident subjects、health targets、逻辑观察时间及公开 observations。不得把整个 kernel、可变内部列表、数据库连接或工具执行器传进去。

验证器只验证模型声明，成功返回，失败给出既有 KernelError；不选根因、不补 claim、不变更状态。分派使用简单的静态函数和分支，保留目前各根因的特殊路径及通用回退规则。不要为了统一接口而加强或削弱已有检查。

对于 `_validate_health` 等交织的检查，按原顺序拆成分阶段函数：例如先检查 run 事实，再检查 claim 形状和引用，随后检查领域充分性。不能简单把通用检查全部移到前面，导致首个错误码变化。

## 实施任务与完成判据

### Task 1：锁定等价基线

- [x] 重核 HEAD、工作区 diff、相关目录 AGENTS.md 和现有测试配置；记录本次文件范围。
- [x] 建立“现有规则 → 迁移位置 → 现有测试”的对应表，覆盖 confirmed、health、insufficient、model error 和工具生命周期。
- [x] 运行 kernel/agent/diagnosis 的相关单元测试，记录本次真实结果，不沿用历史通过数量。
- [x] 补齐有必要的行为特征测试：多条件同时不合法时的首个错误码、拒绝后的状态、终态投影、公共 JSON Schema。
- [x] 对原实现的代表性输入记录确定性期望结果，使用固定 run ID、时间与证据；新期望值不得由重构后的实现现场生成。

完成判据：同一组用例可在重构前后运行，覆盖接受及拒绝行为；没有未解释的基线失败。若存在无关环境失败，记录范围，不宣称全量通过。

### Task 2：提取公共数据合同

- [x] 将类型、相关校验辅助函数和 KernelError 迁入 `diagnostic_contracts.py`，不改字段与验证器逻辑。
- [x] 从 `diagnostic_kernel.py` 显式重导出原有公共类型，既有调用方无需改导入。
- [x] 检查循环导入、公共模型 schema 和序列化一致性，运行 Task 1 中受影响测试。

完成判据：旧入口导入成功，公开 schema 无差异，相关测试通过。不批量迁移外部调用方，只为内部依赖使用 contracts。

### Task 3：提取领域规则并接回内核

- [x] 按规则对应表迁移支付辅助判定，保留证据筛选顺序和边界判断。
- [x] 提取根因/影响资产验证、健康规则及领域相关缺失证据声明检查。
- [x] Kernel 保留通用 gap/假设/引用检查以及成功后的统一状态更新；按旧顺序调用领域验证阶段。
- [x] 对现有测试覆盖不足的接口边界增加少量用例：验证器不修改输入、领域拒绝不提交终态。
- [x] 原有 kernel 测试继续通过公开入口运行，不把所有测试改成只测辅助函数。

完成判据：kernel 不再包含具体支付事故根因分支或 watermark/SLA/历史区间算法；它保留调查状态机与通用门禁。所有既有接受/拒绝用例和基线期望一致。

### Task 4：离线兼容验证与收尾

- [x] 运行完整单元测试与下列常规检查；仅在新失败或新修改需要时重复。
- [x] 在本地 PostgreSQL/dbt 可用的条件下完成 integration 和非真实模型 e2e，包含 FunctionModel 工具调用→终态→产物路径。
- [x] 检查现有 static-skill、消融、benchmark report 和 artifact 测试覆盖未受影响；不运行正式 benchmark。
- [x] 审查 diff：prompt/版本/预算/evaluator/事故配置/历史报告无本次变更；清除本次无用临时文件。
- [x] 在本计划补充实际文件列表、验证结果及剩余限制。若需更新架构说明，仅更新明确涉及本次边界的文字，不覆盖已有未完成图表工作。

完成判据：必要检查通过或明确标出环境阻塞；结构目标达成，兼容差异为零。未完成的服务验证不能记录成已验收。

## 验证矩阵

| 边界 | 必须覆盖 |
|---|---|
| 工具生命周期 | 未证明节点/关系、各工具作用域、重复调用、跨 run 证据、预算耗尽、成功/失败 gap 绑定 |
| CONFIRMED | schema 类通用路径、精确/语义重复支付、永久孤儿、摄取丢失；证据缺失及不兼容拒绝；失败 test 的 distance-1 模型归属 |
| NO_INCIDENT | 成功 run、当前 profile/history、watermark、逻辑时间/SLA、历史区间与目标绑定；保留首错顺序 |
| 其他终态 | 缺失证据声明绑定、未关闭 gap、MODEL_ERROR、安全原因码、二次 finalize |
| 公共兼容 | 旧导入、JSON Schema、snapshot/outcome、错误与状态、FunctionModel trace 和产物路径 |

实施阶段命令（本轮不执行）：

```powershell
uv run pytest tests/unit/test_diagnostic_kernel.py tests/unit/test_diagnostic_agent.py tests/unit/test_diagnosis.py -q
uv run ruff check .
uv run pytest tests/unit -q
uv run pytest tests/integration -q
uv run pytest tests/e2e -m 'not real_model' -q
uv lock --check
uv build
git diff --check
```

第一条用于基线和受影响回归；完整单元验证后不无故重复。服务测试需要 Docker/PostgreSQL/dbt；如果当前环境不可用，报告具体失败，保留验证任务待完成。测试只使用确定性替身和本地服务，不发起真实模型请求、doctor 模型探针或正式批次，也不生成/重新冻结 Manifest。

## 后续独立设计项

1. Controller 自动生成 gap ID，并根据工具推导 gap kind：需要先定义模型可见工具 schema、账本引用、重试及回放兼容规则。
2. 将“所有 gap 关闭”调整为“决定性 gap 关闭”：需要定义决定性、额外探索失败是否允许确认，以及 evaluator 如何独立判定。不能仅删除现有条件。
3. 是否保留至少两个候选及反驳要求：当前 requirements 明确规定，首期保留。

这些项会改变模型行为或实验合同，不是本次结构拆分的验收条件。结构重构完成后可再选择是否推进；本计划不自动授权提交、推送或模型实验。

## 执行状态（2026-09-08 实施记录）

### 实际文件清单

| 文件 | 变更 |
|---|---|
| `src/data_incident_gym/diagnostic_contracts.py` | 新增：枚举、全部 Pydantic 模型、`KernelError`、`reject_duplicates`、ID pattern 常量，逐字迁移 |
| `src/data_incident_gym/diagnostic_payment_rules.py` | 新增：`duplicate_root_supported`/`orphan_root_supported`/`silent_drop_root_supported` 及私有辅助 `_duplicate_count`/`_public_observation`，函数体逐字迁移 |
| `src/data_incident_gym/diagnostic_validation.py` | 新增：冻结 `ValidationContext`（incident subjects、health targets、逻辑观察时间、公开观察、全量登记证据）、`validate_root_cause_evidence` 分派、`validate_asset_claims`、`validate_health_run_evidence`/`validate_health_claim_shape`/`validate_health_claims` 分阶段、`validate_unresolved_declarations` |
| `src/data_incident_gym/diagnostic_kernel.py` | 1492 → 725 行：删除重复合同定义与领域函数；`_validate_confirmed` 保留通用门禁 1–9 后调用验证阶段；`_validate_health` 按原顺序分阶段委托；blocked gap 集由 kernel 投影后传入声明绑定检查；保留私有 pattern 别名（`diagnostic_agent.py` 从 kernel 导入 `_GAP_ID_PATTERN`/`_HYPOTHESIS_ID_PATTERN`，属计划遗漏的兼容面，已按兼容要求保留） |
| `tests/unit/test_diagnostic_kernel.py` | 新增 9 个测试：多条件首错码、拒绝后 revision/status 不变、二次 finalize、CONFIRMED 投影证据顺序、MODEL_ERROR 终态、公共 JSON Schema 形状、领域拒绝不提交终态（CONFIRMED/health 两路）、支付规则不改输入 |

依赖方向实际为 kernel → validation → payment_rules，均依赖 contracts/evidence/profiles/diagnosis，无循环导入。`diagnostic_agent.py`、`evaluation.py`、`benchmark_runner.py`、`benchmark_report.py` 及全部调用方导入路径未改动。

### 验证结果（Windows 本机，main=`df22569` 工作区）

- Task 1 基线：`uv run pytest tests/unit/test_diagnostic_kernel.py tests/unit/test_diagnostic_agent.py tests/unit/test_diagnosis.py -q` → 60 passed（重构前记录）。
- Task 2/3 回归：同一命令 → 66 passed；全量 `uv run pytest tests/unit -q` → 379 passed, 3 skipped（含新增 9 个特征/边界测试）。
- 公共 JSON Schema：从 HEAD 导出原 `diagnostic_kernel.py`，对 9 个模型（Hypothesis…KernelOutcome）逐字节比对 `model_json_schema()` → 零差异。
- 差分行为对比：用测试模块场景 helper 驱动新旧两实现（同步 patch contracts 类型规避枚举双身份假象），六场景 CONFIRMED（语义重复/永久孤儿/摄取丢失）、NO_INCIDENT、INSUFFICIENT（blocked history + watermark 绑定）、领域拒绝后 snapshot 逐字段 → 全部 MATCH。
- `uv run ruff check .`、`uv lock --check`、`uv build`、`git diff --check` 全部通过。
- 服务验证（同日 Docker Desktop 启动后补齐）：`uv run pytest tests/integration -q` → **34 passed**（30:01）；`uv run pytest tests/e2e -m 'not real_model' -q` → **47 passed, 10 deselected**（1:15:38），覆盖 FunctionModel 工具调用 → 终态 → 六文件产物路径。
- 受保护面核对：`diagnostic_agent.py`、`prompts/`、`evaluation.py`、`diagnosis.py`、`config/`、事故规格、历史报告在本轮 diff 中零改动；公开方法签名与 `__all__` 无删减。

### 剩余限制

- 无未完成的验证任务。正式 benchmark 按纪律未运行；无任何真实模型请求（e2e 的 10 个 `real_model` 用例按标记取消选择）。
- 探针工件保留于 ignored 目录 `.dig/diagnostics/kernel-refactor-20260908/`（HEAD 原版 kernel 副本），供复核差分对比。
