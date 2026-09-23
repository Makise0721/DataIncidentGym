# v31 真实测量 preflight 失败报告(2026-09-23,已按纪律停止)

- 授权范围:固定 `91582e5` 干净 worktree 核对 → 一次 preflight(失败即停)→ 通过则一次
  suite(滚动停止)→ 核对与报告。**preflight FAILED,started_cells=0,suite 未启动**,
  本报告即停点产物。重试需新的明确授权;本文如实记录全部环境事件,无静默重试。

## 1. 执行环境与核对(通过)

- worktree:`../v31-exec-worktree` @ `91582e5`(detached,porcelain 干净),
  submodule `36bde6c`,`uv sync --frozen` 完成。
- 清单摘要:sha256 == `f4196010e0b8ff3fdd9fdf26f2c877bb04bd4d9cb4db5d612a13541fca2b4c9a`(一致)。
- 密钥:User 作用域回退读取(长度 93,值不落盘/不输出/不入库)。
- 数据库基线:`pipeline build` 成功,指纹恒等
  `e5c7848eb2b7af16ec37463650b63dbb5d1f52293adf456003517b51c496cb18`(F0)。
- **环境事件(如实记录)**:核对过程中 Docker Desktop 引擎停止(npipe
  `dockerDesktopLinuxEngine` 不可见,早前 `docker ps` 尚能列出 PG 容器),启动
  Docker Desktop 后引擎恢复(容器被 harness compose 流程重新拉起),重跑
  `pipeline build` 通过。属本机环境事件,非产品行为。
- **装配失误(如实记录)**:首次 preflight 命令的密钥注入因 Git Bash→PowerShell
  引号转义失效,`DiagnosticSettings` 校验空 key 直接拒绝,**未发出任何请求**;
  修正注入后输出意外为空,经查无 ledger/归档/scoring-inputs 残留(零副作用)后
  重跑,得到下述真实 preflight 结果。两次未遂 + 一次真实执行,全部如实记录。

## 2. preflight 结果(一次真实执行)

```
status: FAILED
doctor_status: FAILED
model_probe_required: True
receipt: artifacts/benchmarks/p1-formal-v31/doctor.json
started_cells: 0
```

receipt 全量检查表:

| 检查 | 结果 | 归因 |
| --- | --- | --- |
| PYTHON 3.12.10 / DOCKER 29.4.3 / COMPOSE_POSTGRES / PROFILE_SPEC / PROFILE_SNAPSHOT / PROFILE_BOUNDS | PASS | — |
| **MODEL_ENDPOINT / MODEL_PRESENT / MODEL_TOOL_STRUCTURED_OUTPUT** | **PASS** | **真实模型探针通过:端点可达、模型在列、工具调用 + 结构化输出验证成功(密钥通路正常)** |
| UV | FAIL | doctor 钉值 `_EXPECTED_UV = 0.11.24`,本机 `uv 0.12.9`。工具存在且全程健康(本会话所有命令均由其执行),纯版本钉值不匹配;`DoctorResult` 为 any-fail→FAILED,**独立硬门槛** |
| POSTGRES_CONNECTION | FAIL | 级联:`_has_explicit_diagnostic_database_config()`(doctor.py:347)要求 6 个 `DIG_DIAGNOSTIC_POSTGRES_*` 环境键显式存在,否则**不尝试连接**直接 UNAVAILABLE。主树靠 ignored 的 `.env.diagnostic` 提供;worktree 为干净检出,该文件不存在 |
| DBT_PROFILE_CONNECTION / PROFILE_READ_ONLY | FAIL | 同上,PG 检查失败的下游级联(未执行) |

反证(非数据库故障):同一 worktree 内 `pipeline build` 连库成功;以 doctor 完全
相同的参数手工执行 `_db_connect` + `SELECT 1` 成功;COMPOSE_POSTGRES PASS。

## 3. 结论与待裁定项

**preflight 失败的两个根因均在执行环境侧,不在被测实现**:① UV 版本钉值漂移
(硬门槛);② worktree 缺 ignored 本地配置文件导致数据库检查被守卫跳过。模型、
数据库、实现通路均被独立验证正常。本轮未回答「修复 lineage 候选转发并加入提交门
后真实运行表现如何」——suite 未启动。

重走 preflight→suite 需要以下裁定(均不动产品代码、不重冻;按用户此前裁定
「当前不需要再改代码或重冻」):

1. **UV**:环境侧将本机 uv 降级/并行安装 `0.11.24`(改 doctor 钉值属产品改动,
   会触发重冻,不推荐);或裁定接受暂缓测量。
2. **数据库配置**:以进程环境注入 6 个 `DIG_DIAGNOSTIC_POSTGRES_*` 键(不落盘,
   与密钥纪律同构);或经所有者确认后把主树 `.env.diagnostic` 复制进 worktree
   (该文件含 key,属落盘,需明示同意)。
3. **运行状态清理**:preflight receipt 已落盘(worktree
   `artifacts/benchmarks/p1-formal-v31/`),重跑被「preflight receipt already
   exists」拒绝,需删除该目录(untracked 运行产物,删除不影响冻结身份)。
4. 以上就绪后重新授权一次 preflight。

执行 worktree 保留待裁定(`../v31-exec-worktree`,HEAD `91582e5`);主分支本报告
提交不影响其检出门(所有者 2026-09-23 已澄清口径)。
