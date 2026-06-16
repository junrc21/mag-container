"""Build-time patch: sanitize technical cron job errors sent to client channels.

PROBLEM: When a cron job fails (e.g. "No Codex credentials stored. Run hermes auth..."),
Hermes sends the raw technical error directly to the client channel (Telegram/WhatsApp).

The error is included in deliver_content at line ~2103:
    deliver_content = final_response if success else f"⚠️ Cron job '{job.get('name', job['id'])}' failed:\n{error}"

This violates product secrecy and confuses the user.

SOLUTION: This patch adds a sanitize function and applies it BEFORE errors are used in:
- deliver_content (sent to channels)
- mark_job_run (saved to jobs.json)
- _mag_report_job_run (sent to control plane)
- logger.error (logs)

Idempotent + fail-loud (mirrors the other bootstrap patches).
"""

import os
import pathlib
import re

SCHEDULER_PY = pathlib.Path(
    os.getenv("CRON_SCHEDULER_PY", "/opt/hermes/cron/scheduler.py")
)

MARKER = "MAG: sanitize cron job errors"

# Helper function to sanitize error messages - inserted before tick()
SANITIZE_HELPER = '''# MAG: sanitize cron job errors sent to client channels
def _mag_sanitize_cron_error(msg: str) -> str:
    """Sanitize technical error messages before they reach client channels."""
    if not msg:
        return msg
    import re
    technical_patterns = [
        r"no codex credentials stored",
        r"Run hermes auth",
        r"Run hermes model",
        r"authentication",
        r"credential",
        r"API key",
        r"token.*expired",
        r"RuntimeError",
        r"Traceback",
        r'File ".*"',
        r"\\.env",
        r"/opt/",
    ]
    msg_lower = msg.lower()
    for pattern in technical_patterns:
        if re.search(pattern, msg_lower, re.IGNORECASE):
            return "Nao consegui processar essa tarefa agora. Tente novamente em instantes ou entre em contato com o suporte da CyriusX."
    return msg


'''


