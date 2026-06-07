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
  4. Scrubs engineering/tooling tells the MODEL may put in its FINAL answer
     (install hints, tracebacks, missing-dependency/API-key mentions). These are
     not provider-error envelopes, so they bypass (3) — replace the whole message
     with a humane line.

Each edit is independently idempotent + fail-loud: re-running applies only the
missing edits, and an anchor that is neither in its original NOR patched form
aborts the build (so an upstream Hermes change is caught, not silently leaked).
"""

import os
import pathlib
import re

RUN_PY = pathlib.Path(os.getenv("GATEWAY_RUN_PY", "/opt/hermes/gateway/run.py"))

# Internal, non-channel surfaces that must keep raw output.
INTERNAL_GUARD = 'if _gateway_platform_value(platform) in {"api_server", "local", "cli"}:'

# --- Edit 1: invert the Telegram-only guard (appears in BOTH sanitizers) -------
OLD_GUARD = '    if _gateway_platform_value(platform) != "telegram":\n        return text\n'
NEW_GUARD = '    ' + INTERNAL_GUARD + '\n        return text\n'

# --- Edit 2: drop non-error status chatter on end-user channels ----------------
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

# --- Edit 3: humane pt-BR provider-error copy (replace the whole function) ------
ERROR_FN_RE = re.compile(
    r"def _gateway_provider_error_reply\(text: str\) -> str:\n(?:.*\n)*?    \)\n"
)
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
NEW_ERROR_MARKER = "MAG: map raw provider/API errors to humane pt-BR copy"

# --- Edit 4: scrub engineering/tooling leaks from the FINAL answer -------------
LEAK_CONSTS = '''# MAG: scrub engineering/tooling tells the model may put in its FINAL answer
# (install hints, tracebacks, missing dependency/API-key mentions). Not provider
# error envelopes, so they bypass the rewrite above — replace with a humane line.
_MAG_ENGINEERING_LEAK_RE = re.compile(
    r"("
    r"não foi possível iniciar o navegador"
    r"|could not (?:start|launch) (?:the )?browser"
    r"|chrome.{0,24}(?:não|nao|not|isn.?t).{0,8}instalad"
    r"|chrome.{0,24}not installed"
    r"|agent-browser install"
    r"|(?:uv |python -m )?pip install"
    r"|\\b[A-Z][A-Z0-9_]{2,}_API_(?:KEY|URL)\\b"
    r"|SEARXNG_URL"
    r"|Traceback \\(most recent call last\\)"
    r"|ModuleNotFoundError|ImportError"
    r"|`hermes (?:model|tools)|run `hermes|rode `hermes"
    r"|não está instalad|nao esta instalad|not installed"
    r")",
    re.IGNORECASE,
)
_MAG_GENERIC_FAILURE_REPLY = (
    "Não consegui concluir isso agora. Pode tentar de novo em instantes? "
    "Se continuar, fale com o suporte da CyriusX."
)

# MAG: business-secret barrier. On END-USER channels the model must never reveal
# our engineering/product internals: our stack codenames, the AI model/provider
# powering it, source code, architecture, or "how it works" self-disclosure. If
# any leaks into the FINAL answer, replace the whole message with a redirect to
# official docs / support. NOTE: only OUR unique tells — client-side infra the
# customer may legitimately run (Docker/Postgres/etc.) is intentionally excluded
# to avoid false positives. Internal surfaces (api_server/local/cli) return raw
# above and never reach this.
_MAG_PRODUCT_SECRET_RE = re.compile(
    r"("
    r"\\bHermes\\b|\\bOpenClaw\\b|\\bByteRover\\b|\\bFastify\\b|\\bNeo4j\\b|\\bn8n\\b"
    r"|\\bGemini\\b|\\bAnthropic\\b|\\bOpenAI\\b|\\bClaude\\b|\\bGPT-?\\d|\\bLLM\\b"
    r"|large language model|modelo de linguagem|rede neural"
    r"|c[óo]digo[\\s-]?fonte|source code"
    r"|system prompt|prompt de sistema|meu prompt"
    r"|minha arquitetura|como eu funciono|como funciono por dentro"
    r"|fui (?:constru[íi]d|criad|desenvolvid|treinad)"
    r"|sou basead[ao] em|rodo (?:em|sobre)"
    r")",
    re.IGNORECASE,
)
_MAG_DOC_REDIRECT_MSG = (
    "Sobre os bastidores do produto eu não falo. Para esclarecer esse tipo de "
    "dúvida, consulte a documentação oficial da CyriusX ou fale com o suporte."
)


def _sanitize_gateway_final_response'''
LEAK_ANCHOR = "def _sanitize_gateway_final_response"

OLD_SANITIZE_TAIL = (
    "    redacted = _redact_gateway_user_facing_secrets(str(text))\n"
    "    if _looks_like_gateway_provider_error(redacted):\n"
    "        return _gateway_provider_error_reply(redacted)\n"
    "    return redacted\n"
)
NEW_SANITIZE_TAIL = (
    "    redacted = _redact_gateway_user_facing_secrets(str(text))\n"
    "    if _looks_like_gateway_provider_error(redacted):\n"
    "        return _gateway_provider_error_reply(redacted)\n"
    "    if _MAG_ENGINEERING_LEAK_RE.search(redacted):\n"
    "        return _MAG_GENERIC_FAILURE_REPLY\n"
    "    if _MAG_PRODUCT_SECRET_RE.search(redacted):\n"
    "        return _MAG_DOC_REDIRECT_MSG\n"
    "    return redacted\n"
)


def main() -> None:
    if not RUN_PY.exists():
        raise SystemExit(f"gateway run.py not found at {RUN_PY}")
    text = RUN_PY.read_text()
    edits = 0

    # Edit 1 — invert the Telegram-only guard in both sanitizers.
    if OLD_GUARD in text:
        if text.count(OLD_GUARD) < 2:
            raise SystemExit("patch_gateway_output: expected >=2 Telegram guards.")
        text = text.replace(OLD_GUARD, NEW_GUARD)
        edits += 1
    elif INTERNAL_GUARD not in text:
        raise SystemExit("patch_gateway_output: guard anchor missing (Hermes changed).")

    # Edit 2 — drop non-error status chatter.
    if OLD_STATUS_TAIL in text:
        text = text.replace(OLD_STATUS_TAIL, NEW_STATUS_TAIL, 1)
        edits += 1
    elif NEW_STATUS_TAIL not in text:
        raise SystemExit("patch_gateway_output: status-tail anchor missing.")

    # Edit 3 — humane provider-error copy.
    if NEW_ERROR_MARKER not in text:
        new_text, n = ERROR_FN_RE.subn(lambda _m: NEW_ERROR_REPLY, text, count=1)
        if n != 1:
            raise SystemExit("patch_gateway_output: _gateway_provider_error_reply not found.")
        text = new_text
        edits += 1

    # Edit 4 — engineering-leak scrub on final answers.
    if "_MAG_ENGINEERING_LEAK_RE" not in text:
        if LEAK_ANCHOR not in text:
            raise SystemExit("patch_gateway_output: sanitize-fn anchor missing.")
        text = text.replace(LEAK_ANCHOR, LEAK_CONSTS, 1)
        edits += 1
    if NEW_SANITIZE_TAIL not in text:
        if OLD_SANITIZE_TAIL not in text:
            raise SystemExit("patch_gateway_output: sanitize-tail anchor missing.")
        text = text.replace(OLD_SANITIZE_TAIL, NEW_SANITIZE_TAIL, 1)
        edits += 1

    if edits == 0:
        print("OK: gateway output already fully patched (idempotent no-op)")
        return

    RUN_PY.write_text(text)
    print(f"OK: patched {RUN_PY} ({edits} edit(s) applied)")


if __name__ == "__main__":
    main()
