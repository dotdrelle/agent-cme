# Repository Guide

## Goal

`agent-cme` exposes `confluence-markdown-exporter` as an MCP Streamable HTTP
server. It lets an orchestrating agent configure Confluence export sources per
workspace, start asynchronous exports, monitor jobs, and write Markdown files
into a local export directory.

## Architecture

- `cme_mcp_server.py`: Starlette/uvicorn MCP server, bearer-auth middleware,
  HTML status page, tool definitions, source manifest handling, and async CME
  job execution.
- `Dockerfile`: Python runtime with `confluence-markdown-exporter`, MCP,
  Starlette, uvicorn, and PyYAML.
- `docker-compose.yml`: global service and CLI profile. It mounts the shared
  workspaces root at `/workspaces`; each export receives the target workspace
  as a tool argument.
- `data/`: runtime state when running standalone. It contains per-workspace
  credentials, source manifests, exports, and job state and is not source.

## Constraints

- Do not hard-code host workspace paths. The server works against container
  paths, especially `/data` and `/workspaces`; callers pass a workspace name
  and the guardian validates it before writing output.
- Do not log or return Confluence secrets. Fields such as `username`,
  `api_token`, `pat`, and `password` must stay redacted in status responses.
- Authentication is optional for local development, but any documented token
  examples must use placeholders such as `<generated-local-token>`.
- `cme_setup` is synchronous configuration. Orchestrators should call it
  directly when required credentials are available, ask for exact missing
  values, or report unavailable CME tooling. Do not model setup as a background
  activity.
- Exports should be asynchronous and cancellable. Do not block the MCP request
  until a full Confluence export completes.
- `cme_export_run` and `cme_export_status(job_id=...)` should return JSON with
  additive `_activity` metadata so managers can poll progress through
  `cme.cme_export_status` without parsing CME-specific text.
- Keep `agent-cme` workspace-agnostic. Workspace names are request parameters,
  not container configuration. Runtime CME state is namespaced directly under
  `/data/<workspace>/`, for example `/data/juno/cme/app_data.json` and
  `/data/juno/sources-manifest.yaml`.
- When managed by `llm-wiki-manager`, the active `/use <workspace>` is injected
  automatically on every `cme_*` call (except `cme_export_cancel` and
  `cme_export_status(job_id=...)`). Direct MCP callers must pass `workspace` on
  each tool call.

## Common Commands

```bash
docker compose up --build
CME_WORKSPACE=juno docker compose run --rm cme-cli config
CME_WORKSPACE=juno docker compose run --rm cme-cli export
```

When managed by `llm-wiki-manager`, use the manager's global agent stack:

```bash
# from the llm-wiki-manager directory
wiki-workspace agents up
```

This starts CME, documents, and mailer together from `agents.docker-compose.yml`.
Auth token is read from `CME_MCP_AUTH_TOKEN` in the manager's `.env`.

For standalone start (without the full agent stack), from this directory:

```bash
WORKSPACES_ROOT=/path/to/workspaces CME_MCP_AUTH_TOKEN=<token> docker compose up -d
```
