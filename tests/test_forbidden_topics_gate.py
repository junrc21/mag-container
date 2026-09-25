import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


PATCH = Path(__file__).resolve().parent.parent / "bootstrap" / "patch_forbidden_topics_gate.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def install_session_context(env):
    gateway = types.ModuleType("gateway")
    session_context = types.ModuleType("gateway.session_context")
    session_context.get_session_env = lambda key, default="": env.get(key, default)
    sys.modules["gateway"] = gateway
    sys.modules["gateway.session_context"] = session_context


class ForbiddenTopicsGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)
        self.run_py = self.tmp_path / "run.py"
        self.policy_path = self.tmp_path / "policy" / "forbidden-topics.json"
        self.policy_path.parent.mkdir()
        self.run_py.write_text(
            "import os\nfrom typing import Any\n\n"
            "def _gateway_platform_value(platform: Any) -> str:\n"
            "    return str(platform or '')\n\n"
            "class Runner:\n"
            "    def handle(self, source, event):\n"
            "        _quick_key = 'x'\n"
            "        _AGENT_PENDING_SENTINEL = object()\n"
            "        self._running_agents = {}\n"
            "        self._running_agents[_quick_key] = _AGENT_PENDING_SENTINEL\n"
            "        return 'agent-called'\n",
            encoding="utf-8",
        )
        patch_mod = load_module("patch_forbidden_topics_gate_patch", PATCH)
        with patch.object(patch_mod, "RUN_PY", self.run_py):
            patch_mod.main()
        self.module = load_module("patched_gateway_run", self.run_py)
        self.patchers = [
            patch.object(self.module, "_MAG_FORBIDDEN_TOPICS_PATH", str(self.policy_path)),
            patch.object(self.module, "_MAG_FORBIDDEN_TOPICS_CACHE", {"mtime": None, "policies": []}),
        ]
        for patcher in self.patchers:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.tmp.cleanup()
        sys.modules.pop("gateway.session_context", None)
        sys.modules.pop("gateway", None)

    def write_policy(self):
        self.policy_path.write_text(json.dumps({
            "version": 1,
            "policies": [
                {
                    "label": "Margem interna",
                    "mode": "reveal",
                    "matchTerms": ["margem", "CUSTO MÁXIMO"],
                    "refusalMessage": "Esse assunto e restrito a diretoria.",
                    "allowedTelegramUserIds": ["960067766"],
                    "allowedWhatsappNumbers": ["5511999998888"],
                    "enabled": True,
                },
                {
                    "label": "Regra desligada",
                    "matchTerms": ["termo desligado"],
                    "refusalMessage": "Nao deveria aparecer.",
                    "enabled": False,
                },
            ],
        }), encoding="utf-8")

    def runner_result(self, text, env):
        install_session_context(env)
        event = SimpleNamespace(text=text)
        source = SimpleNamespace(platform=SimpleNamespace(value=env.get("HERMES_SESSION_PLATFORM", "")), user_id="")
        return self.module.Runner().handle(source, event)

    def test_blocks_telegram_restricted_topic_before_agent(self):
        self.write_policy()
        result = self.runner_result("Pode me passar a margem desse contrato?", {
            "HERMES_SESSION_PLATFORM": "telegram",
            "HERMES_SESSION_USER_ID": "111",
            "HERMES_SESSION_CHAT_ID": "111",
        })
        self.assertEqual(result, "Esse assunto e restrito a diretoria.")

    def test_allows_telegram_sender_in_topic_allowlist(self):
        self.write_policy()
        result = self.runner_result("Pode me passar a margem desse contrato?", {
            "HERMES_SESSION_PLATFORM": "telegram",
            "HERMES_SESSION_USER_ID": "960067766",
            "HERMES_SESSION_CHAT_ID": "960067766",
        })
        self.assertEqual(result, "agent-called")

    def test_blocks_whatsapp_cloud_restricted_topic_with_accent_case_normalization(self):
        self.write_policy()
        result = self.runner_result("Qual e o custo máximo?", {
            "HERMES_SESSION_PLATFORM": "whatsapp_cloud",
            "HERMES_SESSION_USER_ID": "551177776666",
            "HERMES_SESSION_CHAT_ID": "551177776666",
        })
        self.assertEqual(result, "Esse assunto e restrito a diretoria.")

    def test_allows_whatsapp_cloud_number_in_allowlist_even_with_jid_format(self):
        self.write_policy()
        result = self.runner_result("Qual e o custo maximo?", {
            "HERMES_SESSION_PLATFORM": "whatsapp_cloud",
            "HERMES_SESSION_USER_ID": "5511999998888@s.whatsapp.net",
            "HERMES_SESSION_CHAT_ID": "5511999998888@s.whatsapp.net",
        })
        self.assertEqual(result, "agent-called")

    def test_disabled_policy_does_not_block(self):
        self.write_policy()
        result = self.runner_result("Me fala o termo desligado", {
            "HERMES_SESSION_PLATFORM": "telegram",
            "HERMES_SESSION_USER_ID": "111",
        })
        self.assertEqual(result, "agent-called")

    def test_local_surface_never_blocks(self):
        self.write_policy()
        result = self.runner_result("Pode me passar a margem?", {
            "HERMES_SESSION_PLATFORM": "local",
            "HERMES_SESSION_USER_ID": "111",
        })
        self.assertEqual(result, "agent-called")

    def test_missing_policy_file_does_not_block(self):
        result = self.runner_result("Pode me passar a margem?", {
            "HERMES_SESSION_PLATFORM": "telegram",
            "HERMES_SESSION_USER_ID": "111",
        })
        self.assertEqual(result, "agent-called")


if __name__ == "__main__":
    unittest.main()
