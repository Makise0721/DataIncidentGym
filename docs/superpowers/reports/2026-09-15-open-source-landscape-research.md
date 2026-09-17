# 开源同类项目调研报告：事故诊断 Agent 的评测设计与数据栈可观测性

- 日期: 2026-09-15
- 状态: 外部调研记录。非权威合同文档，不改变 `docs/requirements.md` 的效力；建议条目供后续
  plan 取用。
- 范围: GitHub 开源项目（以英文生态为主）。覆盖四类：AIOps/RCA 基准与诊断 Agent、LLM 评测
  harness 纪律、证据/引用/弃答评测、dbt 数据栈可观测性与 MCP。
- 验证标注:
  - 【核对】本报告作者直接抓取仓库页 / 论文页确认过关键论断；
  - 【转述】并行调研 pass 抓取页面后整理，未逐条复核；
  - 【未验证】无法确认，已在附录列出。

## 1. 摘要

四路调研对照后，值得学习的内容集中在四条：

1. **评分与运行解耦**：Inspect AI 用 `inspect score <log>` 对存档日志重打分，SWE-bench 用
   `swebench report <run_id>` 无容器重判——评测器修复与升级不需要重跑模型。这是本项目当前
   最明显的方法论缺口。
2. **把弃答与可靠性做成统计指标**：τ-bench 的 pass^k 衡量多次重复的稳定性；AbstentionBench
   与 SQuAD 2.0 把"该弃答时弃答"与"看起来可答的对抗样本"做成第一类指标。本项目已有 A/B
   配对与终态门禁，但缺"过度弃答率"与重复试验维度。
3. **参考解（oracle）基线**：AIOpsLab、OpenRCA、RCAEval 都随仓库附带基线解法。本项目只有
   FIXED_RULE（支付场景规则对照），缺一个"只用公开证据走预期路径"的参考解来证明场景可解、
   并把"模型失败"与"场景设计失败"分开。
4. **六个工具开放成 MCP 入口**：dbt-mcp 的实践（只暴露 tools、按组环境变量门控、明确标注可写
   工具的风险）是现成范式；把只读工具包成 MCP server 后，第三方 agent 可直接作为策略被试。

同时确认了几个本项目已采用、且外部有对应先例的方向（无需改动）：冻结 manifest 哈希身份
（≈ HELM RunSpec / SWE-bench 缓存键）、批次无效封存（≈ Inspect 的 log status 纪律）、
A/B 配对与"证据不足给满分"（≈ SQuAD 2.0 / AbstentionBench / τ-bench）、终态等价评分
（≈ τ-bench 数据库终态比对）、只读不变量（≈ AgentDojo 的 tool_filter 思路）。

## 2. 调研方法与验证说明

- 四路并行调研，各自完成检索并抓取仓库页/README/文档；随后由本报告作者对影响结论的条目做
  定向复核（AIOpsLab、Inspect AI、AbstentionBench、dbt-mcp、τ-bench 论文、dbt-mcp `.env.example`）。
- 标注为【转述】的内容来自调研 pass 的抓取记录（含文件路径与页面引用），未逐条复核；引用前
  建议按"9.2 参考链接"回源确认。
- 对本项目现状的判断基于本地代码核对（kernel、evaluator、benchmark runner、场景合同），
  不一致之处以代码为准。

## 3. AIOps / RCA 基准与诊断 Agent

### 3.1 AIOpsLab（Microsoft）【核对】

- 仓库: https://github.com/microsoft/AIOpsLab
- 定位: 微服务环境下"部署—注入故障—生成负载—导出遥测"的 AIOps 智能体框架，附带基准套件。
- 机制:
  - 问题由 Application / Task / Fault / Workload / Evaluator 五部分组成，注册到 orchestrator
    的 registry。
  - Agent 契约是薄适配器：包装成类并实现 `async def get_action(self, state: str) -> str`，
    然后 `orch.register_agent(agent)`。
  - 故障注入按问题实现（`inject_fault`），内置 app / virtual 注入器，允许自定义。
  - 评测器签名 `eval(self, soln, trace, duration)`——**完整工具轨迹参与评分**；评测器分
    quantitative / qualitative 两类。
  - 自带 baseline agent（GPT-4）。
