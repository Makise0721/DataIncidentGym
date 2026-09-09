# M7 development smoke

本报告记录四套分别授权、各执行一次的 M7 development smoke。它们明确排除在正式 94-run benchmark 分母之外；不据此计算 P1 aggregate metrics，也不据此宣布策略优劣。

## 第一套执行边界

- 代码版本：`e529fa5c133faf48075f9a14ec6744cfc23acb08`
- 矩阵：4 个 P1 场景 × 2 个策略 = 8 个 cell
- 顺序：Static Skill 四格，然后 Diagnostic Kernel 四格；每格只执行一次
- 命令：`uv run pytest tests/e2e/test_real_model_m7_smoke.py -m real_model -q -s`
- 结果：`1 failed, 7 passed in 462.31s`；没有 pytest 重试、替换样本或第九次调用
- 产物：7 个返回 `EvaluationAttemptResult` 的 cell 均写出 canonical 六文件；首个 cell 在初始 reset 阶段失败，未开始诊断，因而没有 run/artifact
- 所有已产出 run 的 `recovery_status` 均为 `HEALTHY`

### 状态计数

| 策略 | cell 数 | evaluator `PASSED` | evaluator `FAILED` | 初始 reset 失败 |
| --- | ---: | ---: | ---: | ---: |
| `STATIC_SKILL` | 4 | 0 | 3 | 1 |
| `DIAGNOSTIC_KERNEL` | 4 | 0 | 4 | 0 |
| 合计 | 8 | 0 | 7 | 1 |

诊断状态方面，Static Skill 为 `MODEL_ERROR=2`、`NO_INCIDENT=1`、未产生结果 `1`；Diagnostic Kernel 为 `MODEL_ERROR=4`。这些是本次样本的观测，不是正式 benchmark 指标。

## 八个 cell

| # | 策略 | 场景 | run_id | 诊断状态 | 评测状态 | artifact |
| ---: | --- | --- | --- | --- | --- | --- |
| 1 | `STATIC_SKILL` | `schema_type_change_payment_amount` | — | 未开始 | `INITIAL_RESET_FAILED` | — |
| 2 | `STATIC_SKILL` | `schema_type_change_order_customer_a` | `c2ffd4bfffbb4e2181dc090de29793a2` | `MODEL_ERROR: MODEL_REQUEST_LIMIT` | `FAILED` | `artifacts/c2ffd4bfffbb4e2181dc090de29793a2/` |
| 3 | `STATIC_SKILL` | `schema_type_change_order_customer_b` | `9d814a858b1c43c78a77fb4a3b713bf3` | `MODEL_ERROR: MODEL_REQUEST_LIMIT` | `FAILED` | `artifacts/9d814a858b1c43c78a77fb4a3b713bf3/` |
| 4 | `STATIC_SKILL` | `order_volume_pattern_a` | `44444e19dabc47eab7582cda8dea6982` | `NO_INCIDENT` | `FAILED` | `artifacts/44444e19dabc47eab7582cda8dea6982/` |
| 5 | `DIAGNOSTIC_KERNEL` | `schema_type_change_payment_amount` | `30c222076ff74381a7f678117a7a15a2` | `MODEL_ERROR: MODEL_REQUEST_LIMIT` | `FAILED` | `artifacts/30c222076ff74381a7f678117a7a15a2/` |
| 6 | `DIAGNOSTIC_KERNEL` | `schema_type_change_order_customer_a` | `2ab9d59557984b7fa8d8b34e41941d09` | `MODEL_ERROR: MODEL_REQUEST_LIMIT` | `FAILED` | `artifacts/2ab9d59557984b7fa8d8b34e41941d09/` |
| 7 | `DIAGNOSTIC_KERNEL` | `schema_type_change_order_customer_b` | `82c816d48ffc418aaa569aab2c2dbcb0` | `MODEL_ERROR: MODEL_REQUEST_LIMIT` | `FAILED` | `artifacts/82c816d48ffc418aaa569aab2c2dbcb0/` |
| 8 | `DIAGNOSTIC_KERNEL` | `order_volume_pattern_a` | `47253ea186db4dc6894e949a6d46af1c` | `MODEL_ERROR: MODEL_REQUEST_LIMIT` | `FAILED` | `artifacts/47253ea186db4dc6894e949a6d46af1c/` |

