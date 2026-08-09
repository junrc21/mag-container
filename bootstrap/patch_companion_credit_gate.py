"""Build-time patch: authoritative credit gate + real usage deduction for the
MAG Companion (desktop app), applied to gateway/platforms/api_server.py — the
adapter behind ``POST /v1/chat/completions``.

Context (see MAG plan addendum "Enforcement de créditos", 2026-08-08 — this
corrects an earlier, wrong assumption in that same plan): the Companion's
session used to be assumed "exempt" from the 9 patches that treat
``platform in ("api_server", "local", "cli")`` as an internal/staff tuple
(``patch_credit_hardcap.py``, ``patch_admin_block.py``, etc.). Reading the
actual call graph shows something more specific: those 9 patches all live
inside ``gateway/run.py``'s ``_handle_message``/``_handle_message_with_agent``
— and ``api_server.py`` (this file) is a STRUCTURALLY SEPARATE code path that
never calls into ``gateway/run.py`` at all. It isn't that the Companion is
exempted from those gates; those gates are simply unreachable from here,
regardless of the ``platform`` value. Renaming the platform alone would not
have turned any of them on.

So this patch does two things, scoped ONLY to requests that present the
``X-Hermes-Platform-Override: companion`` header (honored only when the
request is already Bearer-authenticated by ``API_SERVER_KEY`` — the same
secret MAG's control plane injects as ``MAG_INTERNAL_KEY``, see
``provisioner.worker.ts``):

1. A credit gate, reusing the same authoritative, fail-closed
   ``mag_credit_guard.check_authoritative_credits()`` that
   ``patch_credit_hardcap.py`` already uses for normal chat turns — checked
   BEFORE the agent ever runs, returning an OpenAI-shaped 402 if blocked.
2. Real usage reporting to ``POST {MAG_API_URL}/internal/usage/events`` after
   a successful turn — mirroring the generated
   ``~/.hermes/hooks/mag-runtime/handler.py`` ``agent:end`` hook, which (same
   root cause as above) never fires for this code path either. Without this,
   Companion turns that DO run successfully would never debit the tenant's
   credit balance at all.

Other callers of ``/v1/chat/completions`` (``adminChat``, ``ingestKnowledge``,
``analyzeBusinessDocuments`` — none of which send the override header) are
completely unaffected: ``platform_override`` defaults to ``None`` everywhere,
preserving the exact current behavior (``platform="api_server"``, no gate, no
usage POST) for every caller except the Companion.

Explicitly NOT covered by this patch (see plan addendum for the full
reasoning): the other 8 "internal tuple" gates (output sanitization, admin
block, channel code-exec disable, etc.) stay unreachable from this path, same
as before. ``companion.service.ts`` (mag-api) already duplicates the
tenant-blocked check on its own side as defense in depth, so that specific gap
is covered elsewhere.

Idempotent + fail-loud (mirrors the other bootstrap patches).
"""

import os
import pathlib

API_SERVER_PY = pathlib.Path(
    os.getenv("API_SERVER_PY", "/opt/hermes/gateway/platforms/api_server.py")
)

MARKER = "MAG: companion credit gate"

