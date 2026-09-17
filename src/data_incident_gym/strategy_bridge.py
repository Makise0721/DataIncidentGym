"""Harness-owned strategy bridge (T11): one session, one child process, one log.

An isolated strategy runs in its own process — a container or a plain
interpreter — and speaks MCP on that process's stdio. The bridge is the harness
end of that pipe, so the **single** ``StrategySession`` never leaves the harness:

    strategy process  ──stdio(MCP)──▶  bridge  ──▶  harness StrategySession

The tool surface is exactly the T10 one: the bridge builds it with
``build_mcp_server``, so the allowlist, run scope, budget 8/8/2/300, deadline,
evidence registration, receipts and error codes are the same rules the
in-process facade enforces. What the bridge adds is the harness's own record:

- every call the session processed is appended to the authoritative log
  (``recorder``) in order — accepted or refused, including the budget refusal —
  so a child cannot hide a failed call, drop a counter or reset the session;
- the child's own log stays auxiliary. Losing it, trimming it or writing a false
  summary into it changes nothing the harness counted;
- the session outlives the child: after the process exits the harness still owns
  the terminal state, the registered evidence and the counters.

There is deliberately no submit and no cancel tool on this surface. The answer
travels as a file the harness parses and hands to the same session's ``submit``,
because "the client says it submitted" is not evidence of anything.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCMessage

from data_incident_gym.mcp_server import build_mcp_server
from data_incident_gym.strategy_adapter import StrategySession

DEFAULT_TIMEOUT_SECONDS = 600.0
STDERR_TAIL_CHARS = 4000
_CLOSED = object()


class _MessageStream:
    """The two-way message stream an MCP session needs, over asyncio queues.

    The session contract is small — ``async with``, ``await send(...)`` and
    ``async for`` — so the transport needs no further dependency: ``mcp`` brings
    anyio for its own internal use, and this module does not import it.
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        self._closed = False

    async def send(self, item: Any) -> None:
        if self._closed:
            raise RuntimeError("stream is closed")
        await self._queue.put(item)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._queue.put_nowait(_CLOSED)

    async def __aenter__(self) -> _MessageStream:
        return self

    async def __aexit__(self, *_: object) -> None:
        self.close()

    def __aiter__(self) -> _MessageStream:
        return self

    async def __anext__(self) -> Any:
        item = await self._queue.get()
        if item is _CLOSED:
            raise StopAsyncIteration
        return item


@dataclass(frozen=True)
class BridgedRun:
    """What one bridged child process did, as the harness observed it."""

    exit_code: int | None
    timed_out: bool
    #: One entry per call the session processed, in order: sequence, tool name,
    #: raw arguments and the receipt the session returned.
    entries: tuple[dict[str, Any], ...]
    stderr_tail: str

    @property
    def receipts(self) -> tuple[dict[str, Any], ...]:
        return tuple(entry["receipt"] for entry in self.entries)


async def _serve(
    session: StrategySession,
    argv: Sequence[str],
    *,
    recorder: Callable[[dict[str, Any]], None] | None,
    cwd: str | Path | None,
    env: Mapping[str, str] | None,
    timeout_seconds: float,
) -> BridgedRun:
    entries: list[dict[str, Any]] = []

    def record(entry: dict[str, Any]) -> None:
        entries.append(entry)
        if recorder is not None:
            recorder(entry)

    server = build_mcp_server(session, recorder=record)
    process = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=None if cwd is None else str(cwd),
        env=None if env is None else dict(env),
    )
    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None

    from_child = _MessageStream()
    to_child = _MessageStream()

    async def read_child() -> None:
        try:
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                try:
                    message = JSONRPCMessage.model_validate_json(line)
                except Exception as exc:  # noqa: BLE001 - reported to the session
                    await from_child.send(exc)
                    continue
                await from_child.send(SessionMessage(message))
        finally:
            from_child.close()

    async def write_child() -> None:
        try:
            async for message in to_child:
                payload = message.message.model_dump_json(by_alias=True, exclude_none=True)
                process.stdin.write(payload.encode("utf-8") + b"\n")
                await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            # The child already exited; its side of the pipe is gone.
            return

    async def read_stderr() -> str:
        body = await process.stderr.read()
        return body.decode("utf-8", errors="replace")[-STDERR_TAIL_CHARS:]

    reader = asyncio.create_task(read_child())
    writer = asyncio.create_task(write_child())
    stderr_reader = asyncio.create_task(read_stderr())

    timed_out = False
    try:
        await asyncio.wait_for(
            server.run(from_child, to_child, server.create_initialization_options()),
            timeout_seconds,
        )
    except TimeoutError:
        timed_out = True
    finally:
        from_child.close()
        to_child.close()
        try:
            await asyncio.wait_for(writer, 10)
        except (TimeoutError, asyncio.CancelledError):
            writer.cancel()
        reader.cancel()
        if not process.stdin.is_closing():
            process.stdin.close()
        try:
            await asyncio.wait_for(process.wait(), 30)
        except TimeoutError:
            process.kill()
            await process.wait()
        try:
            stderr_tail = await asyncio.wait_for(stderr_reader, 10)
        except (TimeoutError, asyncio.CancelledError):
            stderr_tail = ""
        for task in (reader, writer, stderr_reader):
            if not task.done():
                task.cancel()

    return BridgedRun(
        exit_code=process.returncode,
        timed_out=timed_out,
        entries=tuple(entries),
        stderr_tail=stderr_tail,
    )


def serve_child_session(
    session: StrategySession,
    argv: Sequence[str],
    *,
    recorder: Callable[[dict[str, Any]], None] | None = None,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> BridgedRun:
    """Run one strategy process behind the harness session (blocking).

    ``argv`` is the strategy process, typically ``docker run -i ... python3
    client.py`` or a local interpreter running the same client. The child's
    stdin/stdout carry MCP; its stderr is diagnostics and is returned with the
    log so a failed run is still explainable.
    """

    return asyncio.run(
        _serve(
            session,
            argv,
            recorder=recorder,
            cwd=cwd,
            env=env,
            timeout_seconds=timeout_seconds,
        )
    )


__all__ = ["BridgedRun", "DEFAULT_TIMEOUT_SECONDS", "serve_child_session"]
