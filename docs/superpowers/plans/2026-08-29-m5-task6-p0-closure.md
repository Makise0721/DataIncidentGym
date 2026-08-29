# M5 Task 6 P0 Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. If subagents are used in this repository, use the custom `luna_worker` agent type only.

**Goal:** Close M5/P0 on the current `codex/m5-reimplementation-20260828` branch by correcting the accepted network contract, preserving the six-file artifact contract, running final local and remote gates, and recording only evidence-backed completion claims.

**Architecture:** Treat Task 6 as a closure gate, not a feature task. The existing M5 implementation remains the source of behavior; planned changes are limited to requirements wording, this plan, workspace-only status notes, and evidence collection unless a final review finds a reproducible blocker on the approved path. The real-model acceptance evidence remains valid only while no Task 6 code change touches the diagnosis, evaluator, artifact, doctor, config, or e2e acceptance paths.

**Tech Stack:** Windows 11, PowerShell 7, Python 3.12.10, uv 0.11.24, pytest, Ruff, Typer CLI, GitHub CLI, GitHub Actions Ubuntu CI, OpenAI-compatible `mimo-v2.5`.

---

## Fixed Context

- Repository worktree: `C:\Users\29913\.config\superpowers\worktrees\DataIncidentGym\m5-reimplementation-20260828`
- Branch: `codex/m5-reimplementation-20260828`
- M4 baseline: `96ad13c062a031f79924de1c5212552011b64097`
- Last reviewed code HEAD before this Task 6 plan: `e463ce2c7fdce6653a86f71f8c58d8fdc08e280d`
- Reviewed real M5 sample artifacts were generated at code revision `754c17e`:
  - `artifacts/8b5b5e459a464c4baf13c150f458155e` -> `PASSED`
  - `artifacts/71cc02b4f11147178b50d06ccf876f6b` -> `FAILED`
  - `artifacts/77546521a045496e96aa18bcc87a1cec` -> `PASSED`
- The two commits after `754c17e` were reviewed as non-blocking for that observed sample path: `3e8178a` budget accounting for denied ninth tool attempts, and `e463ce2` path-independent doctor safety test.
- Root `AGENT.md`, `README.md`, and `mistake.md` are workspace-only status files in this worktree unless the user explicitly changes that convention.

## File Structure

- `docs/requirements.md`: canonical requirements and P0 acceptance checklist. Task 6 changes only the external-network acceptance line to match the approved MiMo endpoint exception.
- `docs/superpowers/plans/2026-08-29-m5-task6-p0-closure.md`: this closure plan and execution checklist.
- `README.md`: workspace-only completion/status summary for humans; do not stage or commit.
- `mistake.md`: workspace-only implementation log and review ledger; do not stage or commit.
- `src/data_incident_gym/*.py`, `tests/**/*.py`, `.github/workflows/ci.yml`, `uv.lock`, and `third_party/jaffle_shop`: verify only during Task 6 unless a blocker is accepted for a targeted fix.
- `artifacts/<run_id>/`: ignored runtime evidence; verify six files and metadata, never stage.

---

### Task 1: Align the P0 network acceptance contract

**Files:**
- Modify: `docs/requirements.md`
- Create: `docs/superpowers/plans/2026-08-29-m5-task6-p0-closure.md`

- [ ] **Step 1: Verify the accepted network wording is present**

```powershell
rg -n '未保存隐藏推理，未发生 Agent 写操作；除当前配置的诊断模型 endpoint 所需请求外，未发生其他外部网络访问。' docs/requirements.md
rg -n '诊断及包含诊断的 `eval run` 明确需要网络和 `MIMO_API_KEY`' docs/requirements.md
```

Expected: both commands return exactly one `docs/requirements.md` line. The checklist no longer says unqualified `未发生 Agent 写操作或外部网络访问`.

- [ ] **Step 2: Verify only the planned tracked docs are changed**

```powershell
git diff -- docs/requirements.md docs/superpowers/plans/2026-08-29-m5-task6-p0-closure.md
git status --short --branch
git diff --cached --name-only
```

Expected: `docs/requirements.md` contains only the checklist wording change; this plan is the only new tracked file; index is empty; root `AGENT.md`, `README.md`, and `mistake.md` may remain unstaged workspace-only files.

- [ ] **Step 3: Commit the Task 6 planning contract**

```powershell
git add -- docs/requirements.md docs/superpowers/plans/2026-08-29-m5-task6-p0-closure.md
git diff --cached --name-only
git commit -m "docs: plan m5 task6 closure"
```

