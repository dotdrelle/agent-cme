import asyncio
import contextlib
import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest
import urllib.error
from dataclasses import dataclass
from pathlib import Path
from unittest import mock


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

    def persist():
        # Mirror the real app_data_store: set_setting writes the config file
        # (creating missing parent dirs), so tests can assert disk behaviour
        # such as "a missing config is recreated by cme_setup".
        path = app_data_store.APP_CONFIG_PATH
        if path:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text(json.dumps(STORE), encoding="utf-8")

    app_data_store.get_settings = lambda: Settings()
    app_data_store.set_setting = lambda key, value: (set_nested(key.split("."), value), persist())[1]
    app_data_store.set_setting_with_keys = lambda keys, value: (set_nested(list(keys), value), persist())[1]
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

    def test_test_connection_reports_not_configured_when_no_instance(self):
        text = asyncio.run(self.server._tool_test_connection({"workspace": "demo"}))[0].text
        self.assertIn("not_configured", text)

    def test_test_connection_reports_ok_and_redacts_secret(self):
        asyncio.run(self.server._tool_setup({
            "workspace": "demo",
            "base_url": "https://confluence.example",
            "username": "user@example.com",
            "pat": "secret-pat",
        }))
        with mock.patch("urllib.request.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value.status = 200
            text = asyncio.run(self.server._tool_test_connection({"workspace": "demo"}))[0].text
        self.assertIn("ok (HTTP 200)", text)
        self.assertNotIn("secret-pat", text)

    def test_test_connection_reports_auth_failure(self):
        asyncio.run(self.server._tool_setup({
            "workspace": "demo",
            "base_url": "https://confluence.example",
            "username": "user@example.com",
            "pat": "secret-pat",
        }))
        error = urllib.error.HTTPError("https://confluence.example/rest/api/space?limit=1", 401, "Unauthorized", {}, None)
        with mock.patch("urllib.request.urlopen", side_effect=error):
            text = asyncio.run(self.server._tool_test_connection({"workspace": "demo"}))[0].text
        self.assertIn("auth failed", text)

    def test_mask_helpers_redact_structured_and_text_secrets(self):
        masked = self.server._mask_secrets({"pat": "secret", "nested": {"password": "pw"}})
        self.assertEqual(masked["pat"], "***")
        self.assertEqual(masked["nested"]["password"], "***")

        text = self.server._mask_secret_text("Authorization: Bearer abc pat=secret api_token:tok123")
        self.assertNotIn("abc", text)
        self.assertNotIn("secret", text)
        self.assertNotIn("tok123", text)

    def test_read_scope_cannot_call_mutating_tool(self):
        token = self.server._CURRENT_SCOPES.set({"read"})
        try:
            denied_agent = self.server._require_tool_scope("agent_execute")
            denied = self.server._require_tool_scope("cme_export_run")
            allowed = self.server._require_tool_scope("cme_status")
        finally:
            self.server._CURRENT_SCOPES.reset(token)

        self.assertIsNotNone(denied_agent)
        self.assertIsNotNone(denied)
        self.assertIn("write scope", denied[0].text)
        self.assertIsNone(allowed)

    def test_agent_describe_exposes_generic_cme_contract(self):
        description = json.loads(self.server._tool_agent_describe()[0].text)

        self.assertEqual(description["contractVersion"], "1")
        self.assertEqual(description["agentType"], "cme")
        self.assertEqual(description["agentInstanceId"], "cme-main")
        self.assertEqual(description["orchestration"]["canPlan"], False)
        self.assertEqual(description["orchestration"]["canExecute"], True)
        self.assertEqual(description["orchestration"]["singleTaskOnly"], True)
        self.assertEqual(description["orchestration"]["supportsIdempotency"], True)
        self.assertEqual(description["capabilities"][0]["id"], "external-source.export")
        self.assertEqual(description["capabilities"][0]["version"], "1")
        self.assertEqual(description["capabilities"][0]["supportedOperations"], ["export"])
        self.assertEqual(description["capabilities"][0]["defaultRequiresApproval"], True)
        self.assertNotIn("operation", description["capabilities"][0]["inputSchema"].get("required", []))

    def test_capability_schema_tells_the_planner_what_source_name_means(self):
        # A bare {"type": "string"} is what led a planner to pass the WORKSPACE
        # name as a source name. The contract must say the field is optional,
        # must match a declared source, and is not a workspace name.
        description = json.loads(self.server._tool_agent_describe()[0].text)
        schema = description["capabilities"][0]["inputSchema"]

        self.assertNotIn("source_name", schema.get("required", []))
        text = schema["properties"]["source_name"]["description"].lower()
        self.assertIn("omit", text)
        self.assertIn("not a workspace name", text)

    def test_describe_publishes_the_declared_sources_as_a_closed_vocabulary(self):
        asyncio.run(self.server._tool_setup({
            "workspace": "demo",
            "base_url": "http://confluence.example",
            "username": "user@example.com",
            "pat": "token",
        }))
        asyncio.run(self.server._tool_source_add({
            "workspace": "demo",
            "name": "EAS_Avant_projet_ACPI",
            "type": "page-with-descendants",
            "url": "http://confluence.example/spaces/ODG/pages/1/EAS",
        }))

        described = json.loads(self.server._tool_agent_describe({"workspace": "demo"})[0].text)
        schema = described["capabilities"][0]["inputSchema"]["properties"]["source_name"]

        # Without the enum, a planner fills this field with any noun from the
        # objective ("acpi", "Confluence") and the task fails.
        self.assertEqual(schema["enum"], ["EAS_Avant_projet_ACPI"])

    def test_describe_without_a_workspace_publishes_no_vocabulary(self):
        described = json.loads(self.server._tool_agent_describe()[0].text)
        schema = described["capabilities"][0]["inputSchema"]["properties"]["source_name"]

        # Generic probe or older manager: degrade to a plain string, never fail.
        self.assertNotIn("enum", schema)
        self.assertIn("description", schema)

    def test_export_run_rejects_an_unknown_source_by_naming_the_declared_ones(self):
        args = {
            "workspace": "demo",
            "base_url": "http://confluence.example",
            "username": "user@example.com",
            "pat": "token",
        }
        asyncio.run(self.server._tool_setup(args))
        asyncio.run(self.server._tool_source_add({
            "workspace": "demo",
            "name": "EAS_Avant_projet_ACPI",
            "type": "page-with-descendants",
            "url": "http://confluence.example/spaces/ODG/pages/1/EAS",
        }))

        result = asyncio.run(self.server._tool_export_run({
            "workspace": "demo",
            "source_name": "demo",
        }))[0].text

        self.assertIn("not found", result)
        # The reader must be able to act on this without a second round-trip.
        self.assertIn("EAS_Avant_projet_ACPI", result)
        self.assertIn("Omit source_name", result)

    def test_agent_execute_is_idempotent_for_same_key(self):
        calls = 0

        async def fake_export_run(args):
            nonlocal calls
            calls += 1
            job_id = f"job-{calls}"
            now = self.server._now()
            workspace_path = self.workspaces / args["workspace"]
            output_path = workspace_path / "raw" / "untracked"
            self.server._jobs[job_id] = {
                "status": "success",
                "workspace": args["workspace"],
                "workspace_path": str(workspace_path),
                "output_path": str(output_path),
                "sources": [args.get("source_name") or "all"],
                "started_at": now,
                "finished_at": now,
                "stdout": [],
                "stderr": [],
            }
            return [self.server._json_content({"ok": True, "job_id": job_id, "status": "success"})]

        self.server._tool_export_run = fake_export_run
        request = {
            "taskId": "task-cme",
            "idempotencyKey": "idem-cme",
            "operation": "export",
            "workspace": {"name": "demo", "revision": "rev-1"},
            "arguments": {"source_name": "docs"},
        }

        first = json.loads(asyncio.run(self.server._tool_agent_execute(request))[0].text)
        second = json.loads(asyncio.run(self.server._tool_agent_execute(request))[0].text)

        self.assertEqual(calls, 1)
        self.assertEqual(first["accepted"], True)
        self.assertEqual(first["idempotent"], False)
        self.assertEqual(second["accepted"], True)
        self.assertEqual(second["idempotent"], True)
        self.assertEqual(second["jobId"], first["jobId"])
        self.assertEqual(second["terminal"], True)
        self.assertEqual(second["result"]["status"], "succeeded")

    def test_agent_status_and_cancel_wrap_export_job(self):
        now = self.server._now()
        job_id = "job-running"
        self.server._jobs[job_id] = {
            "status": "running",
            "workspace": "demo",
            "workspace_path": str(self.workspaces / "demo"),
            "output_path": str(self.workspaces / "demo" / "raw" / "untracked"),
            "sources": ["docs"],
            "started_at": now,
            "stdout": ["line"],
            "stderr": [],
            "task_id": "task-cme",
        }

        async def fake_cancel(args):
            self.server._jobs[args["job_id"]]["status"] = "cancelled"
            self.server._jobs[args["job_id"]]["finished_at"] = self.server._now()
            return [self.server.TextContent(type="text", text="cancelled")]

        self.server._tool_export_cancel = fake_cancel
        status = json.loads(self.server._tool_agent_status({"jobId": job_id})[0].text)
        cancelled = json.loads(asyncio.run(self.server._tool_agent_cancel({"jobId": job_id}))[0].text)

        self.assertEqual(status["status"], "running")
        self.assertEqual(status["taskId"], "task-cme")
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(cancelled["result"]["status"], "cancelled")

    def test_confluence_search_requires_configuration(self):
        text = asyncio.run(self.server._tool_confluence_search({
            "workspace": "demo", "query": "architecture",
        }))[0].text
        self.assertIn('"ok": false', text)
        self.assertIn("not_configured", text)

    def test_confluence_search_builds_cql_and_parses_results(self):
        asyncio.run(self.server._tool_setup({
            "workspace": "demo",
            "base_url": "https://confluence.example",
            "username": "user@example.com",
            "pat": "secret-pat",
        }))
        captured = {}

        def fake_urlopen(request, **kwargs):
            captured["url"] = request.full_url
            captured["headers"] = dict(request.headers)

            class FakeResponse:
                status = 200

                def __enter__(self):
                    return self

                def __exit__(self, *exc):
                    return False

                def read(self):
                    return json.dumps({
                        "totalSize": 1,
                        "results": [{
                            "content": {
                                "id": "12345",
                                "title": "Network architecture",
                                "type": "page",
                                "space": {"key": "DEV"},
                                "_links": {"base": "https://confluence.example", "webui": "/display/DEV/Architecture"},
                            },
                            "excerpt": "an overview of the @@@hl@@@network@@@endhl@@@ on the server side",
                        }],
                    }).encode("utf-8")

            return FakeResponse()

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            text = asyncio.run(self.server._tool_confluence_search({
                "workspace": "demo", "query": "network servers",
            }))[0].text
        payload = json.loads(text)
        self.assertTrue(payload["ok"])
        self.assertIn('cql=text+~+%22network+servers%22', captured["url"])
        self.assertEqual(captured["headers"].get("Authorization"), "Bearer secret-pat")
        self.assertEqual(payload["instance"], "https://confluence.example")
        self.assertEqual(payload["results"][0]["title"], "Network architecture")
        self.assertEqual(payload["results"][0]["space"], "DEV")
        self.assertEqual(
            payload["results"][0]["url"],
            "https://confluence.example/display/DEV/Architecture",
        )
        self.assertNotIn("@@@hl@@@", payload["results"][0]["excerpt"])
        self.assertNotIn("secret-pat", text)

    def test_confluence_search_reports_auth_failure(self):
        asyncio.run(self.server._tool_setup({
            "workspace": "demo",
            "base_url": "https://confluence.example",
            "username": "user@example.com",
            "pat": "secret-pat",
        }))
        error = urllib.error.HTTPError("https://confluence.example/rest/api/search", 401, "Unauthorized", {}, None)
        with mock.patch("urllib.request.urlopen", side_effect=error):
            text = asyncio.run(self.server._tool_confluence_search({
                "workspace": "demo", "query": "architecture",
            }))[0].text
        self.assertIn("auth_failed", text)

    def test_confluence_search_uses_the_workspace_source_instance(self):
        asyncio.run(self.server._tool_setup({
            "base_url": "https://alpha.example",
            "username": "user@example.com",
            "pat": "pat-alpha",
        }))
        asyncio.run(self.server._tool_setup({
            "base_url": "https://beta.example",
            "username": "user@example.com",
            "pat": "pat-beta",
        }))
        asyncio.run(self.server._tool_source_add({
            "workspace": "demo",
            "name": "alpha-docs",
            "type": "space",
            "base_url": "https://alpha.example",
            "space": "DEV",
        }))
        captured = {}

        def fake_urlopen(request, **kwargs):
            captured["url"] = request.full_url
            captured["headers"] = dict(request.headers)

            class FakeResponse:
                status = 200

                def __enter__(self):
                    return self

                def __exit__(self, *exc):
                    return False

                def read(self):
                    return json.dumps({"totalSize": 0, "results": []}).encode("utf-8")

            return FakeResponse()

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            text = asyncio.run(self.server._tool_confluence_search({
                "workspace": "demo", "query": "anything",
            }))[0].text
        payload = json.loads(text)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["instance"], "https://alpha.example")
        self.assertTrue(captured["url"].startswith("https://alpha.example/"))
        self.assertEqual(captured["headers"].get("Authorization"), "Bearer pat-alpha")

    def test_confluence_search_names_the_instance_that_lacks_credentials(self):
        # One instance IS configured — the workspace's source just points at a
        # different one. That must be announced by name, not folded into a
        # generic "not configured".
        asyncio.run(self.server._tool_setup({
            "base_url": "https://alpha.example",
            "username": "user@example.com",
            "pat": "pat-alpha",
        }))
        asyncio.run(self.server._tool_source_add({
            "workspace": "demo",
            "name": "beta-docs",
            "type": "space",
            "base_url": "https://beta.example",
            "space": "DEV",
        }))
        text = asyncio.run(self.server._tool_confluence_search({
            "workspace": "demo", "query": "anything",
        }))[0].text
        payload = json.loads(text)
        self.assertFalse(payload["ok"])
        self.assertIn("instance_not_configured", payload["error"])
        self.assertEqual(payload["instance"], "https://beta.example/display/DEV")

    def test_wiki_search_finds_pages_and_reads_their_heading(self):
        wiki = self.workspaces / "demo" / "wiki" / "concepts"
        wiki.mkdir(parents=True)
        (wiki / "reseau.md").write_text(
            "# Network architecture\n\nDigital sovereignty of the internal servers.\n", encoding="utf-8"
        )
        (wiki / "budget.md").write_text("# Budget\n\nYearly forecast.\n", encoding="utf-8")

        text = asyncio.run(self.server._tool_wiki_search({
            "workspace": "demo", "query": "digital sovereignty",
        }))[0].text
        payload = json.loads(text)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["results"][0]["path"], "wiki/concepts/reseau.md")
        self.assertEqual(payload["results"][0]["title"], "Network architecture")
        self.assertIn("sovereignty", payload["results"][0]["excerpt"].lower())

    def test_wiki_search_rejects_path_prefix_traversal(self):
        text = asyncio.run(self.server._tool_wiki_search({
            "workspace": "demo", "query": "anything", "path_prefix": "../agents-data",
        }))[0].text
        self.assertIn("invalid_prefix", text)

    def test_wiki_search_reports_a_workspace_without_wiki(self):
        text = asyncio.run(self.server._tool_wiki_search({
            "workspace": "demo", "query": "anything",
        }))[0].text
        self.assertIn("no_wiki", text)

    def test_search_tools_stay_read_only(self):
        token = self.server._CURRENT_SCOPES.set({"read"})
        try:
            self.assertIsNone(self.server._require_tool_scope("cme_confluence_search"))
            self.assertIsNone(self.server._require_tool_scope("cme_wiki_search"))
        finally:
            self.server._CURRENT_SCOPES.reset(token)

    # ------------------------------------------------------------------
    # Agent-wide (shared) configuration
    # ------------------------------------------------------------------

    def test_setup_stores_credentials_agent_wide_and_workspace_is_optional(self):
        first = asyncio.run(self.server._tool_setup({
            "base_url": "https://confluence.example",
            "username": "user@example.com",
            "pat": "secret-pat",
        }))[0].text
        # The manager injects workspace on every call — it must stay accepted
        # but must NOT scope the write.
        second = asyncio.run(self.server._tool_setup({
            "workspace": "demo",
            "base_url": "https://other.example",
            "username": "user@example.com",
            "pat": "secret-pat-2",
        }))[0].text
        status = asyncio.run(self.server._tool_status({"workspace": "demo"}))[0].text

        self.assertIn("OK: CME configured", first)
        self.assertIn("OK: CME configured", second)
        self.assertIn("shared across all workspaces", first)
        self.assertIn("status: configured", status)
        self.assertIn("https://confluence.example", status)
        self.assertIn("https://other.example", status)
        self.assertIn("auth=pat", status)
        self.assertNotIn("secret-pat", status)

    def test_status_reports_the_shared_config_path(self):
        asyncio.run(self.server._tool_setup({
            "workspace": "demo",
            "base_url": "https://confluence.example",
            "username": "user@example.com",
            "pat": "secret-pat",
        }))
        status = asyncio.run(self.server._tool_status({"workspace": "demo"}))[0].text
        self.assertIn("shared across all workspaces", status)
        self.assertIn(str(self.server._global_cme_config()), status)

    def test_export_run_refuses_sources_without_stored_credentials(self):
        asyncio.run(self.server._tool_source_add({
            "workspace": "demo",
            "name": "dev-docs",
            "type": "space",
            "base_url": "https://confluence.example",
            "space": "DEV",
        }))
        result = asyncio.run(self.server._tool_export_run({"workspace": "demo"}))[0].text
        self.assertIn("no stored Confluence credentials", result)
        self.assertIn("dev-docs", result)
        self.assertIn("https://confluence.example", result)

    def test_export_run_preflight_passes_with_shared_credentials(self):
        asyncio.run(self.server._tool_setup({
            "base_url": "https://confluence.example",
            "username": "user@example.com",
            "pat": "secret-pat",
        }))
        asyncio.run(self.server._tool_source_add({
            "workspace": "demo",
            "name": "dev-docs",
            "type": "space",
            "base_url": "https://confluence.example",
            "space": "DEV",
        }))
        # Never spawn the real CME binary: the job must fail its first step.
        self.server._CME_BIN = "/nonexistent/cme-bin"

        async def _run_and_stop():
            result = await self.server._tool_export_run({"workspace": "demo"})
            job_id = json.loads(result[0].text)["job_id"]
            job = self.server._jobs[job_id]
            if job.get("task") and not job["task"].done():
                job["task"].cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await job["task"]
            return result, job_id

        result, job_id = asyncio.run(_run_and_stop())
        payload = json.loads(result[0].text)
        self.assertTrue(payload["ok"])
        self.assertEqual(self.server._jobs[job_id]["config_path"], str(self.server._global_cme_config()))

    def test_migration_merges_legacy_workspace_configs_into_the_shared_one(self):
        legacy_a = self.server._DATA_DIR / "old-a" / "cme" / "app_data.json"
        legacy_a.parent.mkdir(parents=True)
        legacy_a.write_text(json.dumps({
            "auth": {"confluence": {"https://a.example": {"username": "u", "pat": "pat-a"}}},
            "connection_config": {"verify_ssl": False},
            "export": {"attachments_export": "referenced"},
        }), encoding="utf-8")
        legacy_b = self.server._DATA_DIR / "old-b" / "cme" / "app_data.json"
        legacy_b.parent.mkdir(parents=True)
        legacy_b.write_text(json.dumps({
            "auth": {"confluence": {"https://b.example": {"username": "u", "api_token": "tok-b"}}},
        }), encoding="utf-8")

        notices = self.server._migrate_legacy_workspace_configs()

        shared = json.loads((self.server._DATA_DIR / "app_data.json").read_text(encoding="utf-8"))
        self.assertEqual(shared["auth"]["confluence"]["https://a.example"]["pat"], "pat-a")
        self.assertEqual(shared["auth"]["confluence"]["https://b.example"]["api_token"], "tok-b")
        self.assertFalse(shared["connection_config"]["verify_ssl"])
        self.assertEqual(shared["export"]["attachments_export"], "referenced")
        self.assertFalse(legacy_a.exists())
        self.assertTrue(legacy_a.with_name("app_data.json.migrated").exists())
        self.assertTrue(any("old-a" in n for n in notices))
        self.assertTrue(any("old-b" in n for n in notices))

    def test_migration_adopts_the_former_shared_cme_location(self):
        # The intermediate pre-release layout stored the shared config at
        # /data/cme/app_data.json. Its credentials must be adopted, not
        # orphaned, when the shared config moves to /data/app_data.json.
        former = self.server._DATA_DIR / "cme" / "app_data.json"
        former.parent.mkdir(parents=True)
        former.write_text(json.dumps({
            "auth": {"confluence": {"https://x.example": {"username": "u", "pat": "pat-x"}}},
        }), encoding="utf-8")

        notices = self.server._migrate_legacy_workspace_configs()

        shared = json.loads((self.server._DATA_DIR / "app_data.json").read_text(encoding="utf-8"))
        self.assertEqual(shared["auth"]["confluence"]["https://x.example"]["pat"], "pat-x")
        self.assertTrue(former.with_name("app_data.json.migrated").exists())
        self.assertTrue(any("cme" in n for n in notices))

    def test_status_and_setup_survive_a_missing_data_dir(self):
        # A fresh deployment (or an operator who cleared the state dir) has no
        # data directory at all: reads degrade to not_configured and the first
        # cme_setup recreates the agent-wide config, parent dirs included.
        self.assertFalse(self.server._DATA_DIR.exists())
        status = asyncio.run(self.server._tool_status({"workspace": "demo"}))[0].text
        self.assertIn("not_configured", status)
        self.assertIn("action_required: call cme_setup", status)

        asyncio.run(self.server._tool_setup({
            "base_url": "https://confluence.example",
            "username": "user@example.com",
            "pat": "secret-pat",
        }))
        self.assertTrue((self.server._DATA_DIR / "app_data.json").is_file())

    def test_sources_list_recreates_a_missing_manifest(self):
        # The per-workspace sources manifest is runtime state: when it is gone
        # (fresh workspace, cleared state), a read recreates the empty file
        # instead of failing or inventing sources.
        manifest_path = self.server._workspace_manifest("demo")
        self.assertFalse(manifest_path.exists())
        text = asyncio.run(self.server._tool_sources_list({"workspace": "demo"}))[0].text
        self.assertIn("sources: []", text)
        self.assertTrue(manifest_path.is_file())

    def test_migrated_files_are_not_remerged_when_the_shared_config_is_deleted(self):
        # Deleting the shared config must NOT resurrect credentials from the
        # .migrated legacy files: those were consumed once, and the absence of
        # the shared config is a state the status announces (not_configured),
        # never one migration silently rebuilds.
        legacy_a = self.server._DATA_DIR / "old-a" / "cme" / "app_data.json"
        legacy_a.parent.mkdir(parents=True)
        legacy_a.write_text(json.dumps({
            "auth": {"confluence": {"https://a.example": {"username": "u", "pat": "pat-a"}}},
        }), encoding="utf-8")
        self.server._migrate_legacy_workspace_configs()
        shared = self.server._DATA_DIR / "app_data.json"
        self.assertTrue(shared.is_file())
        shared.unlink()

        notices = self.server._migrate_legacy_workspace_configs()
        status = asyncio.run(self.server._tool_status({"workspace": "demo"}))[0].text

        self.assertEqual(notices, [])
        self.assertFalse(shared.exists())
        self.assertIn("not_configured", status)

    def test_migration_keeps_existing_credentials_and_announces_conflicts(self):
        legacy_a = self.server._DATA_DIR / "old-a" / "cme" / "app_data.json"
        legacy_a.parent.mkdir(parents=True)
        legacy_a.write_text(json.dumps({
            "auth": {"confluence": {"https://a.example": {"username": "u", "pat": "pat-a"}}},
        }), encoding="utf-8")
        self.server._migrate_legacy_workspace_configs()

        legacy_b = self.server._DATA_DIR / "old-b" / "cme" / "app_data.json"
        legacy_b.parent.mkdir(parents=True)
        legacy_b.write_text(json.dumps({
            "auth": {"confluence": {"https://a.example": {"username": "u", "pat": "pat-different"}}},
        }), encoding="utf-8")
        legacy_c = self.server._DATA_DIR / "old-c" / "cme" / "app_data.json"
        legacy_c.parent.mkdir(parents=True)
        legacy_c.write_text(json.dumps({
            "auth": {"confluence": {"https://a.example": {"username": "u", "pat": "pat-a"}}},
        }), encoding="utf-8")

        notices = self.server._migrate_legacy_workspace_configs()

        shared = json.loads((self.server._DATA_DIR / "app_data.json").read_text(encoding="utf-8"))
        self.assertEqual(shared["auth"]["confluence"]["https://a.example"]["pat"], "pat-a")
        self.assertTrue(any("DIFFERENT credentials" in n for n in notices))
        self.assertTrue(any("identical credentials" in n for n in notices))
        self.assertTrue(legacy_b.with_name("app_data.json.migrated").exists())
        self.assertTrue(legacy_c.with_name("app_data.json.migrated").exists())


if __name__ == "__main__":
    unittest.main()
