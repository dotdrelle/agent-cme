#!/usr/bin/env python3
"""CME MCP Server — HTTP/SSE transport for container deployment."""

import asyncio
import json
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

# Local venv support (dev mode without Docker)
_CME_SITE = Path(__file__).parent / ".cme" / "lib" / "python3.11" / "site-packages"
if _CME_SITE.exists():
    sys.path.insert(0, str(_CME_SITE))

import yaml
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import TextContent, Tool
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Mount, Route
import uvicorn

from confluence_markdown_exporter.utils.app_data_store import (
    get_settings,
    set_setting,
    set_setting_with_keys,
)

# CME_DATA_DIR separates runtime data from code (required in Docker, optional locally)
_DATA_DIR = Path(os.environ.get("CME_DATA_DIR", str(Path(__file__).parent)))
_MANIFEST = _DATA_DIR / "sources-manifest.yaml"
_SEED_MANIFEST = Path(__file__).parent / "sources-manifest.yaml"

_CME_VENV_BIN = Path(__file__).parent / ".cme" / "bin" / "cme"
_CME_BIN = str(_CME_VENV_BIN) if _CME_VENV_BIN.exists() else "cme"

_SECRET_KEYS = {"username", "api_token", "pat", "password"}
_jobs: dict[str, dict[str, Any]] = {}

app = Server("cme")


# ---------------------------------------------------------------------------
# Auth middleware
# ---------------------------------------------------------------------------

_MCP_TOKEN = os.environ.get("MCP_AUTH_TOKEN", "")
if not _MCP_TOKEN:
    print("[cme-mcp] WARNING: MCP_AUTH_TOKEN is not set — server is open to all connections")


class _BearerAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if _MCP_TOKEN:
            auth = request.headers.get("authorization", "")
            if auth != f"Bearer {_MCP_TOKEN}":
                return PlainTextResponse("Unauthorized", status_code=401)
        return await call_next(request)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mask_secrets(data: Any) -> Any:
    if isinstance(data, dict):
        return {
            k: ("***" if k in _SECRET_KEYS and v else _mask_secrets(v))
            for k, v in data.items()
        }
    if isinstance(data, list):
        return [_mask_secrets(i) for i in data]
    return data


def _load_manifest() -> dict:
    _init_manifest_if_missing()
    if not _MANIFEST.exists():
        return {"exports": []}
    with _MANIFEST.open() as f:
        return yaml.safe_load(f) or {"exports": []}


def _save_manifest(data: dict) -> None:
    _MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    with _MANIFEST.open("w") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False)


def _init_manifest_if_missing() -> None:
    """Seed the writable runtime manifest once, without overwriting MCP edits."""
    if _MANIFEST.exists() or not _SEED_MANIFEST.exists() or _MANIFEST == _SEED_MANIFEST:
        return
    _MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    _MANIFEST.write_text(_SEED_MANIFEST.read_text(encoding="utf-8"), encoding="utf-8")


def _source_url(source: dict) -> str:
    if source.get("type", "space") == "page":
        return source["url"]
    return f"{source['base_url'].rstrip('/')}/display/{source['space']}"