## 产物与失败细节

七个 artifact 目录均精确包含：`metadata.json`、`trace.jsonl`、`evidence.json`、`diagnosis.json`、`evaluation.json`、`report.md`。

- `c2ffd4...`：Static/customer A，6 次模型请求、6 次工具尝试且均成功；模型最终达到 `MODEL_REQUEST_LIMIT`。
- `9d814a...`：Static/customer B，6 次模型请求、7 次工具尝试且均成功；最终为 `MODEL_REQUEST_LIMIT`。
- `44444e...`：Static/control 的模型输出为 `NO_INCIDENT`，但只引用了 profile/history 证据，未把成功 dbt run 证据绑定到健康主张；evaluator 因 `REQUIRED_EVIDENCE_TYPES_PRESENT`、`CLAIM_EVIDENCE_COMPATIBLE`、`POSITIVE_HEALTH_EVIDENCE` 判定失败。
- `30c222...`、`2ab9d5...`：Kernel/payment 与 Kernel/customer A 各使用 8 次模型请求、8 次工具尝试，成功工具数为 0，终态为 `MODEL_REQUEST_LIMIT`。
- `82c816...`：Kernel/customer B 使用 8 次模型请求、8 次工具尝试，成功工具数为 0，终态为 `MODEL_REQUEST_LIMIT`。
- `47253e...`：Kernel/control 使用 8 次模型请求、10 次工具尝试，成功工具数为 0，终态为 `MODEL_REQUEST_LIMIT`。

首个 cell 的外层错误只保留稳定码 `INITIAL_RESET_FAILED`；实现没有把底层数据库/dbt 异常回显到 smoke 输出，也没有 run_id 可供 artifact 查询。后续 7 格及当前 PostgreSQL compose 均恢复健康，因此该格保留为未进一步归因的本地环境失败，不关闭为产品 bug。

## 模型与协议身份

Smoke artifact 的实际运行身份（来自每个 `metadata.json`）：

- provider：`openai-compatible`
- model：`mimo-v2.5`
- model base URL：`https://api.xiaomimimo.com/v1`
- budget：模型请求 `8`、工具调用 `8`、结构化输出重试 `2`、运行上限 `300s`
- base prompt：`p1.base.v1` / `fea5fe8951d5ae5d5c86165a9586ce5b51dce5586943ba9ab833e92a71dc1cf9`
- Static prompt：`p1.static.v1` / `ab5f42e77b75333e4de78d319f3e8888702afc0bfb2c100cdb9efbec9ae407b7`
- Kernel prompt：`p1.kernel.v1` / `0f295af4417b99efc0ed33a2cecbd321647a836b82e5f19a6a85f276f241a36f`
- Static controller：`p1.controller.v1` / `78f6d15e65794cf7e7b38a00fa4357b532496090c75ac5ce9533b73e7ff6415d`
- Kernel controller：`p1.controller.v1` / `e3c1ea4ac39c0299c9dd95f990f0cdd9ee91999cef75b30a55266a206954abdb`
- shared tool schema：`7e6b557f6ac7eb173b9d9c0f13b167293412103c11f3940b14221794a009504f`
- benchmark manifest：`null`（M7 尚未冻结正式 Manifest）

`doctor` 只执行过一次。它读取 `.env.diagnostic` 的本地 Ollama 配置（`127.0.0.1:11434/v1`, `gemma4:e4b`），当时 `MODEL_ENDPOINT` 为 `UNAVAILABLE`，所以该 Doctor 结果为失败；随后仅启动了已安装的 Ollama 服务并确认模型已存在，没有重跑 Doctor。Smoke 测试显式使用 `DiagnosticSettings(_env_file=None)`，所以本次 7 个 artifact 记录的是上面的进程环境/默认远端身份，而不是 `.env.diagnostic` 的本地身份。

## 结论与边界

本次 smoke 的 8 个调用已全部消耗并完整留痕；由于存在一个初始 reset hard-gate 失败且 7 个 evaluator 结果均未通过，不能把 M7 标为 smoke 通过，也不能据此调整场景、宣称策略胜负或开始 M8。该报告及 `.dig/`、`artifacts/` 产物只服务于本次开发审计；本 smoke 明确排除在正式 94-run benchmark 分母之外。

## 修复后新授权的精确八格

