#!/usr/bin/env python3
"""Patch confluence-markdown-exporter to support personal space keys (~user@domain.com).

The upstream _CLOUD_URL_RE and _SERVER_URL_RE use narrow character classes
([A-Za-z0-9_~-]+ and [A-Za-z0-9._-]+) that reject '.' and '@' in space keys,
breaking personal spaces whose keys look like ~user.name@example.com.
"""
import glob
import pathlib
import sys

files = glob.glob(
    "/usr/local/lib/python*/site-packages/confluence_markdown_exporter/api_clients.py"
)
if not files:
    sys.exit("api_clients.py not found — check install path")

f = pathlib.Path(files[0])
c = f.read_text()
patched = c.replace(
    r"(?P<space_key>[A-Za-z0-9_~-]+)",
    r"(?P<space_key>[^/?#\s]+)",
).replace(
    r"(?P<space_key>[A-Za-z0-9._-]+)",
    r"(?P<space_key>[^/?#\s]+)",
)
if patched == c:
    sys.exit("Patterns not found — check CME version against Dockerfile pin")
f.write_text(patched)
print(f"Patched {files[0]}: personal space key support enabled")
