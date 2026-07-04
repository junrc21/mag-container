"""Build-time patch: record auxiliary LLM call usage into the MAG turn ledger.

Every auxiliary LLM call (vision analysis, compression, web extraction, title
generation, ...) funnels through ``_validate_llm_response(response, task)`` in
agent/auxiliary_client.py before its content is consumed. At that point
``response.model`` and ``response.usage`` (prompt/completion tokens) are
available — but the consumers only read ``choices[0].message.content`` and
discard the usage, so the auxiliary model (e.g. gpt-4o) and its cost are never
metered by the control plane.

This patch appends a ``record_auxiliary(model, in, out, task)`` call right
before ``_validate_llm_response`` returns, so the per-turn ledger (see
mag_turn_ledger.py) captures every auxiliary call. The ledger is forwarded to
the control plane as ``mag_aux_calls`` in the ``agent:end`` payload
(patch_usage_tokens), and the control plane attributes tokens/cost to the real
model in /admin/uso-margem.

Idempotent + fail-loud (mirrors the other bootstrap patches).
"""

import os
import pathlib

AUX_PY = pathlib.Path(os.getenv("AUX_CLIENT_PY", "/opt/hermes/agent/auxiliary_client.py"))

MARKER = "MAG: record auxiliary usage"

# End of _validate_llm_response — the f-string text makes this anchor unique.
OLD_RETURN = (
    '            f"adapter or custom endpoint compatibility."\n'
    '        ) from exc\n'
    '    return response\n'
)
NEW_RETURN = (
    '            f"adapter or custom endpoint compatibility."\n'
    '        ) from exc\n'
    '    # MAG: record auxiliary usage — attribute this call to the real model\n'
    '    # (e.g. auxiliary.vision -> gpt-4o) instead of discarding it. Best-effort.\n'
    '    try:\n'
    '        from agent.mag_turn_ledger import record_auxiliary\n'
    '        _u = getattr(response, "usage", None)\n'
    '        _in_tok = (\n'
    '            getattr(_u, "prompt_tokens", None)\n'
    '            or getattr(_u, "input_tokens", None)\n'
    '            or (_u.get("prompt_tokens") if isinstance(_u, dict) else None)\n'
    '            or (_u.get("input_tokens") if isinstance(_u, dict) else None)\n'
    '            or 0\n'
    '        ) if _u is not None else 0\n'
    '        _out_tok = (\n'
    '            getattr(_u, "completion_tokens", None)\n'
    '            or getattr(_u, "output_tokens", None)\n'
    '            or (_u.get("completion_tokens") if isinstance(_u, dict) else None)\n'
    '            or (_u.get("output_tokens") if isinstance(_u, dict) else None)\n'
    '            or 0\n'
    '        ) if _u is not None else 0\n'
    '        record_auxiliary(getattr(response, "model", None), _in_tok, _out_tok, task)\n'
    '    except Exception:\n'
    '        pass\n'
    '    return response\n'
)


def main() -> None:
    if not AUX_PY.exists():
        raise SystemExit(f"auxiliary_client.py not found at {AUX_PY}")
    text = AUX_PY.read_text(encoding="utf-8")

    if MARKER in text:
        print("OK: auxiliary usage ledger already patched (idempotent no-op)")
        return
    if OLD_RETURN not in text:
        raise SystemExit(
            "patch_aux_usage_ledger: _validate_llm_response return anchor missing "
            "(Hermes changed)."
        )

    text = text.replace(OLD_RETURN, NEW_RETURN, 1)
    AUX_PY.write_text(text, encoding="utf-8")
    print("OK: patched _validate_llm_response with auxiliary usage recording")


if __name__ == "__main__":
    main()
