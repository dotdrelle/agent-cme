# python:3.13-slim si confluence-markdown-exporter n'a pas encore de wheel 3.14
FROM python:3.14-slim

WORKDIR /app

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir \
    confluence-markdown-exporter \
    "mcp>=1.9.4" \
    starlette \
    uvicorn \
    pyyaml

COPY cme_mcp_server.py .

ENV CME_DATA_DIR=/data
ENV MCP_HOST=0.0.0.0
ENV MCP_PORT=8080

EXPOSE 8080

CMD ["python", "cme_mcp_server.py"]
