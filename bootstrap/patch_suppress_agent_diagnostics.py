"""Build-time patch: stop 4 raw-diagnostic leaks to client channels (MAG barreira
de sigilo, CLAUDE.md).

These are synthetic messages the gateway constructs itself (not LLM output),
fired via a direct ``adapter.send()`` call or a ``final_response`` dict built
outside the normal turn — so they completely bypass BOTH the one existing
systemic sanitizer (``_sanitize_gateway_final_response``, see
``patch_gateway_output.py``) and the slash-command gate
(``patch_disable_channel_commands.py``, which only covers commands — none of
these 4 sites are commands).

A broader sweep found 8 candidate raw-bypass sites in gateway/run.py; 4 of them
(Discord `/voice join` PyNaCl failure, the destructive-slash "always" opt-out
note, `/update` progress/finish messages, and the dead proxy-mode path) are
ALREADY unreachable on client channels — they only fire from inside
slash-command dispatch, which ``patch_disable_channel_commands.py`` already
restricts to internal/staff surfaces + `/start`. Only the 4 below fire
automatically (no command needed) and were still leaking:

  1. STT-unavailable message (``_handle_message_with_agent``): leaks
     `uv pip install faster-whisper`, `stt.enabled`/config.yaml, `/restart`,
     `/skill hermes-agent-setup`. Fires on every voice message when no STT
     provider resolves. The client gets a friendly pt-BR ack instead — the
     user did send something real and deserves a reply, not silence.
  2. Context-compression-aborted warning: "...check your auxiliary.compression
     model configuration." Suppressed entirely on client channels — nothing
     was dropped, this is purely an ops note with no user expectation of a
     reply.
  3. Aux-model-fallback notice: reveals the configured model string + "check
     auxiliary.compression.model in config.yaml." Suppressed on client
     channels for the same reason as #2.
  4. Agent-inactivity timeout: a staged "No activity" warning (mentions
     /reset, dead on client channels anyway) AND a final diagnostic response
     (tool name, iteration/max_iterations, "agent.gateway_timeout in
     config.yaml") that DOES pass through ``_sanitize_gateway_final_response``
     but isn't caught by either of its regexes today. Both replaced with
     generic pt-BR copy on client channels; internal surfaces
     (api_server/local/cli) keep every original message verbatim for staff
     debugging.

Gate condition standardized on ``_gateway_platform_value(platform) not in
("api_server", "local", "cli")`` — the same "client channel" variant already
used by ``patch_suppress_reset_banner.py`` and by
``_sanitize_gateway_final_response`` itself. (A different, inverted tuple is
used in ``patch_forbidden_topics_gate.py`` — that's a separate pre-existing
inconsistency, not touched here.)

Idempotent + fail-loud (mirrors the other bootstrap patches).
"""

import os
import pathlib

RUN_PY = pathlib.Path(os.getenv("GATEWAY_RUN_PY", "/opt/hermes/gateway/run.py"))

MARKER = "MAG_suppress_diagnostic_leaks"

# --- Edit 1: STT-unavailable message --------------------------------------------
OLD_1 = r'''                            if self._has_setup_skill():
                                _stt_msg += "\n\nFor full setup instructions, type: `/skill hermes-agent-setup`"
                            await _stt_adapter.send(
'''
NEW_1 = r'''                            if self._has_setup_skill():
                                _stt_msg += "\n\nFor full setup instructions, type: `/skill hermes-agent-setup`"
                            # MAG_suppress_diagnostic_leaks: never leak the
                            # install/config diagnostic to a client channel — the
                            # user sent real audio and deserves a reply, just not
                            # an engineering one. Internal surfaces keep it raw.
                            if _gateway_platform_value(source.platform) not in ("api_server", "local", "cli"):
                                _stt_msg = (
                                    "🎤 Recebi seu áudio, mas no momento não consigo "
                                    "transcrever mensagens de voz. Pode me mandar em texto, por favor?"
                                )
                            await _stt_adapter.send(
'''

# --- Edit 2: context-compression-aborted warning --------------------------------
OLD_2 = r'''                                        try:
                                            _adapter = self.adapters.get(source.platform)
                                            if _adapter and source.chat_id:
                                                await _adapter.send(source.chat_id, _warn_msg, metadata=_hyg_meta)
                                        except Exception as _werr:
'''
NEW_2 = r'''                                        try:
                                            _adapter = self.adapters.get(source.platform)
                                            # MAG_suppress_diagnostic_leaks: internal ops
                                            # note (nothing was dropped) — never surface
                                            # it on a client channel.
                                            if (_adapter and source.chat_id
                                                    and _gateway_platform_value(source.platform) in ("api_server", "local", "cli")):
                                                await _adapter.send(source.chat_id, _warn_msg, metadata=_hyg_meta)
                                        except Exception as _werr:
'''

# --- Edit 3: aux-model-fallback notice ------------------------------------------
OLD_3 = r'''                                        try:
                                            _adapter = self.adapters.get(source.platform)
                                            if _adapter and source.chat_id:
                                                await _adapter.send(source.chat_id, _aux_msg, metadata=_hyg_meta)
                                        except Exception as _werr:
'''
NEW_3 = r'''                                        try:
                                            _adapter = self.adapters.get(source.platform)
                                            # MAG_suppress_diagnostic_leaks: reveals the
                                            # configured model + config.yaml key —
                                            # internal only.
                                            if (_adapter and source.chat_id
                                                    and _gateway_platform_value(source.platform) in ("api_server", "local", "cli")):
                                                await _adapter.send(source.chat_id, _aux_msg, metadata=_hyg_meta)
                                        except Exception as _werr:
'''

