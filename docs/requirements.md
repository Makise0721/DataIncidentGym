# DataIncidentGym 需求文档

> 状态：已批准
> 版本：0.1
> 日期：2026-08-24
> 批准记录：用户于 2026-08-24 明确批准本需求基线。
> M5.1 修订：用户于 2026-08-28 批准当前本地模型由 `gemma4:e4b` 切换为 `qwen3.5:9b`；`gemma4:e4b` 保留为历史失败记录，其结果不计入新模型的验收分母。
> 预算再批准：用户于 2026-08-28 再次批准把单次诊断总超时由 180 秒改为 300 秒（§10.4）；模型请求、工具调用与结构化输出校验重试上限不变。
> 预算再批准（二）：用户于 2026-08-28 再次批准把单次诊断模型请求上限由 6 改为 8（§10.4）；工具调用、结构化输出校验重试与总超时上限不变。
> M5.2 修订：用户于 2026-08-28 批准当前模型切换为 OpenAI-compatible 的 `mimo-v2.5`；`qwen3.5:9b` 的历史失败记录不计入新模型验收分母。
> M5.3 修订：用户于 2026-09-06 批准当前模型由 `mimo-v2.5` 切换为同供应商 OpenAI-compatible 的 `mimo-v2.5-pro`；`mimo-v2.5` 的三批 smoke 观察不计入新模型的验收分母，其记录保留为历史轨迹。
> M6.1 修订：用户于 2026-09-08 批准 M6 责任描述修订——controller 在调用边界管理 gap 标识与工具到 gap 类型的映射，模型工具参数不再包含 kernel_gap_id/kernel_gap_kind（见 §17 M6）；假设、证据与确认合同不变。
> M13.1 修订（2026-09-09 依据 p1-v8 smoke followup 计划批准）：SOURCE_SCHEMA_COLUMN_RENAMED / SOURCE_SCHEMA_COLUMN_TYPE_CHANGED 的 CONFIRMED 必须由模型在 root cause claim 中声明 relation_name 为目标关系，并引用同一目标关系、位于失败节点上游路径上的 RelationSchemaFact；其他关系的 schema 或任意聚合 profile 不能替代该必要证据。目标 schema 不可得或无法排除 TRANSFORMATION_COLUMN_CAST_CHANGED 时必须返回 INSUFFICIENT_EVIDENCE。Diagnostic Kernel 在终态提交前验证该绑定，evaluator 与六文件产物合同不变。get_dbt_lineage 仅接受运行 catalog 与已证明节点求交出的 canonical node_id（账本 provable_lineage_nodes 展示）；关系名或 schema 限定名在创建 gap 前被拒绝且不可重试。对与案情直接相关但不在工具白名单的关系，模型可对该工具发起一次边界探针以记录真实权限拒绝收据：探针计入原预算、不访问数据库、不产生成功证据、不可重试变体；INSUFFICIENT_EVIDENCE 的缺失证据声明必须绑定真实 BLOCKED gap（evaluator 收据合同不变）。
> M13.2 修订（2026-09-09 依据 p1-v8 kernel rerun repair 计划批准）：kernel 模型可见账本增加已接受证据清单（evidence_id / evidence_type / subject）与 gap 的真实 error_code；收尾指引（kernel prompt p1.kernel.v11 / controller p1.controller.v10）要求 INSUFFICIENT_EVIDENCE 前核对仍可区分候选原因且可查的 profile/history，并区分两类声明：schema/profile/history 声明须绑定真实权限拒绝收据；watermark / payment-event identity / transformation definition 声明须有相关公开 subject 与不可观测事实。被 kernel finalize 拒绝的结构化决策在 trace 的 EVIDENCE_GATE 事件写入有界摘要（schema p1.rejected_decision.v1,默认 None 向后兼容）：只保留已注册假设、ontology 根因、公开节点/关系标识与已接受证据引用,未知内容以计数表达;不保存自由文本与原始值。摘要的 assessments / claims / unresolved_evidence 三个数组统一上限 16 项，每条 claim 的证据引用上限 32 个；被上限丢弃的条目不再被检查，计为 truncated_*_count 而不计为 unknown_*_count。计数关系按数组对未知内容的处理区分：assessments 丢弃未注册假设，满足 total_assessments = len(assessments) + unknown_hypothesis_count + truncated_assessment_count；claims 与 unresolved_evidence 在原位脱敏未知值，满足 total = len(数组) + truncated_*_count，unknown_* 计数描述保留项内的脱敏条目；每条 claim 的引用满足 total = kept + unknown_evidence_count + truncated_evidence_count。公开节点投影与 validate_unresolved_declarations 一致，包含已接受 run-results 的 failed_nodes / skipped_nodes，以及 brief subjects、证据的 node_id / related_nodes 与关系名；投影只读已接受证据，不新增工具权限、不读取私有 scenario。预算 8/8/2/300、evaluator 判分与领域门禁不变。
> M13.3 修订（2026-09-10 依据 kernel model interface simplification 计划批准）：两项模型接口简化，kernel prompt `p1.kernel.v12` / controller `p1.controller.v11`。（1）相同 hypothesis_id 且相同 root_cause_code 的跨调用重复声明按幂等处理：不重复登记、不改变既有定义，本次业务调用继续校验与执行；相同 ID 配不同 root code 仍是 `DUPLICATE_HYPOTHESIS` 拒绝，不覆盖原定义；不同 ID 共享同一 root code 仍是两个声明；单次 intent 内部的 ID 重复仍由 InvestigationIntent 去重校验拒绝；未知引用、未知 ontology、重复业务调用与预算上限的拒绝与计数合同不变。新声明在整组校验通过后才提交，混合输入不产生半提交。（2）每个模型请求只注入一份权威动态账本，位置为 agent 动态 instructions（每个请求按当前 kernel 快照重新求值），业务与输出工具说明不再携带账本；账本字段、值来源、provable 白名单与预算口径不变，仍是模型可见投影而非新的持久化字段。只读工具、run-scope、证据真实性与 claim-evidence 绑定不变；预算保持 8/8/2/300；evaluator、领域验证器、scenario 与既有确认门禁不变。
> M13.4 修订（2026-09-10 依据 p1-formal-v10 复跑取证结论批准）：协议观测扩展，controller `p1.controller.v12`（kernel prompt 内容与版本不变）。`MODEL_PROTOCOL` trace 事件新增观测字段，均为带默认值的追加项：`model_request_index`（产生该响应的模型请求序号）、`output_retry_used`（该轮输出校验已消耗的重试数）、`call_shapes`（每个调用的工具名、是否为结构化输出调用、参数解析结果 OBJECT/INVALID_JSON/EMPTY）、`response_ended_with`（OUTPUT_CALL/BUSINESS_CALL/TEXT_ONLY/EMPTY）、`error_type`（终止异常的固定分类名，不记录异常文本）、`error_origin`（OUTPUT_VALIDATION / BUSINESS_TOOL_ARGUMENTS / KERNEL_DECISION / PROVIDER / UNKNOWN，依据会话中记录的 retry prompt 目标判定，无法判定时为 UNKNOWN 而非猜测）、`retry_prompt_targets`（去重后的 retry 目标名，未知名称折叠为固定标记）。`category` 与 `stage` 的既有取值与选择逻辑保持不变，新增字段只做归因，不改变重试、预算或接受行为；不记录参数值、原始响应文本或 provider 载荷。该修订不修改 evaluator、领域验证器、scenario、预算或确认门禁。
> M13.5 修订（2026-09-11 依据协议观测归因修正计划批准）：修正 M13.4 观测字段的生命周期与归因语义，controller `p1.controller.v13`（kernel prompt 内容与版本不变）。（1）响应专属观测在每次 adapter 请求开始前清空，因此 provider 失败等无响应请求不携带上一轮的调用形状、重试计数或 kernel 判定；`model_request_index` 语义明确为 adapter 请求尝试序号（从 1 起，与 SDK usage 记账分开），无响应请求保留其尝试序号并取 `call_shapes=()`、`response_ended_with=None`、`output_retry_used=None`（与确实返回空响应的 `EMPTY` 区分）。（2）`error_origin` 只由当前请求可验证的证据决定：当前 provider 异常记 `PROVIDER`；本请求内经 kernel finalize 拒绝且未因 `KERNEL_FINALIZED` 之类状态冲突触发时记 `KERNEL_DECISION`；`KERNEL_FINALIZED` 表示同一请求内已有决策被接受，属状态冲突而非域判定，不得记为 kernel 拒绝；终止响应为纯 schema 失败的 output 调用记 `OUTPUT_VALIDATION`；终止响应为参数不可解析的已注册业务调用，或本请求内 output 已被接受且同一响应含不可解析业务调用时记 `BUSINESS_TOOL_ARGUMENTS`；未注册工具名（折叠为固定标记）、多来源无法区分、仅有历史 retry 背景或异常来源不明时记 `UNKNOWN`。历史 retry 目标只作背景，不覆盖终止响应的反向证据；业务目标的识别必须基于已注册业务工具集合。`output_retry_used` 取自当轮 output validator 的 `ctx.retry`，未进入 validator 时为 None，不代表本次请求的全部 SDK 重试总数。既有 `category`/`stage` 取值与选择逻辑、预算与终态行为不变；不记录参数值、响应正文或异常消息。该修订不修改 evaluator、领域验证器、scenario、工具权限或确认门禁。
> M13.6 修订（2026-09-11 依据协议观测归因审计批准）：收紧 M13.5 的来源选择规则，controller 仍为 `p1.controller.v13`（仅行为修正，不改变内核接口）。（1）历史 retry 目标严格降级为观测背景，**不得**把当前证据不足的 `UNKNOWN` 提升为历史类别：`error_origin` 只由本请求的可验证证据决定，未知或不可归属的当前失败一律 `UNKNOWN`。（2）kernel 域拒绝不再无条件优先：当同一响应同时含不可解析的已注册业务调用时，kernel 判定无法证明哪一表面终止了本请求，记 `UNKNOWN`；仅当该响应只有 kernel 拒绝一个失败来源时才记 `KERNEL_DECISION`。（3）output 被接受是**整个 run 的终态事实**（`output_accepted` 跨请求保留），因为接受后输出面不可能再是失败面；据此，含不可解析业务调用的响应即使触发 `KERNEL_FINALIZED` 状态冲突，仍记为 `BUSINESS_TOOL_ARGUMENTS`。（4）`KERNEL_FINALIZED` 属状态冲突而非域判定，不计入 kernel 拒绝标记。纯 kernel 拒绝耗尽仍为 `KERNEL_DECISION`，已接受 output 加非法业务参数仍为 `BUSINESS_TOOL_ARGUMENTS`。预算、重试、`category`/`stage`、终态与领域门禁不变。
> M13.7 修订（2026-09-11 依据协议观测归因第二轮审计批准）：把 `error_origin` 统一为「仅当当前响应的证据可证明失败来源唯一时才给确定类别，否则 `UNKNOWN`」，controller 仍为 `p1.controller.v13`。判定顺序：（1）明确 provider 异常 → `PROVIDER`；（2）同一响应同时含结构化输出调用与已注册业务调用 → `UNKNOWN`（不区分 JSON 是否可解析：`{"run_id":[]}` 这类可解析但仍违反工具 schema 的参数无法在不解码参数的前提下排除，本实现不复刻 SDK 参数校验）；（3）仅含 output 调用且本请求存在 kernel 域拒绝 → `KERNEL_DECISION`；（4）仅含 output 调用且本请求本地输出校验确有错误（`current_output_details` 非空）→ `OUTPUT_VALIDATION`；（5）仅含已注册业务调用 → `BUSINESS_TOOL_ARGUMENTS`；（6）其余 → `UNKNOWN`。**移除** M13.6 第（3）项中「据 `output_accepted` 推断当前业务失败」的规则：早期接受属于 run 状态，不是当前响应的证据，不得用于排除当前 output 的 schema 失败；该标记不再参与归因（其描述 run 状态的用途保持不变）。历史 retry 目标仍严格仅为观测背景。预算、重试、`category`/`stage`、终态与领域门禁不变。
> M13.8 修订（2026-09-11 依据首次 kernel 拒绝离线分析与 citation-duty 计划批准）：明确各 claim 的引用义务与收据预算自检，kernel prompt `p1.kernel.v13` / controller `p1.controller.v14`。（1）每个 claim 只承担自己的引用义务：在另一个 claim 中引用某条记录，**不构成**对需要该记录的 claim 的替代；可引用范围即已接受证据清单，补齐引用不需要追加工具调用。（2）ROOT_CAUSE 的必需引用按根因分派**分别**写明，不得合并或概括为通用要求：语义重复 `SOURCE_SEMANTIC_PAYMENT_DUPLICATE` 需要恰好 1 条 run-results 与恰好 1 条符合重复条件的声明关系 profile；精确重复 `SOURCE_EXACT_PAYMENT_DUPLICATE` 先满足该重复条件，**另需**失败节点 node error 与一条上游关系事实；其余根因（含 `SOURCE_REQUIRED_FIELD_NULL`、`TRANSFORMATION_*`、`NORMAL_*`、`LEGITIMATE_SPLIT_PAYMENT`）需要失败节点 node error 与一条位于其上游路径的关系事实；`SOURCE_PAYMENT_INGESTION_LOSS`、`SOURCE_PERMANENT_ORPHAN_PAYMENT` 与 schema 源根因沿用各自既有分支。（3）声明收据自检：声明 schema/profile/history 缺口前须确认同 subject、同工具的收据已存在；预算仍允许时按现行合同发起一次边界探针；预算已耗尽时不得声明该条，按公开证据可支持范围收尾。收据必须真实来自本 run 的拒绝，不得伪造，拒绝后不得再发探针。（4）`ROOT_CLAIM_EVIDENCE_INCOMPATIBLE` 反馈文案改为指出"ROOT_CAUSE 缺少该根因所需的记录类别、引用到其它 claim 不算、已在清单中则直接补引"，不暴露私有期望或具体证据 ID。领域验证器、evaluator、gate、预算、工具权限与 scenario 均未修改。
> M13.9 修订（2026-09-11 依据 p1-formal-v11 可恢复性离线分析批准）：补全拒绝反馈并让提示与终态契约一致，kernel prompt `p1.kernel.v14` / controller `p1.controller.v15`。（1）反馈覆盖：controller 只对 **controller 内部状态冲突**（`KERNEL_FINALIZED`、`PREPARED_CALL_INVALID`、`GAP_NOT_OPEN`、`KERNEL_NOT_INITIALIZED`、`MODEL_ERROR_REASON_INVALID`）保留通用回退文案；其余每个可由模型下次响应改善的 kernel 与策略拒绝码都必须在 retry 文案中给出具体纠正动作，文案只能由对应校验器的真实判据推导，不得暴露私有期望、具体证据 ID 或本 run 的私密标识。该覆盖由源码抽取式守卫测试强制：新增 `_error` / `KernelError` / `_PolicyError` 码而未补文案时测试失败。（2）提示与终态对齐：提示原先把"至少两个假设"限定在 CONFIRMED 之前，与 kernel 对 CONFIRMED 与 INSUFFICIENT_EVIDENCE 同时强制该要求的既有实现不符；提示改为对两个终态一并说明，并写明假设只能随业务调用登记、重复或等价调用不登记任何内容、工具预算耗尽后无法再补登记。该规则由一条新增的终态前提测试与提示合同断言共同锚定。（3）`ROOT_CLAIM_MISMATCH` 与 `ASSET_CLAIM_EVIDENCE_INCOMPATIBLE`（v11 seq62/seq50 的实际拒绝码）反馈分别指出"ROOT_CAUSE 必须等于选中假设已登记的根因码"与"资产值只能由该 claim 自己引用的记录支撑、profile 不能支撑资产值、无法支撑则移除该条声明"。预算保持 8/8/2/300，kernel 门禁、领域验证器、evaluator、scenario 与已冻结 manifest 均未修改；两处提示与反馈改动不改变任何判定结果，只改变模型收到的纠正信息。
> M13.10 修订（2026-09-11 依据提交完整性自检设计批准）：账本新增只读投影 `uncollected_relations`，按关系工具对应的证据类型做差集（`get_relation_schema→RELATION_SCHEMA`、`get_relation_data_profile→RELATION_DATA_PROFILE`、`get_relation_history→RELATION_HISTORY`），以记录的 `content.relation_name` 为准而非 schema 限定 subject，不跨类型相减；语义为"权限允许、该类型尚无已接受证据"，是事实清单而非待办，不保证预算耗尽或已有失败指纹时仍可调用。kernel prompt `p1.kernel.v15` / controller `p1.controller.v16` 新增三项完整性自检：资产完整性限定于 CONFIRMED 且以与根因相关的已接受证据为基线，三分支口径为——失败的 model 连同其已接受 downstream lineage 中命名的全部 model（失败 model 自身不出现在自己的 lineage 邻接中，须独立保留）、失败的 test 只取距离 1 上游 model（失败 test 节点与上游 seed 关系不算资产，upstream 记录的更远或 downstream 节点不扩展集合）、无失败节点时由已确认根因命名的 source/seed 节点的已接受 downstream lineage，明确为本项目影响范围口径而非普适因果事实；采集完整性自检扩展到 CONFIRMED 与 INSUFFICIENT_EVIDENCE 两个终态；history 收据与 watermark 声明分离，后者仅当已结算边界为区分摄取丢失与正常波动所必需且本 run 公开证据无法确定时，对相应公开 subject 独立声明 `INGESTION_WATERMARK / NOT_OBSERVABLE`，已有可用 watermark 或仅凭 history 请求被拒均不得推导。预算、kernel 门禁、领域校验器、evaluator、scenario 与既有冻结 manifest 均未修改；本修订只改变模型可见的提示与账本投影，不改变任何判定结果。
> M13.11 修订（2026-09-11 依据 p1-formal-v14 结果与离线定位需求批准）：为模型级校验拒绝增加固定、脱敏的原因码。（1）动机：模型级校验器（`loc=()`）拒绝整份 payload 时，`error_loc` 为空且 `error_kind` 仅为 `value_error`，无法区分重复项、空白文本、状态与字段组合等多条规则；v14 seq19 的失败因此可记录但不可解释。（2）实现：`diagnostic_contracts.MODEL_RULE_REASONS` 把每条模型级校验规则的原文消息映射为稳定码（13 条规则 13 个互不相同的码），`_model_rule_reasons` 只输出码，未映射的消息统一记为 `UNCLASSIFIED_MODEL_RULE`；`ModelProtocolTraceEvent` 新增 `error_reason` 字段（默认空元组，向后兼容既有 trace），仅承载码，校验器原文与任何 payload 取值均不落盘。（3）覆盖由源码抽取式守卫强制：新增模型级校验规则而未补原因码时测试失败；另有端到端测试证明原因码经 runner 写入 trace。（4）预算 8/8/2/300、kernel 门禁、领域校验器、evaluator、scenario、提示文本、工具 schema 与已冻结 manifest 均未修改；本修订只增加失败分类能力，不改变任何判定结果或重试行为，因此不改变模型可见协议与身份。
> M13.12 修订（2026-09-12 依据证据规划与拒绝后重判设计批准）：kernel prompt 升至 `p1.kernel.v16`（controller 保持 `p1.controller.v16`，控制流程、schema 与预算未变），以三处替换合并原先分散的规划与收据规则，不新增账本字段、不自动调用工具、不改写决策、不改变重试逻辑。（1）调用前规划替换原先无条件批量指令：按公开问题（定位失败、区分已登记原因、建立必要证据边界）选择调用，明确不扫关系白名单、把每次采集与必要边界探针都计入工具预算（被拒探针同样计一次）、为最终决策保留一次模型请求、优先决定性检查而非可选佐证、不因还有余额就继续花、复用已接受证据与既有收据（补引用不需新调用）。（2）弃答与提交自检合并末尾三段重复规则：分开检查「有收据的缺口」与「独立不可观测事实」，每个决定性 schema/profile/history 缺口复用同工具同 subject 的既有收据，否则在白名单内正常采集、或按既有边界规则在预算内发一次探针；不得伪造收据；finalize 从已记录 blocked gap 派生关系声明，一个关系的收据不能建立另一关系的缺口。支付量段落的领域绑定改为按公开 `SETTLED_PAYMENT_WINDOW_END` 观察的 subject 声明 `INGESTION_WATERMARK / NOT_OBSERVABLE`，并写明该观察只标识待证边界、不是 watermark 证据本身，不得仅凭 brief 主体列表推断该 subject，已接受证据已证明边界时不声明。（3）新增拒绝后重判：先从反馈与已接受证据定位失败前提；仅绑定或引用错误时直接修引用且不新增调用；前提仍可观测且预算允许时补采后重判；本 run 无法建立时重评 claim、假设判定与终态，且「多加证据 ID」不等于建立缺失事实。同一声明仅当修正针对被拒前提时才重试；健康声明被拒不证明事故，事故声明被拒不证明健康；只有替代结论具备自身所需证据时才确认，否则保留兼容假设并合格弃答；拒绝不禁止后续合法探针，但不得超预算或重复被阻断的业务调用。三个 payment-volume 场景的公开 brief 均含该观察，subject 由观察给出而非硬编码。预算 8/8/2/300、kernel 门禁、领域验证器、evaluator、scenario 与已冻结 manifest 均未修改；本修订只改变模型可见提示。
> M14 修订（2026-09-15 依据 research-driven improvement plan T01–T03 审计整改）：新增「评分输入归档与离线重评」合同（§13.1）与两条只读命令（§9）。（1）完整评测在写出六文件后，把评测器实际消费的全部输入固化为私有附件 `.dig/scoring-inputs/<run_id>/`：私有 ScenarioSpec 快照、冻结 ScenarioVerification、完整 DiagnosisRunResult、带来源的恢复证明（`lab.restore` 的 case/state/fingerprint）、预算、原 evaluator 身份与六产物摘要表；附件属管理平面，不得进入任何策略可见上下文。（2）加载严格拒绝重复 JSON 键、未知 schema 版本、文件或语义摘要不符、目录/索引/附件三方 run_id 不一致、场景/验证/诊断/恢复内容与其摘要不符、路径或符号链接逃逸、未知 evaluator 版本；kernel 终态必须从归档的类型化终态事件恢复并重新校验，不得仅凭 `diagnosis.json` 重造。（3）`eval score` 在禁用模型、数据库、dbt 与网络的条件下复用确定性 evaluator 重新评分，派生结果写入 `artifacts/rescores/<run_id>/<score_id>/`；`score_id` 由输入摘要、scorer 源码/依赖摘要与评分配置派生，scorer 身份必须对应实际执行代码。（4）派生目录自带完整性证据：provenance 记录 evaluation/diff/report 三个文件的原始字节摘要与跨文件一致性，命中既有 score_id 时先校验，不一致即拒绝；同一输入重复评分返回既有结果，不重写、不原地改分、不自动重跑。（5）逐项差异区分判定变化（适用性/通过状态）与依据变化（expected/actual 记 `details_changed` 并保留前后值）；无法与归档评分对照时必须输出"无法比较（原因）"，不得表述为逐项一致。（6）历史产物按完整可复评／仅可部分分析／不可复评分类并附固定原因；缺附件不得产出完整评分，也不得从当前 `config/scenarios` 或旧布尔值补造私有验证事实。（7）本修订不修改诊断平面、工具权限、预算 8/8/2/300、evaluator 判定规则、scenario 定义与既有冻结 manifest；模型可见协议与身份不变。
> M15 修订（2026-09-16 依据 research-driven improvement plan T07 批准）：新增「诊断质量指标」合同（§13.2），基准报告 `summary.json`/`report.md` 增加弃答、状态混淆、claim 支撑与引用指标。（1）每项新指标带 numerator、denominator、适用集合与零分母原因；既有指标（`paired_success`、`root_cause_accuracy`、`unsupported_confirmation_rate`、`status_accuracy`、`claim_evidence_validity`、`no_incident_accuracy`、资产 macro-F1、效率与预算指标）保持原口径与原结果。（2）有效格为未命中适用**环境门**失败的格；环境门限定为 `ENVIRONMENT_VERIFIED`、`EVIDENCE_RUN_SCOPE`、`RECOVERY_HEALTHY`（场景未验证、证据越出本 run、环境未能恢复）——只有这三类表示 harness 不可信；代理侧违规（`EVIDENCE_IDS_EXIST` 伪造引用、`TOOL_ALLOWLIST_EXACT` 越权工具、`TRACE_READ_ONLY_SAFE` 写尝试、kernel 状态门）一律计为失败试验并留在分母，代理不得靠违规逃出失败率。无效环境样本从所有新指标的分母隔离并单列计数；`MODEL_ERROR` 不是弃答，计入所在集合分母但不进入任何弃答分子，并单列错误率。（3）零分母输出 null 并附固定原因，不写 0 或 100%。（4）逐 claim 支撑判定复用 evaluator 自身的判定规则（与 `CLAIM_EVIDENCE_COMPATIBLE`、`POSITIVE_HEALTH_EVIDENCE` 同一实现，不另造第二套），适用性跟随 evaluator 的适用门控（CONFIRMED：根因与资产；NO_INCIDENT：健康；`INSUFFICIENT_EVIDENCE`：不适用）；无规则或不适用的 claim 类型单列，不记作"未支撑"。（5）引用指标只针对确定性可判定的结构化 claim，不宣称自然语言蕴含准确率，不得冠名 ALCE；引用有效性分别报告存在性与单条引用支撑，冗余敏感度报告"移除一条引用是否改变支撑判定"，移除不影响判定记为冗余而非错误；补足无意义引用不得提高任何硬门通过率。（6）报告在派生上述指标前必须校验运行结果输入（evaluator 源码摘要、profile spec、scenario/diagnosis schema）与冻结 manifest 的 `result_inputs` 一致，并逐格校验场景摘要与冻结目录条目一致，不一致即拒绝出报告。（7）**合同变更**：为复用逐 claim 规则，`evaluator` 升为 `p1.evaluator.v3`。v2 的 `_health_evidence_valid` 在第一条健康 claim 通过后即返回，多 claim 诊断中后续 claim 从未被校验；v3 要求每条健康 claim 独立成立。这是缺陷修复而非等价重构，已用旧实现复现（有效 claim + 指向无关关系的第二条 claim：v2 `True`、v3 `False`），并补多 claim 回归。旧证据保持不变：`KNOWN_EVALUATOR_VERSIONS` 保留 `p1.evaluator.v2`，既有附件、原始六文件、归档评分与冻结 manifest 文件均不改写；派生评分按新的 scorer 身份生成新 `score_id`，不覆盖旧派生目录。诊断平面、工具权限、预算 8/8/2/300 与 scenario 定义未修改；模型可见协议与身份不变。
> M16 修订（2026-09-16 依据 research-driven improvement plan T08 批准）：新增「重复可靠性协议」合同（§13.3），基准报告 `summary.json`/`report.md` 增加 `reliability` 稳定性块（协议 `p1.reliability.v1`）。（1）比较单位为组：单一场景 × 单一策略 × 单一冻结身份（`manifest_id`/摘要/实现修订/evaluator 身份/协议版本）；trial 永不跨组移动，组内 `repeat_index` 必须唯一且连续。（2）预定重复次数 n 来自冻结排程的 `repeat_index`，不由观测决定；当前 P1 排程中主策略每场景 3 次、消融与基线每场景 1 次；执行顺序为冻结 `sequence` 升序（重复按轮转分散）。（3）失败归类：评测 `PASSED` 且无失败的适用 controller（kernel）门为成功（`EvaluationResult.status` 不含 controller 门，必须单独判定），评测 `FAILED` 与 `MODEL_ERROR`（含超时、预算耗尽）为失败 trial；适用**环境门**失败（`ENVIRONMENT_VERIFIED`、`EVIDENCE_RUN_SCOPE`、`RECOVERY_HEALTHY`）为**无效样本**，既不计成功也不计失败，只使所在组不完整；代理侧违规（伪造引用、越权工具、写尝试、kernel 状态门）计为失败 trial，不隔离。环境门与代理违规同时出现时以环境门为准（样本不可解释，不产数字）。（4）`pass^k = C(s, k)/C(n, k)`，`s < k` 为 0，`n < k` 为 null；它是"k 次全部成功"的无偏估计，不是 `pass@k`。（5）组内缺失或无效时主组 `pass^k` 为 null 并列出下标，不得缩小 n、补跑或换格；完整子集分析单列且必须携带覆盖量，不得作为主组数字。（6）跨场景汇总只做完整组的宏平均，同时写出纳入组数与 trial 数；零分母为 null 并附原因；置信区间或重采样必须按场景聚类，不得把重复 trial 当作独立场景。（7）正式报告继续拒绝不完整 suite；协议的不完整组规则由只读入口 `benchmark partial`（`analyze_partial_suite`）提供。（8）坚持更大 n、外部策略或新场景使用新的协议版本与新的冻结 manifest，旧身份与其历史结果不变。（9）本修订不修改诊断平面、工具权限、预算 8/8/2/300、evaluator 判定规则、scenario 定义与既有冻结 manifest；模型可见协议与身份不变。真实测量需另行冻结并批准，在完成之前不得表述为"真实可靠性已验证"。
> M17 修订（2026-09-16 依据 research-driven improvement plan T09 批准）：新增「统一策略接入协议」合同（§10.5，协议 `p1.strategy_adapter.v1`，实现 `strategy_adapter.py`）。（1）harness 独占保管 run ID、工具白名单、预算 8/8/2/300、时限与证据登记；策略只能经 `task_context` / `call_tool` / `submit` / `cancel` / `report_usage` 五个操作行动，自报计数仅为辅助，权威计数来自 harness。（2）公开任务上下文只含 incident brief、关系白名单、工具白名单、预算与申报回显，不含私有场景合同、期望答案或 case ID。（3）工具调用规则：未知/越权工具在预算外拒绝；参数或 run 作用域不符拒绝；超预算拒绝且所有到达后端的调用计入 harness 计数；后端拒绝保留真实错误码作为收据；成功记录由 harness 登记，重复调用返回同一 evidence ID；策略不得跳过登记或更换 evidence ID。**收据必须携带本次返回的公开证据记录（事实内容）**，策略依据事实作答。（3.1）**预算 8/8/2/300 全部由 harness 执行**：模型请求经 `acquire_model_request` 配额门（内部模型策略的请求数由既有 controller 观测）；被拒提交消耗输出重试预算、耗尽后返回 `OUTPUT_RETRY_EXHAUSTED`；所有公开操作检查截止时间（`DEADLINE_EXCEEDED`）；外部进程中的模型调用不可观测，v1 只强制领取门，自报请求数超过已领取槽位记为 `usage_violation` 而非已受控。（3.2）**生产接线**：`FixedRuleRunner.for_run`（含参考解）与 `DiagnosisRunner.for_run` 构造时自行建立会话并把工具替换为门面；两类 runner 的终态都必须经会话——正常终态经 `session.submit` 落账（拒绝即 fail-closed 转 MODEL_ERROR 兜底），异常终态经 `session.cancel` 关闭（超时 `STRATEGY_TIMEOUT`，协议/运行时错误与兜底 `RUN_FAILED`）；终态之后任何工具调用必须返回 `SESSION_CLOSED`；回归必须通过 `for_run` 正常入口并覆盖 static 与 kernel 路径。（4）最终提交只接受已登记引用（`EVIDENCE_NOT_REGISTERED`），终态结构不满足 `Diagnosis` 合同即 `SUBMISSION_INVALID` 且不泄露私有期望；一会话最多一次提交或一次取消。（5）内置策略经 `ProtocolTools` 薄门面接入，等价由回归钉住：经门面与直连产生相同诊断、证据、计数、轨迹形状与 evaluator 检查结果。（6）开工前必须申报框架、模型、额外工具与可见上下文；申报不等于授权；申报、白名单、预算或可见上下文不同的策略不得标记为严格同条件对照。（7）本修订不修改诊断平面、工具实现、预算、evaluator 判定规则与既有冻结 manifest；跨进程传输（T10）与隔离验收（T11）另行交付。
> M18 修订（2026-09-16 依据 research-driven improvement plan T10 批准）：新增「六工具 MCP 入口」合同（§10.6，实现 `mcp_server.py`，SDK `mcp==1.20.0` 锁定）。（1）`tools/list` 只暴露六个只读证据工具，参数名与证据工具一致；无提交工具，最终提交仍走 `p1.strategy_adapter.v1`。（2）每次 `tools/call` 经同一 `StrategySession`：白名单、run 作用域、预算 8/8/2/300、截止时间、证据登记、错误码与证据载荷与进程内门面逐项一致；被拒调用保留后端真实错误码。**入参必须原样投递**（服务端关闭 SDK 的 schema 校验层），缺参、多参、类型错误与外来 run 一律产生与进程内相同的 `TOOL_ARGUMENT_INVALID` 收据并同样计一次尝试；只有未知工具名按门外免费拒绝处理（MCP 工具错误、不计尝试、无收据）。（3）按工具子集启用只是收窄，未知工具拒绝启动；场景关系白名单仍由后端证据工具强制，MCP 不放宽。（4）单进程服务单 run 单会话，调用串行化以保证顺序与计数确定；不得以吞吐为由引入跨 run 状态或缓存。（5）不提供 dbt CLI、SQL、管理命令或通用文件系统能力；断连不影响 harness 侧会话状态。（6）本修订不修改诊断平面、工具实现、预算、evaluator 判定规则与既有冻结 manifest；隔离执行与防泄漏验收由 T11 另行交付。
> M19 修订（2026-09-16 依据 research-driven improvement plan T11 批准）：新增「外部接入隔离与可复现验收」合同（§14.1，实现 `public_package.py`、`examples/isolation_probe.py`、`examples/isolation_acceptance.py`、`docker/isolation.Dockerfile`）。（1）公共任务包只含公开任务上下文与接入说明：不含私有场景合同、评分附件、case ID、期望答案或任何私有文件副本；导出后必须通过泄漏校验（禁用标记、私有文件字节副本、意外文件、符号链接）并以固定码 fail-closed。（2）隔离执行配置：策略进程只挂载沙箱（读写）与包源码（只读），无网络，环境变量按白名单过滤；MCP 协议本身不构成沙箱。（3）能读取私有配置/评分/artifact 平面的同用户进程属**可信开发模式**，仅用于演示与开发，不得用于无泄漏比较；验收报告必须如实标注其未隔离。（4）确定性探针必须尝试读取私有平面、其他 run、管理凭据并尝试写数据库；容器配置下四类尝试必须全部失败，凭据必须不存在。（5）确定性探针只证明边界行为，不得表述为真实模型抗注入能力已验证。（6）Docker 不可用或基础镜像/依赖无法拉取时，如实记录环境验收未完成或回退所用基础镜像，不得以本地未隔离进程冒充容器结论。（7）本修订不修改诊断平面、工具实现、预算、evaluator 判定规则、scenario 定义与既有冻结 manifest。（8）**闭环验收（`strategy_bridge.py` + `examples/isolated_client.py`）**：harness 独占唯一会话，策略进程（容器或本地解释器）的 stdio 承载 MCP，桥接把每次调用实时送入该会话并写出权威日志（`ToolGateway` 的 `recorder` 钩子逐条落盘）——证据登记与计数只由 harness 产生；策略进程自己的日志仅作辅助，丢失、裁剪或写入假数字都不改变 harness 的计数，被拒调用（含预算耗尽）同样留在日志里；权威日志写在沙箱之外，客户端不可达、不可改、不可删。答案只作为文件传递：harness 严格解析 `submission.json`（字段必须落在 `FinalSubmission` 合同内，多一个 `expected_status`/`incident_case_id` 即整文件拒收且不触达会话）后交给同一会话 `submit`；MCP 面上没有 submit/cancel 工具。验收必须同时覆盖成功与拒绝：合规客户端取证后提交被接受；攻击客户端伪造引用（`EVIDENCE_NOT_REGISTERED`）、删除自己的日志、超预算（第 9 次 `TOOL_BUDGET_EXHAUSTED`）、重复提交（`SUBMISSION_ALREADY_FINAL`）、终态后调用（`SESSION_CLOSED`）全部被拒。`isolation_verified` 只有在公共包校验、容器探针与闭环三者都通过时为真；本地进程配置下私有平面可达，闭环仍如实记录该事实，但不得据此宣称隔离。
> M20 修订（2026-09-17 依据 research-driven improvement plan T12；设计稿与计划校验层已审计通过，后续切片交付身份注册、模型接口、runner、离线回归与 benchmark 接线）：新增「公开证据义务规划器」合同（设计 `docs/superpowers/specs/2026-09-17-evidence-obligation-planner-design.md`，实现 `evidence_planner.py`；prompt `p1.planner.v1`，controller 协议 `p1.planner_controller.v1`）。（1）身份：`DiagnosticStrategy.EVIDENCE_PLANNER` 是新的模型策略身份，与 kernel/static 并列；其 runner（`EvidencePlannerRunner.for_run`，自建会话与 settings 模型）接线后进入 `MODEL_STRATEGIES`（benchmark 工厂按策略路由，`policy_surface_for_strategy` 委派给规划器自己的 `planner_policy_surface()`，`DiagnosisRunner.for_run` 对该身份显式拒收），但不在 `MAIN_STRATEGIES`/`KERNEL_STRATEGIES`，也不进入 `FROZEN_POLICY_STRATEGIES`——报告分区、排程单元与既有冻结 manifest 不因注册而改变，只有新的 manifest 身份获准冻结时才会排程它。政策身份由 `evidence_planner_policy_identity()` 给出，绑定 base prompt 摘要、规划器 prompt 摘要（原始 UTF-8 文本摘要）、controller 协议载荷（六工具及其 evidence_kind、主体参数映射 subject_argument、参数集合、预算 8/8/2/300、合法 outcome、`PLAN_*` 码表、义务与 verdict schema、计划拒绝预算条目）与工具 schema 摘要（每个工具的参数名、`string` 类型、required 列表与 `additionalProperties=false`，而不是仅有工具名列表）；切片 3 接入模型接口时，两个动作工具 `plan_step`/`close_obligation` 与终态输出工具 `submit_diagnosis` 的**实际注册 schema** 必须以同样方式绑定进载荷（从注册后的 agent 读回，包括名称、模型可见的描述与参数 schema，而不是另写一份平行副本）；其中任何一项变化都必须升版本，历史身份与既有冻结 manifest 不变。（2）义务身份：`<tool_name>:<canonical(arguments)>`，参数用规范 JSON（键排序、无空白、转义值）编码，不同参数集合不得碰撞；`evidence_kind` 与 `subject` 仅为展示字段。（3）一次 `plan_step` 恰好一个工具请求，在同一模型回合内同步执行；校验通过的声明消耗一次工具尝试（后端接受或拒绝都算），校验拒绝不消耗工具尝试、不产生收据。（4）义务只能被满足或撤销：`SATISFIED` 要求最近一次调用存在、被后端接受、返回非空证据，且声明引用至少一条该次调用的证据；后端拒绝或空结果只能 `REVOKED` 且必须给出公开理由；已关闭义务再次触达（`plan_step` 或再次 `close_obligation`）一律`PLAN_OBLIGATION_CLOSED`；`close_obligation` 的 outcome 必须做运行时校验，非 `SATISFIED`/`REVOKED` 一律 `PLAN_OUTCOME_INVALID`。（5）**校验拒绝不是收据**：`PlanVerdict` 只携带 `PLAN_*` 码，绝不携带证据记录或后端错误码；`ToolReceipt` 只在真实调用后产生；轨迹必须区分二者，报告不得把校验拒绝计成工具尝试或后端拒绝。（6）校验顺序固定：会话终态 → 截止时间 → 计划拒绝预算耗尽 → 工具名/outcome → 参数 → run 作用域 → 义务状态 → 工具预算；计划拒绝预算上限取 `output_retry_limit` 数值但为**独立计数器**，与 T09 提交被拒的计数分别报告；由于规划器比 kernel/static 多这一道策略门，`comparison_identity` 相同**不足以**宣称预算条件逐项相同，报告必须同时写明政策身份不同与该附加规则。（7）新变体本轮只登记为 dev 扩展回归集：现有准入对 holdout 直接返回 `HOLDOUT_SCENARIO`，禁止先按 dev 通过准入再回标 holdout；未见评估用途需要另行定义的受控认证与准入流程。（8）本修订不修改 kernel/static 的 prompt、controller、账本、evaluator 判定规则、工具权限、预算数值与既有冻结 manifest；规划器 runner 接线、规划器路径回归与 benchmark 接线已交付，新变体已物化为 dev 扩展回归集并完成认证准入与规划器真实证据链验证（2026-09-18 运行记录），新 manifest 冻结与真实测量另行交付，完成前不得用该身份做任何测量、比较或能力表述。

