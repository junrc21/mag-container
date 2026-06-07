"""Build-time patch: make the free 'trafilatura' extract backend selectable.

web_tools._is_backend_available() is a hardcoded allow-list (exa/parallel/
firecrawl/tavily/searxng/brave/ddgs/xai) gated on API keys. Our bundled keyless
``trafilatura`` extract provider is registered and importable, but this function
doesn't know its name, so _get_capability_backend("extract") falls back to ddgs
(search-only) and web_extract fails. This adds a ``trafilatura`` branch (available
whenever the package is importable — no key) so config web.extract_backend:
"trafilatura" is honored.

Idempotent + fail-loud (mirrors the other bootstrap patches).
"""

import os
import pathlib

WEB_TOOLS = pathlib.Path(os.getenv("WEB_TOOLS_PY", "/opt/hermes/tools/web_tools.py"))

MARKER = 'backend == "trafilatura"'

ANCHOR = (
    'def _is_backend_available(backend: str) -> bool:\n'
    '    """Return True when the selected backend is currently usable."""\n'
)
INJECT = (
    'def _is_backend_available(backend: str) -> bool:\n'
    '    """Return True when the selected backend is currently usable."""\n'
    '    if backend == "trafilatura":\n'
    '        # Free, keyless page extractor (bundled plugin). Available whenever\n'
    '        # the trafilatura package is importable — no API key.\n'
    '        try:\n'
    '            import trafilatura  # noqa: F401\n'
    '            return True\n'
    '        except ImportError:\n'
    '            return False\n'
)


def main() -> None:
    if not WEB_TOOLS.exists():
        raise SystemExit(f"web_tools.py not found at {WEB_TOOLS}")
    text = WEB_TOOLS.read_text()

    if MARKER in text:
        print("OK: trafilatura extract backend already selectable (idempotent no-op)")
        return
    if ANCHOR not in text:
        raise SystemExit("patch_web_extract_free: _is_backend_available anchor missing (Hermes changed).")

    text = text.replace(ANCHOR, INJECT, 1)
    WEB_TOOLS.write_text(text)
    print("OK: patched _is_backend_available with the trafilatura branch")


if __name__ == "__main__":
    main()
