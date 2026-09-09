# p1-v8 followup 实施审计

日期：2026-09-09。初审发现的四个待修问题已在同日复审中确认关闭；当前结论为本次聚焦复审通过，未发现新的阻塞问题。下文保留初审证据，最新验证范围见文末“修复后复审”。没有运行新真实模型样本。

## 范围与已确认成果

实施 worktree：`C:/Users/29913/.config/superpowers/worktrees/DataIncidentGym/p1-v8-smoke-followup-20260909`，分支 `codex/p1-v8-smoke-followup-20260909`，HEAD `f61bbfde04877eb4b54865db313647a04791db5f`，变更尚未提交。主工作区 main 没有这些实施代码。

核对 diff 后确认：schema 根因要求显式 relation_name 并引用同一关系的 schema；血缘初始候选从 manifest 精确匹配公开 subjects；权限探针仍在原拒绝路径留下收据；evaluator 生产规则、预算和 strict gap gate 未修改。新增 seq44/67 风格的 FunctionModel 收尾测试实际调用 DeterministicEvaluator。

本轮复跑：

```powershell
uv run pytest tests/unit/test_diagnostic_validation.py tests/unit/test_probe_receipt_flows.py tests/unit/test_diagnostic_kernel.py tests/unit/test_diagnostic_agent.py tests/unit/test_kernel_auto_binding.py -q
uv run ruff check .
git diff --check
```

结果：82 passed in 10.11s，Ruff 与 diff 检查通过。用户报告的全套验证通过未在本轮重复执行，本文不将其计为独立服务验证。额外探针全部为本地纯函数或 FunctionModel；临时目录已清理。

## 发现 1：安全诊断会保留模型提供的未知字段名（P1）

位置：实施树 `src/data_incident_gym/diagnostic_agent.py:648`，`_safe_retry_details`。

代码只检查 loc 段是 string，并未按模型 schema 字段白名单过滤。对于 extra_forbidden，Pydantic loc 本身就是模型提交的未知 key，因此“只记录字段名”不能保证不记录原始模型输入。

本轮使用合法 KernelDecision 加一个合成未知 key `PRIVATE_CONTENT_IN_UNEXPECTED_FIELD`，真实执行 Pydantic 校验后把 errors 传给 RetryPromptPart。函数返回：

```text
error_loc = ('PRIVATE_CONTENT_IN_UNEXPECTED_FIELD',)
error_kind = ('extra_forbidden',)
```

该值随后可进入 ModelProtocolTraceEvent 并被持久化。这里是合成标记，没有使用真实敏感值。现有字符串长度也未受限；error_kind 的正则是格式校验，不是固定错误类型白名单。

建议：只输出已知 schema 字段/枚举，未知 key 映射为固定占位符；明确路径及总长度上限、错误类型白名单。增加 extra_forbidden 的任意长 key、嵌套未知 key 和自定义错误类型回归，断言标记不出现在序列化结果中。

## 发现 2：错误字段定位滞后一轮，可能误导后续分析（P2）

位置：`src/data_incident_gym/diagnostic_agent.py:607` 和 `_record_protocol_failure`。

last_retry_details 在请求发出前从整个历史 messages 里提取，记录的是模型本次收到的“上轮错误”；当当前输出耗尽重试并抛异常时，代码把旧详情贴在当前失败上。也没有请求序号或 tool_call_id 来验证归属。

本轮 FunctionModel 探针：前两次输出缺 summary，第三次补齐 summary 但将 status 改成非法 MAYBE。最终三次请求后 MODEL_ERROR，记录仍是：

```text
category = OUTPUT_SCHEMA_REJECTED
error_loc = ('summary',)
error_kind = ('missing',)
```

当前实际错误应为 status 的 literal_error，不能把旧 missing 当作当前失败原因。

建议：在当前校验错误捕获点提取安全详情并绑定对应请求/调用；如果 SDK 最终异常没有细节，则当前事件详情留空，保留粗分类，不推测。若要保存上一次错误，必须明确标为历史记录。增加“错误 A → 错误 B”“参数失败 → 合法调用 → 终态失败”的序列测试。

## 发现 3：首次请求仍跳过 ledger，精确节点候选没有展示（P2）

位置：`src/data_incident_gym/diagnostic_agent.py:747`，`_kernel_state_prepare`。

原有 `if not snapshot.hypotheses and not snapshot.gaps: return tool_def` 仍存在。新 kernel 的初始候选即使有值，第一次模型调用也看不到 provable_lineage_nodes/relations 和预算账本，而 prompt 又要求从账本选 ID。

纯函数探针输入初始候选 `seed.demo.raw_payments`，原工具描述 `Lineage lookup`；prepare 后描述仍只有 `Lineage lookup`。这未完成计划 Task 2 的“初始化账本也应可见”要求。

建议：所有 kernel 首次工具 prepare 都附带账本，包括候选为空的情况；static/no-tool 继续维持原行为。新增捕获第一次 FunctionModel AgentInfo 工具描述的测试，不能仅直接测试 summary 函数。

