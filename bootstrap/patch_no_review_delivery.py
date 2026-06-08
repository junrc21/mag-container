"""Build-time patch: fully disable Hermes' background "self-improvement review" for MAG.

After each turn Hermes can spawn a background review fork that mutates the agent's own
state — it UPDATES THE USER PROFILE and CREATES SKILLS, then delivers a summary like
"💾 Self-improvement review: User profile updated · Skill 'routine-reminders' created" to
the channel.

On a client-facing MAG this is actively harmful, not just noisy:
  - The summary leaks engineering internals to Telegram/WhatsApp (it sends DIRECTLY via the
    status adapter, bypassing `_sanitize_gateway_final_response`).
  - Worse, the mutations CHANGE BEHAVIOR unpredictably: a created skill / an updated user
    profile gets injected into the system prompt every turn, so the agent starts narrating
    the user's routines on every message before actually answering.

So we kill it at the root. Two edits, both idempotent + fail-loud:

  1. run_agent.py — make `_spawn_background_review` a no-op (return before spawning). The
     review never runs → no skill/profile mutations, ever.
  2. gateway/run.py — wire `background_review_callback = None` (defense in depth: if the
     review is ever re-enabled upstream, its summary still can't reach a client channel).
"""

import os
import pathlib
import sys

RUN_AGENT_PY = pathlib.Path(os.getenv("RUN_AGENT_PY", "/opt/hermes/run_agent.py"))
RUN_PY = pathlib.Path(os.getenv("GATEWAY_RUN_PY", "/opt/hermes/gateway/run.py"))

# --- Edit 1: disable the spawn (run_agent.py) ---------------------------------
SPAWN_MARKER = "MAG: self-improvement review disabled"
SPAWN_OLD = (
    "        from agent.background_review import spawn_background_review_thread\n"
    "        target, _prompt = spawn_background_review_thread(\n"
)
SPAWN_NEW = (
    "        return  # " + SPAWN_MARKER + " (mutates skills/profile -> off-schedule chatter)\n"
    "        from agent.background_review import spawn_background_review_thread\n"
    "        target, _prompt = spawn_background_review_thread(\n"
)

# --- Edit 2: never deliver any review summary to a channel (gateway/run.py) ----
CB_MARKER = "MAG: self-improvement/review summaries never reach client channels"
CB_OLD = "            agent.background_review_callback = _bg_review_send\n"
CB_NEW = "            agent.background_review_callback = None  # " + CB_MARKER + "\n"


def patch_file(path: pathlib.Path, marker: str, old: str, new: str, label: str) -> None:
    if not path.exists():
        sys.exit(f"patch_no_review_delivery: {path} not found")
    text = path.read_text()
    if marker in text:
        print(f"OK: {label} already applied (idempotent no-op)")
        return
    if old not in text:
        sys.exit(f"patch_no_review_delivery: anchor for '{label}' missing (Hermes changed).")
    path.write_text(text.replace(old, new, 1))
    print(f"OK: {label}")


def main() -> None:
    patch_file(RUN_AGENT_PY, SPAWN_MARKER, SPAWN_OLD, SPAWN_NEW, "disabled background-review spawn")
    patch_file(RUN_PY, CB_MARKER, CB_OLD, CB_NEW, "disabled background-review channel delivery")


if __name__ == "__main__":
    main()
