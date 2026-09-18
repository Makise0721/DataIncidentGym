# T13 切片 1 报告：事实层（运行绑定、E1/E2 批量工具、拒绝明细）

- 日期：2026-09-18。范围：设计 §6 切片 1 的**离线实现**（发起于切片 0 通过审查 `f93298d`）。
- **未包含**：数据库 dry run（构造验证）、v2 运行归档后的重载路径与模型可见列表签名（属切片 4 策略面）、
  新 manifest 冻结与真实模型测量。

## 1. 运行上下文 v2（设计 §2.4）

- `run_context.py`：新增 `p1.runtime.v2`，在 v1 字段之上增加
  `observable_relations.expectation`、`observable_nodes.definition`、`evidence_baseline`
  （路径 + 基线指纹 + 快照摘要）与 `build_provenance`（`dbt_invocation_id` + manifest/run_results/
  compiled_tree 三个摘要）。**校验按版本分流**：v1 的字段集合保持逐字节严格（带 v2 字段的 v1 载荷被拒），
  v2 追加校验 `expectation_relations ⊆ schema_relations`、节点列表与各摘要格式。
- `lab.build`：仅当场景使用 **v2 合同**时写入 `baseline_evidence.json`（按 `expectation_relations` 裁剪的
  可信基线快照，附 `baseline_fingerprint`）与 runtime v2；v1 场景的产物与写入次序完全不变。
- **归属与一致性的构建时保证（fail-closed）**：runtime v2 在 **redaction 之后**写成，摘要覆盖最终归档
  字节；写前校验 manifest 与 run_results 的 `invocation_id` 相等且非空、同一节点的三个来源
  （run_results / compiled 文件 / manifest）编译文本一致——任一不成立即**构建失败**，不允许产出
  自相矛盾的运行。`compiled_tree_digest` 为共享 helper（构建端与读取端同源，冻结形式：按路径排序的
  `[相对路径, 文件摘要]` JSON 数组的 sha256）。
- 可信基线的**自校验**：读取 `.dig/baseline-summary.json` 后重算指纹并比对，不符即拒绝（防"后来替换的
  全局基线"进入运行绑定）。

## 2. E1/E2 批量工具（设计 §2.1–§2.3）

实现于新模块 `evidence_batch.py`（`EvidenceTools` 只加两个薄委托方法）：

| 项 | 实现 |
| --- | --- |
| 请求编码 | 冻结为逗号连接的字符串（请求顺序、首次出现去重）；空串/纯分隔符 → `TARGETS_EMPTY`；超过 8 个目标 → `BATCH_TOO_LARGE` |
| 原子拒绝 | 任一目标被拒 → 整次调用拒绝，**权威明细为逐目标 `(target, code)` 列表**（同一批可含不同码） |
| E2 拒绝码 | 不在合同 `definition_nodes` 白名单 → `NODE_NOT_ALLOWED`；在白名单但本运行 manifest 中不存在 → `NODE_NOT_FOUND`；在 manifest 但不在失败节点上游闭包 → `NODE_NOT_ALLOWED` |
| E1 拒绝码 | 不在 `expectation_relations` → `RELATION_NOT_ALLOWED`；v1 运行（无 v2 绑定）一律拒绝 |
| 逐项 UNKNOWN | E1：关系不在快照 → `known=false`；E2：无任何编译文本 → `known=false` |
| 文本完整性 | 超过 16 KiB → 文本截断 + `complete=false`；**缺失定义同样 `complete=false`**（映射读器对二者一律 UNKNOWN） |
| 归属校验 | manifest/run_results 字节摘要、compiled_tree 摘要、两处 `invocation_id`；来源冲突 → `EVIDENCE_INTEGRITY_ERROR`（内容一致性与归属分别校验） |
| 计数 | 一次调用 = 一次工具尝试（与列表长度无关）；每条事实单独登记 |

## 3. 会话接线（切片 1c 的会话层）

- `strategy_adapter`：`_TOOL_ARGUMENTS` 登记两个批量工具（参数仍是**字符串**，与冻结编码一致；
  传列表会被既有规则拒绝为 `TOOL_ARGUMENT_INVALID`——模型可见的列表签名在切片 4 的策略面转换）；
  `ToolReceipt` 新增 `target_refusals`（逐目标 `TargetRefusal`），由 `call_tool` 从批量拒绝异常上取；
  调用级码固定 `TARGETS_REFUSED`，永不作为见证。
- v1 默认白名单仍是六工具：v1 会话调用批量工具得到 `TOOL_NOT_ALLOWLISTED`（回归覆盖）。

## 4. 回归与验证

`tests/unit/test_t13_batch_evidence.py`（21 条，全部离线、合成运行目录）：
- 运行上下文：v1 拒绝 v2 字段、v2 要求期望子集；
- E1：逐目标事实与请求顺序/去重、原子拒绝与逐目标码、v1 运行全拒、空请求与上限、快照被篡改 →
  完整性报错；
- E2：来源优先级三种情形与逐项 UNKNOWN、**混合错误码**（`NODE_NOT_ALLOWED` + `NODE_NOT_FOUND`）、
  授权内/外混合、换入他次构建的 manifest / invocation 不一致 / 来源冲突分别报错、超长文本截断且
  `complete=false`；
- 会话：批量拒绝透过会话进入收据（含逐目标明细）、v1 会话不授予、列表参数被拒；
- lab 写入端：快照裁剪与指纹绑定、被篡改的全局基线拒绝、runtime v2 覆盖最终字节且通过自身校验、
  来源矛盾时构建失败。

其他：`tests/unit/test_evidence.py` 的类型表守卫改为"前六项 v1 冻结 + v2 追加两项"（顺序不变、无删除）。

**验证**：`ruff check .`、`git diff --check` 通过；全量单测 **892 passed / 5 skipped**；集成套件
**43 passed / 1 failed**——失败项 `type_change_payment_amount_drift_b` 的 `OFFLINE_SCORE_WRITE_FAILED`
经定位与切片 1 无关：重评写入端（`evaluation_rescore._write_score_dir`）是**另一份**临时目录 + rename
实现，没有上轮加入的有界重试，撞上同一类 Windows 瞬态。本次把重试提为共享的
`evaluation_inputs.rename_directory_with_retry` 并同时用于两个写入端（含两条针对重评写入端的回归），
复跑该案例 **1 passed**。这是对已确证写入路径问题的第二处同类修复，不改变任何身份。

## 5. 边界（如实）

- **v2 运行的归档序列化与重载**、**轨迹中的 `target_refusals` 落盘**、**模型可见的列表签名转换**均属
  切片 4（策略面接入）；当前已验证的是事实层、会话收据与见证判据本身。
- 数据库 dry run 仍未执行（独立授权）；本切片的行为全部由合成运行目录覆盖，**不构成实跑结论**。
- 新 manifest 冻结与真实模型测量不在本切片范围。