- 可借鉴:
  - 轨迹进评分：与我们的 trace 检查同源，验证了"过程也要判"的方向。
  - Agent/环境解耦：策略矩阵（kernel / static / fixed_rule）与它的注册式适配器同构。
  - 故障注入器的可扩展结构，可作为未来扩展注入目录时的参考。

### 3.2 OpenRCA（Microsoft, ICLR'25）【转述】

- 仓库: https://github.com/microsoft/OpenRCA
- 定位: Telecom / Bank / Market 三系统的 LLM 根因分析基准。
- 机制:
  - 数据布局 `dataset/{SYSTEM}/telemetry/{DATE}/{log,metric,trace}`，tasks 在 `query.csv`，
    真值在 `record.csv`；预测为含根因时间/组件/原因的 JSON，用 `main.evaluate` 出报告。
  - 附带多套基线脚本（standard / balanced / oracle 命名），pinned 模型配置 `api_config.yaml`。
  - 明确要求自定义 agent 申报所用工具/MCP，且禁止读取 `record.csv`（防真值泄漏）。
- 可借鉴:
  - oracle 基线的存在形态（我们缺，见建议 P1）。
  - "第三方 agent 申报工具、真值文件禁区"的审查条款，与我们 `forbidden_leakage` 是同类问题的
    更完整版本。
  - 环境因素（如时区）影响结论的教训，对应我们 run-scope 与 trace 纪律。

### 3.3 ITBench（IBM）【转述】

- 仓库: https://github.com/itbench-hub/ITBench
- 定位: IT 自动化（SRE / CISO / FinOps）开源基准框架，Kubernetes 环境"一键部署"。
- 机制: 提供 6 个 SRE 场景与 21 个机制；基线 agent 基于 CrewAI；评分经由托管 leaderboard，
  README 只描述为 "interpretable metrics"，具体指标与真值格式未验证。
- 可借鉴: 场景/机制目录化的组织方式；"一键部署"的复现理念与本项目 `pipeline build` +
  `eval run` 闭环同向。

### 3.4 RCAEval【转述】

- 仓库: https://github.com/phamquiluan/RCAEval
- 定位: 微服务 RCA 基准：9 数据集、735 个失败用例（Online Boutique / Sock Shop / Train Ticket），
  内置 15 个基线方法。
- 机制: 用例带 `metrics.json`、`inject_time.txt`、可选 `logs.csv`/`traces.csv`，标注根因服务与
  指标；`python main.py --method ... --dataset ...` 运行；指标 Avg@5，可选 `--report-chance`
  输出 Chance@5 / Lift@5。
- 可借鉴: 大批量基线矩阵的对照文化；chance-corrected 指标（对本项目意义有限——每格可接受根因
  集合很小，猜测空间不大）。

### 3.5 HolmesGPT（Robusta）【转述】

- 仓库: https://github.com/robusta-dev/holmesgpt
- 定位: 生产事故调查 agent，工具集覆盖 Kubernetes / Prometheus / Grafana / 云厂商 / Jira，
  支持 MCP 集成与告警入口（AlertManager / PagerDuty / OpsGenie）。
- 机制: 仓库内评测 harness（`tests/llm/test_ask_holmes.py`）从 fixtures 加载用例；用例字段含
  `user_prompt`、`expected_output`、correctness 严格度、`forbidden_tools`、`max_tokens`；
  通过要求 correctness==1；"回放正确性"单独判定；有评测报告生成脚本。
- 可借鉴: 用例级 `forbidden_tools` 与严格度字段；首轮正确性与回放正确性分开判——都是第三方
  agent 接入时值得抄的合同字段。

### 3.6 k8sgpt【转述】

