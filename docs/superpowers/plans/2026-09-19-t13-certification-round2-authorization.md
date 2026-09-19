# T13 两对认证与准入授权 · 第二次执行（2026-09-19）

- 授权人：项目所有者（待确认）。审计依据：第一次执行停机报告 `469ef04`（
  `docs/superpowers/reports/2026-09-19-t13-certification-result.md`）、身份桥修复 `469ef04`、
  审计侧对修复的独立复核（真实归档 manifest 离线对照：修复前 AttributeError 复现、修复后桥产出
  8 条目含两个 seed 身份、无 test 节点泄漏；活库只读指纹 == F0）。
- 第一次授权（`2026-09-19-t13-certification-authorization.md`）按"任一 C 项不成立即停"已消耗，
  未重跑。本文档是第二次执行授权书：观察项 C1–C8、判定规则、产出要求全部沿用第一次授权书
  （含其 §4 修订说明），此处只列差异与新增条件。

## 1. 授权范围（相对第一次的差异）

| 项 | 值 |
| --- | --- |
| 数据库 / 操作 / 顺序 / 不包含 / 重试 | 与第一次授权书 §1 完全一致 |
| 命令 | `uv run data-incident-gym certify --case schema_type_change_raw_customer_id_a --case schema_type_change_raw_customer_id_b --case schema_type_change_raw_order_user_id_a --case schema_type_change_raw_order_user_id_b --admit --output artifacts/admissions/t13-pairs.json`；随后对四个 run_id 各执行 `uv run data-incident-gym eval score <run_id>` |
| 输出路径变更理由 | 第一次执行证明多 case `--admit` 的默认落盘是 `artifacts/admissions/all.json`——失败运行已把该文件覆盖为四条 admitted=false。本次用 `--output` 定向到 T13 专用文件，不再触碰 `all.json`；认证目录 `artifacts/certifications/catalog.json`（9 月 16 日、18 条）保持不动 |
| 标准输出 | certify 的 stdout/stderr 逐字捕获进结果报告（admit 模式不写认证报告，findings 只能从 stdout 与准入条目核对） |

## 2. 执行前置条件（逐条核对后写入报告）

1. **执行 HEAD 必须含 `469ef04`**（身份桥 `relation_name=None` 守卫）；记录实际 HEAD 的完整 SHA。
2. 工作树干净（`git status --short` 无已跟踪文件改动）。
3. `pipeline build` 的 fingerprint 等于 `e5c7848eb2b7af16ec37463650b63dbb5d1f52293adf456003517b51c496cb18`；
   不等即停止。
4. dry run 旧归档与第一次认证的四个失败归档（`41f5a489…`/`6a394e3a…`/`07920dcd…`/`c3a352d0…`）
   均不得顶替；认证必须使用本次新产生的四轮运行归档。
5. 前置检查仍为纯环境检查；**不运行 `doctor`**。

## 3. 判定规则（第一次授权书 §4 全部有效，另加一条升级规则）

- 第一次授权书 §4 的全部判定规则继续有效，含 C4 修订后的代表缺口口径（缺口 2 条、`target_refusals`
  6 条）与 C2 的 A/B 分流。
- **新增升级规则：若本次仍在 lab 构建后处理阶段失败（`BUILD_FAILED`），停止并触发对 v2 构建/校验
  链路的整体审计，不再直接授权第三次执行。** 同一链路（dry run 两次 O7 + 认证一次）已暴露三处缺陷，
  逐点修复再跑的收益已低于整体审计。
- 若失败发生在诊断/评测阶段（非 BUILD_FAILED），按第一次授权书 §4 的 failure_classes 归因规则处理。

## 4. 产出

- 结果报告 `docs/superpowers/reports/2026-09-19-t13-certification-result.md` 追加 §7：第二次执行的
  实际 HEAD、逐条命令与逐字 stdout、四个 run_id、C1–C8 实测值、与预期的差异、结论
  （认证成立 / 不成立及原因）。
- 准入报告以 `artifacts/admissions/t13-pairs.json` 留档；任何场景文件或设计文档的必要修订单独提交。
