# Benchmark 重跑使能 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Repository override:** 当前仓库默认单线执行。只有用户在执行请求中明确要求子代理时才委托，并且只能使用 `luna_worker`；否则在主会话逐 Task 推进并在每个授权门停下。

**Goal:** 解除阻止第二次正式 benchmark 的四处硬编码/生命周期约束，并把正式批次的取证数据保全到 Git 跟踪位置，使 106 格批次可以在 hardening 后的实现上重新冻结、分级验证并留下可审计结论。

**Architecture:** 全部改动集中在 benchmark 三件套（manifest / runner / report）与 CLI，不触碰 evaluator 判分逻辑、Agent 控制器、提示词、预算、场景合同或安全边界。manifest 身份改为版本化派生路径，并为正式重跑、固定规则 smoke、真实模型 smoke 分配三个永不复用的身份；runner 增加显式 cell 子集选择器并写入 `subset.json` 标记，reporter 见到该标记即拒绝出具正式结论；preflight 与 run 接收同一选择器，doctor receipt 增加 `model_probe_required`，使纯 `FIXED_RULE` 套件不必通过模型探针即可执行，同时原样保留失败的模型检查；新增只读归档模块从 ledger 指向的 `artifacts/<run_id>` 读取每格产物，把 ledger / receipt / summary / report / 每格门与指标矩阵复制到跟踪目录。

**Tech Stack:** Python 3.12、pydantic 2.13.4（frozen + `extra="forbid"` + `model_validator`）、Typer 0.27.1、pytest 9.1.1、Ruff 0.16.4。

---

## 背景事实（已在本轮取证中核实）

- `benchmark_runner.py:840-843`：`state = "COMPLETED" if attempt.status is EvaluationStatus.PASSED else "FAILED"`。ledger 记录 8 格 `COMPLETED`，即正式批次有 8 格通过评估器。
- 封存的 `artifacts/benchmarks/p1-formal-v1/`（640 文件，聚合 SHA-256 `d6f94642…`）在主仓库、三个 worktree、OneDrive、Desktop 均已不存在；盘上同名目录皆为 pytest 临时 fixture。因此无法取证 8 格身份与 42 格 `EVALUATION_FAILED` 的门分布。
- 盘上 383 个六文件产物中：346 个 `provider=pydantic-function`（320 PASSED，92.5%），35 个 `provider=openai-compatible`（1 PASSED，2.9%）。四道安全/环境门在全部产物上 0 失败。
- 35 个真实模型产物中 32 个终态为 `MODEL_ERROR`；工具成功率 `STATIC_SKILL` 80/83=96%、`DIAGNOSTIC_KERNEL` 41/122=34%。
- 真实产物版本为 `p1.kernel.v1/v2`、`p1.static.v1/v2`、`p1.controller.v1`、`p1.evaluation.v1`；当前树为 `p1.kernel.v5`、`p1.static.v5`、`p1.controller.v4`、`p1.evaluator.v2`。当前配置无任何真实模型证据。

## 明确不做（YAGNI）

- 不修改 `evaluation.py` 的任何判分门、阈值或适用性规则。
- 不修改 `diagnostic_agent.py`、`diagnostic_kernel.py`、`fixed_rule.py`、提示词文件、预算常量（8/8/2/300）。
- 不修改 `config/scenarios/*.json`、`config/profiles/*.json`、已封存的 `config/benchmark/p1-formal-v1.json`。
- 不修改 `doctor.py`：`DoctorResult.validate_complete_checks` 强制 13 项检查齐全有序，豁免逻辑只能落在 receipt 接受规则。
- 不引入新依赖。
- 不为 v2 改动 106 格调度、cell 计数向量或 `run_id` 派生方式。

## 文件结构

| 文件 | 动作 | 责任 |
|---|---|---|
| `src/data_incident_gym/benchmark_manifest.py` | 修改 | 版本化 manifest 路径派生与批准身份清单 |
| `src/data_incident_gym/benchmark_runner.py` | 修改 | cell 子集选择器、subset 标记、receipt 范围接受规则 |
| `src/data_incident_gym/benchmark_report.py` | 修改 | 拒绝为 subset 套件出具正式报告 |
| `src/data_incident_gym/benchmark_archive.py` | 新建 | 只读归档：复制取证文件 + 每格门矩阵 + 聚合哈希 |
| `src/data_incident_gym/cli.py` | 修改 | manifest 路径校验版本化、run/preflight 的 `--only-strategy`/`--only-sequence` 选项、`benchmark archive` 命令 |
| `tests/unit/test_benchmark_manifest.py` | 修改 | 覆盖版本化路径与批准身份 |
| `tests/unit/test_benchmark_runner.py` | 修改 | 覆盖子集选择、subset 标记、receipt 范围规则 |
| `tests/unit/test_benchmark_report.py` | 修改 | 覆盖 subset 拒绝 |
| `tests/unit/test_benchmark_archive.py` | 新建 | 覆盖归档独占写入、门矩阵、聚合哈希 |
| `tests/unit/test_cli.py` | 修改 | 覆盖新选项与新命令的路径校验 |

## Manifest 身份分配（冻结后永不复用）

| Manifest id | 唯一用途 | 是否允许正式报告 |
|---|---|---|
| `p1-formal-v1` | 历史封存批次；保持只读 | 按现有失效边界处理，不重算 |
| `p1-formal-v2` | 第二次正式 106 格批次；在 Task 7 前不得 freeze 或创建 suite | 是 |
| `p1-formal-v3` | Task 5 的 12 格 `FIXED_RULE` smoke | 否，必须有 `subset.json` |
| `p1-formal-v4` | Task 6 的 8 格真实模型 smoke | 否，必须有 `subset.json` |

三个新身份分别绑定独立的 manifest 文件、suite 目录与 receipt。不得通过删除、移动或清空已有 suite 来复用身份；`APPROVED_MANIFEST_IDS` 必须一次性包含上述四个身份，避免在执行门中临时改代码。

## 授权与提交纪律

Task 1-4 每个任务结束时都停在工作区；代码提交与 push 仍由用户单独授权。Task 5-7 是三套彼此独立的执行门，**每一套都需单独授权**，失败不重试、不补位、不替换样本。

runner 的 `_verify_checkout` 强制 clean checkout，因此每次 `freeze` 后必须先暂停，等待用户单独授权一个**仅含该 Manifest 文件**的包装提交，才能 preflight。每次 smoke 归档后也必须先把跟踪证据提交或由用户明确决定其他处置，下一套 suite 才能在 clean checkout 上开始。本计划列出这些必要的提交命令，但列出不等于授权；不包含任何自动 push。v2/v3/v4 必须绑定 Task 1-4 代码提交的同一个、已通过 CI 的 `implementation_revision`，后续仅含 Manifest/报告的提交只能作为它的后代，不能改变结果输入。

---

### Task 0: 复看现有 harness 回归证据（无代码改动）

**Files:** 无

- [ ] **Step 1: 运行 benchmark 三件套的确定性回归**

Run:
```powershell
uv run pytest tests/unit/test_benchmark_manifest.py tests/unit/test_benchmark_runner.py tests/unit/test_benchmark_report.py -q
```
Expected: 全部 PASS，exit 0。这证明 postmortem 缺陷 #3（setup 失败物化）、#4（恢复传播）、#5（fail-stop）在 `b636c6f` 已有确定性覆盖。

- [ ] **Step 2: 记录基线测试总数**

Run:
```powershell
uv run pytest tests/unit -q
```
Expected: `327 passed`（与 CI run `33625447982` 一致）。把这个数字写进后续每个 Task 的验证记录，任何下降都必须是本计划有意删除的测试。

- [ ] **Step 3: 确认封存 manifest 在当前树上按设计被拒**

Run:
```powershell
uv run data-incident-gym benchmark verify --manifest config/benchmark/p1-formal-v1.json
```
Expected: 非零退出，消息含 `result-input hashes drifted`。这是保护行为，**不是需要修复的缺陷**；不要为了让它通过而修改 `config/benchmark/p1-formal-v1.json`。

---

### Task 1: manifest 路径版本化

**Files:**
- Modify: `src/data_incident_gym/benchmark_manifest.py:50-51`、`:81`、`:485-497`、`:558-586`、`:605-620`
- Modify: `src/data_incident_gym/cli.py:117-139`
- Test: `tests/unit/test_benchmark_manifest.py`、`tests/unit/test_cli.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/unit/test_benchmark_manifest.py`：

```python
def test_manifest_path_is_derived_from_manifest_id() -> None:
    assert manifest_path_for(MANIFEST_ID) == MANIFEST_PATH
    assert manifest_path_for("p1-formal-v2") == Path("config/benchmark/p1-formal-v2.json")
    assert manifest_path_for("p1-formal-v3") == Path("config/benchmark/p1-formal-v3.json")
    assert manifest_path_for("p1-formal-v4") == Path("config/benchmark/p1-formal-v4.json")


def test_manifest_path_rejects_unversioned_identity() -> None:
    with pytest.raises(BenchmarkManifestError):
        manifest_path_for("p1-formal")
    with pytest.raises(BenchmarkManifestError):
        manifest_path_for("p2-formal-v1")


def test_build_manifest_accepts_approved_rerun_identities() -> None:
    for manifest_id in ("p1-formal-v2", "p1-formal-v3", "p1-formal-v4"):
        manifest = build_manifest(
            "b" * 40,
            project_root=PROJECT_ROOT,
            manifest_id=manifest_id,
        )

        assert manifest.manifest_id == manifest_id
        assert manifest.total_cells == 106
        assert manifest.model_backed_count == 94


def test_build_manifest_rejects_unapproved_identity() -> None:
    with pytest.raises(BenchmarkManifestError):
        build_manifest("b" * 40, project_root=PROJECT_ROOT, manifest_id="p1-formal-v5")


def test_freeze_manifest_writes_to_id_derived_path(tmp_path: Path) -> None:
    manifest = build_manifest("b" * 40, project_root=PROJECT_ROOT, manifest_id="p1-formal-v2")
    output = tmp_path / "config" / "benchmark" / "p1-formal-v2.json"

    written = freeze_manifest(manifest, output, project_root=tmp_path)

    assert written == output.resolve()
    assert json.loads(written.read_text(encoding="utf-8"))["manifest_id"] == "p1-formal-v2"


def test_freeze_manifest_rejects_path_that_disagrees_with_id(tmp_path: Path) -> None:
    manifest = build_manifest("b" * 40, project_root=PROJECT_ROOT, manifest_id="p1-formal-v2")
    wrong = tmp_path / "config" / "benchmark" / "p1-formal-v1.json"

    with pytest.raises(BenchmarkManifestError):
        freeze_manifest(manifest, wrong, project_root=tmp_path)
```

并在该文件的 import 块补入 `manifest_path_for`：

```python
from data_incident_gym.benchmark_manifest import (
    CONFIRMABLE_SCENARIO_IDS,
    FORMAL_SCENARIO_IDS,
    MANIFEST_ID,
    MANIFEST_PATH,
    BenchmarkManifest,
    BenchmarkManifestError,
    ManifestModelConfiguration,
    build_manifest,
    freeze_manifest,
    generate_cells,
    load_manifest,
    manifest_path_for,
    run_id_for_cell,
    verify_manifest,
)
```