- 仓库: https://github.com/k8sgpt-ai/k8sgpt
- 定位: Kubernetes 扫描诊断工具（8.2k stars，Apache-2.0）。
- 机制: analyzer 模式把 SRE 规则代码化（pod / pvc / event / node / ingress 等）；自定义
  analyzer 以 protobuf/gRPC 服务接入；据 README 有 MCP 模式（12 tools / 3 resources /
  3 prompts）；未发现评测 harness。
- 可借鉴: "确定性检测器 + LLM 解释"的分层（与本项目 FIXED_RULE 策略同构）；工具/分析器
  schema 版本化、按场景筛选分析器的做法。

## 4. 评测 harness 纪律

### 4.1 τ-bench / τ2-bench（Sierra）【核对（pass^k、终态比对）；其余【转述】】

- 仓库: https://github.com/sierra-research/tau2-bench ；论文: https://arxiv.org/abs/2406.12045
- 定位: 工具—用户交互 agent 基准（airline / retail / telecom）。
- 机制:
  - 论文提出 pass^k 评估"多次独立重复的可靠性"（pass^1 为默认主指标）。
  - 评测比对**数据库终态与标注目标状态**，不要求动作序列一致（"过程不同、终态对即通过"）。
  - 【转述】pass^k 实现为 `comb(success, k) / comb(trials, k)`；`evaluation_criteria.actions`
    只是一条参考轨迹，会被重放到全新 gold 环境推导目标末态再按 hash 比对；奖励门控
    `reward_basis`（如 DB×COMMUNICATE）；存在 `env_assertions`；文档示例中"无法完成的请求"
    的正确行为是礼貌拒绝、同样计满分。
- 可借鉴:
  - 重复试验维度与 pass^k 报告（建议 P2）。
  - "参考轨迹只用于推导目标态"——本项目的 `lab_verifier` 扮演相近角色，可以显式化这一表述。
  - 正确拒答计满分：与本项目 INSUFFICIENT_EVIDENCE 路径的合同一致。

### 4.2 Inspect AI（UK AISI）【核对】

- 仓库: https://github.com/UKGovernmentBEIS/inspect_ai ；文档: https://inspect.aisi.org.uk/
- 定位: 通用 LLM 评测框架。
- 机制:
  - solver（产生输出）与 scorer（对输出判分）分离；支持 `--no-score` 先跑后判、
    `inspect score <log>` 对存档日志重打分（offline scoring workflow），`edit_score` 自动重算
    聚合指标并留下 ScoreEditEvent。
  - `EvalLog` 带 status（started / success / error），文档要求"分析结果前先检查 status"。
  - 沙箱内置 local / docker；审批策略可链式组合（human / auto），所有工具调用必须被处理；
    限额（token / message / cost / turn / working / container）集中在 run / handoff / solver
    级声明。
- 可借鉴:
  - **离线重判**（建议 P0）：evaluator 修复不重跑模型，评测器 bug 不污染历史批次。
  - "先查 status 再分析"：与我们 INVALID_HARNESS 的封批纪律互补。
  - 限额集中声明 + 每次交互留痕：可作为 kernel 预算配置的组织参考。

### 4.3 SWE-bench（Princeton NLP）【转述】

- 仓库: https://github.com/SWE-bench/SWE-bench ；论文: https://arxiv.org/abs/2310.06770
- 定位: 真实 GitHub issue 修复基准。
- 机制: `FAIL_TO_PASS` / `PASS_TO_PASS` 测试集合判定（全部通过才算解决）；每实例独立 Docker
  镜像固定在 base commit；缓存键仅为 `run_id` + `instance_id`；`swebench report <run_id>` 支持
  无容器重判；SWE-bench Verified 为 500 题人工复核子集。
- 可借鉴: 无容器重判与发布时间线（P0 同源）；"人工复核子集"概念——可用于标记已人工审计的
  场景/批次；缓存键纪律（不同 run_id 不复用产物）。

### 4.4 HELM（Stanford CRFM）【转述】

