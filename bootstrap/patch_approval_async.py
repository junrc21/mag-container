"""Build-time patch: async support-approval routing (MAG Phase 2).

When config has ``approvals.mode: "async"``, a dangerous (non-HARDLINE) command
is NOT prompted to the user and does NOT block the chat. Instead the agent:
  1. queues a support-approval request in the MAG control plane, and
  2. returns a humane "pending" message (no execution).

HARDLINE / sudo-stdin floors still block unconditionally (kept above this).
Already-granted patterns (session or permanent allowlist) pass through — that is
how Phase 3's standing grant will auto-allow a command after admin approval.

Idempotent + fail-loud (mirrors the other bootstrap patches).
"""

import os
import pathlib

APPROVAL_PY = pathlib.Path(os.getenv("APPROVAL_PY", "/opt/hermes/tools/approval.py"))

MARKER = "_mag_async_approval_mode"

HELPERS = '''import json as _mag_json
import os as _mag_os
import urllib.request as _mag_urllib

_MAG_ASYNC_PENDING_MSG = (
    "Essa ação precisa de uma validação rápida do suporte. Já enviei o pedido e "
    "te aviso assim que for aprovado."
)


def _mag_async_approval_mode() -> bool:
    try:
        return str(_get_approval_config().get("mode", "")).strip().lower() == "async"
    except Exception:
        return False


def _mag_session_value(name: str) -> str:
    try:
        from gateway.session_context import get_session_env
        return get_session_env(name, "") or ""
    except Exception:
        return ""


def _mag_route_async_approval(command: str, description: str, pattern_key: str, session_key: str) -> None:
    """Best-effort: queue a support-approval request in the MAG control plane."""
    api = (_mag_os.getenv("MAG_API_URL") or "").rstrip("/")
    key = _mag_os.getenv("MAG_INTERNAL_KEY") or ""
    slug = _mag_os.getenv("MAG_TENANT_SLUG") or ""
    if not api or not key or not slug:
        logger.warning("MAG async approval: missing MAG_API_URL/MAG_INTERNAL_KEY/MAG_TENANT_SLUG")
        return
    payload = {
        "actionType": "command",
        "actionTitle": (description or "Comando sensível")[:200],
        "summary": description or "",
        "command": (command or "")[:5000],
        "impact": description or "",
        "riskLevel": "medium",
        "platform": _mag_session_value("HERMES_SESSION_PLATFORM"),
        "chatId": _mag_session_value("HERMES_SESSION_CHAT_ID"),
        "sessionKey": session_key or _mag_session_value("HERMES_SESSION_KEY"),
        "requestedByName": _mag_session_value("HERMES_SESSION_USER_NAME"),
        "patternKey": pattern_key or "",
    }
    try:
        data = _mag_json.dumps(payload).encode("utf-8")
        req = _mag_urllib.Request(
            f"{api}/internal/runtime/{slug}/approvals",
            data=data,
            headers={"content-type": "application/json", "x-internal-key": key},
            method="POST",
        )
        _mag_urllib.urlopen(req, timeout=8).read()
    except Exception as exc:  # best-effort: never crash the agent on a queue hiccup
        logger.warning("MAG async approval POST failed: %s", exc)


def check_all_command_guards(command: str, env_type: str,'''

HELPERS_ANCHOR = "def check_all_command_guards(command: str, env_type: str,"

ROUTE_ANCHOR = "    # --yolo or approvals.mode=off: bypass all approval prompts.\n"
ROUTE_BLOCK = '''    # MAG: async support-approval routing (approvals.mode == "async"). Dangerous
    # commands are queued to the CyriusX support panel; the user is never prompted.
    if _mag_async_approval_mode():
        _mag_is_dangerous, _mag_pk, _mag_desc = detect_dangerous_command(command)
        if not _mag_is_dangerous:
            return {"approved": True, "message": None}
        _mag_skey = _mag_session_value("HERMES_SESSION_KEY")
        if is_approved(_mag_skey, _mag_pk):
            return {"approved": True, "message": None}
        _mag_route_async_approval(command, _mag_desc, _mag_pk, _mag_skey)
        return {"approved": False, "message": _MAG_ASYNC_PENDING_MSG}

'''


def main() -> None:
    if not APPROVAL_PY.exists():
        raise SystemExit(f"approval.py not found at {APPROVAL_PY}")
    text = APPROVAL_PY.read_text()

    if MARKER in text:
        print("OK: approval async routing already patched (idempotent no-op)")
        return

    # Insert helpers immediately before check_all_command_guards (first occurrence
    # is the def; HELPERS re-opens that def line so we keep it intact).
    if text.count(HELPERS_ANCHOR) < 1:
        raise SystemExit("patch_approval_async: check_all_command_guards anchor missing.")
    text = text.replace(HELPERS_ANCHOR, HELPERS, 1)

    # Insert the routing block at the top of the decision logic.
    if ROUTE_ANCHOR not in text:
        raise SystemExit("patch_approval_async: route anchor missing (Hermes changed).")
    text = text.replace(ROUTE_ANCHOR, ROUTE_BLOCK + ROUTE_ANCHOR, 1)

    APPROVAL_PY.write_text(text)
    print(f"OK: patched {APPROVAL_PY} (async support-approval routing)")


if __name__ == "__main__":
    main()
