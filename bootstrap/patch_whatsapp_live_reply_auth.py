"""Build-time patch: mark live WhatsApp replies as internally authorized.

Problem:

The WhatsApp adapter can already trust two kinds of sends:
1. Explicit `system_authorized=true` metadata (cron / internal delivery paths)
2. Live same-chat replies inferred from session contextvars

However, many gateway reply paths build metadata through
`gateway/platforms/base.py::_thread_metadata_for_source()`. For WhatsApp DMs
that helper currently returns `None` because there is no thread id, so the
adapter falls back to session-context inference only.

If a reply crosses an internal fallback/callback boundary where the session
context is not preserved, the adapter incorrectly classifies a normal in-chat
reply as proactive outbound and the bridge rejects it with:
  "Proactive messaging requires explicit user confirmation (confirmed_by_user=true)."

Fix:

Whenever a send is derived from a live WhatsApp `source`, attach
`{"system_authorized": True}` in `_thread_metadata_for_source()`.

This keeps proactive outbound protected because arbitrary sends (MCP/outbound
tooling, standalone sends, unrelated chat ids) do not originate from a live
gateway source and therefore do not receive this metadata automatically.

Idempotent + fail-loud.
"""

import os
import pathlib
import sys

BASE_PY = pathlib.Path(
    os.getenv("BASE_PLATFORM_PY", "/opt/hermes/gateway/platforms/base.py")
)
MARKER = "_mag_whatsapp_live_reply_auth"


def apply(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        print(f"  [skip] {label}: already patched")
        return text
    if old not in text:
        sys.exit(
            f"FATAL: base.py anchor not found for '{label}'. "
            "Upstream changed - update patch_whatsapp_live_reply_auth.py."
        )
    print(f"  [ok]   {label}")
    return text.replace(old, new, 1)


THREAD_METADATA_OLD = """def _thread_metadata_for_source(source, reply_to_message_id: str | None = None) -> dict | None:
    \"\"\"Build platform-aware thread metadata for adapter sends.

    Most platforms route threaded sends with a generic ``thread_id`` metadata
    value. Telegram private-chat topics created through Hermes' DM-topic helper
    are exposed in updates as ``message_thread_id`` plus a reply anchor. Live
    user-message replies route with ``message_thread_id`` + ``reply_to_message_id``;
    synthetic/resumed sends that have no reply anchor fall back to Telegram's
    ``direct_messages_topic_id`` when the Bot API supports it.
    \"\"\"
    thread_id = getattr(source, \"thread_id\", None)
    if thread_id is None:
        return None
    metadata = {\"thread_id\": thread_id}
    if _platform_name(getattr(source, \"platform\", None)) == \"telegram\" and getattr(source, \"chat_type\", None) == \"dm\":
        metadata[\"telegram_dm_topic_reply_fallback\"] = True
        tid = str(thread_id)
        if tid and tid not in {\"\", \"1\"}:
            metadata[\"direct_messages_topic_id\"] = tid
        anchor = reply_to_message_id or getattr(source, \"message_id\", None)
        if anchor is not None:
            metadata[\"telegram_reply_to_message_id\"] = str(anchor)
    return metadata
"""

THREAD_METADATA_NEW = """def _thread_metadata_for_source(source, reply_to_message_id: str | None = None) -> dict | None:
    \"\"\"Build platform-aware thread metadata for adapter sends.

    Most platforms route threaded sends with a generic ``thread_id`` metadata
    value. Telegram private-chat topics created through Hermes' DM-topic helper
    are exposed in updates as ``message_thread_id`` plus a reply anchor. Live
    user-message replies route with ``message_thread_id`` + ``reply_to_message_id``;
    synthetic/resumed sends that have no reply anchor fall back to Telegram's
    ``direct_messages_topic_id`` when the Bot API supports it.

    _mag_whatsapp_live_reply_auth: live WhatsApp replies do not have a thread id,
    but they still need stable adapter metadata so the outbound guard can treat
    them as trusted in-chat responses even if session contextvars are not
    available on a later fallback path.
    \"\"\"
    platform_name = _platform_name(getattr(source, \"platform\", None))
    if platform_name == \"whatsapp\":
        return {\"system_authorized\": True}

    thread_id = getattr(source, \"thread_id\", None)
    if thread_id is None:
        return None
    metadata = {\"thread_id\": thread_id}
    if platform_name == \"telegram\" and getattr(source, \"chat_type\", None) == \"dm\":
        metadata[\"telegram_dm_topic_reply_fallback\"] = True
        tid = str(thread_id)
        if tid and tid not in {\"\", \"1\"}:
            metadata[\"direct_messages_topic_id\"] = tid
        anchor = reply_to_message_id or getattr(source, \"message_id\", None)
        if anchor is not None:
            metadata[\"telegram_reply_to_message_id\"] = str(anchor)
    return metadata
"""


def main() -> None:
    if not BASE_PY.exists():
        sys.exit(f"FATAL: base.py not found at {BASE_PY}")

    text = BASE_PY.read_text(encoding="utf-8")
    print(f"Patching {BASE_PY} ({MARKER})")
    text = apply(
        text,
        THREAD_METADATA_OLD,
        THREAD_METADATA_NEW,
        "_thread_metadata_for_source whatsapp authorization",
    )
    BASE_PY.write_text(text, encoding="utf-8")
    print("Done.")


if __name__ == "__main__":
    main()
