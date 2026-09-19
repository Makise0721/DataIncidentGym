# p1-formal-v24 冻结与真实模型测量正式授权书

- 日期：2026-09-19。
- 授权人：项目所有者；授权依据为本轮明确指令“把草案转成正式授权书”。
- 状态：**阶段 A 已授权，尚未执行；阶段 B 待阶段 A 审计通过后由所有者再次放行。** 本轮仅形成授权书，不执行其中操作。
- 执行基线：`main`，`146abd3`（v23 冻结留档）。开始前核实 HEAD；若代码已有后续变化，先记录差异并复核，不自动套用旧基线。
- 本文替代同日期 `-draft.md` 草案。背景见 [接口调研](2026-09-19-deepseek-measurement-research.md) 与 [T13 认证及 v23 冻结报告](../reports/2026-09-19-t13-certification-result.md)。
- 模型目录事实沿用草案记录：此前 `/provider/v1/models` 返回目录包含 `deepseek/deepseek-v4.1-flash`。本次仅整理文件，未重新访问模型目录或端点；阶段 A 不因此增加联网探针。

## 0. 已批准的设计范围

同一清单族允许第二个正式模型，通过每份清单的 `model_configuration` 显式绑定。新档案固定为：

```json
{
  "provider": "openai-compatible",
  "model": "deepseek/deepseek-v4.1-flash",
  "base_url": "https://api.commandcode.ai/provider/v1",
  "settings_overrides": {}
}
```

保持 12 个正式场景、106 格（94 model-backed、12 fixed-rule）及 8 模型请求 / 8 工具调用 / 2 输出重试 / 300 秒每格的合同不变。不得修改既有清单文件，不加入 T13 四场景或 EVIDENCE_PLANNER，不改变 evaluator、诊断 schema、场景合同及策略提示。

本次测量若获阶段 B 放行，回答的是既有赛程在新模型配置下的表现，不是 T13 新证据或规划器的收益。

## 1. 阶段 A：合同扩展与 v24 冻结（已授权，全离线）

### 1.1 允许改动

- `src/data_incident_gym/benchmark_manifest.py`：模型 Literal 增加新模型；新冻结路径校验模型与 base_url 配对；`APPROVED_MANIFEST_IDS` 追加 `p1-formal-v24`。
- 配对表：`mimo-v2.5-pro` 对应 `https://api.xiaomimimo.com/v1`；`deepseek/deepseek-v4.1-flash` 对应 `https://api.commandcode.ai/provider/v1`。未知或交叉组合拒绝。既有清单加载规则不得因新增配对表而收紧。
- `src/data_incident_gym/cli.py`：`benchmark freeze` 增加 `--model`，默认仍为 mimo；根据明确模型选择对应端点并传入构建器，不能选了 DeepSeek 却保留默认 mimo 端点。现有默认输出路径与默认模型行为不变。
- 相应离线单元测试、一个新清单 `config/benchmark/p1-formal-v24.json`、本次授权与实施报告。除此之外的代码改动不属于本授权。

先完成离线实现和验证，再进行两段式提交：

1. `feat: approve p1-formal-v24 deepseek measurement identity`：合同、CLI 与对应测试；记录完整 SHA。
2. 运行下列 freeze 与 verify；成功后单独提交清单：`chore: freeze p1-formal-v24 deepseek measurement manifest`。

```powershell
uv run --offline data-incident-gym benchmark freeze --manifest-id p1-formal-v24 --model deepseek/deepseek-v4.1-flash --implementation-revision <批准提交完整SHA> --output config/benchmark/p1-formal-v24.json
uv run --offline data-incident-gym benchmark verify --manifest config/benchmark/p1-formal-v24.json
```

冻结一次，不覆盖、不删除重冻；失败保留现场并报告。已有未跟踪的历史文档、`.zcode/`、`skills-lock.json` 不得夹带进提交。提交与 push 分离，本授权不包含 push。

### 1.2 必录验收 V1–V5

| 编号 | 验收 |
| --- | --- |
| V1 | 所有实际存在的旧清单文件字节不变且仍可加载；v23 在当前实现上 verify 通过。更早清单已有的 result-input 漂移保留记录，**不要求历史清单全部在当前代码下 verify 通过**。 |
| V2 | v24 verify 通过；记录完整清单摘要与 implementation_revision；106/94/12 格数不变。 |
| V3 | v24 模型档案四字段恰等于 §0；catalog、policies、formal_scenario_ids、预算与 v23 相同；cells 排除新预分配 run_id 后相同。result_inputs 与 v23 相同，其他差异逐项解释。 |
| V4 | 默认 mimo 路径回归通过；两合法配对、交叉配对、未知模型拒绝均有测试；CLI 模型与端点实际传递正确。随机 run_id 与修订号不作“新生成清单逐字节相等”要求。 |
| V5 | `ruff check .`、相关单测、`uv lock --check`、`git diff --check` 通过；报告列出实际命令、结果及改动文件。无数据库、doctor、模型探针或 benchmark run。 |