> M21 修订（2026-09-20 依据所有者对 p1-formal-v26 预检失败的诊断性裁定批准）：doctor 目录探针（`models.list`，经 SDK provider 客户端同路径）失败时在 `DoctorCheck` 新增**可选** `diagnostic` 字段，记录确定性脱敏摘要——`stage=catalog_list;kind={TIMEOUT|CONNECTION_ERROR|HTTP_<status>|MALFORMED:<stage>|ERROR};exc=<异常类名>;timeout_ms;elapsed_ms`。硬约束：不得记录异常消息、响应正文、鉴权头或任何密钥材料（按构造排除，仅插值类名、整数状态与计时）；不加重试、不放宽 5 秒超时、不改请求次数上界；通过检查与其他检查一律无该字段。该字段为加性可选，回执 schema 版本保持 `p1.benchmark_doctor.v3`，历史回执（无该字段）仍可解析；kind=ERROR 仅作未知异常（生产中 SDK 已包装为前三类）的兜底，仍脱敏并保留类名。模型工具调用能力（结构化输出探针）仍未验证，直至获准的预检通过。本修订不修改评测、预算、checkout 门与既有冻结 manifest。

> M21 故障修复补充（2026-09-20，所有者要求解决 v28 结构化探针失败，并逐次批准三次有界独立探针）：失败诊断延伸到 `MODEL_TOOL_STRUCTURED_OUTPUT`，只记录固定 kind（TIMEOUT / CONNECTION_ERROR / HTTP_<status> / USAGE_LIMIT / UNEXPECTED_MODEL_BEHAVIOR / ERROR）、60 秒上限、耗时、SDK 用量计数、工具执行布尔值和输出验证器拒绝次数，不记录异常正文或模型响应。SDK `model_requests` 是用量计数，HTTP 失败时可为 0，不等于没有发送 POST；不作为套餐扣量依据。成功与未执行的检查仍无 diagnostic。针对精确配对 `https://api.commandcode.ai/provider/v1` + `deepseek/deepseek-v4.1-flash`，共享客户端能力声明 `openai_supports_tool_choice_required=False`，doctor、DiagnosisRunner、EvidencePlannerRunner 同源使用；SDK 将默认强制选择降为 `auto`，不关闭 thinking，不改变工具 schema、提示、2 次探针请求/60 秒/1 次输出重试或正式策略预算。实际工具执行和结构化输出验收仍必须满足；其他模型/端点沿用原配置。该传输行为变化须绑定新的实施修订，既有冻结清单与失败回执不得修改。


