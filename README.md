# agent-cme — Agentic Confluence Markdown Exporter

[![License: PolyForm Noncommercial 1.0.0](https://img.shields.io/badge/license-PolyForm%20Noncommercial%201.0.0-blue)](LICENSE)

MCP server that exposes [confluence-markdown-exporter](https://github.com/trentm/confluence-markdown-exporter) (CME) as a set of AI-agent tools. An orchestrating agent can configure CME once, manage export sources, and trigger asynchronous Confluence exports over MCP Streamable HTTP.

`agent-cme` is the exporter only. By default, standalone runs write Markdown
exports under `data/exports/`. When launched by `llm-wiki-manager`, the manager
mounts a specific workspace so exports land directly in that workspace's
`raw/untracked/` directory.

It belongs to a three-repository toolchain:

| Repository | Role |
| ---------- | ---- |
| [`agent-cme`](https://github.com/dotdrelle/agent-cme) | Confluence Markdown exporter and MCP server |
| [`llm-wiki`](https://github.com/dotdrelle/llm-wiki) | Local wiki workspace engine that ingests Markdown and builds deliverables |
| [`llm-wiki-manager`](https://github.com/dotdrelle/llm-wiki-manager) | Orchestrates several wiki workspaces and workspace-scoped CME agents |

Do not hard-code workspace paths inside `agent-cme`. The agent always works with
its container paths (`/data` and `/data/exports`); the manager decides which
workspace those paths map to.

## Architecture

```
Orchestrating agent (Claude or other)
        │  MCP Streamable HTTP
        ▼
  agent-cme MCP server  (port 3000)
        │
        ├── /data/cme/app_data.json   ← CME credentials + connection settings (persistent)
        ├── /data/sources-manifest.yaml ← export sources managed by agent (persistent)
        └── /data/exports/            ← exported markdown files + CME lock file (persistent)
```

All runtime state lives in `./data/` on the host, mounted as a Docker volume.
No export source is versioned in Git. On first startup, agent-cme creates an empty
runtime manifest at `./data/sources-manifest.yaml` if it does not already exist.
MCP edits are persisted there and are not overwritten on later restarts.

---

## Quick start

### Standalone

```bash
cd agent-cme

docker compose up --build
```

The MCP endpoint starts on `http://localhost:3000/mcp/`.
Opening that URL in a browser shows a status page. MCP clients use the same URL
for Streamable HTTP requests.

Authentication is disabled by default. With Docker Compose, set
`CME_MCP_AUTH_TOKEN`; it is mapped to the internal `MCP_AUTH_TOKEN` used by the
server. Clients must then send `Authorization: Bearer <generated-local-token>`.

### From `llm-wiki-manager`

When this repository is used alongside `llm-wiki-manager`, start a
workspace-scoped CME MCP server from the manager directory:

```bash
cd ../llm-wiki-manager
./wiki-workspace cme <workspace> up
```

The manager compose mounts:

```text
<workspace>/.cme          -> /data
<workspace>/raw/untracked -> /data/exports
```

So credentials and source manifests are stored under `<workspace>/.cme/`, while
exported Markdown is written directly to `<workspace>/raw/untracked/`.

### CLI one-shot (`cli` profile)

Configure CME directly from the command line without going through an MCP agent:

```bash
# configure credentials interactively
docker compose run --rm cme-cli config

# run an export manually
docker compose run --rm cme-cli export
```

The `cme-cli` service uses the `cme` binary from `confluence-markdown-exporter` and mounts the same `./data` volume as `cme-mcp`. Credentials written by `cme config` are immediately visible to the MCP server.

From the manager compose file (`llm-wiki-manager/`), prefer the workspace-scoped
MCP service:

```bash
cd ../llm-wiki-manager
./wiki-workspace cme <workspace> up
```

The manager's `cme-cli` profile is still available for debugging, but it must be
run with a workspace `.env` so `WIKI_WORKSPACE_PATH` points at the intended
workspace.

---

## First-run agent workflow

On first start, CME has no credentials. The agent must call `cme_setup` once.
After that, the server is fully autonomous across restarts.

`cme_setup` is synchronous: it writes configuration and returns immediately. It
does not create `_activity` metadata and will not appear in an Activity panel.
An orchestrator should either call it in the same turn, ask for missing required
credentials, or report that the CME tool/server is unavailable. It should not
answer with a plain-text promise to call `cme_setup` later.

```
1. cme_status          → "not_configured — call cme_setup"
2. cme_setup(...)      → credentials + connection settings written to /data/cme/app_data.json
3. cme_sources_list()  → inspect runtime sources from /data/sources-manifest.yaml
4. cme_source_add(...) → add/update sources if needed
5. cme_export_run(...) → async export started, returns JSON with `job_id` and `_activity`
6. cme_export_status(job_id=...) → monitor progress, returns JSON with `_activity`
7. cme_export_cancel(job_id=...) → cancel a running export when needed
```

On subsequent restarts: `cme_status` returns `configured` and the agent skips straight to step 3+.

---

## Tools reference

| Tool                | Description                                                                              |
| ------------------- | ---------------------------------------------------------------------------------------- |
| `cme_status`        | Check if CME is configured. Always call this first.                                      |
| `cme_setup`         | One-time initialization: credentials + connection settings.                              |
| `cme_sources_list`  | List all configured export sources.                                                      |
| `cme_source_add`    | Add or update an export source (space, page, or page-with-descendants).                  |
| `cme_source_remove` | Remove an export source by name.                                                         |
| `cme_export_run`    | Start an async export. Returns JSON with `job_id`, status, sources, and `_activity`.      |
| `cme_export_cancel` | Cancel a running export job. Files already written before cancellation are left in place. |
| `cme_export_status` | Check job progress, or show last-export summary. With `job_id`, returns `_activity`.      |

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
| `base_url`   | string  | yes      | Confluence base URL, e.g. `http://confluence.example.com`      |
| `username`   | string  | yes      | Confluence email address or login                              |
| `pat`        | string  | no       | Personal Access Token (self-hosted)                            |
| `api_token`  | string  | no       | API token (Atlassian Cloud)                                    |
| `verify_ssl` | boolean | no       | Verify SSL certificates (default: `true`)                      |
| `use_v2_api` | boolean | no       | Use REST API v2 — Data Center 8+ or Cloud (default: `false`)   |

Always provide `username` as the Confluence email/login. Provide either `pat`
for self-hosted Confluence, or `api_token` for Atlassian Cloud. `base_url` and
`username` alone store connection settings but do not make `cme_status` return
`configured`.

### `cme_source_add`

| Parameter     | Type   | Required          | Description                    |
| ------------- | ------ | ----------------- | ------------------------------ |
| `name`        | string | yes               | Short identifier               |
| `type`        | string | no                | `space` (default), `page`, or `page-with-descendants` |
| `base_url`    | string | if `type=space`   | Confluence base URL            |
| `space`       | string | if `type=space`   | Confluence space key           |
| `url`         | string | if `type=page*`   | Full Confluence page URL       |
| `description` | string | no                | Human description              |

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

CME credentials are read from the default OS path if `CME_CONFIG_PATH` is not set:

- macOS: `~/Library/Application Support/confluence-markdown-exporter/app_data.json`
- Linux: `~/.config/confluence-markdown-exporter/app_data.json`

---

## Data directory layout

```
agent-cme/data/               ← mounted at /data in the container
├── cme/
│   └── app_data.json        ← CME credentials + settings (written by cme_setup)
├── sources-manifest.yaml    ← runtime export sources (created empty, then managed by agent)
└── exports/                 ← exported markdown files
    └── confluence-lock.json ← CME lock file (written by CME during export)
```

`data/` is gitignored — it contains credentials and generated content.

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
