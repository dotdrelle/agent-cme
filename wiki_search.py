"""Live search over the workspace wiki — a read-only component of the same agent.

Searches the Markdown pages under ``workspaces/<workspace>/wiki/`` directly on
disk, token by token, case- and accent-insensitive, and returns the best
matches with a short excerpt around the first hit. No index is built, nothing
is written: each call is one walk of the wiki tree, which is why the agent
declares it synchronous and best-effort (local wikis are small; a large corpus
should use the wiki engine's own retrieval tools instead).
"""

import os
import re
import unicodedata
from pathlib import Path
from typing import Any

MAX_RESULTS = 30
MAX_EXCERPT_CHARS = 200
MAX_HEAD_BYTES = 256 * 1024
READ_ERRORS_QUIET = True


class WikiSearchError(Exception):
    """One stable, non-secret code per failure family."""


_ERROR_MESSAGES = {
    "no_query": "no_query: provide a non-empty query.",
    "no_wiki": "no_wiki: this workspace has no wiki directory yet.",
}


def _normalize(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(ch for ch in text if not unicodedata.combining(ch)).lower()


def _tokens(value: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", _normalize(value))


def _sanitize_prefix(value: str | None) -> str | None:
    prefix = str(value or "").strip().strip("/")
    if not prefix:
        return None
    if ".." in prefix.split("/") or prefix.startswith("/"):
        raise WikiSearchError("invalid_prefix")
    return prefix


def _first_heading(head: str) -> str | None:
    match = re.search(r"^#[ \t]+([^\r\n]+)", head, flags=re.MULTILINE)
    return match.group(1).strip() if match else None


def _excerpt(text: str, first_match: int) -> str:
    start = max(0, first_match - 60)
    window = text[start : start + MAX_EXCERPT_CHARS + 60]
    flat = re.sub(r"[#>*`_~\[\]|]+", " ", window)
    flat = re.sub(r"\]\([^)]*\)", " ", flat)
    flat = re.sub(r"!\[[^\]]*\]", " ", flat)
    return " ".join(flat.split())[:MAX_EXCERPT_CHARS]


def search_wiki(
    workspaces_root: Path,
    workspace: str,
    query: str,
    *,
    limit: int = 10,
    path_prefix: str | None = None,
) -> dict[str, Any]:
    """Search one workspace's wiki. Raises WikiSearchError on failure families."""
    query_text = str(query or "").strip()
    if not query_text:
        raise WikiSearchError("no_query")
    tokens = _tokens(query_text)
    if not tokens:
        raise WikiSearchError("no_query")
    prefix = _sanitize_prefix(path_prefix)

    wiki_dir = (Path(workspaces_root) / workspace / "wiki").resolve()
    try:
        wiki_dir.relative_to(Path(workspaces_root).resolve())
    except ValueError as exc:  # pragma: no cover - workspace validated upstream
        raise WikiSearchError("invalid_workspace") from exc
    if not wiki_dir.is_dir():
        raise WikiSearchError("no_wiki")

    matches: list[dict[str, Any]] = []
    for root, dirs, files in os.walk(wiki_dir):
        dirs[:] = [d for d in sorted(dirs) if not d.startswith(".")]
        for name in sorted(files):
            if not name.endswith(".md"):
                continue
            file_path = Path(root) / name
            relative = file_path.relative_to(wiki_dir).as_posix()
            if prefix and not relative.startswith(prefix):
                continue
            try:
                head = file_path.read_bytes()[:MAX_HEAD_BYTES].decode("utf-8", errors="ignore")
            except OSError:
                if not READ_ERRORS_QUIET:  # pragma: no cover
                    continue
                continue
            normalized = _normalize(head)
            score = 0
            first_match = -1
            for token in tokens:
                position = normalized.find(token)
                if position != -1:
                    score += 1
                    if first_match == -1 or position < first_match:
                        first_match = position
            if score == 0:
                continue
            matches.append(
                {
                    "path": f"wiki/{relative}",
                    "title": _first_heading(head) or relative.rsplit("/", 1)[-1][:-3],
                    "score": score,
                    "excerpt": _excerpt(head, first_match),
                }
            )

    matches.sort(key=lambda item: (-item["score"], item["path"]))
    capped = max(1, min(int(limit or 10), MAX_RESULTS))
    return {
        "ok": True,
        "query": query_text,
        "total": len(matches),
        "results": matches[:capped],
    }


def wiki_search_error_message(error: WikiSearchError) -> str:
    return _ERROR_MESSAGES.get(str(error), f"search failed: {error}")
