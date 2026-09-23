"""Coexistência oficial: gate por contato + envio serializado no control plane.
Idempotente e fail-loud. Validado contra o digest fixado no Dockerfile.
"""
import os
from pathlib import Path

PATH = Path(os.getenv("WHATSAPP_CLOUD_PY", "/opt/hermes/gateway/platforms/whatsapp_cloud.py"))
MARKER = "# MAG_WHATSAPP_HANDOFF_V1"

def patch():
    text = PATH.read_text()
    if MARKER in text:
        print("OK: whatsapp handoff already patched")
        return
    import_anchor = "import asyncio\n"
    send_anchor = "await self._http_client.post(url, headers=headers, json=payload)"
    ingress_anchor = '                    wamid = str(raw_message.get("id") or "").strip()\n'
    dispatch_anchor = "                        await self.handle_message(event)\n"
    methods_anchor = "    # ------------------------------------------------------------------ helpers\n"
    if (text.count(send_anchor) != 4 or text.count(ingress_anchor) != 1 or
        text.count(dispatch_anchor) != 1 or text.count(methods_anchor) != 1 or import_anchor not in text):
        raise SystemExit("patch_whatsapp_handoff: anchor drift; review adapter before build")
    text = text.replace(import_anchor, import_anchor + MARKER + "\nimport mag_whatsapp_handoff as _mag_handoff\n", 1)
    text = text.replace(send_anchor, "await _mag_handoff.post(self, url, headers, payload)")
    text = text.replace(ingress_anchor, '''                    # Guard before dedup: API outage returns 500 and Meta can retry.
                    if metadata.get("phone_number_id") != self._phone_number_id:
                        continue
                    _mag_state = None
                    if _mag_handoff.enabled():
                        _mag_state = await _mag_handoff.guard(self, str(raw_message.get("from") or ""))
                        if not _mag_state["allowed"]:
                            continue
                        raw_message["_mag_handoff_revision"] = _mag_state["revision"]
''' + ingress_anchor, 1)
    text = text.replace(dispatch_anchor, '''                        if _mag_state is not None:
                            _mag_handoff.enrich(event, _mag_state)
''' + dispatch_anchor, 1)
    text = text.replace(methods_anchor, '''    def _can_merge_text_debounce_events(self, existing, event):
        if _mag_handoff.enabled() and getattr(existing, "_mag_handoff_revision", None) != getattr(event, "_mag_handoff_revision", None):
            return False
        return super()._can_merge_text_debounce_events(existing, event)

    async def _process_message_background(self, event, session_key):
        return await _mag_handoff.process(self, event, session_key, super()._process_message_background)

''' + methods_anchor, 1)
    compile(text, str(PATH), "exec")
    PATH.write_text(text)
    print("OK: whatsapp handoff patched (ingress + all four /messages send paths)")

if __name__ == "__main__":
    patch()
