#!/usr/bin/env python3
"""CME MCP Server — Streamable HTTP transport for container deployment."""

import asyncio
import contextlib
import json
import os
import sys
import time
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Local venv support (dev mode without Docker)
_CME_SITE = next(
    (p for v in ("python3.14", "python3.13", "python3.12", "python3.11")
     if (p := Path(__file__).parent / ".cme" / "lib" / v / "site-packages").exists()),
    None,
)
if _CME_SITE:
    sys.path.insert(0, str(_CME_SITE))

import yaml
from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.types import TextContent, Tool
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse
from starlette.routing import Mount
from starlette.types import Receive, Scope, Send
import uvicorn

from cme_source_urls import extract_confluence_url, parse_confluence_source_url
from confluence_markdown_exporter.utils import app_data_store
from confluence_markdown_exporter.utils.app_data_store import (
    get_settings,
    set_setting,
    set_setting_with_keys,
)

# CME_DATA_DIR separates runtime data from code (required in Docker, optional locally)
_DATA_DIR = Path(os.environ.get("CME_DATA_DIR", str(Path(__file__).parent)))
_WORKSPACES_ROOT = Path(os.environ.get("WORKSPACES_ROOT", "/workspaces")).resolve()
_AGENT_VERSION = "0.6.47"

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
    print("[cme-mcp] Warning: MCP_AUTH_TOKEN is not configured; the endpoint accepts unauthenticated clients.")


