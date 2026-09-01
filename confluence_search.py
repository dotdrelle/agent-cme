"""Live Confluence search for agent-cme — a read-only component of the same agent.

Runs one real, bounded CQL search against the configured Confluence instance,
using the exact credentials and connection settings `cme_setup` stored for the
workspace (same auth shapes as the export path: PAT bearer or username+token
basic, `verify_ssl` from `connection_config`). It never exports, never caches
and never writes: each call is one HTTP request, results are returned as-is.
"""

import base64
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

MAX_LIMIT = 50
TIMEOUT_SECONDS = 15
MAX_EXCERPT_CHARS = 240

# Atlassian wraps matched terms with these markers; stripping them keeps the
# excerpt readable in a plain JSON response.
_HIGHLIGHT_MARKERS = ("@@@hl@@@", "@@@endhl@@@")


class ConfluenceSearchError(Exception):
    """One stable, non-secret code per failure family (see _ERROR_MESSAGES)."""


_ERROR_MESSAGES = {
    "not_configured": "not_configured: no Confluence instance for this workspace. Call cme_setup first.",
    "no_query": "no_query: provide either a free-text query or an explicit CQL expression.",
    "auth_failed": "auth_failed: Confluence rejected the stored credentials (HTTP 401/403).",
    "unreachable": "unreachable: the Confluence instance did not answer the search request.",
}


def _stored_instances(settings: Any) -> list[tuple[str, dict[str, Any]]]:
    data = json.loads(settings.model_dump_json())
    confluence = data.get("auth", {}).get("confluence", {})
    return [
        (url, creds)
        for url, creds in confluence.items()
        if isinstance(creds, dict) and (creds.get("pat") or creds.get("api_token"))
    ]


def _connection_flags(settings: Any) -> tuple[bool, bool]:
    data = json.loads(settings.model_dump_json())
    conn = data.get("connection_config", {})
    return bool(conn.get("verify_ssl", True)), bool(conn.get("use_v2_api", False))


def _authorization(creds: dict[str, Any]) -> str:
    if creds.get("pat"):
        return f"Bearer {creds['pat']}"
    pair = f"{creds.get('username', '')}:{creds.get('api_token', '')}".encode("utf-8")
    return "Basic " + base64.b64encode(pair).decode("ascii")


def _ssl_context(verify_ssl: bool) -> ssl.SSLContext | None:
    if verify_ssl:
        return None
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def _escape_cql(value: str) -> str:
    return str(value or "").replace("\\", "\\\\").replace('"', '\\"')


def _space_key(value: str | None) -> str | None:
    key = str(value or "").strip()
    if not key:
        return None
    if not key.replace("_", "a").replace("-", "a").isalnum():
        raise ConfluenceSearchError("invalid_space")
    return key


def _content_url(base: str, content: dict[str, Any]) -> str | None:
    links = content.get("_links") or {}
    base_url = links.get("base")
    webui = links.get("webui")
    if base_url and webui:
        return f"{base_url}{webui}"
    if content.get("id"):
        return f"{base}/pages/viewpage.action?pageId={content['id']}"
    return None


def _excerpt(value: Any) -> str:
    text = str(value or "")
    for marker in _HIGHLIGHT_MARKERS:
        text = text.replace(marker, "")
    return " ".join(text.split())[:MAX_EXCERPT_CHARS]


def search_confluence(
    settings: Any,
    *,
    query: str | None = None,
    cql: str | None = None,
    limit: int = 10,
    space_key: str | None = None,
) -> dict[str, Any]:
    """One live search. Raises ConfluenceSearchError on any failure family."""
    query_text = str(query or "").strip()
    cql_text = str(cql or "").strip()
    if not cql_text and not query_text:
        raise ConfluenceSearchError("no_query")
    instances = _stored_instances(settings)
    if not instances:
        raise ConfluenceSearchError("not_configured")
    base_url, creds = instances[0]
    verify_ssl, _use_v2 = _connection_flags(settings)
    base = base_url.rstrip("/")

    expression = cql_text if cql_text else f'text ~ "{_escape_cql(query_text)}"'
    space = _space_key(space_key)
    if space:
        expression = f'{expression} and space = "{space}"'

    capped = max(1, min(int(limit or 10), MAX_LIMIT))
    url = f"{base}/rest/api/search?{urllib.parse.urlencode({'cql': expression, 'limit': capped, 'start': 0})}"
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "Authorization": _authorization(creds)},
    )
    try:
        with urllib.request.urlopen(
            request, timeout=TIMEOUT_SECONDS, context=_ssl_context(verify_ssl)
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            raise ConfluenceSearchError("auth_failed") from error
        raise ConfluenceSearchError("unreachable") from error
    except Exception as error:  # noqa: BLE001 - diagnostic, never silent
        raise ConfluenceSearchError("unreachable") from error

    results = []
    for entry in payload.get("results", [])[:capped]:
        content = entry.get("content") or {}
        results.append(
            {
                "id": content.get("id"),
                "title": content.get("title"),
                "type": content.get("type"),
                "space": (content.get("space") or {}).get("key"),
                "url": _content_url(base, content),
                "excerpt": _excerpt(entry.get("excerpt")),
            }
        )
    return {
        "ok": True,
        "query": query_text or None,
        "cql": expression,
        "total": int(payload.get("totalSize", len(results))),
        "results": results,
    }


def search_error_message(error: ConfluenceSearchError) -> str:
    return _ERROR_MESSAGES.get(str(error), f"search failed: {error}")