def _lock_summary() -> dict:
    settings = get_settings()
    lock_path = settings.export.output_path / settings.export.lockfile_name
    if not lock_path.exists():
        return {"status": "no_lock_file", "path": str(lock_path)}
    with open(lock_path) as f:
        lock = json.load(f)
    last = lock.get("last_export", "unknown")
    orgs = lock.get("orgs", {})
    spaces = {}
    for org_url, org_data in orgs.items():
        for space_key, space_data in org_data.get("spaces", {}).items():
            spaces[f"{org_url} / {space_key}"] = len(space_data.get("pages", {}))
    return {"last_export": last, "spaces": spaces}


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="cme_status",
            description=(
                "Check if CME is configured and ready. "
                "Call this first — returns 'configured' or 'not_configured'. "
                "If not_configured, call cme_setup before doing anything else."
            ),
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="cme_setup",
            description=(
                "One-time CME initialization: stores Confluence credentials and connection settings persistently. "
                "After setup the server is autonomous — no reconfiguration needed on restart. "
                "Provide pat (self-hosted PAT) or username+api_token (Atlassian Cloud)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "base_url": {"type": "string", "description": "Confluence base URL, e.g. http://confluence.meteo.fr"},
                    "username": {"type": "string", "description": "Username or email"},
                    "pat": {"type": "string", "description": "Personal Access Token (self-hosted)"},
                    "api_token": {"type": "string", "description": "API token (Atlassian Cloud)"},
                    "verify_ssl": {"type": "boolean", "description": "Verify SSL certificates (default: true)"},
                    "use_v2_api": {"type": "boolean", "description": "Use Confluence REST API v2 — for Data Center 8+ or Cloud (default: false)"},
                },
                "required": ["base_url"],
            },
        ),
        Tool(
            name="cme_sources_list",
            description="List all configured export sources from the manifest.",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="cme_source_add",
            description=(
                "Add or update an export source in the manifest. "
                "For type=space: provide base_url + space key. "
                "For type=page: provide url (full Confluence page URL)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Short identifier, e.g. 'juno'"},
                    "type": {"type": "string", "enum": ["space", "page"], "description": "Export type (default: space)"},
                    "base_url": {"type": "string", "description": "Confluence base URL (required for type=space)"},
                    "space": {"type": "string", "description": "Space key, e.g. 'JDLCDPPO' (required for type=space)"},
                    "url": {"type": "string", "description": "Full page URL (required for type=page)"},
                    "description": {"type": "string", "description": "Human description of this source"},
                },
                "required": ["name"],
            },
        ),
        Tool(
            name="cme_source_remove",
            description="Remove an export source from the manifest by name.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Name of the source to remove"},
                },
                "required": ["name"],
            },
        ),
        Tool(
            name="cme_export_run",
            description=(
                "Start an asynchronous export for one or all sources. "
                "Returns a job_id immediately. Use cme_export_status(job_id=...) to follow progress."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "source_name": {"type": "string", "description": "Source name to export. If omitted, exports all sources."},
                },
                "required": [],
            },
        ),
        Tool(
            name="cme_export_status",
            description="Check status of an export job, or return lock file summary if no job_id given.",
            inputSchema={
                "type": "object",
                "properties": {
                    "job_id": {"type": "string", "description": "Job ID returned by cme_export_run. If omitted, returns lock summary."},
                },
                "required": [],
            },
        ),
    ]


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    match name:
        case "cme_status":
            return await _tool_status()
        case "cme_setup":
            return await _tool_setup(arguments)
        case "cme_sources_list":
            return await _tool_sources_list()
        case "cme_source_add":
            return await _tool_source_add(arguments)
        case "cme_source_remove":
            return await _tool_source_remove(arguments)
        case "cme_export_run":
            return await _tool_export_run(arguments)
        case "cme_export_status":
            return await _tool_export_status(arguments)
        case _:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]


async def _tool_status() -> list[TextContent]:
    settings = get_settings()
    data = json.loads(settings.model_dump_json())
    confluence = data.get("auth", {}).get("confluence", {})
    instances = {
        url: {
            "auth": "pat" if creds.get("pat") else ("username+api_token" if creds.get("api_token") else "incomplete"),
        }
        for url, creds in confluence.items()
        if creds.get("pat") or creds.get("api_token")
    }
    configured = bool(instances)
    lines = [f"status: {'configured' if configured else 'not_configured'}"]
    if configured:
        for url, info in instances.items():
            lines.append(f"  {url}  auth={info['auth']}")
        conn = data.get("connection_config", {})
        lines.append(f"verify_ssl: {conn.get('verify_ssl', True)}")
        lines.append(f"use_v2_api: {conn.get('use_v2_api', False)}")
        lock = _lock_summary()
        if lock.get("last_export"):
            lines.append(f"last_export: {lock['last_export']}")
    else:
        lines.append("action_required: call cme_setup to initialize")
    return [TextContent(type="text", text="\n".join(lines))]


