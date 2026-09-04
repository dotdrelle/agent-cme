# agent-cme — Agentic Confluence Markdown Exporter

[![License: PolyForm Noncommercial 1.0.0](https://img.shields.io/badge/license-PolyForm%20Noncommercial%201.0.0-blue)](LICENSE)

MCP server that exposes [confluence-markdown-exporter](https://github.com/trentm/confluence-markdown-exporter) (CME) as a set of AI-agent tools. An orchestrating agent can configure CME per workspace, manage export sources, and trigger asynchronous Confluence exports over MCP Streamable HTTP.

`agent-cme` is the exporter only. One global instance serves all workspaces:
credentials and connection settings are agent-wide (shared across all
workspaces — they belong to the operator's Confluence account), while source
manifests, export output and export locks stay per workspace. When managed by
`llm-wiki-manager`, the active workspace is injected automatically on every
tool call — orchestrators never pass `workspace` explicitly. Each workspace's
export output lands directly in its `raw/untracked/` directory.

It belongs to a three-repository toolchain:

| Repository | Role |
| ---------- | ---- |
| [`agent-cme`](https://github.com/dotdrelle/agent-cme) | Confluence Markdown exporter and MCP server |
| [`llm-wiki`](https://github.com/dotdrelle/llm-wiki) | Local wiki workspace engine that ingests Markdown and builds deliverables |
| [`llm-wiki-manager`](https://github.com/dotdrelle/llm-wiki-manager) | Orchestrates several wiki workspaces; starts agents globally via `wiki-workspace agents up` |

Do not hard-code workspace paths inside `agent-cme`. The agent always works with
container paths (`/data` and `/workspaces`); the `workspace` tool argument
selects the target, validated by the guardian before any file is written.

## Architecture

```
Orchestrating agent (Claude or other)
        │  MCP Streamable HTTP
        ▼
  agent-cme MCP server  (port 3000)
        │
        ├── /data/app_data.json                     ← CME credentials + connection settings (agent-wide)
        ├── /data/<workspace>/sources-manifest.yaml ← export sources, per workspace
        └── /workspaces/<workspace>/raw/untracked/  ← exported Markdown output
```

All runtime state lives in `./data/` on the host, mounted as a Docker volume.
No export source is versioned in Git. On first use for a workspace, agent-cme
creates `./data/<workspace>/sources-manifest.yaml` if it does not already exist.
MCP edits are persisted there and are not overwritten on later restarts.
On startup, legacy per-workspace configs (`./data/<workspace>/cme/app_data.json`
from the pre-shared layout) are merged into the agent-wide config — each
instance keyed by its `base_url` — and renamed to `app_data.json.migrated`.
Merged credentials never destroy an existing entry: a conflicting instance is
kept as-is and the conflict is announced in the server log.

---

## Quick start

### Standalone

```bash
cd agent-external/agent-cme

WORKSPACES_ROOT=/path/to/workspaces docker compose up --build
```

The MCP endpoint starts on `http://localhost:3336/mcp/`.
Opening that URL in a browser shows a status page. MCP clients use the same URL
for Streamable HTTP requests.

Authentication is disabled by default. With Docker Compose, set
`CME_MCP_AUTH_TOKEN`; it is mapped to the internal `MCP_AUTH_TOKEN` used by the
server. Clients must then send `Authorization: Bearer <generated-local-token>`.

### From `llm-wiki-manager`

When this repository is used alongside `llm-wiki-manager`, start all external
agents together from the manager directory:

```bash
# manager/.env must have WORKSPACES_ROOT and CME_MCP_AUTH_TOKEN set
wiki-workspace agents up
```

This uses `agents.docker-compose.yml` and starts CME, documents, and mailer as
a single stack. Register the endpoint in `mcp.endpoints.json` using `${VAR}`
interpolation so the token is never hard-coded:

```json
{
  "mcpServers": {
    "cme": {
      "url": "http://host.docker.internal:${CME_MCP_PORT:-3336}/mcp/",
      "headers": { "Authorization": "Bearer ${CME_MCP_AUTH_TOKEN}" }
    }
  }
}
```

Credentials are stored agent-wide in `.agents-data/cme/app_data.json`
(one config shared by every workspace, keyed by Confluence base URL). Source
manifests stay per workspace:

```txt
.agents-data/cme/app_data.json                  ← shared credentials + connection settings
.agents-data/cme/my-project/sources-manifest.yaml
```

Each `cme_export_run()` for the active workspace writes Markdown directly to
`/workspaces/my-project/raw/untracked/`.

### CLI one-shot (`cli` profile)

Configure CME directly from the command line without going through an MCP agent:

```bash
# configure credentials interactively (agent-wide config)
docker compose run --rm cme-cli config

# run an export manually
docker compose run --rm cme-cli export
```

The `cme-cli` service uses the `cme` binary from `confluence-markdown-exporter` and mounts the same `./data` volume as `cme-mcp`. It writes to the shared `/data/app_data.json`, immediately visible to the MCP server for every workspace.

Register the running endpoint in `llm-wiki-manager/mcp.endpoints.json` as
`cme`; the manager and served chat UI load it as an external MCP endpoint.

---

## First-run agent workflow

On first start, CME has no credentials. The agent must call `cme_setup` once
per Confluence instance (the config is agent-wide, so every workspace sees the
result). After that, the server is fully autonomous across restarts.

`cme_setup` is synchronous: it writes configuration and returns immediately. It
does not create `_activity` metadata and will not appear in an Activity panel.
An orchestrator should either call it in the same turn, ask for missing required
credentials, or report that the CME tool/server is unavailable. It should not
answer with a plain-text promise to call `cme_setup` later.

**Via `llm-wiki-manager`** — `workspace` is injected automatically by Donna;
the active `/use <workspace>` is set once and applies to every call below:

```
1. cme_status          → "not_configured — call cme_setup"
2. cme_setup(...)      → agent-wide credentials + settings (once per Confluence instance)
3. cme_sources_list()  → inspect runtime sources for active workspace
4. cme_source_add(...) → add/update sources if needed
5. cme_export_run()    → async export started, returns JSON with `job_id` and `_activity`
6. cme_export_status(job_id=...) → monitor progress, returns JSON with `_activity`
7. cme_export_cancel(job_id=...) → cancel a running export when needed
```

**Direct MCP / standalone** — pass `workspace` explicitly on every call that
scopes a workspace (`cme_setup` and `cme_test_connection` accept it but ignore
it: credentials are agent-wide):

```
1. cme_status(workspace="my-project")
2. cme_setup(base_url="https://confluence.example.com", username=..., pat=...)
3. cme_sources_list(workspace="my-project")
4. cme_source_add(workspace="my-project", ...)
5. cme_export_run(workspace="my-project")
6. cme_export_status(job_id=...)
7. cme_export_cancel(job_id=...)
```

On subsequent restarts: `cme_status` returns `configured` and the agent skips straight to step 3+.

---

## Tools reference

| Tool                | Description                                                                              |
| ------------------- | ---------------------------------------------------------------------------------------- |
| `cme_status`        | Check if CME is configured (agent-wide credentials). Always call this first.             |
| `cme_setup`         | Agent-wide initialization: credentials + connection settings, keyed by Confluence base URL. |
| `cme_test_connection` | Live authenticated probe of every configured Confluence instance (real request, no export). |
| `cme_confluence_search` | Live read-only Confluence search (free-text or CQL), using the stored credentials.    |
| `cme_wiki_search`   | Live read-only search over the workspace `wiki/**/*.md` pages (title + excerpt).         |
| `cme_sources_list`  | List configured export sources for one workspace.                                       |
| `cme_source_add`    | Add or update a workspace export source (space, page, or page-with-descendants).         |
| `cme_source_remove` | Remove a workspace export source by name.                                                |
| `cme_export_run`    | Start an async workspace export. Returns JSON with `job_id`, status, sources, and `_activity`. |
| `cme_export_cancel` | Cancel a running export job. Files already written before cancellation are left in place. |
| `cme_export_status` | Check job progress, or show last-export summary. With `job_id`, returns `_activity`.      |

### Live search (no export involved)

The two `*_search` tools are separate components inside the same agent
(`confluence_search.py` and `wiki_search.py`), both read-only:

- `cme_confluence_search` runs one bounded CQL request against the instance
  the workspace's sources point at (falling back to the agent's configured
  instance when the workspace declares none), using the shared credentials
  (`text ~ "query"` is built from a free-text query; pass `cql` for a full
  expression, `space_key` to restrict it). The reply names the `instance`
  that was searched. Returns pages with title, type, space, URL and a short
  excerpt. Use it to explore Confluence directly before deciding what to
  export.
- `cme_wiki_search` walks `workspaces/<workspace>/wiki/` on disk and matches
  the query's tokens case- and accent-insensitively, returning the best pages
  with their first-`#` title and an excerpt. No index is built, nothing is
  written. Use it to check what the local wiki already covers before filing
  new sources.

Neither tool needs the write scope: they are deliberately absent from the
write-only tool list.

Orchestration contract (used by `llm-wiki-manager`'s generic orchestrator —
executor-only agent, `canPlan: false`):

| Tool             | Description                                                                                  |
| ---------------- | -------------------------------------------------------------------------------------------- |
| `agent_describe` | Declare the `external-source.export` capability (`defaultRequiresApproval: true`), limits, health. |
| `agent_execute`  | Execute one approved export task; idempotent via `idempotencyKey` (a retry returns the existing job). |
| `agent_status`   | Report orchestrated task progress and final result.                                          |
| `agent_cancel`   | Cancel the job bound to one orchestrated task.                                               |

Configuring credentials or sources never triggers an export: exports run only
as approved orchestrated tasks or explicit `cme_export_run` calls.

### Activity metadata

`cme_export_run` and `cme_export_status(job_id=...)` include additive
`_activity` metadata so shells/orchestrators can monitor jobs without knowing
CME-specific response details:

```json
{
  "job_id": "abc12345",
  "status": "running",
  "_activity": {
    "id": "abc12345",
    "source": "cme",
    "kind": "export",
    "status": "running",
    "poll": {
      "server": "cme",
      "tool": "cme_export_status",
      "args": { "job_id": "abc12345" },
      "intervalMs": 2500
    }
  }
}
```

### `cme_setup`

| Parameter    | Type    | Required | Description                                                    |
| ------------ | ------- | -------- | -------------------------------------------------------------- |
| `workspace`  | string  | no       | Accepted for compatibility, ignored — credentials are agent-wide |
| `base_url`   | string  | yes      | Confluence base URL, e.g. `http://confluence.example.com`      |
| `username`   | string  | yes      | Confluence email address or login                              |
| `pat`        | string  | no       | Personal Access Token (self-hosted)                            |
| `api_token`  | string  | no       | API token (Atlassian Cloud)                                    |
| `verify_ssl` | boolean | no       | Verify SSL certificates (default: `true`)                      |
| `use_v2_api` | boolean | no       | Use REST API v2 — Data Center 8+ or Cloud (default: `false`)   |
| `attachments_export` | string | no   | `referenced`, `all`, or `disabled` (default: `disabled`) — agent-wide |

Always provide `username` as the Confluence email/login. Provide either `pat`
for self-hosted Confluence, or `api_token` for Atlassian Cloud. `base_url` and
`username` alone store connection settings but do not make `cme_status` return
`configured`. Credentials are stored keyed by `base_url` in the agent-wide
config: call `cme_setup` once per distinct Confluence instance, and every
workspace sharing that instance uses the same credentials.

### `cme_source_add`

| Parameter     | Type   | Required          | Description                    |
| ------------- | ------ | ----------------- | ------------------------------ |
| `workspace`   | string | yes               | Workspace name                 |
| `name`        | string | yes               | Short identifier               |
| `type`        | string | no                | `space` (default), `page`, or `page-with-descendants` |
| `base_url`    | string | if `type=space`   | Confluence base URL            |
| `space`       | string | if `type=space`   | Confluence space key           |
| `url`         | string | if `type=page*`   | Full Confluence URL or Markdown link |
| `description` | string | no                | Human description              |

When `type` is omitted and `url` is provided, CME infers `space` or `page`.
Supported forms include `/spaces/<key>`, `/spaces/<key>/pages/<id>`,
`/display/<key>`, and legacy `viewpage.action?pageId=...` URLs.

---

## HTTPS

To enable TLS, uncomment the SSL lines in `docker-compose.yml` and provide certificate files:

```bash
mkdir certs
# Place server.crt and server.key in agent-cme/certs/
```

```yaml
# docker-compose.yml — uncomment:
environment:
  - MCP_SSL_CERTFILE=/certs/server.crt
  - MCP_SSL_KEYFILE=/certs/server.key
volumes:
  - ./certs:/certs:ro
```

Then update your MCP client URL from `http://` to `https://`.

The server refuses to start if only one of the two SSL variables is set, or if a file is missing.

---

## Connecting MCP clients

With Docker Compose:

```bash
cd agent-cme
docker compose up --build
```

### Claude Code

```bash
claude mcp add --transport http cme http://localhost:3000/mcp/
```

If `CME_MCP_AUTH_TOKEN` is set in Docker Compose, or `MCP_AUTH_TOKEN` is set for
direct local development:

```bash
claude mcp add --transport http cme http://localhost:3000/mcp/ \
  --header "Authorization: Bearer <generated-local-token>"
```

For scoped HTTP access, set `MCP_READ_TOKEN` for status/list clients and
`MCP_WRITE_TOKEN` for clients allowed to configure sources or start/cancel
exports. `MCP_AUTH_TOKEN` remains a legacy full-access read+write token. Rate
limiting defaults to 120 requests per 60 seconds and can be tuned with
`MCP_RATE_LIMIT_REQUESTS` and `MCP_RATE_LIMIT_WINDOW_SECONDS`.

### Claude Code (`.mcp.json`)

Without token:

```json
{
  "mcpServers": {
    "cme": {
      "type": "http",
      "url": "http://localhost:3000/mcp/"
    }
  }
}
```

With token:

```json
{
  "mcpServers": {
    "cme": {
      "type": "http",
      "url": "http://localhost:3000/mcp/",
      "headers": {
        "Authorization": "Bearer <generated-local-token>"
      }
    }
  }
}
```

### OpenWebUI

Register agent-cme as an MCP server:

```
Type: MCP (Streamable HTTP)
URL:  http://localhost:3000/mcp/
Auth: None
```

If OpenWebUI itself runs in Docker, `localhost` means the OpenWebUI container,
not your host. Use one of these instead:

```
http://host.docker.internal:3000/mcp/
```

or, if OpenWebUI is on the same Compose network:

```
http://cme-mcp:8080/mcp/
```

---

## Local development (without Docker)

The server auto-detects the local CME venv at `.cme/`. Install both CME and the
MCP HTTP server dependencies in that venv:

```bash
cd agent-cme
python3 -m venv .cme
.cme/bin/pip install --upgrade pip
.cme/bin/pip install confluence-markdown-exporter "mcp>=1.9.4" starlette uvicorn pyyaml
.cme/bin/python cme_mcp_server.py
# Starts on http://0.0.0.0:8080/mcp/ by default.
# Use MCP_PORT=3000 .cme/bin/python cme_mcp_server.py if you want local dev on port 3000.
# CME_DATA_DIR defaults to agent-cme/
```

CME credentials are read from the agent-wide `CME_CONFIG_PATH`
(`CME_DATA_DIR/app_data.json`) during MCP tool calls. If you run the
underlying `cme` binary manually without setting it, the binary falls back to
its default OS path:

- macOS: `~/Library/Application Support/confluence-markdown-exporter/app_data.json`
- Linux: `~/.config/confluence-markdown-exporter/app_data.json`

---

## Data directory layout

```
agent-cme/data/                ← mounted at /data in the container
├── app_data.json                ← CME credentials + connection settings (agent-wide, keyed by base URL)
├── <workspace>/
│   └── sources-manifest.yaml    ← runtime export sources (per workspace)
└── jobs/
    └── idempotency.json         ← export job idempotency store (agent-wide)
```

`data/` is gitignored — it contains credentials and generated content. Export
output and the export lock live in each workspace's `raw/untracked/` directory,
never under `data/`.

## Relationship With llm-wiki

Use `agent-cme` to create exports from Confluence. In manager mode, exports land
directly in the target workspace, then run:

```bash
./wiki-workspace wiki <workspace> doctor
./wiki-workspace wiki <workspace> ingest
./wiki-workspace wiki <workspace> build --plan
./wiki-workspace wiki <workspace> build
./wiki-workspace wiki <workspace> export
```

The workspace binding is provided by Docker mounts from `llm-wiki-manager`; keep
`agent-cme` itself workspace-agnostic.

---

## License

Released under the **PolyForm Noncommercial License 1.0.0**. See [LICENSE](LICENSE).
