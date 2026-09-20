# p1-formal-v27 第三次预检失败归因报告（2026-09-20）

- 授权依据：所有者 2026-09-20"授权"（v27 一次预检；失败则读 diagnostic 归因）。
- **结论：预检失败，回执 diagnostic 成功归因——目录校验拒绝大写模型 ID；benchmark run 未启动；
  v27 回执原样保留；completion 请求 0 次。历史"归因缺口"就此闭合。**

## 1. 预检结果

环境面 10 项全部通过（同上一轮）；模型面 3 项失败，回执携带：

```
MODEL_ENDPOINT | stage=catalog_list;kind=MALFORMED:entry_id_unsafe;exc=_CatalogResponseError;timeout_ms=5000;elapsed_ms=1327
```

归因链完整：目录 GET 成功（1.3 秒，排除 TIMEOUT / CONNECTION_ERROR / HTTP_*），失败发生在
**响应校验层**——目录中存在不满足 `_SAFE_MODEL_NAME`（`^[a-z0-9][a-z0-9._:/-]{0,127}$`）的条目。

## 2. 只读目录分类确认（目录级 GET，无 completion）

71 个条目中 **26 个含大写字母**（厂商前缀风格，如 `moonshotai/Kimi-K3`、`zai-org/GLM-5.3`、
`Qwen/...` 类），不满足小写限定正则；目标模型 `deepseek/deepseek-v4.1-flash` 在列且本身合法。
doctor 对目录**逐条目**校验，任一非法即整体判 MODEL_ENDPOINT 失败 → MODEL_PRESENT 与结构化探针级联。

## 3. 历史失败重新归因（以本结论回溯）

| 预检 | 传输层 | 校验层 | 真实原因 |
| --- | --- | --- | --- |
| v25（urllib 探针） | Cloudflare 按 UA 403 | 未到达 | 传输封锁（当时已归因） |
| v26（SDK 探针） | 成功 | **entry_id_unsafe** | **与同一校验缺陷一致，尚不能逐次确证**——v26 未保留当次响应或异常证据；已确证的是当时独立复现漏测了校验层（只测 `_models_list` 传输层，未跑 `_endpoint_check`），v27 的 diagnostic 给出了该缺陷的决定性证据 |
| v27（SDK 探针 + diagnostic） | 成功 | **entry_id_unsafe** | 本文归因 |

"三次目录成功不能反推预检当时失败原因"的判断完全正确；可诊断性优先于重试的裁定被结果验证。

## 4. 待所有者裁定（代码修改 → 按流程审计/提交/冻 v28 → 申请新预检）

目录条目在检查外的暴露面：`model_ids` 仅用于绑定模型名的成员判断，`observed` 仅在通过时显示
manifest 绑定的模型名；目录 ID 从不序列化、从不落盘。`_SAFE_MODEL_NAME` 对全部条目的校验属于
超出实际需要的防御。两个修复方向：

1. **（推荐）校验收窄到绑定模型**：仅要求 `settings.model_name`（由 v28 manifest 绑定为
   deepseek/deepseek-v4.1-flash）满足安全正则且在目录中；其余条目不逐个校验，仅在 diagnostic 中
   记录 `unsafe_entries=<计数>`（只计数，不记录名称）。最小改动，防御目的（绑定名安全）不变，
   与第三方目录的现实（大写厂商前缀合法存在）一致。
2. **放宽正则至大写**（`[A-Za-z0-9]`）：改动更小，但对所有条目放宽守卫。

两案均不加重试、不放宽超时、不改请求上界；v25–v27 三份失败回执继续原样保留。
