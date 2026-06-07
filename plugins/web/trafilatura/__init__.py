"""Trafilatura web-extract plugin — bundled, auto-loaded. Keyless, no browser.

Provides a free ``extract_backend`` so the agent can read page content without a
paid provider. Search stays on ddgs (also keyless).
"""

from __future__ import annotations

from plugins.web.trafilatura.provider import TrafilaturaWebExtractProvider


def register(ctx) -> None:
    """Register the trafilatura extract provider with the plugin context."""
    ctx.register_web_search_provider(TrafilaturaWebExtractProvider())
