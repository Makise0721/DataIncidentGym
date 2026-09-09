# p1-v8 Kernel smoke 后续改进计划

状态：待实施。当前轮次只交付分析报告、证据索引和本计划；不修改生产代码、不运行新模型样本。

## 目标与基线

解决 8-cell smoke 暴露的具体确认与工具协议问题，在保持既有预算和 strict gap 门禁的前提下，让确定性回归能拒绝证据不足的确认，并完成合法的缺失证据收尾。

证据来源为 `codex/p1-v8-kernel-smoke` / `21250df9d556eae85e56e638a7b255b351d823d5`；运行代码与 main `f61bbfde04877eb4b54865db313647a04791db5f` 相同。smoke worktree 仅作为只读证据来源。实施从重新核对过的 main 基线开展，不在已经冻结的 smoke worktree 改运行代码。

本计划依据同目录 reports 中 `2026-09-09-p1-v8-kernel-smoke-analysis.md` 及 evidence-index.json。原始 3 PASS/5 FAIL 不修改，历史 Manifest 不重新冻结。新回归通过不能写成历史样本已通过，也不能推出真实模型成功率改善。

## 范围与不变量

- 不放宽 CONFIRMED/NO_INCIDENT 的全部 gap 关闭门禁；不删除或伪装 BLOCKED gap。
- 保留模型对调查选择、假设、结论与引用的责任；controller 不从 case_id/expected_status/私有答案生成结论。
- 模型调用预算 8/8/2/300 不变；不新增真实模型请求、doctor 探针、正式 benchmark、Manifest 生成或推送。
- 保留领域验证器拆分、自动 gap ID、只读业务工具、evaluator 独立性和静态策略边界。
- 允许有明确证据依据的确认条件收紧与提示协议修订；这些是行为改进，不标为行为等价重构。
- 本期保持 evaluator 既有缺失证据收据规则。任何更改该规则的设计必须单列，不可为了让回归变绿而调整预期答案或判分。

## Task 1：seq59 的目标关系证据约束（优先级最高）

问题：ROOT_CAUSE claim 指向 raw_orders，却能用 raw_customers schema 加 raw_orders 聚合 profile 通过 schema 根因验证。

- [ ] 从 seq59 的公开 EvidenceRecord 和最终 KernelState 提取最小离线 fixture，原始产物只读；测试预期独立保存，不让规则读取 scenario 私有答案。
- [ ] 先建立旧实现错误接受的回归：相同 node error 和 upstream lineage、目标关系缺少 schema，但无关关系有 schema；期望拒绝，先确认 RED。
- [ ] 明确首期规则：对 SOURCE_SCHEMA_COLUMN_TYPE_CHANGED / SOURCE_SCHEMA_COLUMN_RENAMED 的 ROOT_CAUSE claim 要求显式 `relation_name`（已有字段），引用同一目标关系的 RelationSchemaFact，并绑定相关失败/上游路径；其他关系 schema 和通用 profile 不能代替该必要证据。
- [ ] 将上述新要求写入 requirements 和 prompt；更新相应正例 fixture 的目标关系字段，不静默推断或从 summary 解析关系名。
- [ ] 在 `diagnostic_validation.py` 增加窄范围内容/归属检查，沿用或明确记录失败原因；保留拒绝不提交终态的行为。
- [ ] 验证目标 schema 缺失、跨关系 schema、未引用的库存 schema、错 run/schema、正确目标 schema 的配对案例。正确目标 schema 只是必要条件，其他现有检查继续执行。
- [ ] 增加“source type 与 transformation cast 未区分”的说明与调查要求；本期不把“两个假设代码必须不同”作为全局修复，不建设通用 REFUTED 规则引擎。

完成判据：seq59 的原始证据与声明不能再通过 CONFIRMED；合格正例保持可确认，拒绝不改变状态。若没有公开基线或具体列映射，不能把当前类型存在当作历史发生了变更的充分证明。该限制需明确留档，不能声称此小修复完成所有 schema 根因或反驳语义验证。

涉及文件：`diagnostic_validation.py`、kernel 相关 unit、`diagnostic_agent.py`/prompt 的说明、requirements；仅有真实依赖需要时改 kernel 传参，不读私有配置。

## Task 2：血缘工具的 canonical node_id 候选

问题：kernel 将混合粒度的 incident subjects 全部当作节点来源，底层工具却只接受 dbt manifest 的精确 ID。

- [ ] 用 seq50/66/67 的公开 brief 与最小 manifest catalog 构造回归：`raw_payments` 是关系，`seed.jaffle_shop.raw_payments` 才是节点；`analytics.raw_payments` 也不是节点 ID。
- [ ] 由已验证的本 run manifest catalog 与已公开可证明节点求交，生成精确的 lineage candidates；只返回合法 ID，不输出 SQL、隐藏图结构或未证明的全部节点。
- [ ] 用同一候选来源驱动 kernel 的调用前验证、模型可见账本和失败提示。初始化的账本也应可见这些候选，不等已有 gap 后才展示。
- [ ] 非 canonical/未证明参数在创建 gap、业务 I/O 和成功调用记账之前拒绝，保持已有计数合同；不得将 relation name 静默改写后隐藏原请求。
- [ ] 候选列表只包含实际可调用节点；不拼接 `seed.jaffle_shop.` 猜 ID。catalog 缺失、同名不同资源和其他 dbt project 名称都需要拒绝或确定性筛选。
- [ ] 修复后仍测试一个真实工具失败导致 BLOCKED 的场景，确认门禁没有被顺带放宽。