> M22 修订（2026-09-20 依据所有者对 p1-formal-v27 预检归因的裁定批准）：目录模型 ID 安全正则由小写限定 `^[a-z0-9][a-z0-9._:/-]{0,127}$` 放宽为**允许 ASCII 大写** `^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$`——长度与其余字符限制不变，不做大小写归一，绑定模型名仍按原始 ID 精确匹配（大小写不同视为不存在）。背景：第三方目录合法包含大写厂商前缀 ID（26/71），旧正则会以无关条目阻断目标模型预检（v26/v27 失败与该缺陷一致）。控制字符、超长（>128）、非字符串 id、响应结构错误仍拒绝。不加重试、不放宽超时、不改请求上界；本修订不修改评测、预算、checkout 门与既有冻结 manifest。

> 当前约束：本文件定义 P0 基本原型及后续阶段边界；实施计划批准前不开始实现。

## 1. 产品摘要

DataIncidentGym 是一个面向数据工程师和值班开发者的、本地单用户使用的可复现数据管道故障诊断项目。它由两部分组成：

1. **Incident Lab**：能够重置数据、注入已知故障、保存标准答案并验证最终环境状态的实验环境。
2. **Diagnosis Agent**：通过受限只读工具调查 dbt 运行、PostgreSQL Schema 和 dbt 血缘，输出可引用证据的结构化诊断。