### 前置与执行边界

- 代码版本：`c166caa0d58f6008760693bca2f04d4b62d99968`
- 确定性 CI：Ubuntu run `33314966099` 全绿；Ruff、`140 passed` unit、`11 passed` integration、`14 passed, 10 deselected` E2E
- 修复：Kernel 隐藏 intent 合同补全并升为 `p1.kernel.v2`；区分 `MODEL_TOOL_CALL_LIMIT`；smoke 与 CLI/Doctor 统一读取有效配置
- 本地前置：只执行确定性健康 baseline build；进程级临时 `0xFFFF` affinity 与 `DBT_PARTIAL_PARSE=false` 在命令后恢复
- 模型：以进程级配置固定批准的 `mimo-v2.5` 与 `https://api.xiaomimimo.com/v1`；未修改用户 `.env.diagnostic`，未追加 Doctor 模型探针
- 命令仍为：`uv run pytest tests/e2e/test_real_model_m7_smoke.py -m real_model -q -s`
- pytest 结果：`8 passed in 825.23s`；没有 rerun、补位或第九格
- 产物：8/8 均返回 run ID，精确包含 canonical 六文件，`recovery_status=HEALTHY`
- 硬门禁：8/8 的 `ENVIRONMENT_VERIFIED`、`TOOL_ALLOWLIST_EXACT`、`TRACE_READ_ONLY_SAFE`、`RECOVERY_HEALTHY` 均通过
- 正确性：8/8 evaluator 均为 `FAILED`

### 新八格结果

| # | 策略 | 场景 | run_id | 诊断终态 | 请求 | 工具尝试/成功 |
| ---: | --- | --- | --- | --- | ---: | ---: |
| 1 | `STATIC_SKILL` | `schema_type_change_payment_amount` | `a242e7e989a64a66b5d85d2ac3ef1ff7` | `MODEL_PROTOCOL_ERROR` | 7 | 6 / 5 |
| 2 | `STATIC_SKILL` | `schema_type_change_order_customer_a` | `cbc9652ef11947dea1a0d8ffc3b05079` | `MODEL_TOOL_CALL_LIMIT` | 4 | 6 / 6 |
| 3 | `STATIC_SKILL` | `schema_type_change_order_customer_b` | `604c375f23b340e78a987b081c04faf5` | `MODEL_PROTOCOL_ERROR` | 8 | 8 / 8 |
| 4 | `STATIC_SKILL` | `order_volume_pattern_a` | `70cf6bd35c8348989b1af6df80d98c9f` | `MODEL_PROTOCOL_ERROR` | 5 | 3 / 3 |
| 5 | `DIAGNOSTIC_KERNEL` | `schema_type_change_payment_amount` | `6d7f9108e79c441a9b627dd2bf9213b8` | `MODEL_REQUEST_LIMIT` | 8 | 8 / 5 |
| 6 | `DIAGNOSTIC_KERNEL` | `schema_type_change_order_customer_a` | `6e1715a3f9d845b39aa345bfd86fd471` | `MODEL_REQUEST_LIMIT` | 8 | 8 / 5 |
| 7 | `DIAGNOSTIC_KERNEL` | `schema_type_change_order_customer_b` | `2361c4b617744885a5ed1aef4fa04935` | `MODEL_REQUEST_LIMIT` | 8 | 6 / 0 |
| 8 | `DIAGNOSTIC_KERNEL` | `order_volume_pattern_a` | `708426791d114218926d462bd44db05f` | `MODEL_REQUEST_LIMIT` | 8 | 6 / 3 |

Static/payment 的一次 profile history 请求因关系未声明而以 `RELATION_NOT_ALLOWED` fail closed，随后最终输出 schema 被拒绝。Static/customer A 在下一批工具调用会超过总上限时准确返回新原因码 `MODEL_TOOL_CALL_LIMIT`；其余两个 Static 格在已取得证据后仍未生成合法最终输出。

Kernel v2 在 payment、customer A、control 三格分别成功执行 5、5、3 个证据工具，较第一套对应的 0、0、0 有明确协议层改善；customer B 仍持续产生 `KERNEL_INTENT_INVALID` / `KERNEL_INTENT_SHAPE_INVALID`。三个取得证据的 Kernel 格仍未在 8 次请求内完成合规的假设、评估、主张和终态，因此控制器按 `MODEL_REQUEST_LIMIT` fail closed。