追加到 `tests/unit/test_cli.py`：

```python
def test_canonical_manifest_path_accepts_approved_rerun_identities() -> None:
    for manifest_id in ("p1-formal-v2", "p1-formal-v3", "p1-formal-v4"):
        resolved = _canonical_benchmark_manifest_path(
            Path(f"config/benchmark/{manifest_id}.json")
        )

        assert resolved == (PROJECT_ROOT / f"config/benchmark/{manifest_id}.json").resolve()


def test_canonical_manifest_path_rejects_unapproved_name() -> None:
    with pytest.raises(BenchmarkManifestError):
        _canonical_benchmark_manifest_path(Path("config/benchmark/p1-formal-v9.json"))
```

- [ ] **Step 2: 运行测试确认失败**

Run:
```powershell
uv run pytest tests/unit/test_benchmark_manifest.py tests/unit/test_cli.py -q
```
Expected: FAIL，`ImportError: cannot import name 'manifest_path_for'`。

- [ ] **Step 3: 实现路径派生**

在 `src/data_incident_gym/benchmark_manifest.py` 中，紧接 `_MANIFEST_ID_PATTERN = r"^p1-formal-v[1-9][0-9]*$"`（第 81 行）之后插入：

```python
APPROVED_MANIFEST_IDS = (
    "p1-formal-v1",
    "p1-formal-v2",
    "p1-formal-v3",
    "p1-formal-v4",
)


def manifest_path_for(manifest_id: str) -> Path:
    """Return the canonical repository path for an approved manifest identity."""

    if re.fullmatch(_MANIFEST_ID_PATTERN, manifest_id) is None:
        raise BenchmarkManifestError("manifest_id must match p1-formal-v<N>")
    return Path(f"config/benchmark/{manifest_id}.json")
```

保留第 51 行的 `MANIFEST_PATH = Path("config/benchmark/p1-formal-v1.json")` 原样不动（它在 `__all__` 中且被 CLI 用作默认值），由 `test_manifest_path_is_derived_from_manifest_id` 保证两者一致。

- [ ] **Step 4: 放开 build_manifest 的单身份锁**

把 `build_manifest` 中这两行（第 495-496 行）：

```python
    if manifest_id != MANIFEST_ID:
        raise BenchmarkManifestError("only p1-formal-v1 is approved for M11")
```

替换为：

```python
    if manifest_id not in APPROVED_MANIFEST_IDS:
        raise BenchmarkManifestError(
            "manifest_id must be an approved formal identity: "
            + ", ".join(APPROVED_MANIFEST_IDS)
        )
```

- [ ] **Step 5: 让 freeze_manifest 按 manifest_id 派生期望路径**

把 `freeze_manifest` 中这两行（第 571-573 行）：

```python
    expected = (project_root / MANIFEST_PATH).resolve(strict=False)
    if output != expected:
        raise BenchmarkManifestError("manifest output must be config/benchmark/p1-formal-v1.json")
```

替换为：

```python
    expected = (project_root / manifest_path_for(manifest.manifest_id)).resolve(strict=False)
    if output != expected:
        raise BenchmarkManifestError(
            f"manifest output must be config/benchmark/{manifest.manifest_id}.json"
        )
```

其余三重覆盖保护（两次 `exists()` 检查、symlink 拒绝、`open("x")` 独占创建）保持不变。

- [ ] **Step 6: 导出新符号**

在 `benchmark_manifest.py` 的 `__all__`（第 605-620 行区域）中按字母序补入 `"APPROVED_MANIFEST_IDS"` 与 `"manifest_path_for"`。

- [ ] **Step 7: CLI 路径校验版本化**

把 `src/data_incident_gym/cli.py` 的 `_canonical_benchmark_manifest_path`（第 117-129 行）整体替换为：

```python
def _canonical_benchmark_manifest_path(path: Path) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    if candidate.is_symlink():
        raise BenchmarkManifestError("formal manifest path must not be a symlink")
    resolved = candidate.resolve(strict=False)
    approved = {
        (PROJECT_ROOT / manifest_path_for(manifest_id)).resolve(strict=False)
        for manifest_id in APPROVED_MANIFEST_IDS
    }
    if resolved not in approved:
        raise BenchmarkManifestError(
            "formal manifest path must be config/benchmark/<approved-manifest-id>.json"
        )
    return resolved
```

并在 `_confirmed_benchmark_manifest`（第 132-139 行）的 `loaded = load_manifest(manifest_path)` 之后、`verify_manifest(loaded)` 之前插入文件名与身份一致性检查：

```python
    if manifest_path.stem != loaded.manifest_id:
        raise BenchmarkManifestError("manifest file name must match its manifest_id")
```

同时把 cli.py 顶部从 `benchmark_manifest` 的 import 补入 `APPROVED_MANIFEST_IDS` 与 `manifest_path_for`。

- [ ] **Step 8: 运行测试确认通过**

Run:
```powershell
uv run pytest tests/unit/test_benchmark_manifest.py tests/unit/test_cli.py -q
```
Expected: PASS，exit 0。

- [ ] **Step 9: 全量回归 + 静态检查**

Run:
```powershell
uv run pytest tests/unit -q
uv run ruff check .
uv lock --check
git diff --check
```
Expected: unit 为 `327 passed` 加本 Task 新增的 8 项；ruff / lock / diff 全部 exit 0。

- [ ] **Step 10: 停在工作区**

不 `git add`、不 commit、不 push。记录改动的文件清单等待授权。

---

### Task 2: benchmark run 的 cell 子集选择与 subset 标记

**Files:**
- Modify: `src/data_incident_gym/benchmark_runner.py:84-88`（文件名常量）、`:168-180`（`BenchmarkRunResult`）、`:368-390`（`__init__`）、`:794-812`（`run` 开头）
- Modify: `src/data_incident_gym/benchmark_report.py:870-881`（`write`）
- Test: `tests/unit/test_benchmark_runner.py`、`tests/unit/test_benchmark_report.py`

- [ ] **Step 1: 写失败测试**

在 `tests/unit/test_benchmark_runner.py` 的 import 中加入：

```python
from pydantic import ValidationError

from data_incident_gym.benchmark_runner import BenchmarkCellSelector
```

把现有 `_runner` helper 改为：

```python
def _runner(
    manifest,
    tmp_path: Path,
    *,
    doctor_result: DoctorResult,
    calls: list[tuple[str, DiagnosticStrategy, str]],
    doctor_calls: list[str],
    writer: object | None = None,
    evaluation_runner_factory: object | None = None,
    cell_selector: BenchmarkCellSelector | None = None,
) -> BenchmarkRunner:
    return BenchmarkRunner(
        manifest,
        project_root=tmp_path,
        doctor_factory=lambda: _FakeDoctor(doctor_result, doctor_calls),
        evaluation_runner_factory=evaluation_runner_factory
        or (lambda: _FakeEvaluationRunner(calls)),
        artifact_writer=writer or _FakeWriter(),
        clock=lambda: NOW,
        checkout_verifier=lambda _manifest: None,
        checkout_revision_reader=lambda: "a" * 40,
        cell_selector=cell_selector,
    )
```

然后追加：

```python
def test_cell_selector_filters_by_strategy() -> None:
    manifest = build_manifest("a" * 40)
    selector = BenchmarkCellSelector(
        manifest_id=manifest.manifest_id,
        strategies=(DiagnosticStrategy.FIXED_RULE,),
    )

    selected = selector.select(manifest.cells)

    assert len(selected) == 12
    assert all(cell.strategy is DiagnosticStrategy.FIXED_RULE for cell in selected)
    assert all(cell.model_backed is False for cell in selected)


def test_cell_selector_filters_by_sequence() -> None:
    manifest = build_manifest("a" * 40)
    selector = BenchmarkCellSelector(
        manifest_id=manifest.manifest_id, sequences=(1, 2, 3)
    )

    selected = selector.select(manifest.cells)

    assert [cell.sequence for cell in selected] == [1, 2, 3]


def test_cell_selector_rejects_empty_duplicate_and_bad_sequence() -> None:
    with pytest.raises(ValidationError):
        BenchmarkCellSelector(manifest_id="p1-formal-v2")
    with pytest.raises(ValidationError):
        BenchmarkCellSelector(
            manifest_id="p1-formal-v2",
            strategies=(
                DiagnosticStrategy.FIXED_RULE,
                DiagnosticStrategy.FIXED_RULE,
            ),
        )
    with pytest.raises(ValidationError):
        BenchmarkCellSelector(manifest_id="p1-formal-v2", sequences=(0,))


def test_runner_rejects_selector_bound_to_another_manifest(
    tmp_path: Path,
) -> None:
    manifest = build_manifest("a" * 40)
    selector = BenchmarkCellSelector(
        manifest_id=(
            "p1-formal-v4"
            if manifest.manifest_id != "p1-formal-v4"
            else "p1-formal-v3"
        ),
        strategies=(DiagnosticStrategy.FIXED_RULE,),
    )

    with pytest.raises(BenchmarkRunnerError):
        _runner(
            manifest,
            tmp_path,
            doctor_result=_doctor_result(),
            calls=[],
            doctor_calls=[],
            cell_selector=selector,
        )


def test_full_runner_rejects_existing_subset_marker(tmp_path: Path) -> None:
    manifest = build_manifest("a" * 40)
    runner = _runner(
        manifest,
        tmp_path,
        doctor_result=_doctor_result(),
        calls=[],
        doctor_calls=[],
    )
    asyncio.run(runner.preflight())
    (runner._suite_root() / "subset.json").write_text(
        "{}\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(BenchmarkRunnerError, match="marked as a subset"):
        asyncio.run(runner.run())
```

在 `tests/unit/test_benchmark_report.py` 从 `benchmark_runner` 的 import 中加入 `BenchmarkCellSelector`，然后追加：

```python
def test_reporter_refuses_subset_suite(tmp_path: Path) -> None:
    manifest = build_manifest("a" * 40)
    suite_root = tmp_path / "artifacts" / "benchmarks" / manifest.manifest_id
    suite_root.mkdir(parents=True)
    (suite_root / "subset.json").write_text(
        BenchmarkCellSelector(
            manifest_id=manifest.manifest_id,
            strategies=(DiagnosticStrategy.FIXED_RULE,),
        ).model_dump_json(indent=2)
        + "\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(BenchmarkReportError, match="subset"):
        BenchmarkReporter(manifest, suite_root).write()
```

- [ ] **Step 2: 运行测试确认失败**

Run:
```powershell
uv run pytest tests/unit/test_benchmark_runner.py tests/unit/test_benchmark_report.py -q
```
Expected: FAIL，`ImportError: cannot import name 'BenchmarkCellSelector'`。

- [ ] **Step 3: 定义选择器与 subset 文件名**

在 `src/data_incident_gym/benchmark_runner.py` 的 `_LEDGER_FILENAME = "ledger.jsonl"`（第 84 行）附近补入：

```python
_SUBSET_FILENAME = "subset.json"
```

在 `BenchmarkLedgerEntry` 之后、`BenchmarkDoctorReceipt` 之前插入：

