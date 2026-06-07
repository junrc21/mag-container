"""MAG: on-demand WhatsApp pairing controller for the web QR flow.

Self-contained gateway module (NOT patching the big api_server class — the api_server
only gets 4 thin wrapper routes that delegate here). Drives the hardened Baileys bridge
(patch_whatsapp_bridge.py) to pair from the web:

  - /api/whatsapp/pair   → ensure a bridge is running (probe the adapter's bridge; if
                           none, spawn one — npm install on first use), using the SAME
                           session dir the WhatsApp adapter uses, so creds land where the
                           adapter will read them.
  - /api/whatsapp/qr     → proxy the bridge's /qr and return the QR as a PNG data-URL.
  - /api/whatsapp/status → proxy the bridge's /status (qr|connecting|connected|logged_out|error|idle).
  - /api/whatsapp/logout → stop our bridge + wipe the session.

After a successful scan, creds.json lands in the shared session dir; the control plane
sets WHATSAPP_ENABLED=true + reloads, and the normal adapter takes over on next boot
(this pairing bridge is ephemeral and dies with the reload-restart).

All blocking I/O (HTTP probe, npm, spawn) runs in a thread executor so the gateway event
loop — which also serves every message — is never blocked.
"""

import asyncio
import base64
import io
import json
import os
import shutil
import subprocess
import urllib.request
from pathlib import Path

from aiohttp import web

try:
    from hermes_constants import get_hermes_dir

    SESSION_DIR = Path(get_hermes_dir("platforms/whatsapp/session", "whatsapp/session"))
except Exception:  # pragma: no cover — fall back to the known default
    SESSION_DIR = Path(os.environ.get("HOME", "/opt/data")) / "platforms" / "whatsapp" / "session"

BRIDGE_DIR = "/opt/hermes/scripts/whatsapp-bridge"
BRIDGE_JS = f"{BRIDGE_DIR}/bridge.js"
PORT = int(os.environ.get("WHATSAPP_BRIDGE_PORT", "3000"))
BASE = f"http://127.0.0.1:{PORT}"

_proc = None  # the pairing bridge process we spawned (if any)


# ── blocking helpers (always called via run_in_executor) ─────────────────────
def _http_get(path: str, timeout: float = 3.0):
    try:
        with urllib.request.urlopen(f"{BASE}{path}", timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def _bridge_alive() -> bool:
    return _http_get("/status", timeout=2) is not None


def _ensure_npm() -> None:
    marker = Path(BRIDGE_DIR) / "node_modules" / "@whiskeysockets" / "baileys"
    if marker.exists():
        return
    subprocess.run(
        ["npm", "install", "--no-audit", "--no-fund"],
        cwd=BRIDGE_DIR,
        timeout=int(os.environ.get("WHATSAPP_NPM_INSTALL_TIMEOUT", "300")),
        capture_output=True,
    )


def _spawn_bridge() -> None:
    """Spawn the hardened bridge (normal mode → serves QR, connects, stays up reporting
    status). Only call when no bridge is already listening on PORT."""
    global _proc
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    _ensure_npm()
    env = dict(os.environ)
    env.setdefault("WHATSAPP_MODE", "bot")  # avoid self-chat behavior during the brief pairing window
    _proc = subprocess.Popen(
        ["node", BRIDGE_JS, "--port", str(PORT), "--session", str(SESSION_DIR)],
        cwd=BRIDGE_DIR,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _stop_and_wipe() -> None:
    global _proc
    try:
        if _proc is not None:
            _proc.terminate()
    except Exception:
        pass
    _proc = None
    try:
        if SESSION_DIR.exists():
            shutil.rmtree(SESSION_DIR)
    except Exception:
        pass


def _qr_png_dataurl(qr_str: str):
    try:
        import qrcode

        img = qrcode.make(qr_str)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return None


async def _run(fn, *args):
    return await asyncio.get_event_loop().run_in_executor(None, fn, *args)


def _authed(request) -> bool:
    """Only the MAG control plane may drive pairing. It sends Authorization: Bearer
    <MAG_INTERNAL_KEY>. If the key isn't set (local dev), allow (fail-open for dev)."""
    key = os.environ.get("MAG_INTERNAL_KEY") or ""
    if not key:
        return True
    auth = request.headers.get("Authorization", "")
    return auth == f"Bearer {key}" or request.headers.get("x-internal-key", "") == key


async def _ensure_bridge() -> None:
    if await _run(_bridge_alive):
        return  # adapter's bridge (or a previous pairing bridge) is already up — reuse it
    await _run(_spawn_bridge)
    for _ in range(40):  # wait up to ~20s for it to answer
        if await _run(_bridge_alive):
            return
        await asyncio.sleep(0.5)


# ── route handlers (thin; called by the api_server wrappers) ─────────────────
async def handle_pair(request):
    if not _authed(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        await _ensure_bridge()
        st = await _run(_http_get, "/status") or {}
        return web.json_response({"ok": True, "status": st.get("status") or "starting"})
    except Exception as e:  # never 500 the gateway over a pairing attempt
        return web.json_response({"ok": False, "error": str(e)}, status=200)


async def handle_qr(request):
    if not _authed(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        await _ensure_bridge()
        data = await _run(_http_get, "/qr") or {}
        qr = data.get("qr")
        png = await _run(_qr_png_dataurl, qr) if qr else None
        return web.json_response({"qr": png, "status": data.get("status") or "starting"})
    except Exception as e:
        return web.json_response({"qr": None, "status": "error", "error": str(e)}, status=200)


async def handle_status(request):
    if not _authed(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    data = await _run(_http_get, "/status")
    if data is None:
        return web.json_response({"status": "idle"})
    return web.json_response(data)


async def handle_logout(request):
    if not _authed(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    await _run(_stop_and_wipe)
    return web.json_response({"ok": True})