- 仓库: https://github.com/stanford-crfm/helm
- 定位: 基础模型整体评测。
- 机制: `RunSpec` 为 frozen dataclass（`name` 即输出目录身份，含 scenario_spec /
  adapter_spec / metric_specs）；run entry 经 expander 展开；复现按版本固定
  `run_entries_*.conf` + `schema_*.yaml` + trials 参数；支持实例缓存。
- 可借鉴: "冻结配置即身份"与本项目 manifest 哈希同思路；trials 作为一等配置维度（支撑 P2）。

### 4.5 METR Task Standard / HCAST【转述】

- 仓库: https://github.com/METR/task-standard ；https://github.com/METR/hcast-public
- 定位: 通用任务打包标准与智能体任务集。
- 机制: TaskFamily 目录 + Dockerfile + 可选 `manifest.yaml`；driver 将目录实例化为容器/VM，
  可声明禁用网络；评分约定 `score(t, submission) -> float | None`，可基于提交字符串与环境
  状态；`score` 与 `intermediate_score` 互斥；HCAST 请求任务不要进入训练数据，部分资产用
  DVC 存放以保护解法。
- 可借鉴: 跨 harness 复用的任务打包标准化；"解法保护/防污染"的发布纪律（本项目以私有
  `.dig` 快照与 forbidden_leakage 达到类似效果）。

### 4.6 pydantic-ai / pydantic-evals【转述】

- 仓库: https://github.com/pydantic/pydantic-ai ；文档: https://pydantic.dev/docs/ai/evals/
- 定位: 本项目正在使用的 agent 框架；配套评测库。
- 机制: `TestModel`（无 AI，按工具 JSON schema 生成合法调用）；`FunctionModel`（自定义
  callable 替换 LLM，可精确控制每轮工具入参）；`Agent.override`（pytest fixture 级替换
  model/deps/toolsets）；`capture_run_messages()` 捕获消息以断言工具调用与参数；
  pydantic-evals 提供 Dataset / Case / Evaluator / EvaluationReport。
- 可借鉴: 无模型回放测试（建议 P3）——把真实失败 trace（如 `INSUFFICIENCY_GAP_DECLARED` 的
  B 场景）固化成确定性测试，断言工具序列与预算计数。

## 5. 证据、引用与弃答评测

### 5.1 GAIA【转述】

- 资源: https://huggingface.co/datasets/gaia-benchmark/GAIA ；论文: https://arxiv.org/abs/2311.12983
- 定位: 通用助手基准，466 题（3 个难度级），人类基线约 92%。
- 机制: 题目要求"只有一个正确答案"，两名标注者独立作答、双一致才进入正式集；dev 166 题
  公开、300 题答案保留；评分是 quasi exact match；**明确不评 trace**（论文与提交表单均不要求
  过程证明）。
- 可借鉴: 出题纪律（答案唯一性、多人核验）；以及差异点——GAIA 不评过程，而本项目把证据引用
  当硬门禁，这是相对优势，值得在文档与面试叙述中显式对照。

### 5.2 ALCE【转述】

- 仓库: https://github.com/princeton-nlp/ALCE ；论文: https://arxiv.org/abs/2305.14627
- 定位: 首个带引用的自动评测 benchmark（ASQA / QAMPARI / ELI5）。
- 机制: 按陈述切分；Citation Recall 要求每条陈述至少一条引用且引用内容蕴含陈述；Citation
  Precision 把引用判"无关"的判据是"删掉这条引用后，其余引用仍蕴含该陈述"，因此**冗余引用
  不重罚**；用人类标注衡量 NLI 判定的一致性。
- 可借鉴: 硬门禁之外补软指标（建议 P3）——引用必要率 / 覆盖 recall，用于观察"堆引用"或
  "引不够"的趋势，而不改变现有合同。

### 5.3 AbstentionBench【核对（规模/场景/judge）；指标细节【转述】】

