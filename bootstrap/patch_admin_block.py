"""Build-time patch: admin "Bloquear acesso" hard-stop on client channels.

Blocks a CLIENT-channel turn BEFORE the agent runs when staff has blocked this
tenant from the Control Center's Manutenção page. Unlike the credit hard cap
(which reads a locally-cached balance file, refreshed live only once the cache
says exhausted, since credits change every turn), block state changes rarely —
this checks GET /internal/runtime/<slug>/blocked LIVE on every client-channel
turn, so a block (or unblock) takes effect immediately, with no propagation
delay or stale-cache window. Internal staff surfaces (api_server/local/cli) are
NEVER blocked — the admin god-mode chat must keep working to investigate/
communicate about the block itself. Fail-open: any error allows the turn (never
let a control-plane hiccup lock every client out of their own MAG).

Idempotent + fail-loud (mirrors the other bootstrap patches).
"""

import os
import pathlib

RUN_PY = pathlib.Path(os.getenv("GATEWAY_RUN_PY", "/opt/hermes/gateway/run.py"))

MARKER = "_mag_admin_block_message"

# --- Edit 1: module-level helpers (injected before a stable top-level def) ------
HELPERS_ANCHOR = "def _gateway_platform_value(platform: Any) -> str:"
HELPERS = '''# MAG: admin block — hard-stop client turns for a tenant staff has blocked from
# the Control Center. Checked LIVE (no local cache) since block state changes
# rarely — unlike credits, there is no per-turn write to piggyback a cache on.
_MAG_ADMIN_BLOCK_DEFAULT_MSG = (
    "Seu acesso a MAG esta temporariamente indisponivel. "
    "Entre em contato com o suporte da CyriusX para mais informacoes."
)


def _mag_admin_block_message(source):
    """Humane block message if this client turn must be stopped, else None.
    Internal surfaces are never blocked. Fail-open on any error."""
    try:
        plat = source.platform.value if source and getattr(source, "platform", None) else ""
        if plat in ("api_server", "local", "cli"):
            return None
        import json as _json
        import urllib.request as _u
        api = (os.getenv("MAG_API_URL") or "").rstrip("/")
        key = os.getenv("MAG_INTERNAL_KEY") or ""
        slug = os.getenv("MAG_TENANT_SLUG") or ""
        if not api or not key or not slug:
            return None
        req = _u.Request(
            f"{api}/internal/runtime/{slug}/blocked",
            headers={"x-internal-key": key},
            method="GET",
        )
        with _u.urlopen(req, timeout=4) as r:
            data = _json.loads(r.read().decode("utf-8"))
        if data.get("blocked") is True:
            return _MAG_ADMIN_BLOCK_DEFAULT_MSG
    except Exception:
        return None
    return None


'''

# --- Edit 2: the gate, right before the agent runs in _handle_message ----------
# Placed FIRST among the pre-turn gates (this patch runs before credit_hardcap
# and forbidden_topics_gate in the Dockerfile) — a blocked tenant shouldn't pay
# for those checks either.
GATE_ANCHOR = "        self._running_agents[_quick_key] = _AGENT_PENDING_SENTINEL\n"
GATE_BLOCK = (
    "        # MAG: admin block — hard-stop before the agent runs if staff has\n"
    "        # blocked this tenant from the Control Center. Internal surfaces are\n"
    "        # exempt (god-mode chat must keep working). Fail-open.\n"
    "        _mag_admin_block = _mag_admin_block_message(source)\n"
    "        if _mag_admin_block is not None:\n"
    "            return _mag_admin_block\n"
)


def main() -> None:
    if not RUN_PY.exists():
        raise SystemExit(f"gateway run.py not found at {RUN_PY}")
    text = RUN_PY.read_text()

    if MARKER in text:
        print("OK: admin block already patched (idempotent no-op)")
        return

    if HELPERS_ANCHOR not in text:
        raise SystemExit("patch_admin_block: helpers anchor missing (Hermes changed).")
    text = text.replace(HELPERS_ANCHOR, HELPERS + HELPERS_ANCHOR, 1)

    if GATE_ANCHOR not in text:
        raise SystemExit("patch_admin_block: gate anchor missing (Hermes changed).")
    text = text.replace(GATE_ANCHOR, GATE_BLOCK + GATE_ANCHOR, 1)

    RUN_PY.write_text(text)
    print("OK: patched admin block (helpers + pre-turn gate)")


if __name__ == "__main__":
    main()
