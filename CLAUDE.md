# Repository Guide

## Goal

`agent-cme` exposes `confluence-markdown-exporter` as an MCP Streamable HTTP
server. It lets an orchestrating agent configure Confluence export sources,
start asynchronous exports, monitor jobs, and write Markdown files into a local
export directory.

## Architecture

- `cme_mcp_server.py`: Starlette/uvicorn MCP server, bearer-auth middleware,
  HTML status page, tool definitions, source manifest handling, and async CME
  job execution.
- `Dockerfile`: Python runtime with `confluence-markdown-exporter`, MCP,
  Starlette, uvicorn, and PyYAML.
- `docker-compose.yml`: standalone service and CLI profile. In manager mode,
  `llm-wiki-manager` provides the workspace-specific mounts.
- `data/`: runtime state when running standalone. It contains credentials,
  source manifests, exports, and job state and is not source.

## Constraints

- Do not hard-code host workspace paths. The server works against container
  paths, especially `/data` and `/data/exports`; the manager decides what those
  paths map to.
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
- Keep `agent-cme` workspace-agnostic. It should not know about
  `llm-wiki-manager` workspace names beyond the mounts it receives.

## Common Commands

```bash
docker compose up --build
docker compose run --rm cme-cli config
docker compose run --rm cme-cli export
```

When managed by `llm-wiki-manager`, start it from the manager repository:

```bash
./wiki-workspace cme <workspace> up
./wiki-workspace cme <workspace> logs
```