- 仓库: https://github.com/facebookresearch/AbstentionBench ；论文: https://arxiv.org/abs/2506.09038
- 定位: 跨任务弃答评测。
- 机制: 20 个数据集、6 类弃答场景（含 3 个新建欠规格推理集）；用人工验证过的 LLM judge 同时
  评估"弃答与否"与"回答正确性"；报告弃答 F1 表；【转述】主指标为 abstention recall，另报
  precision / F1 / accuracy，配对构造方式为"删除决定性上下文生成不可答版本"。
- 可借鉴: 报告里同时呈现弃答 recall（该弃答时弃答的比例）与**过度弃答率**（可答格却弃答），
  与本项目已有的 `unsupported_confirmation_rate` 配成一对（建议 P3）。

### 5.4 SelfAware【转述】

- 仓库: https://github.com/yinzhangyue/SelfAware ；论文: https://arxiv.org/abs/2305.18153
- 定位: "我知道我不知道"评测集：3,369 题（1,032 不可答 + 2,337 可答）。
- 机制: 不可答题来自 Quora / HowStuffWorks，三名标注者独立审阅、一致才保留；分五类（无科学
  共识 / 想象 / 完全主观 / 变量过多 / 哲学）；可答对照用 SimCSE 从标准 QA 集取语义最近邻。
- 可借鉴: 弃答类型学（可用于规范化 evidence-gap 声明的分类）；"从可答样本系统化生成对照"
  的做法，可用于批量生成 B 变体。

### 5.5 SQuAD 2.0【转述】

- 资源: https://rajpurkar.github.io/SQuAD-explorer/ ；论文: https://arxiv.org/abs/1806.03822
- 定位: 阅读理解的"不可答"经典设计。
- 机制: 在原数据集上加入 5 万余道**对抗式撰写的、看起来像可答的**不可答题；模型必须学会在
  无答案时弃答；用 no-answer 标签与 EM/F1 计分。
- 可借鉴: B 变体"看起来可答"是正确设计（干扰 profile 保留），应作为场景设计评审的显式
  纪律；同时记录人类上限作参照。

### 5.6 AgentDojo（ETH SPYLab）【转述】

- 仓库: https://github.com/ethz-spylab/agentdojo ；论文: https://arxiv.org/abs/2406.13352
- 定位: 工具使用安全基准（提示注入）。
- 机制: 用户任务 = 自然语言指令 + 确定性二元 utility 检查（基于输出与环境前后状态）+ 金标
  调用序列；注入任务 = 攻击目标 + security 函数；`tool_filter` 防御（让 agent 先自限到任务
  所需工具）；论文指出该防御在"必需工具本身可构成攻击"时失效（约占测试用例 17%）。
- 可借鉴: 确定性状态检查优于纯文本判分；读写不对称自检——对每个场景显式验证"仅用允许的
  只读工具可解"，这正是建议 P1（oracle）要覆盖的不变量。

## 6. dbt / 数据栈可观测性与 MCP

### 6.1 Elementary【转述】

- 仓库: https://github.com/elementary-data/elementary
- 定位: dbt 原生数据可观测性（dbt 包 + `edr` CLI）。
- 机制: 包在 on-run-end 把 `run_results.json`、manifest/catalog 的关键字段灌进数仓表
  （`elementary_test_results`、`dbt_run_results` 等），CLI 据此出报告与告警；异常检测用
  z-score（|score| >= 3，阈值可配）；失败/警告自动开 incident、修复后自动 resolve，带
  status / assignee / severity（OSS 侧 incident 细节未验证，文档主要在 Cloud 版）。
- 可借鉴: "dbt artifacts → 观测层"是本项目证据工具（run results / node error）的行业常规
  版本，路线得到确认；incident 生命周期词表可作 IncidentBrief 扩展参考。

### 6.2 DQOps【转述】

- 仓库: https://github.com/dqops/dqo
- 定位: 数据质量平台（约 150 个内置检查）。
- 机制: check = sensor（度量查询）+ rule（阈值），违规产生 issue（severity warning / error /
  fatal）；按 table + dimension + category 归组成 incident，带 `first_seen` / `last_seen`；
  状态 Open / Acknowledged / Resolved / Muted；**刻意没有根因引擎**（"直到根因被修复"）。
