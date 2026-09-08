# Kernel 后续计划：由 Controller 管理 gap 记账

状态：已实施（2026-09-08，单线逐任务完成 Task 1–4；unit/integration/非真实模型 e2e 结果见文末"执行状态"节）。

## 前置审计结论

2026-09-08 对 `df225698360a2371ef154f1a488e85925c095bc9` 上未提交的职责拆分做了聚焦审计，未发现阻塞本计划的回归问题。这是有限范围审计，不代表证明所有输入行为等价。

本轮直接验证：

- kernel、agent、diagnosis 相关单元测试：`69 passed in 5.75s`。
- `uv run ruff check .`：通过。
- 动态加载 HEAD 原 kernel，对比 9 个公共 Pydantic 模型的完整 `model_json_schema()`：零差异。
- AST 对比：原有 kernel 方法中仅 `_validate_confirmed`、`_validate_health`、`_validate_unresolved_declarations` 发生变化，另新增 `_validation_context`。
- 支付规则的 5 个原函数体 AST 全部一致，包括重复支付、永久孤儿、摄取丢失及两个辅助函数。
- 人工检查三处委托、验证器和状态提交：保留关键检查顺序，验证器不修改内核状态，不读取 Ground Truth；原入口保留公开类型和 agent 所需 pattern 别名。

服务测试本轮未重跑。首期计划的实施记录报告 integration 34 passed、非真实模型 e2e 47 passed/10 deselected；此处仅引用该记录，不将其冒充本轮独立验证。

非阻塞记录问题：首期计划正文 checkbox 仍未勾选，记录中的聚焦回归是 66 passed，本轮是 69 passed。后续收尾统一任务状态，并以带执行日期的实际命令结果为准，不追改历史运行数字。

## 目标和不变边界

移除模型工具参数中的 `kernel_gap_id` 和 `kernel_gap_kind`，由 controller 在调用边界生成 InvestigationIntent。模型继续负责假设、调查选择、证据引用和最终结论。

这是模型交互协议变更，不再宣称工具 schema 或完整 trace 字节等价。预期改善是模型少填写两个机械字段；不承诺 token、延迟或诊断质量提升，验收不需要真实模型。

以下内容保持：

- 两个候选、反驳替代假设、全部 gap 关闭等既有门禁；领域规则和 evaluator 不变。
- `kernel_hypothesis_ids`、`kernel_new_hypotheses` 继续由模型提供，不自动补候选、不推断或反驳假设。
- Kernel 的 `prepare_tool(intent=...)` 接口、InvestigationIntent/EvidenceGap/InvestigationState/KernelDecision 及六文件产物结构保持。
- tool/run 预算、超时、业务指纹算法、重复调用拒绝、关系来源策略和 NoSchema/NoLineage 消融边界不变。
- static-skill 和 no-tool 的业务工具参数及执行行为不变。

本期不放宽确认条件，不删除假设层，不引入恢复执行、工具结果缓存或多 Agent 调度。

## 调用协议

旧调用例子：`get_relation_schema(relation_name=..., kernel_gap_id=..., kernel_gap_kind=..., kernel_hypothesis_ids=..., kernel_new_hypotheses=...)`。

新调用只保留业务参数及两个假设参数。Controller 将它们转换为原有 InvestigationIntent，再交给 kernel 校验。工具收到的仍然只有业务参数。

### Gap 类型推导

| 工具及参数 | 推导结果 |
|---|---|
| get_dbt_run_results | LOCATE_FAILURE |
| get_dbt_node_error | EXPLAIN_FAILURE |
| get_dbt_lineage upstream | DISCOVER_SOURCE_RELATION |
| get_dbt_lineage downstream | MAP_IMPACT |
| get_relation_schema | DISCRIMINATE_SCHEMA |
| get_relation_data_profile | PROFILE_RELATION |
| get_relation_history | COMPARE_HISTORY |

该映射与 kernel 的现有映射必须只有一个数据来源。将纯映射及查找函数放入 contracts 或一个小型协议模块，kernel 和 controller 共用；不把预算、状态或领域规则移入映射模块。未知工具/非法 direction 不做默认推断，也不执行工具。

### Gap ID 和执行时序

- 采用 run 内单调计数 `g_auto_1`、`g_auto_2` 等，符合现有 pattern。计数器归属每次诊断新建的运行状态，不使用进程全局变量、时间戳、随机数或 provider call ID。
- 参数经过工具 schema 验证后、进入 adapter.prepare 前分配；在任何业务 I/O 或 await 前完成递增。分配本身不增加工具预算或 kernel revision。
- 每次实际进入该边界的尝试消耗一个编号，即使后续被 kernel 拒绝也不回退，因此账本编号可以有空缺。schema 验证失败、不进入边界的调用不占编号。
- 维持现有 sequential 工具执行模式。同一模型响应中的多个调用按现有派发顺序分配不同 ID。不同 run 可从 1 重新开始。
- 重试获得新 ID，但不得绕过业务 fingerprint：重复业务调用仍按原策略拒绝。RELATION_NOT_ALLOWED 仍由 kernel 记录 blocked gap，并遵守既有计费/计数行为。
- 新创建的标准运行从空 kernel 开始。本期不支持把新计数器接到任意非空历史 kernel；若实现发现已有恢复入口，先明确初始化规则，不能默默从 1 重用已有 ID。

