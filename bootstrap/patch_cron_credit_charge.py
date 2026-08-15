"""Build-time patch: cron routines never charged a single credit (MAG).

PROBLEM: the control plane only debits credits and writes a ``usage_events`` row
in reaction to the gateway's ``agent:end`` hook — see the generated
``~/.hermes/hooks/mag-runtime/handler.py``, which POSTs to
``/internal/usage/events`` on that event. That hook fires from exactly ONE
place: ``gateway/run.py``'s live-message handler
(``_handle_message_with_agent``), via ``await self.hooks.emit("agent:end", ...)``.

``cron/scheduler.py``'s ``run_job()`` never goes through that code path — it
imports ``AIAgent`` directly and calls ``agent.run_conversation(prompt)``
itself. The PRE-turn credit gate (``patch_cron_job_runs.py``'s
``_mag_check_authoritative_credits()``) already runs for every job and
correctly blocks/pauses a routine with no balance — that part always worked.
But once a routine clears the gate and its agent turn completes successfully,
nothing downstream ever reports it: no ``usage_events`` row, no debit. A
routine that ran and delivered every day, forever, cost the tenant nothing.
Reproduced live during the 2026-08 QA suite: a routine with plenty of credit
ran and delivered on schedule, and the balance never moved.

FIX: mirror the hook's own payload (same ``eventType``/``feature``, so
``creditsFor()`` treats a routine turn exactly like a chat turn) and POST it
to the same ``/internal/usage/events`` endpoint, straight from
``run_job()`` — right after ``result`` (the ``run_conversation()`` return
dict) is in hand and ``final_response`` has been resolved. Stock Hermes
already populates ``result`` with input/output/total/cache/reasoning tokens,
estimated_cost_usd, cost_source, model, provider and api_calls (same fields
``patch_usage_tokens.py`` forwards from ``agent_result`` on the chat path —
they are native to ``run_conversation()``'s return value, no upstream patch
needed to produce them). Also fills ``routineId`` — a field the control-plane
schema (``usage.schemas.ts``) already accepts and ``credit.service.ts``
already threads through as ``referenceId``, but that no caller had ever
populated until now.

Toolset attribution (for the per-tool credit trava, see F2 in the MAG plan)
is derived the same way ``gateway/run.py``'s ``_mag_toolsets_used`` /
``_mag_toolset_calls`` do — from ``tool_calls`` on ``result["messages"]`` —
duplicated here in miniature rather than imported, to avoid reaching into
``gateway.run`` from cron (that module imports ``cron.scheduler`` for
``tick()``; importing back would risk a cycle).

Known gap, left out of scope on purpose: the auxiliary-usage ledger
(``agent/mag_turn_ledger.py`` — vision/compression calls on a model separate
from the orchestrator, see ``patch_aux_usage_ledger.py``) is bound at the
START of the gateway's message handler and is not wired up for cron's
execution path. A routine's own orchestrator-model tokens ARE charged by
this patch; a routine that happens to trigger an auxiliary vision/compression
call under-reports that slice. Fixing that means binding the ledger inside
``run_job`` too — a separate, smaller follow-up, not the "charges nothing at
all" bug this patch closes.

Never raises: billing/telemetry failure must not break a routine run — same
best-effort contract as ``_mag_report_job_run`` in patch_cron_job_runs.py.

Idempotent + fail-loud (mirrors the other bootstrap patches). Runs AFTER
patch_cron_job_runs.py (same file, and this patch's anchor text — the
``final_response`` resolution inside ``run_job()`` — is upstream Hermes
source that patch never touches, so ordering relative to it doesn't matter;
listed after it in the Dockerfile purely to keep the cron-family patches
grouped together).
"""

import os
import pathlib

SCHEDULER_PY = pathlib.Path(
    os.getenv("CRON_SCHEDULER_PY", "/opt/hermes/cron/scheduler.py")
)

MARKER = "MAG: cron credit charge"