授权书与报告的文档提交另列，不混入“仅清单文件”的提交。无必要不重复全量测试；出现超出本范围的失败先报告。

## 2. 阶段 A → B 放行条件

阶段 A 报告落盘、V1–V5 审计通过后，向所有者提交以下具体信息，由所有者再次确认：

- v24 完整 SHA256、实施修订 SHA、执行环境与预定时间窗口。
- **金额上限及币种**（包含预检），计价来源、输入/输出 token 假设、停止方式；请求次数不能替代金额预算。当前尚无获批金额，不得自行填入或启动预检。
- 一次 preflight 与一次完整 suite 的边界；预检包含真实模型探针和环境检查，须纳入阶段 B 授权。
- 实际 runner/SDK 的请求重试、串行或并发行为、429/5xx/配额异常处置。当前 runner 主要在 setup、环境或恢复失败时停机，**不能声称它已对每次模型 429/5xx 自动停机**。若需要代码变更实现费用或异常停止，另列变更方案，不在阶段 A 偷加。

阶段 B 未放行前，不调用端点、不运行 doctor/preflight、不启动数据库操作。阶段 A 完成不自动授权阶段 B。

## 3. 阶段 B：待放行的固定执行规格

### 3.1 前置与密钥

仅使用本地隔离实验 PostgreSQL/dbt 环境；阶段 B 放行应包含必要的环境启动、基线构建、故障注入及恢复，不涉及生产数据。执行前核对 v24 verify、模型身份及已批准费用上限。

密钥通过 PowerShell 进程环境映射，不能输出密钥、不写 `.env.diagnostic`、不进入命令记录或报告。下列为格式示例，**未执行**；实际执行须在 `finally` 中恢复原环境变量（原来不存在则删除）：

```powershell
$env:DIG_DIAGNOSTIC_MODEL_API_KEY = $env:COMMANDCODE_API_KEY
```

### 3.2 执行命令

阶段 B 明确放行后，仅一次预检；通过并确认费用条件满足，才执行一次全量 suite：

```powershell
uv run data-incident-gym benchmark preflight --manifest config/benchmark/p1-formal-v24.json --confirm-sha256 <获批v24摘要>
uv run data-incident-gym benchmark run --manifest config/benchmark/p1-formal-v24.json --confirm-sha256 <获批v24摘要>
```

预检失败即停止。不得更换模型、扩大样本、重跑或替换失败格；中断时保留 ledger 与归档，报告实际完成量，续跑另行裁定。模型请求合同上界为 94×8=752 次；预检请求及可能的 SDK/传输重试须另计，752 不是 HTTP 请求总数或费用上界。

### 3.3 必录验收 M1–M4

| 编号 | 验收 |
| --- | --- |
| M1 | 完整执行须 ledger 106/106 终态齐全；中断则报告已完成、失败、缺失数量，不判完整验收通过。 |
| M2 | 94 个 model-backed 格的归档模型及端点与 v24 一致；12 个 fixed-rule 格按确定性策略身份检查，不要求它们拥有 DeepSeek 模型身份。 |
| M3 | 不包含 T13 四场景或规划器；报告遵循实际排程及适用集合，未排程的策略×场景组合标 N/A，不能补造成六策略全交叉矩阵。 |
| M4 | 输出完整结果、费用与请求/token 用量、环境与恢复结果。与历史 mimo 结果比较前，核对可用归档、场景交集、分母、evaluator 和模型差异；v22 与 v24 的 evaluator 身份也不同，不能把差异单独归因于模型。不存在的 v23 实测结果不得写成基线。 |

不完整 suite 使用现有只读 partial 分析，不绕过正式 report 的完整性要求。保存历史产物，不覆盖历史评分或改写历史 manifest。

## 4. 停止与报告

- 任一验收不符，停止后续步骤，保留产物与提交并报告；不自动回滚提交、不删除失败证据、不换配置复跑。
- 凭据异常或发现敏感值泄漏时停止输出与执行，报告文件位置但不复述值；撤销凭据、隔离或清理产物按所有者后续指示处理。
- 授权只覆盖上述内容，不包含 T14、其他重构或网络发布。

## 5. 交付物

- 本授权书：正式范围与阶段放行依据；本轮只落盘此文件，未启动阶段 A。
- 阶段 A：`docs/superpowers/reports/2026-09-19-v24-freeze.md`，记录提交、命令、摘要及 V1–V5。
- 阶段 B：`docs/superpowers/reports/<实际日期>-v24-benchmark-measurement.md`，记录再次放行依据、金额上限、预检结果、ledger、M1–M4 与中断情况。