```python
class BenchmarkCellSelector(BaseModel):
    """An explicit, recorded restriction of a suite to a development smoke subset."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["p1.benchmark_subset.v1"] = "p1.benchmark_subset.v1"
    manifest_id: StrictStr
    strategies: tuple[DiagnosticStrategy, ...] = ()
    sequences: tuple[StrictInt, ...] = ()

    @model_validator(mode="after")
    def validate_selector_is_explicit_and_unique(self) -> Self:
        if not self.strategies and not self.sequences:
            raise ValueError("cell selector must restrict strategies or sequences")
        if len(set(self.strategies)) != len(self.strategies):
            raise ValueError("cell selector strategies must be unique")
        if len(set(self.sequences)) != len(self.sequences):
            raise ValueError("cell selector sequences must be unique")
        if any(value < 1 for value in self.sequences):
            raise ValueError("cell selector sequences must be at least 1")
        return self

    def select(self, cells: tuple[ManifestCell, ...]) -> tuple[ManifestCell, ...]:
        return tuple(
            cell
            for cell in cells
            if (not self.strategies or cell.strategy in self.strategies)
            and (not self.sequences or cell.sequence in self.sequences)
        )
```

在 `benchmark_runner.py` 从 `benchmark_manifest` 的 import 块明确补入 `ManifestCell`。

- [ ] **Step 4: runner 接受选择器**

在 `BenchmarkRunner.__init__`（第 369-388 行）的关键字参数末尾加入 `cell_selector: BenchmarkCellSelector | None = None`，并在赋值区加入：

```python
        if cell_selector is not None and cell_selector.manifest_id != manifest.manifest_id:
            raise BenchmarkRunnerError("cell selector is bound to another manifest")
        self._cell_selector = cell_selector
```

并新增两个方法（放在 `_suite_root` 之后）：

```python
    def _selected_cells(self) -> tuple[ManifestCell, ...]:
        if self._cell_selector is None:
            return self._manifest.cells
        return self._cell_selector.select(self._manifest.cells)

    def _model_probe_required(self) -> bool:
        return any(cell.model_backed for cell in self._selected_cells())

    def _write_subset_marker(self, suite_root: Path) -> None:
        path = suite_root / _SUBSET_FILENAME
        if path.is_symlink():
            raise BenchmarkRunnerError("benchmark subset marker must not be a symlink")
        if self._cell_selector is None:
            if path.exists():
                raise BenchmarkRunnerError(
                    "full benchmark cannot use a suite marked as a subset"
                )
            return
        payload = self._cell_selector.model_dump_json(indent=2) + "\n"
        if path.exists():
            try:
                existing = path.read_text(encoding="utf-8")
            except OSError as exc:
                raise BenchmarkRunnerError("cannot read benchmark subset marker") from exc
            if existing != payload:
                raise BenchmarkRunnerError(
                    "benchmark subset marker already exists for another selection"
                )
            return
        try:
            with path.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise BenchmarkRunnerError("cannot write benchmark subset marker") from exc
```

- [ ] **Step 5: run() 使用选中集并写标记**

在 `run()`（第 794 行起）中：

1. 在 `suite_root = self._suite_root()` 之后、`with _ExclusiveSuiteLock(lock_path):` 之前插入：

```python
        cells = self._selected_cells()
        if not cells:
            raise BenchmarkRunnerError("cell selector matched no manifest cell")
```

2. 进入锁后、`ledger = self._read_ledger(ledger_path)` 之前插入：

```python
            self._write_subset_marker(suite_root)
```

3. 把 `for cell in self._manifest.cells:`（第 808 行）改为 `for cell in cells:`。

4. 在构造 `BenchmarkRunResult` 处把 `total_cells` 改为 `len(cells)`，并补入 Step 6 新增的字段。

注意：`preflight()` 的 untouched-suite 检查（第 685-689 行）会把任何残留文件视为违规，因此 `subset.json` **只能由 `run()` 写**，不能由 `preflight()` 写。

- [ ] **Step 6: BenchmarkRunResult 记录 subset 语义**

在 `BenchmarkRunResult`（第 168-179 行）的 `ledger_path: Path` 之前插入：

```python
    subset: bool = False
    model_probe_required: bool = True
```

并在 `run()` 构造结果时传入 `subset=self._cell_selector is not None`、`model_probe_required=self._model_probe_required()`。

- [ ] **Step 7: reporter 拒绝 subset 套件**

在 `src/data_incident_gym/benchmark_report.py` 的 `write()`（第 870 行）第一行之前插入：

```python
        if (self._suite_root / "subset.json").is_symlink():
            self._fail("benchmark subset marker must not be a symlink")
        if (self._suite_root / "subset.json").exists():
            self._fail("subset suites cannot produce a formal report")
```

- [ ] **Step 8: 运行测试确认通过**

Run:
```powershell
uv run pytest tests/unit/test_benchmark_runner.py tests/unit/test_benchmark_report.py -q
```
Expected: PASS，exit 0。

- [ ] **Step 9: 全量回归 + 静态检查**

Run:
```powershell
uv run pytest tests/unit -q
uv run ruff check .
git diff --check
```
Expected: 全绿；unit 数量为 Task 1 结果再加本 Task 新增的 6 项。

- [ ] **Step 10: 停在工作区**

不 commit、不 push。

---

### Task 3: preflight receipt 的模型探针范围豁免

**Files:**
- Modify: `src/data_incident_gym/benchmark_runner.py:155-166`（`BenchmarkDoctorReceipt`）、`:573-611`（`_write_doctor_receipt`）、`:674-698`（`preflight`）、`:794-806`（`run` 的 receipt 校验）
- Test: `tests/unit/test_benchmark_runner.py`、`tests/unit/test_benchmark_report.py`

- [ ] **Step 1: 写失败测试**

在 `tests/unit/test_benchmark_runner.py` 从 `benchmark_runner` 的 import 块补入 `BenchmarkDoctorReceipt` 与 `is_receipt_acceptable`，然后加入以下 helper 与测试：

```python
MODEL_CHECKS = frozenset(
    {
        DoctorCheckCode.MODEL_ENDPOINT,
        DoctorCheckCode.MODEL_PRESENT,
        DoctorCheckCode.MODEL_TOOL_STRUCTURED_OUTPUT,
    }
)


def _doctor_without_model() -> DoctorResult:
    return DoctorResult(
        status=DoctorStatus.FAILED,
        checks=tuple(
            DoctorRunner._check(
                code,
                code not in MODEL_CHECKS,
                "OK" if code not in MODEL_CHECKS else "UNAVAILABLE",
            )
            for code in DoctorCheckCode
        ),
    )


def _scoped_receipt(
    *,
    manifest_id: str = "p1-formal-v3",
    model_probe_required: bool,
) -> BenchmarkDoctorReceipt:
    return BenchmarkDoctorReceipt(
        manifest_id=manifest_id,
        manifest_sha256="a" * 64,
        implementation_revision="a" * 40,
        checkout_revision="a" * 40,
        result_inputs_sha256="b" * 64,
        model_probe_required=model_probe_required,
        checked_at=NOW,
        result=_doctor_without_model(),
    )


def test_fixed_rule_receipt_is_acceptable_without_model_probe() -> None:
    fixed_rule_receipt = _scoped_receipt(model_probe_required=False)

    assert fixed_rule_receipt.model_probe_required is False
    assert fixed_rule_receipt.result.status is DoctorStatus.FAILED
    assert is_receipt_acceptable(fixed_rule_receipt) is True


def test_model_backed_receipt_requires_full_doctor_pass() -> None:
    model_receipt_with_failed_probe = _scoped_receipt(model_probe_required=True)

    assert model_receipt_with_failed_probe.model_probe_required is True
    assert is_receipt_acceptable(model_receipt_with_failed_probe) is False


def test_receipt_rejects_scope_mismatch_against_selected_cells(
    tmp_path: Path,
) -> None:
    manifest = build_manifest("a" * 40, manifest_id="p1-formal-v3")
    runner_with_fixed_rule_selector = _runner(
        manifest,
        tmp_path,
        doctor_result=_doctor_without_model(),
        calls=[],
        doctor_calls=[],
        cell_selector=BenchmarkCellSelector(
            manifest_id=manifest.manifest_id,
            strategies=(DiagnosticStrategy.FIXED_RULE,),
        ),
    )
    model_backed_receipt = _scoped_receipt(model_probe_required=True)

    with pytest.raises(BenchmarkRunnerError, match="scope"):
        runner_with_fixed_rule_selector.assert_receipt_scope(model_backed_receipt)
```

同时把现有 `test_preflight_runs_doctor_and_writes_receipt_before_execution` 对返回值的断言改为：

```python
    receipt = asyncio.run(runner.preflight())

    assert receipt.result.status is DoctorStatus.PASSED
    assert receipt.model_probe_required is True
```

在 `tests/unit/test_benchmark_report.py` 的 `_write_fixture` 中，给现有 `BenchmarkDoctorReceipt(` 增加：

```python
        model_probe_required=True,
```

正式 reporter 仍只接受完整模型 doctor 通过的 receipt；这只是为 schema v3 补齐必填范围字段。

- [ ] **Step 2: 运行测试确认失败**

Run:
```powershell
uv run pytest tests/unit/test_benchmark_runner.py -q
```
Expected: FAIL，`ImportError: cannot import name 'is_receipt_acceptable'`。

- [ ] **Step 3: receipt 增加范围字段**

把 `BenchmarkDoctorReceipt`（第 155-166 行）的 schema 版本与字段改为：

```python
class BenchmarkDoctorReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["p1.benchmark_doctor.v3"] = "p1.benchmark_doctor.v3"
    manifest_id: StrictStr
    manifest_sha256: Annotated[StrictStr, Field(pattern=_DIGEST_PATTERN)]
    implementation_revision: Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{40}$")]
    checkout_revision: Annotated[StrictStr, Field(pattern=_REVISION_PATTERN)]
    result_inputs_sha256: Annotated[StrictStr, Field(pattern=_DIGEST_PATTERN)]
    model_probe_required: bool
    checked_at: datetime
    result: DoctorResult
```

`p1.benchmark_doctor.v2` 无需兼容层：盘上不存在任何历史 receipt（封存 suite 已丢失），且 v2 receipt 无法通过新的范围校验。

- [ ] **Step 4: 增加接受规则**

在 `BenchmarkDoctorReceipt` 之后插入：

```python
_MODEL_CHECK_CODES = frozenset(
    {
        DoctorCheckCode.MODEL_ENDPOINT,
        DoctorCheckCode.MODEL_PRESENT,
        DoctorCheckCode.MODEL_TOOL_STRUCTURED_OUTPUT,
    }
)


def is_receipt_acceptable(receipt: BenchmarkDoctorReceipt) -> bool:
    """Return whether a receipt satisfies the suite scope it was issued for.

    A FIXED_RULE-only suite never issues a model request, so the three model
    checks are out of scope. They are still recorded verbatim in the receipt.
    """

    if receipt.model_probe_required:
        return receipt.result.status is DoctorStatus.PASSED
    return all(
        check.passed
        for check in receipt.result.checks
        if check.code not in _MODEL_CHECK_CODES
    )
```