# 1) Module-level helpers, inserted right before `def _hermes_version()` — the
# first module-level function in the file, right after imports/logger setup.
IMPORT_ANCHOR = "def _hermes_version() -> str:\n"
IMPORT_BLOCK = (
    "# MAG: companion credit gate — shared authoritative check with\n"
    "# patch_credit_hardcap.py (chat) and patch_cron_job_runs.py (cron).\n"
    "from mag_credit_guard import CreditStatus as _MagCreditStatus\n"
    "from mag_credit_guard import check_authoritative_credits as _mag_check_authoritative_credits\n"
    "\n"
    "_MAG_COMPANION_PLATFORM = 'companion'\n"
    "_MAG_CREDIT_LIMIT_MSG_FREE = (\n"
    "    'Você usou todos os seus créditos gratuitos. Para continuar usando a MAG, '\n"
    "    'faça upgrade para um plano pago em Uso e Plano no painel de controle.'\n"
    ")\n"
    "_MAG_CREDIT_LIMIT_MSG_PAID = (\n"
    "    'Você atingiu o limite de créditos do seu plano este mês. Para continuar agora, '\n"
    "    'reforce seus créditos ou faça upgrade em Uso e Plano no painel de controle.'\n"
    ")\n"
    "_MAG_CREDIT_VERIFY_MSG = (\n"
    "    'Não foi possível verificar seus créditos agora. Tente novamente em instantes.'\n"
    ")\n"
    "\n"
    "\n"
    "def _mag_parse_platform_override(request, api_key):\n"
    '    """Read X-Hermes-Platform-Override — only honored on an already\n'
    "    Bearer-authenticated request (api_key must be configured; the caller\n"
    "    already ran _check_auth before this). Any value other than the one\n"
    "    known platform name is ignored — same fail-safe posture as an absent\n"
    '    header, defense in depth even though the call is already authenticated.\n'
    '    """\n'
    "    if not api_key:\n"
    "        return None\n"
    "    raw = request.headers.get('X-Hermes-Platform-Override', '').strip()\n"
    "    if raw != _MAG_COMPANION_PLATFORM:\n"
    "        return None\n"
    "    return raw\n"
    "\n"
    "\n"
    "def _mag_companion_credit_block_message():\n"
    '    """Return a client-safe block message, or None only when credit is\n'
    "    available. Mirrors patch_credit_hardcap.py's chat-turn gate exactly —\n"
    "    same authoritative, fail-closed check, applied here because\n"
    "    gateway/run.py's own gate never runs on this code path.\n"
    '    """\n'
    "    check = _mag_check_authoritative_credits()\n"
    "    if check.status is _MagCreditStatus.AVAILABLE:\n"
    "        return None\n"
    "    if check.status is _MagCreditStatus.EXHAUSTED:\n"
    "        return _MAG_CREDIT_LIMIT_MSG_FREE if check.plan == 'free' else _MAG_CREDIT_LIMIT_MSG_PAID\n"
    "    return _MAG_CREDIT_VERIFY_MSG\n"
    "\n"
    "\n"
    "def _mag_report_companion_usage(usage, model, provider):\n"
    '    """Best-effort POST to /internal/usage/events — mirrors the generated\n'
    "    ~/.hermes/hooks/mag-runtime/handler.py agent:end hook, which never\n"
    "    fires for this code path (api_server.py never calls into\n"
    "    gateway/run.py, where that hook is wired). Without this, a\n"
    "    successful Companion turn would consume real tokens/tool time but\n"
    "    never debit the tenant's credit balance. NEVER raises — same\n"
    "    contract as every other MAG usage-reporting hook.\n"
    '    """\n'
    "    try:\n"
    "        import os as _os\n"
    "        import json as _json\n"
    "        import urllib.request as _u\n"
    "\n"
    "        api = (_os.getenv('MAG_API_URL') or '').rstrip('/')\n"
    "        key = _os.getenv('MAG_INTERNAL_KEY') or _os.getenv('MAG_API_INTERNAL_KEY', '')\n"
    "        tenant_id = _os.getenv('MAG_TENANT_ID', '')\n"
    "        if not api or not tenant_id:\n"
    "            return\n"
    "        payload = {\n"
    "            'tenantId': tenant_id,\n"
    "            'eventType': 'companion.agent_end',\n"
    "            'feature': 'gateway',\n"
    "            'model': model,\n"
    "            'provider': provider,\n"
    "            'inputTokens': int(usage.get('input_tokens') or 0),\n"
    "            'outputTokens': int(usage.get('output_tokens') or 0),\n"
    "            'metadata': {'platform': _MAG_COMPANION_PLATFORM},\n"
    "        }\n"
    "        body = _json.dumps(payload).encode('utf-8')\n"
    "        req = _u.Request(\n"
    "            '%s/internal/usage/events' % api,\n"
    "            data=body,\n"
    "            headers={'Content-Type': 'application/json', 'x-internal-key': key},\n"
    "            method='POST',\n"
    "        )\n"
    "        _u.urlopen(req, timeout=3).read()\n"
    "    except Exception:\n"
    "        return\n"
    "\n"
    "\n"
)

