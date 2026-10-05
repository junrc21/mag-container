import os
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PATCH_MULTI = ROOT / "bootstrap" / "patch_multi_whatsapp_cloud.py"
PATCH_SESSION = ROOT / "bootstrap" / "patch_session_channel_id.py"
PATCH_OUTLOOK = ROOT / "bootstrap" / "patch_outlook_send_provenance.py"

FAKE_WHATSAPP_CLOUD = '''\
from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import BasePlatformAdapter

class WhatsAppBehaviorMixin:
    pass

class Logger:
    def info(self, *args, **kwargs): pass
    def warning(self, *args, **kwargs): pass
logger = Logger()

class WhatsAppCloudAdapter(WhatsAppBehaviorMixin, BasePlatformAdapter):
    def __init__(self, config: PlatformConfig):
        pass
'''

FAKE_WHATSAPP_CLOUD_RUNTIME = '''\
import asyncio
import os

class PlatformConfig:
    def __init__(
        self,
        enabled=True,
        token=None,
        api_key=None,
        home_channel=None,
        reply_to_mode="first",
        gateway_restart_notification=True,
        extra=None,
    ):
        self.enabled = enabled
        self.token = token
        self.api_key = api_key
        self.home_channel = home_channel
        self.reply_to_mode = reply_to_mode
        self.gateway_restart_notification = gateway_restart_notification
        self.extra = extra or {}

class BasePlatformAdapter:
    pass

class WhatsAppBehaviorMixin:
    pass

class Logger:
    def info(self, *args, **kwargs): pass
    def warning(self, *args, **kwargs): pass
logger = Logger()

class Handoff:
    def enabled(self): return False
_mag_handoff = Handoff()

class Resume:
    async def poll(self, adapter): return None
_mag_resume = Resume()

class WhatsAppCloudAdapter(WhatsAppBehaviorMixin, BasePlatformAdapter):
    def __init__(self, config: PlatformConfig):
        self.config = config
        self._phone_number_id = config.extra.get("phone_number_id", "")
        self._http_client = None
        self._runner = object()
        self.dispatched = []
        self.sent = []
        self.typing = []
        self.connected = False

    def set_message_handler(self, handler): self._message_handler = handler
    def set_fatal_error_handler(self, handler): self._fatal_error_handler = handler
    def set_busy_session_handler(self, handler): self._busy_session_handler = handler
    def set_session_store(self, store): self._session_store = store
    def set_topic_recovery_fn(self, fn): self._topic_recovery_fn = fn
    def _mark_connected(self): self.connected = True
    def _mark_disconnected(self): self.connected = False

    async def connect(self):
        self._http_client = object()
        self._mark_connected()
        return True

    async def disconnect(self):
        self._http_client = None
        self._mark_disconnected()

    async def _dispatch_payload(self, payload):
        self.dispatched.append(payload)

    async def send(self, chat_id, content, reply_to=None, metadata=None):
        self.sent.append((chat_id, content, reply_to, metadata))
        return {"ok": True, "phone": self._phone_number_id}

    async def send_typing(self, chat_id, metadata=None):
        self.typing.append((chat_id, metadata))
        return {"ok": True, "phone": self._phone_number_id}
'''

FAKE_SESSION_CONTEXT = '''\
from contextvars import ContextVar
_UNSET = object()
_SESSION_PLATFORM: ContextVar = ContextVar("HERMES_SESSION_PLATFORM", default=_UNSET)
_SESSION_CHAT_ID: ContextVar = ContextVar("HERMES_SESSION_CHAT_ID", default=_UNSET)
_SESSION_CHAT_NAME: ContextVar = ContextVar("HERMES_SESSION_CHAT_NAME", default=_UNSET)
_SESSION_THREAD_ID: ContextVar = ContextVar("HERMES_SESSION_THREAD_ID", default=_UNSET)
_SESSION_USER_ID: ContextVar = ContextVar("HERMES_SESSION_USER_ID", default=_UNSET)
_SESSION_USER_NAME: ContextVar = ContextVar("HERMES_SESSION_USER_NAME", default=_UNSET)
_SESSION_KEY: ContextVar = ContextVar("HERMES_SESSION_KEY", default=_UNSET)
_SESSION_ID: ContextVar = ContextVar("HERMES_SESSION_ID", default=_UNSET)
_SESSION_MESSAGE_ID: ContextVar = ContextVar("HERMES_SESSION_MESSAGE_ID", default=_UNSET)
_SESSION_DIRECT_MESSAGE: ContextVar = ContextVar("MAG_SESSION_DIRECT_MESSAGE", default=_UNSET)
_SESSION_VARS = {
    "HERMES_SESSION_PLATFORM": _SESSION_PLATFORM,
    "HERMES_SESSION_CHAT_ID": _SESSION_CHAT_ID,
    "HERMES_SESSION_CHAT_NAME": _SESSION_CHAT_NAME,
    "HERMES_SESSION_THREAD_ID": _SESSION_THREAD_ID,
    "HERMES_SESSION_USER_ID": _SESSION_USER_ID,
    "HERMES_SESSION_USER_NAME": _SESSION_USER_NAME,
    "HERMES_SESSION_KEY": _SESSION_KEY,
    "HERMES_SESSION_ID": _SESSION_ID,
    "HERMES_SESSION_MESSAGE_ID": _SESSION_MESSAGE_ID,
}

def set_session_vars(
    platform: str = "",
    chat_id: str = "",
    chat_name: str = "",
    thread_id: str = "",
    user_id: str = "",
    user_name: str = "",
    session_key: str = "",
    session_id: str = "",
    message_id: str = "",
    direct_message: str = "",
    cwd: str = "",
) -> list:
    tokens = [
        _SESSION_PLATFORM.set(platform),
        _SESSION_CHAT_ID.set(chat_id),
        _SESSION_CHAT_NAME.set(chat_name),
        _SESSION_THREAD_ID.set(thread_id),
        _SESSION_USER_ID.set(user_id),
        _SESSION_USER_NAME.set(user_name),
        _SESSION_KEY.set(session_key),
        _SESSION_ID.set(session_id),
        _SESSION_MESSAGE_ID.set(message_id),
        _SESSION_DIRECT_MESSAGE.set(direct_message),
    ]
    return tokens

def clear_session_vars(tokens: list) -> None:
    for var in (
        _SESSION_PLATFORM,
        _SESSION_CHAT_ID,
        _SESSION_CHAT_NAME,
        _SESSION_THREAD_ID,
        _SESSION_USER_ID,
        _SESSION_USER_NAME,
        _SESSION_KEY,
        _SESSION_ID,
        _SESSION_MESSAGE_ID,
        _SESSION_DIRECT_MESSAGE,
    ):
        var.set("")
'''

