# 规划器工具契约可见性：离线验证与修复设计

状态：设计稿，待审；本次仅交付设计，不授权实现、冻结或真实请求。

## 1. 问题与证据范围

基准代码为 `566b098`；事实记录见 [v2 暂停前缀报告](../reports/2026-09-29-t12-planner-compare-v2-preflight-and-paused-run.md)。108 格测量停在 29 格，规划器执行 10 格、整格通过 0 格。其 42 次 STEP 中，14 次为工具名拒绝、6 次为参数拒绝、5 次为计划拒绝预算耗尽；10 格均达到两次计划拒绝。该前缀不足以得出三策略总体优劣。

代码可确认的缺口：`TOOL_OBLIGATIONS` 与 `obligation_tool_schemas()` 已描述底层工具名称和参数，并进入政策身份；但 `EvidencePlannerRunner._user_prompt` 只提供 run、brief、关系/节点投影，两个动作工具注册时也没有附带底层工具目录。`plan_step.tool_name` 是 string，`arguments` 是 object。模型无法从当前接口完整读到这些工具的精确调用合同。

观测到的错误名包括 `relation_schema`、`get_relation_profile` 及不存在的转换/水位工具。目录缺失与这些拒绝相容，但不能证明其为 0/10 的唯一原因；状态、引用和缺口质量仍须独立考察。六次参数拒绝的完整请求不应从不足的历史 PLAN 投影中猜补。

## 2. 最小变更

向规划器首次任务输入增加 `evidence_tool_catalog`，来源为**本次 StrategySession 实际授权集合**。它是公开接口说明，不提供场景答案或建议采集顺序。继续保留两个动作工具加一个终态输出工具；不将底层工具注册为可绕过计划层的独立动作。

建议纯函数接口：`planner_tool_catalog(allowlist, surface)`。先验证授权工具均属于该 surface 的已知工具集合，再按工具名稳定排序并返回目录。未知授权名须在 agent 启动前显式失败，不能默默丢弃或回退六工具；空授权集返回空目录，不能触发 `obligations or TOOL_OBLIGATIONS` 的全量回退。

目录每条包含：`name`、简短公开用途 `description`、`input_schema`、`argument_notes`。名称、参数集合、string 类型、required、`additionalProperties=false` 由现有 `ToolObligationSpec` 与 schema builder 同源生成，禁止手抄第二套名称/签名。`argument_notes` 仅补充 schema 未表达的现有公开语义，须绑定身份并有来源核对。

v1 的规范签名如下；实际输入只包含本 run 获准的子集：

| 工具 | arguments 的精确键 |
| --- | --- |
| `get_dbt_run_results` | `run_id` |
| `get_dbt_node_error` | `run_id`, `node_id` |
| `get_dbt_lineage` | `node_id`, `direction` |
| `get_relation_schema` | `relation_name` |
| `get_relation_data_profile` | `relation_name` |
| `get_relation_history` | `relation_name` |

说明须明确：仅签名声明 `run_id` 时传入当前 run；节点和关系标识来自公开上下文/已获得证据；lineage 的方向遵循现有后端接受值。关系工具可用不代表所有关系可读，目录不扩展关系白名单；合法请求仍可能获得真实后端拒绝。工具目录不是证据，不能被引用或计入已采类型。

现有 v2 surface 同样由其已授权集合生成目录，额外两工具沿用当前批量参数编码、大小及目标边界，说明须从现有实现核实后写入；不能把 v2 工具带进 v1。此项只防止通用 runner 回归，不新增 T13 场景或测量。

目录放入一次初始 user payload，后续请求沿正常消息历史可见，不每回合重复插入。prompt 改为要求从目录选择精确名称与参数，不自行造别名；目录未列出的证据能力不能通过猜工具名获取。保持现有拒绝反馈，不新增自动纠错、别名映射或替模型选择取证步骤。

## 3. 校验与信息边界

`plan_step` 的 SDK 入参继续保持宽松；目录里的 JSON schema 是供模型阅读的底层调用说明。非法名称、缺参、多参、错误类型仍进入 PlannerController，产生原有 `PLAN_*` verdict，按原规则消耗计划拒绝预算；不得变成 SDK 门外错误或免计数拒绝。后端拒绝仍为真实 ToolReceipt，不能与 PLAN verdict 合并。

8/8/2/300、独立的计划拒绝/提交拒绝计数、SATISFIED/REVOKED、两道提交门、最终 evaluator、scenario 与恢复协议保持原行为。没有自动补采、关闭或提交。

生成器只接收公开工具授权及 surface，不接收 `ScenarioSpec`、期望状态、required_evidence_types、私有 gap 矩阵、case ID、基线真值或评分附件。运行时关系/节点值继续用既有公开投影，不从管理平面补全。目录排序、描述及例子不得随具体场景答案变化；本切片不添加带真实案例答案的 few-shot。

## 4. 身份与历史兼容

这是模型可见接口变更，采用新候选身份：prompt 建议 `p1.planner.v2`，controller 建议 `p1.planner_controller.v2`（实施前检查未占用）。controller 升版表示输入投影合同改变，不表示增加硬门。

