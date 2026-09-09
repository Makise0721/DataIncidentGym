# p1-formal-v8 Kernel 子集 smoke 分析

分析日期：2026-09-09。仅分析既有产物，不运行新模型样本，不重判或改写原始评价。

## 结论

8 个选定 cell 全部产生终态和六文件产物，3 个评价 PASSED、5 个 FAILED。自动 gap 记账在本子集中正常出现，未看到 DUPLICATE_GAP_ID 或 GAP_TOOL_MISMATCH；但这不能证明较旧协议有性能或质量提升。

下一步应优先修复三个具体问题：schema 根因确认缺少目标关系的直接证据约束；血缘工具把 relation name 当作 dbt node_id 的接口不一致；不可查询关系的提示与缺失证据声明合同相互冲突。暂不放宽全部 gap 关闭门禁，也不增加模型预算。

这次 seq59 提供了新的真实失败证据：所有已打开 gap 均关闭，仍出现不应确认的结论。先前离线评估只建议保留 strict gap；它不表示其他确认校验已经充分，也不妨碍针对本次新证据安排窄范围修复。

## 身份与证据范围

- 来源 worktree：`C:/Users/29913/.config/superpowers/worktrees/DataIncidentGym/p1-v8-kernel-smoke-20260908`。
- 分支：`codex/p1-v8-kernel-smoke`；当前 HEAD 与 8 份 metadata.code_revision 一致：`21250df9d556eae85e56e638a7b255b351d823d5`；metadata.workspace_dirty 全部 false，检查时 worktree 无未提交修改。
- main：`f61bbfde04877eb4b54865db313647a04791db5f`。smoke 比 main 只多冻结 `config/benchmark/p1-formal-v8.json` 的提交，运行代码相同。
- 实际模型：`mimo-v2.5-pro`，provider `openai-compatible`；kernel prompt `p1.kernel.v9`，controller `p1.controller.v8`。批次名 v8 与 prompt v9 是不同版本维度。
- Manifest 文件 SHA-256：`e7c52d4065ed2e6872b40530c38c6821a93140fb54ad7cd2db1c83277998bfac`，与 metadata 和 doctor receipt 中引用一致。
- `subset.json` 指定 DIAGNOSTIC_KERNEL 的 22/37/44/50/59/62/66/67；ledger 为 8 个 STARTED + 8 个终态，无本子集缺失或重复终态。
- 实际执行时间：2026-09-09 11:04:28–11:33:03（Asia/Shanghai）；worktree 名的 20260908 不是实际执行日。
- 每 cell 6 个产物，共 48 文件。所有 cell 的 ENVIRONMENT_VERIFIED、TRACE_READ_ONLY_SAFE、RECOVERY_HEALTHY、EVIDENCE_RUN_SCOPE、TOOL_ALLOWLIST_EXACT 检查均通过；metadata recovery 均为 HEALTHY。本结论复核已存检查结果，没有重建数据库或重新验证环境。
- 来源路径、逐 cell 汇总、52 个文件的 SHA-256 见同目录 `2026-09-09-p1-v8-kernel-smoke-evidence-index.json`（48 产物 + manifest/subset/ledger/doctor）。它是派生分析索引，不是新 Manifest 或正式归档。

名字包含 formal，但 subset 明确存在：这是 8 cell 的定向 smoke，涉及 6 个不同案例。没有 STATIC_SKILL 对照，不覆盖完整 106 cell，也不能与不同条件的历史批次直接比较。

## 逐样本结果

