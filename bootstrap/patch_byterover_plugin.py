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

    plugin_path.write_text(text)
    print(f"OK: patched {plugin_path}")


if __name__ == "__main__":
    main()

