# p1-formal-v25 冻结正式授权书（滚动窗口停止机制实施修订）

- 日期：2026-09-20。
- 授权人：项目所有者；授权依据为本日明确裁定与指令："选 (a)：保留 v24，另建 v25，绑定停止机制的
  新实施修订。不建议放宽 _verify_checkout……审计通过后，单独提交停止机制，再按独立授权冻结 v25。
  保持模型、场景、策略、预算、评分合同和排程不变，逐项解释身份差异，v24 原样保留。"
- 前置状态：停止机制审计通过（[审计报告](../reports/2026-09-20-rolling-window-stop-audit.md)，
  发现全部修复或记录）；停止机制已单独提交为
  `4e485f1642dbccab0f4f1cf5db7bec4987691e86`（feat: enforce the owner-approved rolling-window
  pause between benchmark cells）。
- 本授权不包含：preflight、benchmark run、模型调用、数据库操作、push；阶段 B 仍待所有者最终放行。

## 1. 授权范围（全离线）

1. `src/data_incident_gym/benchmark_manifest.py`：`APPROVED_MANIFEST_IDS` 追加 `p1-formal-v25`。
   相应更新三处把 v25 用作"下一个未批准身份"的测试断言（改用 v26）。
2. 批准提交（commit B）后，以 commit B 的完整 40 位 SHA 为 `implementation_revision`，冻结
   `p1-formal-v25`：模型 `deepseek/deepseek-v4.1-flash`（配对端点
   `https://api.commandcode.ai/provider/v1`），输出 `config/benchmark/p1-formal-v25.json`，
   单独提交（commit C，仅清单文件）。冻结一次，不覆盖、不删除重冻；失败保留现场并报告。
3. 既有清单文件（含 v24）字节不变；v24 原样保留，不重冻、不改名。

## 2. 身份不变量

v25 与 v24 相比，差异必须**恰好**为以下各项，逐项记录于冻结报告：

- `manifest_id`（v24 → v25）；
- `implementation_revision`（v24 批准提交 → 本授权的 commit B）；
- 106 个 `cells[].run_id`（由 `manifest_id:sequence:case:strategy:repeat` 决定性推导，
  随 manifest_id 改变；排程本身不变）。

其余全部相同：`model_configuration`（provider/model/base_url/settings_overrides）、`budget`
（8/8/2/300）、`scenario_catalog`、`formal_scenario_ids`（12 正式场景）、`result_inputs`
（评分合同：profile spec、ScenarioSpec schema、Diagnosis schema、evaluator 版本与摘要）、
`policies`、`schema_version`、`artifact_files`，以及 cells 除 run_id 外的全部字段
（sequence、incident_case_id、strategy、repeat_index、model_backed）。
以程序化归一化比较（剔除上述三项后逐字段对比）验证，不接受目视结论。

## 3. 必录验收 W1–W4

| 编号 | 验收 |
| --- | --- |
| W1 | v24 清单字节不变（sha256 `a4e59d2fe533bc9773da8881328551537b1755dc88e4d9d24c43aefb89cdaa67`）且在当前实现上 verify 通过；v23 同样 verify 通过。 |
| W2 | v25 一次冻结成功；`benchmark verify` 通过；记录 v25 完整 sha256 与 implementation_revision（commit B 完整 SHA）；106/94/12 格数不变。 |
| W3 | §2 的归一化比较通过：差异恰为 manifest_id、implementation_revision、cells[].run_id 三项，逐项列出；任何其他差异即失败，停止并报告。 |
| W4 | 离线门禁通过：`ruff check .`、`pytest tests/unit -q`、`uv lock --check`、`git diff --check`；无数据库、doctor、模型探针或 benchmark run。 |

## 4. 提交纪律

- commit B：`feat: approve p1-formal-v25 benchmark identity`（manifest 模块与测试，不含清单文件）。
- commit C：`chore: freeze p1-formal-v25 benchmark manifest`（仅 `config/benchmark/p1-formal-v25.json`）。
- 授权书、冻结报告与既有文档更新另列文档提交（commit D），不混入 B/C。
- 未跟踪历史文档、`.zcode/`、`skills-lock.json` 不得夹带进任何提交；提交与 push 分离，本授权不含 push。

## 5. 阶段 B 衔接（待最终放行）

- 阶段 B 从 v25 冻结提交（commit C）的检出执行：`_verify_checkout` 允许 HEAD == implementation_revision
  （commit B）或差异仅为清单路径（即 commit C 及之后的 docs 提交之前的干净检出点）。执行时使用
  commit C 的检出（工作树干净，仅清单路径差异），不放宽检出门。
- 预检与全量 suite 仍按 v24 授权书 §3 执行规格与放行申请条款；preflight/run 命令中的对象清单改为
  `config/benchmark/p1-formal-v25.json`，`--confirm-sha256` 使用本授权记录的 v25 摘要。
- 本授权不构成阶段 B 放行；开始时间与最终放行由所有者另行明确。

## 6. 交付物

- 本授权书：`docs/superpowers/plans/2026-09-20-v25-freeze-authorization.md`。
- 冻结报告：`docs/superpowers/reports/2026-09-20-v25-freeze.md`（提交、命令、摘要、W1–W4、
  身份差异逐项表）。