# 1) Module-level helpers, inserted right before `def run_job(`.
ANCHOR_RUN_JOB = 'def run_job(job: dict) -> tuple[bool, str, str, Optional[str]]:\n'
HELPER = (
    "_MAG_CRON_TOOL_TO_TOOLSET = None\n"
    "\n"
    "\n"
    "def _mag_cron_tool_to_toolset_map():\n"
    '    """MAG: cron credit charge — same derivation as gateway/run.py\'s\n'
    "    _mag_tool_to_toolset_map, duplicated (not imported) to avoid reaching\n"
    "    into gateway.run from cron. Cached at module scope: the tool registry\n"
    '    doesn\'t change mid-process.\n'
    '    """\n'
    "    global _MAG_CRON_TOOL_TO_TOOLSET\n"
    "    if _MAG_CRON_TOOL_TO_TOOLSET is None:\n"
    "        m = {}\n"
    "        try:\n"
    "            from tools.registry import registry as _reg\n"
    "            for _e in _reg._snapshot_entries():\n"
    "                if getattr(_e, 'name', None) and getattr(_e, 'toolset', None):\n"
    "                    m[_e.name] = _e.toolset\n"
    "        except Exception:\n"
    "            m = {}\n"
    "        _MAG_CRON_TOOL_TO_TOOLSET = m\n"
    "    return _MAG_CRON_TOOL_TO_TOOLSET\n"
    "\n"
    "\n"
    "def _mag_cron_toolset_calls(result):\n"
    '    """MAG: cron credit charge — every tool call\'s toolset WITH repeats,\n'
    "    same contract as gateway/run.py's _mag_toolset_calls (the control plane\n"
    '    sums these so a multi-tool routine turn bills every call, not just one).\n'
    '    """\n'
    "    try:\n"
    "        tmap = _mag_cron_tool_to_toolset_map()\n"
    "        out = []\n"
    "        for _msg in (result.get('messages') or []):\n"
    "            if not isinstance(_msg, dict):\n"
    "                continue\n"
    "            for _tc in (_msg.get('tool_calls') or []):\n"
    "                _fn = None\n"
    "                if isinstance(_tc, dict):\n"
    "                    _fn = (_tc.get('function') or {}).get('name')\n"
    "                if _fn and tmap.get(_fn):\n"
    "                    out.append(tmap[_fn])\n"
    "        return out\n"
    "    except Exception:\n"
    "        return []\n"
    "\n"
    "\n"
    "def _mag_report_cron_usage(job, result, session_id):\n"
    '    """MAG: cron credit charge — best-effort POST of one routine turn\'s\n'
    "    usage to the control plane, the same endpoint and payload shape the\n"
    "    chat-turn agent:end hook uses (~/.hermes/hooks/mag-runtime/handler.py),\n"
    "    so routines and chat turns are billed identically. Without this,\n"
    "    routines clear the pre-turn credit gate, run, deliver, and NEVER debit\n"
    "    anything — see the module docstring in patch_cron_credit_charge.py.\n"
    "    NEVER raises: cron must not break on billing/telemetry failure.\n"
    '    """\n'
    "    try:\n"
    "        import os as _os\n"
    "        import json as _json\n"
    "        import urllib.request as _u\n"
    "\n"
    '        api = (_os.getenv("MAG_API_URL") or "").rstrip("/")\n'
    '        key = _os.getenv("MAG_INTERNAL_KEY") or _os.getenv("MAG_API_INTERNAL_KEY", "")\n'
    '        tenant_id = _os.getenv("MAG_TENANT_ID", "")\n'
    "        if not api or not tenant_id:\n"
    "            return\n"
    "        if not isinstance(result, dict):\n"
    "            return\n"
    '        job_id = str(job.get("id") or "")\n'
    "        toolset_calls = _mag_cron_toolset_calls(result)\n"
    "        payload = {\n"
    '            "tenantId": tenant_id,\n'
    '            "eventType": "gateway.agent_end",\n'
    '            "feature": "gateway",\n'
    '            "actionsUsed": 1,\n'
    '            "model": result.get("model"),\n'
    '            "provider": result.get("provider"),\n'
    '            "inputTokens": int(result.get("input_tokens") or 0),\n'
    '            "outputTokens": int(result.get("output_tokens") or 0),\n'
    "            # UUID or None — a malformed/legacy job id must not sink the whole\n"
    "            # event (routineId is nullish server-side; see usage.schemas.ts).\n"
    '            "routineId": job_id if len(job_id) == 36 and job_id.count("-") == 4 else None,\n'
    '            "metadata": {\n'
    '                "platform": "cron",\n'
    '                "jobId": job_id,\n'
    '                "jobName": job.get("name") or None,\n'
    '                "sessionId": session_id,\n'
    '                "toolsetsUsed": sorted(set(toolset_calls)),\n'
    '                "toolsetCalls": toolset_calls,\n'
    '                "totalTokens": int(result.get("total_tokens") or 0),\n'
    '                "cacheReadTokens": int(result.get("cache_read_tokens") or 0),\n'
    '                "cacheWriteTokens": int(result.get("cache_write_tokens") or 0),\n'
    '                "reasoningTokens": int(result.get("reasoning_tokens") or 0),\n'
    '                "hermesCostUsd": result.get("estimated_cost_usd"),\n'
    '                "costSource": result.get("cost_source"),\n'
    '                "apiCalls": int(result.get("api_calls") or 0),\n'
    "            },\n"
    "        }\n"
    "        body = _json.dumps(payload).encode(\"utf-8\")\n"
    "        req = _u.Request(\n"
    '            "%s/internal/usage/events" % api,\n'
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

# 2) Call the reporter once `final_response` is resolved — same chokepoint
# shape as `_mag_report_job_run` at the end of `_process_job`, but this one
# needs `result` (with the token/cost fields), which only exists inside
# `run_job` itself; `_process_job` only receives the four-tuple it returns.
OLD_FINAL_RESPONSE = (
    '        final_response = result.get("final_response", "") or ""\n'
    "        # Strip leaked placeholder text that upstream may inject on empty completions.\n"
    '        if final_response.strip() == "(No response generated)":\n'
    '            final_response = ""\n'
)
NEW_FINAL_RESPONSE = (
    '        final_response = result.get("final_response", "") or ""\n'
    "        # Strip leaked placeholder text that upstream may inject on empty completions.\n"
    '        if final_response.strip() == "(No response generated)":\n'
    '            final_response = ""\n'
    "        _mag_report_cron_usage(job, result, _cron_session_id)  # MAG: cron credit charge\n"
)


def main() -> None:
    if not SCHEDULER_PY.exists():
        raise SystemExit(f"cron scheduler.py not found at {SCHEDULER_PY}")
    text = SCHEDULER_PY.read_text(encoding="utf-8")

    if MARKER in text:
        print("OK: cron credit charge already patched (idempotent no-op)")
        return

    if ANCHOR_RUN_JOB not in text:
        raise SystemExit("patch_cron_credit_charge: `def run_job(...)` anchor missing (Hermes changed).")
    text = text.replace(ANCHOR_RUN_JOB, HELPER + ANCHOR_RUN_JOB, 1)

    if OLD_FINAL_RESPONSE not in text:
        raise SystemExit("patch_cron_credit_charge: final_response anchor missing (Hermes changed).")
    text = text.replace(OLD_FINAL_RESPONSE, NEW_FINAL_RESPONSE, 1)

    SCHEDULER_PY.write_text(text, encoding="utf-8")
    print("OK: patched cron scheduler to charge credits for routine agent turns")


if __name__ == "__main__":
    main()
