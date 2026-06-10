"""Build-time patch: never show the auto-reset banner on a CLIENT channel (MAG, §17/§18).

When a session auto-resets (idle / daily / suspended-for-personality-reload), Hermes
sends the user a banner like:

    ◐ Session automatically reset (...). Conversation history cleared.
    Use /resume to browse and restore a previous session.
    Adjust reset timing in config.yaml under session_reset.
    ◆ Model: gpt-5.5   ◆ Provider: openai-codex   ◆ Context: 272K tokens

On an end-user channel that's a hard secrecy-barrier breach: it leaks the AI
model/provider, internal config (`config.yaml`/`session_reset`), slash commands
(`/resume`) and token mechanics — all in English. The agent still gets the internal
`context_note` (so it knows the conversation is fresh); only the USER-FACING banner
must go. With idle_minutes lowered to 120, this banner would otherwise fire often.

Fix: gate `should_notify` off on client channels (platform not in
api_server/local/cli) right after Hermes computes it — including the `suspended`
branch, which otherwise bypasses the policy and always notifies. Internal/staff
surfaces keep the banner. Idempotent + fail-loud (mirrors the other patches).
"""

import os
import pathlib

RUN_PY = pathlib.Path(os.getenv("GATEWAY_RUN_PY", "/opt/hermes/gateway/run.py"))

MARKER = "MAG_suppress_reset_banner"

ANCHOR = (
    '                should_notify = reset_reason == "suspended" or (\n'
    "                    policy.notify\n"
    "                    and had_activity\n"
    "                    and platform_name not in policy.notify_exclude_platforms\n"
    "                )\n"
)

GATE = (
    "                # MAG_suppress_reset_banner: the auto-reset banner leaks the AI\n"
    "                # model/provider, config.yaml internals and slash commands in\n"
    "                # English. Never show it on a client channel (the agent still gets\n"
    "                # the internal context_note). Internal surfaces keep it.\n"
    '                if platform_name not in ("api_server", "local", "cli"):\n'
    "                    should_notify = False\n"
)


def main() -> None:
    if not RUN_PY.exists():
        raise SystemExit(f"gateway run.py not found at {RUN_PY}")
    text = RUN_PY.read_text()

    if MARKER in text:
        print("OK: reset-banner suppression already patched (idempotent no-op)")
        return

    if ANCHOR not in text:
        raise SystemExit(
            "patch_suppress_reset_banner: should_notify anchor missing (Hermes changed)."
        )

    text = text.replace(ANCHOR, ANCHOR + GATE, 1)
    RUN_PY.write_text(text)
    print("OK: patched — auto-reset banner suppressed on client channels")


if __name__ == "__main__":
    main()
