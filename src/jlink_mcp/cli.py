"""Command-line entry point."""

from __future__ import annotations

import argparse
import secrets
from pathlib import Path

import anyio
import uvicorn

from .config import Settings
from .doctor import dependency_report
from .server import MCPRuntime, create_http_app
from .stdio_proxy import run_stdio_proxy


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jlink-mcp")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve", help="run authenticated HTTP MCP")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)

    proxy = subparsers.add_parser("stdio-proxy", help="bridge stdio to HTTP MCP")
    proxy.add_argument("--url", default="http://127.0.0.1:8000/mcp")

    doctor = subparsers.add_parser("doctor", help="print dependency report")
    doctor.add_argument("--json", action="store_true")

    token = subparsers.add_parser("token", help="generate a bearer token")
    token.add_argument("--output", type=Path, required=True)

    direct = subparsers.add_parser(
        "stdio-direct", help="run an in-process stdio MCP for diagnostics"
    )
    direct.set_defaults(command="stdio-direct")
    return parser


def main() -> None:
    args = _parser().parse_args()
    settings = Settings()
    if args.command == "token":
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(secrets.token_urlsafe(48) + "\n", encoding="utf-8")
        args.output.chmod(0o600)
        print(args.output)
        return
    if args.command == "doctor":
        settings.ensure_directories()
        report = dependency_report(settings)
        if args.json:
            print(report.model_dump_json(indent=2))
        else:
            for check in report.checks:
                marker = "PASS" if check.ok else ("FAIL" if check.required else "WARN")
                print(f"{marker:4} {check.name}: {check.observed or ''}")
            print(f"overall: {'PASS' if report.ok else 'FAIL'}")
        raise SystemExit(0 if report.ok else 1)
    if args.command == "serve":
        if args.host:
            settings.host = args.host
        if args.port:
            settings.port = args.port
        app = create_http_app(settings)
        uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")
        return
    if args.command == "stdio-proxy":
        token = settings.bearer_token(required=True)
        assert token is not None
        anyio.run(run_stdio_proxy, args.url, token)
        return
    if args.command == "stdio-direct":
        MCPRuntime(settings).mcp.run(transport="stdio")
        return


if __name__ == "__main__":
    main()