P0 不追求完整的数据可观测平台，而是证明一个最小闭环：

```text
健康数据管道
  → 确定性故障注入
  → dbt 稳定失败
  → Agent 自主选择只读工具调查
  → 输出根因、影响范围、证据和建议
  → 程序化验证诊断
  → 一条命令重置并再次重放
```

## 2. 问题与目标用户

### 2.1 问题

数据管道失败时，工程师通常需要在运行结果、日志、数据库 Schema 和模型血缘之间手工切换。监控系统可以报告“什么失败了”，但往往不能把这些分散事实组合成有证据支持的根因和下游影响范围。

### 2.2 P0 目标用户

P0 的唯一目标用户是：

> 在本地调查 dbt 数据管道失败的数据工程师或值班开发者。

### 2.3 P0 使用方式

- 单机、单用户 CLI。
- 用户主动选择案例或运行 ID 并启动诊断。
- 不接收线上告警，不连接真实生产环境。
- 不提供自由聊天入口。
- 不提供账号、角色、团队协作或通知能力。

## 3. 产品目标与非目标

### 3.1 P0 目标

1. 使用真实 PostgreSQL 和 dbt 模型复现一个确定性失败。
2. 让 Agent 仅通过类型化只读工具获得事实，不直接访问 Shell 或任意文件。
3. 要求每项诊断结论引用系统生成的证据 ID。
4. 使用确定性验证器判断根因、影响范围和证据是否正确。
5. 同时支持无需模型的自动测试和 Ollama 本地模型真实演示。
6. 在 Windows 11 / PowerShell 7 本地可运行，并在 Ubuntu CI 验证第二平台。
7. 在准备好依赖和镜像后，除当前配置的远程模型请求外，环境重置、构建、证据读取、controller/evaluator、产物生成和恢复流程可离线运行。

