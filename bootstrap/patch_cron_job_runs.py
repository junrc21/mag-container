"""Build-time patch: report every cron run to the MAG control plane (job history).

The client panel ("Rotinas") shows, per routine, the full run history — when it ran
and whether it succeeded or failed. Stock Hermes only keeps the LAST run status in
``/opt/data/cron/jobs.json`` (last_run_at/last_status) and per-run output markdown
files; it exposes NO run-by-run history over the gateway API. So the control plane
has nothing to build a history from.

This patch makes the cron scheduler POST a compact run record to
``{MAG_API_URL}/internal/runtime/{slug}/job-runs`` at the end of EVERY run — covering
success, soft-failures (empty/no output), delivery failures, and processing
exceptions. The control plane persists it in ``mag_job_runs`` (Postgres = durable),
so the history survives container restart/upgrade and is queryable per routine.

Injected at the single end-of-run chokepoint inside ``tick()._process_job`` where
the outcome (success/error/delivery_error/final_response) is finalized. The POST is
best-effort and NEVER raises — cron must not break on telemetry failure (same
contract as the generated ~/.hermes/hooks/mag-runtime/handler.py usage hook).

Idempotent + fail-loud (mirrors the other bootstrap patches).
"""

import os
import pathlib

SCHEDULER_PY = pathlib.Path(
    os.getenv("CRON_SCHEDULER_PY", "/opt/hermes/cron/scheduler.py")
)

MARKER = "MAG: cron run history"

# 1) Module-level helper, inserted right before `def run_one_job(` — the shared
#    per-job firing body (extracted from tick's old `_process_job` closure upstream).
ANCHOR_TICK = (
    "def run_one_job(job: dict, *, adapters=None, loop=None, verbose: bool = False) -> bool:\n"
)
HELPER = (
    "def _mag_report_job_run(job, success, error, delivery_error, started_at, output=None):\n"
    '    """MAG: cron run history — best-effort POST of one cron run to the control\n'
    "    plane (persisted in mag_job_runs, surfaced per-routine in the client panel).\n"
    "    NEVER raises: cron must not break on telemetry failure. See patch_cron_job_runs.py.\n"
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
    "            return\n"
    "        ok = bool(success) and not delivery_error\n"
    "        err = None if ok else (error or delivery_error or None)\n"
    "        if delivery_error and success:\n"
    '            err = "Falha na entrega: %s" % delivery_error\n'
    "        payload = {\n"
    '            "jobId": str(job.get("id") or ""),\n'
    '            "jobName": job.get("name") or None,\n'
    '            "status": "success" if ok else "failed",\n'
    '            "startedAt": started_at,\n'
    '            "finishedAt": _hermes_now().isoformat(),\n'
    '            "error": (str(err)[:1000] if err else None),\n'
    '            "outputPreview": (str(output)[:2000] if output else None),\n'
    '            "trigger": "scheduled",\n'
    "        }\n"
    "        body = _json.dumps(payload).encode(\"utf-8\")\n"
    "        req = _u.Request(\n"
    '            "%s/internal/runtime/%s/job-runs" % (api, slug),\n'
    "            data=body,\n"
    '            headers={"Content-Type": "application/json", "x-internal-key": key},\n'
    '            method="POST",\n'
    "        )\n"
    "        _u.urlopen(req, timeout=3).read()\n"
    "    except Exception:\n"
    "        return\n"
    "\n"
    "\n"
)

# 2) Capture a per-run start timestamp at the top of run_one_job's body (right
#    before it dispatches to run_job). Module-level function → 8-space indent.
OLD_START = (
    "    try:\n"
    "        success, output, final_response, error = run_job(job)\n"
)
NEW_START = (
    "    try:\n"
    "        _mag_run_started_at = _hermes_now().isoformat()  # MAG: cron run history\n"
    "        success, output, final_response, error = run_job(job)\n"
)

# 3) Emit the run record on BOTH the normal completion path and the exception path.
#    Module-level run_one_job → 8-space indent (was 16-space inside tick's closure).
OLD_MARK = (
    "        mark_job_run(job[\"id\"], success, error, delivery_error=delivery_error)\n"
    "        return True\n"
    "\n"
    "    except Exception as e:\n"
    "        logger.error(\"Error processing job %s: %s\", job['id'], e)\n"
    "        mark_job_run(job[\"id\"], False, str(e))\n"
    "        return False\n"
)
NEW_MARK = (
    "        mark_job_run(job[\"id\"], success, error, delivery_error=delivery_error)\n"
    "        _mag_report_job_run(job, success, error, delivery_error, _mag_run_started_at, final_response)  # MAG: cron run history\n"
    "        return True\n"
    "\n"
    "    except Exception as e:\n"
    "        logger.error(\"Error processing job %s: %s\", job['id'], e)\n"
    "        mark_job_run(job[\"id\"], False, str(e))\n"
    "        _mag_report_job_run(job, False, str(e), None, _mag_run_started_at, None)  # MAG: cron run history\n"
    "        return False\n"
)


def main() -> None:
    if not SCHEDULER_PY.exists():
        raise SystemExit(f"cron scheduler.py not found at {SCHEDULER_PY}")
    text = SCHEDULER_PY.read_text()

    if MARKER in text:
        print("OK: cron run history already patched (idempotent no-op)")
        return

    if ANCHOR_TICK not in text:
        raise SystemExit("patch_cron_job_runs: `def tick(...)` anchor missing (Hermes changed).")
    if OLD_START not in text:
        raise SystemExit("patch_cron_job_runs: `_process_job` start anchor missing (Hermes changed).")
    if OLD_MARK not in text:
        raise SystemExit("patch_cron_job_runs: mark_job_run/except anchor missing (Hermes changed).")

    text = text.replace(ANCHOR_TICK, HELPER + ANCHOR_TICK, 1)
    text = text.replace(OLD_START, NEW_START, 1)
    text = text.replace(OLD_MARK, NEW_MARK, 1)
    SCHEDULER_PY.write_text(text)
    print("OK: patched cron scheduler with per-run history reporting")


if __name__ == "__main__":
    main()