- 可借鉴: incident 分级与生命周期词表；"没有根因引擎"正是 Agent 诊断的定位缺口——可在文档中
  显式声明这个边界。

### 6.3 Soda Core【转述】

- 仓库: https://github.com/sodadata/soda-core
- 定位: YAML 检查 DSL / 数据合同引擎。
- 机制: 标准指标检查（`missing_count(x) = 0`、`duplicate_percent(id) = 0` 等）与阈值边界、
  freshness / schema / reference / reconciliation 等检查类型；退出码 0 pass / 1 fail /
  2 warn-only / 3 could not run / 4 results unsent。
- 可借鉴: 机器可读的结果词表（退出码契约）——本项目产物侧已有 status 字段，可对照补全
  "could not run" 语义（对应 MODEL_ERROR / INVALID）。

### 6.4 OpenLineage + Marquez【转述】

- 仓库: https://github.com/OpenLineage/OpenLineage ；https://github.com/MarquezProject/marquez
- 定位: 血缘/运行事件标准与参考实现。
- 机制: Run 状态 START / RUNNING / COMPLETE / ABORT / FAIL / OTHER；facet 为实体的原子元数据；
  `DataQualityAssertions` facet 字段为 assertion（not_null / unique / row_count / custom_sql）、
  success、severity（error / warn）、column、expected、actual；dbt 集成（dbt-ol）读 manifest +
  run_results 发射事件。
- 可借鉴: evidence 记录字段与 `DataQualityAssertions` 对齐可降低第三方理解成本；Run 状态词表
  可直接用于产物/事件建模。

### 6.5 dbt-mcp（dbt Labs）【核对】

- 仓库: https://github.com/dbt-labs/dbt-mcp
- 定位: 官方 dbt MCP server（Apache-2.0）。
- 机制: 只暴露 tools（README 未见 resources / prompts）；工具按组控制，`.env.example` 提供
  `DISABLE_TOOLS`、`DBT_MCP_ENABLE_TOOLS` 及 `DBT_MCP_ENABLE_{SQL, SEMANTIC_LAYER, DISCOVERY,
  DBT_CLI, ADMIN_API, LSP, DBT_CODEGEN}`；README 明确警告 dbt CLI 等工具"可能修改数据模型与
  仓库对象"。
- 可借鉴: 建议 P3 的 MCP server 设计范式——只读子集、按组环境变量门控、写能力必须显式标注；
  本项目六个工具天然只读，是相对优势。

### 6.6 Great Expectations（简要）【转述】

- 仓库: https://github.com/fivetran/great_expectations
- 定位: "期望即单测"的数据校验；Checkpoint 驱动验证；`result_format` 控制结果详略
  （BOOLEAN_ONLY / BASIC / SUMMARY / COMPLETE）。
- 可借鉴: 验证结果按需返回详略的格式设计，与本项目证据内容的粒度控制思路一致。

## 7. 对照：本项目现状 vs 外部先例

| 能力 | 本项目现状 | 外部先例 | 差距 |
| --- | --- | --- | --- |
| 冻结身份（哈希绑定） | benchmark manifest 哈希、run_id | HELM RunSpec；SWE-bench 缓存键 | 无 |
| 批次有效性封存 | INVALID_HARNESS 结论 | Inspect log status 纪律 | 无 |
| 终态等价评分 | 判 claim/资产/缺口集合，不判路径 | τ-bench 数据库终态比对 | 无 |
| 弃答配对 | A/B 场景、缺口集合精确匹配 | SQuAD 2.0；AbstentionBench | 缺"过度弃答率"报告指标 |
| 离线重判 | 无（evaluator 运行期写死） | Inspect `score`；SWE-bench `report` | **关键缺口（P0）** |
| 参考解 / oracle | 仅有 FIXED_RULE（支付场景规则） | AIOpsLab / OpenRCA / RCAEval 基线 | **缺（P1）** |
| 重复试验可靠性 | 已有 `repeat_index` 三轮 kernel/static 排程；缺可靠性统计口径 | τ-bench pass^k；HELM trials | **缺（P2；勘误见改进计划第 2 节）** |
| 引用质量 | 硬门禁（逐 claim 兼容性 + 类型齐全） | ALCE recall / precision | 缺软指标（P3） |
| 第三方 agent 接入 | 仅内置策略（kernel / static / fixed_rule） | dbt-mcp、k8sgpt MCP 模式 | 缺 MCP 入口（P3） |
| 事件/证据词表 | 自有 IncidentBrief / evidence schema | OpenLineage facets；DQOps 生命周期 | 可选对齐（P3） |

