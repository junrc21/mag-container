"""Build-time patch: never deliver self-improvement / background-review summaries to
end-user channels (MAG).

Hermes runs a background "self-improvement review" fork after turns (agent/background_review.py)
that can update the user profile / create skills, then DELIVERS a summary like
"💾 Self-improvement review: User profile updated" to the user's channel via a dedicated
callback (`agent.background_review_callback = _bg_review_send` in gateway/run.py). That
callback sends DIRECTLY through the status adapter, BYPASSING `_sanitize_gateway_final_response`
— so the product anti-noise barrier never sees it and the engineering message leaks to
Telegram/WhatsApp.

This patch wires the callback to None instead, so those summaries are never delivered to a
client channel. The background review itself still runs (memory work stays); it just goes
silent to the user — which is the correct behavior for a client-facing MAG.

Idempotent + fail-loud (mirrors the other bootstrap patches).
"""

import os
import pathlib
import sys

RUN_PY = pathlib.Path(os.getenv("GATEWAY_RUN_PY", "/opt/hermes/gateway/run.py"))

MARKER = "MAG: self-improvement/review summaries never reach client channels"

OLD = "            agent.background_review_callback = _bg_review_send\n"
NEW = (
    "            agent.background_review_callback = None  # "
    + MARKER
    + "\n"
)


def main() -> None:
    if not RUN_PY.exists():
        sys.exit(f"gateway run.py not found at {RUN_PY}")
    text = RUN_PY.read_text()

    if MARKER in text:
        print("OK: background-review delivery already disabled (idempotent no-op)")
        return
    if OLD not in text:
        sys.exit(
            "patch_no_review_delivery: anchor "
            "'agent.background_review_callback = _bg_review_send' missing (Hermes changed)."
        )

    text = text.replace(OLD, NEW, 1)
    RUN_PY.write_text(text)
    print("OK: disabled self-improvement/background-review channel delivery")


if __name__ == "__main__":
    main()
