"""Build-time patch: preserve trusted authorization on WhatsApp cron deliveries.

Cron jobs and fallback standalone sends do not run inside an active inbound
message context, so they must carry an explicit internal authorization marker
when they deliver to the saved WhatsApp target of that job.

This patch:
1. Extends `tools/send_message_tool.py` so `_send_whatsapp()` can forward
   `system_authorized=true` to the bridge.
2. Extends `_send_to_platform()` to accept an optional `delivery_metadata`
   payload and forwards it to WhatsApp standalone sends.
3. Marks cron delivery metadata as `system_authorized=true` before the live
   adapter send and reuses the same metadata for standalone fallback.

True proactive outbound still requires `confirmed_by_user=true` and allowlist
approval at the bridge layer.

Idempotent + fail-loud.
"""

import os
import pathlib
import sys

SEND_TOOL = pathlib.Path(
    os.getenv("SEND_MESSAGE_TOOL_PY", "/opt/hermes/tools/send_message_tool.py")
)
SCHEDULER = pathlib.Path(
    os.getenv("CRON_SCHEDULER_PY", "/opt/hermes/cron/scheduler.py")
)
MARKER = "_mag_whatsapp_cron_auth"


def apply(text: str, old: str, new: str, label: str, *, path: pathlib.Path) -> str:
    if new in text:
        print(f"  [skip] {path.name} {label}: already patched")
        return text
    if old not in text:
        sys.exit(
            f"FATAL: anchor not found for '{label}' in {path}. "
            "Upstream changed - update patch_whatsapp_cron_auth.py."
        )
    print(f"  [ok]   {path.name} {label}")
    return text.replace(old, new, 1)


SEND_WHATSAPP_SIG_OLD = """async def _send_whatsapp(extra, chat_id, message):"""
SEND_WHATSAPP_SIG_NEW = """async def _send_whatsapp(extra, chat_id, message, delivery_metadata=None):"""

SEND_WHATSAPP_PAYLOAD_OLD = """            async with session.post(
                f"http://localhost:{bridge_port}/send",
                json={"chatId": chat_id, "message": message},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
"""
SEND_WHATSAPP_PAYLOAD_NEW = """            payload = {"chatId": chat_id, "message": message}
            if isinstance(delivery_metadata, dict) and delivery_metadata.get("system_authorized"):
                payload["system_authorized"] = True
            async with session.post(
                f"http://localhost:{bridge_port}/send",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
"""

SEND_TO_PLATFORM_SIG_OLD = """async def _send_to_platform(platform, pconfig, chat_id, message, thread_id=None, media_files=None, force_document=False):"""
SEND_TO_PLATFORM_SIG_NEW = """async def _send_to_platform(platform, pconfig, chat_id, message, thread_id=None, media_files=None, force_document=False, delivery_metadata=None):"""

SEND_TO_PLATFORM_CALL_OLD = """        elif platform == Platform.WHATSAPP:
            result = await _send_whatsapp(pconfig.extra, chat_id, chunk)
"""
SEND_TO_PLATFORM_CALL_NEW = """        elif platform == Platform.WHATSAPP:
            result = await _send_whatsapp(
                pconfig.extra,
                chat_id,
                chunk,
                delivery_metadata=delivery_metadata,
            )
"""

CRON_METADATA_OLD = """            send_metadata = {"thread_id": thread_id} if thread_id else None
"""
CRON_METADATA_NEW = """            send_metadata = {"thread_id": thread_id} if thread_id else {}
            if platform == Platform.WHATSAPP:
                send_metadata["system_authorized"] = True
            if not send_metadata:
                send_metadata = None
"""

CRON_STANDALONE_CALL_OLD = """            coro = _send_to_platform(platform, pconfig, chat_id, cleaned_delivery_content, thread_id=thread_id, media_files=media_files)
            try:
                result = asyncio.run(coro)
            except RuntimeError:
                # asyncio.run() checks for a running loop before awaiting the coroutine;
                # when it raises, the original coro was never started — close it to
                # prevent "coroutine was never awaited" RuntimeWarning, then retry in a
                # fresh thread that has no running loop.
                coro.close()
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(asyncio.run, _send_to_platform(platform, pconfig, chat_id, cleaned_delivery_content, thread_id=thread_id, media_files=media_files))
                    result = future.result(timeout=30)
"""
CRON_STANDALONE_CALL_NEW = """            coro = _send_to_platform(
                platform,
                pconfig,
                chat_id,
                cleaned_delivery_content,
                thread_id=thread_id,
                media_files=media_files,
                delivery_metadata=send_metadata,
            )
            try:
                result = asyncio.run(coro)
            except RuntimeError:
                # asyncio.run() checks for a running loop before awaiting the coroutine;
                # when it raises, the original coro was never started — close it to
                # prevent "coroutine was never awaited" RuntimeWarning, then retry in a
                # fresh thread that has no running loop.
                coro.close()
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(
                        asyncio.run,
                        _send_to_platform(
                            platform,
                            pconfig,
                            chat_id,
                            cleaned_delivery_content,
                            thread_id=thread_id,
                            media_files=media_files,
                            delivery_metadata=send_metadata,
                        ),
                    )
                    result = future.result(timeout=30)
"""


def patch_send_tool() -> None:
    if not SEND_TOOL.exists():
        sys.exit(f"FATAL: send_message_tool.py not found at {SEND_TOOL}")
    text = SEND_TOOL.read_text(encoding="utf-8")
    print(f"Patching {SEND_TOOL} ({MARKER})")
    text = apply(text, SEND_WHATSAPP_SIG_OLD, SEND_WHATSAPP_SIG_NEW, "_send_whatsapp signature", path=SEND_TOOL)
    text = apply(text, SEND_WHATSAPP_PAYLOAD_OLD, SEND_WHATSAPP_PAYLOAD_NEW, "_send_whatsapp payload", path=SEND_TOOL)
    text = apply(text, SEND_TO_PLATFORM_SIG_OLD, SEND_TO_PLATFORM_SIG_NEW, "_send_to_platform signature", path=SEND_TOOL)
    text = apply(text, SEND_TO_PLATFORM_CALL_OLD, SEND_TO_PLATFORM_CALL_NEW, "_send_to_platform WhatsApp call", path=SEND_TOOL)
    SEND_TOOL.write_text(text, encoding="utf-8")


def patch_scheduler() -> None:
    if not SCHEDULER.exists():
        sys.exit(f"FATAL: scheduler.py not found at {SCHEDULER}")
    text = SCHEDULER.read_text(encoding="utf-8")
    print(f"Patching {SCHEDULER} ({MARKER})")
    text = apply(text, CRON_METADATA_OLD, CRON_METADATA_NEW, "cron send metadata", path=SCHEDULER)
    text = apply(text, CRON_STANDALONE_CALL_OLD, CRON_STANDALONE_CALL_NEW, "cron standalone fallback metadata", path=SCHEDULER)
    SCHEDULER.write_text(text, encoding="utf-8")


def main() -> None:
    patch_send_tool()
    patch_scheduler()
    print("Done.")


if __name__ == "__main__":
    main()
