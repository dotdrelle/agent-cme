# agent-cme — Agentic Confluence Markdown Exporter

[![License](https://img.shields.io/badge/license-PolyForm%20Noncommercial%201.0.0-blue)](https://polyformproject.org/licenses/noncommercial/1.0.0/)

MCP server that exposes [confluence-markdown-exporter](https://github.com/trentm/confluence-markdown-exporter) (CME) as a set of AI-agent tools. An orchestrating agent can configure CME once, manage export sources, and trigger asynchronous Confluence exports over MCP Streamable HTTP.

`agent-cme` is the exporter only. It writes Markdown exports under `data/exports/`; importing those exports into one or more `llm-wiki` workspaces is handled by `llm-wiki-manager`.

It belongs to a three-repository toolchain:

| Repository | Role |
| ---------- | ---- |
| [`agent-cme`](https://github.com/dotdrelle/agent-cme) | Confluence Markdown exporter and MCP server |
| [`llm-wiki`](https://github.com/dotdrelle/llm-wiki) | Local wiki workspace engine that ingests Markdown and builds deliverables |
| [`llm-wiki-manager`](https://github.com/dotdrelle/llm-wiki-manager) | Orchestrates several wiki workspaces and copies selected exports |

Do not configure `agent-cme` with a wiki workspace path. Keep exports in `agent-cme/data/exports/`, then let the manager copy selected Markdown into the target workspace.

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
server. Clients must then send `Authorization: Bearer your_secret_token`.

### From `llm-wiki-manager`

When this repository is used alongside `llm-wiki-manager`, start the shared MCP server from the manager directory:

```bash
cd ../llm-wiki-manager
./wiki-workspace cme up
```

The manager compose mounts `../agent-cme/data` into the container, so credentials, export source manifests, and exported Markdown remain in this repository.

### CLI one-shot (`cli` profile)

Configure CME directly from the command line without going through an MCP agent:

```bash
# configure credentials interactively
docker compose run --rm cme-cli config

# run an export manually
docker compose run --rm cme-cli export
```

The `cme-cli` service uses the `cme` binary from `confluence-markdown-exporter` and mounts the same `./data` volume as `cme-mcp`. Credentials written by `cme config` are immediately visible to the MCP server.

From the manager compose file (`llm-wiki-manager/`):

```bash
cd ../llm-wiki-manager
docker compose run --rm cme-cli config
docker compose run --rm cme-cli export
```

---

## First-run agent workflow

On first start, CME has no credentials. The agent must call `cme_setup` once.
After that, the server is fully autonomous across restarts.

```
1. cme_status          → "not_configured — call cme_setup"
2. cme_setup(...)      → credentials + connection settings written to /data/cme/app_data.json
3. cme_sources_list()  → inspect runtime sources from /data/sources-manifest.yaml
4. cme_source_add(...) → add/update sources if needed
5. cme_export_run(...) → async export started, returns job_id
6. cme_export_status(job_id=...) → monitor progress
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
| `cme_source_add`    | Add or update an export source (space or page).                                          |
| `cme_source_remove` | Remove an export source by name.                                                         |
| `cme_export_run`    | Start an async export. Returns a `job_id`.                                               |
| `cme_export_cancel` | Cancel a running export job. Files already written before cancellation are left in place. |
| `cme_export_status` | Check job progress, or show last-export summary.                                         |

### `cme_setup`

| Parameter    | Type    | Required | Description                                                    |
| ------------ | ------- | -------- | -------------------------------------------------------------- |
| `base_url`   | string  | yes      | Confluence base URL, e.g. `http://confluence.example.com`      |
| `username`   | string  | no       | Username or email                                              |
| `pat`        | string  | no       | Personal Access Token (self-hosted)                            |
| `api_token`  | string  | no       | API token (Atlassian Cloud)                                    |
| `verify_ssl` | boolean | no       | Verify SSL certificates (default: `true`)                      |
| `use_v2_api` | boolean | no       | Use REST API v2 — Data Center 8+ or Cloud (default: `false`)   |

Provide either `pat` for self-hosted Confluence, or `username` + `api_token`
for Atlassian Cloud. `base_url` alone stores connection settings but does not
make `cme_status` return `configured`.

### `cme_source_add`

| Parameter     | Type   | Required          | Description                    |
| ------------- | ------ | ----------------- | ------------------------------ |
| `name`        | string | yes               | Short identifier               |
| `type`        | string | no                | `space` (default) or `page`    |
| `base_url`    | string | if `type=space`   | Confluence base URL            |
| `space`       | string | if `type=space`   | Confluence space key           |
| `url`         | string | if `type=page`    | Full Confluence page URL       |
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
  --header "Authorization: Bearer your_secret_token"
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
        "Authorization": "Bearer your_secret_token"
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

Use `agent-cme` to create exports from Confluence. Use `llm-wiki-manager` to copy selected export directories into a target `llm-wiki` workspace and run:

```bash
./wiki-workspace wiki <workspace> doctor
./wiki-workspace wiki <workspace> ingest
./wiki-workspace wiki <workspace> build --plan
./wiki-workspace wiki <workspace> build
./wiki-workspace wiki <workspace> export
```

Do not point `agent-cme` directly at arbitrary workspace `raw/untracked` folders. Keeping export and ingest responsibilities separate avoids cross-workspace data leaks.

---

## License

agent-cme is licensed under the same license as `llm-wiki`:
[PolyForm Noncommercial 1.0.0](https://polyformproject.org/licenses/noncommercial/1.0.0/).
