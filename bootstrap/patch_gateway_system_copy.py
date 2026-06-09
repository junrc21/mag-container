"""Build-time patch: humanize/suppress stock Hermes gateway SYSTEM messages that leak to
end-user channels (MAG).

These are sent by the gateway itself (not the agent), so they bypass the SOUL persona AND
the output sanitizer — they reach Telegram/WhatsApp raw, in English, leaking the "Hermes"
stack name and internal CLI/slash mechanics:

  1. Pairing prompt (gateway/run.py): "Hi~ I don't recognize you yet! Here's your pairing
     code: X. Ask the bot owner to run: `hermes pairing approve telegram X`".
     → humane pt-BR, no CLI, no "Hermes"; the owner approves in the panel.
  2. Pairing rate-limit: "Too many pairing requests right now~ Please try again later!"
     → pt-BR.
  3. Home-channel nag: "📬 No home channel is set... Hermes delivers cron job results...
     Type /sethome...". Pure engineering noise that also leaks "Hermes" + the /sethome
     command. MAG cron delivers to the message origin, so this prompt is unnecessary.
     → suppressed.

Idempotent + fail-loud (mirrors the other bootstrap patches).
"""

import os
import pathlib
import sys

RUN_PY = pathlib.Path(os.getenv("GATEWAY_RUN_PY", "/opt/hermes/gateway/run.py"))

PAIR_OLD = (
    '                            f"Hi~ I don\'t recognize you yet!\\n\\n"\n'
    '                            f"Here\'s your pairing code: `{code}`\\n\\n"\n'
    '                            f"Ask the bot owner to run:\\n"\n'
    '                            f"`hermes pairing approve {platform_name} {code}`"\n'
)
PAIR_NEW = (
    '                            f"Oi! Ainda não reconheço você por aqui. 🙂\\n\\n"  # MAG\n'
    '                            f"Seu código de acesso é: `{code}`\\n\\n"\n'
    '                            f"Envie esse código para quem cuida da MAG liberar o seu acesso no painel."\n'
)

RATE_OLD = (
    '                            "Too many pairing requests right now~ "\n'
    '                            "Please try again later!"\n'
)
RATE_NEW = (
    '                            "Recebi muitos pedidos de acesso agora há pouco. "  # MAG\n'
    '                            "Tente de novo em alguns minutos, por favor."\n'
)

HOME_OLD = (
    '                notice = (\n'
    '                    f"📬 No home channel is set for {platform_name.title()}. "\n'
    '                    f"A home channel is where Hermes delivers cron job results "\n'
    '                    f"and cross-platform messages.\\n\\n"\n'
    '                    f"Type {sethome_cmd} to make this chat your home channel, "\n'
    '                    f"or ignore to skip."\n'
    '                )\n'
    '                await self._deliver_platform_notice(source, notice)\n'
)
HOME_NEW = (
    '                # MAG: suppress the engineering "set home channel" nag — it leaks the\n'
    '                # stack name + /sethome mechanic, and MAG cron delivers to the origin chat.\n'
    '                notice = ""\n'
    '                _ = sethome_cmd  # computed above; intentionally unused now\n'
    '                if notice:\n'
    '                    await self._deliver_platform_notice(source, notice)\n'
)

MARKER = "MAG: suppress the engineering \"set home channel\" nag"


def apply(text: str, old: str, new: str, label: str, *, optional: bool = False) -> str:
    if new in text:
        print(f"  [skip] {label}: already patched")
        return text
    if old not in text:
        if optional:
            print(f"  [warn] {label}: anchor not found (optional) — skipping")
            return text
        sys.exit(f"FATAL: anchor not found for '{label}'. gateway/run.py changed — update patch_gateway_system_copy.py.")
    print(f"  [ok]   {label}")
    return text.replace(old, new, 1)


def main() -> None:
    if not RUN_PY.exists():
        sys.exit(f"gateway run.py not found at {RUN_PY}")
    text = RUN_PY.read_text()
    if MARKER in text and PAIR_NEW.split(chr(10))[0] in text:
        print("OK: gateway system copy already patched (idempotent no-op)")
        return
    text = apply(text, PAIR_OLD, PAIR_NEW, "pairing prompt → pt-BR (no CLI/Hermes)")
    text = apply(text, RATE_OLD, RATE_NEW, "pairing rate-limit → pt-BR", optional=True)
    text = apply(text, HOME_OLD, HOME_NEW, "home-channel nag → suppressed")
    RUN_PY.write_text(text)
    print("OK: patched gateway system messages (pairing + home-channel)")


if __name__ == "__main__":
    main()
