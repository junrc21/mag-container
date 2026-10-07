"""Expose MAG_CHANNEL_ID as a task-local session variable.

Small, isolated patch: tools and memory code can read the active tenant channel
without touching process-global os.environ during concurrent gateway turns.
"""
from pathlib import Path
import os


PATH = Path(os.getenv("GATEWAY_SESSION_CONTEXT_PY", "/opt/hermes/gateway/session_context.py"))
RUN_PATH = Path(os.getenv("GATEWAY_RUN_PY", "/opt/hermes/gateway/run.py"))
MARKER = "# MAG_SESSION_CHANNEL_ID_V1"
RUN_MARKER = "# MAG_RUN_CHANNEL_ID_V1"


def patch_session_context() -> None:
    text = PATH.read_text()
    if MARKER in text:
        print("OK: session_context channel id already patched")
        return
    anchors = [
        ('_SESSION_MESSAGE_ID: ContextVar = ContextVar("HERMES_SESSION_MESSAGE_ID", default=_UNSET)\n',
         '_SESSION_MESSAGE_ID: ContextVar = ContextVar("HERMES_SESSION_MESSAGE_ID", default=_UNSET)\n'
         f'{MARKER}\n'
         '_MAG_CHANNEL_ID: ContextVar = ContextVar("MAG_CHANNEL_ID", default=_UNSET)\n'),
        ('    "HERMES_SESSION_MESSAGE_ID": _SESSION_MESSAGE_ID,\n',
         '    "HERMES_SESSION_MESSAGE_ID": _SESSION_MESSAGE_ID,\n'
         '    "MAG_CHANNEL_ID": _MAG_CHANNEL_ID,\n'),
        ('    message_id: str = "",\n',
         '    message_id: str = "",\n'
         '    mag_channel_id: str = "",\n'),
        ('        _SESSION_MESSAGE_ID.set(message_id),\n',
         '        _SESSION_MESSAGE_ID.set(message_id),\n'
         '        _MAG_CHANNEL_ID.set(mag_channel_id),\n'),
        ('        _SESSION_MESSAGE_ID,\n',
         '        _SESSION_MESSAGE_ID,\n'
         '        _MAG_CHANNEL_ID,\n'),
    ]
    for old, new in anchors:
        if text.count(old) != 1:
            raise SystemExit(f"patch_session_channel_id: session_context anchor drift: {old[:80]!r}")
        text = text.replace(old, new, 1)
    compile(text, str(PATH), "exec")
    PATH.write_text(text)
    print("OK: session_context channel id patched")


def patch_run() -> None:
    text = RUN_PATH.read_text()
    if RUN_MARKER in text:
        print("OK: run channel id already patched")
        return
    call_replacements = [
        (
            '_session_env_tokens = self._set_session_env(context, direct_message=(event.text or ""))\n',
            '        _session_env_tokens = self._set_session_env(context, direct_message=(event.text or ""), mag_channel_id=_mag_channel_id)\n',
        ),
        (
            '_session_env_tokens = self._set_session_env(context)\n',
            '        _session_env_tokens = self._set_session_env(context, mag_channel_id=_mag_channel_id)\n',
        ),
    ]
    matched = [item for item in call_replacements if item[0] in text]
    if len(matched) != 1:
        raise SystemExit("patch_session_channel_id: run.py set_session_env call anchor drift")
    call_old, call_new_tail = matched[0]
    call_new = (
        f'{RUN_MARKER}\n'
        '        _mag_raw_message = getattr(event, "raw_message", None) if event is not None else None\n'
        '        _mag_channel_id = ""\n'
        '        if isinstance(_mag_raw_message, dict):\n'
        '            _mag_channel_id = str(_mag_raw_message.get("_mag_channel_id") or "")\n'
        '        if not _mag_channel_id:\n'
        '            try:\n'
        '                import json as _mag_json\n'
        '                _mag_provider = str(getattr(context.source.platform, "value", context.source.platform) or "").lower()\n'
        '                _mag_policy_path = os.getenv("MAG_CHANNELS_CONFIG_PATH") or os.path.expanduser("~/policy/channels.json")\n'
        '                with open(_mag_policy_path, "r") as _mag_f:\n'
        '                    _mag_channels = (_mag_json.load(_mag_f) or {}).get("channels") or []\n'
        '                _mag_matches = [c for c in _mag_channels if isinstance(c, dict) and str(c.get("provider") or "").lower() == _mag_provider and str(c.get("status") or "") in ("connected", "pending_review")]\n'
        '                if len(_mag_matches) == 1:\n'
        '                    _mag_channel_id = str(_mag_matches[0].get("channelId") or "")\n'
        '            except Exception:\n'
        '                pass\n'
        + call_new_tail
    )
    text = text.replace(call_old, call_new, 1)

    signature_replacements = [
        (
            '    def _set_session_env(self, context: SessionContext, direct_message: str = "") -> list:\n',
            '    def _set_session_env(self, context: SessionContext, direct_message: str = "", mag_channel_id: str = "") -> list:\n',
        ),
        (
            '    def _set_session_env(self, context: SessionContext) -> list:\n',
            '    def _set_session_env(self, context: SessionContext, mag_channel_id: str = "") -> list:\n',
        ),
    ]
    matched = [item for item in signature_replacements if item[0] in text]
    if len(matched) != 1:
        raise SystemExit("patch_session_channel_id: run.py _set_session_env signature anchor drift")
    signature_old, signature_new = matched[0]
    text = text.replace(signature_old, signature_new, 1)

    arg_old = '            message_id=str(context.source.message_id) if context.source.message_id else "",\n'
    arg_new = arg_old + '            mag_channel_id=mag_channel_id,\n'
    if text.count(arg_old) != 1:
        raise SystemExit("patch_session_channel_id: run.py set_session_vars args anchor drift")
    text = text.replace(arg_old, arg_new, 1)
    compile(text, str(RUN_PATH), "exec")
    RUN_PATH.write_text(text)
    print("OK: run channel id patched")


if __name__ == "__main__":
    patch_session_context()
    patch_run()
