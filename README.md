# DataIncidentGym

DataIncidentGym 是一个在本地复现数据管道健康基线的数据事故诊断项目。
DataIncidentGym is a reproducible local healthy-baseline workflow for a data-incident diagnosis project.

## 当前状态

当前仅实现 M1 健康基线；M2、M3、M4、M5 尚未实现，故障注入、证据工具、Agent、评测和完整事故闭环均不可用。

## 前置条件

- Windows 11
- PowerShell 7
- Git
- Python 3.12.10
- uv 0.11.24
- Docker Desktop（运行 PostgreSQL 容器）

版本必须满足 `pyproject.toml`、`uv.lock` 和 `compose.yaml` 中的固定约束。

## 初始化

在项目根目录使用 PowerShell 7 执行：

```powershell
git submodule update --init --recursive
uv sync --frozen
```

`git submodule update` 会初始化固定 commit 的 `third_party/jaffle_shop`。`uv sync --frozen` 只按现有 `uv.lock` 安装依赖，不更新锁文件。

## 运行 M1 健康基线

唯一的业务运行命令是：

```powershell
uv run data-incident-gym pipeline build
```

该命令启动固定版本的 PostgreSQL，重新加载固定 seeds，执行 PostgreSQL 适配的 dbt `seed --full-refresh` 和 `dbt build`，检查 dbt 产物，读取关系 Schema 与行数，并写出确定性 fingerprint。它不执行故障注入，也不调用 Ollama 或其他模型。

期望存在以下 8 个关系及固定行数：

| relation | rows |
| --- | ---: |
| `customers` | 100 |
| `orders` | 99 |
| `raw_customers` | 100 |
| `raw_orders` | 99 |
| `raw_payments` | 113 |
| `stg_customers` | 100 |
| `stg_orders` | 99 |
| `stg_payments` | 113 |

运行产物写入 `.dig/`（默认不提交 Git）：

- `.dig/baseline-summary.json`：Schema、8 个关系的列摘要、行数和 fingerprint。
- `.dig/dbt/target/manifest.json`：dbt manifest。
- `.dig/dbt/target/run_results.json`：dbt 运行结果，健康构建必须非空且只包含 `success`/`pass`。
- `.dig/dbt/logs/dbt.log`：dbt 日志。

## 测试与静态检查

```powershell
uv run ruff check .
uv run pytest tests/unit -q
uv run pytest tests/integration -q
uv run pytest tests/e2e -q
uv lock --check
git diff --check
```

`unit` 不需要 Docker；`integration` 需要 Docker Desktop 和 PostgreSQL；`e2e` 会执行 10 次真实的 seed refresh 与 dbt build，验证 Schema/行数 fingerprint 一致。

## 离线边界与 telemetry

首次准备时需要联网下载 Python 包、Docker PostgreSQL image，并初始化 Git submodule。上述依赖、image 和 submodule 准备完成后，M1 的 `pipeline build`、测试和产物生成不需要外网；Docker Desktop 仍需在本机运行。运行时不调用 Ollama，不发送模型请求，也不把数据、Schema 或产物发送到外部服务。

dbt anonymous usage statistics 已关闭：`config/dbt/profiles.yml` 设置 `send_anonymous_usage_stats: false`，运行环境也固定设置 `DBT_SEND_ANONYMOUS_USAGE_STATS=false`。

## 第三方许可证

固定的 Jaffle Shop 数据模型来自 [dbt-labs/jaffle_shop_duckdb](https://github.com/dbt-labs/jaffle_shop_duckdb)，使用 Apache-2.0 许可，并以 commit `36bde6cba69d962b83be1d52fc65a0dce1cb4ebb` 保存在 `third_party/jaffle_shop` submodule 中。本项目使用 Apache License 2.0，完整文本见 [`LICENSE`](LICENSE)。来源、固定版本、复用范围和适配方式见 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)；第三方文件保留其原始许可证和版权声明。
