# 目录探针失败可诊断性与 p1-formal-v27 冻结报告（2026-09-20）

- 授权依据：所有者 2026-09-20 裁定——本轮停在"模型目录检查失败，原因未确定"；先补**失败可诊断性**，
  不直接申请第三次原样预检；修改代码则按既有流程审计、提交、冻结新身份，再申请新预检；
  不加重试、不放宽超时；v25/v26 失败回执继续原样保留。

## 1. 离线检查结论：现有回执确实无归因信息

- v26 回执失败项仅含 `code/observed/passed/reason_code/recommendation_code` 五字段，
  `observed` 被 `_check` 清洗为 `UNAVAILABLE`；无异常类型、无 HTTP 状态、无耗时。
- worktree `artifacts/` 下无任何日志文件。doctor 的 `except Exception` 静默吞掉异常。
  ——这是本次最小代码修改的依据，已记录在案。

## 2. 实现（提交 `6d3e76272313827d7d72806b731c055b4765fbc7`，单独提交）

- `DoctorCheck` 新增可选 `diagnostic: StrictStr | None = None`（校验器禁止通过项携带）；
  仅 `_endpoint_check` 失败路径填充，其余检查恒为 None。
- 摘要格式（确定性、按构造脱敏）：
  `stage=catalog_list;kind={TIMEOUT|CONNECTION_ERROR|HTTP_<status>|MALFORMED:<stage>|ERROR};exc=<类名>;timeout_ms=5000;elapsed_ms=<int>`。
  分类顺序：`_CatalogResponseError`（MALFORMED:data_not_list / entry_id_type / entry_id_unsafe）→
  `APITimeoutError` → `APIConnectionError` → `status_code` 整数守卫 → ERROR 兜底。
  **绝不**包含异常消息、响应正文、鉴权头、URL；仅插值代码控制的字面量、类名、整数状态与计时。
- 行为不变式：不加重试（`max_retries` 仍 0）、不放宽超时（仍 5 秒）、请求次数上界不变、
  checkout 门与 manifest/身份代码零改动。
- 兼容性：加性可选字段，回执 schema 保持 `p1.benchmark_doctor.v3`；历史回执（无该字段）仍可解析；
  pydantic 等值语义下"缺失 == None"，既有回执比较行为不变；v25/v26 磁盘回执不受影响。
- `docs/requirements.md` 追加 M21 修订留痕。

## 3. 审计（独立代理，结论：PASS WITH FINDINGS，无阻塞项）

逐条核验：脱敏按构造成立（逐 f-string 组件）；分类顺序必需且正确（APITimeoutError 是
APIConnectionError 的子类，必须先判）；生产异常形态（SDK 包装）全部落入前三类；
CancelledError 等不捕获属正确行为；新旧回执解析与等值比较经实验确认。处置：

| # | 级别 | 发现 | 处置 |
| --- | --- | --- | --- |
| 1 | MINOR | kind=ERROR 兜底分支未测试、未文档化（裸 httpx2 异常仅注入 fake 可达，生产已包装） | 已修复：新增回退分支测试 + docstring 说明 |
| 2 | NOTE | APIResponseValidationError（2xx 体非法）记为 HTTP_2xx 而非 MALFORMED | 接受：exc 类名仍可归因 |
| 3 | NOTE | 独立 `doctor` CLI 输出不含 diagnostic（仅回执承载） | 知情接受：符合"最小"范围 |
| 4 | NOTE | 序列化工件变更未在 requirements.md 留痕 | 已修复：M21 追加 |
| 5 | NOTE | status_code=True 理论上产生 HTTP_True 等 | 接受：生产不可达 |

## 4. 离线夹具（可区分性证明）

超时（APITimeoutError→TIMEOUT）、连接错误（APIConnectionError→CONNECTION_ERROR）、
HTTP 401/403/429/500/503（APIStatusError→HTTP_<status>）、响应结构错误（三阶段 MALFORMED）、
未知异常（→ERROR 兜底）、脱敏（投毒消息不泄漏）、计时字段（timeout_ms=5000、elapsed≥0）、
通过项无字段、validator 不变式、经真实 SDK + MockTransport 的端到端 403 落盘、
新旧回执解析与 round-trip。

## 5. 验证与 v27 冻结

门禁：`ruff check .` / `pytest tests/unit -q`（**1024 passed, 5 skipped**）/ `uv lock --check` /
`git diff --check` 全绿；无网络、无数据库、无 doctor 对真实端点执行。

| 提交 | SHA | 内容 |
| --- | --- | --- |
| 修复 | `6d3e7627…` | 诊断记录 + 测试 + M21（单独提交） |
| 批准（= v27 implementation_revision） | `69bf86f764d3fdf5457fd116b08c473c8671cfd5` | `feat: approve p1-formal-v27 benchmark identity` |
| 冻结 | `4f382c384d315bd997eec91e2ae0b5379a61e0cc` | `chore: freeze p1-formal-v27 benchmark manifest`（仅清单） |

v27：sha256 **`170578359b2953f1cbe980d88d791d5348b384c617a7a7fc62d021f30dbc0006`**，verify 通过，
106/94/12 不变。归一化对比 v27 vs v26：非 cell 差异恰为 manifest_id、implementation_revision；
cells 除 run_id 外 0 差异；其余全部字段相等。B→C 差异恰为清单路径（C27 干净检出自带检出门通过）。
v26 清单与两份失败回执（v25/v26）原样保留。

## 6. 申请

请求所有者授权**一次新的预检（v27）**：
`benchmark preflight --manifest config/benchmark/p1-formal-v27.json --confirm-sha256 17057835…0006`。
执行环境与 launcher 不变（worktree 前移到 `4f382c3` 后执行）。若模型面再失败，回执将携带
脱敏 diagnostic，可据此归因（TIMEOUT / CONNECTION_ERROR / HTTP_<status> / MALFORMED）；
若通过且费用条件满足，按既定放行进入一次完整 run。模型工具调用能力（结构化探针）仍未验证，
以本次预检的实际结果为准。