## 8. 建议

### P0 — 离线重判（evaluator 与运行解耦）

- 动机: 现在 evaluator 的任何修复/升级都必须重跑模型，历史批次的模型质量结论因此与评测器
  版本耦合。Inspect AI 与 SWE-bench 都提供了"对存档日志重打分"的先例。
- 落地: 新增 CLI 子命令（如 `eval score <artifacts 路径或 run_id>`），复用
  `DeterministicEvaluator`；把运行期独占的检查结果（环境验证、恢复结论）固化进产物使其可离线
  读取；在 evaluation 产物与报告中记录 evaluator 版本（现有 `p1.evaluator.v2` 常量提升为随
  产物落盘的身份字段）。
- 验收: 对既有批次 artifacts 重跑评分与原 `evaluation.json` 一致（回归）；切换 evaluator 版本
  后，不重跑模型即可产出新评分，并能在 ledger/report 中区分版本。

### P1 — oracle 参考解基线（场景可解性证明）

- 动机: 现有 FIXED_RULE 是支付场景的规则对照，不是覆盖全部场景的参考解。AIOpsLab、OpenRCA、
  RCAEval 都以附带基线为常规做法。没有参考解就无法自动区分"模型失败"与"场景设计失败"。
- 落地: 新增确定性策略（如 `ORACLE_ANALYST`）或离线校验命令，输入为 scenario 合同
  （observable_evidence_contract、required_evidence_types、expected_status），按预期路径调用
  公开工具、按合同提交终局（CONFIRMED 或精确缺口集合）；接入 benchmark preflight 或
  `lab_verifier` 流程。
- 验收: 全部 CONFIRMED 场景 oracle 通过；全部 INSUFFICIENT 场景 oracle 提交的缺口集合与期望
  完全一致并通过收据检查；任何失败以场景缺陷上报（阻止冻结/执行）。

### P2 — 稳定性子集与 pass^k

- 勘误: 初稿写"每格只跑一次"有误；`benchmark_manifest.py` 已有 `repeat_index` 与三轮
  kernel/static 排程（改进计划第 2 节）。缺的是可靠性与通用试验协议，不是重复维度本身。
- 动机: 现有排程未被换算成可靠性口径，无法回答"同一格多跑几次是否稳定"；τ-bench 用 pass^k
  给出了现成的指标定义。
- 落地: 复用现有 `repeat_index` 与 `--only-strategy` / `--only-sequence` 子集机制定义重复子集
  （同格 k 次），ledger 增加 trial 维度；报告按组合公式计算 pass^k（k 随最少 trial 数收窄），
  子集照旧不出具正式报告。
- 验收: 对单个场景（如 B）完成 k=3 的小规模稳定性测量，产出 pass^1 / pass^2 / pass^3 与
  失败模式分布。

### P3 — 其他候选（按需取用）

- 弃答报告指标: 增加"过度弃答率"（可答格返回 INSUFFICIENT_EVIDENCE 的比例），与
  `unsupported_confirmation_rate` 并列（AbstentionBench / SQuAD 2.0 思路）。
