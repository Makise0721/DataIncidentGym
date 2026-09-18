# T13 切片 4（第一增量）报告：公开身份桥

- 日期：2026-09-18。范围：切片 4 的第一项——**把"完整关系身份 → 合同/E1 名称"的映射做成公开元数据**，
  供参考解与规划器接线时使用（切片 2 报告 §6、审计切片 4 清单第一条）。
- **未包含**：v2 工具面白名单与策略身份、参考解的 T13 判别分支、管理平面卡片 v2 派生、规划器模型可见
  列表签名、`certify --admit` 与端到端验收（需数据库授权）。

## 1. 为什么需要这一层（问题与约束）

读器按 SQL 原文上报**完整关系身份**（如 `data_incident_gym.analytics.raw_customers`），而合同与 E1 用的是
**声明名**（`raw_customers`）。审计已明确：这个对应关系必须"由接线方按公开元数据**显式**建立"，读器不做
名称归一（否则就会回到"按末段名匹配"的老问题）。同时参考解的证据纪律是"只接收被评策略能拿到的公开面"，
所以桥必须**经由工具事实**，不能靠参考解直接读 manifest 文件。

## 2. 落地内容

1. **读器新增公开函数** `column_mapping.relation_identity(reference) -> str | None`：把任意关系引用渲染成
   **读器上报的同一文本**（分段 + 引号规则一致），非法引用返回 `None`。桥的两侧都过这套规则，因此不可能
   出现"工具说 A、读器说 B"。
2. **E2 事实（`DBT_NODE_DEFINITION`）新增两个字段**：`name`（节点声明名，与 E1 的名称同一套）与
   `relation_identity`（该节点 SQL 写出的关系身份）。两者都直接取自**本次运行的 manifest**（E2 声明的
   来源就是运行产物）；测试类节点没有关系 → 两字段均为 `None`，绝不猜测。
3. **E1 事实（`RELATION_SCHEMA_EXPECTATION`）新增两个字段**：`relation_identity` 与 `resource_type`
   （`seed`/`model`）——后者让调用方能判断某关系能否终止追溯（seed/source 可以、model 不可以），
   这正是"必须由公开元数据显式建立"的另一半。
4. **运行期基线快照**（`baseline_evidence.json`，v2 运行专有、构建时由 lab 从 manifest 写出）为每个关系
   带上 `relation_identity` 与 `resource_type`；解析器把这两个键设为**可选**：扩展前写出的快照仍可读，
   其 E1 事实两个字段为 `None`（调用方必须因此弃答，见 §4）。
5. v1 面零改动：六工具的事实模型新字段均为可选且不参与序列化差异（不存在即为不存在），v1 运行不写
   `baseline_evidence.json`，也不产生这两个事实。

## 3. 验证

`tests/unit/test_t13_batch_evidence.py` 新增 6 条（28 → **34**）：

| 用例 | 断言 |
| --- | --- |
| `test_the_definition_fact_carries_the_public_identity_bridge` | E2 给出 `name` 与 3 段身份 |
| `test_a_quoted_relation_name_keeps_its_identity_in_the_fact` | 引号大写身份保真（`analytics."STG_CUSTOMERS"`） |
| `test_a_test_node_carries_no_relation_identity` | 测试节点两字段为 `None` |
| `test_the_expectation_fact_carries_the_bridge_when_the_snapshot_has_it` | E1 给出身份与 `resource_type=seed` |
| `test_a_snapshot_without_the_bridge_stays_readable` | 扩展前快照可读、字段为 `None`（向后兼容） |
| `test_the_build_writes_the_bridge_into_the_snapshot` | lab 从 manifest 写出身份与资源类型 |

测试夹具同步修正两处**不真实之处**：编译文件按字节写（此前文本模式会隐式改写行尾）、节点可按声明写
`resource_type`（此前一律 `model`，无法表达 seed）。这正是此前 CRLF 缺陷躲过单测的同一类夹具问题。

**真实归档核对**（dry run 第三次运行的 T1′ run `6c2c5a03079d42ab8ae498767a1394c2`，只读）：

```text
model.jaffle_shop.customers:     name=customers     identity=data_incident_gym.analytics.customers     complete=True
model.jaffle_shop.stg_customers: name=stg_customers identity=data_incident_gym.analytics.stg_customers complete=True
model.jaffle_shop.stg_orders:    name=stg_orders    identity=data_incident_gym.analytics.stg_orders    complete=True
E1（扩展前快照）: identity=None resource_type=None
```

`ruff check .`、`git diff --check` 通过；全量单测 **963 passed / 5 skipped**。

## 4. 边界与已知后果

- **扩展前的 v2 归档**：E1 事实不再带身份（快照无该键），调用方必须因此弃答（fail-closed）。dry run 两次
  运行的归档属于这一类；它们作为"构建路径成立"的证据仍有效，但不具备身份桥。新构建（认证运行）会带上
  桥，因此**认证必须在本次改动之后重新构建运行**。
- 本增量不改变任何现有结论：不接策略面、不改判据、不动 v1 面、不新增工具、不新增加权。
- 桥只提供**对应关系**，不提供判断：它不说明某个偏差是否存在、也不做任何归因。

## 5. 切片 4 剩余项（按依赖顺序，各自独立提交与审计）

1. **v2 工具面白名单与策略身份**：`EVIDENCE_V2_TOOL_ALLOWLIST`（六工具 + E1/E2）、按运行上下文选择白名单、
   参考解与规划器的 v2 政策身份（v1 身份逐字节不变）。
2. **参考解的 T13 判别分支**：用 E2 定义 + 桥构造读器输入（`upstream` 按身份、`terminal_relations` 取
   `resource_type ∈ {seed, source}` 的关系），按设计 §4.2 的充分条件判 `SOURCE_SCHEMA_COLUMN_TYPE_CHANGED`；
   桥缺失或读器 UNKNOWN → 弃答。
3. **管理平面卡片 v2 派生**：`_readonly_path` 纳入两个 v2 工具；`_decisive_difference` 描述被扣留的
   `expectation_relations`/`definition_nodes`。
4. **规划器模型可见列表签名**：v2 运行时模型可见工具清单为八项，身份计入 v2。
5. **认证与端到端**（需数据库授权）：`certify --admit` 两对、B 缺口在归档中逐条复现、`score_run_offline`
   与在线评测逐字段一致。
