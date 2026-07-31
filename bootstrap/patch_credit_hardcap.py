"""Build-time patch: authoritative, fail-closed credit gate for client chat."""

import os
import pathlib

RUN_PY = pathlib.Path(os.getenv("GATEWAY_RUN_PY", "/opt/hermes/gateway/run.py"))
MARKER = "MAG: authoritative credit hard cap"

IMPORT_ANCHOR = "def _gateway_platform_value(platform: Any) -> str:"
IMPORT_BLOCK = '''# MAG: authoritative credit hard cap — shared with cron.
from mag_credit_guard import CreditStatus as _MagCreditStatus
from mag_credit_guard import check_authoritative_credits as _mag_check_authoritative_credits

_MAG_CREDIT_LIMIT_MSG_FREE = (
    "Você usou todos os seus créditos gratuitos. Para continuar usando a MAG, "
    "faça upgrade para um plano pago em Uso e Plano no painel de controle."
)
_MAG_CREDIT_LIMIT_MSG_PAID = (
    "Você atingiu o limite de créditos do seu plano este mês. Para continuar agora, "
    "reforce seus créditos ou faça upgrade em Uso e Plano no painel de controle."
)
_MAG_CREDIT_VERIFY_MSG = (
    "Não foi possível verificar seus créditos agora. Tente novamente em instantes."
)


def _mag_credit_block_message(source):
    """Return a client-safe block message, or None only when credit is available."""
    platform = _gateway_platform_value(getattr(source, "platform", ""))
    if platform in ("api_server", "local", "cli"):
        return None
    check = _mag_check_authoritative_credits()
    if check.status is _MagCreditStatus.AVAILABLE:
        return None
    if check.status is _MagCreditStatus.EXHAUSTED:
        return _MAG_CREDIT_LIMIT_MSG_FREE if check.plan == "free" else _MAG_CREDIT_LIMIT_MSG_PAID
    return _MAG_CREDIT_VERIFY_MSG


'''

GATE_ANCHOR = "        self._running_agents[_quick_key] = _AGENT_PENDING_SENTINEL\n"
GATE_BLOCK = (
    "        # MAG: authoritative credit hard cap — central check before agent execution.\n"
    "        _mag_block = _mag_credit_block_message(source)\n"
    "        if _mag_block is not None:\n"
    "            return _mag_block\n"
)


def main() -> None:
    if not RUN_PY.exists():
        raise SystemExit(f"gateway run.py not found at {RUN_PY}")
    text = RUN_PY.read_text(encoding="utf-8")
    if MARKER in text:
        print("OK: authoritative credit hard cap already patched (idempotent no-op)")
        return
    if IMPORT_ANCHOR not in text:
        raise SystemExit("patch_credit_hardcap: import anchor missing (Hermes changed).")
    if GATE_ANCHOR not in text:
        raise SystemExit("patch_credit_hardcap: gate anchor missing (Hermes changed).")
    text = text.replace(IMPORT_ANCHOR, IMPORT_BLOCK + IMPORT_ANCHOR, 1)
    text = text.replace(GATE_ANCHOR, GATE_BLOCK + GATE_ANCHOR, 1)
    RUN_PY.write_text(text, encoding="utf-8")
    print("OK: patched authoritative fail-closed chat credit gate")


if __name__ == "__main__":
    main()
