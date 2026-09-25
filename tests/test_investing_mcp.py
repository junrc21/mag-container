import json
import os
from pathlib import Path
import subprocess
import unittest


class InvestingMcpTests(unittest.TestCase):
    def test_discovers_curated_tools_and_fails_gracefully_without_runtime_config(self):
        server = Path(__file__).resolve().parents[1] / "mcp" / "investing" / "server.mjs"
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "resolve_issuer", "arguments": {"query": "Petrobras", "limit": 5}},
            },
        ]
        completed = subprocess.run(
            ["node", str(server)],
            input="".join(json.dumps(message) + "\n" for message in messages),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=5,
            check=True,
            # `env={}` limpava o PATH junto e o `node` não era encontrado — a suíte não
            # rodava localmente. O que o teste precisa é da ausência das MAG_*, não do PATH.
            env={"PATH": os.environ.get("PATH", "")},
        )
        responses = [json.loads(line) for line in completed.stdout.splitlines()]
        self.assertEqual(len(responses), 3)
        tools = responses[1]["result"]["tools"]
        self.assertEqual([tool["name"] for tool in tools], ["resolve_issuer", "get_regulatory_report", "get_notifications"])
        self.assertIn("get_regulatory_report", tools[0]["description"])
        self.assertIn("sourceUrl", tools[1]["description"])
        self.assertIn("Não fornece cotação", tools[1]["description"])
        self.assertIn("sourceUrl", tools[2]["description"])
        self.assertTrue(responses[2]["result"]["isError"])
        self.assertNotIn("stack", responses[2]["result"]["content"][0]["text"].lower())


class InvestingB3Tests(unittest.TestCase):
    def call(self, message, b3=True, url="http://127.0.0.1:1"):
        server = Path(__file__).resolve().parents[1] / "mcp" / "investing" / "server.mjs"
        env = {"PATH": os.environ.get("PATH", ""), "INVESTING_B3_ENABLED": str(b3).lower(), "MAG_API_URL": url, "MAG_TENANT_ID": "tenant-fixed", "MAG_INVESTING_RUNTIME_TOKEN": "runtime-test-token"}
        run = subprocess.run(["node", str(server)], input=json.dumps(message) + "\n", capture_output=True, text=True, env=env, timeout=5, check=True)
        return json.loads(run.stdout)

    def test_market_tools_are_discovered_only_when_enabled(self):
        message = {"id": 1, "method": "tools/list"}
        names = [t["name"] for t in self.call(message)["result"]["tools"]]
        self.assertEqual(len(names), 8)
        self.assertIn("get_asset_history", names)
        self.assertEqual(len(self.call(message, b3=False)["result"]["tools"]), 3)

    def test_rejects_tenant_override_and_hidden_tool(self):
        message = {"id": 1, "method": "tools/call", "params": {"name": "get_asset", "arguments": {"asset": "PETR4", "tenantId": "other"}}}
        self.assertTrue(self.call(message)["result"]["isError"])
        self.assertIn("error", self.call(message, b3=False))

    def test_http_forwarding_preserves_structured_data_and_identity(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from threading import Thread
        received = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                received.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"]))), self.headers.get("x-investing-runtime-token")))
                response = json.dumps({"data": {"prices": [{"close": "48.42"}], "warnings": ["historical"], "source": {"realtime": False}}, "isError": False}).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(response)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            result = self.call({"id": 1, "method": "tools/call", "params": {"name": "get_asset_history", "arguments": {"asset": "PETR4", "from": "2026-09-01", "to": "2026-09-09"}}}, url=f"http://127.0.0.1:{server.server_port}")["result"]
            self.assertFalse(result["isError"])
            self.assertEqual(result["structuredContent"]["result"]["prices"][0]["close"], "48.42")
            self.assertEqual(received[0][0], "/internal/investing/asset-history")
            self.assertEqual(received[0][1]["tenantId"], "tenant-fixed")
            self.assertEqual(received[0][2], "runtime-test-token")
        finally:
            server.shutdown(); server.server_close(); thread.join()


if __name__ == "__main__":
    unittest.main()