FAKE_RUN = '''\
class SessionContext: pass

class Runner:
    def _handle(self, event, context):
        _session_env_tokens = self._set_session_env(context, direct_message=(event.text or ""))

    def _set_session_env(self, context: SessionContext, direct_message: str = "") -> list:
        from gateway.session_context import set_session_vars
        return set_session_vars(
            platform=context.source.platform.value,
            chat_id=context.source.chat_id,
            chat_name=context.source.chat_name or "",
            thread_id=str(context.source.thread_id) if context.source.thread_id else "",
            user_id=str(context.source.user_id) if context.source.user_id else "",
            user_name=str(context.source.user_name) if context.source.user_name else "",
            session_key=context.session_key,
            session_id=context.session_id,
            message_id=str(context.source.message_id) if context.source.message_id else "",
            direct_message=direct_message,
        )
'''


class MultiWhatsAppCloudPatchTests(unittest.TestCase):
    def test_multi_adapter_patch_applies_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "whatsapp_cloud.py"
            target.write_text(FAKE_WHATSAPP_CLOUD)
            env = {"PATH": os.environ.get("PATH", ""), "WHATSAPP_CLOUD_PY": str(target)}

            first = subprocess.run([sys.executable, str(PATCH_MULTI)], text=True, capture_output=True, env=env)
            once = target.read_text()
            second = subprocess.run([sys.executable, str(PATCH_MULTI)], text=True, capture_output=True, env=env)
            twice = target.read_text()

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("already patched", second.stdout)
        self.assertEqual(once, twice)
        self.assertIn("_mag_load_whatsapp_cloud_channels", once)
        self.assertIn('metadata.get("phone_number_id")', once)
        self.assertIn('raw_message["_mag_channel_id"]', once)
        compile(once, "whatsapp_cloud.py", "exec")

    def test_multi_adapter_patch_fails_loud_when_adapter_anchor_moves(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "whatsapp_cloud.py"
            target.write_text(FAKE_WHATSAPP_CLOUD.replace("class WhatsAppCloudAdapter", "class CloudWhatsAppAdapter"))
            completed = subprocess.run(
                [sys.executable, str(PATCH_MULTI)],
                text=True,
                capture_output=True,
                env={"PATH": os.environ.get("PATH", ""), "WHATSAPP_CLOUD_PY": str(target)},
            )
            unchanged = target.read_text()

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("anchor drift", completed.stderr)
        self.assertNotIn("_mag_load_whatsapp_cloud_channels", unchanged)

    def test_multi_adapter_routes_by_phone_number_and_tags_channel(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            target = tmp_path / "whatsapp_cloud_runtime.py"
            config_path = tmp_path / "channels.json"
            target.write_text(FAKE_WHATSAPP_CLOUD_RUNTIME)
            config_path.write_text(json.dumps({
                "channels": [
                    {
                        "channelId": "chan-primary",
                        "provider": "whatsapp_cloud",
                        "status": "connected",
                        "externalId": "phone-1",
                        "credentials": {"phoneNumberId": "phone-1", "accessToken": "token-1"},
                    },
                    {
                        "channelId": "chan-secondary",
                        "provider": "whatsapp_cloud",
                        "status": "connected",
                        "externalId": "phone-2",
                        "credentials": {"phoneNumberId": "phone-2", "accessToken": "token-2"},
                    },
                ]
            }))
            env = {"PATH": os.environ.get("PATH", ""), "WHATSAPP_CLOUD_PY": str(target)}
            patched = subprocess.run([sys.executable, str(PATCH_MULTI)], text=True, capture_output=True, env=env)
            self.assertEqual(patched.returncode, 0, patched.stderr)

            old_path = os.environ.get("MAG_CHANNELS_CONFIG_PATH")
            os.environ["MAG_CHANNELS_CONFIG_PATH"] = str(config_path)
            try:
                spec = importlib.util.spec_from_file_location("whatsapp_cloud_runtime", target)
                module = importlib.util.module_from_spec(spec)
                assert spec.loader is not None
                spec.loader.exec_module(module)
                adapter = module.WhatsAppCloudAdapter(module.PlatformConfig(extra={}))
            finally:
                if old_path is None:
                    os.environ.pop("MAG_CHANNELS_CONFIG_PATH", None)
                else:
                    os.environ["MAG_CHANNELS_CONFIG_PATH"] = old_path

            self.assertEqual(adapter._phone_number_id, "phone-1")
            self.assertEqual(adapter._mag_channel_id, "chan-primary")
            self.assertEqual(len(adapter._mag_children), 1)
            child = adapter._mag_children[0]
            self.assertEqual(child._phone_number_id, "phone-2")
            self.assertEqual(child._mag_channel_id, "chan-secondary")

            handler = object()
            adapter.set_message_handler(handler)
            self.assertIs(child._message_handler, handler)

            raw_message = {"from": "5511999999999"}
            payload = {
                "object": "whatsapp_business_account",
                "entry": [{
                    "changes": [{
                        "field": "messages",
                        "value": {
                            "metadata": {"phone_number_id": "phone-2"},
                            "messages": [raw_message],
                        },
                    }]
                }],
            }
            import asyncio
            asyncio.run(adapter._dispatch_payload(payload))
            self.assertEqual(raw_message["_mag_channel_id"], "chan-secondary")
            self.assertEqual(len(child.dispatched), 1)
            self.assertEqual(adapter.mag_channel_id_for_chat("5511999999999"), "chan-secondary")

            result = asyncio.run(adapter.send("5511999999999", "oi"))
            self.assertEqual(result["phone"], "phone-2")
            self.assertEqual(child.sent[0][1], "oi")

    def test_session_channel_id_patch_applies_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session_context.py"
            run = Path(tmp) / "run.py"
            session.write_text(FAKE_SESSION_CONTEXT)
            run.write_text(FAKE_RUN)
            env = {
                "PATH": os.environ.get("PATH", ""),
                "GATEWAY_SESSION_CONTEXT_PY": str(session),
                "GATEWAY_RUN_PY": str(run),
            }

            first = subprocess.run([sys.executable, str(PATCH_SESSION)], text=True, capture_output=True, env=env)
            once_session = session.read_text()
            once_run = run.read_text()
            second = subprocess.run([sys.executable, str(PATCH_SESSION)], text=True, capture_output=True, env=env)
            twice_session = session.read_text()
            twice_run = run.read_text()

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("already patched", second.stdout)
        self.assertEqual(once_session, twice_session)
        self.assertEqual(once_run, twice_run)
        self.assertIn('"MAG_CHANNEL_ID": _MAG_CHANNEL_ID', once_session)
        self.assertIn("mag_channel_id=mag_channel_id", once_run)
        self.assertIn('_mag_raw_message.get("_mag_channel_id")', once_run)
        compile(once_session, "session_context.py", "exec")
        compile(once_run, "run.py", "exec")

    def test_outlook_provenance_patch_keeps_session_context_compatible_after_channel_id_patch(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = Path(tmp) / "session_context.py"
            run = Path(tmp) / "run.py"
            mcp = Path(tmp) / "mcp_tool.py"
            session.write_text(FAKE_SESSION_CONTEXT)
            run.write_text(FAKE_RUN)
            mcp.write_text('def _make_tool_handler(server_name: str, tool_name: str, tool_timeout: float):\n    pass\n')
            env = {
                "PATH": os.environ.get("PATH", ""),
                "GATEWAY_SESSION_CONTEXT_PY": str(session),
                "GATEWAY_RUN_PY": str(run),
                "SESSION_CONTEXT_PY": str(session),
                "MCP_TOOL_PY": str(mcp),
            }

            first = subprocess.run([sys.executable, str(PATCH_SESSION)], text=True, capture_output=True, env=env)
            second = subprocess.run([sys.executable, str(PATCH_OUTLOOK)], text=True, capture_output=True, env=env)
            patched_session = session.read_text()
            patched_run = run.read_text()

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn('direct_message: str = ""', patched_session)
        self.assertIn('mag_channel_id: str = ""', patched_session)
        self.assertIn('_SESSION_DIRECT_MESSAGE.set(direct_message)', patched_session)
        self.assertIn('_MAG_CHANNEL_ID.set(mag_channel_id)', patched_session)
        self.assertIn('direct_message=direct_message', patched_run)
        self.assertIn('mag_channel_id=mag_channel_id', patched_run)
        compile(patched_session, "session_context.py", "exec")
        compile(patched_run, "run.py", "exec")


if __name__ == "__main__":
    unittest.main()
