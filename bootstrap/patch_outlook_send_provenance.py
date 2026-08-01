#!/usr/bin/env python3
"""Inject per-turn Outlook send provenance into Hermes MCP calls."""
import os
from pathlib import Path

RUN = Path(os.getenv("GATEWAY_RUN_PY", "/opt/hermes/gateway/run.py"))
CTX = Path(os.getenv("SESSION_CONTEXT_PY", "/opt/hermes/gateway/session_context.py"))
MCP = Path(os.getenv("MCP_TOOL_PY", "/opt/hermes/tools/mcp_tool.py"))
MARKER = "MAG_outlook_send_provenance_v1"

def patch(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        print(f"skip {path}: already patched")
        return
    if old not in text:
        raise SystemExit(f"anchor not found in {path}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    print(f"patched {path}")

patch(CTX,
'''_SESSION_MESSAGE_ID: ContextVar = ContextVar("HERMES_SESSION_MESSAGE_ID", default=_UNSET)
''',
'''_SESSION_MESSAGE_ID: ContextVar = ContextVar("HERMES_SESSION_MESSAGE_ID", default=_UNSET)
# MAG_outlook_send_provenance_v1: immutable direct-user text for trusted tool wrappers.
_SESSION_DIRECT_MESSAGE: ContextVar = ContextVar("MAG_SESSION_DIRECT_MESSAGE", default=_UNSET)
''')

text = CTX.read_text(encoding="utf-8")
text = text.replace('''    "HERMES_SESSION_MESSAGE_ID": _SESSION_MESSAGE_ID,
''', '''    "HERMES_SESSION_MESSAGE_ID": _SESSION_MESSAGE_ID,
    "MAG_SESSION_DIRECT_MESSAGE": _SESSION_DIRECT_MESSAGE,
''', 1)
text = text.replace('''    message_id: str = "",
    cwd: str = "",
''', '''    message_id: str = "",
    direct_message: str = "",
    cwd: str = "",
''', 1)
text = text.replace('''        _SESSION_MESSAGE_ID.set(message_id),
    ]
''', '''        _SESSION_MESSAGE_ID.set(message_id),
        _SESSION_DIRECT_MESSAGE.set(direct_message),
    ]
''', 1)
text = text.replace('''        _SESSION_MESSAGE_ID,
    ):
''', '''        _SESSION_MESSAGE_ID,
        _SESSION_DIRECT_MESSAGE,
    ):
''', 1)
CTX.write_text(text, encoding="utf-8")

patch(RUN,
'''        _session_env_tokens = self._set_session_env(context)
''',
'''        # MAG_outlook_send_provenance_v1: capture authenticated inbound text before agent enrichment.
        _session_env_tokens = self._set_session_env(context, direct_message=(event.text or ""))
''')
text = RUN.read_text(encoding="utf-8")
text = text.replace('''    def _set_session_env(self, context: SessionContext) -> list:
''', '''    def _set_session_env(self, context: SessionContext, direct_message: str = "") -> list:
''', 1)
text = text.replace('''            message_id=str(context.source.message_id) if context.source.message_id else "",
        )
''', '''            session_id=context.session_id,
             message_id=str(context.source.message_id) if context.source.message_id else "",
            direct_message=direct_message,
        )
''', 1)
RUN.write_text(text, encoding="utf-8")

patch(MCP,
'''def _make_tool_handler(server_name: str, tool_name: str, tool_timeout: float):
''',
'''# MAG_outlook_send_provenance_v1
def _mag_send_provenance(tool_name: str, args: dict) -> str:
    if tool_name != "outlook_send_email":
        return ""
    import base64, hashlib, hmac, json, os, re, time, uuid
    from gateway.session_context import get_session_env
    key = os.getenv("MAG_SEND_PROVENANCE_KEY", "")
    tenant = os.getenv("MAG_TENANT_ID", "")
    direct = get_session_env("MAG_SESSION_DIRECT_MESSAGE", "").strip()
    # Positive allowlist: only a direct, explicit send request at message start.
    if not key or len(key) < 32 or not tenant or not re.match(
        r"^(?:envie|enviar|mande|mandar)\\s+(?:um\\s+)?e-?mail\\b|^send\\s+(?:an?\\s+)?e-?mail\\b",
        direct, re.IGNORECASE,
    ):
        return ""
    safe_args = {k: args.get(k) for k in ("account", "to", "cc", "bcc", "subject", "body")}
    draft = json.dumps(safe_args, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    now = int(time.time())
    claims = {
        "v": 1, "aud": "mag-outlook-send", "action": "outlook.send",
        "tenantId": tenant,
        "platform": get_session_env("HERMES_SESSION_PLATFORM", ""),
        "platformUserId": get_session_env("HERMES_SESSION_USER_ID", ""),
        "chatId": get_session_env("HERMES_SESSION_CHAT_ID", ""),
        "sessionId": get_session_env("HERMES_SESSION_ID", ""),
        "messageId": get_session_env("HERMES_SESSION_MESSAGE_ID", ""),
        "draftHash": hashlib.sha256(draft.encode()).hexdigest(),
        "intentHash": hashlib.sha256(direct.encode()).hexdigest(),
        "iat": now, "exp": now + 90, "jti": str(uuid.uuid4()),
    }
    if not claims["platformUserId"] or not claims["chatId"]:
        return ""
    payload = base64.urlsafe_b64encode(json.dumps(claims, sort_keys=True, separators=(",", ":")).encode()).rstrip(b"=").decode()
    sig = base64.urlsafe_b64encode(hmac.new(key.encode(), payload.encode(), hashlib.sha256).digest()).rstrip(b"=").decode()
    return payload + "." + sig


def _make_tool_handler(server_name: str, tool_name: str, tool_timeout: float):
''')
text = MCP.read_text(encoding="utf-8")
text = text.replace('''    def _handler(args: dict, **kwargs) -> str:
        # Circuit breaker:''', '''    def _handler(args: dict, **kwargs) -> str:
        args = dict(args or {})
        args.pop("__mag_provenance", None)
        provenance = _mag_send_provenance(tool_name, args)
        if tool_name == "outlook_send_email":
            if not provenance:
                return json.dumps({"error": "Envio bloqueado: solicitaÃ§Ã£o direta autenticada ausente."}, ensure_ascii=False)
            args["__mag_provenance"] = provenance
        # Circuit breaker:''', 1)
MCP.write_text(text, encoding="utf-8")
