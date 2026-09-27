# Kernel 的 profile 结论提交前 schema 核对

日期：2026-09-27。状态：已批准实施（owner 2026-09-27 指示「设计稿已写好……你来推进」；实现见 `2026-09-27-kernel-profile-schema-check-implementation.md`，提交 `7ca169c`）。冻结与真实模型测量仍需另行授权，本稿不授权后两者。

本轮只提出 prompt 策略改动。实现、冻结与真实模型测量分别验收；本稿不授权后两者。

## 1. 问题与证据范围

v32 在 74/106 格暂停，属于部分结果。本设计只针对其中五个 Kernel 格：

| seq | 场景 | 工具尝试 | 唯一失败检查 |
| --- | --- | --- | --- |
| 14、29、70 | orphan_payment_coupon_a | 各 4 | REQUIRED_EVIDENCE_TYPES_PRESENT |
| 25、66 | duplicate_payment_coupon_a | 各 3 | REQUIRED_EVIDENCE_TYPES_PRESENT |

这五格已有 profile 与 lineage；孤儿格还有 order history，但没有 schema。其余评测检查通过，工具上限为 8。这说明存在有余量而未采集的具体形态，**尚未证明补一条 schema 就能通过**：新事实、新引用和额外模型回合仍可能改变结论。

证据定位：v32 manifest 摘要 `898747c0d17edf207095ca789b0fc04ea950484b94dc1a2ffc04124dd7f5f992`，执行 checkout `cc1e181`；本地 `v32-exec-worktree/artifacts/benchmarks/p1-formal-v32/ledger.jsonl` 中相应 sequence 绑定 run，再以该 run 的严格加载 bundle 核对。测试实现时输出 sequence → run_id → 已采类型 → 失败码清单，不将私有 bundle 入库。

v32 全部失败不能作为本改动的效果分母。Static 的协议失败、传输失败、工具耗尽，以及 Kernel 的缺口矩阵失配另行处理。

## 2. 选择：收窄现有提示，不新增控制器机制

当前实现已有 `_uncollected_relations` 和每回合的 `CURRENT INVESTIGATION LEDGER`：按工具公开白名单减去同类型已采记录。当前 prompt 也已要求检查决定性证据，但将未采集列表明确限定为事实清单。

因此不再添加一份“待办清单”，也不新增提交拒绝、隐藏自动取证、额外重试或状态字段。改进点是把泛化的自检具体化为：**准备依据 profile 确认一个关系上的异常时，优先核对该关系的列结构上下文**。

这是有意选择的诊断策略偏好，不是从 schema 推出的充分因果证明，也不是私有评分合同的公开副本。schema 只包含当前列名、类型、nullable 和位置；它不能证明历史变更、业务唯一性、事件身份、外键约束或孤儿永久性。即使 schema 完整，决定性 profile/history/lineage 仍必须成立。

## 3. 模型可见规则

规则作用于 Kernel 家族的共享 prompt；不对 Static、NO_TOOL、REFERENCE_ANALYST 或 EVIDENCE_PLANNER 增加规则。

1. 触发点是模型准备依据一个**已接受的 relation profile** 提交 CONFIRMED，且 profile 所属关系就是拟确认根因的关系。关系取自公开事实，不从 case ID、mutation、预期根因或评分失败码推导。
2. 已有该关系的 schema 时复用，不再次调用。其它关系的 schema 不替代它；仅采集过该关系的 profile 也不等于已有 schema。
3. 若 schema 工具启用、该关系在工具自己的 `provable_relations` 内、尚未有同参调用或拒绝，并且保留决定性取证与最终提交后仍有预算，优先调用一次 `get_relation_schema`。只核对拟诊断的关系，不扫描所有已 profile 的关系，更不扫描白名单。
4. 已知决定性采集优先于此上下文核对。若还需 history、lineage 或必要边界见证，先为它们保留预算；不能为了补 schema 挤掉它们。仅剩最后一个模型请求时，不新增此补采批次。
5. 工具禁用、目标不可读、已调用失败/已存在指纹，或预算不足时，沿用已有公开证据与终态规则。此策略本身不要求边界探针，不自动生成缺口，也不强制弃答。若另有真正决定性的 schema 缺口，仍按既有边界规则处理。
6. 取证失败不能当成成功采集；实际调用产生的 gap/拒绝收据由既有内核处理，本策略不能撤销它们或绕过 finalize。
7. schema 与 profile 中被使用的列不一致时，不能凭猜测修正字段或继续沿用原推理；重新判断公开证据是否足够。没有可信健康基线时，当前类型不能被解释为“类型发生变化”。
8. 各 claim 仍只引用真正支撑自己的证据。不能为满足类型覆盖，把 schema ID 塞入所有 claim；尤其不得破坏已有的精确引用数量约束。**采集完整性不等于逐 claim 引用完整性。**

第 3、4 条是模型需要执行的规划指令，不是新增的确定性调度器。控制器不会保证模型遵守；必须在后续真实测量中观察其执行率与副作用。

### 建议插入的英文提示

放在现有“Before a final decision”段之前，保留原有权限、预算、引用及边界规则：

> Before confirming an anomaly from an accepted relation profile, check whether the same relation's schema has been observed. Use the relation named by that profile and the proposed root cause, not every relation mentioned in the incident. Reuse an accepted schema. If it is missing, prefer one schema check when the tool is enabled, the relation is allowed for that tool, no identical call or refusal has been recorded, and the remaining budget covers both this check and the still-needed decisive evidence plus the final decision. Do not spend the last model request on this corroboration. Do not sweep relations or displace decisive profile, history or lineage checks. A disabled or unavailable schema is not, by itself, a reason to probe or abstain. Existing gap and receipt rules still apply to any call actually made. A schema describes current columns and types; it does not prove uniqueness, foreign-key validity, event identity, permanence or a historical change. Reconsider inconsistent column evidence, and cite each record only in claims it actually supports, preserving each claim's citation constraints.

