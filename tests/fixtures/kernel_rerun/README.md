# kernel_rerun fixtures:seq50/59/67 公开证据(修复计划 Task 1)

来源:2026-09-09 p1-formal-v8 rerun smoke(run manifest SHA-256
`b6978f16c50f2f5f5c87df6f3cc736cfa0e3a1aeeb881ac6cfded63513ed0a2b`,
code revision `af8a85535ad5ced06bcf6c532ec0945d2cdf1239`——冻结 v8 rerun manifest
的提交,其父提交 `815469ee43763b191bcae7dd37edde3175e8c5bf` 是诊断实现本身;
运行时的 kernel prompt `p1.kernel.v10`、controller `p1.controller.v9`)。

原始产物目录:`C:/Users/29913/.config/superpowers/worktrees/DataIncidentGym/p1-v8-kernel-rerun-20260909/artifacts/`(该 worktree 只读;测试不得依赖其存在)。本目录为独立副本。

## 文件与角色

| 文件 | 来源 run | 案例 | 角色 | SHA-256 |
| --- | --- | --- | --- | --- |
| seq50_evidence.json | `870de53c…84e7` | silent_payment_drop_partition_a | 合成合法决策/漏引对照 | `7f9a0cbd21dc1d098704be20bfe234234d37ba05d71f2c8c776663009bb5de85` |
| seq50_brief.json | 同上 | 同上 | subjects + 四件公开观察 | `73d3d7da6374c9304a26561df3f9bbe287fe4f15038f20dbfd269bcedef1054c` |
| seq50_diagnosis.json | 同上 | 同上 | 原始 MODEL_ERROR/MODEL_TIMEOUT 终态原样读取 | `732434c0d00d6033a1173b886d6349aa2b9945602e73685d279b6b5fd6c4f394` |
| seq59_evidence.json | `d2abcf13…89e` | schema_type_change_order_customer_b | 缺 history 对照 | `19d49b5a9f53c957e38c4ea1cb86ce5725728a503adbb5b3bfe8d03894ebb736` |
| seq59_brief.json | 同上 | 同上 | subjects + DBT_MODEL_FAILURE | `5d72dc24cf81f993b6574d1e3cfa4223f02b81733e33f1d02e1cac2b9361d4bf` |
| seq67_evidence.json | `c9f54f9e…fb7` | duplicate_payment_coupon_b | 声明集对照 | `cbb12c135bc559a67cc911e3b1f05d04bb00c253ffc57345fee3c0f447c93657` |
| seq67_brief.json | 同上 | 同上 | subjects + retry alert | `2468a4060e4e16280f0bc92e06c3e82a2f4f7ac87ac17b8467cef6a0cf8fdd76` |

证据记录为原六文件 evidence.json 的 `records` 数组(EvidenceRecord 模型可直接
`model_validate`),含 `content_digest`;保留原 run_id 与 evidence_id 以便 kernel
run-scope 校验一致。文件只含公开聚合证据与 brief,不含模型请求内容、日志、
scenario 私有答案或任何凭证。seq59 对照中的"补 history"记录按需从 seq50 的
raw_orders history 记录派生:先 `model_copy(update={"run_id": …})` 改写内容,
再用 `EvidenceRecord.create` 重建身份,因此 `content_digest` 与 `evidence_id`
都随 run_id 改变——不是保留原 digest 的复制。

seq50 的合成决策由测试显式构造并标注为合成(不是恢复出的模型响应);原始
MODEL_ERROR 终态由"原样读取"测试保留在既有产物中,不在本目录复制六文件全集。
