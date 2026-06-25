"""Normalize Confluence source URLs accepted by the CME MCP adapter."""

import re
from urllib.parse import parse_qs, unquote, urlsplit, urlunsplit


_MARKDOWN_LINK_RE = re.compile(r"\[[^\]]*]\((?P<url>https?://[^)]+)\)")
_SPACE_PATH_RE = re.compile(
    r"^(?P<prefix>.*?)/(?:spaces)/(?P<space>[^/]+)"
    r"(?:/pages/(?P<page_id>\d+)(?:/[^?#]*)?)?/?$",
)
_DISPLAY_PATH_RE = re.compile(
    r"^(?P<prefix>.*?)/display/(?P<space>[^/]+)(?P<title>/[^?#]+)?/?$",
)


def extract_confluence_url(value: str) -> str:
    """Return the URL from either a raw URL or one Markdown link."""
    raw = str(value or "").strip()
    match = _MARKDOWN_LINK_RE.search(raw)
    if match:
        raw = match.group("url").strip()
        if raw.startswith("<") and raw.endswith(">"):
            raw = raw[1:-1].strip()
    return raw


def parse_confluence_source_url(value: str) -> dict[str, str]:
    """Classify a Confluence URL and return manifest-ready source fields."""
    url = extract_confluence_url(value)
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Confluence source must be an absolute HTTP(S) URL")

    origin = urlunsplit((parsed.scheme, parsed.netloc, "", "", "")).rstrip("/")
    query = {key.lower(): values for key, values in parse_qs(parsed.query).items()}
    if any(value.isdigit() for value in query.get("pageid", [])):
        return {"type": "page", "url": url}

    if match := _SPACE_PATH_RE.match(parsed.path.rstrip("/")):
        space = unquote(match.group("space"))
        if match.group("page_id"):
            return {"type": "page", "url": url}
        prefix = match.group("prefix").rstrip("/")
        return {
            "type": "space",
            "base_url": f"{origin}{prefix}",
            "space": space,
        }

    if match := _DISPLAY_PATH_RE.match(parsed.path.rstrip("/")):
        space = unquote(match.group("space"))
        if match.group("title"):
            return {"type": "page", "url": url}
        prefix = match.group("prefix").rstrip("/")
        return {
            "type": "space",
            "base_url": f"{origin}{prefix}",
            "space": space,
        }

    raise ValueError(
        "Unsupported Confluence URL. Expected /spaces/<key>, "
        "/spaces/<key>/pages/<id>, /display/<key>, or a URL with pageId."
    )
