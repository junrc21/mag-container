import json
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
            env={},
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


if __name__ == "__main__":
    unittest.main()