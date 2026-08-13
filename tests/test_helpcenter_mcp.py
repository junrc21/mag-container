import json
import os
from pathlib import Path
import subprocess
import unittest


class HelpCenterMcpTests(unittest.TestCase):
    """O MCP da central de ajuda, dirigido por stdio sem nenhuma env configurada.

    O que importa provar aqui: com `MAG_DOC_URL` ausente ele ainda descobre as ferramentas
    (para o agente saber que elas existem) e falha com uma frase humana em português, sem
    vazar rede, stack ou nome de variável para o canal do cliente.
    """

    def _run(self, messages):
        server = Path(__file__).resolve().parents[1] / "mcp" / "helpcenter" / "server.mjs"
        completed = subprocess.run(
            ["node", str(server)],
            input="".join(json.dumps(message) + "\n" for message in messages),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
            check=True,
            # Só o PATH: sem isto o `node` não é encontrado. Nenhuma MAG_* entra, que é o
            # ponto do teste — provar a degradação graciosa sem configuração.
            env={"PATH": os.environ.get("PATH", "")},
        )
        return [json.loads(line) for line in completed.stdout.splitlines()]

    def test_discovers_tools_and_fails_gracefully_without_config(self):
        responses = self._run(
            [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "search_help", "arguments": {"query": "conectar whatsapp"}},
                },
            ]
        )

        self.assertEqual(len(responses), 3)

        tools = responses[1]["result"]["tools"]
        self.assertEqual([tool["name"] for tool in tools], ["search_help", "read_help_page"])
        # A descrição é o que faz o agente usar a busca em vez de responder de cabeça.
        self.assertIn("SEMPRE que perguntarem como fazer algo", tools[0]["description"])
        self.assertIn("nunca invente etapas", tools[1]["description"])

        failure = responses[2]["result"]
        self.assertTrue(failure["isError"])
        text = failure["content"][0]["text"]
        self.assertIn("ajuda", text.lower())
        for leak in ("stack", "econnrefused", "fetch failed", "mag_doc_url", "undefined"):
            self.assertNotIn(leak, text.lower())

    def test_unknown_tool_is_reported_without_crashing(self):
        responses = self._run(
            [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "nao_existe", "arguments": {}}},
            ]
        )
        self.assertTrue(responses[1]["result"]["isError"])
        self.assertIn("desconhecida", responses[1]["result"]["content"][0]["text"])