# --- Edit 4: agent-inactivity staged warning ------------------------------------
OLD_4 = r'''                            try:
                                await _warn_adapter.send(
                                    source.chat_id,
                                    f"⚠️ No activity for {_elapsed_warn} min. "
                                    f"If the agent does not respond soon, it will "
                                    f"be timed out in {_remaining_mins} min. "
                                    f"You can continue waiting or use /reset.",
                                    metadata=_status_thread_metadata,
                                )
                            except Exception as _warn_err:
'''
NEW_4 = r'''                            try:
                                # MAG_suppress_diagnostic_leaks: no /reset mention
                                # (dead on client channels) on a client surface.
                                if _gateway_platform_value(source.platform) in ("api_server", "local", "cli"):
                                    _warn_text = (
                                        f"⚠️ No activity for {_elapsed_warn} min. "
                                        f"If the agent does not respond soon, it will "
                                        f"be timed out in {_remaining_mins} min. "
                                        f"You can continue waiting or use /reset."
                                    )
                                else:
                                    _warn_text = "Ainda estou trabalhando no seu pedido, só mais um instante. 🙂"
                                await _warn_adapter.send(
                                    source.chat_id,
                                    _warn_text,
                                    metadata=_status_thread_metadata,
                                )
                            except Exception as _warn_err:
'''

# --- Edit 5: agent-inactivity final diagnostic ----------------------------------
OLD_5 = r'''                # Construct a user-facing message with diagnostic context.
                _diag_lines = [
                    f"⏱️ Agent inactive for {_timeout_mins} min — no tool calls "
                    f"or API responses."
                ]
                if _cur_tool:
                    _diag_lines.append(
                        f"The agent appears stuck on tool `{_cur_tool}` "
                        f"({_secs_ago:.0f}s since last activity, "
                        f"iteration {_iter_n}/{_iter_max})."
                    )
                else:
                    _diag_lines.append(
                        f"Last activity: {_last_desc} ({_secs_ago:.0f}s ago, "
                        f"iteration {_iter_n}/{_iter_max}). "
                        "The agent may have been waiting on an API response."
                    )
                _diag_lines.append(
                    "To increase the limit, set agent.gateway_timeout in config.yaml "
                    "(value in seconds, 0 = no limit) and restart the gateway.\n"
                    "Try again, or use /reset to start fresh."
                )

                response = {
                    "final_response": "\n".join(_diag_lines),
                    "messages": result_holder[0].get("messages", []) if result_holder[0] else [],
                    "api_calls": _iter_n,
                    "tools": tools_holder[0] or [],
                    "history_offset": 0,
                    "failed": True,
                }
'''
NEW_5 = r'''                # Construct a user-facing message with diagnostic context.
                # MAG_suppress_diagnostic_leaks: the full diagnostic (tool name,
                # iteration/max_iterations, config.yaml key) is internal-only —
                # showing it on a client channel violates the engineering-secrecy
                # barrier. Internal/staff/CLI surfaces keep it verbatim.
                if _gateway_platform_value(source.platform) in ("api_server", "local", "cli"):
                    _diag_lines = [
                        f"⏱️ Agent inactive for {_timeout_mins} min — no tool calls "
                        f"or API responses."
                    ]
                    if _cur_tool:
                        _diag_lines.append(
                            f"The agent appears stuck on tool `{_cur_tool}` "
                            f"({_secs_ago:.0f}s since last activity, "
                            f"iteration {_iter_n}/{_iter_max})."
                        )
                    else:
                        _diag_lines.append(
                            f"Last activity: {_last_desc} ({_secs_ago:.0f}s ago, "
                            f"iteration {_iter_n}/{_iter_max}). "
                            "The agent may have been waiting on an API response."
                        )
                    _diag_lines.append(
                        "To increase the limit, set agent.gateway_timeout in config.yaml "
                        "(value in seconds, 0 = no limit) and restart the gateway.\n"
                        "Try again, or use /reset to start fresh."
                    )
                    _timeout_final_text = "\n".join(_diag_lines)
                else:
                    _timeout_final_text = (
                        "Isso demorou mais do que o esperado e eu precisei parar por aqui. "
                        "Pode tentar de novo, por favor?"
                    )

                response = {
                    "final_response": _timeout_final_text,
                    "messages": result_holder[0].get("messages", []) if result_holder[0] else [],
                    "api_calls": _iter_n,
                    "tools": tools_holder[0] or [],
                    "history_offset": 0,
                    "failed": True,
                }
'''

EDITS = [
    ("STT-unavailable message", OLD_1, NEW_1),
    ("compression-aborted warning", OLD_2, NEW_2),
    ("aux-model-fallback notice", OLD_3, NEW_3),
    ("inactivity staged warning", OLD_4, NEW_4),
    ("inactivity final diagnostic", OLD_5, NEW_5),
]


def main() -> None:
    if not RUN_PY.exists():
        raise SystemExit(f"gateway run.py not found at {RUN_PY}")
    text = RUN_PY.read_text(encoding="utf-8")

    if MARKER in text:
        print("OK: diagnostic-leak suppression already patched (idempotent no-op)")
        return

    for label, old, new in EDITS:
        if text.count(old) != 1:
            raise SystemExit(
                f"patch_suppress_agent_diagnostics: anchor missing or not unique "
                f"for '{label}' (Hermes changed)."
            )
        text = text.replace(old, new, 1)

    RUN_PY.write_text(text, encoding="utf-8")
    print(f"OK: patched {len(EDITS)} diagnostic-leak site(s)")


if __name__ == "__main__":
    main()
