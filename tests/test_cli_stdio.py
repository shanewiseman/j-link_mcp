from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import anyio
import pytest

from jlink_mcp import cli
from jlink_mcp.models import DependencyCheck, DependencyReport
from jlink_mcp.stdio_proxy import _relay


def test_cli_token_mode_0600(tmp_path: Path, monkeypatch, settings) -> None:
    token = tmp_path / "nested" / "token"
    monkeypatch.setattr(sys, "argv", ["jlink-mcp", "token", "--output", str(token)])
    monkeypatch.setattr(cli, "Settings", lambda: settings)
    cli.main()
    assert len(token.read_text().strip()) >= 48
    assert token.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    ("json_mode", "ok", "exit_code"), [(False, True, 0), (True, False, 1)]
)
def test_cli_doctor_output(
    json_mode, ok, exit_code, monkeypatch, settings, manifest, capsys
) -> None:
    argv = ["jlink-mcp", "doctor"] + (["--json"] if json_mode else [])
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(cli, "Settings", lambda: settings)
    report = DependencyReport(
        checks=[DependencyCheck(name="dependency", ok=ok, observed="value")],
        manifest=manifest,
    )
    monkeypatch.setattr(cli, "dependency_report", lambda value: report)
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == exit_code
    output = capsys.readouterr().out
    assert ("dependency" in output) if not json_mode else ("generated_at" in output)


def test_cli_serve_proxy_and_direct(monkeypatch, settings) -> None:
    calls = []
    monkeypatch.setattr(cli, "Settings", lambda: settings)
    monkeypatch.setattr(cli, "create_http_app", lambda value: "app")
    monkeypatch.setattr(
        cli.uvicorn,
        "run",
        lambda app, **kwargs: calls.append(("serve", app, kwargs)),
    )
    monkeypatch.setattr(
        sys, "argv", ["jlink-mcp", "serve", "--host", "127.0.0.1", "--port", "9000"]
    )
    cli.main()
    assert calls[-1][2]["port"] == 9000

    async def proxy(url, token):
        calls.append(("proxy", url, token))

    monkeypatch.setattr(cli, "run_stdio_proxy", proxy)
    monkeypatch.setattr(
        cli.anyio, "run", lambda function, *args: asyncio.run(function(*args))
    )
    monkeypatch.setattr(
        sys, "argv", ["jlink-mcp", "stdio-proxy", "--url", "http://loop/mcp"]
    )
    cli.main()
    assert calls[-1] == ("proxy", "http://loop/mcp", "test-token")

    fake_mcp = SimpleNamespace(run=lambda **kwargs: calls.append(("direct", kwargs)))
    monkeypatch.setattr(cli, "MCPRuntime", lambda value: SimpleNamespace(mcp=fake_mcp))
    monkeypatch.setattr(sys, "argv", ["jlink-mcp", "stdio-direct"])
    cli.main()
    assert calls[-1] == ("direct", {"transport": "stdio"})


@pytest.mark.asyncio
async def test_stdio_relay_messages_and_exceptions() -> None:
    send_in, receive_in = anyio.create_memory_object_stream(2)
    send_out, receive_out = anyio.create_memory_object_stream(2)
    await send_in.send({"jsonrpc": "2.0"})
    await send_in.aclose()
    await _relay(receive_in, send_out)
    assert await receive_out.receive() == {"jsonrpc": "2.0"}

    send_in, receive_in = anyio.create_memory_object_stream(1)
    send_out, _ = anyio.create_memory_object_stream(1)
    await send_in.send(RuntimeError("bridge failed"))
    await send_in.aclose()
    with pytest.raises(RuntimeError, match="bridge failed"):
        await _relay(receive_in, send_out)