在 `benchmark_runner.py` 从 `data_incident_gym.doctor` 的 import 块明确补入 `DoctorCheckCode`。

- [ ] **Step 5: preflight 计算并返回带范围的 receipt**

给 `_write_doctor_receipt` 增加 `model_probe_required: bool` 关键字参数，并在其 `BenchmarkDoctorReceipt(` 构造中加入：

```python
            model_probe_required=model_probe_required,
```

把 `_run_doctor` 整体替换为：

```python
    async def _run_doctor(
        self,
        path: Path,
        *,
        checkout_revision: str,
        model_probe_required: bool,
    ) -> BenchmarkDoctorReceipt:
        doctor = self._doctor_factory()
        try:
            result = doctor.run()
            if inspect.isawaitable(result):
                result = await result
            if not isinstance(result, DoctorResult):
                raise TypeError("doctor returned an invalid result")
        except Exception:
            result = _failed_doctor()
        return self._write_doctor_receipt(
            path,
            result,
            checkout_revision=checkout_revision,
            model_probe_required=model_probe_required,
        )
```

把 `preflight()` 的返回类型从 `DoctorResult` 改为 `BenchmarkDoctorReceipt`，并把结尾调用改为：

```python
            return await self._run_doctor(
                doctor_path,
                checkout_revision=checkout_revision,
                model_probe_required=self._model_probe_required(),
            )
```

不要先写 receipt 再返回裸 `DoctorResult`，否则 CLI 无法基于同一份持久化 receipt 判断范围是否可接受。

- [ ] **Step 6: run() 用范围规则替换全量 PASSED 要求**

把 `run()` 中这三行（第 803-805 行）：

```python
            doctor_result = receipt.result
            if doctor_result.status is not DoctorStatus.PASSED:
                raise BenchmarkRunnerError("doctor receipt is not PASSED")
```

替换为：

```python
            doctor_result = receipt.result
            self.assert_receipt_scope(receipt)
            if not is_receipt_acceptable(receipt):
                raise BenchmarkRunnerError(
                    "doctor receipt is not acceptable for this suite scope"
                )
```

并新增方法（放在 `_read_doctor_receipt` 之后）：

```python
    def assert_receipt_scope(self, receipt: BenchmarkDoctorReceipt) -> None:
        if receipt.model_probe_required != self._model_probe_required():
            raise BenchmarkRunnerError(
                "doctor receipt scope does not match the selected cells"
            )
        if receipt.manifest_id != self._manifest.manifest_id:
            raise BenchmarkRunnerError("doctor receipt is bound to another manifest")
```

- [ ] **Step 7: 运行测试确认通过**

Run:
```powershell
uv run pytest tests/unit/test_benchmark_runner.py -q
```
Expected: PASS，exit 0。

- [ ] **Step 8: 全量回归**

Run:
```powershell
uv run pytest tests/unit -q
uv run ruff check .
git diff --check
```
Expected: 全绿。

- [ ] **Step 9: 停在工作区**

不 commit、不 push。

---

### Task 4: CLI 暴露子集选项与 archive 命令

**Files:**
- Create: `src/data_incident_gym/benchmark_archive.py`
- Modify: `src/data_incident_gym/cli.py:52-58`（选项常量）、`:305-346`（`run` / `preflight`）、`:348-365`（`report` 之后新增命令）
- Create: `tests/unit/test_benchmark_archive.py`
- Modify: `tests/unit/test_cli.py`

- [ ] **Step 1: 写归档模块的失败测试**

创建 `tests/unit/test_benchmark_archive.py`。fixture 必须复现真实布局：suite 只放 ledger/receipt/report，六文件产物位于 `artifacts/<run_id>`，不能伪造为 suite 的子目录。

```python
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from data_incident_gym.benchmark_archive import ArchiveError, archive_suite
from data_incident_gym.benchmark_runner import BenchmarkLedgerEntry
from data_incident_gym.diagnosis import DiagnosticStrategy

SIX = (
    "metadata.json",
    "trace.jsonl",
    "evidence.json",
    "diagnosis.json",
    "evaluation.json",
    "report.md",
)
NOW = datetime(2026, 9, 3, tzinfo=UTC)


def _write_cell(project_root: Path, run_id: str) -> None:
    cell = project_root / "artifacts" / run_id
    cell.mkdir(parents=True)
    for name in SIX:
        (cell / name).write_text("{}\n", encoding="utf-8", newline="\n")
    metadata = {
        "incident_case_id": "row_count_drop_a",
        "run_id": run_id,
        "strategy": "FIXED_RULE",
        "recovery_status": "HEALTHY",
        "diagnosis_metrics": {
            "provider": "pydantic-function",
            "model": "deterministic",
            "model_requests": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "tool_call_attempts": 2,
            "successful_tool_calls": 2,
            "elapsed_ms": 7,
        },
    }
    evaluation = {
        "incident_case_id": "row_count_drop_a",
        "run_id": run_id,
        "status": "FAILED",
        "checks": [
            {
                "code": code,
                "applicability": "APPLICABLE",
                "passed": code != "ROOT_CAUSE_ACCEPTED",
            }
            for code in ("STATUS_EXACT", "ROOT_CAUSE_ACCEPTED", "RECOVERY_HEALTHY")
        ],
    }
    diagnosis = {"run_id": run_id, "status": "MODEL_ERROR"}
    for name, value in (
        ("metadata.json", metadata),
        ("diagnosis.json", diagnosis),
        ("evaluation.json", evaluation),
    ):
        (cell / name).write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )


def _write_ledger(suite_root: Path, manifest_id: str, run_id: str) -> None:
    values = (
        BenchmarkLedgerEntry.create(
            manifest_id=manifest_id,
            sequence=1,
            run_id=run_id,
            incident_case_id="row_count_drop_a",
            strategy=DiagnosticStrategy.FIXED_RULE,
            state="STARTED",
            now=NOW,
            started_at=NOW,
        ),
        BenchmarkLedgerEntry.create(
            manifest_id=manifest_id,
            sequence=1,
            run_id=run_id,
            incident_case_id="row_count_drop_a",
            strategy=DiagnosticStrategy.FIXED_RULE,
            state="FAILED",
            now=NOW,
            started_at=NOW,
            reason_code="EVALUATION_FAILED",
        ),
    )
    (suite_root / "ledger.jsonl").write_text(
        "\n".join(value.model_dump_json() for value in values) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def test_archive_reads_ledger_artifacts_and_builds_gate_matrix(tmp_path: Path) -> None:
    manifest_id = "p1-formal-v2"
    run_id = "a" * 32
    suite_root = tmp_path / "artifacts" / "benchmarks" / manifest_id
    suite_root.mkdir(parents=True)
    _write_ledger(suite_root, manifest_id, run_id)
    _write_cell(tmp_path, run_id)
    (suite_root / "doctor.json").write_text("{}\n", encoding="utf-8", newline="\n")
    (suite_root / "summary.json").write_text("{}\n", encoding="utf-8", newline="\n")
    (suite_root / "report.md").write_text("# r\n", encoding="utf-8", newline="\n")
    archive_root = tmp_path / "reports" / "benchmark" / manifest_id

    result = archive_suite(
        suite_root,
        archive_root,
        manifest_id,
        project_root=tmp_path,
    )

    assert (archive_root / "ledger.jsonl").exists()
    assert (archive_root / "doctor.json").exists()
    assert (archive_root / "summary.json").exists()
    assert (archive_root / "report.md").exists()
    rows = (archive_root / "gate-matrix.csv").read_text(encoding="utf-8").splitlines()
    assert rows[0].startswith("run_id,sequence,incident_case_id,strategy,diagnosis_status,")
    assert rows[1].startswith(
        f"{run_id},1,row_count_drop_a,FIXED_RULE,MODEL_ERROR,FAILED,"
    )
    assert ",2,2,7," in rows[1]
    assert result.cell_count == 1
    aggregate = json.loads(
        (archive_root / "aggregate_sha256.json").read_text(encoding="utf-8")
    )
    assert aggregate["source_file_count"] == 10
    assert result.aggregate_sha256 == aggregate["source_aggregate_sha256"]


def test_archive_rejects_existing_target(tmp_path: Path) -> None:
    manifest_id = "p1-formal-v2"
    suite_root = tmp_path / "artifacts" / "benchmarks" / manifest_id
    suite_root.mkdir(parents=True)
    archive_root = tmp_path / "reports" / "benchmark" / manifest_id
    archive_root.mkdir(parents=True)

    with pytest.raises(ArchiveError, match="already exists"):
        archive_suite(
            suite_root,
            archive_root,
            manifest_id,
            project_root=tmp_path,
        )


def test_archive_rejects_symlinked_suite(tmp_path: Path) -> None:
    manifest_id = "p1-formal-v2"
    real_suite = tmp_path / "real-suite"
    real_suite.mkdir()
    suite_root = tmp_path / "artifacts" / "benchmarks" / manifest_id
    suite_root.parent.mkdir(parents=True)
    try:
        suite_root.symlink_to(real_suite, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is not permitted on this platform")

    with pytest.raises(ArchiveError, match="symlink"):
        archive_suite(
            suite_root,
            tmp_path / "reports" / "benchmark" / manifest_id,
            manifest_id,
            project_root=tmp_path,
        )
```

- [ ] **Step 2: 运行测试确认失败**

Run:
```powershell
uv run pytest tests/unit/test_benchmark_archive.py -q
```
Expected: FAIL，collection error（`data_incident_gym.benchmark_archive` 不存在）。

- [ ] **Step 3: 实现归档模块**

创建 `src/data_incident_gym/benchmark_archive.py`：