async def _tool_setup(args: dict) -> list[TextContent]:
    base_url: str = args["base_url"].rstrip("/")
    username: str = args.get("username", "")
    pat: str = args.get("pat", "")
    api_token: str = args.get("api_token", "")
    verify_ssl: bool = args.get("verify_ssl", True)
    use_v2_api: bool = args.get("use_v2_api", False)
    try:
        if os.environ.get("CME_CONFIG_PATH"):
            Path(os.environ["CME_CONFIG_PATH"]).parent.mkdir(parents=True, exist_ok=True)
        if username:
            set_setting_with_keys(["auth", "confluence", base_url, "username"], username)
        if pat:
            set_setting_with_keys(["auth", "confluence", base_url, "pat"], pat)
        if api_token:
            set_setting_with_keys(["auth", "confluence", base_url, "api_token"], api_token)
        set_setting("connection_config.verify_ssl", verify_ssl)
        set_setting("connection_config.use_v2_api", use_v2_api)
        fields = [f for f in ("username", "pat", "api_token") if args.get(f)]
        return [TextContent(type="text", text=(
            f"OK: CME configured\n"
            f"instance: {base_url}\n"
            f"credentials: {', '.join(fields)}\n"
            f"verify_ssl: {verify_ssl}\n"
            f"use_v2_api: {use_v2_api}\n"
            f"Config persisted — no reconfiguration needed on restart."
        ))]
    except (ValueError, KeyError, TypeError) as e:
        return [TextContent(type="text", text=f"Error: {e}")]


async def _tool_sources_list() -> list[TextContent]:
    manifest = _load_manifest()
    lock = _lock_summary()
    result = {"manifest_path": str(_MANIFEST), "sources": manifest.get("exports", []), "lock": lock}
    return [TextContent(type="text", text=yaml.dump(result, allow_unicode=True, default_flow_style=False))]


async def _tool_source_add(args: dict) -> list[TextContent]:
    manifest = _load_manifest()
    exports: list = manifest.setdefault("exports", [])
    name = args["name"]
    source_type = args.get("type", "space")
    existing = next((e for e in exports if e.get("name") == name), None)
    entry: dict = existing or {}
    entry["name"] = name
    entry["type"] = source_type
    if source_type == "page":
        if not args.get("url"):
            return [TextContent(type="text", text="Error: 'url' is required for type=page")]
        entry["url"] = args["url"]
    else:
        if not args.get("base_url") or not args.get("space"):
            return [TextContent(type="text", text="Error: 'base_url' and 'space' are required for type=space")]
        entry["base_url"] = args["base_url"]
        entry["space"] = args["space"]
    if args.get("description"):
        entry["description"] = args["description"]
    if existing is None:
        exports.append(entry)
        action = "added"
    else:
        action = "updated"
    _save_manifest(manifest)
    return [TextContent(type="text", text=f"OK: source '{name}' {action} in {_MANIFEST}")]


async def _tool_source_remove(args: dict) -> list[TextContent]:
    manifest = _load_manifest()
    exports: list = manifest.get("exports", [])
    name = args["name"]
    before = len(exports)
    manifest["exports"] = [e for e in exports if e.get("name") != name]
    if len(manifest["exports"]) == before:
        return [TextContent(type="text", text=f"Error: source '{name}' not found")]
    _save_manifest(manifest)
    return [TextContent(type="text", text=f"OK: source '{name}' removed")]


async def _tool_export_run(args: dict) -> list[TextContent]:
    manifest = _load_manifest()
    exports: list = manifest.get("exports", [])
    source_name = args.get("source_name")
    if source_name:
        sources = [e for e in exports if e.get("name") == source_name]
        if not sources:
            return [TextContent(type="text", text=f"Error: source '{source_name}' not found")]
    else:
        sources = exports
    if not sources:
        return [TextContent(type="text", text="Error: no sources defined. Use cme_source_add first.")]

    space_urls = [_source_url(s) for s in sources if s.get("type", "space") == "space"]
    page_urls = [_source_url(s) for s in sources if s.get("type", "page") == "page"]

    job_id = str(uuid.uuid4())[:8]
    _jobs[job_id] = {
        "status": "starting",
        "sources": [s["name"] for s in sources],
        "started_at": datetime.now().isoformat(),
        "stdout": [],
        "stderr": [],
        "returncode": None,
    }

    async def _run_cmd(cmd: list[str]) -> int:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(_DATA_DIR),
        )
        async def _read(stream: asyncio.StreamReader, buf: list) -> None:
            async for line in stream:
                buf.append(line.decode(errors="replace").rstrip())
        await asyncio.gather(
            _read(proc.stdout, _jobs[job_id]["stdout"]),
            _read(proc.stderr, _jobs[job_id]["stderr"]),
        )
        await proc.wait()
        return proc.returncode

    async def _run() -> None:
        _jobs[job_id]["status"] = "running"
        try:
            rc = 0
            if space_urls:
                rc = await _run_cmd([_CME_BIN, "spaces", *space_urls])
            if page_urls and rc == 0:
                rc = await _run_cmd([_CME_BIN, "pages", *page_urls])
            _jobs[job_id]["returncode"] = rc
            _jobs[job_id]["status"] = "success" if rc == 0 else "failed"
            _jobs[job_id]["finished_at"] = datetime.now().isoformat()
        except Exception as e:
            _jobs[job_id]["status"] = "error"
            _jobs[job_id]["error"] = str(e)

    asyncio.create_task(_run())
    return [TextContent(
        type="text",
        text=f"Export started — job_id: {job_id}\nSources: {', '.join(s['name'] for s in sources)}\nUse cme_export_status(job_id='{job_id}') to follow progress.",
    )]


