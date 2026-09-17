"""T10 six-tool MCP entry tests: offline, over the SDK's in-process transport.

Every test drives a real MCP client session against a real MCP server built on a
synthetic evidence backend, so the transport, the tool schemas, the receipts and
the T09 session rules are all exercised without a database or a network.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from data_incident_gym.evidence import RelationNotAllowedError
from data_incident_gym.mcp_server import MCP_TOOL_ARGUMENTS, ToolGateway, build_mcp_server
from data_incident_gym.strategy_adapter import (
    PROTOCOL_TOOL_ALLOWLIST,
    ProtocolTools,
    StrategyDeclaration,
    StrategyProtocolError,
    StrategySession,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from unit.test_strategy_adapter import (  # noqa: E402
    RUN_ID,
    _context,
    _declaration,
    _run_record,
    _StaticTools,
)


def _session(
    tools: object | None = None,
    *,
    declaration: StrategyDeclaration | None = None,
    allowlist: frozenset[str] = PROTOCOL_TOOL_ALLOWLIST,
    clock=None,
) -> StrategySession:
    return StrategySession(
        run_id=RUN_ID,
        tools=tools or _StaticTools(_run_record()),
        context=_context(),
        declaration=declaration or _declaration(),
        allowlist=allowlist,
        clock=clock,
    )


async def _with_client(session: StrategySession, body) -> object:
    server = build_mcp_server(session)
    async with create_connected_server_and_client_session(server) as client:
        return await body(client)


def test_serves_exactly_the_six_evidence_tools() -> None:
    session = _session()

    async def body(client) -> list[tuple[str, tuple[str, ...]]]:
        tools = await client.list_tools()
        return [
            (tool.name, tuple(sorted(tool.inputSchema.get("properties", {}))))
            for tool in tools.tools
        ]

    served = asyncio.run(_with_client(session, body))

    assert sorted(name for name, _ in served) == sorted(MCP_TOOL_ARGUMENTS)
    for name, arguments in served:
        assert arguments == tuple(sorted(MCP_TOOL_ARGUMENTS[name]))


def test_no_submission_tool_exists_on_the_mcp_surface() -> None:
    session = _session()

    async def body(client) -> list[str]:
        tools = await client.list_tools()
        return [tool.name for tool in tools.tools]

    names = asyncio.run(_with_client(session, body))

    assert not any("submit" in name or "cancel" in name for name in names)
    # The submission still happens through the T09 protocol, in process.
    session.call_tool(
        __import__("data_incident_gym.strategy_adapter", fromlist=["ToolRequest"]).ToolRequest(
            request_id="r1", tool_name="get_dbt_run_results", arguments={"run_id": RUN_ID}
        )
    )
    assert session.final_diagnosis is None


def test_receipts_match_the_in_process_facade() -> None:
    """The MCP transport must not change what a tool call returns."""

    calls = (
        ("get_dbt_run_results", {"run_id": RUN_ID}),
        ("get_dbt_node_error", {"run_id": RUN_ID, "node_id": "test.x"}),
        ("get_dbt_lineage", {"node_id": "test.x", "direction": "downstream"}),
        ("get_relation_schema", {"relation_name": "raw_orders"}),
        ("get_relation_data_profile", {"relation_name": "raw_orders"}),
        ("get_relation_history", {"relation_name": "raw_orders"}),
    )
    mcp_session = _session()
    facade_session = _session()
    facade = ProtocolTools(facade_session)

    async def body(client) -> list[dict[str, object]]:
        results = []
        for name, arguments in calls:
            result = await client.call_tool(name, arguments)
            results.append(result.structuredContent or {})
        return results

    over_mcp = asyncio.run(_with_client(mcp_session, body))

    for (name, arguments), payload in zip(calls, over_mcp, strict=True):
        records = getattr(facade, name)(**arguments)
        assert payload["accepted"] is True
        assert payload["evidence_ids"] == [record.evidence_id for record in records]
        assert [item["evidence_id"] for item in payload["evidence"]] == payload["evidence_ids"]
        assert payload["error"] is None

    assert mcp_session.snapshot()["tool_call_attempts"] == len(calls)
    assert facade_session.snapshot()["tool_call_attempts"] == len(calls)
    assert mcp_session.registered_evidence_ids() == facade_session.registered_evidence_ids()


def test_wrong_run_and_refused_relation_keep_their_codes() -> None:
    tools = _StaticTools(_run_record(), refuse_profile_for="raw_customers")
    session = _session(tools)

    async def body(client):
        foreign = await client.call_tool("get_dbt_run_results", {"run_id": "d" * 32})
        refused = await client.call_tool(
            "get_relation_data_profile", {"relation_name": "raw_customers"}
        )
        return foreign.structuredContent, refused.structuredContent

    foreign, refused = asyncio.run(_with_client(session, body))

    assert foreign["accepted"] is False
    assert foreign["error"]["code"] == "TOOL_ARGUMENT_INVALID"
    assert refused["accepted"] is False
    assert refused["error"]["code"] == RelationNotAllowedError.code
    # A well-addressed call is an attempt even when its arguments or the backend
    # refuse it; only unknown or not-allowlisted names are rejected for free.
    assert session.snapshot()["tool_call_attempts"] == 2


def test_budget_and_duplicates_hold_over_mcp() -> None:
    session = _session()

    async def body(client):
        return [
            (await client.call_tool("get_dbt_run_results", {"run_id": RUN_ID})).structuredContent
            for _ in range(9)
        ]

    payloads = asyncio.run(_with_client(session, body))

    assert all(payload["accepted"] for payload in payloads[:8])
    assert len({payload["evidence_ids"][0] for payload in payloads[:8]}) == 1
    assert all(payload["duplicate"] for payload in payloads[1:8])
    exhausted = payloads[8]
    assert exhausted["accepted"] is False
    assert exhausted["error"]["code"] == "TOOL_BUDGET_EXHAUSTED"
    assert session.snapshot()["tool_call_attempts"] == 9
    assert len(session.registered_evidence_ids()) == 1


def test_deadline_is_enforced_over_mcp() -> None:
    now = {"t": 0.0}
    session = _session(clock=lambda: now["t"])

    async def body(client):
        first = await client.call_tool("get_dbt_run_results", {"run_id": RUN_ID})
        now["t"] = 400.0
        late = await client.call_tool("get_dbt_run_results", {"run_id": RUN_ID})
        return first.structuredContent, late.structuredContent

    first, late = asyncio.run(_with_client(session, body))

    assert first["accepted"] is True
    assert late["accepted"] is False
    assert late["error"]["code"] == "DEADLINE_EXCEEDED"


def test_concurrent_requests_are_serialized_and_counted() -> None:
    session = _session()

    async def body(client):
        return await asyncio.gather(
            *(
                client.call_tool("get_dbt_run_results", {"run_id": RUN_ID})
                for _ in range(8)
            )
        )

    results = asyncio.run(_with_client(session, body))
    payloads = [result.structuredContent for result in results]

    assert all(payload["accepted"] for payload in payloads)
    assert session.snapshot()["tool_call_attempts"] == 8
    assert len(session.registered_evidence_ids()) == 1
    # Serialized: request ids are unique and ordered by completion.
    request_ids = [payload["request_id"] for payload in payloads]
    assert len(set(request_ids)) == 8
    assert [int(item.rsplit(":", 1)[1]) for item in request_ids] == list(range(1, 9))


def test_disconnect_does_not_corrupt_the_session() -> None:
    session = _session()
    server = build_mcp_server(session)

    async def body() -> None:
        async with create_connected_server_and_client_session(server) as client:
            await client.call_tool("get_dbt_run_results", {"run_id": RUN_ID})
            # Leaving the context closes the transport mid-session.

    asyncio.run(body())

    snapshot = session.snapshot()
    assert snapshot["tool_call_attempts"] == 1
    assert len(session.registered_evidence_ids()) == 1
    assert snapshot["submitted"] is False
    # The session stays usable: the harness owns it, not the transport.
    assert session.call_tool(
        __import__("data_incident_gym.strategy_adapter", fromlist=["ToolRequest"]).ToolRequest(
            request_id="after-disconnect",
            tool_name="get_dbt_run_results",
            arguments={"run_id": RUN_ID},
        )
    ).accepted


def test_enabling_a_subset_only_narrows_the_surface() -> None:
    allowlist = PROTOCOL_TOOL_ALLOWLIST - {"get_dbt_lineage"}
    session = _session(allowlist=allowlist)

    async def body(client) -> list[str]:
        tools = await client.list_tools()
        return [tool.name for tool in tools.tools]

    names = asyncio.run(_with_client(session, body))

    assert "get_dbt_lineage" not in names
    assert len(names) == len(PROTOCOL_TOOL_ALLOWLIST) - 1

    with pytest.raises(StrategyProtocolError, match="DECLARATION_INVALID"):
        _session(
            declaration=_declaration(extra_tools=("read_private_contract",)),
        )


def test_gateway_rejects_tools_outside_the_evidence_six() -> None:
    gateway = ToolGateway(_session())

    with pytest.raises(ValueError, match="unknown tool"):
        asyncio.run(gateway.call("run_sql", {"query": "select 1"}))
    assert gateway.session.snapshot()["tool_call_attempts"] == 0


def test_example_client_over_real_stdio_subprocess() -> None:
    """The minimal client example must work as a real separate process."""

    import subprocess

    example = Path(__file__).resolve().parents[2] / "examples" / "mcp_client.py"
    result = subprocess.run(
        [sys.executable, str(example)],
        capture_output=True,
        text=True,
        timeout=180,
        cwd=str(example.parent),
    )

    assert result.returncode == 0, result.stderr[-2000:]
    stdout = result.stdout
    assert "tools: get_dbt_run_results" in stdout
    assert "get_relation_data_profile] raw_customers: refused RELATION_NOT_ALLOWED" in stdout
    assert "get_relation_history] raw_orders: accepted evidence=1 (used 7)" in stdout


def test_advertised_schema_documents_the_canonical_arguments() -> None:
    session = _session()

    async def body(client):
        tools = await client.list_tools()
        return {tool.name: tool.inputSchema for tool in tools.tools}

    schemas = asyncio.run(_with_client(session, body))

    for name, arguments in MCP_TOOL_ARGUMENTS.items():
        schema = schemas[name]
        assert list(schema["properties"]) == list(arguments)
        assert schema["required"] == list(arguments)
        assert schema["additionalProperties"] is False
        assert all(entry == {"type": "string"} for entry in schema["properties"].values())


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    (
        ("get_relation_data_profile", {}),  # missing argument
        ("get_relation_data_profile", {"relation_name": "raw_orders", "unexpected": "x"}),
        ("get_relation_data_profile", {"relation_name": 5}),  # wrong type
        ("get_dbt_run_results", {}),
        ("get_dbt_node_error", {"run_id": RUN_ID}),
    ),
)
def test_argument_errors_reach_the_protocol_receipt_path(
    tool_name: str, arguments: dict[str, object]
) -> None:
    """Audit regression: the SDK schema layer used to reject (missing) or drop
    (extra) arguments before the session saw them, so those calls produced no
    receipt and consumed no attempt. Raw delivery makes both paths identical."""

    from data_incident_gym.strategy_adapter import ToolRequest

    mcp_session = _session()
    direct_session = _session()

    async def body(client):
        result = await client.call_tool(tool_name, arguments)
        return result.structuredContent

    payload = asyncio.run(_with_client(mcp_session, body))
    direct = direct_session.call_tool(
        ToolRequest(request_id="direct", tool_name=tool_name, arguments=dict(arguments))
    )

    assert payload["accepted"] is False
    assert payload["error"]["code"] == "TOOL_ARGUMENT_INVALID"
    assert payload["tool_calls_used"] == 1
    assert direct.accepted is False
    assert direct.error.code == "TOOL_ARGUMENT_INVALID"
    assert direct.tool_calls_used == 1
    mcp_snapshot = mcp_session.snapshot()
    direct_snapshot = direct_session.snapshot()
    assert mcp_snapshot["tool_call_attempts"] == 1
    counters = ("tool_call_attempts", "model_requests_granted", "output_retries_used")
    assert {key: mcp_snapshot[key] for key in counters} == {
        key: direct_snapshot[key] for key in counters
    }
    assert direct_snapshot["tool_call_attempts"] == 1

    # The session stays usable after a rejected call.
    async def follow_up(client):
        return (await client.call_tool("get_dbt_run_results", {"run_id": RUN_ID})).structuredContent

    assert asyncio.run(_with_client(mcp_session, follow_up))["accepted"] is True
    assert asyncio.run(_with_client(direct_session, follow_up))["accepted"] is True


def test_unknown_tool_is_an_mcp_error_not_a_receipt() -> None:
    """Only the six tools exist: an unknown name is a client bug, answered as an
    MCP tool error (never a protocol receipt) and never counted."""

    session = _session()

    async def body(client):
        result = await client.call_tool("run_sql", {"query": "select 1"})
        return result

    result = asyncio.run(_with_client(session, body))

    assert result.isError is True
    assert result.structuredContent is None
    assert "unknown tool: run_sql" in result.content[0].text
    assert session.snapshot()["tool_call_attempts"] == 0