Expected: cached names are exactly `docs/requirements.md` and `docs/superpowers/plans/2026-08-29-m5-task6-p0-closure.md`; no root Markdown, artifacts, source, tests, `uv.lock`, or submodule entry is staged.

### Task 2: Run the final non-network local regression gate

**Files:**
- Verify without modification: `src/data_incident_gym`
- Verify without modification: `tests`
- Verify without modification: `pyproject.toml`
- Verify without modification: `uv.lock`

- [ ] **Step 1: Clear real-model opt-in for ordinary regression**

```powershell
Remove-Item Env:DIG_RUN_REAL_MODEL_TESTS -ErrorAction SilentlyContinue
if (Test-Path Env:DIG_RUN_REAL_MODEL_TESTS) { exit 1 }
```

Expected: exit 0 and no real-model opt-in remains in the current process.

- [ ] **Step 2: Run formatter, unit, integration, e2e, lock, help, and whitespace checks**

```powershell
uv run ruff check .
uv run pytest tests/unit -q
uv run pytest tests/integration -q
uv run pytest tests/e2e -q
uv lock --check
uv run data-incident-gym --help
uv run data-incident-gym pipeline --help
uv run data-incident-gym lab --help
uv run data-incident-gym diagnose --help
uv run data-incident-gym eval run --help
uv run data-incident-gym doctor --help
git diff --check
```

Expected: all commands exit 0. The ordinary e2e run skips the two `real_model` tests and sends no MiMo request.

- [ ] **Step 3: Confirm Task 6 has not changed runtime behavior**

```powershell
git diff --name-only e463ce2c7fdce6653a86f71f8c58d8fdc08e280d..HEAD
```

Expected: output is limited to `docs/requirements.md` and `docs/superpowers/plans/2026-08-29-m5-task6-p0-closure.md`. If any `src/`, `tests/`, `.env.diagnostic.example`, `pyproject.toml`, `uv.lock`, `.github/workflows/ci.yml`, or `third_party/jaffle_shop` path appears, stop and require a targeted review before using the old real-model sample evidence.

### Task 3: Audit scope, safety, and artifact evidence

**Files:**
- Verify without modification: `src/data_incident_gym/diagnostic_agent.py`
- Verify without modification: `src/data_incident_gym/evidence_tools.py`
- Verify without modification: `src/data_incident_gym/evaluation.py`
- Verify without modification: `src/data_incident_gym/evaluation_runner.py`
- Verify without modification: `src/data_incident_gym/artifacts.py`
- Verify without modification: `tests/e2e/test_ollama_evaluation.py`
- Verify ignored runtime outputs: `artifacts/`

- [ ] **Step 1: Confirm Ground Truth stays out of the agent path**

```powershell
rg -n 'load_ground_truth|IncidentVerifier|lab_verifier' src/data_incident_gym/diagnosis.py src/data_incident_gym/diagnostic_agent.py src/data_incident_gym/evidence_tools.py
rg -n 'ground_truth_digest' src/data_incident_gym/evidence_tools.py
```

Expected: first command has no output. Second command may show only the run-context digest validation in `evidence_tools.py`; it must not expose answers, affected assets, expected evidence IDs, or evaluator data to the agent.

- [ ] **Step 2: Confirm the agent still exposes exactly the four M3 read-only tools**

```powershell
$toolLines = @(rg -n '@agent\.tool' src/data_incident_gym/diagnostic_agent.py)
$toolLines
if ($toolLines.Count -ne 4) { exit 1 }
rg -n 'get_dbt_run_results|get_dbt_node_error|get_relation_schema|get_dbt_lineage' src/data_incident_gym/diagnostic_agent.py
```

Expected: exactly four `@agent.tool` registrations, and the only registered tool functions are `get_dbt_run_results`, `get_dbt_node_error`, `get_relation_schema`, and `get_dbt_lineage`.

- [ ] **Step 3: Validate the reviewed real-model artifact trio**