class _BearerAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method == "GET" and _wants_html(request):
            return await call_next(request)
        if _MCP_TOKEN:
            auth = request.headers.get("authorization", "")
            if auth != f"Bearer {_MCP_TOKEN}":
                return PlainTextResponse("Unauthorized", status_code=401)
        return await call_next(request)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _escape_html(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _wants_html(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    return "text/html" in accept


def _render_landing_page(endpoint_url: str, scheme: str) -> str:
    auth_status = (
        "Bearer token enabled"
        if _MCP_TOKEN
        else "Warning: MCP_AUTH_TOKEN is not configured; the endpoint accepts unauthenticated clients."
    )
    tool_names = [
        ("cme_status", "Check agent-cme runtime configuration and readiness."),
        ("cme_setup", "Store agent-cme Confluence credentials and connection settings."),
        ("cme_sources_list", "List agent-cme configured Confluence export sources."),
        ("cme_source_add", "Add or update a Confluence export source."),
        ("cme_source_remove", "Remove an export source by name."),
        ("cme_export_run", "Start an asynchronous markdown export."),
        ("cme_export_cancel", "Cancel a running agent-cme export job."),
        ("cme_export_status", "Check export progress or last-export summary."),
    ]
    tools = "\n".join(
        f"<li><code>{_escape_html(name)}</code><span>{_escape_html(description)}</span></li>"
        for name, description in tool_names
    )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>agent-cme MCP connector</title>
  <style>
    :root {{ color-scheme: light dark; --bg: #f8fafc; --panel: #ffffff; --text: #111827; --muted: #64748b; --line: #d8dee8; --accent: #2563eb; --code: #eef2ff; }}
    @media (prefers-color-scheme: dark) {{ :root {{ --bg: #0f172a; --panel: #111827; --text: #f8fafc; --muted: #94a3b8; --line: #253044; --accent: #60a5fa; --code: #1e293b; }} }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; min-height: 100vh; background: var(--bg); color: var(--text); font: 15px/1.55 ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    main {{ width: min(920px, calc(100% - 32px)); margin: 0 auto; padding: 56px 0; }}
    .eyebrow {{ color: var(--accent); font-weight: 700; letter-spacing: .04em; text-transform: uppercase; font-size: 12px; }}
    h1 {{ margin: 8px 0 10px; font-size: clamp(32px, 6vw, 52px); line-height: 1.05; letter-spacing: 0; }}
    h2 {{ margin: 0 0 16px; font-size: 20px; letter-spacing: 0; }}
    .lead {{ margin: 0 0 28px; color: var(--muted); max-width: 720px; font-size: 17px; }}
    .panel {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 22px; margin: 18px 0; }}
    dl {{ display: grid; grid-template-columns: 150px 1fr; gap: 10px 18px; margin: 0; }}
    dt {{ color: var(--muted); }}
    dd {{ margin: 0; min-width: 0; overflow-wrap: anywhere; }}
    code {{ background: var(--code); border: 1px solid var(--line); border-radius: 6px; padding: 2px 6px; font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size: 13px; }}
    ul {{ list-style: none; margin: 0; padding: 0; display: grid; gap: 10px; }}
    li {{ display: grid; grid-template-columns: minmax(170px, 240px) 1fr; gap: 14px; align-items: start; padding: 12px 0; border-top: 1px solid var(--line); }}
    li:first-child {{ border-top: 0; padding-top: 0; }}
    li span {{ color: var(--muted); }}
    .note {{ color: var(--muted); margin: 12px 0 0; }}
    @media (max-width: 640px) {{ dl, li {{ grid-template-columns: 1fr; }} main {{ padding: 32px 0; }} }}
  </style>
</head>
<body>
  <main>
    <div class="eyebrow">MCP Streamable HTTP</div>
    <h1>agent-cme MCP connector</h1>
    <p class="lead">This endpoint is intended for MCP clients such as Claude and OpenWebUI. Browsers can view this status page; MCP clients should send Streamable HTTP requests to the same URL.</p>
    <section class="panel">
      <dl>
        <dt>Status</dt><dd>Ready</dd>
        <dt>Version</dt><dd><code>{_escape_html(_AGENT_VERSION)}</code></dd>
        <dt>Endpoint</dt><dd><code>{_escape_html(endpoint_url)}</code></dd>
        <dt>Transport</dt><dd>MCP Streamable HTTP over {_escape_html(scheme.upper())}</dd>
        <dt>Authentication</dt><dd>{_escape_html(auth_status)}</dd>
        <dt>Data directory</dt><dd><code>{_escape_html(str(_DATA_DIR))}</code></dd>
        <dt>Workspaces root</dt><dd><code>{_escape_html(str(_WORKSPACES_ROOT))}</code></dd>
      </dl>
    </section>
    <section class="panel">
      <h2>Available tools</h2>
      <ul>{tools}</ul>
      <p class="note">First-run workflow: call <code>cme_status(workspace=...)</code>, then <code>cme_setup(workspace=...)</code> if credentials are not configured. Export jobs can be cancelled with <code>cme_export_cancel</code>; files already written before cancellation are left in place.</p>
    </section>
  </main>
</body>
</html>"""


def _mask_secrets(data: Any) -> Any:
    if isinstance(data, dict):
        return {
            k: ("***" if k in _SECRET_KEYS and v else _mask_secrets(v))
            for k, v in data.items()
        }
    if isinstance(data, list):
        return [_mask_secrets(i) for i in data]
    return data


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _terminal_status(status: Any) -> bool:
    return str(status or "").lower() in {"success", "failed", "error", "cancelled", "canceled", "done", "completed"}


def _validate_workspace(name: str) -> Path:
    value = str(name or "").strip()
    if not value or "/" in value or "\\" in value or value in {".", ".."} or ".." in value:
        raise ValueError(f"Invalid workspace: {name}")
    path = (_WORKSPACES_ROOT / value).resolve()
    try:
        path.relative_to(_WORKSPACES_ROOT)
    except ValueError as exc:
        raise ValueError("Path traversal attempt") from exc
    if not path.is_dir():
        raise ValueError(f"Unknown workspace: {value}")
    return path


def _workspace_name(name: str) -> str:
    _validate_workspace(name)
    return str(name or "").strip()


def _workspace_data_dir(workspace: str) -> Path:
    value = _workspace_name(workspace)
    path = (_DATA_DIR / value).resolve()
    try:
        path.relative_to(_DATA_DIR.resolve())
    except ValueError as exc:
        raise ValueError("Data path traversal attempt") from exc
    path.mkdir(parents=True, exist_ok=True)
    return path


def _workspace_manifest(workspace: str) -> Path:
    return _workspace_data_dir(workspace) / "sources-manifest.yaml"


def _workspace_cme_config(workspace: str) -> Path:
    return _workspace_data_dir(workspace) / "cme" / "app_data.json"


@contextlib.contextmanager
def _cme_workspace_context(workspace: str):
    config_path = _workspace_cme_config(workspace)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    old_value = os.environ.get("CME_CONFIG_PATH")
    old_app_config_path = app_data_store.APP_CONFIG_PATH
    os.environ["CME_CONFIG_PATH"] = str(config_path)
    app_data_store.APP_CONFIG_PATH = config_path
    try:
        yield _workspace_data_dir(workspace)
    finally:
        app_data_store.APP_CONFIG_PATH = old_app_config_path
        if old_value is None:
            os.environ.pop("CME_CONFIG_PATH", None)
        else:
            os.environ["CME_CONFIG_PATH"] = old_value


def _activity_for_job(job_id: str, job: dict[str, Any]) -> dict[str, Any]:
    status = str(job.get("status") or "running")
    sources = job.get("sources") or []
    workspace = job.get("workspace")
    label = f"CME export · {status}"
    if workspace:
        label = f"{label} · {workspace}"
    if sources:
        label = f"{label} · {', '.join(str(source) for source in sources[:3])}"
        if len(sources) > 3:
            label = f"{label} +{len(sources) - 3}"
    return {
        "id": job_id,
        "source": "cme",
        "kind": "export",
        "label": label,
        "status": status,
        "progress": {
            "step": "export",
            "stdoutLines": len(job.get("stdout") or []),
            "stderrLines": len(job.get("stderr") or []),
        },
        "poll": {
            "server": "cme",
            "tool": "cme_export_status",
            "args": {"job_id": job_id},
            "intervalMs": 2500,
        } if not _terminal_status(status) else None,
        "startedAt": job.get("started_at"),
        "updatedAt": job.get("finished_at") or _now(),
        "error": job.get("error"),
        "terminal": _terminal_status(status),
    }


def _json_content(payload: dict[str, Any]) -> TextContent:
    return TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, indent=2))


def _load_manifest(workspace: str) -> dict:
    manifest_path = _workspace_manifest(workspace)
    _init_manifest_if_missing(workspace)
    if not manifest_path.exists():
        return {"exports": []}
    with manifest_path.open() as f:
        return yaml.safe_load(f) or {"exports": []}


def _save_manifest(workspace: str, data: dict) -> None:
    manifest_path = _workspace_manifest(workspace)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with manifest_path.open("w") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False)


def _init_manifest_if_missing(workspace: str) -> None:
    """Create the writable runtime manifest once, without versioned source data."""
    manifest_path = _workspace_manifest(workspace)
    if manifest_path.exists():
        return
    _save_manifest(workspace, {"exports": []})


def _source_url(source: dict) -> str:
    if source.get("type", "space") in {"page", "page-with-descendants"}:
        return source["url"]
    return f"{source['base_url'].rstrip('/')}/display/{source['space']}"


def _lock_summary(output_path: Path | None = None) -> dict:
    settings = get_settings()
    lock_path = (output_path or settings.export.output_path) / settings.export.lockfile_name
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
                "Check agent-cme runtime configuration and readiness for one workspace. "
                "Use this for questions about the agent-cme agent/server config, Confluence credentials, SSL/API mode, or last export state. "
                "Do not use wiki tools for live agent-cme configuration. "
                "Call this first — returns 'configured' or 'not_configured'. "
                "If not_configured, call cme_setup before doing anything else."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "workspace": {"type": "string", "description": "Workspace name. Agent configuration is stored in agent state; exports are written to the workspace raw/untracked directory."},
                },
                "required": ["workspace"],
            },
        ),
        Tool(
            name="cme_setup",
            description=(
                "One-time agent-cme initialization: stores Confluence credentials and connection settings persistently. "
                "Use this only to configure the live agent-cme Confluence exporter, not to edit llm-wiki markdown pages. "
                "After setup the server is autonomous — no reconfiguration needed on restart. "
                "Always provide username as the Confluence email/login. "
                "Provide pat (self-hosted PAT) or api_token (Atlassian Cloud)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "workspace": {"type": "string", "description": "Workspace name. Agent configuration is stored in agent state; exports are written to the workspace raw/untracked directory."},
                    "base_url": {"type": "string", "description": "Confluence base URL, e.g. http://confluence.meteo.fr"},
                    "username": {"type": "string", "description": "Confluence email address or login"},
                    "pat": {"type": "string", "description": "Personal Access Token (self-hosted)"},
                    "api_token": {"type": "string", "description": "API token (Atlassian Cloud)"},
                    "verify_ssl": {"type": "boolean", "description": "Verify SSL certificates (default: true)"},
                    "use_v2_api": {"type": "boolean", "description": "Use Confluence REST API v2 — for Data Center 8+ or Cloud (default: false)"},
                    "attachments_export": {
                        "type": "string",
                        "enum": ["referenced", "all", "disabled"],
                        "description": "Which page attachments to download during export (default: disabled).",
                    },
                },
                "required": ["workspace", "base_url", "username"],
            },
        ),
        Tool(
            name="cme_sources_list",
            description=(
                "List agent-cme configured Confluence export sources from the live manifest. "
                "Use this for questions about which Confluence spaces/pages the agent-cme agent exports. "
                "Do not use llm-wiki source listing tools for this runtime manifest."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "workspace": {"type": "string", "description": "Workspace name. Sources are read from the agent state manifest for this workspace."},
                },
                "required": ["workspace"],
            },
        ),
        Tool(
            name="cme_source_add",
            description=(
                "Add or update an agent-cme Confluence export source in the live manifest. "
                "Use this to configure what agent-cme exports, not to ingest or edit llm-wiki content. "
                "For type=space: provide base_url + space key. "
                "For type=page: provide url (full Confluence page URL). "
                "For type=page-with-descendants: provide url to export that page and all pages below it. "
                "If type is omitted and url is provided, the source type is inferred. "
                "The url may be raw or a Markdown link."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "workspace": {"type": "string", "description": "Workspace name. Sources are written to the agent state manifest for this workspace."},
                    "name": {"type": "string", "description": "Short identifier for this export source."},
                    "type": {"type": "string", "enum": ["space", "page", "page-with-descendants"], "description": "Export type (default: space)"},
                    "base_url": {"type": "string", "description": "Confluence base URL (required for type=space)"},
                    "space": {"type": "string", "description": "Space key, e.g. 'JDLCDPPO' (required for type=space)"},
                    "url": {"type": "string", "description": "Confluence page/space URL or Markdown link. Required for page types; type is inferred when omitted."},
                    "description": {"type": "string", "description": "Human description of this source"},
                },
                "required": ["workspace", "name"],
            },
        ),
        Tool(
            name="cme_source_remove",
            description=(
                "Remove an agent-cme Confluence export source from the live manifest by name. "
                "Use this for agent-cme export configuration only."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "workspace": {"type": "string", "description": "Workspace name. Sources are removed from the agent state manifest for this workspace."},
                    "name": {"type": "string", "description": "Name of the source to remove"},
                },
                "required": ["workspace", "name"],
            },
        ),
        Tool(
            name="cme_export_run",
            description=(
                "Start an asynchronous agent-cme Confluence-to-Markdown export for one or all configured sources. "
                "Use this when the user asks agent-cme to run or refresh an export. "
                "This only exports Confluence content to Markdown files on disk; it does not ingest content into llm-wiki. "
                "To ingest exported files into the wiki, run production_start_job(type=\"ingest\") separately. "
                "Use cme_export_cancel(job_id=...) to request cancellation of a running export; files already written before cancellation are left in place. "
                "If the export fails during the initial Confluence preflight request, such as /rest/api/space?limit=1, no markdown export files have been written yet. "
                "Returns a job_id immediately. Use cme_export_status(job_id=...) to follow progress."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "source_name": {"type": "string", "description": "Source name to export. If omitted, exports all sources."},
                    "workspace": {"type": "string", "description": "Target workspace name. Output is written to <workspace>/raw/untracked."},
                },
                "required": ["workspace"],
            },
        ),
        Tool(
            name="cme_export_cancel",
            description=(
                "Cancel a running agent-cme export job by job_id. "
                "This terminates the active cme subprocess when possible and marks the job cancelled. "
                "It does not delete files already written before cancellation."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "job_id": {"type": "string", "description": "Job ID returned by cme_export_run."},
                },
                "required": ["job_id"],
            },
        ),
        Tool(
            name="cme_export_status",
            description=(
                "Check agent-cme export job status, or return export lock/state summary if no job_id is given. "
                "Use this for live export progress and recent agent-cme export state."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "job_id": {"type": "string", "description": "Job ID returned by cme_export_run. If omitted, returns lock summary."},
                    "workspace": {"type": "string", "description": "Optional workspace name for lock summary when job_id is omitted."},
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
    start = time.perf_counter()
    print(f"[cme-mcp] tools/call {name}")
    try:
        match name:
            case "cme_status":
                result = await _tool_status(arguments)
            case "cme_setup":
                result = await _tool_setup(arguments)
            case "cme_sources_list":
                result = await _tool_sources_list(arguments)
            case "cme_source_add":
                result = await _tool_source_add(arguments)
            case "cme_source_remove":
                result = await _tool_source_remove(arguments)
            case "cme_export_run":
                result = await _tool_export_run(arguments)
            case "cme_export_cancel":
                result = await _tool_export_cancel(arguments)
            case "cme_export_status":
                result = await _tool_export_status(arguments)
            case _:
                result = [TextContent(type="text", text=f"Unknown tool: {name}")]
        elapsed_ms = round((time.perf_counter() - start) * 1000)
        status = "error" if any(item.text.startswith(("Error:", "Unknown tool:")) for item in result) else "ok"
        print(f"[cme-mcp] tools/result {name} {status} {elapsed_ms}ms")
        return result
    except Exception as exc:
        elapsed_ms = round((time.perf_counter() - start) * 1000)
        print(f"[cme-mcp] tools/result {name} exception {elapsed_ms}ms {exc}")
        raise


async def _tool_status(args: dict) -> list[TextContent]:
    workspace = _workspace_name(str(args.get("workspace", "")).strip())
    with _cme_workspace_context(workspace):
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
    lines = [
        f"status: {'configured' if configured else 'not_configured'}",
        f"version: {_AGENT_VERSION}",
        f"workspace: {workspace}",
        f"workspaces_root: {_WORKSPACES_ROOT}",
        f"data_dir: {_workspace_data_dir(workspace)}",
        f"config_path: {_workspace_cme_config(workspace)}",
    ]
    if configured:
        for url, info in instances.items():
            lines.append(f"  {url}  auth={info['auth']}")
        conn = data.get("connection_config", {})
        lines.append(f"verify_ssl: {conn.get('verify_ssl', True)}")
        lines.append(f"use_v2_api: {conn.get('use_v2_api', False)}")
        lines.append(f"attachments_export: {data.get('export', {}).get('attachments_export', 'disabled')}")
        with _cme_workspace_context(workspace):
            lock = _lock_summary()
        if lock.get("last_export"):
            lines.append(f"last_export: {lock['last_export']}")
    else:
        lines.append("action_required: call cme_setup to initialize")
    return [TextContent(type="text", text="\n".join(lines))]


async def _tool_setup(args: dict) -> list[TextContent]:
    workspace = _workspace_name(str(args.get("workspace", "")).strip())
    base_url: str = args["base_url"].rstrip("/")
    username: str = str(args.get("username", "")).strip()
    pat: str = args.get("pat", "")
    api_token: str = args.get("api_token", "")
    verify_ssl: bool = args.get("verify_ssl", True)
    use_v2_api: bool = args.get("use_v2_api", False)
    attachments_export: str = args.get("attachments_export", "disabled")
    try:
        if not username:
            return [TextContent(type="text", text="Error: username is required. Provide the Confluence email address or login.")]
        if attachments_export not in {"referenced", "all", "disabled"}:
            return [TextContent(type="text", text="Error: attachments_export must be one of: referenced, all, disabled.")]
        with _cme_workspace_context(workspace):
            set_setting_with_keys(["auth", "confluence", base_url, "username"], username)
            if pat:
                set_setting_with_keys(["auth", "confluence", base_url, "pat"], pat)
            if api_token:
                set_setting_with_keys(["auth", "confluence", base_url, "api_token"], api_token)
            set_setting("connection_config.verify_ssl", verify_ssl)
            set_setting("connection_config.use_v2_api", use_v2_api)
            set_setting("export.attachments_export", attachments_export)
        fields = [f for f in ("username", "pat", "api_token") if args.get(f)]
        return [TextContent(type="text", text=(
            f"OK: CME configured\n"
            f"workspace: {workspace}\n"
            f"instance: {base_url}\n"
            f"credentials: {', '.join(fields)}\n"
            f"verify_ssl: {verify_ssl}\n"
            f"use_v2_api: {use_v2_api}\n"
            f"attachments_export: {attachments_export}\n"
            f"config_path: {_workspace_cme_config(workspace)}\n"
            f"Config persisted for this workspace — no reconfiguration needed on restart."
        ))]
    except (ValueError, KeyError, TypeError) as e:
        return [TextContent(type="text", text=f"Error: {e}")]


async def _tool_sources_list(args: dict) -> list[TextContent]:
    workspace = _workspace_name(str(args.get("workspace", "")).strip())
    manifest = _load_manifest(workspace)
    with _cme_workspace_context(workspace):
        lock = _lock_summary()
    result = {
        "workspace": workspace,
        "manifest_path": str(_workspace_manifest(workspace)),
        "config_path": str(_workspace_cme_config(workspace)),
        "sources": manifest.get("exports", []),
        "lock": lock,
    }
    return [TextContent(type="text", text=yaml.dump(result, allow_unicode=True, default_flow_style=False))]


async def _tool_source_add(args: dict) -> list[TextContent]:
    workspace = _workspace_name(str(args.get("workspace", "")).strip())
    manifest = _load_manifest(workspace)
    exports: list = manifest.setdefault("exports", [])
    name = args["name"]
    source_type = args.get("type")
    source_url = extract_confluence_url(str(args.get("url", "")))
    parsed_source: dict[str, str] | None = None
    if source_url:
        try:
            parsed_source = parse_confluence_source_url(source_url)
        except ValueError as exc:
            return [TextContent(type="text", text=f"Error: {exc}")]
    if not source_type:
        source_type = parsed_source["type"] if parsed_source else "space"
    existing = next((e for e in exports if e.get("name") == name), None)
    entry: dict = existing or {}
    entry["name"] = name
    entry["type"] = source_type
    if source_type in {"page", "page-with-descendants"}:
        if not source_url:
            return [TextContent(type="text", text=f"Error: 'url' is required for type={source_type}")]
        if parsed_source and parsed_source["type"] != "page":
            return [TextContent(type="text", text=f"Error: page source URL points to a Confluence space")]
        entry.pop("base_url", None)
        entry.pop("space", None)
        entry["url"] = source_url
    elif source_type == "space":
        base_url = str(args.get("base_url", "")).strip()
        space = str(args.get("space", "")).strip()
        if base_url and not space:
            try:
                parsed_base_url = parse_confluence_source_url(base_url)
            except ValueError:
                parsed_base_url = None
            if parsed_base_url and parsed_base_url["type"] == "space":
                base_url = parsed_base_url["base_url"]
                space = parsed_base_url["space"]
        if parsed_source:
            if parsed_source["type"] != "space":
                return [TextContent(type="text", text="Error: space source URL points to a Confluence page")]
            base_url = parsed_source["base_url"]
            space = parsed_source["space"]
        if not base_url or not space:
            return [TextContent(type="text", text="Error: 'base_url' and 'space' are required for type=space")]
        entry.pop("url", None)
        entry["base_url"] = base_url.rstrip("/")
        entry["space"] = space
    else:
        return [TextContent(type="text", text=f"Error: unsupported source type '{source_type}'")]
    if args.get("description"):
        entry["description"] = args["description"]
    if existing is None:
        exports.append(entry)
        action = "added"
    else:
        action = "updated"
    _save_manifest(workspace, manifest)
    return [TextContent(type="text", text=f"OK: source '{name}' {action} in {_workspace_manifest(workspace)}")]


async def _tool_source_remove(args: dict) -> list[TextContent]:
    workspace = _workspace_name(str(args.get("workspace", "")).strip())
    manifest = _load_manifest(workspace)
    exports: list = manifest.get("exports", [])
    name = args["name"]
    before = len(exports)
    manifest["exports"] = [e for e in exports if e.get("name") != name]
    if len(manifest["exports"]) == before:
        return [TextContent(type="text", text=f"Error: source '{name}' not found")]
    _save_manifest(workspace, manifest)
    return [TextContent(type="text", text=f"OK: source '{name}' removed")]


async def _tool_export_run(args: dict) -> list[TextContent]:
    workspace = str(args.get("workspace", "")).strip()
    workspace_path = _validate_workspace(workspace)
    workspace_data_dir = _workspace_data_dir(workspace)
    cme_config_path = _workspace_cme_config(workspace)
    output_path = workspace_path / "raw" / "untracked"
    output_path.mkdir(parents=True, exist_ok=True)

    manifest = _load_manifest(workspace)
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

    space_urls = [
        url
        for s in sources if s.get("type", "space") == "space"
        for url in (
            f"{s['base_url'].rstrip('/')}/display/{s['space']}",
            f"{s['base_url'].rstrip('/')}/spaces/{s['space']}",
        )
    ]
    page_urls = [_source_url(s) for s in sources if s.get("type", "space") == "page"]
    page_descendant_urls = [_source_url(s) for s in sources if s.get("type", "space") == "page-with-descendants"]

    job_id = str(uuid.uuid4())[:8]
    _jobs[job_id] = {
        "status": "starting",
        "workspace": workspace,
        "data_path": str(workspace_data_dir),
        "workspace_path": str(workspace_path),
        "config_path": str(cme_config_path),
        "output_path": str(output_path),
        "sources": [s["name"] for s in sources],
        "started_at": _now(),
        "stdout": [],
        "stderr": [],
        "returncode": None,
        "process": None,
        "task": None,
    }

    async def _run_cmd(cmd: list[str]) -> int:
        env = {
            **os.environ,
            "CME_CONFIG_PATH": str(cme_config_path),
            "CME_EXPORT__OUTPUT_PATH": str(output_path),
        }
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(workspace_data_dir),
            env=env,
        )
        _jobs[job_id]["process"] = proc
        async def _read(stream: asyncio.StreamReader, buf: list) -> None:
            async for line in stream:
                buf.append(line.decode(errors="replace").rstrip())
        stdout_task = asyncio.create_task(_read(proc.stdout, _jobs[job_id]["stdout"]))
        stderr_task = asyncio.create_task(_read(proc.stderr, _jobs[job_id]["stderr"]))
        wait_task = asyncio.create_task(proc.wait())
        try:
            await asyncio.gather(stdout_task, stderr_task, wait_task)
            return proc.returncode
        except asyncio.CancelledError:
            if proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
            raise
        finally:
            for task in (stdout_task, stderr_task, wait_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(stdout_task, stderr_task, wait_task, return_exceptions=True)
            if _jobs[job_id].get("process") is proc:
                _jobs[job_id]["process"] = None

    async def _run() -> None:
        _jobs[job_id]["status"] = "running"
        try:
            rc = 0
            if space_urls:
                rc = await _run_cmd([_CME_BIN, "spaces", *space_urls])
            if page_urls and rc == 0:
                rc = await _run_cmd([_CME_BIN, "pages", *page_urls])
            if page_descendant_urls and rc == 0:
                rc = await _run_cmd([_CME_BIN, "pages-with-descendants", *page_descendant_urls])
            _jobs[job_id]["returncode"] = rc
            _jobs[job_id]["status"] = "success" if rc == 0 else "failed"
            _jobs[job_id]["finished_at"] = _now()
        except asyncio.CancelledError:
            _jobs[job_id]["status"] = "cancelled"
            _jobs[job_id]["finished_at"] = _now()
            _jobs[job_id]["error"] = "Export cancelled by cme_export_cancel."
        except Exception as e:
            _jobs[job_id]["status"] = "error"
            _jobs[job_id]["error"] = str(e)
            _jobs[job_id]["finished_at"] = _now()

    _jobs[job_id]["task"] = asyncio.create_task(_run())
    return [_json_content({
        "ok": True,
        "job_id": job_id,
        "status": _jobs[job_id]["status"],
        "workspace": workspace,
        "workspacePath": str(workspace_path),
        "outputPath": str(output_path),
        "sources": [s["name"] for s in sources],
        "message": f"Export started. Use cme_export_status(job_id='{job_id}') to follow progress.",
        "_activity": _activity_for_job(job_id, _jobs[job_id]),
    })]


async def _tool_export_cancel(args: dict) -> list[TextContent]:
    job_id = args["job_id"]
    job = _jobs.get(job_id)
    if not job:
        return [TextContent(type="text", text=f"Unknown job_id: {job_id}")]

    status = job.get("status")
    if status in {"success", "failed", "error", "cancelled"}:
        return [TextContent(type="text", text=f"Job {job_id} is already {status}.")]

    proc = job.get("process")
    if proc is not None and proc.returncode is None:
        proc.terminate()

    task = job.get("task")
    if task is not None and not task.done():
        task.cancel()

    job["status"] = "cancelling"
    return [TextContent(
        type="text",
        text=(
            f"Cancellation requested for job {job_id}. "
            "Use cme_export_status(job_id=...) to confirm it reaches cancelled. "
            "Files already written before cancellation are left in place."
        ),
    )]


async def _tool_export_status(args: dict) -> list[TextContent]:
    job_id = args.get("job_id")
    if job_id:
        job = _jobs.get(job_id)
        if not job:
            return [TextContent(type="text", text=f"Unknown job_id: {job_id}")]
        stdout = job.get("stdout", [])
        stderr = job.get("stderr", [])
        return [_json_content({
            "ok": True,
            "job_id": job_id,
            "status": job["status"],
            "workspace": job.get("workspace"),
            "agentStatePath": job.get("data_path"),
            "configPath": job.get("config_path"),
            "workspacePath": job.get("workspace_path"),
            "outputPath": job.get("output_path"),
            "sources": job.get("sources", []),
            "started_at": job.get("started_at"),
            "finished_at": job.get("finished_at"),
            "returncode": job.get("returncode"),
            "error": job.get("error"),
            "stdout_tail": stdout[-20:],
            "stderr_tail": stderr[-20:],
            "_activity": _activity_for_job(job_id, job),
        })]
    workspace = str(args.get("workspace", "") or "").strip()
    output_path = None
    if workspace:
        output_path = _validate_workspace(workspace) / "raw" / "untracked"
        with _cme_workspace_context(workspace):
            lock = _lock_summary(output_path)
    else:
        lock = {
            "status": "workspace_required",
            "message": "Provide workspace when job_id is omitted.",
        }
    return [TextContent(type="text", text=yaml.dump(lock, allow_unicode=True, default_flow_style=False))]


# ---------------------------------------------------------------------------
# Entrypoint — Streamable HTTP server
# ---------------------------------------------------------------------------

def main() -> None:
    streamable_http = StreamableHTTPSessionManager(
        app=app,
        event_store=None,
        json_response=False,
    )

    async def handle_mcp(scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")

        if path not in {"/mcp", "/mcp/"}:
            response = PlainTextResponse("Not Found", status_code=404)
            await response(scope, receive, send)
            return

        request = Request(scope, receive)
        if request.method == "GET" and _wants_html(request):
            scheme = "https" if ssl_certfile else "http"
            endpoint_url = f"{scheme}://{request.headers.get('host', f'{host}:{port}')}/mcp/"
            response = HTMLResponse(
                _render_landing_page(endpoint_url, scheme),
                headers={"Cache-Control": "no-store"},
            )
            await response(scope, receive, send)
            return

        mcp_scope = dict(scope)
        mcp_scope["path"] = "/"
        mcp_scope["root_path"] = f"{scope.get('root_path', '').rstrip('/')}/mcp"
        await streamable_http.handle_request(mcp_scope, receive, send)

    @contextlib.asynccontextmanager
    async def lifespan(starlette_app: Starlette) -> AsyncIterator[None]:
        async with streamable_http.run():
            yield

    starlette_app = Starlette(
        routes=[
            Mount("/", app=handle_mcp),
        ],
        middleware=[Middleware(_BearerAuthMiddleware)],
        lifespan=lifespan,
    )
    starlette_app = CORSMiddleware(
        starlette_app,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["*"],
        expose_headers=["Mcp-Session-Id"],
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

    scheme = "https" if ssl_certfile else "http"
    print(f"[cme-mcp] Streamable HTTP sur {scheme}://{host}:{port}/mcp")
    uvicorn.run(starlette_app, **uvicorn_kwargs)


if __name__ == "__main__":
    main()
