# Repository Guidelines

## Project Structure & Module Organization

- `src/data_incident_gym/` contains the Python package and CLI. Core modules cover baselines, incident labs, read-only evidence, diagnosis, evaluation, the diagnostic kernel, and artifact writing.
- `tests/unit/`, `tests/integration/`, and `tests/e2e/` contain isolated, service-backed, and full workflow tests respectively.
- `config/` stores dbt profiles and incident specifications. `third_party/jaffle_shop/` is a pinned Git submodule used as the fixture project.
- `docs/requirements.md` is the authoritative requirements and acceptance contract. `artifacts/` and `.dig/` contain generated, ignored output.

## Build, Test, and Development Commands

Use PowerShell 7 with Python 3.12 and `uv`:

```powershell
git submodule update --init --recursive
uv sync --frozen
uv run data-incident-gym pipeline build       # build the healthy dbt baseline
uv build                                      # build the Python package
```

Run the normal verification set with Docker Desktop/PostgreSQL available:

```powershell
uv run ruff check .
uv run pytest tests/unit -q
uv run pytest tests/integration -q
uv run pytest tests/e2e -m 'not real_model' -q
uv lock --check
git diff --check
```

Real-model tests make external requests and require explicit opt-in; do not add them to routine local runs.

## Coding Style & Naming Conventions

Write Python 3.12 code with four-space indentation and a 100-character line limit. Ruff enforces `E`, `F`, `I`, `UP`, `B`, and `SIM`; run it before submitting. Use `snake_case` for modules, functions, variables, and incident IDs; `PascalCase` for classes; and `UPPER_SNAKE_CASE` for constants. Preserve public schemas and serialized artifact fields unless the requirements contract is intentionally updated.

## Testing Guidelines

Name files `test_*.py` and test functions `test_*`; use the configured `integration`, `e2e`, and `real_model` markers. Add focused unit coverage for logic changes and an integration or e2e regression when crossing database, dbt, or artifact boundaries. No coverage threshold is configured.

## Commit & Pull Request Guidelines

Follow the existing `<type>: <imperative summary>` style, such as `fix: ...`, `test: ...`, or `docs: ...`. Keep commits focused. PRs should explain the behavior and incident cases affected, link the issue when applicable, list commands and environment requirements used for verification, and call out schema, generated-artifact, or database changes. Never commit API keys, `.env.diagnostic`, or generated `artifacts/` output.
