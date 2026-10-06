import json
import os
from pathlib import Path
import subprocess
import unittest


class TrelloMcpTests(unittest.TestCase):
    def run_server(self, messages, env=None):
        server = Path(__file__).resolve().parents[1] / "mcp" / "trello" / "server.mjs"
        completed = subprocess.run(
            ["node", str(server)],
            input="".join(json.dumps(message) + "\n" for message in messages),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=5,
            check=True,
            env={"PATH": os.environ.get("PATH", ""), **(env or {})},
        )
        return [json.loads(line) for line in completed.stdout.splitlines()]

    def test_discovers_curated_tools_and_fails_gracefully_without_runtime_config(self):
        responses = self.run_server(
            [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "trello_me", "arguments": {}}},
            ]
        )

        self.assertEqual(len(responses), 3)
        names = [tool["name"] for tool in responses[1]["result"]["tools"]]
        self.assertEqual(
            names,
            [
                "trello_me",
                "trello_check_updates",
                "trello_list_boards",
                "trello_list_lists",
                "trello_list_members",
                "trello_list_cards",
                "trello_search_cards",
                "trello_get_card",
                "trello_create_card",
                "trello_update_card",
                "trello_move_card",
                "trello_comment_card",
            ],
        )
        self.assertIn("webhook", responses[1]["result"]["tools"][1]["description"].lower())
        self.assertTrue(responses[2]["result"]["isError"])
        text = responses[2]["result"]["content"][0]["text"].lower()
        self.assertIn("mcp não configurado", text)
        self.assertNotIn("stack", text)

    def test_dockerfile_copies_trello_mcp_into_runtime_image(self):
        root = Path(__file__).resolve().parents[1]
        dockerfile = (root / "Dockerfile").read_text()
        self.assertIn("/opt/mag/trello-mcp", dockerfile)
        self.assertIn("mcp/trello/server.mjs /opt/mag/trello-mcp/server.mjs", dockerfile)


if __name__ == "__main__":
    unittest.main()
