# AgentCME — Agentic Confluence Markdown Exporter

MCP server that exposes [confluence-markdown-exporter](https://github.com/trentm/confluence-markdown-exporter) (CME) as a set of AI-agent tools. An orchestrating agent can configure CME once, manage export sources, and trigger asynchronous Confluence exports — all over HTTP/SSE.

## Architecture

```
Orchestrating agent (Claude or other)
        │  MCP over HTTP/SSE
        ▼
  AgentCME MCP server  (port 8080)
        │
        ├── /data/cme/app_data.json   ← CME credentials + connection settings (persistent)
        ├── /data/sources-manifest.yaml ← export sources managed by agent (persistent)
        └── /data/exports/            ← exported markdown files + CME lock file (persistent)
```

All runtime state lives in `./data/` on the host, mounted as a Docker volume.
The versioned `sources-manifest.yaml` at the project root is used only as the
initial seed: on first startup it is copied to `./data/sources-manifest.yaml` if
that runtime file does not already exist. MCP edits are then persisted in
`./data/sources-manifest.yaml` and are not overwritten on later restarts.

---

## Quick start

```bash
cd AgentCME

# Inject the MCP bearer token from your shell (never written to a file)
export MCP_AUTH_TOKEN=your_secret_token

docker compose up --build
```

The server starts on `http://localhost:8080/sse`.
If `MCP_AUTH_TOKEN` is set, MCP clients must send
`Authorization: Bearer your_secret_token`.

---

## First-run agent workflow

On first start, CME has no credentials. The agent must call `cme_setup` once.
After that, the server is fully autonomous across restarts.

```
1. cme_status          → "not_configured — call cme_setup"
2. cme_setup(...)      → credentials + connection settings written to /data/cme/app_data.json
3. cme_sources_list()  → inspect seeded sources from /data/sources-manifest.yaml
4. cme_source_add(...) → add/update sources if needed
5. cme_export_run(...) → async export started, returns job_id
6. cme_export_status(job_id=...) → monitor progress
```

On subsequent restarts: `cme_status` returns `configured` and the agent skips straight to step 3+.

---

## Tools reference

| Tool | Description |
|------|-------------|
| `cme_status` | Check if CME is configured. Always call this first. |
| `cme_setup` | One-time initialization: credentials + connection settings. |
| `cme_sources_list` | List all configured export sources. |
| `cme_source_add` | Add or update an export source (space or page). |
| `cme_source_remove` | Remove an export source by name. |
| `cme_export_run` | Start an async export. Returns a `job_id`. |
| `cme_export_status` | Check job progress, or show last-export summary. |

### `cme_setup`

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `base_url` | string | yes | Confluence base URL, e.g. `http://confluence.example.com` |
| `username` | string | no | Username or email |
| `pat` | string | no | Personal Access Token (self-hosted) |
| `api_token` | string | no | API token (Atlassian Cloud) |
| `verify_ssl` | boolean | no | Verify SSL certificates (default: `true`) |
| `use_v2_api` | boolean | no | Use REST API v2 — Data Center 8+ or Cloud (default: `false`) |

Provide either `pat` for self-hosted Confluence, or `username` + `api_token`
for Atlassian Cloud. `base_url` alone stores connection settings but does not
make `cme_status` return `configured`.

### `cme_source_add`

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | string | yes | Short identifier, e.g. `juno` |
| `type` | string | no | `space` (default) or `page` |
| `base_url` | string | if `type=space` | Confluence base URL |
| `space` | string | if `type=space` | Space key, e.g. `JDLCDPPO` |
| `url` | string | if `type=page` | Full Confluence page URL |
| `description` | string | no | Human description |

---

## HTTPS

To enable TLS, uncomment the SSL lines in `docker-compose.yml` and provide certificate files:

```bash
mkdir certs
# Place server.crt and server.key in AgentCME/certs/
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

### Claude Code (`.mcp.json`)

HTTP without bearer token:
```json
{
  "mcpServers": {
    "cme": {
      "url": "http://localhost:8080/sse"
    }
  }
}
```

HTTP/HTTPS with bearer token:
```json
{
  "mcpServers": {
    "cme": {
      "url": "http://localhost:8080/sse",
      "headers": {
        "Authorization": "Bearer your_secret_token"
      }
    }
  }
}
```

### Generic SSE MCP client

```
SSE endpoint :  GET  http(s)://host:8080/sse
Message POST :  POST http(s)://host:8080/messages/?session_id=<id>
Auth header  :  Authorization: Bearer <MCP_AUTH_TOKEN>   (if token is set)
```

---

## Local development (without Docker)

The server auto-detects the local CME venv at `.cme/`. Install both CME and the
MCP HTTP server dependencies in that venv:

```bash
cd AgentCME
python3.11 -m venv .cme
.cme/bin/pip install --upgrade pip
.cme/bin/pip install confluence-markdown-exporter mcp starlette uvicorn sse-starlette pyyaml
.cme/bin/python cme_mcp_server.py
# Starts on http://0.0.0.0:8080/sse
# CME_DATA_DIR defaults to AgentCME/
```

CME credentials are read from the default OS path if `CME_CONFIG_PATH` is not set:
- macOS: `~/Library/Application Support/confluence-markdown-exporter/app_data.json`
- Linux: `~/.config/confluence-markdown-exporter/app_data.json`

---

## Data directory layout

```
AgentCME/data/               ← mounted at /data in the container
├── cme/
│   └── app_data.json        ← CME credentials + settings (written by cme_setup)
├── sources-manifest.yaml    ← export sources (seeded once, then managed by agent)
└── exports/                 ← exported markdown files
    └── confluence-lock.json ← CME lock file (written by CME during export)
```

`data/` is gitignored — it contains credentials and generated content.