def main() -> None:
    if not SCHEDULER_PY.exists():
        raise SystemExit(f"cron scheduler.py not found at {SCHEDULER_PY}")

    text = SCHEDULER_PY.read_text(encoding='utf-8')

    if MARKER in text:
        print("OK: cron error sanitization already patched (idempotent no-op)")
        return

    # Check anchors
    if "def tick(" not in text:
        raise SystemExit("patch_sanitize_cron_errors: `def tick(` anchor missing (Hermes changed).")
    if 'mark_job_run' not in text:
        raise SystemExit("patch_sanitize_cron_errors: `mark_job_run` anchor missing (Hermes changed).")
    if "deliver_content = final_response if success else" not in text:
        raise SystemExit("patch_sanitize_cron_errors: `deliver_content` anchor missing (Hermes changed).")

    edits = 0

    # 1) Add sanitize helper function before tick()
    if "_mag_sanitize_cron_error" not in text:
        # Find the last import line and insert before def tick(
        lines = text.split('\n')
        insert_idx = 0
        for i, line in enumerate(lines):
            if line.strip().startswith(('import ', 'from ')):
                insert_idx = i + 1
            elif 'def tick(' in line:
                insert_idx = i
                break

        if insert_idx > 0:
            lines.insert(insert_idx, SANITIZE_HELPER.strip())
            text = '\n'.join(lines)
            edits += 1

    # 2) Sanitize error BEFORE it's used in deliver_content
    # OLD: deliver_content = final_response if success else f"⚠️ Cron job '{job.get('name', job['id'])}' failed:\n{error}"
    # NEW: error = _mag_sanitize_cron_error(error)
    #      deliver_content = final_response if success else f"⚠️ Cron job '{job.get('name', job['id'])}' failed:\n{error}"
    OLD_DELIVERY = (
        "                deliver_content = final_response if success else f\"⚠️ Cron job '{job.get('name', job['id'])}' failed:\\n{error}\""
    )
    NEW_DELIVERY = (
        "                # MAG: sanitize error before including in deliver_content\n"
        "                error = _mag_sanitize_cron_error(error)\n"
        "                deliver_content = final_response if success else f\"⚠️ Cron job '{job.get('name', job['id'])}' failed:\\n{error}\""
    )
    if OLD_DELIVERY in text:
        text = text.replace(OLD_DELIVERY, NEW_DELIVERY, 1)
        edits += 1
    elif "sanitize error before including in deliver_content" not in text:
        raise SystemExit("patch_sanitize_cron_errors: deliver_content anchor missing (Hermes changed).")

    # 3) Sanitize error BEFORE mark_job_run and _mag_report_job_run
    # OLD: mark_job_run(job["id"], success, error, delivery_error=delivery_error)
    # NEW: mark_job_run(job["id"], success, _mag_sanitize_cron_error(error), delivery_error=delivery_error)
    OLD_MARK_RUN = (
        "                mark_job_run(job[\"id\"], success, error, delivery_error=delivery_error)"
    )
    NEW_MARK_RUN = (
        "                mark_job_run(job[\"id\"], success, _mag_sanitize_cron_error(error), delivery_error=delivery_error)"
    )
    if OLD_MARK_RUN in text:
        text = text.replace(OLD_MARK_RUN, NEW_MARK_RUN, 1)
        edits += 1

    # 4) Sanitize error in _mag_report_job_run
    # OLD: _mag_report_job_run(job, success, error, delivery_error, _mag_run_started_at, final_response)
    # NEW: _mag_report_job_run(job, success, _mag_sanitize_cron_error(error), delivery_error, _mag_run_started_at, final_response)
    OLD_REPORT_RUN = (
        "                _mag_report_job_run(job, success, error, delivery_error, _mag_run_started_at, final_response)"
    )
    NEW_REPORT_RUN = (
        "                _mag_report_job_run(job, success, _mag_sanitize_cron_error(error), delivery_error, _mag_run_started_at, final_response)"
    )
    if OLD_REPORT_RUN in text:
        text = text.replace(OLD_REPORT_RUN, NEW_REPORT_RUN, 1)
        edits += 1

    # 5) Sanitize exception in except block
    # OLD: logger.error("Error processing job %s: %s", job['id'], e)
    #      mark_job_run(job["id"], False, str(e))
    #      _mag_report_job_run(job, False, str(e), None, _mag_run_started_at, None)
    # NEW: sanitized_e = _mag_sanitize_cron_error(str(e))
    #      logger.error("Error processing job %s: %s", job['id'], sanitized_e)
    #      mark_job_run(job["id"], False, sanitized_e)
    #      _mag_report_job_run(job, False, sanitized_e, None, _mag_run_started_at, None)
    OLD_EXCEPT_BLOCK = (
        "            except Exception as e:\n"
        "                logger.error(\"Error processing job %s: %s\", job['id'], e)\n"
        "                mark_job_run(job[\"id\"], False, str(e))\n"
        "                _mag_report_job_run(job, False, str(e), None, _mag_run_started_at, None)"
    )
    NEW_EXCEPT_BLOCK = (
        "            except Exception as e:\n"
        "                sanitized_e = _mag_sanitize_cron_error(str(e))\n"
        "                logger.error(\"Error processing job %s: %s\", job['id'], sanitized_e)\n"
        "                mark_job_run(job[\"id\"], False, sanitized_e)\n"
        "                _mag_report_job_run(job, False, sanitized_e, None, _mag_run_started_at, None)"
    )
    if OLD_EXCEPT_BLOCK in text:
        text = text.replace(OLD_EXCEPT_BLOCK, NEW_EXCEPT_BLOCK, 1)
        edits += 1

    if edits == 0:
        print("OK: cron error sanitization already applied (idempotent no-op)")
        return

    SCHEDULER_PY.write_text(text, encoding='utf-8')
    print(f"OK: patched {SCHEDULER_PY} with cron error sanitization ({edits} edit(s))")


if __name__ == "__main__":
    main()
