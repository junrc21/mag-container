import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "bootstrap" / "patch_companion_credit_gate.py"


FAKE_API_SERVER = '''\
def _hermes_version() -> str:
    return "test"

class ApiServer:
    def _check_auth(self, request):
        return None

    async def _handle_chat_completions(self, request):
        auth_err = self._check_auth(request)
        if auth_err:
            return auth_err

        # Parse request body
        body = await request.json()
        platform_override = None
        user_message = body.get("message")
        history = []
        system_prompt = None
        session_id = "sess"
        gateway_session_key = "tenant"
        stream = body.get("stream")
        agent_ref = []
        if stream:
            agent_task = asyncio.ensure_future(self._run_agent(
                user_message=user_message,
                conversation_history=history,
                ephemeral_system_prompt=system_prompt,
                session_id=session_id,
                stream_delta_callback=_on_delta,
                tool_start_callback=_on_tool_start,
                tool_complete_callback=_on_tool_complete,
                agent_ref=agent_ref,
                gateway_session_key=gateway_session_key,
            ))
            return agent_task
        else:
            return await self._run_agent(
                user_message=user_message,
                conversation_history=history,
                ephemeral_system_prompt=system_prompt,
                session_id=session_id,
                gateway_session_key=gateway_session_key,
            )

    async def _run_agent(
        self,
        user_message,
        conversation_history,
        ephemeral_system_prompt,
        session_id,
        stream_delta_callback=None,
        tool_start_callback=None,
        tool_complete_callback=None,
        agent_ref: Optional[list] = None,
        gateway_session_key: Optional[str] = None,
    ) -> tuple:
        if True:
            tokens = set_session_vars(
                platform="api_server",
                chat_id=session_id or "",
                session_key=gateway_session_key or session_id or "",
                session_id=session_id or "",
            )
        result = {"completed": True}
        agent = object()
        if True:
            if True:
                usage = {
                    "input_tokens": getattr(agent, "session_prompt_tokens", 0) or 0,
                    "output_tokens": getattr(agent, "session_completion_tokens", 0) or 0,
                    "total_tokens": getattr(agent, "session_total_tokens", 0) or 0,
                }
        return result, usage
'''


class CompanionChannelIdPatchTests(unittest.TestCase):
    def test_companion_patch_threads_channel_id_into_session_vars(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "api_server.py"
            target.write_text(FAKE_API_SERVER)
            env = {"PATH": os.environ.get("PATH", ""), "API_SERVER_PY": str(target)}

            first = subprocess.run([sys.executable, str(PATCH)], text=True, capture_output=True, env=env)
            once = target.read_text()
            second = subprocess.run([sys.executable, str(PATCH)], text=True, capture_output=True, env=env)
            twice = target.read_text()

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("already patched", second.stdout)
        self.assertEqual(once, twice)
        self.assertIn("def _mag_parse_channel_id", once)
        self.assertIn("mag_channel_id = _mag_parse_channel_id(request, self._api_key)", once)
        self.assertIn("mag_channel_id=mag_channel_id,  # MAG: active Companion channel", once)
        self.assertIn('mag_channel_id: str = "",  # MAG: active Companion channel', once)
        self.assertIn("X-MAG-Channel-Id", once)
        compile(once, "api_server.py", "exec")


if __name__ == "__main__":
    unittest.main()
