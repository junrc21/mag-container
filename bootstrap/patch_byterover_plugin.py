import pathlib
import re


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

    plugin_path.write_text(text)
    print(f"OK: patched {plugin_path}")


if __name__ == "__main__":
    main()

