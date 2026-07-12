# Repository Guide

Current coordinated release: **0.14.5**. Keep `_AGENT_VERSION` aligned with
the coordinated workspace stack.

## Goal

`agent-cme` exposes `confluence-markdown-exporter` as an MCP Streamable HTTP
server. It lets an orchestrating agent configure Confluence export sources per
workspace, start asynchronous exports, monitor jobs, and write Markdown files
into a local export directory.

Since 0.12.0 it is also an **executor-only orchestrable agent** for the
manager's agnostic orchestration: it implements `agent_describe`
(`canPlan: false`, `singleTaskOnly: true`), `agent_execute`, `agent_status`
and `agent_cancel`, declaring the capability `external-source.export` with
`defaultRequiresApproval: true`. Configuring credentials or sources
(`cme_setup`, `cme_source_add`) must **never** trigger an export — exports run
only as approved orchestrated tasks (or explicit direct `cme_export_run`
calls). `agent_execute` is idempotent: key→job mappings are persisted so a
retry with a known `idempotencyKey` returns the existing job or result.

## Architecture

- `cme_mcp_server.py`: Starlette/uvicorn MCP server, bearer-auth middleware,
  HTML status page, tool definitions, source manifest handling, and async CME
  job execution.
- `cme_source_urls.py`: URL normalisation helpers (`extract_confluence_url`,
  `parse_confluence_source_url`). Accepts raw URLs, Markdown links, `/spaces/`,
  `/display/`, and `pageId=` forms and returns manifest-ready source fields.
  Used by `cme_mcp_server.py` to infer source type when `type` is omitted.
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
  `/data/<workspace>/`, for example `/data/my-project/cme/app_data.json` and
  `/data/my-project/sources-manifest.yaml`.
- When managed by `llm-wiki-manager`, the active `/use <workspace>` is injected
  automatically on every `cme_*` call (except `cme_export_cancel` and
  `cme_export_status(job_id=...)`). Direct MCP callers must pass `workspace` on
  each tool call.
- Keep `_AGENT_VERSION` aligned with the coordinated `llm-wiki-manager`
  release version so status responses identify the deployed agent bundle.
  Current release line: `0.12.0`. Alignment is checked by
  `llm-wiki-manager/scripts/check-versions.js` and synced by the root
  `build-and-push.sh`.
- MCP tool descriptions, `_activity` metadata, status pages, progress labels,
  and operator-facing errors must stay in English. Exported Confluence content
  keeps the source language; this service does not localize its UI from
  `.wikirc`.

## Common Commands

```bash
docker compose up --build
CME_WORKSPACE=my-project docker compose run --rm cme-cli config
CME_WORKSPACE=my-project docker compose run --rm cme-cli export
```

When managed by `llm-wiki-manager`, use the manager's global agent stack:

```bash
# from the llm-wiki-manager directory
wiki-workspace agents up
```

This starts CME, documents, and mailer together from `agents.docker-compose.yml`.
Auth token is read from `CME_MCP_AUTH_TOKEN` in the manager's `.env`.

**Auth, scopes, rate limiting** (0.10.3): `MCP_AUTH_TOKEN` remains a legacy
full-access (read+write) token; `MCP_READ_TOKEN`/`MCP_WRITE_TOKEN` grant
scoped access instead. `_token_scopes` compares with `hmac.compare_digest`
(constant-time). `_require_tool_scope` denies `_WRITE_TOOLS` (`agent_execute`, `agent_cancel`,
`cme_setup`, `cme_source_add`, `cme_source_remove`, `cme_export_run`,
`cme_export_cancel`) to read-only callers; the current request's scope is threaded through a
`contextvars.ContextVar` set by `_BearerAuthMiddleware`, not passed
explicitly. Requests are rate-limited (`MCP_RATE_LIMIT_REQUESTS`/
`MCP_RATE_LIMIT_WINDOW_SECONDS`, default 120/60s) keyed by token or remote IP.
`_any_token_configured()` is the single "is any token set" check, used both
at startup (unauthenticated-access warning) and inside `_token_scopes`; this
same auth/scope/rate-limit block is copy-pasted near-verbatim into the other
three agent repos (`agent-mailer-api`, `agent-wiki-documents`,
`agent-wiki-production`) and again, separately, in `llm-wiki`'s TypeScript
`mcpHttp.ts` — deliberate, not an oversight: these are four independently
deployed Docker images with no shared Python package between them today, so
extracting one is a real packaging/versioning decision, not a quick fix. If
that decision is made, keep the fix in whichever of these files is edited
first in sync with the other three by hand until a shared module exists.

**Multi-user status**: this agent's bearer-token scoping is
per-token, not per-user — it distinguishes read vs. write access, not one
caller's workspace/data from another's. The wikiLLM workspace remains an
industrialized single-user deployment baseline; the multi-user model
(identity, ownership, per-user scopes/conflicts) is specified in
`llm-wiki/docs/industrialisation.md` and planned next.
Until that lands, do not deploy this agent as a shared endpoint serving
distinct end users who should not see each other's export sources or data —
treat it as a workspace-scoped service protected by bearer tokens, the same
posture documented for the manager runtime and `llm-wiki mcp-http`.

For standalone start (without the full agent stack), from this directory:

```bash
WORKSPACES_ROOT=/path/to/workspaces CME_MCP_AUTH_TOKEN=<token> docker compose up -d
```