`planner_controller_payload` 新增版本化的 catalog projection 合同：字段结构、最大授权 surface 下的完整目录内容（包括描述/参数说明）、授权过滤/排序规则，以及 user payload 中的字段名。运行时同一生成器按真实 allowlist 过滤；禁止身份绑定一份目录、模型却读另一份。动态 run_id、关系和节点值不进入政策摘要，仍由 run/场景身份绑定。空集合、v1/v2 的行为必须明确区分。

底层 `tool_schema_sha256` 在签名未变时可以保持不变；prompt/controller 摘要必须改变。修改描述、签名、参数说明、目录字段或过滤合同的变异测试必须使相应身份摘要变化。不要修改共享 schema builder 的旧输出来顺带影响 Kernel/Static 身份。

规划器声明增加自身的 `evidence_tool_catalog` 可见上下文标记，不修改所有策略共用的默认声明。报告须说明该信息显式披露方式和政策身份变化；不能凭旧 comparison_identity 相同宣称全条件相同。Kernel/Static 已通过 SDK 得到底层工具声明，但这不意味着修复后三者提示字节或附加拒绝预算相同。

历史清单、回执和归档保持字节不变。旧身份在冻结 checkout 复核；当前树政策已变时，不承诺旧清单能直接启动新运行。本设计不新增或冻结实验 ID。

## 5. 离线验收

全部使用 FunctionModel、合成工具后端和必要的离线 SDK 请求捕获，不调用模型端点、数据库或 dbt。

| 验收 | 可执行断言 |
| --- | --- |
| 基准缺口 | 在原实现捕获真实 runner 发给模型的 system/user 内容和注册工具定义，确认无法获得完整底层名称与参数集合；留存脱敏测试摘要，不输出私有上下文 |
| 目录准确性 | v1 六工具、v2 扩展、收窄集合、空集合分别与实际授权和参数校验契约相等；未知授权名构造失败；不出现额外工具 |
| 实际送达 | 经正常 `for_run → diagnose`，FunctionModel 首次请求从 messages 解析目录；后续请求仍能读到同一目录。断言目录内容与身份载荷生成源一致；仅检查 helper 输出不算通过 |
| 按目录调用 | 脚本从 messages 中解析名称和 required 参数后生成请求，不直接读取 TOOL_OBLIGATIONS、私有 case 或答案。覆盖六工具合法参数，取得真实测试后端收据；该脚本在旧输入上因无目录失败，在新输入上通过 |
| 参数边界 | 缺参、多参、类型错、外来 run 均维持现有码、预算与无后端调用语义；关系签名不额外添加 run_id。另测合法签名但后端拒绝，保留原真实拒绝码 |
| 名称边界 | `relation_schema`、`get_relation_profile`、不存在的能力仍被原码拒绝；同一脚本收到拒绝后从目录选正确名，在剩余预算内取得收据。只能称“纠正路径可执行” |
| 状态与计数 | 两次计划拒绝后第三次合法计划仍被挡，工具尝试不增加；提交通道与原规则一致。目录本身不计工具调用、证据或义务 |
| 隐私与授权 | 不同私有期望但相同公开输入产生相同目录；使用带私有哨兵的夹具检查实际模型请求；目录不能越过关系/节点边界 |
| 身份 | prompt/controller 明确升版；描述/签名/投影合同变异改变身份；Kernel/Static/其他策略完整政策身份逐字段不变 |
| probe 接线 | 规划器专用 probe 继续通过真实 runner 获取相同目录；离线模型验证 plan→receipt→submit 全链路。探针通过只证明兼容，不证明诊断能力 |

“从目录生成合法请求”属于确定性接口验收，不统计为原 10 格修复，也不重写历史失败码。历史六次参数错误若缺少可恢复的原始入参，只用明确标注的合成变体覆盖，不声称逐次重放。

## 6. 交付顺序和完成标准

1. 先补正常 runner 的模型输入捕获回归，确证旧输入缺目录；记录基准 SHA 与实际公开面。
2. 实现同源目录、任务输入接线、规划器专属 prompt/声明与政策身份更新，同步 requirements M20/§10.7；保持动作工具注册和校验语义。
3. 完成上述定向回归，运行规划器/controller/协议/身份/probe 相关单测；最终一次完整 unit、ruff、lock 和 diff 检查。无数据库、dbt 或归档写入链路改动时不重跑 integration/E2E；若实施触及这些路径，先说明扩展原因并按影响补验证。
4. 提交实施与验收报告，逐项列明证据、身份差异及未验证项。全部离线门通过，才交付为“工具契约完整送达的策略候选”。

完成并不等于真实模型调用错误下降或整格通过率提高。若后续批准测量，应另写预登记与新冻结身份，至少同时观察名称/参数拒绝率（各自以 STEP 提议为分母）、发生拒绝的 run 比例、实际取证覆盖、整格通过和 MODEL_ERROR；拒绝次数下降本身不构成成功。当前暂停前缀不能补填、重试至通过或作为完整对照组。
