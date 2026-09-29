# T12 planner-compare v2：401 排查与暂停前缀

状态：预检通过；一次正式 run 在 29/108 格后按预登记滚动窗口规则暂停。本文只描述已执行前缀，不构成 108 格完整比较或晋级结论。

## 身份与执行

- 执行检出：`566b0985d04ccd07720d0440b16eddab8c5b8f70`，独立 worktree 始终干净。
- 冻结清单：`p1-planner-compare-v2.json`，SHA-256 `ea05a5e3862a55ea589e024f7e15adc0e224a52a500a72599ee755c6c5765765`；赛程 108 格，均为 model-backed。
- 仅修正本机未跟踪启动器的进程环境组装：六个诊断数据库配置键继续从主工作区 `.env.diagnostic` 读取；模型键从 Process/User `COMMANDCODE_API_KEY` 显式映射到运行时实际读取的 `DIG_DIAGNOSTIC_MODEL_API_KEY`。原启动器使用 `.env.diagnostic` 中另一个模型键，其值与可用的 CommandCode 键不同。密钥值未记录或提交；产品源码、清单和执行检出未改。
- 使用本机固定的 `uv 0.11.24`。一次修正后的 preflight 为 **PASSED，13/13 检查通过**；模型结构化工具探针及规划器专用探针均通过。doctor 回执 SHA-256：`8ade3e891455c1a95c56be274fe3f5ca737ed1ab8ee396f101ccf29d013a83a8`。
- 先前第 4 次失败 preflight 目录原样移至同一 `artifacts/benchmarks` 下的 `p1-planner-compare-v2.preflight-4.FAILED`，移动前后 `doctor.json` SHA-256 均为 `75e417e8bfcd9268ca3fb8593a9d240e2936630a94b6e67ceaf48d3c76bebaab`。失败回执 3、4 中的模型探针为 HTTP 401；它们与这次通过回执没有混写。

## 一次正式 run 的停点

正式 run 从 seq 1 启动，在 seq 29 写入终态后、seq 30 启动前返回 `ROLLING_WINDOW_UNPASSED_PAUSE`。ledger 恰有 58 行，即 29 条 STARTED 与 29 条终态；seq 1–29 为 **10 COMPLETED / 19 FAILED**。窗口尾 seq 18–29 为 **2 COMPLETED / 10 FAILED**，达到预登记阈值。`stop_reason` 与 ledger 一致；未续跑、未覆盖、未新冻结。

对 29 格逐一按冻结清单核对 ledger 身份与顺序，使用 `PlannerComparisonReporter._load_cell` 严格重载六文件和评分输入、核对摘要/元数据/恢复身份，并以冻结 evaluator 逐格重算：**29/29 通过**。29 格均未出现环境门或 controller 内部错误；运行后数据库基线指纹仍为 F0 `e5c7848eb2b7af16ec37463650b63dbb5d1f52293adf456003517b51c496cb18`，执行 worktree 保持干净。ledger SHA-256：`9ad3aaf611002c56a4e152f7ffddd18c13f803038e890e266f5a62e3aebefa23`。

归档 trace 中未发现传输诊断，尤其无 HTTP 401。两条 `MODEL_PROTOCOL` 事件分别发生在 seq 6 与 12，均为输出 schema 拒绝，非传输错误。前缀合计 119 次已计量模型请求、958,824 输入 token、270,914 输出 token。此证据足以证明修正后的端点调用在本次预检与已执行前缀中工作，不能保证未来请求永远不遇到 401。

## 前缀画像与限制

| 策略 | 已执行 | 整格通过 |
| --- | ---: | ---: |
| EVIDENCE_PLANNER | 10 | 0 |
| DIAGNOSTIC_KERNEL | 10 | 6 |
| STATIC_SKILL | 9 | 4 |

这些是**暂停前缀的描述值**，不是完整、平衡的三策略比较。`experiment partial` 也报告 0/36 个完整重复组，`pass^k` 全为不适用。

在这 10 个规划器格中，PLAN 轨迹有 42 次 STEP 提议：17 次接受、25 次计划层拒绝；拒绝为 `PLAN_TOOL_NOT_ALLOWLISTED` 14 次、`PLAN_ARGUMENTS_INVALID` 6 次、拒绝预算耗尽 5 次。另有 7 次 CLOSE，其中 2 次被拒绝预算挡下。每个规划器格都至少经历一次计划层拒绝；部分错误名称包括 `relation_schema`、`get_relation_profile`、`get_transformation_definition`。归档据此证明模型在计划入口频繁使用了不在授权集合中的工具名或错误参数，不能仅从这一前缀推断模型总体能力。

模型可见的规划器 prompt 只说“六个只读证据工具”，`_user_prompt` 只投影 brief、可见关系和可选节点；模型实际注册的动作工具是 `plan_step` 与 `close_obligation`，其中 `plan_step` 的 `tool_name` 是自由字符串、`arguments` 是宽泛对象。六个底层工具的精确名称与参数集合定义在控制器的 `TOOL_OBLIGATIONS`，当前并未作为列表投影进 `_user_prompt`。这是值得离线验证的接口假设：显式公开本 run 的合法工具名与参数契约是否减少计划层拒绝。它尚未经过对照实验，不能写作本轮失败的唯一原因，也不能在冻结身份内临时修改。

## 后续边界

本次任务到滚动暂停为止；保留 doctor、ledger、归档与评分输入。下一步先对上述工具契约可见性做离线设计与脚本回归；任何 prompt/输入面修改都会改变策略身份，须另行登记与冻结。不得把未执行的 seq 30–108 计作失败，也不得拿此不完整前缀宣称规划器与其他策略的正式优劣。
