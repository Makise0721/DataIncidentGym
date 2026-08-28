# M5.1 本地模型迁移实施计划（qwen3.5:9b）

**Goal:** 把 M5/P0 的当前本地模型从 `gemma4:e4b` 迁移为用户已批准的 `qwen3.5:9b`：冻结新的模型合同，迁移有效配置与测试，并执行一次独立真实能力探针。本计划不重写、不伪造旧 M5 计划历史。

**Architecture:** 不新增生产 Module、不改变既有架构。模型仍通过统一 OpenAI-compatible 接口（`OpenAIChatModel` + `OpenAIProvider`）访问；qwen3.5 renderer/parser 由 Ollama 模型层原生提供，项目代码不实现任何模型专属解析器、正则修补、fallback、隐藏 retry 或 Ollama 私有生成路径。

**Tech Stack:** 与 M5 冻结基线完全一致（Python 3.12.10, uv 0.11.24, PydanticAI 2.34.0, dbt-core 1.12.3, PostgreSQL 17.6 Alpine 等）；本计划不变更任何依赖版本。

---

## 实施前基线与已批准决策

- 基线：隔离 worktree `codex/m5-reimplementation-20260828`，父提交 `6913addb39a95217f160b26b0c0fd95aafcf6587`。不修改、不 reset、不 rebase、不 merge 共享 `master`；未经新授权不 push。
- 用户于 2026-08-28 决定下一轮 M5.1 改用 `qwen3.5:9b`。本地已导入：标签 `qwen3.5:9b`，9.0B，Q5_K_M，约 6.6GB，Ollama 识别 `completion`、`tools`、`thinking` 能力；Modelfile 使用 Ollama 原生 qwen3.5 renderer/parser。不重新 pull、不重复导入。
- `gemma4:e4b` 的历史失败（M5 真实评测 0/3：两次 `MODEL_REQUEST_LIMIT`、一次 `MODEL_PROTOCOL_ERROR`）保留在旧计划与 `mistake.md` 中，不覆盖、不计入新模型分母。
- 旧三样本从未以 `qwen3.5:9b` 运行；真实模型验收分母保持为零。任何能力探针、完整 doctor 都不得计入三样本分母。
- Windows 用户级 `OLLAMA_HOST` 已持久化为 `http://127.0.0.1:11434`。继承旧值的进程只同步当前进程，不改回 `0.0.0.0`，不修改机器级配置。
- 根目录 `AGENT.md`、`README.md`、`mistake.md` 为 workspace-only：保留用户修改，不得 stage、commit、覆盖或格式化。
- `third_party/jaffle_shop` 固定且不得修改。

## 冻结边界（本计划不得改变）

| 边界 | 内容 |
|---|---|
| canonical 产物 | `metadata.json`、`trace.jsonl`、`evidence.json`、`diagnosis.json`、`evaluation.json`、`report.md` 六个 |
| 只读工具 | M3 四个：`get_dbt_run_results`、`get_dbt_node_error`、`get_relation_schema`、`get_dbt_lineage` |
| Diagnosis 终态 | `CONFIRMED`、`INSUFFICIENT_EVIDENCE`、`MODEL_ERROR` 三种 |
| 单次预算 | 模型请求 6 / 工具调用 8 / 结构化输出校验重试 2 / 总超时 300 秒（2026-08-28 用户再批准由 180 秒改为 300 秒） |
| 评分口径 | 确定性精确匹配；不使用 LLM judge；confidence 不参与主要评分 |
| Ground Truth 隔离 | Agent、prompt、工具与 M3 不接触 Ground Truth |
| 三样本规则 | 精确三个独立样本、至少 2 次通过、失败保留且全部计入分母 |
| 安全与恢复边界 | 秘密不进入数据流；类型化错误码；`EvaluationRunner` finally 恢复且最终状态必须 `HEALTHY` |
| prompt | 不修改 `SYSTEM_PROMPT` 内容与版本；除非调查证明模型迁移无法兼容且用户另行批准 |
| 依赖 | 不新增、不升级、不降级任何依赖 |

## 范围：三个小 Task，严格顺序执行