完成判据：错误 relation ID 被明确拒绝、不会留下底层 NODE_NOT_FOUND 的 BLOCKED；模型随后选择公开 canonical ID 可正常采集血缘。不得声称 seq50 原始超时已经因此消失，原样本没有重跑。

涉及文件：`diagnostic_kernel.py`、`diagnostic_agent.py`、现有 EvidenceTools/run_context 的只读 catalog 适配、相关 unit/integration。NoLineage 不能通过新候选接口获取血缘结果；静态策略不得获得额外调查能力。

## Task 3：统一权限限制、缺失声明与 evaluator 收据

选择本期改动最小的路线：保留“真实记录一次权限拒绝”的现有合同，在模型提示中定义受限探针例外。暂不引入新的 policy-evidence schema 或改 evaluator。

- [ ] 确定性复现：白名单为空/目标不在该工具清单时，仅声明 RELATION_NOT_ALLOWED 会被 UNRESOLVED_EVIDENCE_UNBOUND 拒绝；真实权限拒绝后同声明可被接受。
- [ ] 区分正常数据查询与权限收据探针。正常查询仍严格按 provable_relations；只有已由 brief/已接受证据证明相关、且需要解释证据缺口的关系，允许对该工具发起一次边界探针。
- [ ] 该调用在 controller/kernel 的权限检查处拒绝，记录真实 tool/error/subject，计入原工具预算；以 spy 断言底层数据库方法调用次数为零。禁止扩大 allowlist、反复试变体或生成虚假成功证据。
- [ ] 修改 prompt、账本尾部与 RELATION_NOT_ALLOWED 重试说明，空候选也明确说明合法收尾路径。同步修订现有“绝不调用白名单外”的测试，仍保护普通调用规则。
- [ ] 增加 seq67 的 FunctionModel 流程：run results + schema + canonical lineage + 一次 profile 权限拒绝 + 两个精确缺失声明 → INSUFFICIENT_EVIDENCE；真实调用 evaluator 验证现有合同。
- [ ] 增加 seq44/59 型流程：公开失败、血缘、必要 profile/history、一次目标 schema 拒绝和 transformation definition 缺失声明；完整 fixture 满足既有证据类型要求，8 次工具预算内收尾。
- [ ] 模型从公开证据决定声明哪些缺失项，不能读取 expected unresolved_gaps 并自动替它填答案。未知 subject 或无依据的任意缺失声明仍拒绝。

完成判据：提示、kernel 与 evaluator 对同一受限调用和最终声明一致；不要求模型违反自己收到的指令才能通过；旧历史记录按原合同读取。以上 FunctionModel 输出为新构造协议回归，不是恢复出的 seq44/67 原始模型响应。

涉及文件：`diagnostic_agent.py`、kernel prompt、requirements、unit/integration/e2e fixtures；evaluator 生产规则不变。

## Task 4：输出结构错误的最小可诊断信息

问题：seq44/67 只留下 OUTPUT_SCHEMA_REJECTED，不能定位具体不合法字段。该任务用于补充未来故障可观察性，不以修复历史输出为验收标准。

- [ ] 检查当前 Pydantic/模型 SDK 错误在捕获点实际可用的信息，使用本地 FunctionModel 构造已知错误，不访问在线文档或调用真实模型。
- [ ] 在必要且安全的诊断位置保留字段路径、校验错误类型、验证阶段、关联请求序号；只保留白名单枚举/字段名，不记录原始输入值、异常 repr、API key 或请求 header。
- [ ] 优先采用已有补充诊断机制；若必须新增 trace 字段/schema，先在本任务文档列出版本与旧消费者兼容方案，再实现，不能静默修改六文件合同。
- [ ] 覆盖缺字段、错误 enum、跨字段不一致及修正后合法输出，证明错误分类正确、重试上限与 MODEL_ERROR 投影保持。

完成判据：新构造的结构失败可以定位到安全字段类别；历史 seq44/67 仍标为具体字段未知。若现有异常已丢失细节，报告不可恢复边界，不能从错误类别猜字段。

## 验证顺序与交付

单线执行 Task 1 → 2 → 3 → 4，按真实依赖推进，不同时改变领域门禁和 evaluator 来掩盖问题。每项先执行有意义的失败回归，再最小修复；只迁移相关 fixture，不批量改旧报告或测试预期。

实施前重核版本。当前 kernel prompt v9/controller v8，行为和 prompt 修改需使用新的未占用版本，policy identity/hash 自动计算。旧 v8 smoke/Manifest 不更新为新身份；不以此自动授权新样本。

完成范围内改动后执行：

```powershell
uv run ruff check .
uv run pytest tests/unit -q
uv run pytest tests/integration -q
uv run pytest tests/e2e -m 'not real_model' -q
uv lock --check
uv build
git diff --check
```

阶段内先运行受影响测试，全套完成后不无故重复。服务不可用时明确列出未验证项；不以增加超时、减少样本或跳过新失败获得通过。

交付应包括：精确文件 allowlist、RED/GREEN 证据、新协议的 FunctionModel 完整轨迹、真实 evaluator 检查、没有改动的门禁/预算和历史样本说明。准备提交范围，但不自动提交或推送。

## 本计划不承诺的结果

不承诺真实模型 pass rate、速度或 token 降幅；本次没有公平的旧协议对照。seq66 对语义重复与合法拆分的解释仍可能涉及公开领域语义不足，不能只修 lineage 就承诺确认成功。若以后要重新评估模型，需单独确定新 smoke 身份、固定样本与预算；当前只推进离线改进。