### 3.2 P0 非目标

- 多 Agent 协作。
- Airflow、OpenLineage 或 Marquez 集成。
- 向量数据库、embedding 或 RAG。
- Web UI、聊天界面或交互式追问。
- 自动修改 dbt SQL、数据库 Schema 或项目文件。
- 自动重跑任务或执行任何修复。
- 线上告警接入、生产部署或真实企业数据。
- 以另一个 LLM 作为主要正确性裁判。

## 4. 术语

| 术语 | 定义 |
|---|---|
| Incident Case | 一个可重置的故障案例，包含注入方式、标准答案和验收规则。 |
| Healthy Baseline | 故障注入前能够通过 `dbt build` 的固定数据与模型状态。 |
| Fault Injector | 只由 Incident Lab 使用的确定性写操作，用于制造已知故障。 |
| Run | 一次健康构建、故障构建、诊断或评测执行。 |
| EvidenceRecord | 工具返回的可审计事实，带唯一 ID、来源、类型和内容摘要。 |
| Diagnosis | Agent 的结构化最终输出，包括状态、根因、影响范围、证据引用和建议。 |
| Ground Truth | 案例预先定义的正确根因、受影响节点及必需证据类型。 |
| Blast Radius | 由故障直接或间接影响的 dbt 模型集合。 |

## 5. 开源复用与许可证策略

### 5.1 项目许可证

DataIncidentGym 使用 Apache License 2.0。

### 5.2 P0 数据模型基线

P0 通过固定 Git submodule 复用下列 Apache-2.0 项目，并在本项目外围提供 PostgreSQL 适配：

