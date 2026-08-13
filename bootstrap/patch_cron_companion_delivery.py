"""Build-time patch: let a cron job deliver to the MAG Companion (desktop app).

## Why

Stock Hermes resolves a job's ``deliver`` string to a platform adapter. The MAG
Companion is not a Hermes platform — and by design never will be: the desktop app
authenticates against the mag-api, which then relays into the runtime. That means the
single most valuable proactive routine ("todo dia útil às 8h, me manda o resumo") could
only ever land on Telegram or WhatsApp. Someone whose whole workflow is the computer had
no way to receive it where they actually work.

## How

``deliver="companion:<id>"`` already survives the whole pipeline untouched: the
``":"`` branch of ``_resolve_single_delivery_target`` does NOT validate the platform
name (only the bare-name branch does), so the target reaches ``_deliver_result`` intact.
It then fails cleanly at ``Platform(platform_name.lower())`` with a logged delivery
error. This patch intercepts one line earlier and POSTs to the control plane instead.

That failure mode is why the anchor is safe: if this patch is ever skipped, behaviour
reverts to a clean, observable "unknown platform" delivery error — not a crash, not
silence.

## Two deliberate differences from patch_cron_job_runs.py

1. **Failure is NOT swallowed.** It appends to ``delivery_errors``, which flows into
   ``mark_job_run`` and then into the run history the client sees in "Rotinas". Without
   this, a routine delivering into the void (app uninstalled, device revoked) would
   report success forever.

2. **``media_files`` is ignored.** Those are paths INSIDE this tenant's container. The
   control plane could read them — that is exactly what ``getPendingRelay`` already does
   with ``readMediaFile`` — but the inbox model holds one attachment and this patch
   should stay dumb: it is the most fragile piece of the system. **Consequence to accept:
   a routine that generates a file (PDF, spreadsheet) should keep Telegram as its
   destination.** Wiring ``attachmentPath`` through is a cheap follow-up, not a v1.

Idempotent + fail-loud (mirrors the other bootstrap patches).
"""

import os
import pathlib

SCHEDULER_PY = pathlib.Path(os.getenv("CRON_SCHEDULER_PY", "/opt/hermes/cron/scheduler.py"))

MARKER = "MAG: companion cron delivery"

# The helper goes right before `_deliver_result` and not before `def tick(` — which is
# where patch_cron_job_runs.py injects — so the two patches never nest inside each other's
# blocks regardless of the order the Dockerfile applies them.
ANCHOR_HELPER = "def _deliver_result(job: dict, content: str, adapters=None, loop=None) -> Optional[str]:\n"

HELPER = (
    "\n"
    "def _mag_deliver_companion(job, target, content):\n"
    '    """MAG: companion cron delivery — hand the job output to the control plane.\n'
    "\n"
    "    Returns None on success, or an error string. NEVER raises: a delivery problem\n"
    "    must not take down the cron tick. But unlike the telemetry hooks, it does not\n"
    "    swallow failure either — the caller pushes the string into delivery_errors so a\n"
    "    routine delivering into the void shows up as failed in the client's run history.\n"
    '    """\n'
    "    try:\n"
    "        import os as _os\n"
    "        import json as _json\n"
    "        import urllib.request as _u\n"
    "\n"
    '        api = (_os.getenv("MAG_API_URL") or "").rstrip("/")\n'
    '        key = _os.getenv("MAG_INTERNAL_KEY") or _os.getenv("MAG_API_INTERNAL_KEY", "")\n'
    '        slug = _os.getenv("MAG_TENANT_SLUG", "")\n'
    "        if not api or not slug:\n"
    '            return "companion: runtime sem MAG_API_URL/MAG_TENANT_SLUG"\n'
    "\n"
    "        payload = {\n"
    '            "target": str(target or ""),\n'
    '            "kind": "routine",\n'
    '            "title": job.get("name") or None,\n'
    '            "body": str(content or ""),\n'
    '            "jobId": str(job.get("id") or ""),\n'
    "        }\n"
    '        body = _json.dumps(payload).encode("utf-8")\n'
    "        req = _u.Request(\n"
    '            "%s/internal/runtime/%s/companion/outbox" % (api, slug),\n'
    "            data=body,\n"
    '            headers={"Content-Type": "application/json", "x-internal-key": key},\n'
    '            method="POST",\n'
    "        )\n"
    "        raw = _u.urlopen(req, timeout=8).read()\n"
    "        # The control plane answers 200 with {enqueued:false, reason} when it chose\n"
    "        # NOT to deliver (no approved device, quiet hours, duplicate). Only 'no_device'\n"
    "        # is a real problem worth surfacing — the others are the interruption budget\n"
    "        # doing its job, and reporting them as failures would train the client to\n"
    "        # ignore the run history.\n"
    "        try:\n"
    '            parsed = _json.loads(raw.decode("utf-8"))\n'
    "        except Exception:\n"
    "            return None\n"
    '        if parsed.get("enqueued") is False and parsed.get("reason") == "no_device":\n'
    '            return "companion: nenhum computador pareado para receber"\n'
    "        return None\n"
    "    except Exception as exc:\n"
    '        return "companion: %s" % str(exc)[:200]\n'
    "\n"
    "\n"
)

# Single occurrence of `Platform(` in the whole file — verified by reading the module out
# of the shipped image. Intercepting BEFORE it means the branch that fails today becomes
# the branch that delivers.
ANCHOR_PLATFORM = "        try:\n            platform = Platform(platform_name.lower())\n"

INJECT_PLATFORM = (
    "        # MAG: companion cron delivery — o Companion não é uma plataforma do Hermes\n"
    "        # (quem autentica o device é o mag-api), então a entrega sai por HTTP pro\n"
    "        # control plane em vez de por adapter. Ver patch_cron_companion_delivery.py.\n"
    '        if platform_name.lower() == "companion":\n'
    "            _mag_err = _mag_deliver_companion(job, chat_id, cleaned_delivery_content)\n"
    "            if _mag_err:\n"
    "                logger.warning(\"Job '%s': %s\", job[\"id\"], _mag_err)\n"
    "                delivery_errors.append(_mag_err)\n"
    "            else:\n"
    "                logger.info(\"Job '%s': delivered to companion:%s\", job[\"id\"], chat_id)\n"
    "            continue\n"
    "        try:\n"
    "            platform = Platform(platform_name.lower())\n"
)


def main() -> None:
    text = SCHEDULER_PY.read_text(encoding="utf-8")

    if MARKER in text:
        print("OK: companion cron delivery already patched (idempotent)")
        return

    if ANCHOR_HELPER not in text:
        raise SystemExit("patch_cron_companion_delivery: _deliver_result anchor missing (Hermes changed).")
    if text.count(ANCHOR_PLATFORM) != 1:
        raise SystemExit(
            "patch_cron_companion_delivery: expected exactly one Platform(...) anchor "
            f"(found {text.count(ANCHOR_PLATFORM)}) — Hermes changed."
        )

    text = text.replace(ANCHOR_HELPER, HELPER + ANCHOR_HELPER, 1)
    text = text.replace(ANCHOR_PLATFORM, INJECT_PLATFORM, 1)

    SCHEDULER_PY.write_text(text, encoding="utf-8")
    print("OK: patched cron scheduler with companion delivery")


if __name__ == "__main__":
    main()