```powershell
$sampleRunIds = @(
    '8b5b5e459a464c4baf13c150f458155e',
    '71cc02b4f11147178b50d06ccf876f6b',
    '77546521a045496e96aa18bcc87a1cec'
)
$expectedFiles = @(
    'diagnosis.json',
    'evaluation.json',
    'evidence.json',
    'metadata.json',
    'report.md',
    'trace.jsonl'
)
foreach ($runId in $sampleRunIds) {
    $artifactDir = Join-Path 'artifacts' $runId
    if (-not (Test-Path -LiteralPath $artifactDir -PathType Container)) { exit 1 }
    $actualFiles = @(Get-ChildItem -LiteralPath $artifactDir -File | Select-Object -ExpandProperty Name | Sort-Object)
    $missing = @(Compare-Object -ReferenceObject ($expectedFiles | Sort-Object) -DifferenceObject $actualFiles)
    if ($missing.Count -ne 0) { exit 1 }
    $metadata = Get-Content -Raw -LiteralPath (Join-Path $artifactDir 'metadata.json') | ConvertFrom-Json
    $evaluation = Get-Content -Raw -LiteralPath (Join-Path $artifactDir 'evaluation.json') | ConvertFrom-Json
    "$runId status=$($evaluation.status) model=$($metadata.model) prompt=$($metadata.prompt_version) recovery=$($metadata.recovery_status)"
}
```

Expected: three lines are printed; models are `mimo-v2.5`, prompt version is `m5.diagnosis.v7`, recovery is `HEALTHY`, and exactly two of the three statuses are `PASSED`.

- [ ] **Step 4: Confirm runtime outputs and submodule stay out of Git**

```powershell
git status --short --branch
git diff --cached --name-only
git -C third_party/jaffle_shop rev-parse HEAD
git -C third_party/jaffle_shop status --short
```

Expected: index is empty after Task 1 commit; runtime artifact files are not tracked; submodule HEAD is `36bde6cba69d962b83be1d52fc65a0dce1cb4ebb`; submodule status has no output.

### Task 4: Close the final review findings

**Files:**
- Verify without modification: `docs/requirements.md`
- Verify without modification: `src/data_incident_gym/diagnostic_agent.py`
- Verify without modification: `src/data_incident_gym/evaluation_runner.py`
- Verify without modification: `src/data_incident_gym/artifacts.py`
- Verify without modification: `tests/integration/test_evaluation_runner.py`
- Verify without modification: `README.md`
- Verify without modification: `mistake.md`

- [ ] **Step 1: Recheck the three known final-review findings**

```powershell
rg -n '除当前配置的诊断模型 endpoint 所需请求外，未发生其他外部网络访问' docs/requirements.md
rg -n 'provider="openai-compatible"|model=self\._settings\.model_name' src/data_incident_gym/diagnostic_agent.py
rg -n 'EVALUATION_FAILED|write_artifacts|artifact_writer.write' src/data_incident_gym/evaluation_runner.py tests/unit/test_evaluation_runner.py
```

Expected: the requirements DECISION is closed by the first command. The metadata-label and evaluator-exception findings remain classified as non-blocking `LOCAL` unless the user explicitly promotes either one to Task 6 scope.

- [ ] **Step 2: Run one final diff review from the fixed M4 baseline**

```powershell
git diff --stat 96ad13c062a031f79924de1c5212552011b64097..HEAD
git diff --name-status 96ad13c062a031f79924de1c5212552011b64097..HEAD
git log --oneline 96ad13c062a031f79924de1c5212552011b64097..HEAD
```

Expected: the diff contains the approved M5 implementation, model migration docs, the Task 6 requirements fix, and this plan. It does not contain unapproved P1 multi-case kernel work, free SQL/prompt/path CLI, production write tools, or root workspace-only Markdown.

- [ ] **Step 3: Apply only accepted blocker fixes**

If the final review finds a reproducible `BLOCKER`, make the smallest targeted fix, then run the directly affected test plus the Task 2 gate. Use one commit per accepted blocker:

```powershell
git status --short --branch
git add -- docs/requirements.md src tests pyproject.toml uv.lock .github/workflows/ci.yml
git diff --cached --name-only
git commit -m "fix: close m5 task6 blocker"
```

Expected: this step is skipped when there is no `BLOCKER`. If used, cached files must not include root `AGENT.md`, `README.md`, `mistake.md`, `artifacts/`, or `third_party/jaffle_shop`.

### Task 5: Push the current branch and observe Ubuntu CI for the exact HEAD

**Files:**
- Verify without modification: `.github/workflows/ci.yml`
- Verify without modification: remote branch `origin/codex/m5-reimplementation-20260828`

- [ ] **Step 1: Push only after user authorization**

```powershell
git rev-parse HEAD
git status --short --branch
git push origin codex/m5-reimplementation-20260828
```

Expected: push exits 0. If root workspace-only files are still modified, they remain uncommitted and unpushed.

- [ ] **Step 2: Resolve the GitHub Actions run for the exact pushed HEAD**

