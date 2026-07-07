"""Build-time patch: expose WhatsApp web-pairing routes on the gateway API (MAG).

Adds 4 thin routes to the gateway's API server so the MAG control plane can drive QR
pairing from the web:
    POST /api/whatsapp/pair    GET /api/whatsapp/qr
    GET  /api/whatsapp/status  POST /api/whatsapp/logout
All the logic lives in the self-contained module mag_whatsapp_pairing.py (copied to
gateway/platforms/); the api_server only gets 4 one-line wrapper methods that delegate
there — so we DON'T touch any existing api_server behavior (low risk).

Idempotent + fail-loud (mirrors the other bootstrap patches).
"""

import os
import pathlib
import sys

API_SERVER = pathlib.Path(
    os.getenv("GATEWAY_API_SERVER_PY", "/opt/hermes/gateway/platforms/api_server.py")
)
MARKER = "_mag_wa_pair"


def apply(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        print(f"  [skip] {label}: already patched")
        return text
    if old not in text:
        sys.exit(
            f"FATAL: api_server anchor not found for '{label}'. "
            f"Upstream api_server.py changed — update patch_whatsapp_gateway.py."
        )
    print(f"  [ok]   {label}")
    return text.replace(old, new, 1)


# --- Edit 1: register the 5 routes (after /v1/chat/completions) ---------------
OLD_ROUTES = '            self._app.router.add_post("/v1/chat/completions", self._handle_chat_completions)\n'
NEW_ROUTES = (
    '            self._app.router.add_post("/v1/chat/completions", self._handle_chat_completions)\n'
    "            # _mag_wa_pair: WhatsApp web pairing (QR) + direct send. Logic in mag_whatsapp_pairing.py.\n"
    '            self._app.router.add_post("/api/whatsapp/pair", self._mag_wa_pair)\n'
    '            self._app.router.add_get("/api/whatsapp/qr", self._mag_wa_qr)\n'
    '            self._app.router.add_get("/api/whatsapp/status", self._mag_wa_status)\n'
    '            self._app.router.add_post("/api/whatsapp/logout", self._mag_wa_logout)\n'
    '            self._app.router.add_post("/api/whatsapp/send-direct", self._mag_wa_send_direct)\n'
)

# --- Edit 2: 5 thin wrapper methods (before _handle_models) -------------------
OLD_METHODS = (
    '    async def _handle_models(self, request: "web.Request") -> "web.Response":\n'
    '        """GET /v1/models — return hermes-agent as an available model."""\n'
)
NEW_METHODS = (
    "    async def _mag_wa_pair(self, request):\n"
    "        from gateway.platforms import mag_whatsapp_pairing as _wa\n"
    "        return await _wa.handle_pair(request)\n"
    "\n"
    "    async def _mag_wa_qr(self, request):\n"
    "        from gateway.platforms import mag_whatsapp_pairing as _wa\n"
    "        return await _wa.handle_qr(request)\n"
    "\n"
    "    async def _mag_wa_status(self, request):\n"
    "        from gateway.platforms import mag_whatsapp_pairing as _wa\n"
    "        return await _wa.handle_status(request)\n"
    "\n"
    "    async def _mag_wa_logout(self, request):\n"
    "        from gateway.platforms import mag_whatsapp_pairing as _wa\n"
    "        return await _wa.handle_logout(request)\n"
    "\n"
    "    async def _mag_wa_send_direct(self, request):\n"
    "        from gateway.platforms import mag_whatsapp_pairing as _wa\n"
    "        return await _wa.handle_send_direct(request)\n"
    "\n"
    '    async def _handle_models(self, request: "web.Request") -> "web.Response":\n'
    '        """GET /v1/models — return hermes-agent as an available model."""\n'
)


def main() -> None:
    if not API_SERVER.exists():
        sys.exit(f"FATAL: api_server.py not found at {API_SERVER}")
    text = API_SERVER.read_text()
    print(f"Patching {API_SERVER} ({MARKER})")
    text = apply(text, OLD_ROUTES, NEW_ROUTES, "register /api/whatsapp/* routes")
    text = apply(text, OLD_METHODS, NEW_METHODS, "wrapper methods")
    API_SERVER.write_text(text)
    print("  api_server WhatsApp routes patched.")


if __name__ == "__main__":
    main()
