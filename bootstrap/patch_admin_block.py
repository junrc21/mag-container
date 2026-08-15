"""Build-time patch: admin "Bloquear acesso" hard-stop on client channels.

Blocks a CLIENT-channel turn BEFORE the agent runs when staff has blocked this
tenant from the Control Center. Internal staff surfaces (api_server/local/cli)
are NEVER blocked — the admin god-mode chat must keep working to investigate and
communicate about the block itself.

## Why this stopped being fail-open

The first version allowed the turn on ANY error, with a good argument: a
control-plane hiccup should not lock every client out of their own MAG. The cost
of that choice was the opposite failure — a blocked tenant kept being served for
as long as the API was unreachable, which is exactly the window an unhappy
client is most likely to be pushing.

Neither answer is right, because the question was wrong. Block state is *sticky*
— it changes once a month, not once a turn — so it can be cached on disk like
the credit balance already is. The rule now:

  * API answers            -> use it, and remember it
  * API silent, cache warm -> use the last known answer (an outage does not
                              unblock anyone, and does not block anyone either)
  * API silent, no cache   -> block

The last case only affects a container that has never once reached the control
plane. Serving a tenant we have never been able to verify is the one situation
where being wrong is unbounded.

## Why the client is never told why

Product decision, explicit: the reason for a block is CyriusX-internal. The API
returns `reason` and this patch reads it only to ignore it. The message says the
request could not be processed and to contact support — nothing about
suspension, billing or accounts. See `acesso.rules.ts` in the control plane for
the same rule on the panel side.

Idempotent + fail-loud (mirrors the other bootstrap patches).
"""

import os
import pathlib

RUN_PY = pathlib.Path(os.getenv("GATEWAY_RUN_PY", "/opt/hermes/gateway/run.py"))

MARKER = "_mag_admin_block_message"

# --- Edit 1: module-level helpers (injected before a stable top-level def) ------
HELPERS_ANCHOR = "def _gateway_platform_value(platform: Any) -> str:"
HELPERS = '''# MAG: admin block — hard-stop client turns for a tenant staff has blocked from
# the Control Center. A regra (live + cache em disco, fail-closed) mora em
# `mag_block_guard`, compartilhada com o agendador de rotinas: quando ela existia
# só aqui, as rotinas continuavam rodando e ENTREGANDO mensagem para quem estava
# bloqueado.
from mag_block_guard import AccessStatus as _MagAccessStatus
from mag_block_guard import BLOCKED_MESSAGE as _MAG_ADMIN_BLOCK_DEFAULT_MSG
from mag_block_guard import check_tenant_access as _mag_check_tenant_access


def _mag_admin_block_message(source):
    """Block message if this client turn must be stopped, else None.
    Internal surfaces (api_server/local/cli) are never blocked — the admin
    god-mode chat has to keep working to investigate the block itself."""
    try:
        # `_gateway_platform_value` tolera `platform` chegando como str simples.
        # O `source.platform.value` anterior levantava AttributeError nesse caso,
        # caía no except e liberava o turno — um fail-open escondido dentro do
        # que parecia um detalhe de tipo.
        plat = _gateway_platform_value(getattr(source, "platform", None)) if source else ""
        if plat in ("api_server", "local", "cli"):
            return None
        check = _mag_check_tenant_access()
        return _MAG_ADMIN_BLOCK_DEFAULT_MSG if check.status is _MagAccessStatus.BLOCKED else None
    except Exception:
        # Falha imprevista no nosso próprio código bloqueia, não serve.
        return _MAG_ADMIN_BLOCK_DEFAULT_MSG


'''

# --- Edit 2: the gate, right before the agent runs in _handle_message ----------
# Placed FIRST among the pre-turn gates (this patch runs before credit_hardcap
# and forbidden_topics_gate in the Dockerfile) — a blocked tenant shouldn't pay
# for those checks either.
GATE_ANCHOR = "        self._running_agents[_quick_key] = _AGENT_PENDING_SENTINEL\n"
GATE_BLOCK = (
    "        # MAG: admin block — hard-stop before the agent runs if staff has\n"
    "        # blocked this tenant from the Control Center. Internal surfaces are\n"
    "        # exempt (god-mode chat must keep working). Fail-closed.\n"
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
