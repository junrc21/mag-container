"""Build-time patch: credit hard cap on client channels (MAG Fase 2).

Blocks a CLIENT-channel turn BEFORE the agent runs when the tenant is out of
credits, replying with a humane message (no engineering leak). The remaining
balance is cached by the usage hook (~/.mag_credits.json); when the cache says
exhausted, the gate re-checks the LIVE balance (GET /internal/runtime/<slug>/
credits) so an admin top-up unblocks on the very next message. Internal staff
surfaces (api_server / local / cli) are NEVER capped.

Recording stays in "measure mode" on the control plane (never blocks the POST);
enforcement lives here, at the runtime, pre-turn — so no LLM cost is spent once
the tenant is over the limit. Fail-open: any error allows the turn.

Idempotent + fail-loud (mirrors the other bootstrap patches).
"""

import os
import pathlib

RUN_PY = pathlib.Path(os.getenv("GATEWAY_RUN_PY", "/opt/hermes/gateway/run.py"))

MARKER = "_mag_credit_block_message"

# --- Edit 1: module-level helpers (injected before a stable top-level def) ------
HELPERS_ANCHOR = "def _gateway_platform_value(platform: Any) -> str:"
HELPERS = '''# MAG: credit hard cap (Fase 2) — block client turns when the tenant is out of
# credits. Balance is cached by the usage hook; re-checked live before blocking.
_MAG_CREDIT_LIMIT_MSG_FREE = (
    "Você usou todos os seus créditos gratuitos. "
    "Para continuar usando a MAG, faça upgrade para um plano pago "
    "em Uso e Plano no painel de controle."
)
_MAG_CREDIT_LIMIT_MSG_PAID = (
    "Você atingiu o limite de créditos do seu plano este mês. "
    "Para continuar agora, reforce seus créditos ou faça upgrade em Uso e Plano "
    "no painel de controle. Se preferir, seus créditos renovam sozinhos no "
    "próximo ciclo."
)


def _mag_credits_path() -> str:
    return os.path.expanduser("~/.mag_credits.json")


def _mag_fetch_credits():
    """Best-effort LIVE balance from the control plane (so a top-up unblocks)."""
    try:
        import json as _json
        import urllib.request as _u
        api = (os.getenv("MAG_API_URL") or "").rstrip("/")
        key = os.getenv("MAG_INTERNAL_KEY") or ""
        slug = os.getenv("MAG_TENANT_SLUG") or ""
        if not api or not key or not slug:
            return None
        req = _u.Request(
            f"{api}/internal/runtime/{slug}/credits",
            headers={"x-internal-key": key},
            method="GET",
        )
        with _u.urlopen(req, timeout=4) as r:
            data = _json.loads(r.read().decode("utf-8"))
        rem = data.get("creditsRemaining")
        try:
            with open(_mag_credits_path(), "w") as f:
                _json.dump({
                    "creditsRemaining": rem,
                    "creditsMax": data.get("creditsMax"),
                    "plan": data.get("plan"),
                }, f)
        except Exception:
            pass
        return rem
    except Exception:
        return None


def _mag_credit_block_message(source):
    """Humane limit message if this client turn must be blocked, else None.
    Internal surfaces are never capped. Fail-open on any error."""
    try:
        plat = source.platform.value if source and getattr(source, "platform", None) else ""
        if plat in ("api_server", "local", "cli"):
            return None
        import json as _json
        path = _mag_credits_path()
        if not os.path.exists(path):
            return None
        with open(path) as f:
            data = _json.load(f)
        rem = data.get("creditsRemaining")
        plan = data.get("plan", "")
        if isinstance(rem, (int, float)) and rem <= 0:
            fresh = _mag_fetch_credits()
            if isinstance(fresh, (int, float)):
                rem = fresh
            # Re-read plan from cache after live refresh (may have been updated)
            try:
                with open(path) as f2:
                    plan = _json.load(f2).get("plan", plan)
            except Exception:
                pass
            if isinstance(rem, (int, float)) and rem <= 0:
                return _MAG_CREDIT_LIMIT_MSG_FREE if plan == "free" else _MAG_CREDIT_LIMIT_MSG_PAID
    except Exception:
        return None
    return None


'''

# --- Edit 2: the gate, right before the agent runs in _handle_message ----------
GATE_ANCHOR = "        self._running_agents[_quick_key] = _AGENT_PENDING_SENTINEL\n"
GATE_BLOCK = (
    "        # MAG: credit hard cap — block out-of-credit client turns before the\n"
    "        # agent runs (humane message, no engineering leak; internal surfaces\n"
    "        # are never capped). Fail-open.\n"
    "        _mag_block = _mag_credit_block_message(source)\n"
    "        if _mag_block is not None:\n"
    "            return _mag_block\n"
)


def main() -> None:
    if not RUN_PY.exists():
        raise SystemExit(f"gateway run.py not found at {RUN_PY}")
    text = RUN_PY.read_text()

    if MARKER in text:
        print("OK: credit hard cap already patched (idempotent no-op)")
        return

    if HELPERS_ANCHOR not in text:
        raise SystemExit("patch_credit_hardcap: helpers anchor missing (Hermes changed).")
    text = text.replace(HELPERS_ANCHOR, HELPERS + HELPERS_ANCHOR, 1)

    if GATE_ANCHOR not in text:
        raise SystemExit("patch_credit_hardcap: gate anchor missing (Hermes changed).")
    text = text.replace(GATE_ANCHOR, GATE_BLOCK + GATE_ANCHOR, 1)

    RUN_PY.write_text(text)
    print("OK: patched credit hard cap (helpers + pre-turn gate)")


if __name__ == "__main__":
    main()
