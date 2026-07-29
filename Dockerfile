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
