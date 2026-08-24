from __future__ import annotations

import typer

from data_incident_gym.baseline import BaselineBuilder, BaselineError
from data_incident_gym.config import Settings

app = typer.Typer(help="可复现的数据事故诊断实验场。")
pipeline_app = typer.Typer(help="构建并检查 dbt 数据管道。")
app.add_typer(pipeline_app, name="pipeline")


def create_baseline_builder() -> BaselineBuilder:
    return BaselineBuilder(Settings())


@pipeline_app.command("build")
def pipeline_build() -> None:
    """重置 seeds，运行 dbt build，并生成健康基线摘要。"""
    try:
        summary = create_baseline_builder().build()
    except BaselineError as exc:
        typer.echo(f"健康基线构建失败：{exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo("健康基线构建成功。")
    typer.echo(f"schema: {summary.schema}")
    typer.echo(f"relations: {len(summary.relations)}")
    typer.echo(f"fingerprint: {summary.fingerprint}")
    typer.echo("summary: .dig/baseline-summary.json")
