"""Build-time patch: include per-turn token usage in the agent:end hook (MAG).

The MAG control plane meters LLM cost from the gateway's ``agent:end`` hook (the
generated ~/.hermes/hooks/mag-runtime/handler.py POSTs to /internal/usage/events).
Stock Hermes emits ``agent:end`` with only message/response — NOT the turn's token
usage — so the control plane can only count "1 action" with no real cost.

The token counts already exist in ``agent_result`` (in scope at the emit site):
input/output/total/cache/reasoning tokens, estimated_cost_usd, cost_source, model,
provider, api_calls — aggregated across every LLM call / tool loop / subagent in
the turn. This patch adds them to the ``agent:end`` payload so the metering hook
can forward real tokens + cost per turn.

Idempotent + fail-loud (mirrors the other bootstrap patches).
"""

import os
import pathlib

RUN_PY = pathlib.Path(os.getenv("GATEWAY_RUN_PY", "/opt/hermes/gateway/run.py"))

MARKER = "MAG: per-turn token usage"

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
    '            })\n'
)


def main() -> None:
    if not RUN_PY.exists():
        raise SystemExit(f"gateway run.py not found at {RUN_PY}")
    text = RUN_PY.read_text()

    if MARKER in text:
        print("OK: agent:end token usage already patched (idempotent no-op)")
        return
    if OLD_EMIT not in text:
        raise SystemExit("patch_usage_tokens: agent:end emit anchor missing (Hermes changed).")

    text = text.replace(OLD_EMIT, NEW_EMIT, 1)
    RUN_PY.write_text(text)
    print("OK: patched agent:end hook with per-turn token usage")


if __name__ == "__main__":
    main()
