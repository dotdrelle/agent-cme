import asyncio
import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest
from dataclasses import dataclass
from pathlib import Path


STORE = {}


@dataclass
class TextContent:
    type: str
    text: str


class Tool:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class Server:
    def __init__(self, *_args, **_kwargs):
        pass

    def list_tools(self):
        return lambda fn: fn

    def call_tool(self):
        return lambda fn: fn


class Settings:
    def __init__(self):
        self.export = types.SimpleNamespace(
            output_path=Path(tempfile.gettempdir()) / "cme-test-output",
            lockfile_name="export.lock.json",
        )

    def model_dump_json(self):
        return json.dumps(STORE)


def set_nested(path, value):
    current = STORE
    for key in path[:-1]:
        current = current.setdefault(key, {})
    current[path[-1]] = value


def install_stubs():
    modules = {
        "mcp": types.ModuleType("mcp"),
        "mcp.server": types.ModuleType("mcp.server"),
        "mcp.server.streamable_http_manager": types.ModuleType("mcp.server.streamable_http_manager"),
        "mcp.types": types.ModuleType("mcp.types"),
    }
    modules["mcp.server"].Server = Server
    modules["mcp.server.streamable_http_manager"].StreamableHTTPSessionManager = object
    modules["mcp.types"].TextContent = TextContent
    modules["mcp.types"].Tool = Tool
    sys.modules.update(modules)
    for name in [
        "starlette.applications",
        "starlette.middleware",
        "starlette.middleware.base",
        "starlette.middleware.cors",
        "starlette.requests",
        "starlette.responses",
        "starlette.routing",
        "starlette.types",
        "uvicorn",
    ]:
        sys.modules[name] = types.ModuleType(name)
    sys.modules["starlette.applications"].Starlette = object
    sys.modules["starlette.middleware"].Middleware = lambda *args, **kwargs: (args, kwargs)
    sys.modules["starlette.middleware.base"].BaseHTTPMiddleware = object
    sys.modules["starlette.middleware.cors"].CORSMiddleware = object
    sys.modules["starlette.requests"].Request = object
    sys.modules["starlette.responses"].HTMLResponse = object
    sys.modules["starlette.responses"].PlainTextResponse = object
    sys.modules["starlette.routing"].Mount = object
    sys.modules["starlette.types"].Receive = object
    sys.modules["starlette.types"].Scope = dict
    sys.modules["starlette.types"].Send = object
    sys.modules["uvicorn"].run = lambda *args, **kwargs: None

    exporter = types.ModuleType("confluence_markdown_exporter")
    utils = types.ModuleType("confluence_markdown_exporter.utils")
    app_data_store = types.ModuleType("confluence_markdown_exporter.utils.app_data_store")
    app_data_store.APP_CONFIG_PATH = None
    app_data_store.get_settings = lambda: Settings()
    app_data_store.set_setting = lambda key, value: set_nested(key.split("."), value)
    app_data_store.set_setting_with_keys = lambda keys, value: set_nested(list(keys), value)
    utils.app_data_store = app_data_store
    sys.modules.update({
        "confluence_markdown_exporter": exporter,
        "confluence_markdown_exporter.utils": utils,
        "confluence_markdown_exporter.utils.app_data_store": app_data_store,
    })


def load_module(workspaces_root, data_dir):
    install_stubs()
    sys.path.insert(0, str(Path(__file__).parent))
    os.environ["WORKSPACES_ROOT"] = str(workspaces_root)
    os.environ["CME_DATA_DIR"] = str(data_dir)
    path = Path(__file__).with_name("cme_mcp_server.py")
    spec = importlib.util.spec_from_file_location("cme_mcp_server_test_subject", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CmeMcpServerTest(unittest.TestCase):
    def setUp(self):
        STORE.clear()
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.workspaces = root / "workspaces"
        self.workspaces.mkdir()
        (self.workspaces / "demo").mkdir()
        self.server = load_module(self.workspaces, root / "data")

    def tearDown(self):
        self.tmp.cleanup()

    def test_setup_is_idempotent_and_status_redacts_auth(self):
        args = {
            "workspace": "demo",
            "base_url": "https://confluence.example",
            "username": "user@example.com",
            "pat": "secret-pat",
            "verify_ssl": False,
        }
        first = asyncio.run(self.server._tool_setup(args))[0].text
        second = asyncio.run(self.server._tool_setup(args))[0].text
        status = asyncio.run(self.server._tool_status({"workspace": "demo"}))[0].text

        self.assertIn("OK: CME configured", first)
        self.assertIn("OK: CME configured", second)
        self.assertIn("status: configured", status)
        self.assertIn("auth=pat", status)
        self.assertNotIn("secret-pat", status)

    def test_mask_helpers_redact_structured_and_text_secrets(self):
        masked = self.server._mask_secrets({"pat": "secret", "nested": {"password": "pw"}})
        self.assertEqual(masked["pat"], "***")
        self.assertEqual(masked["nested"]["password"], "***")

        text = self.server._mask_secret_text("Authorization: Bearer abc pat=secret api_token:tok123")
        self.assertNotIn("abc", text)
        self.assertNotIn("secret", text)
        self.assertNotIn("tok123", text)


if __name__ == "__main__":
    unittest.main()