### Task A：冻结 M5.1 模型迁移合同（文档）

- 更新 `docs/requirements.md`：当前模型明确为 `qwen3.5:9b`（§7.5、§10.1、§10.3、§12.1、§16、§18），并在头部记录修订批准；§7.4 M4 章节保留 `gemma4:e4b` 历史。
- 新增本计划（窄范围 delta），不改写 `2026-08-25-m4-diagnosis-agent.md` 与 `2026-08-26-m5-evaluation-report.md` 的历史文字。
- 文档审查（diff、状态、事实核验）无 BLOCKER/DECISION 后，仅提交上述两个文档路径。

### Task B：迁移有效模型配置

仅修改迁移直接需要的文件：

- `src/data_incident_gym/diagnostic_config.py`：`DiagnosticSettings.model_name` 默认值 → `qwen3.5:9b`。
- `.env.diagnostic.example`：当前模型名。
- 本地 ignored `.env.diagnostic`：当前模型名（不入 Git）。
- `src/data_incident_gym/doctor.py` / `src/data_incident_gym/cli.py`：`MODEL_PRESENT` 失败的建议码与中文建议文案从 `PULL_GEMMA4_E4B` 迁移为 qwen3.5 对应码。
- `pyproject.toml`：`ollama` marker 描述中的模型名（不改版本与依赖）。
- 测试中“当前模型”断言：`test_diagnostic_config.py`、`test_doctor.py`、`test_cli.py`、`test_evaluation_runner.py`、`test_artifacts.py`、`test_evaluation.py`、`test_diagnosis.py`、`test_diagnostic_agent.py`、`tests/e2e/test_ollama_diagnosis.py` 等。
- 不得机械替换 `mistake.md` 或旧计划中的历史 `gemma4:e4b`。
- 先写/调整测试，再做最小实现；不得为通过测试放宽结构化输出或预算。
- 验收：相关 unit、全量 unit、`uv run ruff check .`、`uv lock --check`、`git diff --check`、staged 范围核验；一次独立广度审查按 BLOCKER/LOCAL/BACKLOG/DECISION 分类，预算为 1 广审 + 1 BLOCKER 修复 + 1 定向复审。
- 提交前确认根目录三个 Markdown 未进入 index。

### Task C：一次独立真实模型能力探针

前置：Task A、B 全部通过且无未关闭 BLOCKER/DECISION。

- 仅执行一次；模型 `qwen3.5:9b`；使用与 doctor 相同的通用工具调用＋严格结构化输出能力 seam（`DoctorRunner` 的 `_model_probe_check` 同源实现）。
- 不含案例答案、Ground Truth 或三样本数据；不运行完整 doctor、不运行 `eval run`、不运行 `tests/e2e/test_ollama_evaluation.py`；不计入三样本分母。
- 不重试、不更换模型、不增加预算；不保存或输出隐藏推理、原始 provider 日志、密码、token、DSN 或敏感路径。
- PASS：记录模型、量化、工具是否调用、结构化输出是否通过、脱敏 usage 与 exit；停止并向用户申请是否授权一次完整 doctor。
- FAIL：记录稳定错误分类后立即停止；不得再次探针或修复—重试。证据指向模型能力、预算、prompt/schema 或 adapter 选择时，分类为 DECISION 并停止。

## 明确非目标（需另行授权）

- 完整 `doctor`、`eval run`、真实三样本、追加第四样本或改变分母。
- Task 6、P1/P2/P3 任何内容。
- 修改 prompt 内容、依赖版本、安全边界、评分标准。
- 新增 `output_safety.py`、通用敏感信息分类器、SQL/DSN/自然语言解析器、全局锁或崩溃恢复协议。
- 修改 `third_party/jaffle_shop`、共享 `master` 或执行 push。

## 实施停止规则

1. 现场状态与已批准基线不一致，或出现 DECISION。
2. 定向复审后仍有可复现 BLOCKER。
3. 迁移必须改动冻结边界任一项才能继续。
4. 能力探针失败后需要重试、换模型或加预算。

触发任一条件：保留脱敏证据、停止当前 Task、向用户报告，不自行扩展范围。
