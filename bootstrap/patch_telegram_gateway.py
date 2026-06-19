"""Build-time patch: expose Telegram access-control routes on the gateway API (MAG).

Adds thin routes to the gateway's API server so the MAG control plane can drive the
full Telegram access surface from the web (no SSH/CLI):
    GET  /api/telegram/pairing
    POST /api/telegram/pairing/approve        (by pairing code)
    POST /api/telegram/pairing/approve_user   (by known user id — no code)
    POST /api/telegram/pairing/revoke
    POST /api/telegram/pairing/block          (deny → block list)
    POST /api/telegram/pairing/unblock
All the logic lives in the self-contained module mag_telegram_pairing.py (copied to
gateway/platforms/); the api_server only gets one-line wrapper methods that delegate
there — so we DON'T touch any existing api_server behavior (low risk).

Anchors on the WhatsApp pairing lines that patch_whatsapp_gateway.py already inserted
(that patch runs first in the Dockerfile), so this is appended right after them.

Idempotent + fail-loud (mirrors the other bootstrap patches).
"""

import os
import pathlib
import sys

API_SERVER = pathlib.Path(
    os.getenv("GATEWAY_API_SERVER_PY", "/opt/hermes/gateway/platforms/api_server.py")
)
MARKER = "_mag_tg_pairing"


def apply(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        print(f"  [skip] {label}: already patched")
        return text
    if old not in text:
        sys.exit(
            f"FATAL: api_server anchor not found for '{label}'. "
            f"Upstream api_server.py (or patch_whatsapp_gateway.py) changed — "
            f"update patch_telegram_gateway.py."
        )
    print(f"  [ok]   {label}")
    return text.replace(old, new, 1)


# --- Edit 1: register the routes (after the WhatsApp logout route) -------------
OLD_ROUTES = '            self._app.router.add_post("/api/whatsapp/logout", self._mag_wa_logout)\n'
NEW_ROUTES = (
    '            self._app.router.add_post("/api/whatsapp/logout", self._mag_wa_logout)\n'
    "            # _mag_tg_pairing: Telegram access control. Logic in mag_telegram_pairing.py.\n"
    '            self._app.router.add_get("/api/telegram/pairing", self._mag_tg_pairing_list)\n'
    '            self._app.router.add_post("/api/telegram/pairing/approve", self._mag_tg_pairing_approve)\n'
    '            self._app.router.add_post("/api/telegram/pairing/approve_user", self._mag_tg_pairing_approve_user)\n'
    '            self._app.router.add_post("/api/telegram/pairing/revoke", self._mag_tg_pairing_revoke)\n'
    '            self._app.router.add_post("/api/telegram/pairing/block", self._mag_tg_pairing_block)\n'
    '            self._app.router.add_post("/api/telegram/pairing/unblock", self._mag_tg_pairing_unblock)\n'
)

# --- Edit 2: thin wrapper methods (after the WhatsApp logout wrapper) ----------
OLD_METHODS = (
    "    async def _mag_wa_logout(self, request):\n"
    "        from gateway.platforms import mag_whatsapp_pairing as _wa\n"
    "        return await _wa.handle_logout(request)\n"
)
NEW_METHODS = (
    "    async def _mag_wa_logout(self, request):\n"
    "        from gateway.platforms import mag_whatsapp_pairing as _wa\n"
    "        return await _wa.handle_logout(request)\n"
    "\n"
    "    async def _mag_tg_pairing_list(self, request):\n"
    "        from gateway.platforms import mag_telegram_pairing as _tg\n"
    "        return await _tg.handle_list(request)\n"
    "\n"
    "    async def _mag_tg_pairing_approve(self, request):\n"
    "        from gateway.platforms import mag_telegram_pairing as _tg\n"
    "        return await _tg.handle_approve(request)\n"
    "\n"
    "    async def _mag_tg_pairing_approve_user(self, request):\n"
    "        from gateway.platforms import mag_telegram_pairing as _tg\n"
    "        return await _tg.handle_approve_user(request)\n"
    "\n"
    "    async def _mag_tg_pairing_revoke(self, request):\n"
    "        from gateway.platforms import mag_telegram_pairing as _tg\n"
    "        return await _tg.handle_revoke(request)\n"
    "\n"
    "    async def _mag_tg_pairing_block(self, request):\n"
    "        from gateway.platforms import mag_telegram_pairing as _tg\n"
    "        return await _tg.handle_block(request)\n"
    "\n"
    "    async def _mag_tg_pairing_unblock(self, request):\n"
    "        from gateway.platforms import mag_telegram_pairing as _tg\n"
    "        return await _tg.handle_unblock(request)\n"
)


def main() -> None:
    if not API_SERVER.exists():
        sys.exit(f"FATAL: api_server.py not found at {API_SERVER}")
    text = API_SERVER.read_text()
    print(f"Patching {API_SERVER} ({MARKER})")
    text = apply(text, OLD_ROUTES, NEW_ROUTES, "register /api/telegram/pairing routes")
    text = apply(text, OLD_METHODS, NEW_METHODS, "wrapper methods")
    API_SERVER.write_text(text)
    print("  api_server Telegram access-control routes patched.")


if __name__ == "__main__":
    main()