### 新八格协议身份

- base prompt：`p1.base.v1` / `fea5fe8951d5ae5d5c86165a9586ce5b51dce5586943ba9ab833e92a71dc1cf9`
- Static prompt：`p1.static.v1` / `ab5f42e77b75333e4de78d319f3e8888702afc0bfb2c100cdb9efbec9ae407b7`
- Kernel prompt：`p1.kernel.v2` / `67af76ada83bc0138fafc1e6079009c290ed569012b547c783526a21a8bfa133`
- Static controller：`78f6d15e65794cf7e7b38a00fa4357b532496090c75ac5ce9533b73e7ff6415d`
- Kernel controller：`e3c1ea4ac39c0299c9dd95f990f0cdd9ee91999cef75b30a55266a206954abdb`
- shared tool schema：`7e6b557f6ac7eb173b9d9c0f13b167293412103c11f3940b14221794a009504f`
- budget：`8 / 8 / 2 / 300s`；benchmark manifest 仍为 `null`

### 最终边界

第二套 smoke 工程协议有效、安全硬门禁全部通过，但诊断正确性为 `0/8`。审计未发现需要继续修复的确定性产品缺陷；剩余失败表现为当前模型在冻结预算下的工具选择、复合 intent 遵循和结构化终态能力不足。不得以额外重试、JSON 修补、放宽预算或按测试场景调参把这些失败改写为成功。M7 仍不能标记为 smoke 通过，也不能开始 M8 或正式 94-run benchmark。

## 2026-08-31 二次只读审计补记

二次审计重新对照 Task 6/7 的模型决策合同、八格 trace 和当前 runner，确认一项此前遗漏的确定性产品缺陷：Static 策略直接把包含 `MODEL_ERROR` 的公共 `Diagnosis` 暴露为模型输出 Schema，但同一 runner 又规定 `MODEL_ERROR` 只能由 controller 生成并拒绝模型输出该状态。三个 Static 失败格都在 `final_result` Schema/决策校验处终止，但 artifact 未保存被拒绝的原始模型载荷，因此不能断言三个失败均由该缺陷造成。

最小修复新增私有 Static decision Schema，只允许 `CONFIRMED`、`INSUFFICIENT_EVIDENCE` 和 `NO_INCIDENT`，最终仍使用既有公共 `Diagnosis`。修复没有改变六工具、8/8/2/300、evaluator、场景、artifact Schema 或安全门禁。离线 RED/GREEN、全量 unit `141 passed`、Ruff、lock 和 diff check 已通过；本轮未执行任何新的真实模型调用。故本补记修正“未发现确定性产品缺陷”的旧判断，但不改变原 smoke 的 `0/8` 事实，也不把 M7 标记为 smoke 通过。

## 2026-08-31 第三套授权的精确八格

### 数据库诊断与执行边界

- Docker Engine：`29.4.3`；项目 PostgreSQL：`running/healthy`，`pg_isready` 为 accepting connections。
- 服务端证据：直接 SQL round-trip、`dbt debug` 连接检查均通过；数据库集成用例的一次受控复验为 `1 passed in 24.60s`。
- 本机不稳定证据：同一诊断窗口出现 dbt 子进程 `3221225477 / 0xc0000005`、dbt 异常清洗遍历 `os.environ.items()` 时的 `ValueError: too many values to unpack`，以及无模型 policy matrix 主进程在 Pydantic schema 生成处的 `0xc0000005`。Windows Application log 记录了对应 `python.exe` access violation；不据此修改产品或加入平台 workaround。
- 代码身份：artifact 记录 `code_revision=4b2c1d4a670a81d670a725b85a1d65ca31b9cb0b`、`workspace_dirty=true`。工作区包含此前获准的 Static decision Schema 修复，尚未 commit。
- 模型身份：`openai-compatible` / `mimo-v2.5` / `https://api.xiaomimimo.com/v1`；仅使用进程级临时配置，结束后全部恢复。
- 唯一命令：`uv run pytest tests/e2e/test_real_model_m7_smoke.py -m real_model -q -s`。
- pytest：`4 passed, 4 failed in 402.24s`；harness 只启动一次，没有 rerun、补位或第九格。

### 八格结果

