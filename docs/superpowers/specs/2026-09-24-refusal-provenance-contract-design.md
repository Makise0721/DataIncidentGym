# 拒绝来源显式化合同（spec v1，待实施）

- 日期：2026-09-24。
- 所有者裁决：**控制器调用前的权限拒绝可以成为合格的证据缺口见证，但必须如实标为控制器拒绝，不能称为证据后端拒绝。**
- 本文锁定新运行的合同与验收，不批准修改历史归档、重新评分 v31、冻结 manifest、调用真实模型或执行数据库测量。实施、集中验证与新身份冻结分别推进。

## 1. 问题、范围与不变量

当前 `ToolTraceEvent` 只有工具、参数、错误码和证据 ID。模型工具调用在 `diagnostic_agent._execute_evidence` 中可能于三处被拒：`adapter.prepare` 在后端调用前拒绝、`call()` 返回证据工具错误、`adapter.accept` 在后端返回后拒绝；三者都写成同形的 `TOOL_CALL` 事件。`refusal_witnessed` 仅按工具、对象、错误码和唯一性匹配，无法区分拒绝来自哪一层。`StrategySession` 也可能在分派前拒绝调用。

v31 已终态的 79 格有 602 条轨迹事件，其中 381 条是 `TOOL_CALL`；38 条关系权限拒绝中，16 条在 Kernel 控制器前置校验处产生，未调用证据后端。在“期望与实际均为 `INSUFFICIENT_EVIDENCE`”的格中，实际声明 39 条 `RELATION_*` 缺口；27 条与合同期望三元组相交且被 v1 规则见证，其中 14 条仅由 Kernel 前置拒绝见证。完整缺口矩阵当前匹配的 12 格中，8 格的 10 条缺口依赖这类见证；这 8 格有 5 格已归档为 `PASSED`（seq11/36/51/59/67）。这些数字是**暂停前缀的分层审计计数**，不代表完整套件的通过率，也不授权改判旧格。

本次只改变**新版本轨迹对拒绝来源的表达与核验**。以下既有约束保持：同一工具、对象、错误码的精确匹配与拒绝唯一性；T13 批量工具的逐目标原子拒绝规则；8/8/2/300 预算、工具与关系权限、私有场景真值不进入模型上下文；`Diagnosis` 的根因、资产和缺口三元组不因来源字段而自动改变。禁止从错误码文本猜测来源。

## 2. 新轨迹的来源事实

新 `TOOL_CALL` 合同为每个工具事件强制记录 harness 生成的 `outcome_origin`：

| `outcome_origin` | 唯一写入点 | 事实含义 | 可作权限缺口见证 |
| --- | --- | --- | --- |
| `CONTROLLER_PRECHECK` | `adapter.prepare` 在 `call()` 前拒绝 | 控制器禁止本次调用；后端**未执行** | 可，须明确报告为控制器拒绝 |
| `EVIDENCE_BACKEND` | 证据工具完成调用，或抛出受控 `EvidenceToolError` | 已进入证据后端；错误码是后端返回的安全码 | 可，须明确报告为后端拒绝 |
| `CONTROLLER_POSTCHECK` | 后端返回后，`adapter.accept` 拒绝 | 结果未被控制器登记 | 不可 |
| `PROTOCOL_GATE` | `StrategySession` 在分派前拒绝 | 会话协议拒绝，未到证据后端 | 不可 |
| `TOOL_RUNTIME` | 工具调用中的非受控异常 | 运行时失败，不能证明某关系被权限拒绝 | 不可 |

`outcome_origin` 由上列执行分支设置；模型输出、brief、工具错误字符串与离线报告都不能设置或改写它。成功事件只能是 `EVIDENCE_BACKEND`，且原有证据登记约束不变；失败事件不得携带成功证据 ID。无法判明来源的新事件必须拒绝归档/评分，不能写成后端来源，也不能回退 v1 规则。`ProtocolTools` 已通过会话保留后端的原始 `EvidenceToolError`；实现仍须针对 `PROTOCOL_GATE` 与后端分支分别作回归，不得仅凭同名错误码分类。

当前 `StrategySession.call_tool` 的分派前拒绝和分派中的通用异常都可能在 `ProtocolTools._call` 后表现为 `StrategyProtocolError`，再被 `_execute_evidence` 的通用异常路径压成 `EVIDENCE_TOOL_ERROR`。因此不能凭异常类补填来源。会话/门面必须向 harness 保留受信任的“是否已分派到证据后端”事实，或等价的结构化内部回执；分派前归 `PROTOCOL_GATE`，已分派的非受控异常归 `TOOL_RUNTIME`，受控 `EvidenceToolError` 归 `EVIDENCE_BACKEND`。这个事实不进入模型可写字段，也不改变外部错误码。验收必须走真实 `ProtocolTools` 路径，分别断言三种情况的分派次数和来源。

