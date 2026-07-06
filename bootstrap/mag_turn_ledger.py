"""MAG: per-turn auxiliary LLM usage ledger (best-effort telemetry).

Why this exists
---------------
The control plane meters cost from the gateway's ``agent:end`` hook using the
*orchestrator* model (``agent.model``) and ``agent.session_*`` token counters.
Those counters are incremented ONLY for the main agent loop's own LLM calls
(agent/conversation_loop.py, agent/codex_runtime.py).

Auxiliary calls (vision analysis, compression, web extraction, title
generation, ...) use **separate models** (e.g. ``auxiliary.vision -> gpt-4o``)
via agent/auxiliary_client.py. Their token usage is **not** folded into
``agent.session_*`` — it is discarded by the consumers, which only read
``response.choices[0].message.content``. As a result the auxiliary model
(gpt-4o) and its cost are invisible to the control plane (never recorded in
``usage_events``), so it never shows up in /admin/uso-margem.

This module captures per-call auxiliary usage into a ``ContextVar`` so it can
be forwarded alongside ``agent:end`` (payload field ``mag_aux_calls``).

Why a ContextVar (not a thread-local)
-------------------------------------
Auxiliary calls happen across asyncio tasks. The parent turn binds a fresh
list at turn start; child tasks INHERIT the ContextVar and append to the SAME
list object; the ``agent:end`` emitter (same method as the bind) reads it back
via its local reference. ContextVars propagate parent -> child (never up), so
the bind MUST happen in the parent turn context (see patch_usage_tokens:
``_handle_message_with_agent`` binds, ``agent:end`` reads).
"""

import contextvars
import logging

logger = logging.getLogger(__name__)

# None when no turn is bound in the current context (e.g. an auxiliary call
# fired outside of a metered turn — best-effort: we just drop it).
_TURN_CALLS: "contextvars.ContextVar[list | None]" = contextvars.ContextVar(
    "mag_turn_calls", default=None
)


def bind_turn() -> list:
    """Bind a fresh ledger to the current context. Call at turn start.

    Returns the list so the caller can also hold a local reference (the
    ``agent:end`` emitter uses that local to read the collected calls without
    touching the ContextVar again).
    """
    calls: list = []
    _TURN_CALLS.set(calls)
    return calls


def record_auxiliary(
    model,
    input_tokens,
    output_tokens,
    task=None,
    provider=None,
) -> None:
    """Append one auxiliary LLM call's usage. Best-effort — never raises."""
    try:
        calls = _TURN_CALLS.get()
        if calls is None:
            return
        calls.append(
            {
                "model": model,
                "input_tokens": int(input_tokens or 0),
                "output_tokens": int(output_tokens or 0),
                "task": task,
                "provider": provider,
            }
        )
    except Exception as exc:  # pragma: no cover - telemetry must never break the turn
        logger.debug("mag_turn_ledger.record_auxiliary failed: %s", exc)
