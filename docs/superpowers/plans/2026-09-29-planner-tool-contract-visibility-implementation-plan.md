# 规划器工具契约可见性实施计划

状态：按设计稿编制，待实施；本次指令仅要求写计划。本计划不授权新冻结或真实模型执行。

设计依据：[工具契约可见性设计](../specs/2026-09-29-planner-tool-contract-visibility-design.md)。问题证据：[29 格暂停前缀](../reports/2026-09-29-t12-planner-compare-v2-preflight-and-paused-run.md)。计划基准为 `c0dd41b`；原测量实现基准为 `566b098`。实施开始时重新核对 HEAD 和工作树，不覆盖已有修改。

## 目标与完成边界

将本 run 获准的底层工具目录，通过正常 EvidencePlannerRunner 交给模型；目录、控制器工具签名与政策身份同源。以离线请求捕获和 FunctionModel 证明目录完整送达、可据此构造合法调用、原有拒绝与预算规则不变。

完成品是一个新规划器策略候选，不能登记为历史失败已修复或真实模型能力提高。保留 `p1-planner-compare-v2` 的暂停状态、清单、回执、ledger 与归档。本轮不运行 preflight、benchmark、freeze、real_model、数据库或 dbt，不 push。

## 文件范围

| 文件 | 责任 |
| --- | --- |
| `src/data_incident_gym/evidence_planner.py` | 同源工具目录生成器、版本化投影合同与身份绑定 |
| `src/data_incident_gym/planner_agent.py` | 从真实 session 授权生成目录，注入初始 user payload；规划器专属可见上下文声明 |
| `src/data_incident_gym/prompts/evidence_planner.md` | 指示查目录使用准确工具名与参数，说明目录不是证据或取证清单 |
| `src/data_incident_gym/diagnostic_agent.py` | 仅规划器 prompt 版本常量；不改变 Kernel/Static 路径 |
| `tests/unit/test_planner_tool_catalog.py`（新增） | 目录、模型实际输入、按目录调用、权限/隐私/身份变异回归 |
| 既有规划器、协议、身份、probe 单测 | 复用夹具并补必要接线断言，避免重复测试 |
| `docs/requirements.md` | 同步 M20 与 §10.7 的新输入合同及身份 |
| 新实施报告 | 逐项验收、身份差异、命令结果与能力结论边界 |

不扩展共享 StrategyDeclaration schema 或修改共享默认声明；规划器构造自己的声明值。若实际实现确需突破以上合同边界，先记录具体冲突供审查，不能借修复扩展任务。

## 步骤 1：固定基准与先失败回归

1. 记录 HEAD、工作树状态、当前各策略政策身份及 v1/v2 底层工具 schema。确认候选版本 `p1.planner.v2` / `p1.planner_controller.v2` 未占用；若占用，先查明用途，不覆盖身份。
2. 经正常 `EvidencePlannerRunner.for_run → diagnose` 装配 FunctionModel，捕获实际 system/user 消息与注册工具定义。只用公开合成输入与假后端，不打印真实 private bundle。
3. 写“首次请求有完整 `evidence_tool_catalog`”回归，在原实现确认失败；记录失败确因缺目录。最终保留新合同断言，不保留要求旧缺陷成立的测试。
4. 建立只从模型消息读目录的测试 director：按公开任务选择用途，再用目录解析名称/required 参数；不得导入控制器的 TOOL_OBLIGATIONS 作为答案，不读场景期望。

验收：失败原因和基准版本可复核；原实现只有动作工具的宽泛 schema，没有完整底层调用目录。此阶段不提交失败的中间代码。

## 步骤 2：实现同源目录与授权过滤

1. 在 `evidence_planner.py` 增加 `planner_tool_catalog(allowlist, surface)`，只接收公开授权和工具 surface。
2. 先校验 surface 与授权名，再按名称排序；空集合直接生成空目录。未知授权名在 agent 启动前显式失败，不吞掉、不回退全六工具，也不映射为一次模型计划拒绝。
3. 名称与 input_schema 来自现有 ToolObligationSpec/schema builder；新增静态 description/argument_notes 仅描述现有能力。逐条核对后端对 direction、run_id、v2 批量编码/上限的真实规则，记录来源文件/函数。
4. 保持共享 schema builder 的既有输出和行为；通过调用侧显式处理空集合，避免为本修复改变其它调用者语义。
5. 补 v1 全集、v2 全集、授权子集、空集、未知授权名与跨 surface 工具负例。验证签名与实际 session 参数校验集合相等；v1 不出现 v2 工具。

验收：目录恰等于获准集合，参数名/类型/required/闭合对象规则准确，描述和参数说明与现有后端一致；目录不含场景答案、节点或关系的管理平面补全。

## 步骤 3：模型接线、声明与身份一次落地