- 引用软指标: 在硬门禁之外报告"引用必要率 / 覆盖 recall"（ALCE 思路），不进任何 gate。
- MCP server: 把六个只读工具包成 MCP server（dbt-mcp 范式：只读子集、按组门控、统一错误
  信封、每次返回带 evidence id），让第三方 agent 作为策略被试直接接入。
- 回放测试: 用 pydantic-ai `FunctionModel` + `capture_run_messages` 把真实失败 trace 固化为
  确定性测试（工具序列 + 预算计数断言）。
- 词表对齐: evidence 字段向 OpenLineage `DataQualityAssertions` 对齐；IncidentBrief 状态词表
  参考 DQOps（Open / Acknowledged / Resolved / Muted）。
- 设计文档显式化: 把"B 变体必须看起来可答""参考轨迹只用于推导目标态""仅用只读工具可解"写成
  场景设计不变量（SQuAD 2.0 / τ-bench / AgentDojo 的对偶经验）。

## 9. 附录

### 9.1 未验证与注意事项

- Kaleidoscope（弃答评测相关）：多路检索均未找到对应的公开 benchmark，本报告未采纳其结论。
- τ2-bench 的 pass^k 在仓库 README 页面上未见；该指标以论文（arXiv:2406.12045）为准。
- ITBench 的具体评分指标、故障注入细节、真值格式未验证。
- HolmesGPT 用例字段截图式转述自其测试代码，未逐条复核。
- k8sgpt 的 MCP 模式（12 tools / 3 resources / 3 prompts）来自其 README 转述，未复核细节。
- Elementary 的 incident 生命周期主要在 Cloud 文档，OSS 侧未验证。
- 仓库星标/活跃度等数字随时间变化，引用前请复核。
- 标注【转述】的条目建议按 9.2 链接回源确认后再作为决策依据。

### 9.2 参考链接

- AIOpsLab: https://github.com/microsoft/AIOpsLab
- OpenRCA: https://github.com/microsoft/OpenRCA
- ITBench: https://github.com/itbench-hub/ITBench
- RCAEval: https://github.com/phamquiluan/RCAEval
- HolmesGPT: https://github.com/robusta-dev/holmesgpt
- k8sgpt: https://github.com/k8sgpt-ai/k8sgpt
- τ-bench / τ2-bench: https://github.com/sierra-research/tau2-bench ，https://arxiv.org/abs/2406.12045
- Inspect AI: https://github.com/UKGovernmentBEIS/inspect_ai ，https://inspect.aisi.org.uk/
- SWE-bench: https://github.com/SWE-bench/SWE-bench ，https://arxiv.org/abs/2310.06770
- HELM: https://github.com/stanford-crfm/helm
- METR Task Standard / HCAST: https://github.com/METR/task-standard ，https://github.com/METR/hcast-public
- pydantic-ai: https://github.com/pydantic/pydantic-ai ，https://pydantic.dev/docs/ai/evals/
- GAIA: https://huggingface.co/datasets/gaia-benchmark/GAIA ，https://arxiv.org/abs/2311.12983
- ALCE: https://github.com/princeton-nlp/ALCE ，https://arxiv.org/abs/2305.14627
- AbstentionBench: https://github.com/facebookresearch/AbstentionBench ，https://arxiv.org/abs/2506.09038
- SelfAware: https://github.com/yinzhangyue/SelfAware ，https://arxiv.org/abs/2305.18153
- SQuAD 2.0: https://rajpurkar.github.io/SQuAD-explorer/ ，https://arxiv.org/abs/1806.03822
- AgentDojo: https://github.com/ethz-spylab/agentdojo ，https://arxiv.org/abs/2406.13352
- Elementary: https://github.com/elementary-data/elementary
- DQOps: https://github.com/dqops/dqo
- Soda Core: https://github.com/sodadata/soda-core
- OpenLineage / Marquez: https://github.com/OpenLineage/OpenLineage ，https://github.com/MarquezProject/marquez
- dbt-mcp: https://github.com/dbt-labs/dbt-mcp
- Great Expectations: https://github.com/fivetran/great_expectations