旧 `TOOL_CALL` 没有这个字段。只在报告层显示为 `LEGACY_UNATTRIBUTED`；这不是可写入新轨迹的来源，也不表示旧事件必定来自后端。T13 批量工具的 `target_refusals` 只可在 `EVIDENCE_BACKEND` 的原子拒绝上承载；单个 call-level `TARGETS_REFUSED` 仍不能代替逐目标见证。

## 3. 新版缺口见证规则

共享判定函数返回结构化结果（是否成立、来源、匹配事件指纹/序号、失败原因），供 evaluator、I2 提交门、质量基线、拒绝复核与场景认证复用；消费者不得各写一套来源推断。版本由**受信任的运行/归档 schema 身份**选择，不能由模型提交内容或“有没有来源字段”自行选择。

对每条合同中绑定工具的缺口，新版要求：

1. 在本 run 的轨迹中找到**恰好一条**对应该工具与对象的拒绝事件；沿用 v1 的唯一性和参数/批量目标提取规则。同一工具与对象的重复拒绝不因来源不同而变成两张可用收据。
2. 错误码与缺口 `reason_code` 相同；事件不带成功证据。现有 `get_relation_schema_expectation` 与 `get_dbt_node_definition` 按其冻结的批量工具身份继续要求 `TARGETS_REFUSED`、请求目标存在及逐目标 `(target, code)` 精确匹配；该规则在旧 `TOOL_CALL` 中已经存在，不能被新事件标签替代或削弱。
3. 单目标工具的 `RELATION_NOT_ALLOWED` 或 `NODE_NOT_ALLOWED` 合格来源为 `CONTROLLER_PRECHECK` **或** `EVIDENCE_BACKEND`。返回值保留实际来源，二者不得在报告中合并称为“后端拒绝”。T13 批量工具必须是 `EVIDENCE_BACKEND` 的逐目标原子拒绝；新事件模型直接禁止其他来源携带 `TARGETS_REFUSED` 或非空 `target_refusals`。控制器前置拒绝没有逐目标后端判定，不能由 call-level 错误推成批量缺口见证。`CONTROLLER_POSTCHECK`、`PROTOCOL_GATE`、`TOOL_RUNTIME` 均不能作权限缺口见证。`NOT_OBSERVABLE` 等无工具缺口仍按原合同判定，不从轨迹伪造收据。

场景合同仍比较原有的 `(evidence_kind, subject, reason_code)` 集合；来源是**所见事实的限定**，不由模型自报，也不在本次为每个策略复制私有缺口矩阵。若将来某场景必须要求“仅后端拒绝”，须另行版本化场景合同并在认证时检查各策略的可解性；本次不暗加这种要求。如此，v31 中控制器前置拒绝在**新身份的相同机制**下仍可合格，但会被如实标记，不会被误称为后端调用。

## 4. 身份、序列化与历史兼容

- 保留旧 `ToolTraceEvent`、`TraceEnvelope(p1.trace.v1)`、`DiagnosisRunResult` v1 与 `EvaluationInputBundle(p1.evaluation_inputs.v1)` 的读回和**规范 JSON/摘要逐字行为**。不得给旧模型直接增加会在 `model_dump` 中出现的默认字段；这会改变历史 `diagnosis_run_digest` 与 `inputs_digest`。
- 新工具事件的判别值固定为 `event_type="TOOL_CALL_V2"`，`outcome_origin` 必填且只能取 §2 的五个值；旧事件仍为 `event_type="TOOL_CALL"`。新 envelope 固定为 `schema_version="p1.trace.v2"`，新运行结果固定为 `schema_version="p1.diagnosis_run.v2"`，新评分输入固定为 `schema_version="p1.evaluation_inputs.v2"`。旧聚合运行结果的**实际**标签是 `p1.diagnosis.v1`；新名 `p1.diagnosis_run.v2` 有意将聚合结果与模型提交的 `Diagnosis` 区分，后者 schema 不升版。加载器先按受信任的顶层 schema marker 分派至对应版本的严格模型，再检查组合：`p1.trace.v1`/`p1.diagnosis.v1`/`p1.evaluation_inputs.v1` 只能承载 `TOOL_CALL`；`p1.trace.v2`/`p1.diagnosis_run.v2`/`p1.evaluation_inputs.v2` 只能承载 `TOOL_CALL_V2`。不能只往全局 `TraceEvent` 判别联合加入新成员，否则旧 envelope/run 可能也接受新事件。缺来源、混合版本、交叉组合或降级输入均 fail-closed；无工具调用的 run 也由运行结果版本决定判定规则。
- evaluator 身份固定升至 `p1.evaluator.v4`；新运行使用 §3，v1 归档在离线重评时**显式走冻结的 v1 见证规则**，包括既有 T13 批量规则。新套件若包含 v1 轨迹策略格，v4 evaluator 内仍按该格的受信任 schema/policy identity 选择 v1 语义，并在报告中标 `LEGACY_UNATTRIBUTED`；不得以 v4 身份把 v1 事件猜成新来源。`KNOWN_EVALUATOR_VERSIONS` 显式保留 `p1.evaluator.v2`、`p1.evaluator.v3`、`p1.evaluator.v4`。评分结果形状不变，既有 `EvaluationResult` schema 不因此升版。旧附件、六文件、评分目录与 v31 manifest 字节不改写；新派生评分按新 evaluator 身份产生新 `score_id`，不覆盖旧目录。
- 模型可见 `Diagnosis` 输出无需来源字段；来源只在 harness 轨迹。受影响的内置模型策略控制器协议升为 `p1.controller.v21`，政策身份须绑定新工具事件及见证合同。外部调用接口和预算未变，`p1.strategy_adapter.v2` 保持不变；若实施时发现必须修改该协议的公开形状，应另行修订本 spec 后再实施。新 manifest 的 `result_inputs` 绑定新 evaluator 源码摘要。冻结前不得把新代码当作 v31 的执行修订，也不得修改 v31 检出门。
- 一个新套件可以包含 v1 与 v2 策略格，但每格的 manifest policy identity、归档 schema 与 scorer 分派必须一致。报表对 v1 只给 `LEGACY_UNATTRIBUTED`，不能把历史 16 次 Kernel 前置拒绝当作从 v1 归档**单独可证明**的来源事实；上文计数依赖 v31 运行时白名单和代码的只读审计。