| # | 策略 | 场景 | run_id | 工作流/诊断结果 | artifact |
| ---: | --- | --- | --- | --- | --- |
| 1 | `STATIC_SKILL` | `schema_type_change_payment_amount` | — | `INITIAL_RESET_FAILED`，未进入模型路径 | — |
| 2 | `STATIC_SKILL` | `schema_type_change_order_customer_a` | `54c19fffe19b4682adf20fcb9bae77f1` | `MODEL_ERROR: MODEL_TOOL_CALL_LIMIT`；evaluator `FAILED` | 六文件 |
| 3 | `STATIC_SKILL` | `schema_type_change_order_customer_b` | `50217be4e196474a8f79421a6c611524` | `MODEL_ERROR: MODEL_TOOL_CALL_LIMIT`；evaluator `FAILED` | 六文件 |
| 4 | `STATIC_SKILL` | `order_volume_pattern_a` | `3ea05dd5ebe94f11b9566f81c9da264d` | `ARTIFACT_WRITE_FAILED`；私有 verification 为 `HEALTHY_CONTROL` | 未发布 |
| 5 | `DIAGNOSTIC_KERNEL` | `schema_type_change_payment_amount` | `835915a89d9c4c6c85818ccbba4aae74` | `MODEL_ERROR: MODEL_REQUEST_LIMIT`；evaluator `FAILED` | 六文件 |
| 6 | `DIAGNOSTIC_KERNEL` | `schema_type_change_order_customer_a` | — | `INITIAL_RESET_FAILED`，未进入模型路径 | — |
| 7 | `DIAGNOSTIC_KERNEL` | `schema_type_change_order_customer_b` | — | `INITIAL_RESET_FAILED`，未进入模型路径 | — |
| 8 | `DIAGNOSTIC_KERNEL` | `order_volume_pattern_a` | `100cad3bfc334a898ea6af9a0d6ab39e` | `MODEL_ERROR: MODEL_REQUEST_LIMIT`；evaluator `FAILED` | 六文件 |

三格在初始 reset 前终止，因而没有模型请求或 run_id；其余五格进入诊断路径。四个正式 bundle 均精确包含 `metadata.json`、`trace.jsonl`、`evidence.json`、`diagnosis.json`、`evaluation.json`、`report.md`。没有第九个 cell，也没有用失败格替换样本。

### 四个正式 bundle

| run_id | 请求 | 工具尝试/成功 | recovery | 主要失败 |
| --- | ---: | ---: | --- | --- |
| `54c19fffe19b4682adf20fcb9bae77f1` | 4 | 6 / 6 | `FAILED` | `MODEL_TOOL_CALL_LIMIT` 与正确性/证据门禁 |
| `50217be4e196474a8f79421a6c611524` | 4 | 8 / 8 | `HEALTHY` | `MODEL_TOOL_CALL_LIMIT` 与不足证据门禁 |
| `835915a89d9c4c6c85818ccbba4aae74` | 8 | 7 / 5 | `HEALTHY` | `MODEL_REQUEST_LIMIT` 与正确性/证据门禁 |
| `100cad3bfc334a898ea6af9a0d6ab39e` | 8 | 8 / 3 | `FAILED` | `MODEL_REQUEST_LIMIT`、健康主张与 recovery 门禁 |

四个 evaluator 均为 `FAILED`，没有诊断质量通过格。两次 recovery 失败与三次 reset 失败发生在同一 Windows 原生不稳定窗口；smoke 后直接 SQL 复核 `raw_customers=100`、`raw_orders=99`、`raw_payments=113`、三个 staging 关系以及 `customers=100`、`orders=99`，raw 列类型也恢复为健康基线。数据库当前状态健康不改写各格已经记录的 recovery 结果。

### `ARTIFACT_WRITE_FAILED` 的产品根因与最小修复

第三套 smoke 暴露了一个与 Windows 崩溃独立的确定性产品缺陷。私有 `_StaticDecision` 的合法输出被直接保存在公共 `DiagnosisRunResult` 中；ArtifactWriter 将 JSON 按公共 `Diagnosis` 反序列化后执行严格等值校验，而 Pydantic 的不同模型类型即使字段完全相同也不相等。最小复现稳定得到：

`Diagnosis(...) != _StaticDecision(...)`

