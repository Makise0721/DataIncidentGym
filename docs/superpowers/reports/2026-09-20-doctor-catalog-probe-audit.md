# doctor 目录探针改用 OpenAI SDK 的实施与审计报告（2026-09-20）

- 授权依据：所有者 2026-09-20 裁定 R3——"优先复用 OpenAI SDK 的 `models.list()`，让目录探针与实际
  模型客户端使用相同的端点、鉴权和网络路径，保留超时、禁用自动重试及模型存在性检查。补离线回归后
  单独提交，再冻结 v26；不修改检出门。"
- 改动文件（未含其他）：`src/data_incident_gym/doctor.py`、`tests/unit/test_doctor.py`。

## 1. 实现

- `DoctorRunner` 依赖注入：`url_open`/`UrlOpen` 移除，新增 `models_list`/`ModelsList`
  （`Callable[[], Awaitable[Iterable[Any]]]`）；urllib 导入与 `_MAX_RESPONSE_BYTES` 一并移除（零残留）。
- `for_project`：目录探针接线为 `provider.client.models.list(timeout=_URL_TIMEOUT_SECONDS)`——与
  结构化输出探针**同一个 AsyncOpenAI 实例**（同 base_url、同密钥、同 HTTP 栈），该 client 已设
  `max_retries = 0`（无自动重试）；超时保持 `_URL_TIMEOUT_SECONDS = 5`。
- `_endpoint_check` 改 async：`await` 得到首页（`AsyncPaginator.__await__` = 单次 GET，API 层无分页，
  与旧 urllib 单响应语义等价）；`page.data` 必须为 list，逐条目校验 `id` 为字符串且匹配
  `_SAFE_MODEL_NAME`；任何异常 fail-closed 为 UNAVAILABLE/空集；MODEL_PRESENT 与探针级联不变。
- 鉴权头由旧 urllib 的 `api-key` 变为 SDK 的 `Authorization: Bearer`——与模型流量一致，属"同路径"
  的本意（审计确认项）。
- `_verify_checkout`、manifest/身份代码零改动。

## 2. 审计（独立代理，结论：通过，无阻塞项）

关键验证：同 client 证明（唯一 `OpenAIProvider`，lambda 与 `OpenAIChatModel` 共享 `provider.client`）；
请求计数上界 = 目录 GET 恰 1 次 + 探针 ≤2 次 POST（`max_retries=0`，无循环）；fail-closed 与校验对等
（畸形响应不可能以 REACHABLE 通过；空目录 → MODEL_PRESENT 失败，与旧版逐位对等）；异步无嵌套循环；
`run()` 在 CLI（`asyncio.run`）与 benchmark runner（awaitable 处理）两条路径均正确。

| # | 级别 | 发现 | 处置 |
| --- | --- | --- | --- |
| 1 | MINOR | 5 秒目录超时未被测试钉住 | 已修复：接线测试断言 `probe._options.timeout == _URL_TIMEOUT_SECONDS` |
| 2 | MINOR | 非 list `data`、目录 GET 失败时 `run()` 的模型面三级级联未测 | 已修复：新增 `test_endpoint_check_rejects_non_list_catalog_data` 与 `test_run_cascades_model_checks_when_catalog_get_fails`（403 GET → 仅 1 次 GET，三项模型检查全 UNAVAILABLE） |
| 3 | MINOR | 1 MiB 响应体积上限随 urllib 移除 | 接受并记录：SDK 解析 + 已认证端点 + 条目校验 fail-closed，实际风险低 |
| 4 | NOTE | 鉴权头 api-key → Bearer | 记录为"同路径"本意（设计事实） |

## 3. 测试（12 项 doctor 单测，全离线）

新增/更新：目录成功 + 在列、传输异常 fail-closed + 级联、不安全条目拒绝、非 list data 拒绝、
for_project 接线（awaitable + `max_retries == 0` + 5 秒超时）、同一共享 client 的 GET→POST 顺序、
目录 GET 403 时 `run()` 模型面三级全失败且仅 1 次 GET。

## 4. 验证（全部离线）

```powershell
uv run ruff check .        # All checks passed!
uv run pytest tests/unit -q  # 1016 passed, 5 skipped
uv lock --check             # 通过
git diff --check            # 通过
```

无网络调用、无数据库操作、无 doctor 对真实端点的执行。按裁定"补离线回归后单独提交"，本报告与
v26 冻结另行按序执行。
