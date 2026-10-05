import pathlib
import re
import textwrap


def main() -> None:
    plugin_path = pathlib.Path("/opt/hermes/plugins/memory/byterover/__init__.py")
    if not plugin_path.exists():
        raise SystemExit(f"ByteRover plugin not found at {plugin_path}")

    text = plugin_path.read_text()

    # Ensure `import os` exists.
    if not re.search(r"^\s*import\s+os\s*$", text, flags=re.M):
        m = re.search(r"^(import\s+[^\n]+\n)+", text, flags=re.M)
        if not m:
            raise SystemExit("Could not locate imports block to insert import os")
        text = text[: m.end()] + "import os\n" + text[m.end() :]

    # Make constants env-driven.
    text = re.sub(
        r"^_QUERY_TIMEOUT\s*=.*$",
        '_QUERY_TIMEOUT = int(os.getenv("HERMES_BYTEROVER_QUERY_TIMEOUT_SECONDS", "10"))  # brv query',
        text,
        flags=re.M,
        count=1,
    )
    text = re.sub(
        r"^_CURATE_TIMEOUT\s*=.*$",
        '_CURATE_TIMEOUT = int(os.getenv("HERMES_BYTEROVER_CURATE_TIMEOUT_SECONDS", "120"))  # brv curate',
        text,
        flags=re.M,
        count=1,
    )

    # Make status timeout env-driven (hardcoded 15s is too low on busy queues).
    text = text.replace(
        'result = _run_brv(["status"], timeout=15, cwd=self._cwd)',
        'result = _run_brv(["status"], timeout=int(os.getenv("HERMES_BYTEROVER_STATUS_TIMEOUT_SECONDS", "60")), cwd=self._cwd)',
        1,
    )

    # MAG: multimodal turns (image/audio) arrive with `content` as a LIST of parts,
    # not a str. Both `prefetch` (recall) and `sync_turn` (curate) call `.strip()` /
    # slice the content assuming str, so an image turn raised
    #   "'list' object has no attribute 'strip'"
    # and that turn's memory was silently dropped (logged as a WARNING). Coerce the
    # raw content to plain text first. Idempotent (guarded on the helper name).
    if "_mag_brv_text" not in text:
        helper = (
            "\n\ndef _mag_brv_text(content):\n"
            '    """MAG: flatten multimodal content (list of parts) to plain text for brv."""\n'
            "    if isinstance(content, str):\n"
            "        return content\n"
            "    if isinstance(content, list):\n"
            "        out = []\n"
            "        for part in content:\n"
            "            if isinstance(part, str):\n"
            "                out.append(part)\n"
            "            elif isinstance(part, dict):\n"
            '                txt = part.get("text") or part.get("content") or ""\n'
            "                if isinstance(txt, str):\n"
            "                    out.append(txt)\n"
            '        return " ".join(out)\n'
            '    return "" if content is None else str(content)\n'
        )
        m = re.search(r"^(import\s+[^\n]+\n|from\s+[^\n]+\n)+", text, flags=re.M)
        if not m:
            raise SystemExit("Could not locate imports block to insert _mag_brv_text")
        text = text[: m.end()] + helper + text[m.end() :]

        # Coerce in sync_turn (curate path).
        sync_anchor = (
            "        # Only curate substantive turns\n"
            "        if len(user_content.strip()) < _MIN_QUERY_LEN:\n"
        )
        if sync_anchor not in text:
            raise SystemExit("Could not find sync_turn anchor for multimodal coercion")
        text = text.replace(
            sync_anchor,
            "        # Only curate substantive turns\n"
            "        user_content = _mag_brv_text(user_content)\n"
            "        assistant_content = _mag_brv_text(assistant_content)\n"
            "        if len(user_content.strip()) < _MIN_QUERY_LEN:\n",
            1,
        )

        # Coerce in prefetch (recall path).
        prefetch_anchor = (
            "        the result is available as context before the model is called.\n"
            '        """\n'
            "        if not query or len(query.strip()) < _MIN_QUERY_LEN:\n"
        )
        if prefetch_anchor not in text:
            raise SystemExit("Could not find prefetch anchor for multimodal coercion")
        text = text.replace(
            prefetch_anchor,
            "        the result is available as context before the model is called.\n"
            '        """\n'
            "        query = _mag_brv_text(query)\n"
            "        if not query or len(query.strip()) < _MIN_QUERY_LEN:\n",
            1,
        )

    # MAG: enforce per-agent memory markers for direct brv_query/brv_curate calls.
    # The control plane writes /opt/data/policy/agent-memory.json and the gateway
    # exposes HERMES_SESSION_PLATFORM during a channel turn. This keeps model-called
    # memory operations in the same namespace as the control-plane hook curation.
    if "_mag_agent_memory_prefix" not in text:
        helper = textwrap.dedent(
            r'''

            _MAG_AGENT_MEMORY_POLICY_CACHE = None


            def _mag_agent_memory_policy():
                global _MAG_AGENT_MEMORY_POLICY_CACHE
                if _MAG_AGENT_MEMORY_POLICY_CACHE is not None:
                    return _MAG_AGENT_MEMORY_POLICY_CACHE
                path = os.getenv("MAG_AGENT_MEMORY_POLICY_PATH") or os.path.expanduser("~/policy/agent-memory.json")
                try:
                    import json
                    with open(path, "r") as f:
                        _MAG_AGENT_MEMORY_POLICY_CACHE = json.load(f)
                except Exception:
                    _MAG_AGENT_MEMORY_POLICY_CACHE = {}
                return _MAG_AGENT_MEMORY_POLICY_CACHE


            def _mag_active_agent():
                policy = _mag_agent_memory_policy()
                agents = policy.get("agents") if isinstance(policy, dict) else []
                channels = policy.get("channels") if isinstance(policy, dict) else []
                platform = (os.getenv("HERMES_SESSION_PLATFORM") or "").lower()
                try:
                    from gateway.session_context import get_session_env as _mag_get_session_env
                except Exception:
                    _mag_get_session_env = None
                channel_id = ((_mag_get_session_env("MAG_CHANNEL_ID", "") if _mag_get_session_env else os.getenv("MAG_CHANNEL_ID", "")) or "").strip()
                default_id = policy.get("defaultAgentId") if isinstance(policy, dict) else None
                agent_id = default_id
                if platform and isinstance(channels, list):
                    if channel_id:
                        for channel in channels:
                            if isinstance(channel, dict) and str(channel.get("channelId") or "") == channel_id:
                                agent_id = channel.get("agentProfileId") or default_id
                                break
                    if agent_id == default_id:
                        matches = [channel for channel in channels if isinstance(channel, dict) and str(channel.get("provider") or "").lower() == platform]
                        if len(matches) == 1:
                            agent_id = matches[0].get("agentProfileId") or default_id
                if isinstance(agents, list):
                    for agent in agents:
                        if isinstance(agent, dict) and agent.get("id") == agent_id:
                            return agent
                return None


            def _mag_agent_memory_prefix(kind="query"):
                agent = _mag_active_agent()
                if not isinstance(agent, dict):
                    return "[mag-scope:shared]"
                slug = str(agent.get("slug") or "").strip()
                scopes = agent.get("memoryScopes") if isinstance(agent.get("memoryScopes"), list) else []
                visibility = str(agent.get("visibility") or "public").lower()
                if kind == "query":
                    allowed = [s for s in scopes if s in ("shared", "public", "internal")]
                    if visibility != "internal":
                        allowed = [s for s in allowed if s != "internal"]
                    scope_part = " ".join(f"[mag-scope:{s}]" for s in (allowed or ["shared"]))
                else:
                    scope = "internal" if visibility == "internal" and "internal" in scopes else ("public" if "public" in scopes else "shared")
                    scope_part = f"[mag-scope:{scope}]"
                return f"[mag-agent:{slug}] {scope_part}" if slug else scope_part
            '''
        )
        m = re.search(r"^(import\s+[^\n]+\n|from\s+[^\n]+\n)+", text, flags=re.M)
        if not m:
            raise SystemExit("Could not locate imports block to insert agent memory helpers")
        text = text[: m.end()] + helper + text[m.end() :]

        curate_anchor = (
            "        user_content = _mag_brv_text(user_content)\n"
            "        assistant_content = _mag_brv_text(assistant_content)\n"
        )
        if curate_anchor not in text:
            raise SystemExit("Could not find sync_turn anchor for agent memory prefix")
        text = text.replace(
            curate_anchor,
            curate_anchor + '        user_content = f"{_mag_agent_memory_prefix(\'curate\')} {user_content}"\n',
            1,
        )

        query_anchor = "        query = _mag_brv_text(query)\n"
        if query_anchor not in text:
            raise SystemExit("Could not find prefetch anchor for agent memory prefix")
        text = text.replace(
            query_anchor,
            query_anchor + '        query = f"{_mag_agent_memory_prefix(\'query\')} {query}"\n',
            1,
        )

    plugin_path.write_text(text)
    print(f"OK: patched {plugin_path}")


if __name__ == "__main__":
    main()