这会使任何合法 Static 决策在 artifact round-trip 阶段失败，解释了 `3ea05d...` 已完成健康控制验证、却没有正式 bundle 的现象。修复只在 runner 输出边界执行一次公共 `Diagnosis` 投影，不放宽 ArtifactWriter、不改公共 Schema、不补写旧 artifact，也不调用模型。

验证结果：新增回归先 RED，随后 focused `1 passed`；runner/artifact/evaluation-runner 相关测试 `15 passed`；完整 unit `142 passed`；`uv run ruff check .`、`uv lock --check`、`git diff --check` 通过。

### 最终边界

第三套 smoke 的诊断质量为 `0/8`，并包含 3 个 initial reset hard failure 与 1 个 artifact hard failure，不能标记通过。最小 artifact 投影修复发生在 smoke 之后，因此当前最终工作区尚无对应的真实模型证据；严格遵守一次性授权，不重跑、不补位。M7 仍不具备提交收口条件，不开始 M8，也不启动正式 94-run benchmark。

## 2026-08-31 artifact 投影修复后的第四套八格

### 授权与节流

用户将 M7 阶段 smoke 改为持续授权，但要求不得连续运行多套。本次仅用于验证第三套后新增的 artifact 类型投影修复；执行前完成确定性门禁，执行后只做审计和记录，没有继续启动第二套 harness。

- PostgreSQL：`running/healthy`，关系检查为 `8 / 100 / 99 / 113`。
- 完整 unit：`142 passed`。
- harness collection：精确 8 项。
- 模型：`openai-compatible` / `mimo-v2.5` / `https://api.xiaomimimo.com/v1`。
- 代码身份：`4b2c1d4a670a81d670a725b85a1d65ca31b9cb0b`，`workspace_dirty=true`，包含 Static decision Schema 与公共 Diagnosis 投影两项未提交修复。
- 唯一命令：`uv run pytest tests/e2e/test_real_model_m7_smoke.py -m real_model -q -s`。
- pytest：`5 passed, 3 failed in 374.42s`；没有 rerun、补位或第九格。

### 八格结果

| # | 策略 | 场景 | run_id | 工作流/诊断结果 | artifact |
| ---: | --- | --- | --- | --- | --- |
| 1 | `STATIC_SKILL` | `schema_type_change_payment_amount` | `1bc56ae93f5443cd8d2f9dee4fc03123` | `CONFIRMED`；evaluator `FAILED` | 六文件 |
| 2 | `STATIC_SKILL` | `schema_type_change_order_customer_a` | — | `INITIAL_RESET_FAILED`，未进入模型路径 | — |
| 3 | `STATIC_SKILL` | `schema_type_change_order_customer_b` | `59838233013c4f88bb16c320276c12ce` | `BUILD_FAILED`，未进入模型路径 | 仅 lab run |
| 4 | `STATIC_SKILL` | `order_volume_pattern_a` | — | `INITIAL_RESET_FAILED`，未进入模型路径 | — |
| 5 | `DIAGNOSTIC_KERNEL` | `schema_type_change_payment_amount` | `8dfc4553827d48f5afa956893109fabe` | `MODEL_ERROR: MODEL_REQUEST_LIMIT`；evaluator `FAILED` | 六文件 |
| 6 | `DIAGNOSTIC_KERNEL` | `schema_type_change_order_customer_a` | `d03a364b13724aadbf9cd0e42a7c24df` | `MODEL_ERROR: MODEL_REQUEST_LIMIT`；evaluator `FAILED` | 六文件 |
| 7 | `DIAGNOSTIC_KERNEL` | `schema_type_change_order_customer_b` | `9aa543a0ab1542e99dcc9a9845bd870f` | `MODEL_ERROR: MODEL_REQUEST_LIMIT`；evaluator `FAILED` | 六文件 |
| 8 | `DIAGNOSTIC_KERNEL` | `order_volume_pattern_a` | `21dc2aaa2ffa4042a97f1ef5d280c7dc` | `MODEL_ERROR: MODEL_PROTOCOL_ERROR`；evaluator `FAILED` | 六文件 |

三格在诊断前终止，没有模型请求。其余五格均发布规范六文件，且 recovery 全部为 `HEALTHY`。

### 五个正式 bundle

