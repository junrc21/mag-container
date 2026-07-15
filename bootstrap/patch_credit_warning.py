"""Build-time patch: 80%-of-quota credit warning (MAG Fase 2), sibling to
patch_credit_hardcap.py.

Appends a short heads-up to the tenant's OWN reply for whichever turn happens
to run once the cached balance shows >=80% of quota consumed — never a
separately-scheduled/proactive message. This matters specifically for
WhatsApp: the Cloud API only allows free-form (non-template) messages as a
reply inside the ~24h customer-service window; a same-turn append is always
safe on both channels because it's attached to a reply the tenant just
triggered by messaging.

Balance is read (read-only) from the same ~/.mag_credits.json the hardcap
patch and the control-plane usage hook already maintain — this patch never
writes to that file (both existing writers do full-object overwrites, so any
field added here would get silently dropped). Dedup/self-reset state instead
lives in its own file, ~/.mag_credit_warning_state.json, owned exclusively by
this patch: "shown" is cleared automatically whenever creditsRemaining goes
up or creditsMax changes since the last observed snapshot (a top-up/upgrade),
so no control-plane coordination is needed to re-arm it.

Anchored right before the existing agent:end hook emit inside
_handle_message_with_agent, where `response` (the turn's final reply text)
and `source` (platform) are already in scope, and where an existing feature
(the optional runtime-footer line) already establishes the exact safe-append
guard this patch reuses: response truthy, not agent_result["already_sent"]
(streaming may have already sent the un-appended text), not
_intentional_silence. Internal staff surfaces (api_server/local/cli) are
never shown this.

Idempotent + fail-loud (mirrors the other bootstrap patches).
"""

import os
import pathlib

RUN_PY = pathlib.Path(os.getenv("GATEWAY_RUN_PY", "/opt/hermes/gateway/run.py"))

MARKER = "_mag_credit_warning_suffix"

# --- Edit 1: module-level helpers (injected before a stable top-level def) ------
HELPERS_ANCHOR = "def _gateway_platform_value(platform: Any) -> str:"
HELPERS = '''# MAG: 80%-of-quota credit warning (Fase 2) — appended to this exact turn's
# own reply only (never a separate/proactive push). See module docstring.
_MAG_CREDIT_WARNING_SUFFIX = (
    "\\n\\nIh, você já consumiu 80% da sua cota de créditos deste ciclo. "
    "Se quiser reforçar, é em Uso e Plano no painel."
)


def _mag_credit_warning_suffix(source):
    """Suffix to append to this turn's response if the tenant just crossed
    80% of quota and hasn't been shown the heads-up yet this cycle, else None.
    Read-only against ~/.mag_credits.json; dedup state lives in its own file
    so it never collides with that file's other writers. Fail-open."""
    try:
        plat = source.platform.value if source and getattr(source, "platform", None) else ""
        if plat in ("api_server", "local", "cli"):
            return None

        import json as _json

        credits_path = os.path.expanduser("~/.mag_credits.json")
        if not os.path.exists(credits_path):
            return None
        with open(credits_path) as f:
            data = _json.load(f)
        rem = data.get("creditsRemaining")
        mx = data.get("creditsMax")
        if not isinstance(rem, (int, float)) or not isinstance(mx, (int, float)) or mx <= 0:
            return None
        if rem <= 0 or (rem / mx) > 0.2:
            return None

        state_path = os.path.expanduser("~/.mag_credit_warning_state.json")
        state = {}
        if os.path.exists(state_path):
            try:
                with open(state_path) as f:
                    state = _json.load(f)
            except Exception:
                state = {}

        # Self-reset: remaining going UP or max changing since our last
        # snapshot means a top-up/upgrade happened — re-arm for the new cycle.
        if rem > state.get("lastSeenRemaining", -1) or mx != state.get("lastSeenMax"):
            state = {"shown": False}

        already_shown = bool(state.get("shown"))
        state["lastSeenRemaining"] = rem
        state["lastSeenMax"] = mx
        if not already_shown:
            state["shown"] = True
        try:
            with open(state_path, "w") as f:
                _json.dump(state, f)
        except Exception:
            pass

        return None if already_shown else _MAG_CREDIT_WARNING_SUFFIX
    except Exception:
        return None


'''

# --- Edit 2: append the suffix right before the agent:end hook emit ------------
GATE_ANCHOR = (
    '            # Emit agent:end hook\n'
    '            await self.hooks.emit("agent:end", {\n'
    '                **hook_ctx,\n'
)
GATE_BLOCK = (
    "            # MAG: 80%-of-quota credit warning — appended to this exact\n"
    "            # turn's own reply only (never a separate/proactive push).\n"
    '            if response and not agent_result.get("already_sent") and not _intentional_silence:\n'
    "                _mag_credit_warn = _mag_credit_warning_suffix(source)\n"
    "                if _mag_credit_warn:\n"
    '                    response = f"{response}{_mag_credit_warn}"\n'
    "\n"
)


def main() -> None:
    if not RUN_PY.exists():
        raise SystemExit(f"gateway run.py not found at {RUN_PY}")
    text = RUN_PY.read_text(encoding="utf-8")

    if MARKER in text:
        print("OK: credit warning suffix already patched (idempotent no-op)")
        return

    if HELPERS_ANCHOR not in text:
        raise SystemExit("patch_credit_warning: helpers anchor missing (Hermes changed).")
    text = text.replace(HELPERS_ANCHOR, HELPERS + HELPERS_ANCHOR, 1)

    if GATE_ANCHOR not in text:
        raise SystemExit("patch_credit_warning: agent:end emit anchor missing (Hermes changed).")
    text = text.replace(GATE_ANCHOR, GATE_BLOCK + GATE_ANCHOR, 1)

    RUN_PY.write_text(text, encoding="utf-8")
    print("OK: patched credit warning suffix (helpers + pre-emit append)")


if __name__ == "__main__":
    main()