# 2) Gate check inside _handle_chat_completions, right after auth — before any
# body parsing, so a blocked request never even looks at `messages`.
GATE_ANCHOR = (
    "        auth_err = self._check_auth(request)\n"
    "        if auth_err:\n"
    "            return auth_err\n"
    "\n"
    "        # Parse request body\n"
)
GATE_BLOCK = (
    "        auth_err = self._check_auth(request)\n"
    "        if auth_err:\n"
    "            return auth_err\n"
    "\n"
    "        # MAG: companion credit gate — see patch_companion_credit_gate.py.\n"
    "        platform_override = _mag_parse_platform_override(request, self._api_key)\n"
    "        if platform_override == _MAG_COMPANION_PLATFORM:\n"
    "            _mag_block = _mag_companion_credit_block_message()\n"
    "            if _mag_block is not None:\n"
    "                return web.json_response(\n"
    "                    _openai_error(_mag_block, err_type='insufficient_quota', code='credits_exhausted'),\n"
    "                    status=402,\n"
    "                )\n"
    "\n"
    "        # Parse request body\n"
)

# 3) Propagate platform_override through the two /v1/chat/completions call
# sites (streaming + non-streaming). The four OTHER _run_agent call sites in
# this file — /api/sessions/{id}/chat[/stream], /v1/responses — are untouched
# on purpose: the Companion never calls those routes, and leaving their calls
# unmodified means they keep passing no platform_override (defaults to None,
# identical behavior to before this patch).
STREAM_CALL_ANCHOR = (
    "            agent_task = asyncio.ensure_future(self._run_agent(\n"
    "                user_message=user_message,\n"
    "                conversation_history=history,\n"
    "                ephemeral_system_prompt=system_prompt,\n"
    "                session_id=session_id,\n"
    "                stream_delta_callback=_on_delta,\n"
    "                tool_start_callback=_on_tool_start,\n"
    "                tool_complete_callback=_on_tool_complete,\n"
    "                agent_ref=agent_ref,\n"
    "                gateway_session_key=gateway_session_key,\n"
    "            ))\n"
)
STREAM_CALL_BLOCK = (
    "            agent_task = asyncio.ensure_future(self._run_agent(\n"
    "                user_message=user_message,\n"
    "                conversation_history=history,\n"
    "                ephemeral_system_prompt=system_prompt,\n"
    "                session_id=session_id,\n"
    "                stream_delta_callback=_on_delta,\n"
    "                tool_start_callback=_on_tool_start,\n"
    "                tool_complete_callback=_on_tool_complete,\n"
    "                agent_ref=agent_ref,\n"
    "                gateway_session_key=gateway_session_key,\n"
    "                platform_override=platform_override,  # MAG: companion credit gate\n"
    "            ))\n"
)
NONSTREAM_CALL_ANCHOR = (
    "            return await self._run_agent(\n"
    "                user_message=user_message,\n"
    "                conversation_history=history,\n"
    "                ephemeral_system_prompt=system_prompt,\n"
    "                session_id=session_id,\n"
    "                gateway_session_key=gateway_session_key,\n"
    "            )\n"
)
NONSTREAM_CALL_BLOCK = (
    "            return await self._run_agent(\n"
    "                user_message=user_message,\n"
    "                conversation_history=history,\n"
    "                ephemeral_system_prompt=system_prompt,\n"
    "                session_id=session_id,\n"
    "                gateway_session_key=gateway_session_key,\n"
    "                platform_override=platform_override,  # MAG: companion credit gate\n"
    "            )\n"
)

# 4) _run_agent signature — new trailing kwarg, defaults to None so the four
# untouched call sites (see above) behave exactly as before.
SIGNATURE_ANCHOR = (
    "        agent_ref: Optional[list] = None,\n"
    "        gateway_session_key: Optional[str] = None,\n"
    "    ) -> tuple:\n"
)
SIGNATURE_BLOCK = (
    "        agent_ref: Optional[list] = None,\n"
    "        gateway_session_key: Optional[str] = None,\n"
    "        platform_override: Optional[str] = None,  # MAG: companion credit gate\n"
    "    ) -> tuple:\n"
)