## 5. 接线与验收

实施范围按调用链限定：`diagnostic_agent` 的三处分支与 `StrategySession`/`ProtocolTools` 的会话前置门写入来源；`diagnosis` 中的统一见证函数、新轨迹类型及工具次数校验；`artifacts`、`benchmark_archive` 与 `evaluation_inputs` 的版本化读写；`evaluation`、`submission_policy`、`quality_baseline`、`refusal_review`、`scenario_certification` 使用同一见证结果；`benchmark_report` 的工具计数与白名单检查识别新事件，不因 `event_type` 改名漏计。`quality_baseline` 的 bundle 加载器也须按顶层版本分派，不能固定用旧模型校验。相应 policy/evaluator 身份与 `docs/requirements.md` 的合同措辞同步。`fixed_rule`、planner 或外部策略若暂不产出新轨迹，继续按其明示的 v1 身份运行，不得凭错误码自动升级为 v2。

验收必须至少覆盖：

1. 相同 `(tool, target, code)` 分别经 Kernel 前置拒绝和证据后端拒绝：两者都可见证，来源不同；前者断言后端调用数为 0，后者断言确实进入后端。再经真实门面分别触发协议前置拒绝（分派数 0）与分派后的非受控异常（分派数 1），核对 `PROTOCOL_GATE`/`TOOL_RUNTIME`，两者及后置校验均不见证。
2. 错对象、错码、重复拒绝、夹带成功证据、批量工具仅有 call-level 拒绝、伪造/缺失来源均 fail-closed；新 trace 的来源字段只能由 harness 产生。I2 与 evaluator 对同一归档给出一致见证结果，拒绝反馈不泄露私有合同。
3. 新六文件与评分输入严格加载、摘要绑定及离线重算逐项一致；版本交叉、混合事件和降级输入拒绝。聚合运行结果的工具次数、benchmark 报告的调用数/白名单、归档与质量基线加载器均对 V2 事件给出正确结果，不得因 `TOOL_CALL_V2` 漏计。旧 v1 规范 JSON 与摘要回归逐字相同；已有可加载的 v31 78 份评分输入在 legacy 分支重算与归档评分逐项相等，seq4 无评分输入单列，不伪称损坏。尤其 seq11/36/51/59/67 的历史 `PASSED` 不被改判。
4. 新报告分别统计 `CONTROLLER_PRECHECK` 与 `EVIDENCE_BACKEND` 合格见证，并单列 legacy 不可归因；总体成功指标、分母和预算口径不因增加描述性来源指标而改变。

先运行有意义的单元、归档和离线回归；实现跨 runner/归档/评测后，再按改动影响运行集成与非真实模型 e2e。真实模型请求、正式 suite、manifest 冻结与 push 均不属于本 spec 的实施授权，分别遵循项目既有流程。

## 6. 本轮不做

不回填旧轨迹来源，不重评或改写 v31 正式结果，不把 v1 历史拒绝统称为后端拒绝；不接入未采纳的 history probe 原型；不改 Kernel 白名单、不放宽工具权限、不加入新的模型可见事实、不宣称策略能力提升。
