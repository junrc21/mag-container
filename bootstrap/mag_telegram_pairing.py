"""MAG: Telegram access-control surface for the web (pairing + allow + block).

When an un-allowlisted user DMs the bot, Hermes' pairing system DMs them an 8-char code
and waits for `hermes pairing approve telegram <code>`. This module exposes the full
access-control surface over HTTP so the MAG control plane (and the client's panel) can
drive it from the web, without SSH/CLI:

  GET  /api/telegram/pairing            → { pending: [...], approved: [...], blocked: [...] }
  POST /api/telegram/pairing/approve    → body {code}             → approve the code the bot
                                          DM'd the user (immediate, no restart).
  POST /api/telegram/pairing/approve_user → body {user_id, user_name?} → approve a KNOWN id
                                          directly (no code needed); used by the panel's
                                          "add ID" + the pending "Approve" button. Clears any
                                          block + stale pending entry for that user.
  POST /api/telegram/pairing/revoke     → body {user_id}          → remove an approved user.
  POST /api/telegram/pairing/block      → body {user_id, user_name?} → deny a user: revoke
                                          access, drop their pending request, and add them to
                                          the block list. Blocked users are denied even if
                                          they appear in TELEGRAM_ALLOWED_USERS and are never
                                          re-issued a pairing code.
  POST /api/telegram/pairing/unblock    → body {user_id}          → lift a block.

Authorization (allow path):
Delegates to Hermes' own gateway.pairing.PairingStore (file-based, ~/.hermes/pairing/),
so it shares the EXACT store the live Telegram adapter checks on every message — approving
here takes effect on the user's next message.

Block path:
The block list lives alongside the pairing files (``{platform}-blocked.json``, same
~/.hermes/pairing/ dir → /opt/data, persisted). It is consulted by ``patch_authz_blocklist.py``
inside Hermes core (`_is_user_authorized` and the unauthorized-DM handler) — see that patch.

File I/O runs in a thread so the gateway event loop (which also serves every message) is
never blocked. Only the MAG control plane (Authorization: Bearer <MAG_INTERNAL_KEY>) may
call these.
"""

import asyncio
import json
import os
import threading
import time

from aiohttp import web

PLATFORM = "telegram"

try:
    from gateway.pairing import PairingStore, PAIRING_DIR, _secure_write

    _store = PairingStore()
except Exception:  # pragma: no cover — pairing module missing/changed
    _store = None
    PAIRING_DIR = None
    _secure_write = None


# --------------------------------------------------------------------------- #
# Block list — self-contained, stored next to the pairing files.
# --------------------------------------------------------------------------- #
_block_lock = threading.RLock()


def _blocked_path(platform: str):
    return PAIRING_DIR / f"{platform}-blocked.json"


def _load_blocked(platform: str) -> dict:
    if PAIRING_DIR is None:
        return {}
    path = _blocked_path(platform)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def is_blocked(platform: str, user_id: str) -> bool:
    """Return True when ``user_id`` is on the block list for ``platform``.

    Called from Hermes core on every inbound message (via the authz patch), so it
    must be cheap and fail-open: on any error it returns False (an unreadable block
    file must not lock out every user). Telegram user IDs are plain numeric strings,
    so a direct membership test is sufficient.
    """
    uid = str(user_id or "").strip()
    if not uid:
        return False
    try:
        return uid in _load_blocked(platform)
    except Exception:
        return False


def list_blocked(platform: str) -> list:
    blocked = _load_blocked(platform)
    results = []
    for uid, info in blocked.items():
        entry = {"user_id": uid}
        if isinstance(info, dict):
            entry.update(info)
        results.append(entry)
    return results


def block_user(platform: str, user_id: str, user_name: str = "") -> bool:
    uid = str(user_id or "").strip()
    if not uid or PAIRING_DIR is None or _secure_write is None:
        return False
    with _block_lock:
        blocked = _load_blocked(platform)
        blocked[uid] = {"user_name": user_name or "", "blocked_at": time.time()}
        _secure_write(
            _blocked_path(platform),
            json.dumps(blocked, indent=2, ensure_ascii=False),
        )
    return True


def unblock_user(platform: str, user_id: str) -> bool:
    uid = str(user_id or "").strip()
    if not uid or PAIRING_DIR is None or _secure_write is None:
        return False
    with _block_lock:
        blocked = _load_blocked(platform)
        if uid in blocked:
            del blocked[uid]
            _secure_write(
                _blocked_path(platform),
                json.dumps(blocked, indent=2, ensure_ascii=False),
            )
            return True
    return False


# --------------------------------------------------------------------------- #
# Approved-store helpers that the stock PairingStore doesn't expose publicly.
# --------------------------------------------------------------------------- #
def _approve_user_locked(user_id: str, user_name: str = "") -> None:
    """Add a known user id straight to the approved list (no pairing code)."""
    with _store._lock:
        _store._approve_user(PLATFORM, user_id, user_name)


