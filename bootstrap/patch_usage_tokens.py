"""Build-time patch: include per-turn token usage + auxiliary call ledger in
the agent:end hook (MAG).

The MAG control plane meters LLM cost from the gateway's ``agent:end`` hook
(the generated ~/.hermes/hooks/mag-runtime/handler.py POSTs to
/internal/usage/events). Stock Hermes emits ``agent:end`` with only
message/response — NOT the turn's token usage — so the control plane can only
count "1 action" with no real cost.

The token counts already exist in ``agent_result`` (in scope at the emit site):
input/output/total/cache/reasoning tokens, estimated_cost_usd, cost_source,
model, provider, api_calls — aggregated across every LLM call / tool loop /
subagent in the turn. This patch adds them to the ``agent:end`` payload so the
metering hook can forward real tokens + cost per turn.

Additionally, this patch binds a per-turn auxiliary-usage ledger
(agent/mag_turn_ledger.py) at the start of ``_handle_message_with_agent`` and
forwards it as ``mag_aux_calls`` in the ``agent:end`` payload. Auxiliary calls
(vision -> gpt-4o, compression, web extraction, ...) use models separate from
the orchestrator and their usage is not in ``agent_result``; the ledger
captures them so the control plane can attribute tokens/cost to the real model
(see patch_aux_usage_ledger.py).

Idempotent + fail-loud (mirrors the other bootstrap patches).
"""

import os
import pathlib

RUN_PY = pathlib.Path(os.getenv("GATEWAY_RUN_PY", "/opt/hermes/gateway/run.py"))

MARKER = "MAG: per-turn token usage"
BIND_MARKER = "MAG: bind per-turn auxiliary-usage ledger"

# --- 1) turn-start: bind the auxiliary-usage ledger ----------------------
# _handle_message_with_agent is the turn boundary; the agent:end emit below
# lives in this same method, so _mag_turn_calls is in scope at the emit.
OLD_TURN_START = (
    '        """Inner handler that runs under the _running_agents sentinel guard."""\n'
    '        _msg_start_time = time.time()\n'
)
NEW_TURN_START = (
    '        """Inner handler that runs under the _running_agents sentinel guard."""\n'
    '        _msg_start_time = time.time()\n'
    '        # MAG: bind per-turn auxiliary-usage ledger (best-effort).\n'
    '        try:\n'
    '            from agent.mag_turn_ledger import bind_turn\n'
    '            _mag_turn_calls = bind_turn()\n'
    '        except Exception:\n'
    '            _mag_turn_calls = []\n'
)

# --- 2) agent:end emit: forward tokens + auxiliary ledger ----------------
OLD_EMIT = (
    '            await self.hooks.emit("agent:end", {\n'
    '                **hook_ctx,\n'
    '                "response": (response or "")[:500],\n'
    '            })\n'
)
NEW_EMIT = (
    '            await self.hooks.emit("agent:end", {\n'
    '                **hook_ctx,\n'
    '                "response": (response or "")[:500],\n'
    '                # MAG: per-turn token usage for control-plane cost metering\n'
    '                # (best-effort telemetry; agent_result is in scope here).\n'
    '                "input_tokens": agent_result.get("input_tokens", 0),\n'
    '                "output_tokens": agent_result.get("output_tokens", 0),\n'
    '                "total_tokens": agent_result.get("total_tokens", 0),\n'
    '                "cache_read_tokens": agent_result.get("cache_read_tokens", 0),\n'
    '                "cache_write_tokens": agent_result.get("cache_write_tokens", 0),\n'
    '                "reasoning_tokens": agent_result.get("reasoning_tokens", 0),\n'
    '                "estimated_cost_usd": agent_result.get("estimated_cost_usd"),\n'
    '                "cost_source": agent_result.get("cost_source"),\n'
    '                "model": agent_result.get("model"),\n'
    '                "provider": agent_result.get("provider"),\n'
    '                "api_calls": agent_result.get("api_calls", 0),\n'
    '                # MAG: per-call auxiliary usage (vision/etc.) attributed to\n'
    '                # the real model; collected via mag_turn_ledger. Snapshot so\n'
    '                # the forwarded payload is stable.\n'
    '                "mag_aux_calls": list(_mag_turn_calls or []),\n'
    '            })\n'
)


def main() -> None:
    if not RUN_PY.exists():
        raise SystemExit(f"gateway run.py not found at {RUN_PY}")
    text = RUN_PY.read_text(encoding="utf-8")
    changed = False

    # 1) bind ledger at turn start
    if BIND_MARKER in text:
        pass
    elif OLD_TURN_START not in text:
        raise SystemExit(
            "patch_usage_tokens: _handle_message_with_agent turn-start anchor "
            "missing (Hermes changed)."
        )
    else:
        text = text.replace(OLD_TURN_START, NEW_TURN_START, 1)
        changed = True

    # 2) extend agent:end emit
    if MARKER in text:
        pass
    elif OLD_EMIT not in text:
        raise SystemExit("patch_usage_tokens: agent:end emit anchor missing (Hermes changed).")
    else:
        text = text.replace(OLD_EMIT, NEW_EMIT, 1)
        changed = True

    if changed:
        RUN_PY.write_text(text, encoding="utf-8")
        print("OK: patched agent:end hook (token usage + auxiliary ledger)")
    else:
        print("OK: agent:end token usage + ledger already patched (idempotent no-op)")


if __name__ == "__main__":
    main()