## 4. 身份与改动边界

拟修改 `src/data_incident_gym/prompts/diagnostic_kernel.md` 与 `KERNEL_PROMPT_VERSION`，从 `p1.kernel.v18` 升为 `p1.kernel.v19`（实施前重新核对版本未被占用）。三个 Kernel 策略使用共享 prompt，所以 prompt 摘要与政策身份一起变化；NO_SCHEMA 消融仍不能调用 schema，NO_LINEAGE 的权限仍不变。

controller 仍为 `p1.controller.v22`；其载荷、工具/输出 schema、账本结构、evaluator v5、归档版本、8/8/2/300 预算与滚动暂停规则均不改。Static 等非 Kernel 策略身份必须逐字段不变。不得为了使旧 manifest 在新代码上验证通过而更新旧文件；旧身份的复现仍使用其冻结 checkout。

需求文档新增“策略提示修订”的记录，明确这是偏好而非硬门。实施若发现必须修改 controller/工具/证据 schema 才能完成，应先提交设计变更，不把它混入本切片。

## 5. 离线验收：机制与效果分开

### 5.1 入库的合成边界用例

通过真实 `DiagnosisRunner.for_run` 和 FunctionModel 驱动现有 Kernel 路径，不另造控制器：

| 公开输入形态 | 脚本动作与应验证结果 |
| --- | --- |
| 同关系 profile 已有、schema 允许且未采、预算充足 | 原入口能补采 schema，回执进入同一会话，下一回合账本移除该 schema 未采项 |
| 同关系 schema 已有；其它关系 schema 已有 | 前者复用；后者不冒充目标关系，账本仍保留目标未采项 |
| NO_SCHEMA 或该工具关系不可读 | 脚本不补采，不自动创建 gap；既有终态规则不被新提示变成强制 schema 门 |
| 同参已有失败/阻断 | 复用真实记录，不重复探测；不得造“已采集”证据 |
| 预算只够决定性 history 与提交，或只剩最后模型请求 | 脚本跳过上下文补采；预算上限与已有失败码不变 |
| 干扰关系有 profile，真正诊断关系不同 | 不扫描干扰关系，不根据 case 名选择目标 |
| schema 与 profile 涉及列不一致 | 测试脚本不把当前 schema 误当健康基线或变化证明 |

FunctionModel 测试只证明约定路径可执行、权限/计数/归档正确，**不能证明真实模型读懂新提示或会作这些选择**。不得把脚本按新规则行动当成策略收益。

身份回归比较 Kernel 家族和非 Kernel 策略的完整 policy surface：只有 Kernel prompt 版本/摘要允许改变；其余字段逐项一致。不要只断言新提示包含几个关键词。

### 5.2 五格本地归档分析

只读严格加载并复算 v32 五格，确认缺采形态和剩余预算。旧 trace 未发生的 schema 调用不能凭空重放成功；不得从私有合同、健康基线或其它 run 拼一条 schema 塞进旧 bundle。没有新的合法取证时，反事实结果只能写“未验证”，不能登记为五格修复。

这些案例是已暴露的 dev 回归材料，不是 holdout。目标关系改名、干扰关系置换的合成用例用于检查是否按公开事实选目标，也不冒称真实模型泛化验证。

### 5.3 验证规模

实施先跑受影响的 prompt/身份/Kernel runner 定向回归及 ruff、diff 检查；收口统一跑单测。纯 prompt 与版本变更不触达数据库或归档写入，默认不重跑约两小时的全 integration/E2E。若定向路径暴露跨层问题，再按实际修复影响扩展验证。所有既有失败如实记录，不为通过本方案修改 evaluator 或答案断言。

## 6. 后续真实验收提案（本稿不授权执行）

离线验收通过后才提出独立的配对测量计划：固定模型、端点、场景、预算与其他合同，以旧/新 Kernel prompt 为对照，登记重复次数、执行顺序、额度与停止条件，再冻结新身份。不能拿不同停止长度的 v31/v32 前缀当作单变量对照。

主要结果仍是整格通过，不以“多采了 schema”代替成功。至少分列：

- 目标五格所属两类 dev 场景的成功/失败与精确分母；每格 schema 是否采集、是否还有原失败码。
- 其它场景的退化，尤其 history 被挤掉、超预算、过度弃答和多余查询。
- 工具调用、模型请求、token 与用时；补采成功但最终仍失败的格单列。
- schema 核对机会：首次满足“已接受目标 profile、同关系 schema 未采、允许调用且未尝试”的前缀；只剩一个模型请求或零工具预算的机会单列，不算执行遗漏。每 run 首次机会计一次；未来决定性取证是否有余量无法自动确定，人工按公开前缀复核，不伪称精确分母。

若只是证据类型检查变好，但整格通过未改善或预算失败增加，则记录负面/混合结论，停止扩大此规则。新冻结编号、重复次数和真实执行预算留给该测量计划，不在此预先占用。

## 7. 本切片完成定义

设计批准后实施的完成条件是：提示与版本更新、身份差异核验、上述定向回归、需求记录与实施报告齐备。它只完成一个可测的策略候选；“修复五格”“提升模型能力”“可以继续 v32”均不属于离线交付结论。
