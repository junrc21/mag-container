"""Build-time patch: report the toolsets used in a turn (for per-tool credits).

The MAG control plane charges credits per turn weighted by the toolsets the turn
used (admin-tunable in toolset_credit_costs). Stock Hermes doesn't surface this,
so this patch:
  1. Adds helpers that map each tool the agent called (from the turn's tool_calls)
     to its toolset, using the in-process tool registry.
  2. Adds ``toolsets_used`` to the agent:end hook payload so the usage hook can
     forward it; the control plane then bills max(cost of used toolsets, 1).

Idempotent + fail-loud (mirrors the other bootstrap patches).
"""

import os
import pathlib

RUN_PY = pathlib.Path(os.getenv("GATEWAY_RUN_PY", "/opt/hermes/gateway/run.py"))

MARKER = "_mag_toolsets_used"

HELPERS_ANCHOR = "def _gateway_platform_value(platform: Any) -> str:"
HELPERS = '''# MAG: map the tools a turn called to their toolsets, for per-tool credit billing.
_MAG_TOOL_TO_TOOLSET = None


def _mag_tool_to_toolset_map():
    global _MAG_TOOL_TO_TOOLSET
    if _MAG_TOOL_TO_TOOLSET is None:
        m = {}
        try:
            from tools.registry import registry as _reg
            for _e in _reg._snapshot_entries():
                if getattr(_e, "name", None) and getattr(_e, "toolset", None):
                    m[_e.name] = _e.toolset
        except Exception:
            m = {}
        _MAG_TOOL_TO_TOOLSET = m
    return _MAG_TOOL_TO_TOOLSET


def _mag_toolsets_used(agent_result):
    """Distinct toolsets used in the turn, derived from the messages' tool_calls."""
    try:
        tmap = _mag_tool_to_toolset_map()
        used = set()
        for _msg in (agent_result.get("messages") or []):
            if not isinstance(_msg, dict):
                continue
            for _tc in (_msg.get("tool_calls") or []):
                _fn = None
                if isinstance(_tc, dict):
                    _fn = (_tc.get("function") or {}).get("name")
                if _fn and tmap.get(_fn):
                    used.add(tmap[_fn])
        return sorted(used)
    except Exception:
        return []


'''

OLD_LINE = '                "response": (response or "")[:500],\n'
NEW_LINE = (
    '                "response": (response or "")[:500],\n'
    '                "toolsets_used": _mag_toolsets_used(agent_result),\n'
)


def main() -> None:
    if not RUN_PY.exists():
        raise SystemExit(f"gateway run.py not found at {RUN_PY}")
    text = RUN_PY.read_text()

    if MARKER in text:
        print("OK: toolsets_used already patched (idempotent no-op)")
        return
    if HELPERS_ANCHOR not in text:
        raise SystemExit("patch_toolsets_used: helpers anchor missing (Hermes changed).")
    if OLD_LINE not in text:
        raise SystemExit("patch_toolsets_used: agent:end response-line anchor missing (Hermes changed).")

    text = text.replace(HELPERS_ANCHOR, HELPERS + HELPERS_ANCHOR, 1)
    text = text.replace(OLD_LINE, NEW_LINE, 1)
    RUN_PY.write_text(text)
    print("OK: patched agent:end with toolsets_used")


if __name__ == "__main__":
    main()