```powershell
$head = (git rev-parse HEAD).Trim()
$runs = gh run list --branch codex/m5-reimplementation-20260828 --json databaseId,headSha,status,conclusion,workflowName,url,createdAt --limit 20 | ConvertFrom-Json
$run = @($runs | Where-Object { $_.headSha -eq $head -and $_.workflowName -eq 'ci' } | Select-Object -First 1)
if ($run.Count -ne 1) { exit 1 }
$run[0].databaseId
$run[0].url
```

Expected: exactly one `ci` workflow run is identified for the current HEAD.

- [ ] **Step 3: Wait for CI and verify all required jobs**

```powershell
$runId = ($run[0].databaseId).ToString()
gh run watch $runId --exit-status
gh run view $runId --json headSha,status,conclusion,jobs,url | ConvertFrom-Json | ConvertTo-Json -Depth 8
```

Expected: CI conclusion is `success`, `headSha` equals local `git rev-parse HEAD`, and the workflow includes successful Ruff, unit, integration, and ordinary e2e steps on Ubuntu. A skipped diagnostic-upload step after success is acceptable only when it is conditionally skipped because no failure artifact exists.

### Task 6: Record the final P0 completion evidence without overclaiming

**Files:**
- Modify (workspace-only, never stage or commit): `README.md`
- Modify (workspace-only, never stage or commit): `mistake.md`
- Verify without modification: `docs/requirements.md`
- Verify without modification: `docs/superpowers/plans/2026-08-29-m5-task6-p0-closure.md`

- [ ] **Step 1: Update the workspace-only human status files**

Record the exact final facts in `README.md` and `mistake.md`:

```text
M5/P0 status: completed for the single approved P0 case only.
Branch: codex/m5-reimplementation-20260828.
Final HEAD: use the exact output of `git rev-parse HEAD`.
Remote CI: record the exact GitHub Actions run URL and conclusion for that HEAD.
Real-model acceptance: 2/3 reviewed MiMo samples passed, with run IDs 8b5b5e459a464c4baf13c150f458155e, 71cc02b4f11147178b50d06ccf876f6b, and 77546521a045496e96aa18bcc87a1cec, unless Task 4 or Task 5 introduced source changes that required fresh samples.
Artifact contract: metadata.json, trace.jsonl, evidence.json, diagnosis.json, evaluation.json, report.md.
Boundary: do not claim general accuracy, multi-case coverage, production readiness, P1 Diagnostic Kernel, or superiority over a static skill baseline.
```

Expected: the docs state only evidence-backed completion for the single P0 case and keep the future Diagnostic Kernel work out of P0 claims.

- [ ] **Step 2: Verify root status files remain workspace-only**

```powershell
git status --short --branch
git diff --cached --name-only
git ls-files README.md mistake.md AGENT.md
```

Expected: `README.md`, `mistake.md`, and `AGENT.md` are not staged. They are not part of the Task 6 tracked commits unless the user explicitly changes the convention.

- [ ] **Step 3: Deliver the completion report**

The final report to the user must include:

```text
final HEAD
remote branch
Ubuntu CI run URL and success conclusion
local regression command results
known real-model sample run IDs and 2/3 result
whether fresh samples were run under final HEAD or why prior reviewed samples remain in scope
artifact contract status
submodule status
root workspace-only file status
any remaining LOCAL/BACKLOG findings
explicit statement that P0 is single-case and not a general diagnostic-accuracy claim
```

Expected: there are no open `BLOCKER` or `DECISION` findings. Any remaining `LOCAL` or `BACKLOG` finding is recorded with a concrete rationale and does not block M5/P0 completion.

---

## Self-Review

- Spec coverage: Task 1 closes the approved MiMo network exception conflict; Task 2 covers normal Windows regression without real-model calls; Task 3 covers Ground Truth isolation, four-tool scope, six-file artifacts, and submodule cleanliness; Task 4 closes final-review findings without unapproved scope growth; Task 5 requires Ubuntu CI on the exact pushed HEAD; Task 6 records evidence without overstating P0.
- Placeholder scan: no forbidden placeholder token, hidden fill-in step, or unspecified test target remains. Dynamic values such as final HEAD and CI run are produced by exact commands in the plan.
- Type and command consistency: the plan uses current `DIG_RUN_REAL_MODEL_TESTS`, current branch `codex/m5-reimplementation-20260828`, current model `mimo-v2.5`, current prompt `m5.diagnosis.v7`, and current CLI command `data-incident-gym eval run`.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-29-m5-task6-p0-closure.md`.

1. Subagent-Driven - dispatch a fresh `luna_worker` subagent per task and review between tasks.
2. Inline Execution - execute the plan in this session with checkpoints.
