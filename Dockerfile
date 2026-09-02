# python:3.13-slim si confluence-markdown-exporter n'a pas encore de wheel 3.14
FROM python:3.14-slim

WORKDIR /app

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir \
    confluence-markdown-exporter==5.2.1 \
    "mcp>=1.9.4,<2" \
    starlette \
    uvicorn \
    pyyaml
# Patch CME library: expand space_key regex to support personal spaces (~user@domain.com).
# Upstream [A-Za-z0-9_~-]+ / [A-Za-z0-9._-]+ stop at '.' or '@' in email-style keys.
COPY patch_cme_urls.py .
RUN python3 patch_cme_urls.py && rm patch_cme_urls.py

COPY cme_mcp_server.py .
COPY cme_source_urls.py .
# Read-only search components of the same agent, imported at module load by
# cme_mcp_server.py. Missing one of them does not fail the build: the container
# dies on ModuleNotFoundError at start and restarts forever (restart:
# unless-stopped) — the manager then sees no CME agent at all.
COPY confluence_search.py .
COPY wiki_search.py .
# Fail the BUILD, not the first start, when a helper module is missing or
# broken: byte-compile every shipped module and resolve the helper imports.
RUN python -m py_compile cme_mcp_server.py cme_source_urls.py confluence_search.py wiki_search.py \
    && python -c "import cme_source_urls, confluence_search, wiki_search"

ENV CME_DATA_DIR=/data
ENV MCP_HOST=0.0.0.0
ENV MCP_PORT=8080
# confluence-markdown-exporter resolves its default app-config path via
# click's get_app_dir(), which falls back to $HOME/.config. When the
# container runs as an arbitrary non-root UID (no matching /etc/passwd
# entry, see docker-compose `user:`), Path.home() resolves to "/" and the
# mkdir fails with a permission error before the server can even start.
# XDG_CONFIG_HOME is get_app_dir()'s first-choice override and keeps this
# under the already-writable, already-mounted data directory.
ENV XDG_CONFIG_HOME=/data/.config

EXPOSE 8080

CMD ["python", "cme_mcp_server.py"]
