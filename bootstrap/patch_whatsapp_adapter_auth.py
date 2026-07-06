"""Build-time patch: mark trusted WhatsApp runtime sends as internally authorized.

The bridge outbound guard distinguishes between:
1. Trusted runtime-originated sends (replies, cron) - use system_authorized=true
2. Proactive outbound sends (AI-initiated to arbitrary numbers) - require confirmed_by_user=true

This patch teaches gateway/platforms/whatsapp.py to:
- Pass system_authorized=true for trusted runtime sends (replies in same session, cron)
- Pass confirmed_by_user=true for all other sends (proactive outbound)

The bridge validates both flags and enforces allowlist checking for proactive sends.

Idempotent + fail-loud.
"""

import os
import pathlib
import sys

WHATSAPP_PY = pathlib.Path(
    os.getenv("WHATSAPP_PLATFORM_PY", "/opt/hermes/gateway/platforms/whatsapp.py")
)
MARKER = "_mag_whatsapp_runtime_auth"


def apply(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        print(f"  [skip] {label}: already patched")
        return text
    if old not in text:
        sys.exit(
            f"FATAL: WhatsApp adapter anchor not found for '{label}'. "
            "Upstream whatsapp.py changed - update patch_whatsapp_adapter_auth.py."
        )
    print(f"  [ok]   {label}")
    return text.replace(old, new, 1)


HELPERS_OLD = """logger = logging.getLogger(__name__)

"""
HELPERS_NEW = """logger = logging.getLogger(__name__)


def _mag_normalize_chat_ref(value: Optional[str]) -> str:
    # _mag_whatsapp_runtime_auth: compare WhatsApp chat identities across
    # LID/JID/digit formats without depending on bridge-side JS helpers.
    if value is None:
        return ""
    text = str(value).strip().lower()
    if not text:
        return ""
    if "@" in text:
        return text
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits


def _mag_is_runtime_authorized_whatsapp_send(
    chat_id: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    # _mag_whatsapp_runtime_auth: trusted runtime sends are either explicit
    # system-authorized deliveries (cron) or replies inside the active
    # WhatsApp session context for the same chat.
    metadata = metadata or {}
    if bool(metadata.get("system_authorized")):
        return True

    try:
        from gateway.session_context import get_session_env

        session_platform = (get_session_env("HERMES_SESSION_PLATFORM", "") or "").strip().lower()
        session_chat_id = get_session_env("HERMES_SESSION_CHAT_ID", "") or ""
    except Exception:
        return False

    if session_platform != "whatsapp" or not session_chat_id:
        return False

    normalized_target = _mag_normalize_chat_ref(chat_id)
    normalized_session = _mag_normalize_chat_ref(session_chat_id)
    if not normalized_target or not normalized_session:
        return False

    if normalized_target == normalized_session:
        return True

    target_digits = "".join(ch for ch in normalized_target if ch.isdigit())
    session_digits = "".join(ch for ch in normalized_session if ch.isdigit())
    return bool(target_digits and session_digits and target_digits == session_digits)

"""


SEND_PAYLOAD_OLD = """            for chunk in chunks:
                payload: Dict[str, Any] = {
                    "chatId": chat_id,
                    "message": chunk,
                }
                if reply_to and last_message_id is None:
                    # Only reply-to on the first chunk
                    payload["replyTo"] = reply_to
"""
SEND_PAYLOAD_NEW = """            runtime_authorized = _mag_is_runtime_authorized_whatsapp_send(chat_id, metadata)
            for chunk in chunks:
                payload: Dict[str, Any] = {
                    "chatId": chat_id,
                    "message": chunk,
                }
                if runtime_authorized:
                    payload["system_authorized"] = True
                else:
                    # Proactive sends require explicit user confirmation
                    payload["confirmed_by_user"] = True
                if reply_to and last_message_id is None:
                    # Only reply-to on the first chunk
                    payload["replyTo"] = reply_to
"""


MEDIA_SIGNATURE_OLD = """    async def _send_media_to_bridge(
        self,
        chat_id: str,
        file_path: str,
        media_type: str,
        caption: Optional[str] = None,
        file_name: Optional[str] = None,
    ) -> SendResult:
"""
MEDIA_SIGNATURE_NEW = """    async def _send_media_to_bridge(
        self,
        chat_id: str,
        file_path: str,
        media_type: str,
        caption: Optional[str] = None,
        file_name: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
"""


MEDIA_PAYLOAD_OLD = """            payload: Dict[str, Any] = {
                "chatId": chat_id,
                "filePath": file_path,
                "mediaType": media_type,
            }
            if caption:
"""
MEDIA_PAYLOAD_NEW = """            payload: Dict[str, Any] = {
                "chatId": chat_id,
                "filePath": file_path,
                "mediaType": media_type,
            }
            if _mag_is_runtime_authorized_whatsapp_send(chat_id, metadata):
                payload["system_authorized"] = True
            else:
                # Proactive sends require explicit user confirmation
                payload["confirmed_by_user"] = True
            if caption:
"""


SEND_IMAGE_OLD = """            local_path = await cache_image_from_url(image_url)
            return await self._send_media_to_bridge(chat_id, local_path, "image", caption)
"""
SEND_IMAGE_NEW = """            local_path = await cache_image_from_url(image_url)
            return await self._send_media_to_bridge(
                chat_id,
                local_path,
                "image",
                caption,
                metadata=metadata,
            )
"""


SEND_VIDEO_OLD = """        return await self._send_media_to_bridge(chat_id, video_path, "video", caption)
"""
SEND_VIDEO_NEW = """        return await self._send_media_to_bridge(
            chat_id,
            video_path,
            "video",
            caption,
            metadata=kwargs.get("metadata"),
        )
"""


SEND_VOICE_OLD = """        return await self._send_media_to_bridge(chat_id, audio_path, "audio", caption)
"""
SEND_VOICE_NEW = """        return await self._send_media_to_bridge(
            chat_id,
            audio_path,
            "audio",
            caption,
            metadata=kwargs.get("metadata"),
        )
"""


SEND_DOCUMENT_OLD = """        return await self._send_media_to_bridge(
            chat_id, file_path, "document", caption,
            file_name or os.path.basename(file_path),
        )
"""
SEND_DOCUMENT_NEW = """        return await self._send_media_to_bridge(
            chat_id,
            file_path,
            "document",
            caption,
            file_name or os.path.basename(file_path),
            metadata=kwargs.get("metadata"),
        )
"""


def main() -> None:
    if not WHATSAPP_PY.exists():
        sys.exit(f"FATAL: whatsapp.py not found at {WHATSAPP_PY}")

    text = WHATSAPP_PY.read_text(encoding="utf-8")
    print(f"Patching {WHATSAPP_PY} ({MARKER})")

    text = apply(text, HELPERS_OLD, HELPERS_NEW, "insert runtime authorization helpers")
    text = apply(text, SEND_PAYLOAD_OLD, SEND_PAYLOAD_NEW, "mark trusted /send payloads")
    text = apply(text, MEDIA_SIGNATURE_OLD, MEDIA_SIGNATURE_NEW, "extend _send_media_to_bridge signature")
    text = apply(text, MEDIA_PAYLOAD_OLD, MEDIA_PAYLOAD_NEW, "mark trusted /send-media payloads")
    text = apply(text, SEND_IMAGE_OLD, SEND_IMAGE_NEW, "propagate metadata in send_image")
    text = apply(text, SEND_VIDEO_OLD, SEND_VIDEO_NEW, "propagate metadata in send_video")
    text = apply(text, SEND_VOICE_OLD, SEND_VOICE_NEW, "propagate metadata in send_voice")
    text = apply(text, SEND_DOCUMENT_OLD, SEND_DOCUMENT_NEW, "propagate metadata in send_document")

    WHATSAPP_PY.write_text(text, encoding="utf-8")
    print("Done.")


if __name__ == "__main__":
    main()
