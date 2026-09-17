"""Minimal external MCP client (T10): local stdio, six read-only tools.

The client spawns the server as a **separate process** over stdio — the same
transport a third-party agent would use — lists the tools, calls every one of
them and prints the receipts (accepted flag, evidence ids, preserved refusal
codes). It never sees the private scenario contract, the expected answer or the
case id: the public task context and the six tools are the whole surface.

    uv run python examples/mcp_client.py

The child process serves a deterministic synthetic evidence backend from the T09
example, so the demo needs no database and no model. For a real run the same
client talks to:

    uv run python -m data_incident_gym.mcp_server --run-id <verified-run-id>
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import textwrap
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

EXAMPLES_DIR = Path(__file__).resolve().parent

#: The child builds a synthetic session (public evidence only) and serves stdio.
BOOTSTRAP = textwrap.dedent(
    """
    import sys
    from pathlib import Path

    sys.path.insert(0, {examples!r})
    import external_strategy_client as evidence

    from data_incident_gym.mcp_server import serve_stdio

    backend = evidence.PathBackend(fail=True, refuse_profile_for="raw_customers")
    serve_stdio(evidence.open_session(backend))
    """
)

TEST_NODE = "test.jaffle_shop.not_null_orders_customer_id"

CALLS: tuple[tuple[str, dict[str, str]], ...] = (
    ("get_dbt_run_results", {"run_id": "e" * 32}),
    (
        "get_dbt_node_error",
        {"run_id": "e" * 32, "node_id": TEST_NODE},
    ),
    ("get_dbt_lineage", {"node_id": TEST_NODE, "direction": "downstream"}),
    ("get_relation_schema", {"relation_name": "raw_orders"}),
    ("get_relation_data_profile", {"relation_name": "raw_orders"}),
    ("get_relation_data_profile", {"relation_name": "raw_customers"}),
    ("get_relation_history", {"relation_name": "raw_orders"}),
)


async def run_client() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        bootstrap = Path(tmp) / "serve_synthetic_evidence.py"
        bootstrap.write_text(
            BOOTSTRAP.format(examples=str(EXAMPLES_DIR)), encoding="utf-8"
        )
        parameters = StdioServerParameters(
            command=sys.executable, args=[str(bootstrap)], cwd=str(EXAMPLES_DIR)
        )
        async with (
            stdio_client(parameters) as (read, write),
            ClientSession(read, write) as client,
        ):
            await client.initialize()
            tools = await client.list_tools()
            print(f"tools: {', '.join(tool.name for tool in tools.tools)}")
            for name, arguments in CALLS:
                result = await client.call_tool(name, arguments)
                payload = result.structuredContent or {}
                outcome = (
                    f"accepted evidence={len(payload.get('evidence_ids', []))}"
                    if payload.get("accepted")
                    else f"refused {payload.get('error', {}).get('code')}"
                )
                subject = arguments.get("relation_name") or arguments.get("node_id", "")
                print(f"[{name}] {subject}: {outcome} (used {payload['tool_calls_used']})")
    return 0


def main() -> int:
    return asyncio.run(run_client())


if __name__ == "__main__":
    raise SystemExit(main())