async def _tool_export_status(args: dict) -> list[TextContent]:
    job_id = args.get("job_id")
    if job_id:
        job = _jobs.get(job_id)
        if not job:
            return [TextContent(type="text", text=f"Unknown job_id: {job_id}")]
        lines = [
            f"job_id: {job_id}",
            f"status: {job['status']}",
            f"sources: {', '.join(job.get('sources', []))}",
            f"started_at: {job.get('started_at', '?')}",
        ]
        if job.get("finished_at"):
            lines.append(f"finished_at: {job['finished_at']}")
        if job.get("returncode") is not None:
            lines.append(f"returncode: {job['returncode']}")
        stdout = job.get("stdout", [])
        stderr = job.get("stderr", [])
        if stdout:
            lines.append("\n--- stdout (last 20 lines) ---")
            lines.extend(stdout[-20:])
        if stderr:
            lines.append("\n--- stderr (last 20 lines) ---")
            lines.extend(stderr[-20:])
        return [TextContent(type="text", text="\n".join(lines))]
    lock = _lock_summary()
    return [TextContent(type="text", text=yaml.dump(lock, allow_unicode=True, default_flow_style=False))]


# ---------------------------------------------------------------------------
# Entrypoint — HTTP/SSE server
# ---------------------------------------------------------------------------

def main() -> None:
    _init_manifest_if_missing()
    sse = SseServerTransport("/messages/")

    async def handle_sse(request: Request) -> Response:
        async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
            await app.run(streams[0], streams[1], app.create_initialization_options())
        return Response()

    starlette_app = Starlette(
        routes=[
            Route("/sse", endpoint=handle_sse),
            Mount("/messages/", app=sse.handle_post_message),
        ],
        middleware=[Middleware(_BearerAuthMiddleware)],
    )

    host = os.environ.get("MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("MCP_PORT", "8080"))
    ssl_certfile = os.environ.get("MCP_SSL_CERTFILE")
    ssl_keyfile = os.environ.get("MCP_SSL_KEYFILE")

    uvicorn_kwargs: dict[str, Any] = {"host": host, "port": port}
    if ssl_certfile or ssl_keyfile:
        missing = [name for name, val in (("MCP_SSL_CERTFILE", ssl_certfile), ("MCP_SSL_KEYFILE", ssl_keyfile)) if not val]
        if missing:
            raise RuntimeError(f"TLS mal configuré — variables manquantes : {', '.join(missing)}")
        for label, path in (("MCP_SSL_CERTFILE", ssl_certfile), ("MCP_SSL_KEYFILE", ssl_keyfile)):
            if not Path(path).exists():
                raise RuntimeError(f"TLS mal configuré — fichier introuvable : {label}={path}")
        uvicorn_kwargs["ssl_certfile"] = ssl_certfile
        uvicorn_kwargs["ssl_keyfile"] = ssl_keyfile
        print(f"[cme-mcp] HTTPS activé — cert={ssl_certfile}")
    else:
        print(f"[cme-mcp] HTTP (pas de TLS)")

    print(f"[cme-mcp] Écoute sur {'https' if ssl_certfile else 'http'}://{host}:{port}/sse")
    uvicorn.run(starlette_app, **uvicorn_kwargs)


if __name__ == "__main__":
    main()