| run_id | 请求 | 工具尝试/成功 | recovery | evaluator 结果 |
| --- | ---: | ---: | --- | --- |
| `1bc56ae93f5443cd8d2f9dee4fc03123` | 7 | 6 / 6 | `HEALTHY` | `FAILED` |
| `8dfc4553827d48f5afa956893109fabe` | 8 | 8 / 3 | `HEALTHY` | `FAILED` |
| `d03a364b13724aadbf9cd0e42a7c24df` | 8 | 8 / 5 | `HEALTHY` | `FAILED` |
| `9aa543a0ab1542e99dcc9a9845bd870f` | 8 | 8 / 3 | `HEALTHY` | `FAILED` |
| `21dc2aaa2ffa4042a97f1ef5d280c7dc` | 4 | 1 / 0 | `HEALTHY` | `FAILED` |

Static/payment 是本项目首次在修复后的私有 Static decision Schema 下得到合法 `CONFIRMED` 并成功发布 artifact 的真实样本。其 controller hash 为 `b0307abb7cae26ba8f78ef9b34493d5f191fe8380860e7c7b67256983ca3832a`；六文件严格校验通过，证明 `_StaticDecision` 到公共 `Diagnosis` 的投影修复已覆盖真实模型路径，第三套的 `ARTIFACT_WRITE_FAILED` 未复现。

该诊断仍未通过质量验收：模型给出 `SCHEMA_TYPE_MISMATCH`，而场景合同要求 `SOURCE_SCHEMA_COLUMN_TYPE_CHANGED`；模型把 `raw_payments` 当作 affected asset，漏掉 `orders`、`customers`，并未提供要求的 lineage 证据。因此 `ROOT_CAUSE_ACCEPTED`、`AFFECTED_ASSETS_EXACT`、`REQUIRED_EVIDENCE_TYPES_PRESENT` 和 `CLAIM_EVIDENCE_COMPATIBLE` 正确 fail closed。这里没有放宽 evaluator 或改写模型结果。

### 环境与停止边界

本次窗口 Windows Application log 记录两次 `python.exe 0xc0000005`；Static/customer B 的 dbt stdout 在 adapter 注册后中断，与已知本机 Python/dbt 原生不稳定一致。Smoke 后 PostgreSQL 仍为 `running/healthy`，八个关系行数和 `raw_payments.amount=integer` 均回到健康基线；临时模型变量、`DBT_PARTIAL_PARSE` 与 affinity 已恢复。

第四套仍是诊断质量 `0/8`，但确认 artifact 投影产品缺陷已修复。未发现新的确定性产品缺陷，不连续运行第五套，不 commit/push，不开始 M8 或正式 94-run benchmark。

## 2026-08-31 Static 声明合同与恢复修复后的第五套八格

### 前置与唯一执行

本套验证两项确定性修复：Static Skill 补齐双方共享的 M7 root-cause ontology 与 direct/downstream affected-asset 语义；Lab recovery 在 partial reset 已恢复健康 Schema 时跳过重复反向 mutation，同时继续拒绝未知漂移。

- 完整 unit：`144 passed`；完整 integration：`11 passed`；M7 FunctionModel 精确矩阵：`8 passed`。
- `uv run ruff check .`、`uv lock --check`、`git diff --check`：通过。
- PostgreSQL：`running/healthy`；八个关系行数为健康基线，`raw_orders.user_id` 与 `raw_payments.amount` 均为 `integer`。
- harness collection：精确 8 项；`MIMO_API_KEY` 只检查存在性，模型固定为 `mimo-v2.5`。
- 代码身份：`4b2c1d4a670a81d670a725b85a1d65ca31b9cb0b`，`workspace_dirty=true`；Static prompt 为 `p1.static.v2`。
- 唯一命令：`uv run pytest tests/e2e/test_real_model_m7_smoke.py -m real_model -q -s`。
- pytest：`8 passed in 774.29s`；只启动一次完整 harness，没有 rerun、补位、第九格或连续第二套。

### 八格结果

