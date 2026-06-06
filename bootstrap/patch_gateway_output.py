"""Build-time patch: never leak engineering output to end-user channels.

The stock Hermes gateway only sanitizes status/error messages for Telegram
(`if _gateway_platform_value(platform) != "telegram": return text`). Every other
channel (WhatsApp/Slack/Signal/…) receives raw status, provider errors and even
secrets. This patch:

  1. Inverts that guard in both sanitizers so EVERY end-user channel is
     sanitized; only internal surfaces (api_server / local / cli) stay raw.
  2. Drops all non-error status chatter on end-user channels (lifecycle/progress
     status is engineering noise — the user only ever needs the final reply).
  3. Replaces the English "check gateway logs" provider-error copy with humane
     pt-BR product copy in two tiers: transient (try again) vs critical (contact
     support) so the user doesn't retry a hard failure forever.

Idempotent + fail-loud: re-running is a no-op, and a missing anchor aborts the
build (so an upstream Hermes change is caught instead of silently leaking).
"""

import os
import pathlib
import re

RUN_PY = pathlib.Path(os.getenv("GATEWAY_RUN_PY", "/opt/hermes/gateway/run.py"))

# Internal, non-channel surfaces that must keep raw output.
INTERNAL_GUARD = 'if _gateway_platform_value(platform) in {"api_server", "local", "cli"}:'

OLD_GUARD = '    if _gateway_platform_value(platform) != "telegram":\n        return text\n'
NEW_GUARD = '    ' + INTERNAL_GUARD + '\n        return text\n'

# Status function tail: after redaction + noisy-regex + provider-error rewrite,
# the stock code returns the raw status text. On end-user channels we drop it.
OLD_STATUS_TAIL = (
    "    if _looks_like_gateway_provider_error(text):\n"
    "        return _gateway_provider_error_reply(text)\n"
    "    return text\n"
)
NEW_STATUS_TAIL = (
    "    if _looks_like_gateway_provider_error(text):\n"
    "        return _gateway_provider_error_reply(text)\n"
    "    return None  # MAG: drop lifecycle/status chatter on end-user channels\n"
)

# Humane pt-BR provider-error copy (two tiers). Replaces the whole function body
# via regex so it's robust to the original string formatting.
NEW_ERROR_REPLY = '''def _gateway_provider_error_reply(text: str) -> str:
    """MAG: map raw provider/API errors to humane pt-BR copy (no internals)."""
    if _GATEWAY_AUTH_ERROR_RE.search(text):
        return (
            "Tive um erro crítico de configuração aqui e não consegui concluir. "
            "Por favor, contate o suporte da CyriusX."
        )
    if _GATEWAY_PROVIDER_POLICY_RE.search(text):
        return "Não consigo seguir com esse pedido específico. Se quiser, me peça de outro jeito."
    if _GATEWAY_RATE_LIMIT_RE.search(text):
        return "Tive um contratempo técnico agora há pouco. Pode tentar de novo em instantes?"
    return (
        "Tive um erro crítico aqui e não consegui concluir. "
        "Por favor, contate o suporte da CyriusX."
    )
'''


def main() -> None:
    if not RUN_PY.exists():
        raise SystemExit(f"gateway run.py not found at {RUN_PY}")
    text = RUN_PY.read_text()

    if INTERNAL_GUARD in text:
        print("OK: gateway output already patched (idempotent no-op)")
        return

    # 1. Invert the Telegram-only guard in BOTH sanitizers (identical 2-line block).
    count_guard = text.count(OLD_GUARD)
    if count_guard < 2:
        raise SystemExit(
            f"patch_gateway_output: expected >=2 Telegram guards, found {count_guard}. "
            "Hermes gateway changed — review before shipping."
        )
    text = text.replace(OLD_GUARD, NEW_GUARD)

    # 2. Drop non-error status chatter on end-user channels.
    if OLD_STATUS_TAIL not in text:
        raise SystemExit("patch_gateway_output: status-message tail anchor not found.")
    text = text.replace(OLD_STATUS_TAIL, NEW_STATUS_TAIL, 1)

    # 3. Humane pt-BR provider-error copy (replace the whole function body).
    new_text, n = re.subn(
        r"def _gateway_provider_error_reply\(text: str\) -> str:\n(?:.*\n)*?    \)\n",
        lambda _m: NEW_ERROR_REPLY,  # function repl: re won't process backslashes
        text,
        count=1,
    )
    if n != 1:
        raise SystemExit("patch_gateway_output: _gateway_provider_error_reply not found.")
    text = new_text

    RUN_PY.write_text(text)
    print(f"OK: patched {RUN_PY} (channel sanitization + humane error copy)")


if __name__ == "__main__":
    main()
