"""Six-tool MCP entry (T10): stdio transport over the T09 protocol session.

The MCP surface exposes **exactly the six read-only evidence tools** and nothing
else — there is no submit tool here, because the final answer stays on the
``p1.strategy_adapter.v1`` protocol (see ``strategy_adapter``). Every call is
routed through the same ``StrategySession`` the in-process adapters use, so the
allowlist, run scope, budget gates, deadline, evidence registration, evidence
payloads and error codes are identical to the in-process path: the MCP layer
adds a transport, not a second policy.

Rules that hold on this surface:

- **Single-run serialization**: one server process serves one run and one
  session; tool calls run behind an asyncio lock, so registration order and
  counters stay deterministic. Throughput is never bought with cross-run state.
- **Capability narrowing only**: enabling a subset of tools narrows the surface;
  nothing can widen it past the six. The scenario's relation whitelist stays
  enforced by the backed evidence tools, not by MCP.
- **Real refusal receipts**: a refused call keeps the backend's error code; the
  receipt carries the evidence records (facts) that were actually returned.
- **Harness-owned log**: the optional ``recorder`` hook of ``ToolGateway`` hands
  the harness one authoritative entry per processed call. A remote client's own
  log is auxiliary and may be missing, trimmed or wrong without changing what
  the harness counted.
- **No ambient capabilities**: the server offers no dbt CLI, no SQL, no
  management command and no generic filesystem access.

Verified against ``mcp==1.20.0`` (locked in ``pyproject.toml``).
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Callable
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.shared.exceptions import McpError
from mcp.types import INVALID_PARAMS as MCP_INVALID_PARAMS
from mcp.types import ErrorData, Tool

from data_incident_gym.config import PROJECT_ROOT
from data_incident_gym.evidence_tools import EvidenceTools
from data_incident_gym.run_context import resolve_run_context
from data_incident_gym.strategy_adapter import (
    PROTOCOL_TOOL_ALLOWLIST,
    STRATEGY_PROTOCOL_VERSION,
    StrategySession,
    ToolRequest,
    builtin_declaration,
)

MCP_SERVER_NAME = "data-incident-gym-evidence"

#: Arguments of the six read-only evidence tools, in their canonical order.
MCP_TOOL_ARGUMENTS: dict[str, tuple[str, ...]] = {
    "get_dbt_run_results": ("run_id",),
    "get_dbt_node_error": ("run_id", "node_id"),
    "get_dbt_lineage": ("node_id", "direction"),
    "get_relation_schema": ("relation_name",),
    "get_relation_data_profile": ("relation_name",),
    "get_relation_history": ("relation_name",),
}

MCP_TOOL_DESCRIPTIONS: dict[str, str] = {
    "get_dbt_run_results": "Read the frozen dbt run results for this run",
    "get_dbt_node_error": "Read one failed node's recorded error for this run",
    "get_dbt_lineage": "Read the manifest lineage around one node",
    "get_relation_schema": "Read a whitelisted relation's schema snapshot",
    "get_relation_data_profile": "Read a whitelisted relation's data profile snapshot",
    "get_relation_history": "Read a whitelisted relation's history snapshot",
}


def receipt_payload(receipt: Any) -> dict[str, Any]:
    """The JSON-safe receipt shape shared with every MCP client."""

    return {
        "request_id": receipt.request_id,
        "accepted": receipt.accepted,
        "evidence_ids": list(receipt.evidence_ids),
        "evidence": [record.model_dump(mode="json") for record in receipt.evidence],
        "duplicate": receipt.duplicate,
        "error": None if receipt.error is None else receipt.error.model_dump(mode="json"),
        "tool_calls_used": receipt.tool_calls_used,
    }


class ToolGateway:
    """Transport-neutral six-tool gateway: one JSON-able receipt per call.

    The asyncio lock serializes every call of this run, which is what keeps
    request order, registration order and counters deterministic even when a
    client pipelines requests.

    ``recorder`` receives the authoritative log entry of every call the session
    processed, in order, as the harness's own record. It is a *harness* hook: a
    client cannot write to it, skip it or rewrite it, so an external process's
    own log stays auxiliary (see ``strategy_bridge``).
    """

    def __init__(
        self,
        session: StrategySession,
        *,
        recorder: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._session = session
        self._recorder = recorder
        self._lock = asyncio.Lock()
        self._sequence = 0

    @property
    def session(self) -> StrategySession:
        return self._session

    def tool_names(self) -> tuple[str, ...]:
        allowlist = set(self._session.task_context().tool_allowlist)
        return tuple(name for name in MCP_TOOL_ARGUMENTS if name in allowlist)

    async def call(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """One call, exactly as the client sent it.

        Arguments are forwarded verbatim: a missing key, an extra key, a
        non-string value or a foreign run id is judged by the session, so the
        client gets the same receipt and the same attempt counting as an
        in-process call. Nothing is cleaned, dropped or coerced here.
        """

        if tool_name not in MCP_TOOL_ARGUMENTS:
            raise ValueError(f"unknown tool: {tool_name}")
        async with self._lock:
            self._sequence += 1
            request = ToolRequest(
                request_id=f"{tool_name}:{self._sequence}",
                tool_name=tool_name,
                arguments=dict(arguments),
            )
            receipt = self._session.call_tool(request)
            payload = receipt_payload(receipt)
            if self._recorder is not None:
                self._recorder(
                    {
                        "sequence": self._sequence,
                        "tool_name": tool_name,
                        "arguments": request.arguments,
                        "receipt": payload,
                    }
                )
        return payload


def build_mcp_server(
    session: StrategySession,
    *,
    name: str = MCP_SERVER_NAME,
    recorder: Callable[[dict[str, Any]], None] | None = None,
) -> Server:
    """Build the stdio MCP server for one run-bound strategy session.

    Registered on the SDK's low-level server with ``validate_input=False``: the
    advertised schema documents the canonical argument names, but incoming
    arguments are delivered to the session **raw**, so any argument problem
    becomes a protocol receipt with the same error code and the same attempt
    counting as an in-process call. Letting the SDK's schema layer reject or
    drop arguments would bypass the unified receipts and budget.
    """

    gateway = ToolGateway(session, recorder=recorder)
    context = session.task_context()
    server: Server = Server(
        name,
        version="1.0",
        instructions=(
            f"{STRATEGY_PROTOCOL_VERSION} evidence surface for run "
            f"{context.run_id}; budget {context.budget.model_request_limit} model requests / "
            f"{context.budget.tool_call_limit} tool calls / {context.budget.timeout_seconds}s; "
            "the final answer is submitted through the protocol, not through MCP."
        ),
    )

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        """The six read-only evidence tools this run grants."""

        return [
            Tool(
                name=tool_name,
                description=MCP_TOOL_DESCRIPTIONS[tool_name],
                inputSchema={
                    "type": "object",
                    "properties": {
                        argument: {"type": "string"}
                        for argument in MCP_TOOL_ARGUMENTS[tool_name]
                    },
                    "required": list(MCP_TOOL_ARGUMENTS[tool_name]),
                    "additionalProperties": False,
                },
            )
            for tool_name in gateway.tool_names()
        ]

    @server.call_tool(validate_input=False)
    async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """One evidence call; the receipt carries facts, codes and counters."""

        if tool_name not in MCP_TOOL_ARGUMENTS:
            raise McpError(
                ErrorData(code=MCP_INVALID_PARAMS, message=f"unknown tool: {tool_name}")
            )
        return await gateway.call(tool_name, arguments)

    return server


async def _serve_stdio(server: Server) -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def serve_stdio(session: StrategySession) -> None:
    """Serve one run over the local stdio transport (blocking)."""

    asyncio.run(_serve_stdio(build_mcp_server(session)))


def _parse_allowlist(raw: str | None) -> frozenset[str]:
    if not raw:
        return PROTOCOL_TOOL_ALLOWLIST
    requested = {item.strip() for item in raw.split(",") if item.strip()}
    unknown = sorted(requested - set(PROTOCOL_TOOL_ALLOWLIST))
    if unknown:
        raise SystemExit(f"unknown tools are never granted: {', '.join(unknown)}")
    return frozenset(requested)


def main(argv: list[str] | None = None) -> int:
    """``python -m data_incident_gym.mcp_server --run-id <id> [--tools a,b]``."""

    import argparse

    parser = argparse.ArgumentParser(description="六工具只读 MCP 入口（stdio）")
    parser.add_argument("--run-id", required=True, help="verified run id served by this process")
    parser.add_argument(
        "--tools",
        default=None,
        help="comma-separated subset of the six tools (narrowing only)",
    )
    parser.add_argument("--framework", default="mcp-external")
    parser.add_argument("--model", default="external-client")
    parser.add_argument("--deterministic", action="store_true")
    args = parser.parse_args(argv)

    allowlist = _parse_allowlist(args.tools)
    context = resolve_run_context(args.run_id, project_root=PROJECT_ROOT)
    session = StrategySession(
        run_id=args.run_id,
        tools=_run_tools(args.run_id, allowlist),
        context=context,
        declaration=builtin_declaration(
            model_provider=args.framework,
            model_name=args.model,
            deterministic=args.deterministic,
        ),
        allowlist=allowlist,
    )
    print(
        json.dumps(
            {
                "protocol": STRATEGY_PROTOCOL_VERSION,
                "run_id": args.run_id,
                "tools": list(session.task_context().tool_allowlist),
            },
            ensure_ascii=False,
        ),
        file=sys.stderr,
    )
    serve_stdio(session)
    return 0


def _run_tools(run_id: str, allowlist: frozenset[str]) -> Any:
    """The real read-only evidence tools; the scenario whitelist stays theirs."""

    from data_incident_gym.diagnostic_config import DiagnosticSettings

    return EvidenceTools.for_run(run_id, DiagnosticSettings(_env_file=None), PROJECT_ROOT)


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main())