```python
"""Preserve decisive benchmark records outside the ignored artifact tree."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import shutil
from pathlib import Path

from pydantic import BaseModel, ConfigDict, StrictInt, StrictStr

from data_incident_gym.artifacts import ARTIFACT_FILENAMES
from data_incident_gym.benchmark_runner import BenchmarkLedgerEntry
from data_incident_gym.config import PROJECT_ROOT

_COPIED_FILES = (
    "ledger.jsonl",
    "doctor.json",
    "subset.json",
    "summary.json",
    "report.md",
)
_GATE_ORDER = (
    "ENVIRONMENT_VERIFIED",
    "STATUS_EXACT",
    "ROOT_CAUSE_ACCEPTED",
    "AFFECTED_ASSETS_EXACT",
    "EVIDENCE_IDS_EXIST",
    "EVIDENCE_RUN_SCOPE",
    "REQUIRED_EVIDENCE_TYPES_PRESENT",
    "CLAIM_EVIDENCE_COMPATIBLE",
    "INSUFFICIENCY_GAP_DECLARED",
    "POSITIVE_HEALTH_EVIDENCE",
    "TOOL_ALLOWLIST_EXACT",
    "TRACE_READ_ONLY_SAFE",
    "RECOVERY_HEALTHY",
)
_METRIC_FIELDS = (
    "provider",
    "model",
    "model_requests",
    "input_tokens",
    "output_tokens",
    "tool_call_attempts",
    "successful_tool_calls",
    "elapsed_ms",
)


class ArchiveError(RuntimeError):
    """Raised when a suite cannot be archived safely."""


class ArchiveResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    manifest_id: StrictStr
    archive_root: StrictStr
    cell_count: StrictInt
    aggregate_sha256: StrictStr


def _reject_symlink(path: Path, message: str) -> None:
    if path.is_symlink():
        raise ArchiveError(message)


def _load_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ArchiveError(f"cannot read benchmark JSON: {path.name}") from exc
    if not isinstance(value, dict):
        raise ArchiveError(f"benchmark JSON must be an object: {path.name}")
    return value


def _terminal_entries(
    suite_root: Path,
    manifest_id: str,
) -> tuple[BenchmarkLedgerEntry, ...]:
    ledger_path = suite_root / "ledger.jsonl"
    if ledger_path.is_symlink() or not ledger_path.is_file():
        raise ArchiveError("benchmark ledger is missing or invalid")
    try:
        lines = ledger_path.read_text(encoding="utf-8").splitlines()
        entries = tuple(BenchmarkLedgerEntry.model_validate_json(line) for line in lines)
    except Exception as exc:
        raise ArchiveError("benchmark ledger is invalid") from exc
    if not entries or len(entries) % 2:
        raise ArchiveError("benchmark ledger must contain complete entry pairs")
    terminals: list[BenchmarkLedgerEntry] = []
    for index in range(0, len(entries), 2):
        started, terminal = entries[index : index + 2]
        identity = (started.manifest_id, started.sequence, started.run_id)
        if (
            identity != (terminal.manifest_id, terminal.sequence, terminal.run_id)
            or started.manifest_id != manifest_id
            or started.state != "STARTED"
            or terminal.state not in {"COMPLETED", "FAILED"}
            or terminal.started_at != started.started_at
        ):
            raise ArchiveError("benchmark ledger contains an invalid entry pair")
        terminals.append(terminal)
    if len({entry.run_id for entry in terminals}) != len(terminals):
        raise ArchiveError("benchmark ledger repeats a terminal cell")
    return tuple(terminals)


def _artifact_path(project_root: Path, entry: BenchmarkLedgerEntry) -> Path:
    artifacts_root = project_root / "artifacts"
    _reject_symlink(artifacts_root, "artifacts root must not be a symlink")
    artifacts_root = artifacts_root.resolve(strict=True)
    candidate = project_root / entry.artifact_path
    _reject_symlink(candidate, "cell artifact must not be a symlink")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ArchiveError(f"cell artifact is missing: {entry.run_id}") from exc
    if resolved != artifacts_root / entry.run_id or not resolved.is_dir():
        raise ArchiveError("ledger artifact path escaped its canonical location")
    children = tuple(resolved.iterdir())
    if {path.name for path in children} != set(ARTIFACT_FILENAMES) or any(
        path.is_symlink() or not path.is_file() for path in children
    ):
        raise ArchiveError(f"cell artifact is not the canonical six files: {entry.run_id}")
    return resolved


def _cell_matrix(
    project_root: Path,
    entries: tuple[BenchmarkLedgerEntry, ...],
) -> str:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(
        (
            "run_id",
            "sequence",
            "incident_case_id",
            "strategy",
            "diagnosis_status",
            "evaluation_status",
            "recovery_status",
            *_METRIC_FIELDS,
            *_GATE_ORDER,
        )
    )
    for entry in entries:
        artifact = _artifact_path(project_root, entry)
        metadata = _load_json(artifact / "metadata.json")
        diagnosis = _load_json(artifact / "diagnosis.json")
        evaluation = _load_json(artifact / "evaluation.json")
        if (
            metadata.get("run_id") != entry.run_id
            or metadata.get("incident_case_id") != entry.incident_case_id
            or metadata.get("strategy") != entry.strategy.value
            or diagnosis.get("run_id") != entry.run_id
            or evaluation.get("run_id") != entry.run_id
            or evaluation.get("incident_case_id") != entry.incident_case_id
        ):
            raise ArchiveError(f"cell identity does not match ledger: {entry.run_id}")
        metrics = metadata.get("diagnosis_metrics")
        checks = evaluation.get("checks")
        if not isinstance(metrics, dict) or not isinstance(checks, list):
            raise ArchiveError(f"cell metrics or checks are invalid: {entry.run_id}")
        gates = {
            check.get("code"): (
                "NOT_APPLICABLE"
                if check.get("applicability") != "APPLICABLE"
                else "PASS" if check.get("passed") is True else "FAIL"
            )
            for check in checks
            if isinstance(check, dict)
        }
        writer.writerow(
            (
                entry.run_id,
                entry.sequence,
                entry.incident_case_id,
                entry.strategy.value,
                diagnosis.get("status"),
                evaluation.get("status"),
                metadata.get("recovery_status"),
                *(metrics.get(field) for field in _METRIC_FIELDS),
                *(gates.get(code, "NOT_APPLICABLE") for code in _GATE_ORDER),
            )
        )
    return stream.getvalue()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_aggregate(
    project_root: Path,
    suite_root: Path,
    entries: tuple[BenchmarkLedgerEntry, ...],
) -> tuple[str, int]:
    files = []
    for path in suite_root.rglob("*"):
        _reject_symlink(path, "benchmark suite must not contain a symlink")
        if path.is_file():
            files.append(path)
    for entry in entries:
        files.extend(_artifact_path(project_root, entry).iterdir())
    named = sorted(
        (
            path.relative_to(project_root).as_posix(),
            _sha256_file(path),
        )
        for path in files
    )
    digest = hashlib.sha256()
    for name, value in named:
        digest.update(name.encode("utf-8") + b"\0" + value.encode("ascii") + b"\0")
    return digest.hexdigest(), len(named)


def archive_suite(
    suite_root: Path,
    archive_root: Path,
    manifest_id: str,
    *,
    project_root: Path = PROJECT_ROOT,
) -> ArchiveResult:
    """Copy decisive suite records into a tracked directory without overwriting."""

    project_root = project_root.resolve(strict=True)
    suite = Path(suite_root)
    if not suite.is_absolute():
        suite = project_root / suite
    _reject_symlink(suite, "benchmark suite root must not be a symlink")
    expected_suite = project_root / "artifacts" / "benchmarks" / manifest_id
    if suite.resolve(strict=True) != expected_suite.resolve(strict=True):
        raise ArchiveError("benchmark suite root is not canonical")
    archive = Path(archive_root)
    if not archive.is_absolute():
        archive = project_root / archive
    _reject_symlink(archive, "archive root must not be a symlink")
    expected_archive = project_root / "reports" / "benchmark" / manifest_id
    if archive.resolve(strict=False) != expected_archive.resolve(strict=False):
        raise ArchiveError("archive root is not canonical")
    if archive.exists():
        raise ArchiveError("archive root already exists")

    entries = _terminal_entries(suite, manifest_id)
    matrix = _cell_matrix(project_root, entries)
    aggregate, file_count = _source_aggregate(project_root, suite, entries)

    archive.mkdir(parents=True, exist_ok=False)
    try:
        for name in _COPIED_FILES:
            source = suite / name
            if not source.is_file():
                continue
            _reject_symlink(source, "archived suite file must not be a symlink")
            shutil.copyfile(source, archive / name)
        payloads = (
            ("gate-matrix.csv", matrix),
            (
                "aggregate_sha256.json",
                json.dumps(
                    {
                        "manifest_id": manifest_id,
                        "source_file_count": file_count,
                        "cell_count": len(entries),
                        "source_aggregate_sha256": aggregate,
                    },
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
            ),
        )
        for name, payload in payloads:
            with (archive / name).open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
    except OSError as exc:
        shutil.rmtree(archive, ignore_errors=True)
        raise ArchiveError("无法写入 benchmark 归档") from exc

    return ArchiveResult(
        manifest_id=manifest_id,
        archive_root=archive.relative_to(project_root).as_posix(),
        cell_count=len(entries),
        aggregate_sha256=aggregate,
    )


__all__ = ["ArchiveError", "ArchiveResult", "archive_suite"]
```

- [ ] **Step 4: 运行归档测试确认通过**

Run:
```powershell
uv run pytest tests/unit/test_benchmark_archive.py -q
```
Expected: `3 passed`，exit 0。

- [ ] **Step 5: CLI 让 run 与 preflight 共用同一子集选择器**

在 `src/data_incident_gym/cli.py` 的选项常量区（第 55-58 行附近）加入：

```python
BENCHMARK_STRATEGY_OPTION = typer.Option(
    None,
    "--only-strategy",
    help="开发期 smoke 子集：只执行该策略的 cell；会留下 subset.json，"
    "该 suite 永远无法出具正式报告。可重复传入。",
)
BENCHMARK_SEQUENCE_OPTION = typer.Option(
    None,
    "--only-sequence",
    help="开发期 smoke 子集：只执行该 sequence 的 cell。可重复传入。",
)
```

在 `_confirmed_benchmark_manifest` 之后新增：

```python
def _cell_selector(
    manifest,
    only_strategy: list[str],
    only_sequence: list[int],
) -> "BenchmarkCellSelector | None":
    if not only_strategy and not only_sequence:
        return None
    strategies = tuple(
        DiagnosticStrategy(value.upper().replace("-", "_")) for value in only_strategy
    )
    return BenchmarkCellSelector(
        manifest_id=manifest.manifest_id,
        strategies=strategies,
        sequences=tuple(only_sequence),
    )
```

把 `create_benchmark_runner` 改为接受并透传选择器：

```python
def create_benchmark_runner(manifest, selector=None) -> BenchmarkRunner:
    return BenchmarkRunner.for_project(manifest, cell_selector=selector)
```

`BenchmarkRunner.for_project` 同步新增 `cell_selector` 关键字参数并原样透传给 `__init__`。

把 `benchmark_run` 替换为：

```python
@benchmark_app.command("run")
def benchmark_run(
    manifest: Path = BENCHMARK_MANIFEST_OPTION,
    confirm_sha256: str = BENCHMARK_SHA256_OPTION,
    only_strategy: list[str] = BENCHMARK_STRATEGY_OPTION,
    only_sequence: list[int] = BENCHMARK_SEQUENCE_OPTION,
) -> None:
    """执行已冻结的 benchmark；无重试、替换或扩展样本选项。"""
    try:
        _, loaded = _confirmed_benchmark_manifest(manifest, confirm_sha256)
        selector = _cell_selector(loaded, only_strategy, only_sequence)
        result = asyncio.run(create_benchmark_runner(loaded, selector).run())
    except (BenchmarkManifestError, BenchmarkRunnerError, OSError, ValueError) as exc:
        typer.echo(f"benchmark 执行失败：{exc}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(f"status: {result.status}")
    typer.echo(f"cells: {result.terminal_cells}/{result.total_cells}")
    typer.echo(f"subset: {result.subset}")
    typer.echo(f"model_probe_required: {result.model_probe_required}")
    typer.echo(f"ledger: {result.ledger_path}")
    if result.status != "COMPLETED":
        raise typer.Exit(code=1)
```

把 `benchmark_preflight` 替换为：