| # | 策略 | 场景 | run_id | 诊断结果 | evaluator |
| ---: | --- | --- | --- | --- | --- |
| 1 | `STATIC_SKILL` | `schema_type_change_payment_amount` | `1a1a8a4dd5124fda9743546e9b59b556` | `CONFIRMED: SOURCE_SCHEMA_COLUMN_TYPE_CHANGED` | `PASSED` |
| 2 | `STATIC_SKILL` | `schema_type_change_order_customer_a` | `00e777c26c2d4825b2dd1a3c3dee3452` | `MODEL_ERROR: MODEL_TOOL_CALL_LIMIT` | `FAILED` |
| 3 | `STATIC_SKILL` | `schema_type_change_order_customer_b` | `e42e51a9853249a58adc39c3c4c9a872` | `MODEL_ERROR: MODEL_TOOL_CALL_LIMIT` | `FAILED` |
| 4 | `STATIC_SKILL` | `order_volume_pattern_a` | `9cd3b9c3e840440c8e2fa9d672747299` | `MODEL_ERROR: MODEL_PROTOCOL_ERROR` | `FAILED` |
| 5 | `DIAGNOSTIC_KERNEL` | `schema_type_change_payment_amount` | `fa2bb6bec0ac48618cf9c00ff49e1563` | `MODEL_ERROR: MODEL_PROTOCOL_ERROR` | `FAILED` |
| 6 | `DIAGNOSTIC_KERNEL` | `schema_type_change_order_customer_a` | `80a1bd65c0f34c6397b54536b44405d0` | `MODEL_ERROR: MODEL_PROTOCOL_ERROR` | `FAILED` |
| 7 | `DIAGNOSTIC_KERNEL` | `schema_type_change_order_customer_b` | `57d243b57653437a9b96e99a07528571` | `MODEL_ERROR: MODEL_REQUEST_LIMIT` | `FAILED` |
| 8 | `DIAGNOSTIC_KERNEL` | `order_volume_pattern_a` | `9276862afffd4e54ba265fc0faa29410` | `MODEL_ERROR: MODEL_REQUEST_LIMIT` | `FAILED` |

8/8 格均发布规范六文件，recovery 全部为 `HEALTHY`。诊断质量为 `1/8`，不能把 pytest 的 `8 passed` 误述为八个诊断通过。

### 首个 Static 质量通过格

`1a1a...` 使用 `p1.static.v2`，在 6 次模型请求中执行 6 个工具尝试、5 个成功调用。它给出合同内根因 `SOURCE_SCHEMA_COLUMN_TYPE_CHANGED`，affected assets 精确为直接失败节点 `model.jaffle_shop.stg_payments` 与 downstream lineage 返回的 `model.jaffle_shop.customers`、`model.jaffle_shop.orders`，并引用 node error、schema 与 downstream lineage 的兼容 Evidence IDs。全部 evaluator 检查及 recovery 均通过。

这与第四套 `1bc56a...` 的同一场景形成直接对照：此前模型使用 `SCHEMA_TYPE_MISMATCH`、把上游 `raw_payments` 当 affected asset 且漏掉 downstream lineage；本套在通用提示合同修复后通过。该证据只证明修复覆盖了一个 development smoke 样本，不构成正式 benchmark 结果或普遍准确率结论。

### 七个失败格与停止边界

- Static/customer A 与 B 都在 7 条已执行 business-tool trace 后提交使批次超过 8-call 上限的下一批调用，框架按批次原子拒绝并返回 `MODEL_TOOL_CALL_LIMIT`。本地 PydanticAI 实现确认 final-result output tool 不计入该上限，因此不是 7+1 off-by-one。
- Static/control 已取得 run/profile/history 正向证据，随后请求不可观测 relation schema，得到 `RELATION_NOT_ALLOWED`，最终结构化输出被拒并安全映射为 `MODEL_PROTOCOL_ERROR`。
- Kernel/payment 的证据调查已推进，但最终缺少合同要求的替代假设；Kernel/customer A 在 intent shape 失败；customer B/control 在公开的 intent JSON/shape 协议上耗尽请求。controller 均按已公开合同 fail closed。
- smoke 后 PostgreSQL 仍为 `running/healthy`，八个关系与关键 raw 类型均为健康基线；临时模型变量、`DBT_PARTIAL_PARSE` 和 affinity 已恢复。smoke 窗口未记录新的 `python.exe` Application Error。

未基于上述模型样本继续调整预算、调查顺序、Schema、controller 或 evaluator，也未启动第六套 smoke。M7 real-model smoke 的当前质量仍为 `1/8`，不具备“smoke 已通过”的收口条件；不 commit/push，不开始 M8 或正式 94-run benchmark。