Gap ID 是可追踪标识，不是新的业务依据。相同固定输入和调用顺序应生成相同 ID 序列；历史工具 trace 继续读作历史记录，不转换成新协议重新执行。

## 账本、提示词与版本

- 删除 prompt、工具描述、账本尾部、重试提示中的“由模型生成 gap ID/选择 gap kind”要求。
- 账本继续显示 controller 生成的 gap ID/kind/status，并补充 tool_name 和 subject，便于模型将自动编号对应到实际调查；这些是模型可见文本投影，不新增产物字段。
- 不要求模型重发自动字段；旧字段调用必须得到明确 schema 拒绝，不静默忽略。Kernel 直接 API 仍接受完整 InvestigationIntent。
- 保留关系工具白名单约束，并让账本和重试文本准确反映当前校验规则，避免残留“任意已返回关系都可查询”的歧义。EVIDENCE_GAP_OPEN 文本应明确当前要求全部已打开 gap 关闭，不能暗示只关闭决定性 gap 即可。
- 以当前版本为基线，计划将 kernel prompt 从 `p1.kernel.v8` 升至 `p1.kernel.v9`，controller protocol 从 `p1.controller.v7` 升至 `p1.controller.v8`。实施前重新核对占用情况；若已有后续版本，顺延并记录。
- 通过现有 policy surface 计算新 prompt/schema/protocol hash，不手写 hash。Controller 版本目前为共享常量，其变更可能改变其他策略的 policy identity；明确记录此影响，不声称所有策略 hash 不变，也不借此改动其他策略行为。
- 历史 Manifest、批次记录和历史 policy identity 原样保留。旧产物继续按原格式读取，旧冻结批次不得被描述为采用新协议；不重新冻结或运行 benchmark。

## 实施任务

### Task 1：冻结协议与验收样例

- [x] 核对最新 HEAD/diff、工具注册和 sequential 派发、policy identity 生成及引用位置。
- [x] 记录当前各策略工具参数及身份基线；固定新旧工具参数差异范围。
- [x] 增加协议回归用例，使旧实现不能满足自动字段移除、新映射和分配行为；固定 run/调用序列，不使用模型服务。
- [x] 更新 requirements 中 M6 的责任描述：模型维护调查语义，controller 管理 gap 标识和工具类型映射；保留原假设、证据和确认合同。

完成判据：所有预期行为变化明确；本计划之外没有新增产品决策。

### Task 2：实现 Controller 自动绑定

- [x] 建立共用工具-gap 映射，保留 kernel 的原验证接口与拒绝语义。
- [x] 在 run 内增加计数器和单一绑定入口，移除所有 kernel 工具签名中的两个机械字段。
- [x] 保留模型提供的假设参数，确保其仍经原 kernel 验证；静态工具注册不受影响。
- [x] 核对成功、prepare 拒绝、工具失败和重试四条路径的预算、gap、trace 与指纹行为。

完成判据：六个工具的七种映射均正确；每次运行隔离，自动字段不进入业务工具参数；没有新增绕过来源或门禁的路径。

### Task 3：同步模型可见说明和版本

- [x] 更新 kernel prompt、账本投影、工具描述及相关重试文本，移除旧协议要求。
- [x] 递增 prompt/controller 版本，验证 policy surface hash 自动变化且可重复生成。
- [x] 更新 FunctionModel 测试调用参数；保留直接 kernel API 测试中的人工 InvestigationIntent，覆盖底层兼容性。
- [x] 增加旧字段拒绝和旧产物可读测试，不为旧模型协议增加双栈运行模式。

完成判据：模型可见 schema 与说明一致，只有 kernel 策略及其消融的预定参数变化；旧记录不需要改写。

### Task 4：离线验收与交付

- [x] 覆盖同轮多调用、跨轮调用、schema 拒绝、prepare 拒绝、blocked relation、重复业务调用和新 run 编号重置。
- [x] 对固定等价业务序列验证：根因/claims/evidence/outcome 与原逻辑一致，差异限定于自动 gap ID、工具参数、账本文本及政策身份；不得直接忽略整个 trace 来宣称等价。
- [x] 完成相关单元、全量单元、integration 和非真实模型 e2e；确认 FunctionModel 从调用到六文件产物贯通。
- [x] 审查最终 diff，更新本计划的执行结果与首期任务状态，清理本次无用临时文件。