```python
@benchmark_app.command("preflight")
def benchmark_preflight(
    manifest: Path = BENCHMARK_MANIFEST_OPTION,
    confirm_sha256: str = BENCHMARK_SHA256_OPTION,
    only_strategy: list[str] = BENCHMARK_STRATEGY_OPTION,
    only_sequence: list[int] = BENCHMARK_SEQUENCE_OPTION,
) -> None:
    """执行与所选 cell 范围绑定的 doctor；不会创建 cell 或 ledger。"""
    try:
        _, loaded = _confirmed_benchmark_manifest(manifest, confirm_sha256)
        selector = _cell_selector(loaded, only_strategy, only_sequence)
        receipt = asyncio.run(create_benchmark_runner(loaded, selector).preflight())
        acceptable = is_receipt_acceptable(receipt)
    except (BenchmarkManifestError, BenchmarkRunnerError, OSError, ValueError) as exc:
        typer.echo(f"benchmark preflight 失败：{exc}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(f"status: {'PASSED' if acceptable else 'FAILED'}")
    typer.echo(f"doctor_status: {receipt.result.status.value}")
    typer.echo(f"model_probe_required: {receipt.model_probe_required}")
    typer.echo(
        "receipt: "
        f"{PROJECT_ROOT / 'artifacts' / 'benchmarks' / loaded.manifest_id / 'doctor.json'}"
    )
    typer.echo("started_cells: 0")
    if not acceptable:
        raise typer.Exit(code=1)
```

同时在 cli.py import 中补入 `BenchmarkCellSelector`、`DiagnosticStrategy` 与 `is_receipt_acceptable`。preflight 与 run 必须收到完全相同的 selector flags；receipt 范围不一致时由 Task 3 的 `assert_receipt_scope` fail closed。

- [ ] **Step 6: CLI 增加 archive 命令**

在 `benchmark_report` 之后插入：

```python
@benchmark_app.command("archive")
def benchmark_archive(
    manifest: Path = BENCHMARK_MANIFEST_OPTION,
    confirm_sha256: str = BENCHMARK_SHA256_OPTION,
) -> None:
    """只读保全 suite 的取证数据；不会调用模型、数据库或 evaluator。"""
    try:
        _, loaded = _confirmed_benchmark_manifest(manifest, confirm_sha256)
        suite_root = PROJECT_ROOT / "artifacts" / "benchmarks" / loaded.manifest_id
        archive_root = PROJECT_ROOT / "reports" / "benchmark" / loaded.manifest_id
        result = archive_suite(suite_root, archive_root, loaded.manifest_id)
    except (
        ArchiveError,
        BenchmarkManifestError,
        BenchmarkRunnerError,
        OSError,
        ValueError,
    ) as exc:
        typer.echo(f"benchmark 归档失败：{exc}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(f"archive: {result.archive_root}")
    typer.echo(f"cells: {result.cell_count}")
    typer.echo(f"source sha256: {result.aggregate_sha256}")
```

并在 cli.py 顶部补入 `from data_incident_gym.benchmark_archive import ArchiveError, archive_suite`。

注意：`_confirmed_benchmark_manifest` 会调用 `verify_manifest`。不要为了归档旧的失效 manifest 而放宽 `verify_manifest`；该命令只归档刚刚在同一实现 revision 上完成的 suite。

- [ ] **Step 7: 写 CLI 测试**

追加 help、选择器和 archive 注册测试：

```python
def test_benchmark_run_and_preflight_help_expose_subset_options() -> None:
    for command in ("run", "preflight"):
        result = runner.invoke(app, ["benchmark", command, "--help"])

        assert result.exit_code == 0
        assert "--only-strategy" in result.stdout
        assert "--only-sequence" in result.stdout


def test_benchmark_archive_command_is_registered() -> None:
    result = runner.invoke(app, ["benchmark", "--help"])

    assert result.exit_code == 0
    assert "archive" in result.stdout


def test_cell_selector_maps_kebab_strategy_names() -> None:
    selector = cli._cell_selector(
        SimpleNamespace(manifest_id="p1-formal-v3"), ["fixed-rule"], []
    )

    assert selector is not None
    assert selector.strategies == (DiagnosticStrategy.FIXED_RULE,)
    assert selector.manifest_id == "p1-formal-v3"


def test_cell_selector_is_none_without_flags() -> None:
    assert cli._cell_selector(SimpleNamespace(manifest_id="p1-formal-v2"), [], []) is None
```

把现有 `test_benchmark_preflight_uses_confirmed_manifest_without_starting_cells` 替换为范围接受测试：

```python
def test_benchmark_preflight_accepts_fixed_rule_scope_without_model_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = SimpleNamespace(manifest_id="p1-formal-v3")
    receipt = SimpleNamespace(
        result=SimpleNamespace(status=DoctorStatus.FAILED),
        model_probe_required=False,
    )
    captured = []

    class StubRunner:
        async def preflight(self):
            return receipt

    monkeypatch.setattr(
        cli,
        "_confirmed_benchmark_manifest",
        lambda path, digest: (path, manifest),
    )
    monkeypatch.setattr(
        cli,
        "create_benchmark_runner",
        lambda loaded, selector=None: captured.append(selector) or StubRunner(),
    )
    monkeypatch.setattr(cli, "is_receipt_acceptable", lambda value: value is receipt)

    result = runner.invoke(
        app,
        [
            "benchmark",
            "preflight",
            "--manifest",
            "manifest.json",
            "--confirm-sha256",
            "abc",
            "--only-strategy",
            "fixed-rule",
        ],
    )

    assert result.exit_code == 0
    assert captured[0].strategies == (DiagnosticStrategy.FIXED_RULE,)
    assert "status: PASSED" in result.stdout
    assert "doctor_status: FAILED" in result.stdout
    assert "model_probe_required: False" in result.stdout
    assert "started_cells: 0" in result.stdout
```

- [ ] **Step 8: 运行测试确认通过**

Run:
```powershell
uv run pytest tests/unit/test_cli.py tests/unit/test_benchmark_archive.py -q
```
Expected: PASS，exit 0。

- [ ] **Step 9: 全量回归 + 打包检查**

Run:
```powershell
uv run pytest tests/unit -q
uv run ruff check .
uv lock --check
uv run data-incident-gym benchmark --help
git diff --check
```
Expected: 全绿；`benchmark --help` 列出 `freeze`、`verify`、`run`、`preflight`、`report`、`archive`。

- [ ] **Step 10: 停在工作区**

不 `git add`、不 commit、不 push。`reports/` 是计划新增的取证目录，必须保持未被 `.gitignore` 覆盖；是否提交其中的具体归档仍需用户单独授权。

---

### Task 5: 执行门 A —— 冻结 v3 Manifest 并跑 12 格 FIXED_RULE smoke

**Files:**
- Create: `config/benchmark/p1-formal-v3.json`
- Generate (ignored): `artifacts/benchmarks/p1-formal-v3/`、`artifacts/<run_id>/`
- Create: `reports/benchmark/p1-formal-v3/`
- Product code: 无改动

**前置条件（全部满足才可开始）**

- Task 1-4 的改动已由用户授权提交，且远端默认分支的 Ubuntu exact-HEAD CI 全绿（unit / integration / `pytest tests/e2e -m "not real_model"`）。
- 当前用户自有的 `AGENTS.md`、`decision.md` 等工作区文件不得提交或删除；开始前必须按用户另行确认的本地排除/保管方式，使 `git status --porcelain` 为空。
- Docker Desktop 运行中，`docker compose -f compose.yaml ps` 显示 postgres `running (healthy)`。
- `uv run data-incident-gym doctor` 除三项 `MODEL_*` 外全部通过。
- 用户单独授权本次 v3 suite；该授权不包含 Task 6 或 Task 7。

- [ ] **Step 1: 冻结 v3 Manifest 并捕获 digest**

