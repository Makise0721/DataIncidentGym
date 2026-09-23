# v31 preflight 第二次失败报告:环境修复生效,模型 key 装配失误(2026-09-23)

- 授权与执行:按所有者四项裁定(隔离 uv 0.11.24 / 数据库六键进程级注入 / 失败回执
  归档后腾路径 / 环境检查后一次 preflight,通过则一次 suite)。preflight 一次执行
  **FAILED**,按「失败即停」停止。suite 未启动。**重新授权前不重试。**
- 上游:`2026-09-23-v31-preflight-failed.md`(第一次失败 + 未遂调用定性更正)。

## 1. 环境修复执行(裁定 1–3,全部完成并验证)

1. **隔离 uv 0.11.24**:GitHub 直连与 PowerShell 下载均因当前网络 TLS 故障不可用,
   改由 **PyPI wheel**(`uv-0.11.24-py3-none-win_amd64.whl`)取得,解包置于
   `C:\Users\29913\tools-uv-0.11.24\uv.exe`(无全局改动、无 PATH 持久化)。
   排障记录:首置 `C:\Users\29913\.tools\…` 时 PATH 查找始终不命中——**MSYS/Git Bash
   的命令查找跳过点开头目录**,移至非点目录后解析正常(仅影响 bash 交互层;
   真实执行经 Python 子进程的原生 PATH 解析,已实测)。
2. **数据库六键进程级注入**:launcher(`v31-launch.py`,仓库外临时文件)从主树
   ignored 的 `.env.diagnostic` 读取六键写入子进程环境;不回显、未复制文件进
   worktree。
3. **失败回执归档**:第一份回执(doctor.json,sha256 `362b54d7…60ced`)归档至
   `C:\Users\29913\codex_space\v31-preflight-failed-20260923T1546\`,双侧摘要一致后
   腾出活动路径;本轮第二份回执(`ff1887fd…5037`)同法归档至
   `…\v31-preflight-failed-20260923T1628\`,活动路径已再次清空。

**launcher 内实际解析(裁定 1 要求确认)**:

```
uv: C:\Users\29913\tools-uv-0.11.24\uv.EXE   (uv 0.11.24)
python: 3.12.10   C:\Users\29913\codex_space\v31-exec-worktree\.venv\Scripts\python.exe
dbt:              C:\Users\29913\codex_space\v31-exec-worktree\.venv\Scripts\dbt.EXE
```

## 2. 重启核对链(通过)

HEAD `91582e5`(porcelain 0)、清单 sha256 `f4196010…` 一致、六键在 launcher 环境源
齐备、`pipeline build` 指纹恒等 F0 `e5c7848e…`。

## 3. preflight 结果(一次执行,07:28:54Z 回执)

**两项原始根因已修复**:UV PASS(observed 0.11.24)、POSTGRES_CONNECTION PASS
(CONNECTED)、DBT_PROFILE_CONNECTION PASS、PROFILE_READ_ONLY PASS——上一份报告的
失败面全部转绿。MODEL_ENDPOINT REACHABLE、MODEL_PRESENT `deepseek/deepseek-v4.1-flash`。

**唯一失败项:`MODEL_TOOL_STRUCTURED_OUTPUT`,kind=HTTP_401**(452ms,
model_requests=0——鉴权即拒,无计费消耗)。

## 4. 401 归因(执行侧装配失误,如实归属)

授权范围是**数据库六键**注入;launcher 第一版把 `.env.diagnostic` 的**全部九键**
(含 `DIG_DIAGNOSTIC_MODEL_BASE_URL/NAME/API_KEY`,即主树 mimo 配置)都注入了,
其中文件里的 **mimo 模型 key** 覆盖了 commandcode 端点所需的 User 作用域
`COMMANDCODE_API_KEY` → 工具探针对 commandcode 端点鉴权被拒(401)。昨天探针
全绿时的装配恰是「仅注入 User 作用域 commandcode key」,本轮偏离了该有效组合,
且超出了六键授权范围。**责任在执行侧环境装配,不在产品、清单或 provider。**

launcher 已修正为授权装配:仅注入六键;模型 key 取 User 作用域
`COMMANDCODE_API_KEY`;**不**注入文件的模型 base_url/name/key(端点与模型由冻结
清单接线)。

## 5. 当前状态与所需授权

- 执行 worktree(`91582e5`,porcelain 干净)与修正后的 launcher 就绪;活动 suite
  路径已清空;两份失败回执均已归档可审计。
- 需**重新授权一次 preflight**(装配修正后);通过则按原裁定执行一次 suite
  (滚动停止规则、恢复失败即停、无额外探针或补测)。