验收命令沿用项目常规集合：

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

相关测试用于阶段回归，全量验收后不无故重复；服务不可用时报告具体阻塞，不能把缺失检查写成通过。全程只使用 FunctionModel/确定性替身与本地服务，不运行真实模型、doctor 模型探针或正式 benchmark。

## 预期文件范围

主要修改 `diagnostic_agent.py`、`diagnostic_contracts.py`（或新增小型映射模块）、`diagnostic_kernel.py` 的映射引用、`prompts/diagnostic_kernel.md`、相关 unit/integration/e2e 中的 FunctionModel 调用 fixture，以及 `docs/requirements.md` 和本计划。实施前列出具体测试文件，不批量替换所有 gap 参数。

`diagnostic_validation.py`、`diagnostic_payment_rules.py`、evaluator、业务工具、事故配置和历史 benchmark 不属于修改范围。若版本断言涉及报告测试，只更新新运行的预期身份，保留历史记录用例。

## 再下一步

“只要求决定性 gap 关闭”继续单独保留：先定义决定性的可验证依据，再讨论是否修改 gate/evaluator。不能把它混入自动记账改造。本轮仅写计划，不开始实现、提交或推送。

## 执行状态（2026-09-08 实施记录）

### Task 1 基线核对结论（实施前记录）

- 版本占用核对：`p1.kernel.v9` 与 `p1.controller.v8` 在 src/tests/config/docs 中无占用，按计划顺延使用。
- Policy identity 生成路径：`_build_policy_surface` 将 `CONTROLLER_PROTOCOL_VERSION` 与工具 schema payload 一起哈希进 `controller_protocol_sha256`。该常量为共享常量，其递增改变**全部**模型策略（含 static-skill/no-tool）的 policy identity；kernel 策略额外因工具 schema 移除两个字段而改变 `tool_schema_sha256`。历史 Manifest 与冻结批次原样保留，未重跑。
- 旧调用基线：六个 kernel 工具签名带 `kernel_gap_id`（required）+ `kernel_gap_kind`（required）+ 两个 hypothesis 参数；`_kernel_intent` 构造完整 `InvestigationIntent` 经 `_execute_evidence(kernel_intent=...)` 进入 `_KernelPolicyAdapter.prepare`。trace 的 `arguments` 历来只含业务参数（工具函数内手工构造），故 trace 形状不受本次影响。
- 恢复入口核对：`_kernel()` 每次诊断新建空 kernel，`benchmark_runner` 每 cell 新建；不存在把新计数器接到非空历史 kernel 的入口，`g_auto_1` 起始安全。
- requirements 更新：§17 M6 增补责任描述（M6.1 修订，2026-09-08），并在文档头部登记修订行。

### 实际文件清单

| 文件 | 变更 |
|---|---|
| `src/data_incident_gym/diagnostic_contracts.py` | 映射单一数据源迁入：`expected_tool_for_gap`、`gap_kind_for_tool`（未知工具/缺非法 direction 抛 `KernelError("GAP_TOOL_MISMATCH")`，不做默认推断）；kernel 删除本地 `_GAP_TOOL` 改用共用映射 |
| `src/data_incident_gym/diagnostic_kernel.py` | 仅映射引用替换（`expected_tool_for_gap`），验证接口与拒绝语义零改动 |
| `src/data_incident_gym/diagnostic_agent.py` | `_RunState` 新增 run 内计数器 `next_gap_number` 与 `allocate_kernel_intent`（先查 kind 再取号，`g_auto_N`，分配不加预算不升 revision）；六个 kernel 工具签名移除 `kernel_gap_id`/`kernel_gap_kind`，保留两个 hypothesis 参数；`_execute_evidence` 成为单一绑定入口（`kernel_hypothesis_ids is not None` 区分 kernel 与 static 路径）；账本投影 gaps 增加 `tool_name`/`subject` 并更新说明文本；重试文本 `EVIDENCE_GAP_OPEN` 明确"全部已打开 gap 必须关闭"；版本升至 `p1.kernel.v9` + `p1.controller.v8` |
| `src/data_incident_gym/prompts/diagnostic_kernel.md` | 移除"模型生成 gap ID/选择 gap kind"要求与 gap-to-tool 映射表；声明 controller 分配；保留假设合同、根因码列表、领域判定语义与 provable_relations 约束 |
| `tests/unit/test_kernel_auto_binding.py` | 新增 6 个协议回归用例：7 种映射、未知工具/缺 direction 拒绝、工具 schema 无自动字段且 required 只含业务参数、同响应顺序编号+跨 run 重置、kernel 拒绝不回退编号（blocked gap 占号）、旧字段调用被 schema 拒绝且不进入 kernel 记账 |
| `tests/unit/test_m7_contracts.py` 等 | FunctionModel 调用参数迁移（`_kernel_binding`/`_intent` 去掉机械字段）；schema 断言翻转为"无自动字段 + hypothesis 参数保留"；CONFIRMED 等价序列断言补强（root_cause_code、affected_assets、evidence inventory、选中假设、g_auto_1..5 编号） |
| `tests/integration/…`、`tests/e2e/…` | `_intent` helper 与 wire 合同用例同步新协议；wire 用例断言回显参数恰为业务+hypothesis 参数 |
| `docs/requirements.md` | M6.1 修订行 + M6 责任描述 |