## 发现 4：schema prompt 引入了超出实现范围的不足条件（P2）

位置：`src/data_incident_gym/prompts/diagnostic_kernel.md:55`。

新增规则使用 `target relation schema is blocked ... or the transformation definition is not observable`，要求任一成立即返回 INSUFFICIENT_EVIDENCE。当前业务工具没有 transformation definition 的类型化输出，因此即使正确目标 schema、失败证据及其他确认条件齐全，也会被该提示引导为不足。

这与 Task 1 保留合格正例的范围、实际 validator 的必要证据检查及新正例 `test_schema_source_root_accepts_target_relation_schema_on_upstream_path` 不一致。FunctionModel 正例按脚本提交结果，不证明模型遵循新 prompt 时不会受此影响。此项是静态合同冲突，未通过真实模型测量其发生频率。

建议：把不足条件写为“目标 schema 不可得，或现有公开证据仍不能区分 source change 与 transformation cast”，不要把单一工具能力缺失等同于结论必然不可判定。同步核对 requirements、正反 fixture 与 prompt 的判定矩阵，保持对当前 schema 并非历史变更充分证明的限制说明。

## 下一步建议

1. 在实施 worktree 中修复以上四项，先补对应失败回归，再做最小修改；本次审计没有编辑生产代码。
2. 明确新增 `lineage_node_candidates` 与 ModelProtocolTraceEvent 两个字段的序列化兼容说明。本期模型仍使用 `p1.investigation.v1` / `p1.trace.v1`；默认空值让新读者能读旧记录，不自动保证旧的 extra=forbid 读者能读新记录。原计划要求的兼容边界应在实施交付中写清楚，不只写“六文件合同不变”。
3. 修复后复跑受影响单元/协议与旧产物读取回归。根据最终变更确定需要补跑的 integration/e2e，不重复真实模型，不重写旧 smoke 的 3/8 结果。
4. 补充实际文件 allowlist、RED/GREEN 和全套验证命令/结果，再进入精确范围提交与集成准备。当前主目录的原计划仍为待实施状态，实施树没有该计划副本；收尾应提供一份一致的执行记录。

本轮不建议增加预算、放宽 gap、继续拆分 kernel 或新开功能方向。先把已执行计划的边界和可观察性做正确。

## 修复后复审（2026-09-09，当前结论）

复审对象仍为 `p1-v8-smoke-followup-20260909` 的未提交工作区，HEAD 为 `f61bbfde04877eb4b54865db313647a04791db5f`。初审四项按以下证据关闭：

| 初审问题 | 当前修复与验证 |
|---|---|
| 未知字段进入 trace | `_safe_validation_details` 使用固定字段/错误类型白名单，未知键映射 `<unknown-field>`；新增完整 FunctionModel 用例证明未知键与值不进入序列化结果，另覆盖长键/嵌套键 |
| 错误定位滞后一轮 | 删除历史 retry 提取，改为对当前 response 的输出 payload 做只读本地校验；“前两次缺 summary、最后一次非法 status”回归得到 `status / literal_error`，不再沿用 `summary / missing`；无法提取详情时留空 |
| 首次请求没有 ledger | 移除空调查提前返回；本轮独立重放初始 prepare，canonical `seed.demo.raw_payments` 与预算/账本在首个工具描述中可见 |
| prompt 无条件不足 | 条件收窄为目标 schema 不可得或公开证据仍无法区分根因；明确仅缺 transformation definition 不自动否决确认，相关文本回归通过 |

本轮实际执行：

```powershell
uv run pytest tests/unit/test_diagnostic_agent.py tests/unit/test_kernel_auto_binding.py tests/unit/test_followup_serialization_compat.py tests/unit/test_probe_receipt_flows.py tests/unit/test_diagnostic_validation.py tests/unit/test_diagnostic_kernel.py -q
uv run ruff check .
git diff --check
```

结果：**92 passed in 12.46s**；Ruff 和 diff 检查通过。

独立历史兼容检查：只读加载原 `p1-v8-kernel-smoke-20260908` 的 8 个产物目录，以当前 TraceEnvelope 验证全部 **78 条 trace**，以当前 InvestigationState 验证 **8 个旧 kernel state**，全部可读。没有重写或重新评价旧样本。

兼容范围：已验证“新读者读旧记录”。新记录增加字段，旧版 `extra=forbid` 读者不保证可读；消费新产物时应使用包含本次改动的读者。六文件数量不变不等于字段完全不变。

当前未发现新的阻塞问题，可以进入精确文件范围的提交/集成准备。完整 unit、integration、e2e 未在此次复审重复运行，用户此前报告的全套通过不计为本轮独立结果；如需把最终修复后的完整验收标为完成，应核对该全套记录是否对应本次最终代码。复审不代表真实模型质量改善，原 smoke 仍为 3 PASS/5 FAIL。

本次只更新审计报告，没有修改实施树生产代码、提交或推送。