def _prune_pending_for_user(platform: str, user_id: str) -> None:
    """Drop any pending pairing requests for ``user_id`` (after approve/block) so the
    panel's pending list doesn't keep showing a user who's already decided on."""
    uid = str(user_id or "").strip()
    if not uid or _store is None:
        return
    with _store._lock:
        pending = _store._load_json(_store._pending_path(platform))
        removed = False
        for entry_id in list(pending.keys()):
            info = pending.get(entry_id)
            if isinstance(info, dict) and str(info.get("user_id", "")).strip() == uid:
                del pending[entry_id]
                removed = True
        if removed:
            _store._save_json(_store._pending_path(platform), pending)


# --------------------------------------------------------------------------- #
# HTTP handlers
# --------------------------------------------------------------------------- #
def _authorized(request) -> bool:
    """Only the MAG control plane may drive access control (Bearer <MAG_INTERNAL_KEY>
    or the x-internal-key header). If the key isn't set (local dev), allow — mirrors
    the WhatsApp pairing controller's fail-open-for-dev behavior."""
    key = os.environ.get("MAG_INTERNAL_KEY") or ""
    if not key:
        return True
    auth = request.headers.get("Authorization", "")
    return auth == f"Bearer {key}" or request.headers.get("x-internal-key", "") == key


def _deny():
    return web.json_response({"error": "unauthorized"}, status=401)


async def _body(request) -> dict:
    try:
        body = await request.json()
    except Exception:
        body = {}
    return body or {}


async def handle_list(request):
    if not _authorized(request):
        return _deny()
    if _store is None:
        return web.json_response(
            {"pending": [], "approved": [], "blocked": [], "error": "pairing_unavailable"}
        )
    pending = await asyncio.to_thread(_store.list_pending, PLATFORM)
    approved = await asyncio.to_thread(_store.list_approved, PLATFORM)
    blocked = await asyncio.to_thread(list_blocked, PLATFORM)
    return web.json_response({"pending": pending, "approved": approved, "blocked": blocked})


async def handle_approve(request):
    if not _authorized(request):
        return _deny()
    body = await _body(request)
    code = str(body.get("code", "")).strip()
    if not code:
        return web.json_response({"ok": False, "error": "missing_code"}, status=400)
    if _store is None:
        return web.json_response({"ok": False, "error": "pairing_unavailable"}, status=503)
    # PairingStore.approve_code returns {user_id, user_name} on success, or None when the
    # code is invalid/expired OR the platform is locked out after too many failed tries.
    result = await asyncio.to_thread(_store.approve_code, PLATFORM, code)
    if result:
        # A freshly-approved user must not stay on the block list.
        await asyncio.to_thread(unblock_user, PLATFORM, result.get("user_id", ""))
        return web.json_response({"ok": True, "user": result})
    return web.json_response({"ok": False, "error": "invalid_or_expired"})


async def handle_approve_user(request):
    """Approve a KNOWN user id directly (no pairing code). Used by the panel's
    "add ID" field and the pending "Approve" button."""
    if not _authorized(request):
        return _deny()
    body = await _body(request)
    user_id = str(body.get("user_id", "")).strip()
    user_name = str(body.get("user_name", "") or "")
    if not user_id:
        return web.json_response({"ok": False, "error": "missing_user_id"}, status=400)
    if _store is None:
        return web.json_response({"ok": False, "error": "pairing_unavailable"}, status=503)
    # Approving wins over a prior block, and clears any pending request for them.
    await asyncio.to_thread(unblock_user, PLATFORM, user_id)
    await asyncio.to_thread(_approve_user_locked, user_id, user_name)
    await asyncio.to_thread(_prune_pending_for_user, PLATFORM, user_id)
    return web.json_response({"ok": True, "user": {"user_id": user_id, "user_name": user_name}})


async def handle_revoke(request):
    if not _authorized(request):
        return _deny()
    body = await _body(request)
    user_id = str(body.get("user_id", "")).strip()
    if not user_id:
        return web.json_response({"ok": False, "error": "missing_user_id"}, status=400)
    if _store is None:
        return web.json_response({"ok": False, "error": "pairing_unavailable"}, status=503)
    ok = await asyncio.to_thread(_store.revoke, PLATFORM, user_id)
    return web.json_response({"ok": bool(ok)})


async def handle_block(request):
    """Deny a user: revoke access, drop their pending request, add to the block list."""
    if not _authorized(request):
        return _deny()
    body = await _body(request)
    user_id = str(body.get("user_id", "")).strip()
    user_name = str(body.get("user_name", "") or "")
    if not user_id:
        return web.json_response({"ok": False, "error": "missing_user_id"}, status=400)
    if _store is not None:
        await asyncio.to_thread(_store.revoke, PLATFORM, user_id)
        await asyncio.to_thread(_prune_pending_for_user, PLATFORM, user_id)
    ok = await asyncio.to_thread(block_user, PLATFORM, user_id, user_name)
    return web.json_response({"ok": bool(ok)})


async def handle_unblock(request):
    if not _authorized(request):
        return _deny()
    body = await _body(request)
    user_id = str(body.get("user_id", "")).strip()
    if not user_id:
        return web.json_response({"ok": False, "error": "missing_user_id"}, status=400)
    ok = await asyncio.to_thread(unblock_user, PLATFORM, user_id)
    return web.json_response({"ok": bool(ok)})