Run:
```powershell
$implementationRev = git rev-parse HEAD
$freezeV3 = @(uv run data-incident-gym benchmark freeze --manifest-id p1-formal-v3 --implementation-revision $implementationRev --output config/benchmark/p1-formal-v3.json)
$freezeV3 | Write-Output
$shaV3 = [regex]::Match(($freezeV3 -join "`n"), '(?m)^sha256: ([0-9a-f]{64})$').Groups[1].Value
if ($shaV3.Length -ne 64) { throw '未能从 freeze 输出捕获 v3 sha256' }
```
Expected: exit 0，输出 `cells: 106; model_backed: 94; fixed_rule: 12`，且 `$shaV3` 是 64 位小写十六进制。

- [ ] **Step 2: 验证 v3 Manifest**

Run:
```powershell
uv run data-incident-gym benchmark verify --manifest config/benchmark/p1-formal-v3.json
```
Expected: exit 0，`verified: 17 catalog scenarios; 12 formal scenarios; 106 cells; 94 model-backed`。

- [ ] **Step 3: 暂停并等待 v3 Manifest-only 包装提交授权**

没有用户对这一个提交的明确授权时，不执行以下命令。获授权后只暂存 v3 Manifest：

```powershell
git add -- config/benchmark/p1-formal-v3.json
$staged = @(git diff --cached --name-only)
if ($staged.Count -ne 1 -or $staged[0] -ne 'config/benchmark/p1-formal-v3.json') {
    throw 'v3 Manifest 包装提交的暂存范围不精确'
}
git commit -m "chore: package fixed-rule smoke manifest"
if (git status --porcelain) { throw 'v3 preflight 前 checkout 仍不干净' }
```
Expected: 提交只含 `config/benchmark/p1-formal-v3.json`；checkout clean；不得 push。`implementation_revision` 仍是提交前的 `$implementationRev`，包装提交只是其后代。

- [ ] **Step 4: 对同一 FIXED_RULE 范围执行 preflight**

Run:
```powershell
$verifyV3 = @(uv run data-incident-gym benchmark verify --manifest config/benchmark/p1-formal-v3.json)
$shaV3 = [regex]::Match(($verifyV3 -join "`n"), '(?m)^sha256: ([0-9a-f]{64})$').Groups[1].Value
if ($shaV3.Length -ne 64) { throw '未能从 verify 输出恢复 v3 sha256' }
uv run data-incident-gym benchmark preflight --manifest config/benchmark/p1-formal-v3.json --confirm-sha256 $shaV3 --only-strategy fixed-rule
```
Expected: exit 0，`status: PASSED`、`model_probe_required: False`、`started_cells: 0`。若只有三项 `MODEL_*` 失败，可同时看到 `doctor_status: FAILED`；任何非模型检查失败都必须使 `status: FAILED` 并停止。preflight 与下一步 run 的 selector 必须字节级一致。

- [ ] **Step 5: 执行 12 格 FIXED_RULE smoke（需单独授权，只执行一次）**

Run:
```powershell
$verifyV3 = @(uv run data-incident-gym benchmark verify --manifest config/benchmark/p1-formal-v3.json)
$shaV3 = [regex]::Match(($verifyV3 -join "`n"), '(?m)^sha256: ([0-9a-f]{64})$').Groups[1].Value
if ($shaV3.Length -ne 64) { throw '未能从 verify 输出恢复 v3 sha256' }
uv run data-incident-gym benchmark run --manifest config/benchmark/p1-formal-v3.json --confirm-sha256 $shaV3 --only-strategy fixed-rule
```
Expected: `cells: 12/12`、`subset: True`、`model_probe_required: False`。`status: FAILED` 可能只是某格质量门失败，不能单独判为 harness 失败；必须按 Step 8 的安全/环境条件判断。

**严格约束**：不重跑、不补位、不修改场景或策略。任何 `RUN_SETUP_ERROR` 都按原样保留并触发 fail-stop。

- [ ] **Step 6: 确认 reporter 拒绝 subset 套件**

Run:
```powershell
$verifyV3 = @(uv run data-incident-gym benchmark verify --manifest config/benchmark/p1-formal-v3.json)
$shaV3 = [regex]::Match(($verifyV3 -join "`n"), '(?m)^sha256: ([0-9a-f]{64})$').Groups[1].Value
if ($shaV3.Length -ne 64) { throw '未能从 verify 输出恢复 v3 sha256' }
uv run data-incident-gym benchmark report --manifest config/benchmark/p1-formal-v3.json --confirm-sha256 $shaV3
```
Expected: 非零退出，消息含 `subset suites cannot produce a formal report`。

- [ ] **Step 7: 归档实际完成的 cell**

Run:
```powershell
$verifyV3 = @(uv run data-incident-gym benchmark verify --manifest config/benchmark/p1-formal-v3.json)
$shaV3 = [regex]::Match(($verifyV3 -join "`n"), '(?m)^sha256: ([0-9a-f]{64})$').Groups[1].Value
if ($shaV3.Length -ne 64) { throw '未能从 verify 输出恢复 v3 sha256' }
uv run data-incident-gym benchmark archive --manifest config/benchmark/p1-formal-v3.json --confirm-sha256 $shaV3
```
Expected: exit 0，生成 `reports/benchmark/p1-formal-v3/`，包含 `ledger.jsonl`、`doctor.json`、`subset.json`、`gate-matrix.csv`、`aggregate_sha256.json`。subset suite 没有 `summary.json` / `report.md` 是预期行为。

- [ ] **Step 8: 只按 harness 条件判定是否进入 Task 6**

检查归档的 `ledger.jsonl` 与 `gate-matrix.csv`：

| 观察 | 结论 |
|---|---|
| 12 格全部终态、0 个 `RUN_SETUP_ERROR`、`ENVIRONMENT_VERIFIED` 与 `RECOVERY_HEALTHY` 全部 `PASS` | harness smoke 通过，可请求 Task 6 的独立授权 |
| terminal cell 少于 12，或出现 `RUN_SETUP_ERROR` | fail-stop 保留；记录首个 `stage_code`，不得进入 Task 6 |
| 任一 `ENVIRONMENT_VERIFIED` / `RECOVERY_HEALTHY` 为 `FAIL` | Windows/dbt 环境未稳定，不得进入 Task 6 |
| 只有诊断质量门失败 | 如实记录，但不把它误判成 harness 失败 |

- [ ] **Step 9: 暂停并等待 v3 归档提交授权**

无论 harness 是否通过，都先报告结果并等待这一个证据提交的独立授权。获授权后：

```powershell
git add -- reports/benchmark/p1-formal-v3
$staged = @(git diff --cached --name-only)
if (-not $staged -or @($staged | Where-Object { $_ -notlike 'reports/benchmark/p1-formal-v3/*' }).Count) {
    throw 'v3 归档提交包含范围外文件'
}
git commit -m "docs: preserve fixed-rule smoke evidence"
if (git status --porcelain) { throw '进入 v4 smoke 前 checkout 仍不干净' }
```
Expected: 只提交 v3 跟踪归档，不提交 `artifacts/`，不 push。Task 6 仍需新的执行授权。

---

### Task 6: 执行门 B —— 冻结 v4 Manifest 并跑 8 格真实模型 smoke

**Files:**
- Create: `config/benchmark/p1-formal-v4.json`
- Generate (ignored): `artifacts/benchmarks/p1-formal-v4/`、`artifacts/<run_id>/`
- Create: `reports/benchmark/p1-formal-v4/`、`reports/benchmark/p1-formal-v4/smoke-report.md`
- Product code: 无改动

**前置条件**

- Task 5 的 harness 判定通过。
- v3 Manifest 与归档已分别经授权提交，checkout clean；不得通过删除 v3 文件来腾空工作区。
- 模型配置通过完整 doctor 探针。
- 用户单独授权一次性 8 格真实模型 smoke；不授权重试、第九格或 Task 7。

- [ ] **Step 1: 冻结 v4 Manifest 并捕获 digest**

Run:
```powershell
$implementationRev = (Get-Content -LiteralPath 'config\benchmark\p1-formal-v3.json' -Raw | ConvertFrom-Json).implementation_revision
git merge-base --is-ancestor $implementationRev HEAD
if ($LASTEXITCODE -ne 0) { throw 'v3 implementation_revision 不是当前 HEAD 的祖先' }
$freezeV4 = @(uv run data-incident-gym benchmark freeze --manifest-id p1-formal-v4 --implementation-revision $implementationRev --output config/benchmark/p1-formal-v4.json)
$freezeV4 | Write-Output
$shaV4 = [regex]::Match(($freezeV4 -join "`n"), '(?m)^sha256: ([0-9a-f]{64})$').Groups[1].Value
if ($shaV4.Length -ne 64) { throw '未能从 freeze 输出捕获 v4 sha256' }
```
Expected: exit 0，且 `$shaV4` 是 64 位小写十六进制。

- [ ] **Step 2: 暂停并等待 v4 Manifest-only 包装提交授权**

获单独授权后才执行：

```powershell
git add -- config/benchmark/p1-formal-v4.json
$staged = @(git diff --cached --name-only)
if ($staged.Count -ne 1 -or $staged[0] -ne 'config/benchmark/p1-formal-v4.json') {
    throw 'v4 Manifest 包装提交的暂存范围不精确'
}
git commit -m "chore: package real-model smoke manifest"
if (git status --porcelain) { throw 'v4 preflight 前 checkout 仍不干净' }
```
Expected: 只提交 v4 Manifest，不 push。

- [ ] **Step 3: 对精确 8 格范围执行 preflight**

Run:
```powershell
$verifyV4 = @(uv run data-incident-gym benchmark verify --manifest config/benchmark/p1-formal-v4.json)
$shaV4 = [regex]::Match(($verifyV4 -join "`n"), '(?m)^sha256: ([0-9a-f]{64})$').Groups[1].Value
if ($shaV4.Length -ne 64) { throw '未能从 verify 输出恢复 v4 sha256' }
uv run data-incident-gym benchmark preflight --manifest config/benchmark/p1-formal-v4.json --confirm-sha256 $shaV4 --only-sequence 1 --only-sequence 2 --only-sequence 3 --only-sequence 4 --only-sequence 5 --only-sequence 6 --only-sequence 7 --only-sequence 8
```
Expected: exit 0，`status: PASSED`、`doctor_status: PASSED`、`model_probe_required: True`、`started_cells: 0`。失败时按需求 §10.3 的兼容失败规则停止，不加 JSON 修补或模型专属循环。

- [ ] **Step 4: 用完全相同的 selector 执行 8 格（只执行一次）**

Run:
```powershell
$verifyV4 = @(uv run data-incident-gym benchmark verify --manifest config/benchmark/p1-formal-v4.json)
$shaV4 = [regex]::Match(($verifyV4 -join "`n"), '(?m)^sha256: ([0-9a-f]{64})$').Groups[1].Value
if ($shaV4.Length -ne 64) { throw '未能从 verify 输出恢复 v4 sha256' }
uv run data-incident-gym benchmark run --manifest config/benchmark/p1-formal-v4.json --confirm-sha256 $shaV4 --only-sequence 1 --only-sequence 2 --only-sequence 3 --only-sequence 4 --only-sequence 5 --only-sequence 6 --only-sequence 7 --only-sequence 8
```
Expected: `cells: 8/8`、`subset: True`、`model_probe_required: True`。若 terminal cell 少于 8，说明触发 fail-stop，立即停止；命令因质量失败返回非零但 8 格均终态时，仍可进入指标计算。

**严格约束**：不重跑、不补位、不追加第九格、不根据输出调场景或策略、不放宽 8/8/2/300。

- [ ] **Step 5: 归档并从归档矩阵计算唯一指标**

Run:
```powershell
$verifyV4 = @(uv run data-incident-gym benchmark verify --manifest config/benchmark/p1-formal-v4.json)
$shaV4 = [regex]::Match(($verifyV4 -join "`n"), '(?m)^sha256: ([0-9a-f]{64})$').Groups[1].Value
if ($shaV4.Length -ne 64) { throw '未能从 verify 输出恢复 v4 sha256' }
uv run data-incident-gym benchmark archive --manifest config/benchmark/p1-formal-v4.json --confirm-sha256 $shaV4
```
Expected: `reports/benchmark/p1-formal-v4/gate-matrix.csv` 恰有 8 个数据行；其中 sequence 1-8 对应 4 个 `DIAGNOSTIC_KERNEL` 与 4 个 `STATIC_SKILL` cell，并保留 `tool_call_attempts`、`successful_tool_calls`、`model_requests` 等指标。

从该 CSV 汇总：

| 策略 | 成功工具调用 / 尝试 | 成功率 | 平均模型请求 | MODEL_ERROR 数 |
|---|---|---|---|---|
| `DIAGNOSTIC_KERNEL` | | | | |
| `STATIC_SKILL` | | | | |

**判定门（本轮唯一目的）**

| Kernel 工具成功率 | 结论 | 下一步 |
|---|---|---|
| ≥ 80% | `p1.kernel.v5` 的提示词修复有效 | 可请求 Task 7 的独立授权 |
| 50-79% | 部分有效，intent 契约仍偏苛 | 记录后由用户决定是否另立契约修订计划；不进 Task 7 |
| < 50%（基线 34%） | intent pairing 对 `mimo-v2.5` 仍然太苛刻 | 另立契约修订计划；不进 Task 7 |

- [ ] **Step 6: 写受跟踪的 smoke 报告**

创建 `reports/benchmark/p1-formal-v4/smoke-report.md`，记录 Manifest id 与 sha256、implementation revision、CI run、8 格逐格结果、上表指标、判定结论，以及“本 smoke 是 subset，不进入任何正式分母”的边界声明。不要在本步骤修改提示词或契约。

- [ ] **Step 7: 暂停并等待 v4 证据提交授权**

获单独授权后才执行：

```powershell
git add -- reports/benchmark/p1-formal-v4
$staged = @(git diff --cached --name-only)
if (-not $staged -or @($staged | Where-Object { $_ -notlike 'reports/benchmark/p1-formal-v4/*' }).Count) {
    throw 'v4 证据提交包含范围外文件'
}
git commit -m "docs: preserve real-model smoke evidence"
if (git status --porcelain) { throw '进入正式 v2 批次前 checkout 仍不干净' }
```
Expected: 只提交 v4 归档与 smoke report，不提交 `artifacts/`，不 push。Task 7 仍需新的正式批次授权。

---

### Task 7: 执行门 C —— 冻结 v2 Manifest 并跑正式 106 格（需单独授权）

**Files:**
- Create: `config/benchmark/p1-formal-v2.json`
- Generate (ignored): `artifacts/benchmarks/p1-formal-v2/`、`artifacts/<run_id>/`
- Create: `reports/benchmark/p1-formal-v2/`、`RESULTS.md`
- Product code: 无改动

**前置条件（全部满足才可开始）**

- Task 6 的 Kernel 工具成功率 ≥ 80%。
- Task 5 的 harness 判定通过。
- v4 Manifest、归档与 smoke report 已分别经授权提交，checkout clean。
- `config/benchmark/p1-formal-v2.json` 与 `artifacts/benchmarks/p1-formal-v2/` 均从未创建；v2 身份一直保留给正式批次。
- v3/v4 绑定的同一个 `implementation_revision` 已在远端默认分支通过 Ubuntu exact-revision CI；后续提交只含 Manifest/报告，未改变 result inputs。
- 用户明确授权一次性正式 106 格批次，并理解不重试、不补位、不替换样本、不扩展预算。

- [ ] **Step 1: 冻结正式 v2 Manifest 并捕获 digest**

Run:
```powershell
$v3Revision = (Get-Content -LiteralPath 'config\benchmark\p1-formal-v3.json' -Raw | ConvertFrom-Json).implementation_revision
$v4Revision = (Get-Content -LiteralPath 'config\benchmark\p1-formal-v4.json' -Raw | ConvertFrom-Json).implementation_revision
if ($v3Revision -ne $v4Revision) { throw 'v3/v4 没有绑定同一个 implementation_revision' }
$implementationRev = $v3Revision
git merge-base --is-ancestor $implementationRev HEAD
if ($LASTEXITCODE -ne 0) { throw 'implementation_revision 不是当前 HEAD 的祖先' }
$freezeV2 = @(uv run data-incident-gym benchmark freeze --manifest-id p1-formal-v2 --implementation-revision $implementationRev --output config/benchmark/p1-formal-v2.json)
$freezeV2 | Write-Output
$shaV2 = [regex]::Match(($freezeV2 -join "`n"), '(?m)^sha256: ([0-9a-f]{64})$').Groups[1].Value
if ($shaV2.Length -ne 64) { throw '未能从 freeze 输出捕获 v2 sha256' }
```
Expected: exit 0；Manifest 为 106 格，其中 94 格 model-backed、12 格 `FIXED_RULE`。

- [ ] **Step 2: 暂停并等待 v2 Manifest-only 包装提交授权**

获单独授权后才执行：

```powershell
git add -- config/benchmark/p1-formal-v2.json
$staged = @(git diff --cached --name-only)
if ($staged.Count -ne 1 -or $staged[0] -ne 'config/benchmark/p1-formal-v2.json') {
    throw 'v2 Manifest 包装提交的暂存范围不精确'
}
git commit -m "chore: package formal benchmark manifest"
if (git status --porcelain) { throw '正式 preflight 前 checkout 仍不干净' }
```
Expected: 只提交 v2 Manifest，不 push。该包装提交不改变 Manifest 绑定的 `$implementationRev`。

- [ ] **Step 3: 执行无 selector 的完整 preflight**

Run:
```powershell
$verifyV2 = @(uv run data-incident-gym benchmark verify --manifest config/benchmark/p1-formal-v2.json)
$shaV2 = [regex]::Match(($verifyV2 -join "`n"), '(?m)^sha256: ([0-9a-f]{64})$').Groups[1].Value
if ($shaV2.Length -ne 64) { throw '未能从 verify 输出恢复 v2 sha256' }
uv run data-incident-gym benchmark preflight --manifest config/benchmark/p1-formal-v2.json --confirm-sha256 $shaV2
```
Expected: exit 0，`status: PASSED`、`doctor_status: PASSED`、`model_probe_required: True`、`started_cells: 0`。

- [ ] **Step 4: 执行正式 106 格（需单独授权，不带任何子集选项）**

Run:
```powershell
$verifyV2 = @(uv run data-incident-gym benchmark verify --manifest config/benchmark/p1-formal-v2.json)
$shaV2 = [regex]::Match(($verifyV2 -join "`n"), '(?m)^sha256: ([0-9a-f]{64})$').Groups[1].Value
if ($shaV2.Length -ne 64) { throw '未能从 verify 输出恢复 v2 sha256' }
uv run data-incident-gym benchmark run --manifest config/benchmark/p1-formal-v2.json --confirm-sha256 $shaV2
```
必须观察到 `subset: False`、`model_probe_required: True`。结果按以下条件处理：

| 观察 | 处理 |
|---|---|
| `cells: 106/106` 且 `status: COMPLETED` | 全部评估器通过，立即进入 Step 5 |
| `cells: 106/106` 且 `status: FAILED` | 存在诊断质量门失败，但 suite 完整；这不是 harness 失败，立即进入 Step 5 |
| terminal cell 少于 106 | fail-stop 导致批次不完整；不得生成正式报告，先归档现有证据并将结论固定为 `INVALID` |

- [ ] **Step 5: 先生成正式报告**

仅当 106 格全部终态时运行：

```powershell
$verifyV2 = @(uv run data-incident-gym benchmark verify --manifest config/benchmark/p1-formal-v2.json)
$shaV2 = [regex]::Match(($verifyV2 -join "`n"), '(?m)^sha256: ([0-9a-f]{64})$').Groups[1].Value
if ($shaV2.Length -ne 64) { throw '未能从 verify 输出恢复 v2 sha256' }
uv run data-incident-gym benchmark report --manifest config/benchmark/p1-formal-v2.json --confirm-sha256 $shaV2
```
Expected: exit 0，在 suite 中生成 `summary.json` 与 `report.md`，含 Wilson 95% 区间与四态结论（`KERNEL_ADVANTAGE` / `TRADEOFF` / `NOT_PROVEN` / `INVALID`）。报告必须先于独占归档生成，否则 archive 无法包含这两个文件。

- [ ] **Step 6: 立即执行一次独占归档**

Run:
```powershell
$verifyV2 = @(uv run data-incident-gym benchmark verify --manifest config/benchmark/p1-formal-v2.json)
$shaV2 = [regex]::Match(($verifyV2 -join "`n"), '(?m)^sha256: ([0-9a-f]{64})$').Groups[1].Value
if ($shaV2.Length -ne 64) { throw '未能从 verify 输出恢复 v2 sha256' }
uv run data-incident-gym benchmark archive --manifest config/benchmark/p1-formal-v2.json --confirm-sha256 $shaV2
```
完整 suite 的 Expected：`reports/benchmark/p1-formal-v2/` 含 `ledger.jsonl`、`doctor.json`、`summary.json`、`report.md`、`gate-matrix.csv`、`aggregate_sha256.json`，`cells: 106`。若 Step 3 fail-stop，则归档实际终态 cell，且不得伪称 106 格正式结果。

归档命令只允许调用一次；目标已存在时 fail closed，不通过覆盖或删除重建来“刷新”归档。

- [ ] **Step 7: 写对外 RESULTS.md**

仅对完整 106 格 suite，从 `reports/benchmark/p1-formal-v2/gate-matrix.csv` 与 `summary.json` 提炼根目录 `RESULTS.md`：

- **Abstention accuracy**：5 个 `TEST_INSUFFICIENT` 场景上正确返回 `INSUFFICIENT_EVIDENCE` 的比率。
- **False-alarm rate**：2 个 `NO_INCIDENT_CONTROL` 场景上正确返回 `NO_INCIDENT` 的比率。
- 3 臂 × 7 故障族混淆矩阵、消融对比、`FIXED_RULE` 对照、每格 token / 请求数 / 墙钟成本。

**措辞红线**：结论必须与 `benchmark_report.py` 的四态输出一致。若为 `NOT_PROVEN` 或 `INVALID`，不得表述为“Kernel 更优”或任何准确率优势。

- [ ] **Step 8: 停在工作区等待保全授权**

不 `git add`、不 commit、不 push，也不擅自复制到仓库外。列出 `reports/benchmark/p1-formal-v2/` 与 `RESULTS.md`，报告 aggregate SHA-256，等待用户分别授权提交、推送或指定仓库外备份位置。

---

## Self-Review

**Spec coverage（0-5 步逐项对照）**

| 用户要求 | 对应 Task |
|---|---|
| 第 0 步：复看 harness 回归证据 | Task 0 |
| 第 1 步 a：解除 manifest 路径硬编码 | Task 1 |
| 第 1 步 b：run/preflight 使用同一 cell 子集 | Task 2（runner）+ Task 4（CLI） |
| 第 1 步 c：doctor 模型探针范围豁免 | Task 3 |
| 第 2 步：12 格 FIXED_RULE smoke | Task 5，专用 v3 |
| 第 3 步：8 格真实模型 smoke + Kernel 工具成功率判定 | Task 6，专用 v4 |
| 第 4 步：冻结 v2 跑完整 106 格 | Task 7，保留 v2 |
| 第 5 步：产物保全 | Task 4 从 ledger 定位六文件产物；Task 5-7 独占归档 |

**已解决的一致性问题**

1. v2/v3/v4 身份已固定分工，任何 suite 都不删除、不移动、不复用。
2. preflight 与 run 必须使用相同 selector；纯 `FIXED_RULE` receipt 的范围接受成为唯一方案，不再保留不可执行的 A/B 分叉。
3. archive 从 ledger 的 `artifacts/<run_id>` 读取六文件产物，不再错误假设 cell 位于 suite 子目录。
4. 正式 suite 先 report、后 archive，保证独占归档一次即包含 `summary.json` 与 `report.md`。
5. “94 格”统一为“94 个 model-backed + 12 个 FIXED_RULE = 正式 106 格”。
6. `status: FAILED` 与 harness fail-stop 分开判断：全格终态的质量失败仍可报告，批次不完整才停止正式结论。

**Type consistency 核对**

- `manifest_path_for(manifest_id: str) -> Path`：Task 1 定义，CLI 与测试一致使用。
- `APPROVED_MANIFEST_IDS`：Task 1 一次性固定为 v1/v2/v3/v4，执行门不再临时扩充。
- `BenchmarkCellSelector(manifest_id, strategies, sequences)`：Task 2 定义；Task 4、Task 5、Task 6 对 preflight/run 一致透传。
- `BenchmarkDoctorReceipt.model_probe_required: bool`（schema `v3`）与 `preflight() -> BenchmarkDoctorReceipt`：Task 3 定义，Task 4 CLI 按同一 receipt 判定接受性。
- `BenchmarkRunResult.subset: bool` / `.model_probe_required: bool`：Task 2 定义，Task 4 CLI 输出使用。
- `archive_suite(suite_root, archive_root, manifest_id, *, project_root) -> ArchiveResult`：Task 4 的测试、实现与 CLI 一致；cell 来源由 ledger 的 `artifact_path` 决定。
- `_SUBSET_FILENAME = "subset.json"`：runner 写入、reporter 拒绝、archive 复制三处语义一致。

**Placeholder scan**：无 TBD / TODO / 日期占位符 / 未定义 digest 占位符 / “类似 Task N” / “补充适当错误处理”。运行期 digest 均从 freeze 输出捕获为 `$shaV2` / `$shaV3` / `$shaV4`。所有代码步骤给出完整代码，所有命令给出预期输出。
