# T06 实施报告：场景卡片、准入与开发/保留集管理

- 日期：2026-09-16。HEAD 基线 `e664b8e`（未提交工作树）。
- 交付：`scenario_cards.py`（卡片 + A/B 对称性核对，4 个固定 finding 码）、
  `scenario_sets.py` + `config/scenario-sets.json`（p1.scenario_sets.v1：18 场景全部 dev，
  holdout 空，划分按机制+任务结构、已开发场景永不回标）、`scenario_admission.py`
  （`admit_scenario`/`admit_catalog`，拒绝码 CERTIFICATION_FAILED / SYMMETRY_MISMATCH /
  CARD_INCOMPLETE / HOLDOUT_SCENARIO / UNREGISTERED_SCENARIO）、`certify --admit`。
- 验证：`ruff` 通过；单测 **676 passed / 4 skipped**（T06 新增 35 项）；真实准入 smoke
  required_null a/b **2/2 admitted**（a: CONFIRMED 5 次调用；b: INSUFFICIENT 6 次调用含收据；
  对称性 4 项 finding 全 satisfied）；`benchmark verify`（v22）通过；`git diff --check` 通过。
- 与计划的偏差：A/B 对为 5 组（volume 两个场景均为健康对照，卡片以 `is_control` 编码）；
  允许差异集含 `required_evidence_types`（随 answerability 变化的 evaluator 私有合同）；
  `readonly_path` 由公开合同确定性推导（smoke 中与真实 trace 工具名一致）。
- 未做：保留集场景本身（目录当前无未见样本，准入机制已就绪）；真实模型测量（归 T08）。
  改动未提交 git。

## 准入边界整改（2026-09-16 复核）

- **证书 case 绑定（P1，第一轮）**：`build_admission` 校验
  `certification.case_id == 准入目标 case_id`（否则 `CERTIFICATION_CASE_MISMATCH`）。回归：
  `test_admission_rejects_certification_from_another_scenario`。
- **证书摘要绑定（P1，第二轮）**：第一轮只把当前合同摘要写入准入记录、从不与证书比较，
  合同漂移后旧证书仍可通过（复核复现：给 `schema_rename_payment_amount` 增加必需证据
  `RELATION_HISTORY` 后复用旧证书，仍 admitted=True）。现在：
  `ScenarioCertification.scenario_digest` 为必填字段（64 位十六进制），由
  `certify_scenario` 在两条返回路径写入被认证合同的摘要（`ScenarioSpec.digest()`）；
  准入时与当前合同摘要比较，不匹配即拒绝（`CERTIFICATION_DIGEST_MISMATCH`）；
  缺摘要或摘要格式错误的证书无法构造或加载，因此根本到不了准入入口；
  `ScenarioAdmission` 的 `admitted=True` 校验同样要求证书摘要与记录摘要一致。
  回归：`test_admission_rejects_certificate_for_a_changed_contract`（同 case、合同改变、
  旧证书拒绝）、`test_admission_requires_a_certificate_for_the_current_digest`、
  `test_certification_cannot_exist_without_a_contract_digest`、
  `test_certify_scenario_binds_the_loaded_contract_digest`。
- **历史开发集冻结（P2）**：`HISTORICAL_DEVELOPMENT_SCENARIO_IDS` 冻结全部 18 个已开发场景；
  加载器拒绝其中任何一个进入 holdout（`SCENARIO_SETS_HISTORICAL_DEV_HELD_OUT`），不再只保护
  正式 12 场景。回归：
  `test_historical_development_scenario_cannot_be_marked_holdout`（审计复现）。
- 同轮分类口径二次修订：采集 ≠ 引用——认证新增 `REQUIRED_EVIDENCE_TYPES_CITED` 见证核对，
  "已采集但最终诊断未引用"归 `REFERENCE_IMPLEMENTATION`（见 T05 报告分类口径章节）。
- 影响与验证边界：`artifacts/certifications|admissions/` 下 2026-09-16 真实 smoke 产出的
  旧证书不含 `scenario_digest`，新模型下不可解析；仓库内没有读回这些文件的代码路径，下一次
  真实 `certify --admit` 会重写它们。本轮验证为定向测试 + 全量单测
  （**681 passed / 4 skipped**，ruff、`git diff --check` 通过），按复核要求未重跑数据库认证，
  也未调用真实模型；改动未提交 git。