| seq / repeat | 案例 | 预期 → 实际 | 评价 | 请求数 / 工具成功数-尝试数 | 关键证据 |
|---|---|---|---|---|---|
| 22 / 1 | order_volume_pattern_a | NO_INCIDENT → NO_INCIDENT | PASS | 2 / 3-3 | 健康证据与终态通过 |
| 37 / 2 | order_volume_pattern_a | NO_INCIDENT → NO_INCIDENT | PASS | 4 / 3-3 | 同案例第二次，质量通过；请求数不同 |
| 44 / 2 | schema_type_change_order_customer_b | INSUFFICIENT → MODEL_ERROR | FAIL | 7 / 8-8 | ASSET_CLAIM_EVIDENCE_INCOMPATIBLE，随后 OUTPUT_SCHEMA_REJECTED |
| 50 / 3 | silent_payment_drop_partition_a | CONFIRMED → MODEL_ERROR | FAIL | 4 / 7-8 | 非 canonical lineage ID 失败后留下 BLOCKED，EVIDENCE_GAP_OPEN，最终 MODEL_TIMEOUT |
| 59 / 3 | schema_type_change_order_customer_b | INSUFFICIENT → CONFIRMED | FAIL | 6 / 7-7 | 无 raw_orders schema/转化定义，仍确认 source type changed |
| 62 / 3 | required_null_order_customer_a | CONFIRMED → CONFIRMED | PASS | 3 / 7-7 | 根因、影响资产和证据检查通过 |
| 66 / 3 | duplicate_payment_coupon_a | CONFIRMED → INSUFFICIENT | FAIL | 7 / 3-6 | 血缘参数/假设引用三次失败，未获取影响血缘 |
| 67 / 3 | duplicate_payment_coupon_b | INSUFFICIENT → MODEL_ERROR | FAIL | 7 / 3-4 | 两次 UNRESOLVED_EVIDENCE_UNBOUND，随后 OUTPUT_SCHEMA_REJECTED |

表中 INSUFFICIENT 简写指 INSUFFICIENT_EVIDENCE。成功比例 3/8=37.5% 仅描述所选 cell；健康 2/2、confirmable 1/3、insufficient 0/3，不是总体质量估计。

累计 metadata 用量为 40 次模型请求，419,016 input tokens、52,464 output tokens；41/46 次工具尝试成功。诊断阶段累计 1,201,759 ms，包含串行样本，不代表端到端批次耗时。seq50 诊断耗时 300,094 ms，实际触发 300 秒诊断超时。

## A. seq59：确认门禁放行了证据不足的根因

run：`d2abcf132c6a3e4d97256035e806c89e`。

trace 中接受的相关事实包括：customers 节点的 `integer = text` 报错、customers 上游血缘、raw_customers/raw_payments schema、raw_orders/raw_customers profile。没有 raw_orders schema，也没有 transformation definition；runtime 明确只允许 schema 查询 raw_customers/raw_payments。

最终 ROOT_CAUSE claim 的 `relation_name=raw_orders`，却引用 raw_customers schema 和 raw_orders 聚合 profile；summary 进一步断言 stg_orders 的字段重命名和源列 text 类型，当前这些类型化证据不足以排除 transformation cast。最后两条 gate 为 ROOT_CLAIM_MISMATCH 拒绝一次、CONFIRMED 接受一次。

两个假设 `h_col_type_raw_customers` 与 `h_col_type_raw_orders` 的 root_cause_code 都是 SOURCE_SCHEMA_COLUMN_TYPE_CHANGED。它们区分了模型命名的关系，但没有显式保留 source type change 与 transformation cast 的根因类别竞争。不能仅因 ID 不同就认为这类竞争已被检验；也不能把“所有根因代码必须不同”直接推广到所有场景。

实现原因：`diagnostic_validation._require_incident_node_and_upstream_evidence` 只要求 node error + 任一上游关系的 schema/profile fact，并不把 schema 根因 claim 的目标关系与直接 schema 证据绑定。当前严格 gap 门禁只约束已打开的调查，未请求过的关键证据不会自动成为 gap。

评价拒绝 CONFIRMED 的依据是冻结的 INSUFFICIENT 合同。source type change 与实际注入方向一致，不代表 Agent 凭公开证据已经排除了其他解释。修复不能读取该 cell 的 expected_status、case_id 或私有答案来直接返回 INSUFFICIENT。

## B. seq50/66/67：关系名和血缘节点 ID 混用

