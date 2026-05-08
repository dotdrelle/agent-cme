FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    confluence-markdown-exporter \
    mcp \
    starlette \
    uvicorn \
    sse-starlette \
    pyyaml

COPY cme_mcp_server.py sources-manifest.yaml .

ENV CME_DATA_DIR=/data
ENV CME_CONFIG_PATH=/data/cme/app_data.json
ENV MCP_HOST=0.0.0.0
ENV MCP_PORT=8080

EXPOSE 8080

CMD ["python", "cme_mcp_server.py"]