- 上游：[dbt-labs/jaffle_shop_duckdb](https://github.com/dbt-labs/jaffle_shop_duckdb)
- 固定分支：`duckdb`
- 固定 commit：`36bde6cba69d962b83be1d52fc65a0dce1cb4ebb`
- submodule 路径：`third_party/jaffle_shop`。
- 复用范围：submodule 内的 `seeds/`、核心 `models/`、相关 Schema/Test 定义和上游许可证。
- 修改范围：本项目仅增加外部 PostgreSQL profile、外围运行逻辑和测试；不得直接修改 submodule 中的上游文件。

仓库必须提供 `THIRD_PARTY_NOTICES.md`，记录来源、固定 commit、许可证、submodule 路径和适配方式。克隆后的准备流程必须明确执行 `git submodule update --init --recursive`。第三方文件继续保留其原始历史、版权声明和许可证。

### 5.3 后续阶段参考

| 项目 | 许可证 | 使用阶段 | 使用方式 |
|---|---|---|---|
| [jaffle-shop-generator](https://github.com/dbt-labs/jaffle-shop-generator) commit `01c0e8370f1855a86b740fc2e7c4b910cc7f52b8` | Apache-2.0 | P1 | 生成更大规模、带趋势的虚构数据。 |
| [Correlator Demo](https://github.com/correlator-io/correlator-demo) commit `ce35640e10ddd76eee4b59bf5eae6d60935144e0` | Apache-2.0 | P2 | 参考 Jaffle Shop、Airflow、dbt、数据质量和 OpenLineage 的组合方式。 |
| [OpenLineage Airflow Quickstart](https://openlineage.io/docs/next/guides/airflow-quickstart/) | 官方文档 | P2 | 参考 Schema 改名故障、Airflow 事件和 Marquez 排障流程。 |
| [PydanticAI](https://github.com/pydantic/pydantic-ai) | MIT | P0 | 复用 Agent、Ollama Provider、类型化工具、结构化输出和 TestModel。 |

未识别出明确许可证的仓库只允许作为设计参考，不复制其源码或数据。依赖版本在实现阶段依据真实兼容性测试写入 `uv.lock`；不得使用未锁定依赖完成正式验收。

## 6. P0 总体架构

```text
┌─────────────────────────────────────────────────────────────┐
│                       Python CLI                            │
│ doctor | lab reset/inject | pipeline build | diagnose | eval│
└───────────────┬───────────────────────────────┬─────────────┘
                │                               │
       管理平面（允许写）                诊断平面（严格只读）
                │                               │
       Incident Lab / dbt              PydanticAI Agent
                │                               │
       PostgreSQL + dbt artifacts       Typed Evidence Tools
                │                               │
       Ground Truth / Reset              EvidenceRecord
                └──────────────┬────────────────┘
                               │
                    Deterministic Evaluator
                               │
                    JSON / Markdown / JSONL
```

管理平面与诊断平面必须使用不同的数据库权限和配置。Agent 工具不得获得管理连接串、Fault Injector 或 Shell 执行能力。

`config/dbt/profiles.yml` 是管理 profile，仅供管理平面使用，包括 pipeline、IncidentLab/lab；`doctor` 和 `dbt debug` 必须使用独立的 `config/dbt/diagnostic/profiles.yml`；该诊断 profile 不含管理凭据、默认凭据或管理环境变量回退，只引用由 `DiagnosticSettings` 受控注入的 `DIG_DIAGNOSTIC_POSTGRES_*` 变量。缺少诊断配置时必须固定返回不可用，不得回退到管理 profile。

## 7. P0 模块与闭环要求

前一模块未通过验收时，不开始依赖它的后一模块。

### 7.1 M1：数据模型闭环

**职责**

- 启动固定版本 PostgreSQL 容器。
- 载入经过归属记录的 Jaffle Shop seeds。
- 运行适配 PostgreSQL 的 dbt 模型和测试。
- 生成 `manifest.json`、`run_results.json` 和 dbt 日志。

**完成定义**

- 全新环境执行一次命令后，`dbt build` 成功。
- `raw_customers`、`raw_orders`、`raw_payments` 及下游模型存在。
- dbt tests 全部通过。
- 同一固定 seed 重置 10 次产生相同的表结构和行数摘要。

### 7.2 M2：故障实验室闭环

**职责**

- 将健康基线恢复为已知状态。
- 注入首个字段改名故障。
- 运行 dbt 并保存失败产物。
- 提供机器可读 Ground Truth。

**完成定义**

- `reset → inject → build` 连续 10 次产生相同失败节点、错误类别和 Schema 状态。
- Schema 状态必须与固定 Ground Truth 精确匹配关系名、列名及顺序、`data_type`、可空性、`ordinal_position` 和行数；任一类型、可空性、序号、列或行数漂移都必须 fail closed，不得判定为健康或已注入。
- `reset → build` 能恢复健康状态。
- 故障注入不修改第三方模型源码。
- Ground Truth 能被独立验证器读取。
- M2 的稳定错误类别固定为 `DBT_MODEL_ERROR`，由 dbt `run_results.json` 中直接模型节点的 `status=error` 推导；不得依赖完整自然语言错误文本。

### 7.3 M3：证据工具闭环

P0 仅暴露以下只读工具：

1. `get_dbt_run_results(run_id)`：读取运行状态、失败节点和跳过节点。
2. `get_dbt_node_error(run_id, node_id)`：读取指定节点的规范化错误事实。
3. `get_relation_schema(relation_name)`：通过只读 PostgreSQL 连接读取列名和类型。
4. `get_dbt_lineage(node_id, direction)`：从固定运行的 `manifest.json` 查询上下游节点。

**完成定义**

- 每个工具都有独立单元测试和真实产物集成测试。
- 每次调用返回一个或多个 `EvidenceRecord`。
- 输入非法 run/node/relation 时返回类型化错误，不返回伪造空结果。
- 工具没有写数据库、执行 Shell、访问网络或读取任意路径的能力。

### 7.4 M4：Agent 闭环

**职责**

- 使用 PydanticAI 定义单 Agent。
- 通过统一 OpenAI-compatible 模型配置工作。
- 开发和本地演示优先使用 Ollama `gemma4:e4b`。
- 测试使用 PydanticAI `TestModel` 或等价的框架内测试模型。
- 输出固定 `Diagnosis` Schema。

**完成定义**

- TestModel 能覆盖工具注册、调用、输出校验和错误路径。
- `gemma4:e4b` 真实调用能够访问工具并生成结构化输出。
- Agent 不获得自由 Shell、文件系统、网络或写数据库工具。
- 证据不足时返回 `INSUFFICIENT_EVIDENCE`。

### 7.5 M5：评测与报告闭环

**职责**

- 一次命令完成重置、故障注入、失败构建、诊断和验收。
- 以 Ground Truth 和环境事实程序化验证输出。
- 保存机器报告、人工报告和可审计轨迹。

**完成定义**

- TestModel 自动化测试 100% 通过。
- 当前模型（M5.3 起为 `mimo-v2.5-pro`）在相同案例上独立运行 3 次，至少 2 次完整通过；历史模型结果不计入新模型分母。
- 失败运行同样保存，不从统计中排除。
- 不以另一个 LLM 的主观评分作为主要判据。

## 8. P0 数据模型与首个案例

### 8.1 逻辑模型

沿用 Jaffle Shop 的核心模型：

```text
raw_customers ─→ stg_customers ───────────────→ customers
raw_orders    ─→ stg_orders    ─→ orders ─────→ customers
raw_payments  ─→ stg_payments ─→ orders
                              └───────────────→ customers
```

### 8.2 案例标识

`schema_rename_payment_amount`

### 8.3 健康状态

`raw_payments` 至少包含：

```text
id
order_id
payment_method
amount
```

固定 Ground Truth 同时锁定上述列的 `data_type`、`nullable` 和 `ordinal_position`，以及行数 113。故障改名只允许把最后一列从 `amount` 改为 `total_amount`，其余 Schema 元数据必须保持一致；任意漂移都属于未知状态并拒绝继续。

`stg_payments` 读取 `amount` 并将其从分转换为金额单位。`orders` 和 `customers` 均依赖 `stg_payments` 的金额字段。

### 8.4 故障注入

Incident Lab 使用管理连接执行语义等价于下列操作的确定性变更：

```sql
ALTER TABLE raw_payments
RENAME COLUMN amount TO total_amount;
```

dbt 模型保持不变。

### 8.5 Ground Truth

```text
root_cause_code：SOURCE_SCHEMA_COLUMN_RENAMED
direct_failure：stg_payments
affected_assets：stg_payments、orders、customers
required_evidence_types：
  - DBT_NODE_ERROR
  - RELATION_SCHEMA
  - DBT_LINEAGE
```

正确诊断必须证明：

1. dbt 错误指向 `stg_payments` 对 `amount` 的读取。
2. 当前 `raw_payments` 不包含 `amount`，但包含 `total_amount`。
3. dbt manifest 显示 `orders` 和 `customers` 位于该节点下游。

## 9. CLI 需求

P0 的主入口为：

```text
data-incident-gym
```

必须提供：

```powershell
uv run data-incident-gym doctor
uv run data-incident-gym lab reset schema_rename_payment_amount
uv run data-incident-gym lab inject schema_rename_payment_amount
uv run data-incident-gym lab build schema_rename_payment_amount
uv run data-incident-gym pipeline build
uv run data-incident-gym diagnose schema_rename_payment_amount
uv run data-incident-gym eval run schema_rename_payment_amount
uv run data-incident-gym eval score <run_id>
uv run data-incident-gym eval compare-scores <run_id> <score_id_a> <score_id_b>
uv run data-incident-gym benchmark partial --manifest config/benchmark/<id>.json --confirm-sha256 <sha256>
```

要求：

- CLI 帮助和人工消息使用中文。
- JSON 字段、文件名、错误码和代码标识使用英文。
- 命令失败必须使用非零退出码。
- `eval run` 是完整闭环的一键入口。
- `eval score` 只读重评已归档的评分输入（§13.1）：不调用模型、数据库或 dbt，不修改原始六文件、manifest、ledger 或旧报告，也不改变原批次结论；输入不完整时输出历史产物分类与固定原因，不产出完整评分。
- `eval compare-scores` 只读比较同一运行的两个派生评分并输出逐项差异；无法对照时明确输出"无法比较（原因）"。
- `benchmark partial` 只读分析未完成（中断或仍在运行）suite 的重复稳定性（§13.3）：缺失格与无效样本按协议标记，不缩小 n、不补跑；它不产出正式报告，也不放宽 `benchmark report` 的完整性要求（ledger 每格两条、六产物齐全）。
- P0 不接受自由文本问题。
- `pipeline build` 保持健康基线语义，始终执行 `seed --full-refresh` 后再执行健康 `dbt build`。
- `lab build` 只在已注入状态执行不含 seed 的故障构建。底层 dbt 非零且独立验证符合 Ground Truth 时，实验命令成功并返回 `EXPECTED_FAILURE`；非预期结果返回非零退出码。

## 10. 模型与 Agent 要求

### 10.1 模型接口

- 统一使用 OpenAI-compatible 接口。
- 默认 Base URL：`https://api.xiaomimimo.com/v1`，允许通过 `DIG_` 前缀环境变量覆盖；默认 API Key 从用户环境变量 `MIMO_API_KEY` 读取。
- 默认开发模型：`mimo-v2.5-pro`（M5.3 起；历史默认 `gemma4:e4b`、`qwen3.5:9b`、`mimo-v2.5`）。
- 模型适配层不得绑定 Ollama 私有调用方式。
- P0 不使用 embedding 模型。

### 10.2 测试模式

- CI 和常规单元测试不得要求模型密钥或 Ollama。
- 优先使用 PydanticAI 自带 TestModel，不自建通用 fake model 框架。

### 10.3 兼容失败规则

真实验证必须先证明当前模型能完成工具调用和结构化输出。若连续 3 次可复现实验仍失败：

1. 停止添加正则解析、JSON 修补或模型专属循环。
2. 保存完整失败证据。
3. 保留当前模型配置但标记为未通过验证。
4. 切换其他模型或提供商前再次获得用户确认。

历史记录：`gemma4:e4b` 与 `qwen3.5:9b` 均在 M5 阶段未能在冻结预算内稳定完成工具调用后的结构化输出；用户已于 2026-08-28 确认切换，本规则现适用于 `mimo-v2.5-pro`。

当前批准的 M5.2 Diagnosis 合同（`m5.diagnosis.v7`）：模型只输出语义 decision；controller 仅根据当前 run 内的类型化证据确定性生成最终 `affected_assets` 与 `evidence_ids`，不读取或使用 Ground Truth；evaluator 保持独立，不参与答案生成。

### 10.5 统一策略接入协议

策略接入必须遵循 `p1.strategy_adapter.v1`（完整规范见
`docs/superpowers/specs/2026-09-16-strategy-access-protocol.md`）：

- harness 独占 run ID、白名单、预算、时限与证据登记；策略只有 `task_context`、`call_tool`、
  `submit`、`cancel`、`report_usage` 五个操作；自报计数仅为辅助。
- 公开任务上下文不含私有场景合同、期望答案或 case ID；工具收据必须携带公开证据记录的内容。
- 预算 8/8/2/300 由 harness 执行：工具调用计数、模型请求领取门、提交重试预算与截止时间。
- 生产入口（`for_run`）必须自行建立会话；不得只在测试里手动接入。
- 后端拒绝保留真实错误码；引用必须来自本 run 已登记的证据；一会话最多一次提交或取消。
- 内置策略经薄门面接入且与直连等价（诊断、计数、检查结果）。
- 申报（框架、模型、额外工具、可见上下文）不同的策略不得标记为严格同条件对照。

### 10.4 单次诊断预算

```text
模型请求上限：8
工具调用上限：8
结构化输出校验重试：2
总超时：300 秒
```

超过限制返回 `MODEL_ERROR` 并保存轨迹。P0 记录 token、耗时和工具调用次数，但不设置固定延迟完成门槛。
### 10.6 六工具 MCP 入口

`mcp_server.py` 通过本机 stdio 暴露六个只读证据工具（`mcp==1.20.0`，版本锁定）：

- 只暴露六工具、无提交工具；最终提交走 §10.5 协议。
- 工具子集只收窄不放宽；未知工具拒绝启动；关系白名单仍由后端工具强制。
- 每次调用经同一会话，预算门、截止时间、登记与错误码与进程内一致；拒绝保留真实错误码。
- 单进程单 run 单会话、调用串行化；不提供 dbt CLI、SQL、管理命令或文件系统能力。
- 断连不影响 harness 侧会话状态；隔离与防泄漏验收见 T11。

### 10.7 公开证据义务规划器（T12）

模型不再直接调用证据工具，而是先声明"要满足什么义务"：`evidence_planner.py` 是确定性计划校验层，
策略身份 `DiagnosticStrategy.EVIDENCE_PLANNER`（prompt `p1.planner.v1`，controller 协议 `p1.planner_controller.v1`）。

- 模型侧是两个**动作工具** `plan_step(tool_name, arguments, intent)` 与
  `close_obligation(obligation_id, outcome, evidence_ids, reason)`（都把收据/`PlanVerdict` 返回给模型继续
  推理，不结束 run）以及一个**终态输出工具** `submit_diagnosis`（唯一结束 run 的调用，终态仍走 §10.5 协议）。
  三者的实际注册 schema 从注册后的 agent 读回并计入政策身份载荷。
- 义务身份 `<tool_name>:<canonical(arguments)>`（规范 JSON：键排序、无空白、转义值），覆盖全部有效参数；
  `evidence_kind` 与 `subject_argument` 是展示字段，但其中任何一项都在政策身份载荷内，改动即改变身份摘要。
  一次 `plan_step` 恰好一个工具请求，经校验后同步执行。
- 校验拒绝**不是**收据：`PlanVerdict` 只带 `PLAN_*` 码，绝不携带证据记录或后端错误码，也不消耗工具尝试；
  `ToolReceipt` 只在真实调用之后产生。校验顺序固定为：会话终态 → 截止时间 → 计划拒绝预算耗尽 →
  工具名/outcome → 参数 → run 作用域 → 义务状态 → 工具预算。
- 计划拒绝预算是规划器**独有**的策略门（上限取 `output_retry_limit` 数值，独立计数器，与 §10.5 的提交重试
  分别报告）；因此 `comparison_identity` 相同不足以宣称预算条件逐项相同。
- 规划器身份与 kernel/static 并列；runner 接线（`EvidencePlannerRunner.for_run` 自建会话与
  settings 模型、benchmark 工厂路由、`policy_surface_for_strategy` 委派给
  `planner_policy_surface()`、`DiagnosisRunner.for_run` 显式拒收该身份）已交付，因此进入
  `MODEL_STRATEGIES`，但仍不在 `MAIN_STRATEGIES`/`KERNEL_STRATEGIES`，也不进入
  `FROZEN_POLICY_STRATEGIES`——排程单元、报告分区与冻结 manifest 只有在新的 manifest 身份获准
  冻结时才会看到它；完整规则见 M20 修订与设计文档。


## 11. 数据契约

### 11.1 EvidenceRecord

每条工具事实至少包含：

```text
evidence_id       运行内唯一且稳定
evidence_type     枚举类型
source            dbt artifact、dbt log 或 PostgreSQL catalog
subject           被观察的节点、关系或运行
observed_at       观察时间
content           规范化事实
content_digest    规范化内容摘要
```

工具不得把“未找到”伪装成正常空证据；未找到必须返回类型化工具错误。

### 11.2 Diagnosis

最终输出至少包含：

```text
status
incident_case_id
run_id
root_cause_code
summary
affected_assets[]
evidence_ids[]
recommended_actions[]
confidence（展示用途，不参与主要评分）
```

`status` 仅允许：

- `CONFIRMED`
- `INSUFFICIENT_EVIDENCE`
- `MODEL_ERROR`

当 `status=CONFIRMED` 时，根因和影响范围必须引用存在的证据 ID。当证据不足时必须返回 `INSUFFICIENT_EVIDENCE`，不得猜测。

## 12. 评测要求

### 12.1 P0 验收门槛

1. 环境重置与故障注入连续 10 次结果一致。
2. TestModel 自动化测试 100% 通过。
3. 当前模型（M5.3 起为 `mimo-v2.5-pro`）独立运行 3 次，至少 2 次完整诊断正确。
4. 正确诊断必须同时满足：
   - `root_cause_code` 精确匹配。
   - 受影响模型集合精确匹配。
   - 所有 `evidence_id` 真实存在。
   - 包含 dbt 错误、Schema 差异和血缘三类必需证据。
   - 输出通过 Pydantic Schema 校验。
5. 不发生任何 Agent 写操作。
6. 所有失败运行纳入统计并保留轨迹。

### 12.2 P0 结论边界

P0 的单个案例只证明工程闭环。不得从单个案例宣称系统具有普遍准确率、优于其他方法或达到生产水平。

### 12.3 P1 后的比较

至少完成 5 类故障、每类多个变体后，才比较：

- 无工具的单次模型回答。
- 固定规则诊断。
- 完整工具调用 Agent。
- 移除血缘工具或 Schema 工具的消融版本。

简历中的 Accuracy、F1 和提升比例只能引用这套完整评测的实际结果。

## 13. 运行产物与可观测性

每次诊断生成：

```text
artifacts/<run_id>/
├── metadata.json
├── trace.jsonl
├── evidence.json
├── diagnosis.json
├── evaluation.json
└── report.md
```

- `metadata.json`：案例、代码版本、模型、配置摘要、开始与结束时间。
- `trace.jsonl`：工具调用、参数、工具结果引用、耗时和错误。
- `evidence.json`：本次诊断实际取得的完整 `EvidenceRecord` 快照。
- `diagnosis.json`：最终结构化输出。
- `evaluation.json`：确定性评测器的逐项检查、失败原因码和总体判定。
- `report.md`：由结构化输出模板化生成的中文报告。

评测器必须只评分 Agent 结束时冻结的 `DiagnosisRunResult`、证据快照和轨迹，不得在恢复健康环境后重新查询数据库并混入新事实。凡已产生 `DiagnosisRunResult` 的诊断运行，无论评测通过、`INSUFFICIENT_EVIDENCE` 或 `MODEL_ERROR`，都必须保存全部六个文件并纳入统计。

不得保存或展示模型隐藏推理。允许记录工具选择、工具参数、可验证结果、最终结论、token、耗时和重试信息。

`artifacts/` 默认不提交 Git。固定 Ground Truth、预期证据 ID 规则及脱敏示例报告可以提交。提示词受版本控制，运行元数据记录其版本或内容 hash。

### 13.1 评分输入附件与离线重评

完整评测在写出六文件之后，必须把评测器实际消费的全部输入固化为私有附件
`.dig/scoring-inputs/<run_id>/`（`evaluation_inputs.json` 与 `index.json`）：私有 `ScenarioSpec`
快照、冻结 `ScenarioVerification`、完整 `DiagnosisRunResult`、带来源的恢复证明（`lab.restore`
返回的 case、state 与 fingerprint）、预算、原 evaluator 身份与六个产物的摘要表。附件属于管理
平面，不得进入任何策略可见上下文，也不随公开任务包发布。

- 加载必须严格：重复 JSON 键、未知 schema 版本、文件或语义摘要不符、目录/索引/附件三方
  `run_id` 不一致、场景/验证/诊断/恢复内容与其摘要不符、路径或符号链接逃逸、未知 evaluator
  版本，均以固定代码拒绝；kernel 终态必须从归档的类型化终态事件恢复并重新校验，不得仅凭
  `diagnosis.json` 重造。
- `eval score` 在禁用模型、数据库、dbt 与网络的条件下复用确定性 evaluator 重新评分；派生结果
  写入 `artifacts/rescores/<run_id>/<score_id>/`（provenance、evaluation、逐项 diff、报告）。
  `score_id` 由输入摘要、scorer 源码/依赖摘要与评分配置派生，scorer 身份必须对应实际执行代码，
  不得只替换标签。
- 派生目录自带完整性证据：provenance 记录 evaluation/diff/report 三个文件的原始字节摘要与
  跨文件一致性；命中既有 `score_id` 时先校验，不一致即拒绝，不返回被修改的缓存。同一输入重复
  评分返回既有结果：不重写、不原地改分、不自动重跑，原始六文件、manifest、ledger 与报告不变。
- 逐项差异必须区分判定变化（适用性/通过状态）与依据变化（expected/actual，记
  `details_changed` 并保留前后值）；无法与归档评分对照时，报告与 CLI 必须输出"无法比较（原因）"，
  不得表述为"与原评分逐项一致"。
- 历史产物按 `RE_SCORABLE` / `PARTIAL_ANALYSIS` / `NOT_RE_SCORABLE` 分类并附固定原因；缺附件的
  运行不得产出完整评分，也不得从当前 `config/scenarios` 或旧 `PASSED` 布尔值补造私有验证事实。
- 本小节不修改诊断平面、工具权限、单次诊断预算、evaluator 判定规则、scenario 定义与既有冻结
  manifest；模型可见协议与身份不变。

### 13.2 诊断质量指标（弃答与引用）

基准报告在既有运行级指标之外，必须输出下列指标。每项都带 numerator、denominator、适用集合
与零分母原因，并使用固定口径：

| 指标 | 分子 / 分母 |
| --- | --- |
| 可确认场景过度弃答率 | 期望 CONFIRMED 的有效格中实际弃答数 / 该集合格数 |
| 健康场景过度弃答率 | 期望 NO_INCIDENT 的有效格中实际弃答数 / 该集合格数 |
| 合格弃答率 | 期望 `INSUFFICIENT_EVIDENCE` 的有效格中弃答且整格 `PASSED` 的数 / 该集合格数 |
| 弃答 precision / recall | 阳性为"应弃答"、预测阳性为弃答终态；precision 分母为实际弃答的有效格，recall 分母为期望弃答的有效格 |
| 逐 claim 支撑覆盖 | 有合格事实支撑的适用结构化 claim 数 / 适用 claim 数 |
| 引用存在性 | 存在于本次运行证据清单中的引用数 / 全部引用数（按格去重） |
| 单条引用支撑 | 单独引用即可支撑其所属 claim 的引用数 / 根因与资产 claim 的引用数 |
| 冗余敏感度 | 移除后改变支撑判定的引用数 / 可移除引用数 |
| 状态混淆表 | 期望三态 × 实际四态（含 `MODEL_ERROR`）的完整计数 |

- 有效格：未命中适用**环境门**（`ENVIRONMENT_VERIFIED`、`EVIDENCE_RUN_SCOPE`、`RECOVERY_HEALTHY`）失败的格。无效环境样本从所有新指标的分母中隔离并单独计数；代理侧违规（伪造引用、越权工具、写尝试、kernel 状态门）不是环境失效，计为失败试验并留在分母——逃逸失败率不因违规而改善。
- `MODEL_ERROR` 不是弃答：计入所在集合的分母，不进入任何弃答分子，并单列错误率。
- "整格 `PASSED`"指 evaluator 证据检查全部通过**且**适用 controller（kernel）门全部通过：`EvaluationResult.status` 只覆盖证据检查，kernel 门失败必须被显式排除，否则违规 run 会被计为合格弃答。
- 零分母输出 null 并附固定原因，不写 0，也不写 100%。
- 支撑判定的唯一来源是确定性 evaluator 的逐 claim 规则；适用性跟随 evaluator 的适用门控，
  无规则或不适用的 claim 类型单列，不计为"未支撑"。
- 引用存在性同时在套件校验中作为硬前提（`referenced ⊆ known`，见 §13 六文件一致性）：能通过校验的
  套件其存在性恒为 1.0，该指标保留为显式不变式与单条引用支撑的分母语境，不作为区分度信号。
- 引用指标只针对确定性可判定的结构化 claim，不宣称自然语言蕴含准确率，不得冠名 ALCE；
  冗余是"移除一条引用不改变支撑判定"，属于报告事实而非扣分项；补足无意义引用不得提高任何
  硬门通过率。
- 报告在派生上述指标前必须校验运行结果输入与冻结 manifest 的 `result_inputs` 一致，并逐格
  校验场景摘要与冻结目录条目一致；不一致即拒绝出报告。
- 本小节不修改诊断平面、工具权限、单次诊断预算 8/8/2/300 与 scenario 定义；模型可见协议与身份
  不变。本合同变更包含一项 evaluator 缺陷修复：健康声明校验必须逐条成立（v2 只校验第一条，
  见 M15（7）），evaluator 身份随之升为 `p1.evaluator.v3`；新正式测量必须先冻结新的 manifest
  身份。

### 13.3 重复可靠性协议（`p1.reliability.v1`）

基准报告的 `strategies[*].reliability` 必须按下列固定规则输出重复稳定性；完整规范见
`docs/superpowers/specs/2026-09-16-repeat-reliability-protocol.md`。

| 规则 | 固定口径 |
| --- | --- |
| 分组身份 | 单一场景 × 单一策略 × 单一冻结身份；trial 不跨组、不跨版本移动 |
| 预定重复 n | 来自冻结排程的 `repeat_index`（主策略每场景 3 次；消融/基线 1 次），不由观测决定 |
| 成功 | 评测 `PASSED` **且**无失败的适用 controller（kernel）门 |
| 失败 trial | 评测 `FAILED`，以及 `MODEL_ERROR`（含超时、请求/工具预算耗尽） |
| 无效样本 | 适用环境门失败（`ENVIRONMENT_VERIFIED`、`EVIDENCE_RUN_SCOPE`、`RECOVERY_HEALTHY`）；代理侧违规计为失败 trial |
| 优先级 | 环境门与失败同时出现时以环境门为准，样本不产数字 |
| `pass^k` | `C(s, k)/C(n, k)`；`s < k` → 0；`n < k` → null；报告 k=1/2/3（受 n 限制） |
| 缺失/无效 | 主组 `pass^k` 全为 null 并列出下标；完整子集分析单列且必须带覆盖量 |
| 跨场景汇总 | 仅完整组的宏平均，写出纳入组数与 trial 数；零分母 null + 原因 |
| 区间 | 跨场景区间必须按场景聚类；不得把重复 trial 当作独立场景 |

- 不得为得到更好结果缩小 n、补跑、换格或静默丢弃失败 trial。
- 历史单次（n=1）运行只输出 `pass^1`；不得用跨场景或跨版本 trial 拼出更大的 n。
- 更大 n、外部策略（T09/T11）或新场景使用新的协议版本与新的冻结 manifest。
- 部分运行入口：正式报告（`benchmark report`）继续拒绝不完整 suite；协议承诺的"不完整组 + null + 覆盖量"由只读的 `benchmark partial`（`analyze_partial_suite`）提供，缺失格不进入分母，也绝不被补齐。该入口必须逐格校验身份：ledger 条目、`metadata`、`evaluation`、`diagnosis` 的 run_id/场景/策略/序号/时间线必须与目标格一致，`metadata` 必须携带本 manifest 摘要；身份不符、结构非法（合法 JSON 但形状错误）或状态不一致的样本标记为缺失并给出固定原因，绝不作为其它试次的分数；单格损坏只影响该格，不中断整批分析。

## 14. 安全与权限

1. Fault Injector 和 reset 使用仅属于管理平面的数据库连接。
2. Agent 工具使用单独的 PostgreSQL 只读角色。
3. 诊断进程不得读取管理连接串。
4. Agent 不暴露 Shell、任意文件读取、网络访问、数据库写入或源码修改工具。
5. 路径读取限制在当前 run 的已知 dbt artifacts 和日志目录。
6. P0 建议动作仅为文本，不触发执行。
7. 所有数据均为虚构数据。

### 14.1 外部接入隔离（T11）

- 公共任务包（`public_package.py`）：只导出公开任务上下文与说明；导出后由
  `verify_public_package` 逐文件校验禁用标记、私有文件字节副本与意外文件，发现即拒绝。
- 受限运行配置：容器内只挂载沙箱与包源码（只读）、`--network none`、环境变量白名单；本地同用户
  进程配置如实记录为"未隔离的开发模式"。
- 验收证据（`artifacts/isolation/acceptance.json`）：探针尝试读取私有合同、评分附件、其他 run、
  管理凭据并尝试写数据库（写尝试必须回滚，不留痕迹）；容器配置要求四类全部 DENIED。
- 闭环验收（`strategy_bridge.py`、`examples/isolated_client.py`）：harness 独占唯一会话，隔离客户端
  经桥接做 MCP 取证；答案只以 `submission.json` 文件提交，由 harness 严格解析后交给同一会话
  `submit`；harness 权威日志逐条记录每次调用（含拒绝），写在沙箱之外（客户端不可达），
  客户端自己的日志仅作辅助比对，删除或伪造它不影响结论。
- 闭环必须同时跑通合规路径（取证 → 提交被接受 → 终态为 `CONFIRMED`）与攻击路径（伪造引用、
  超预算、重复提交、终态后调用、跨出场景白名单的关系、未声明的工具全部被拒）；只有公共包校验、
  容器探针与闭环三者都通过时 `isolation_verified` 为真。`artifacts/isolation/closed_loop_*_harness_audit.jsonl`
  保留两条路径的权威日志。
- 确定性探针只证明边界行为，不证明真实模型抗注入能力；镜像或网络不可用时如实记录未完成。

## 15. 非功能要求

### 15.1 可复现性

- 相同 seed、相同案例和相同版本必须产生相同环境状态和 Ground Truth。
- 环境确定性与模型输出确定性分开度量。
- 每个案例必须支持一条命令重置和完整重放。

### 15.2 离线运行

依赖和 Docker 镜像准备完成后，环境重置、构建、四个只读证据工具、controller materialization、evaluator、artifact/report 生成及恢复流程必须可在无互联网环境中完成。当前批准的 M5.3 诊断默认使用远程 OpenAI-compatible endpoint `https://api.xiaomimimo.com/v1` 和 `mimo-v2.5-pro`，因此诊断及包含诊断的 `eval run` 明确需要网络和 `MIMO_API_KEY`；该模型请求不属于离线保证范围。除当前配置的模型 endpoint 所需的诊断请求（包含模型调查所需的 prompt 与工具结果）外，不发送遥测或其他数据到外部服务。

### 15.3 跨平台

- 本地硬要求：Windows 11 + PowerShell 7。
- 不以 Bash、Makefile 或 Unix 路径作为必需入口。
- Docker Compose 仅负责 PostgreSQL。
- 核心流程通过跨平台 Python CLI 驱动。
- GitHub Actions Ubuntu 作为第二平台验证。

### 15.4 工具链

```text
Python 3.12
uv + uv.lock
PostgreSQL Docker image
dbt-core + dbt-postgres
PydanticAI
Typer
pydantic-settings
pytest + pytest-asyncio
Ruff
Jinja2
```

版本必须锁定，但以实现阶段真实兼容性测试为依据，不在需求阶段任意指定。

### 15.5 语言

- Python、SQL、文件名、JSON 字段和错误码使用英文。
- 需求文档、CLI 帮助和诊断报告使用中文。
- dbt 上游模型名保持英文。
- Agent 系统提示词优先使用英文。
- 用户报告由结构化结果模板化为中文。
- P0 完成后 README 提供中英双语摘要。

## 16. doctor 要求

`doctor` 必须以只读方式检查并明确报告：

- Python 与 uv 可用性。
- Docker/Compose 可用性。
- PostgreSQL 容器和连接。
- dbt 安装、profile 和数据库连接。
- OpenAI-compatible 模型服务 endpoint 可达性。
- 当前模型（M5.3 起为 `mimo-v2.5-pro`）是否存在。
- 模型工具调用和结构化输出最小探针结果。
- 失败项对应的修复建议。

doctor 的成功不能替代完整 P0 评测；它只证明依赖和最小能力就绪。

## 17. 增量路线

### P0：基本原型

依次完成 M1 至 M5，不并行扩展功能。

### P1：增加故障类型

每次只新增一个“注入器 → Ground Truth → Agent 调查 → 自动评测”的完整案例：

1. 字段类型变化。
2. 必填字段空值。
3. 重复支付记录。
4. 孤立支付记录。
5. 静默行数下降。

#### M6：Diagnostic Kernel v1 与字段类型变化纵切

M6 只交付第一个 P1 纵切：

- 保留 schema_rename_payment_amount，并新增 schema_type_change_payment_amount；
- 新案例把 raw_payments.amount 从 integer 改为 text，root_cause_code 为 SOURCE_SCHEMA_COLUMN_TYPE_CHANGED；
- Diagnosis Agent 显式维护候选假设、EvidenceGap、claim-evidence bindings 和剩余预算；
- 调查语义由模型负责：选择业务工具、维护候选假设、引用证据并给出结论；controller 在调用边界管理 gap 标识（分配 gap ID）和工具到 gap 类型的映射，模型不再在工具参数中填写 kernel_gap_id 或 kernel_gap_kind（M6.1 修订，2026-09-08）；
- CONFIRMED 前至少有两个候选假设、一个受支持的选中假设和一个被证据反驳的替代假设；
- 模型负责 root cause、affected assets 与 evidence IDs；Diagnostic Kernel 只验证、拒绝和投影模型声明，不替模型生成答案；
- evaluator 继续独立读取 Ground Truth，并对冻结的 InvestigationState、Diagnosis、EvidenceRecord 与 trace 做确定性评分；
- 六文件产物合同不变，最终 InvestigationState 作为 trace.jsonl 的类型化终态事件保存并进入 report.md；
- 两个案例分别执行精确三个真实模型样本，每个案例至少两个 PASSED；失败全部保留并进入分母。

M6 不实现静态 Skill baseline、消融、Accuracy/F1、跨变体结论、用户自定义故障 DSL、自由 SQL、写工具或自动修复。

### P2：真实编排与血缘平台

- Airflow。
- OpenLineage。
- Marquez。
- 跨任务运行历史和变更时间线。

P2 优先参考 Correlator Demo 与 OpenLineage 官方教程，不复制许可证不清晰的实现。

### P3：受控操作与安全性

- 人工批准后重跑。
- 暂停与恢复。
- 间接提示注入测试。
- 权限和副作用审计。

只有 P3 出现明确的持久化、暂停和恢复需求时，才重新评估 LangGraph Functional API；P0 不引入 LangGraph。

## 18. P0 总验收清单

- [ ] Apache-2.0 项目许可证和第三方归属完整。
- [ ] Windows PowerShell 从全新环境说明可启动 PostgreSQL。
- [ ] 健康 Jaffle Shop PostgreSQL 适配版 `dbt build` 通过。
- [ ] 首个案例可重复注入、稳定失败并完整重置。
- [ ] 四个只读证据工具分别通过测试。
- [ ] PydanticAI TestModel 流程通过。
- [ ] 当前模型（M5.3 起为 `mimo-v2.5-pro`）完成真实工具调用和结构化输出。
- [ ] 单次运行预算被强制执行。
- [ ] Agent 证据不足时明确拒答。
- [ ] 确定性验证器拒绝错误根因、错误影响范围和不存在的证据。
- [ ] TestModel 测试 100% 通过。
- [ ] 真实模型 3 次中至少 2 次通过。
- [ ] `metadata.json`、`trace.jsonl`、`evidence.json`、`diagnosis.json`、`evaluation.json` 和 `report.md` 六个产物齐全。
- [ ] 未保存隐藏推理，未发生 Agent 写操作；除当前配置的诊断模型 endpoint 所需请求外，未发生其他外部网络访问。
- [ ] P0 结果未被误述为通用准确率结论。

## 19. 风险与控制

| 风险 | 控制措施 |
|---|---|
| 本地小模型不能稳定调用工具 | 先运行最小能力探针；连续 3 次失败后停止补丁堆叠并请求模型决策。 |
| dbt 上游示例更新导致漂移 | 固定上游 commit，保留许可证和变更记录。 |
| PostgreSQL 与 DuckDB SQL 差异 | 只做最小方言适配，并以健康 `dbt build` 测试证明。 |
| Agent 猜测或引用不存在证据 | EvidenceRecord、结构化引用和确定性验证器共同拒绝。 |
| 管理连接泄漏给 Agent | 管理平面与诊断平面使用不同配置和数据库角色。 |
| 原型被基础设施拖累 | P0 不接 Airflow、OpenLineage、Marquez 或 Web UI。 |
| 单案例结果被过度宣传 | P0 明确只证明闭环，P1 后才报告比较指标。 |

## 20. 需求变更规则

- P0 范围、完成定义、非目标或安全边界发生变化时，必须先修改并重新批准本文件。
- 不得以“顺便实现”为由把 P1–P3 功能提前塞入 P0。
- 需求文档批准后，再单独编写带确切文件路径、测试和命令的实施计划。
- 实施计划批准后才开始代码开发。