`diagnostic_validation.py`、`diagnostic_payment_rules.py`、evaluator、业务工具、事故配置、历史 benchmark 未改动。

### 关键行为确认（与计划逐条对应）

- 旧字段调用被**明确 schema 拒绝**（非静默忽略）：`test_model_supplied_auto_fields_are_rejected_without_kernel_binding` 证明旧字段工具调用不进入 kernel 记账（gaps 为空、无 TOOL_CALL trace、successful_tool_calls=0）。
- 编号语义：同响应多调用按派发顺序 `g_auto_1..N`；RELATION_NOT_ALLOWED/EVIDENCE_EMPTY 等 blocked gap 占号不回退；新 run 从 `g_auto_1` 重新开始；schema 验证失败不进入函数体故不占号。
- 分配时序：`_execute_evidence` 函数体开头（schema 验证后、`adapter.prepare` 与业务 I/O 前）完成取号；分配本身不加工具预算、不升 revision。
- kernel 直接 API 兼容：`prepare_tool(intent=...)` 与完整 `InvestigationIntent` 不变；`KERNEL_INTENT_MISSING` 保留为防御路径。
- 等价业务序列：CONFIRMED 场景的 root_cause_code、affected_assets、evidence、选中假设与拆分前语义一致（差异仅 gap ID 值、工具参数、账本文本与 policy identity）；m7 用例逐字段断言。
- policy surface hash 由代码自动计算，未手写；新旧版本号断言更新于 `test_both_prompts_expose_the_shared_m11_ontology_and_test_claim_rule`。

### 验证结果（Windows 本机，同一未提交工作区，含首期重构改动）

- 聚焦回归：`uv run pytest tests/unit/test_diagnostic_kernel.py tests/unit/test_diagnostic_agent.py tests/unit/test_diagnosis.py tests/unit/test_kernel_auto_binding.py -q` → 72 passed。
- 全量：`uv run pytest tests/unit -q` → **385 passed, 3 skipped**（含新增 6 个协议用例）。
- `uv run ruff check .`、`uv lock --check`、`uv build`、`git diff --check` 全部通过。
- 服务验证：`uv run pytest tests/integration -q` → **34 passed**（33:55）；`uv run pytest tests/e2e -m 'not real_model' -q` → **47 passed, 10 deselected**（policy matrix 34 项 32:55 + 其余 13 项 37:38，分两段执行），覆盖 FunctionModel 从调用到六文件产物的完整路径。
- 全程无真实模型请求、无 doctor 探针、无 benchmark。

### 实施中发现并修复的问题

- 首轮 e2e 有 6 个 policy matrix 用例失败（`TOOL_ARGUMENT_REJECTED` → `MODEL_PROTOCOL_ERROR`）：`_intent` helper 迁移时遗漏一个以 f-string 为首参的调用点（`_intent(f"g_schema_{n}", "DISCRIMINATE_SCHEMA", ...)`），新签名收到位置参数抛 TypeError。因 `_intent(...)` 作为 `_with_intent(...)` 实参总是先求值，STATIC_SKILL 用例同样受影响。已修复该调用点并用 AST 扫描确认两个服务测试文件再无位置参数残留；修复后 policy matrix 34/34 通过。诊断工件保留于 `.dig/diagnostics/dbg_e2e2.py` 已清理，失败 run 产物位于被忽略的 `artifacts/`。
- 审查修复（同日）：账本尾部文本仍残留"或已返回证据中出现的关系"例外（上一份计划点名要消除的歧义），已改为"白名单精确且完整、证据返回过的关系不扩大白名单"；新增一致性回归 `test_ledger_text_and_kernel_rejection_agree_on_relation_allowlist`——登记含 `raw_orders` 关系名的 upstream lineage 证据后，账本文本不宣称 `raw_orders` 可查、不出现例外短语，且 kernel 对 `get_relation_schema("raw_orders")` 仍按工具白名单抛 `RELATION_NOT_ALLOWED`。node 参数的"证据返回过的 node"provenance 说明属正确语义，保留。

### 剩余限制

- 本计划未承诺且未测量 token、延迟或诊断质量变化；验收不含真实模型。
- 历史 trace 中的人工 gap ID（如 `g_locate`）按原样保留为历史记录，未转换为新协议。