- seq50：`get_dbt_lineage(raw_payments, downstream)` → NODE_NOT_FOUND；随后 `seed.jaffle_shop.raw_payments` 成功，但旧失败 gap 保持 BLOCKED，CONFIRMED 被 EVIDENCE_GAP_OPEN 拒绝，最后超时。
- seq66：`analytics.raw_payments` → NODE_ARGUMENT_NOT_PROVEN；`raw_payments` → HYPOTHESIS_REFERENCE_UNKNOWN；再次 `raw_payments` → NODE_NOT_FOUND。没有成功血缘，最终不足。
- seq67：`raw_payments` → NODE_NOT_FOUND；随后 canonical seed ID 成功。终态还受缺失声明问题影响，不能将全部失败只归因于血缘。

已核对三份 run 的 incident_brief.subjects 同时包含 relation `raw_payments` 和 canonical ID `seed.jaffle_shop.raw_payments`。kernel `_validate_argument_provenance` 接受任何 incident subject；底层 `EvidenceTools.get_dbt_lineage` 只接受 manifest catalog 的精确键。三个 manifest 中前者不存在、后者存在。

这是可确定性复现的接口边界缺陷。优先让模型看到精确可调用 node_id，并在打开 gap 前拒绝不合法标识；不要为弥补该缺陷而删除 BLOCKED、改写为 CLOSED 或放宽确认门禁。

## C. seq44/67：已知不可查询与缺失声明合同冲突

现行 prompt 要求只查询 provable_relations；然而 `validate_unresolved_declarations` 对 RELATION_SCHEMA/PROFILE/HISTORY 的声明要求实际存在相同 subject/error 的 BLOCKED gap。独立 evaluator `_insufficiency_matches` 进一步要求每个有 tool_name 的预期缺口恰好有一次匹配错误调用。

seq44 未尝试被限制的 raw_orders schema，也未取 raw_orders history；seq67 未尝试被限制的 raw_payments profile。提示词鼓励避开的调用，恰好是当前缺失证据合同要求的收据来源。

本轮纯函数探针验证：同一个 `RELATION_DATA_PROFILE / raw_payments / RELATION_NOT_ALLOWED` 声明，没有 blocked tuple 时抛 UNRESOLVED_EVIDENCE_UNBOUND，提供匹配 tuple 时通过。没有数据库访问或模型请求。

现有 trace 没保存被拒绝的模型输出内容，因此无法断言 seq67 两次声明的具体字段，更不能把该探针视为逐字节重放 seq67 的失败输出。这里成立的是实现上的合同冲突及观察到的错误链。

建议首期保留现有 evaluator 失败调用收据合同，在提示中明确一个窄例外：对已由公开证据证明相关、确需声明的不可查询关系，允许一次只到 controller 权限拒绝层的调用；它必须计入原预算并记录真实拒绝，不访问数据库、不扩展工具权限、不反复尝试。更长期的“根据权限快照直接证明不可得”可另行设计，本期不引入新证据 schema。

## D. 输出失败与时延：能确定和不能确定的内容

seq44、67 的 MODEL_PROTOCOL 都定位到 OUTPUT_SCHEMA_VALIDATION / OUTPUT_SCHEMA_REJECTED，但现有 trace 不含安全字段定位或具体 Pydantic 错误类型。无法判断是 status、claims、unresolved 字段还是其他结构错误。不能凭终态猜测精确模型响应，或承诺某一个 prompt 改动必然解决。

seq50 在一次 EVIDENCE_GAP_OPEN 后超时；底层成功工具通常只花几十至几百毫秒，诊断大部分耗时在模型及循环。仅凭这些聚合数据不能区分生成速度、输出冗长、网络等待和重试各自占比。保持 8 请求/8 工具/2 输出重试/300 秒预算，不用延长时间掩盖前面三个问题。

MODEL_ERROR 的 diagnosis.evidence_ids 被投影为空不表示此前没有采集证据：44/50/67 的 evidence 文件和终态 evidence_inventory 分别保留了 8/7/3 条事实。相应 REQUIRED_EVIDENCE_TYPES_PRESENT 的 actual=[] 需结合失败终态理解。

## 下一步

详见 `../plans/2026-09-09-p1-v8-kernel-smoke-followup.md`。先建立 seq59 的错误确认离线回归，再修血缘候选与缺失声明提示的一致性；安全输出错误定位作为后续小任务。所有新验证均使用确定性替身和本地服务，真实模型重跑不在本次计划执行范围内。
