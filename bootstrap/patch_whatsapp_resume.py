"""V2: ingresso durável e retomada pelo fluxo real do canal. Aplicar após handoff V1."""
import os
from pathlib import Path
PATH = Path(os.getenv("WHATSAPP_CLOUD_PY", "/opt/hermes/gateway/platforms/whatsapp_cloud.py"))
MARKER = "# MAG_WHATSAPP_RESUME_V2"

def patch():
    text = PATH.read_text()
    if MARKER in text:
        print("OK: whatsapp resume already patched")
        return
    replacements = [
        ('    async def _process_message_background(self, event, session_key):\n', '    def _unwrap_ephemeral(self, response):\n        text, ttl = super()._unwrap_ephemeral(response)\n        if _mag_resume.suppress_reply(text or ""):\n            return None, 0\n        return text, ttl\n\n    async def _process_message_background(self, event, session_key):\n'),
        ('    async def _process_message_background(self, event, session_key):\n', '    async def _run_processing_hook(self, hook_name, *args, **kwargs):\n        _mag_resume.processing_hook(hook_name, args)\n        return await super()._run_processing_hook(hook_name, *args, **kwargs)\n\n    async def _process_message_background(self, event, session_key):\n'),
        ('import mag_whatsapp_handoff as _mag_handoff\n', 'import mag_whatsapp_handoff as _mag_handoff\n' + MARKER + '\nimport mag_whatsapp_resume as _mag_resume\n'),
        ('''                        _mag_state = await _mag_handoff.guard(self, str(raw_message.get("from") or ""))
                        if not _mag_state["allowed"]:
                            continue
                        raw_message["_mag_handoff_revision"] = _mag_state["revision"]
''', '''                        await _mag_resume.ingress(self, raw_message)
                        continue
'''),
        ('        self._mark_connected()\n', '        self._mark_connected()\n        if _mag_handoff.enabled():\n            self._mag_resume_task = asyncio.create_task(_mag_resume.poll(self))\n'),
        ('    async def disconnect(self) -> None:\n', '    async def disconnect(self) -> None:\n        task = getattr(self, "_mag_resume_task", None)\n        if task:\n            task.cancel()\n            try:\n                await task\n            except asyncio.CancelledError:\n                pass\n'),
        ('        if not content or not content.strip():\n', '        if _mag_resume.suppress_reply(content or ""):\n            return SendResult(success=True, message_id=None)\n        if not content or not content.strip():\n'),
    ]
    for old, new in replacements:
        if text.count(old) != 1:
            raise SystemExit("patch_whatsapp_resume: anchor drift: " + old[:70])
        text = text.replace(old, new, 1)
    compile(text, str(PATH), "exec")
    PATH.write_text(text)
    print("OK: WhatsApp durable ingress + resume queue")

if __name__ == '__main__':
    patch()
