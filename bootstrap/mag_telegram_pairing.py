"""MAG: Telegram pairing approval surface for the web.

When an un-allowlisted user DMs the bot, Hermes' pairing system DMs them an 8-char code
and waits for `hermes pairing approve telegram <code>`. This module exposes that approval
over HTTP so the MAG control plane (and the client's panel) can drive it from the web,
without SSH/CLI:

  GET  /api/telegram/pairing          → { pending: [...], approved: [...] }
  POST /api/telegram/pairing/approve  → body {code}     → approve the code the bot DM'd
                                        the user; they gain access immediately (no restart).
  POST /api/telegram/pairing/revoke   → body {user_id}  → remove an approved user.

Delegates to Hermes' own gateway.pairing.PairingStore (file-based, ~/.hermes/pairing/),
so it shares the EXACT store the live Telegram adapter checks on every message — approving
here takes effect on the user's next message. File I/O runs in a thread so the gateway
event loop (which also serves every message) is never blocked. Only the MAG control plane
(Authorization: Bearer <MAG_INTERNAL_KEY>) may call these.
"""

import asyncio
import os

from aiohttp import web

PLATFORM = "telegram"

try:
    from gateway.pairing import PairingStore

    _store = PairingStore()
except Exception:  # pragma: no cover — pairing module missing/changed
    _store = None


def _authorized(request) -> bool:
    """Only the MAG control plane may drive pairing (Bearer <MAG_INTERNAL_KEY> or the
    x-internal-key header). If the key isn't set (local dev), allow — mirrors the
    WhatsApp pairing controller's fail-open-for-dev behavior."""
    key = os.environ.get("MAG_INTERNAL_KEY") or ""
    if not key:
        return True
    auth = request.headers.get("Authorization", "")
    return auth == f"Bearer {key}" or request.headers.get("x-internal-key", "") == key


def _deny():
    return web.json_response({"error": "unauthorized"}, status=401)


async def handle_list(request):
    if not _authorized(request):
        return _deny()
    if _store is None:
        return web.json_response({"pending": [], "approved": [], "error": "pairing_unavailable"})
    pending = await asyncio.to_thread(_store.list_pending, PLATFORM)
    approved = await asyncio.to_thread(_store.list_approved, PLATFORM)
    return web.json_response({"pending": pending, "approved": approved})


async def handle_approve(request):
    if not _authorized(request):
        return _deny()
    try:
        body = await request.json()
    except Exception:
        body = {}
    code = str((body or {}).get("code", "")).strip()
    if not code:
        return web.json_response({"ok": False, "error": "missing_code"}, status=400)
    if _store is None:
        return web.json_response({"ok": False, "error": "pairing_unavailable"}, status=503)
    # PairingStore.approve_code returns {user_id, user_name} on success, or None when the
    # code is invalid/expired OR the platform is locked out after too many failed tries.
    result = await asyncio.to_thread(_store.approve_code, PLATFORM, code)
    if result:
        return web.json_response({"ok": True, "user": result})
    return web.json_response({"ok": False, "error": "invalid_or_expired"})


async def handle_revoke(request):
    if not _authorized(request):
        return _deny()
    try:
        body = await request.json()
    except Exception:
        body = {}
    user_id = str((body or {}).get("user_id", "")).strip()
    if not user_id:
        return web.json_response({"ok": False, "error": "missing_user_id"}, status=400)
    if _store is None:
        return web.json_response({"ok": False, "error": "pairing_unavailable"}, status=503)
    ok = await asyncio.to_thread(_store.revoke, PLATFORM, user_id)
    return web.json_response({"ok": bool(ok)})
