# M12 Harness Postmortem

日期：2026-09-02
批次：`p1-formal-v1`
结论：`INVALID_HARNESS`

## 结论边界

`p1-formal-v1` 的 106 格正式批次已经执行过唯一一次，永久封存为
`INVALID_HARNESS`。本批次不重新运行、不重新冻结 Manifest，P1 不产生有效的真实模型质量结论。

本文件记录的是批次后的 harness 取证和修复依据，不是对旧结果的重算；后续代码修复不具追溯效力。

## 批次事实

- 总计 106 格：94 个 model-backed，12 个 fixed-rule。
- 98 格 `FAILED`，8 格 `COMPLETED`。
- 56 格 `RUN_SETUP_ERROR`，42 格 `EVALUATION_FAILED`。
- 41 个 model-backed 格实际到达 MIMO；53 个在 harness/setup 阶段失败；没有重试。
- 56 个 setup placeholder 错误地携带了诊断、证据、工具和 trace 的适用失败检查。
- 在实际执行的单元中没有未知 evidence ID，也没有实际工具越界或 trace 读写违规。
- 正式产物保持不变；修复前 `artifacts` 树 640 个文件的聚合 SHA-256 为
  `d6f94642d878e6501f0682c12fd4c65c3c5caffe3956b32c94779bccf6f4df1c`。

## 已确认的 harness 问题

1. Reporter 把 fixed-rule 的合法只读证据工具误当成策略越界；只有 `NO_TOOL` 应要求空工具集合。
2. `EVIDENCE_IDS_EXIST` 把空引用集合和悬空引用混为一谈，并未统一检查 claim 引用集合。
3. setup 失败物化时，除环境和恢复之外的检查被错误标成真实适用失败，掩盖了失败发生阶段。
4. 初始 reset 抛错时可能跳过恢复；恢复结果也没有传递到 benchmark runner。
5. benchmark runner 在 setup/harness 失败后继续启动后续格，存在无意义的预算消耗风险。

## Native 环境取证

仅分析一个已有 WSL ELF core dump，没有复现崩溃或读取进程环境：

- 文件：`wsl-crash-1788335561-24487-_home_makise_.local_share_uv_python_cpython-3.12.10-linux-x86_64-gnu_bin_python3.12-11.dmp`
- 大小：109,711,360 bytes
- SHA-256：`495AA595BD532DCA3C09B344611261115616FF0D08C907EF00D750908FB8A55A`
- ELF：64-bit、x86-64、core file
- `addr2line`/`objdump`：崩溃地址位于
  `call_instrumentation_vector.llvm.4202081695392425352`

该证据只支持“Python/dbt/WSL native 环境未解决”，不支持修改 DataIncidentGym
业务代码或放宽测试断言。core dump 不上传、不复制、不提交；不使用 `strings`、内存扫描或环境变量输出。

## 修复与发布边界

- evaluator 版本升级为 `p1.evaluator.v2`，序列化 evaluation Schema 不变。
- setup artifact 仅保留环境与恢复两个适用检查，其余使用规范 `NOT_APPLICABLE` 安全载荷。
- setup/harness 异常触发当前格终态化后 fail-stop；普通质量失败在环境和恢复健康时继续。
- 不修改 `config/benchmark/p1-formal-v1.json`、正式 ledger 或六文件产物。
- 本修复不触发真实模型；commit、push 和 exact-HEAD Ubuntu CI 另行授权。