1. `_user_prompt` 接收从当前 session 得到的目录，追加一次 `evidence_tool_catalog`；不能根据 surface 独自假定全量权限。后续请求沿消息历史读取，避免反复注入。
2. prompt 使用中性的“本 run 工具目录”描述，明确名称/参数以目录为准、关系权限另受现有白名单约束、目录不是必须执行的清单。保持三个模型工具注册和宽松输入 schema。
3. 仅规划器声明增加 `evidence_tool_catalog` visible_context 标记。若测试通过注入 session 覆盖正常构造路径，也必须校验实际 session 授权而不是 runner 默认集合；不修改调用者提供的授权。
4. 升规划器 prompt/controller 版本；在 controller payload 绑定目录结构、全 surface 内容、描述、参数说明、字段名以及过滤/排序规则。运行输入和身份均调用同源构建逻辑。
5. 同步 requirements M20/§10.7，明确身份变化源于公开输入合同。v1/v2 是证据工具 surface，与规划器 prompt/controller 的 v2 版本名独立，不混写。

验收：实际请求中的目录等于按 session 授权过滤后的身份定义；prompt/controller 摘要改变，底层签名未改则其摘要保持；所有非规划器策略完整政策身份逐字段等于步骤 1 基准。旧冻结清单字节不变；不强求旧规划器身份在新源码上 verify 通过。

## 步骤 4：离线 runner 与边界验收

| 测试组 | 实施要求与断言 |
| --- | --- |
| 真实请求可见性 | 从 FunctionModel 收到的 messages 解析目录，首次及后续回合均可见且只有一次初始注入；注册动作仍为 plan/close，终态仍为 submit |
| 六工具可调用 | 参数化用例各走正常 runner，从目录构造合法请求并获得测试后端生成的收据；不把所有负例塞进同一 run 导致预算混淆 |
| 拒绝后查目录纠正 | 合成错误名先被 PLAN 拒绝，下一步从目录选准确名称，取得真实测试后端收据；断言拒绝次数 1、工具尝试只在执行时增加 |
| 参数/权限拒绝 | 缺参、多参、类型错、外来 run 维持原码；正确签名但不可读目标仍产生真实后端拒绝；不发生 SDK 提前拦截、补参或别名纠正 |
| 两次拒绝耗尽 | 两次 PLAN 拒绝后合法计划仍被挡，提交通道按原规则可用；目录不产生证据/收据/义务或额外调用计数 |
| 隐私 | 同公开输入、不同私有期望的目录相同；真实模型请求不含私有哨兵。生成器的生产调用不能接收或读取私有合同 |
| 身份变异 | 分别改描述、参数说明、签名、目录字段与过滤合同，验证对应摘要变化；同时检验模型收到内容与身份生成源一致 |
| 专用 probe | 使用现有 probe 的离线路径，经同一 runner 获得目录，完成 plan→receipt→submit；不向端点执行真实 probe |

历史 PLAN 未保留足够原始参数时，参数负例明确标作合成机制用例。改动失败码、弱化成功断言或放宽预算均不能用来使测试通过。

## 步骤 5：集中验证与提交

开发期间只跑受影响文件。接线稳定后执行一次相关批次，失败定位后只重跑受影响部分；收口时完整 unit 一次。

```powershell
uv run pytest tests/unit/test_planner_tool_catalog.py tests/unit/test_evidence_planner.py tests/unit/test_planner_runner.py tests/unit/test_strategy_adapter.py tests/unit/test_policy_fairness.py tests/unit/test_planner_probe.py tests/unit/test_planner_probe_receipt.py tests/unit/test_t13_v2_tool_surface.py tests/unit/test_planner_comparison_manifest.py -q
uv run pytest tests/unit -q
uv run ruff check .
uv lock --check
git diff --check
```

本切片只改变规划器公开输入与身份，不改数据库/dbt/产物读写，因此不重跑 integration/E2E。若执行中出现相关失败或不得不改这些链路，先据实际影响补验证方案；不能将未跑套件写成已验证。

建议两个提交：

1. `fix: expose the granted evidence tool contract to the planner`：目录、接线、prompt/身份、requirements 与回归放在同一可测试提交，避免出现“模型已见新目录但身份仍旧”的中间状态。
2. `docs: record planner tool catalog validation`：报告设计验收逐行映射、基准/新身份差异、测试结果与未验证项。

只暂存本任务文件，保留原有 README、环境示例、历史文档等用户改动；清理本任务不用的临时捕获文件。报告可放脱敏的目录/摘要，不提交密钥、真实评分附件或原始模型数据。

## 收口判据与下一停点

步骤 1–5 全部通过后，报告结论限于“准确的授权工具合同已送达模型，离线调用与拒绝路径通过，候选身份已版本化”。未经真实对照不得声称减少拒绝或提升整格通过。

停在新实验设计/冻结之前。后续如需真实测量，另行预登记样本、主指标与停止规则，绑定新实施修订；既有 29 格仅作历史描述，不能补全为新身份的对照。批准本计划的实施也不等于批准 preflight、freeze 或正式测量。
