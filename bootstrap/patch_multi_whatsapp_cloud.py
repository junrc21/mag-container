"""MAG multi WhatsApp Cloud support.

Hermes exposes one Platform.WHATSAPP_CLOUD adapter to the gateway. MAG can have
multiple WhatsApp Cloud tenant_channels. This patch keeps the public adapter key
unchanged, but lets the adapter route multiple phone_number_id credentials from
/opt/data/policy/channels.json.

Design:
- one parent adapter owns the webhook socket;
- each extra WhatsApp number is a child WhatsAppCloudAdapter with its own token,
  phone id, allowlist and send state;
- inbound payloads dispatch to the child matching metadata.phone_number_id;
- outbound replies produced by child.handle_message use that child directly;
- parent-level sends are routed by the latest inbound chat -> child map.
"""
from pathlib import Path
import os
import textwrap


PATH = Path(os.getenv("WHATSAPP_CLOUD_PY", "/opt/hermes/gateway/platforms/whatsapp_cloud.py"))
MARKER = "# MAG_MULTI_WHATSAPP_CLOUD_V2"
LEGACY_MARKER = "# MAG_MULTI_WHATSAPP_CLOUD_V1"


def patch() -> None:
    text = PATH.read_text()
    if MARKER in text:
        print("OK: multi WhatsApp Cloud already patched")
        return
    if LEGACY_MARKER in text:
        old = 'await child._dispatch_payload({"object": payload.get("object"), "entry": [{"changes": [change]}]})'
        new = 'await _MAG_OriginalWhatsAppCloudAdapter._dispatch_payload(child, {"object": payload.get("object"), "entry": [{"changes": [change]}]})'
        if text.count(old) != 1:
            raise SystemExit("patch_multi_whatsapp_cloud: legacy dispatch anchor drift")
        text = text.replace(old, new).replace(LEGACY_MARKER, MARKER)
        compile(text, str(PATH), "exec")
        PATH.write_text(text)
        print("OK: multi WhatsApp Cloud upgraded to V2")
        return
    if text.count("class WhatsAppCloudAdapter(WhatsAppBehaviorMixin, BasePlatformAdapter):") != 1:
        raise SystemExit("patch_multi_whatsapp_cloud: WhatsAppCloudAdapter anchor drift")
    addition = textwrap.dedent(
        r'''

        # MAG_MULTI_WHATSAPP_CLOUD_V2
        _MAG_OriginalWhatsAppCloudAdapter = WhatsAppCloudAdapter


        def _mag_load_whatsapp_cloud_channels():
            path = os.getenv("MAG_CHANNELS_CONFIG_PATH") or os.path.expanduser("~/policy/channels.json")
            try:
                import json as _json
                with open(path, "r") as _f:
                    payload = _json.load(_f)
            except Exception:
                return []
            rows = payload.get("channels") if isinstance(payload, dict) else []
            if not isinstance(rows, list):
                return []
            live = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                if str(row.get("provider") or "").lower() != "whatsapp_cloud":
                    continue
                if str(row.get("status") or "") not in ("connected", "pending_review"):
                    continue
                creds = row.get("credentials") if isinstance(row.get("credentials"), dict) else {}
                phone_id = str(creds.get("phoneNumberId") or row.get("externalId") or "").strip()
                token = str(creds.get("accessToken") or "").strip()
                if not phone_id or not token:
                    continue
                live.append({**row, "credentials": creds, "_phoneNumberId": phone_id})
            return live


        def _mag_child_config(parent_config, channel):
            extra = dict(getattr(parent_config, "extra", None) or {})
            creds = channel.get("credentials") if isinstance(channel.get("credentials"), dict) else {}
            allowed = creds.get("allowedUsers") or extra.get("allow_from")
            extra.update({
                "phone_number_id": channel.get("_phoneNumberId"),
                "access_token": creds.get("accessToken"),
                "app_id": creds.get("appId") or extra.get("app_id"),
                "app_secret": creds.get("appSecret") or extra.get("app_secret"),
                "waba_id": creds.get("wabaId") or extra.get("waba_id"),
                "verify_token": creds.get("verifyToken") or extra.get("verify_token"),
            })
            if allowed:
                extra["allow_from"] = allowed
            return PlatformConfig(
                enabled=True,
                token=getattr(parent_config, "token", None),
                api_key=getattr(parent_config, "api_key", None),
                home_channel=getattr(parent_config, "home_channel", None),
                reply_to_mode=getattr(parent_config, "reply_to_mode", "first"),
                gateway_restart_notification=getattr(parent_config, "gateway_restart_notification", True),
                extra=extra,
            )


        class WhatsAppCloudAdapter(_MAG_OriginalWhatsAppCloudAdapter):
            def __init__(self, config: PlatformConfig):
                channels = _mag_load_whatsapp_cloud_channels()
                self._mag_channels = channels
                self._mag_children = []
                self._mag_chat_to_child = {}
                self._mag_child_resume_tasks = []
                if len(channels) > 1:
                    super().__init__(_mag_child_config(config, channels[0]))
                    self._mag_channel_id = str(channels[0].get("channelId") or "")
                    for channel in channels[1:]:
                        child = _MAG_OriginalWhatsAppCloudAdapter(_mag_child_config(config, channel))
                        child._mag_channel_id = str(channel.get("channelId") or "")
                        self._mag_children.append(child)
                    logger.info("[whatsapp_cloud] MAG multi-channel enabled (%d phone numbers)", len(channels))
                else:
                    super().__init__(config)
                    self._mag_channel_id = str(channels[0].get("channelId") or "") if channels else ""

            def _mag_all_adapters(self):
                return [self] + list(getattr(self, "_mag_children", []) or [])

            def set_message_handler(self, handler):
                super().set_message_handler(handler)
                for child in getattr(self, "_mag_children", []):
                    child.set_message_handler(handler)

            def set_fatal_error_handler(self, handler):
                super().set_fatal_error_handler(handler)
                for child in getattr(self, "_mag_children", []):
                    child.set_fatal_error_handler(handler)

            def set_busy_session_handler(self, handler):
                super().set_busy_session_handler(handler)
                for child in getattr(self, "_mag_children", []):
                    child.set_busy_session_handler(handler)

            def set_session_store(self, session_store):
                super().set_session_store(session_store)
                for child in getattr(self, "_mag_children", []):
                    child.set_session_store(session_store)

            def set_topic_recovery_fn(self, fn):
                super().set_topic_recovery_fn(fn)
                for child in getattr(self, "_mag_children", []):
                    child.set_topic_recovery_fn(fn)

            async def connect(self) -> bool:
                ok = await super().connect()
                if not ok or not getattr(self, "_mag_children", None):
                    return ok
                for child in self._mag_children:
                    child._http_client = self._http_client
                    child._runner = None
                    child._mark_connected()
                    if _mag_handoff.enabled():
                        task = asyncio.create_task(_mag_resume.poll(child))
                        child._mag_resume_task = task
                        self._mag_child_resume_tasks.append(task)
                return True

            async def disconnect(self) -> None:
                for task in getattr(self, "_mag_child_resume_tasks", []):
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                for child in getattr(self, "_mag_children", []):
                    child._http_client = None
                    child._mark_disconnected()
                await super().disconnect()

            def _mag_child_for_phone(self, phone_number_id):
                phone = str(phone_number_id or "").strip()
                for adapter in self._mag_all_adapters():
                    if phone == getattr(adapter, "_phone_number_id", ""):
                        return adapter
                return None

            def mag_channel_id_for_chat(self, chat_id):
                child = self._mag_chat_to_child.get(str(chat_id or ""))
                if child is not None:
                    return getattr(child, "_mag_channel_id", "")
                return getattr(self, "_mag_channel_id", "")

            async def send(self, chat_id, content, reply_to=None, metadata=None):
                child = self._mag_chat_to_child.get(str(chat_id or ""))
                if child is not None and child is not self:
                    return await child.send(chat_id, content, reply_to=reply_to, metadata=metadata)
                return await super().send(chat_id, content, reply_to=reply_to, metadata=metadata)

            async def send_typing(self, chat_id, metadata=None):
                child = self._mag_chat_to_child.get(str(chat_id or ""))
                if child is not None and child is not self:
                    return await child.send_typing(chat_id, metadata=metadata)
                return await super().send_typing(chat_id, metadata=metadata)

            async def _dispatch_payload(self, payload):
                if not getattr(self, "_mag_children", None):
                    return await super()._dispatch_payload(payload)
                if payload.get("object") != "whatsapp_business_account":
                    return await super()._dispatch_payload(payload)
                for entry in payload.get("entry") or []:
                    if not isinstance(entry, dict):
                        continue
                    for change in entry.get("changes") or []:
                        if not isinstance(change, dict):
                            continue
                        if change.get("field") != "messages":
                            continue
                        value = change.get("value") or {}
                        metadata = value.get("metadata") or {}
                        child = self._mag_child_for_phone(metadata.get("phone_number_id"))
                        if child is None:
                            logger.warning("[whatsapp_cloud] dropping payload for unknown phone_number_id=%r", metadata.get("phone_number_id"))
                            continue
                        for raw_message in value.get("messages") or []:
                            if isinstance(raw_message, dict):
                                raw_message["_mag_channel_id"] = getattr(child, "_mag_channel_id", "")
                                sender = str(raw_message.get("from") or "").strip()
                                if sender:
                                    self._mag_chat_to_child[sender] = child
                        await _MAG_OriginalWhatsAppCloudAdapter._dispatch_payload(child, {"object": payload.get("object"), "entry": [{"changes": [change]}]})
        '''
    )
    text = text + addition
    compile(text, str(PATH), "exec")
    PATH.write_text(text)
    print("OK: multi WhatsApp Cloud patched")


if __name__ == "__main__":
    patch()
