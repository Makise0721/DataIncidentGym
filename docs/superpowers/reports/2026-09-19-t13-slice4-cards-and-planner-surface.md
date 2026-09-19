# T13 切片 4 · 第四增量：管理平面卡片 v2 派生 + 规划器模型可见列表签名

- 日期：2026-09-19。状态：**已完成（离线），待审计**。
- 前置：增量 3（参考解 T13 判别分支，`40da2c4`）审计通过；"代表缺口规则"裁定批准。

## 0. 裁定边界记录（审计要求记档）

第三增量报告 §4"代表缺口规则"（每次原子拒绝的诊断代表 = 请求序第一个目标）经裁定批准，边界记录在案：
**该约定假设每次原子拒绝对应合同中恰好一条缺口、且 subject 为请求首目标。** 若未来场景需要表达"一次
拒绝阻塞多个语义不同的缺口"，需另立设计——评测器 `_insufficiency_matches` 与认证
`gap_keys == _expected_gap_keys` 的精确相等要求会强制这一点（多代表声明会破坏精确匹配而失败，不会误过）。

## 1. 管理平面卡片 v2 派生（`scenario_cards.py`）

- **`_readonly_path`**：v2 合同（`observable_evidence.v2`）上，两个批量工具按声明加入路径——
  必需证据类型点名（A 变体：`RELATION_SCHEMA_EXPECTATION`/`DBT_NODE_DEFINITION` 在 required 中）
  **或** 合同的 `unresolved_gaps` 以 `tool_name` 点名（B 变体：拒绝探针）。两对 A/B 卡片的路径同为
  `[get_dbt_run_results, get_dbt_node_error, get_relation_schema, get_relation_schema_expectation,
  get_dbt_node_definition]`——与两条真实参考路径一致（A 授予采集、B 拒绝探针）。v1 合同的派生
  逐字节不变（既有卡片回归原样通过）。
- **`_decisive_difference`**：当两个合同都是 v2 时，把 `expectation_relations` 与 `definition_nodes`
  纳入"失去读取权"的比较并点名。对 1/对 2 的 B 卡片文本同时点名两个被扣留白名单
  （`expectation raw_customers,raw_orders`、`definition_nodes <三节点>`）与两条拒绝缺口；镜像对
  文本逐字节相同（回归钉住）。

**如实声明的偏差（需裁定）**：两对合同的 `required_evidence_types` 不含 `DBT_LINEAGE`（切片 3 落盘时
按 §4.1"6 步 + 余量"清单定的），但参考实现为推导请求目标**确实调用** upstream lineage（A），B 亦然。
因此卡片的 readonly_path 不含 `get_dbt_lineage`——合同派生规则无法推出它。两个选项：① 维持现状
（卡片只复述合同可推导的工具；文档略保守）；② 给两对合同的 required 增加 `DBT_LINEAGE`（更准确，
但要改切片 3 已落盘的合同文件与 digest，需审计批准）。本增量未动合同文件，选择 ① 并在此声明。

## 2. 规划器模型可见列表签名（`evidence_planner.py` / `planner_agent.py`）

模型可见列表 = 两个动作工具 + 终端输出工具的**注册 schema**（`planner_model_tool_payload` 读回 SDK
实际注册的定义），经 `planner_controller_payload["model_tools"]` 进入 controller 摘要、经
`planner_policy_surface().tool_schema_payload` 进入身份面。增量 2 后仍缺的三处已补齐：

- **终端输出工具按 surface 绑定词表**：v1 保持 `FinalSubmission`；v2 运行的 `submit_diagnosis` 绑
  `FinalSubmissionV2`——没有这一步，v2 运行的规划器模型在 schema 层面就声明不了
  `RELATION_SCHEMA_EXPECTATION`/`NODE_NOT_ALLOWED` 缺口。动作工具两个面**完全相同**（其描述文本
  不含"六工具"字样；`PlanStepDeclaration.tool_name` 字段描述里的"six"只存在于服务端校验模型，不在
  SDK 注册的函数签名里，模型不可见——如实的边界，审计可验证）。
- **`planner_controller_payload` 的 `model_tools` 透传 surface**：增量 2 的 v2 controller 摘要只换了
  义务表，`model_tools` 仍是 v1 输出工具 schema；修正后 v2 身份摘要真正绑定 v2 模型可见面（v2 摘要
  值随之变化——v2 身份未冻结，允许；v1 摘要逐字节不变，由冻结 manifest 回归钉住）。
- **`planner_policy_surface(surface=…)`**：默认 v1（注册表路径——manifest、setup-failure 物化、身份
  读取器——全部不变）；v2 面的 `final_diagnosis_schema_sha256` 用 `DiagnosisV2` 的 schema。
- **`planner_agent` 接线**：`_agent` 的 `output_type` 按运行 surface 取输出工具；提交校验签名接受
  两种提交模型（会话侧按类型构造 `Diagnosis`/`DiagnosisV2`，增量 3 已接）；`_user_prompt` 在运行时
  含 `observable_nodes`（v2 节点白名单进入模型可见上下文），v1 runtime 无该键、提示逐字节不变。

## 3. 回归与验证

- `tests/unit/test_scenario_cards.py`：新增 T13 卡片回归（A/B 路径含两批量工具、decisive difference
  点名两白名单与两缺口、镜像对文本一致）；全目录卡片的工具面断言改为按合同版本选面。
- `tests/unit/test_t13_v2_tool_surface.py`：新增 3 条——模型可见列表按面绑定词表（v1 schema 不含
  v2 缺口词、动作工具两面相同）；注册表面恒为冻结 v1 面（identity、tool_schema_payload、schema 摘要
  三点核对）且 v2 面不同；v2 用户提示含 `observable_nodes` 而 v1 逐字节不变。
- `tests/unit/test_t13_reference_v2_branch.py`：逐字节 fixture 改为经 `tests.unit.` 命名空间包从读器
  回归单源导入（`--import-mode=importlib` 支持），删除重复。
- 全量：`uv run python -m pytest tests/unit -q` → **986 passed / 5 skipped**（982 + 新增 4，零回归，
  冻结 manifest 身份回归全在内）；`uv run ruff check .`、`git diff --check` 干净，改动文件均为 LF。

## 4. 边界与未决

- 规划器 v2 路径的**离线单测**覆盖到 payload/schema/提示；`EvidencePlannerRunner` 的完整 v2 运行需要
  真实模型，属 real_model 测量，不在 T13 范围。
- lineage 需求面问题（§1 声明）待裁定；其余切片 4 项至此全部完成。
- 下一步：认证与端到端（需数据库授权书——按审计要求沿用 dry run 规格：重建运行（身份桥之后）、
  `certify --admit` 两对、B 缺口归档复现、`score_run_offline` 与在线评测逐字段一致）。