# 5) set_session_vars — keep the actual Hermes platform as api_server.
#
# Important: the Companion override is an out-of-band MAG control-plane signal, not
# a native Hermes platform. Hermes' prompt/hint stack only knows configured platform
# names such as "api_server", "telegram", "whatsapp_cloud", etc. Running a turn as
# platform="companion" can break downstream platform lookups. The override is still
# propagated to _run_agent so the scoped credit gate and usage reporting below can
# fire, but the agent session itself remains on the known api_server platform.
SESSION_VARS_ANCHOR = (
    "            tokens = set_session_vars(\n"
    '                platform="api_server",\n'
    '                chat_id=session_id or "",\n'
    '                session_key=gateway_session_key or session_id or "",\n'
    '                session_id=session_id or "",\n'
    "            )\n"
)
SESSION_VARS_BLOCK = (
    "            tokens = set_session_vars(\n"
    '                platform="api_server",  # MAG: companion gate keeps Hermes on a known platform\n'
    '                chat_id=session_id or "",\n'
    '                session_key=gateway_session_key or session_id or "",\n'
    '                session_id=session_id or "",\n'
    "            )\n"
)

# 6) Real usage deduction — right after the usage dict is built, before the
# function returns. Only fires for the Companion (platform_override set);
# every other caller of _run_agent is unaffected.
USAGE_ANCHOR = (
    "                usage = {\n"
    '                    "input_tokens": getattr(agent, "session_prompt_tokens", 0) or 0,\n'
    '                    "output_tokens": getattr(agent, "session_completion_tokens", 0) or 0,\n'
    '                    "total_tokens": getattr(agent, "session_total_tokens", 0) or 0,\n'
    "                }\n"
)
USAGE_BLOCK = (
    "                usage = {\n"
    '                    "input_tokens": getattr(agent, "session_prompt_tokens", 0) or 0,\n'
    '                    "output_tokens": getattr(agent, "session_completion_tokens", 0) or 0,\n'
    '                    "total_tokens": getattr(agent, "session_total_tokens", 0) or 0,\n'
    "                }\n"
    "                if platform_override == _MAG_COMPANION_PLATFORM:  # MAG: companion credit gate\n"
    "                    _mag_report_companion_usage(usage, getattr(agent, \"model\", None), getattr(agent, \"provider\", None))\n"
)


def main() -> None:
    if not API_SERVER_PY.exists():
        raise SystemExit(f"api_server.py not found at {API_SERVER_PY}")
    text = API_SERVER_PY.read_text(encoding="utf-8")

    if MARKER in text:
        print("OK: companion credit gate already patched (idempotent no-op)")
        return

    for anchor, block, label in (
        (IMPORT_ANCHOR, IMPORT_BLOCK + IMPORT_ANCHOR, "module helpers"),
        (GATE_ANCHOR, GATE_BLOCK, "credit gate in _handle_chat_completions"),
        (STREAM_CALL_ANCHOR, STREAM_CALL_BLOCK, "streaming _run_agent call site"),
        (NONSTREAM_CALL_ANCHOR, NONSTREAM_CALL_BLOCK, "non-streaming _run_agent call site"),
        (SIGNATURE_ANCHOR, SIGNATURE_BLOCK, "_run_agent signature"),
        (SESSION_VARS_ANCHOR, SESSION_VARS_BLOCK, "set_session_vars platform label"),
        (USAGE_ANCHOR, USAGE_BLOCK, "usage deduction POST"),
    ):
        if anchor not in text:
            raise SystemExit(f"patch_companion_credit_gate: anchor missing for '{label}' (Hermes changed).")
        text = text.replace(anchor, block, 1)
        print(f"  [ok]   {label}")

    API_SERVER_PY.write_text(text, encoding="utf-8")
    print("OK: patched api_server.py with the companion credit gate")


if __name__ == "__main__":
    main()
