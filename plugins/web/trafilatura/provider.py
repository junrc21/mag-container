"""Free, keyless web content extraction via trafilatura — plugin form.

DuckDuckGo (ddgs) gives keyless SEARCH but cannot EXTRACT page content (the
stock paid extract backends — firecrawl/tavily/exa/parallel — all need API
keys). This provider fills the gap with ``trafilatura`` (no API key, no browser):
it fetches each URL and returns clean main-text content. Pair with ddgs:

    web:
      search_backend: ddgs
      extract_backend: trafilatura

Subclasses :class:`agent.web_search_provider.WebSearchProvider`. ``trafilatura``
is an optional dependency; ``is_available()`` reflects whether it's importable.
"""

from __future__ import annotations

import logging
import urllib.request
from typing import Any, Dict, List, Optional

from agent.web_search_provider import WebSearchProvider

logger = logging.getLogger(__name__)

# A realistic browser User-Agent — many sites return 403/close the connection for
# the default python/trafilatura UA. Keyless; no browser engine, just the header.
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
}


# Search-engine result pages can't be scraped (redirect/consent walls) and aren't
# content anyway — guide the agent back to the web_search tool.
_SEARCH_ENGINE_HOSTS = (
    "google.", "bing.com", "duckduckgo.com", "search.brave.com", "yahoo.com", "yandex.",
)


def _is_search_engine_url(url: str) -> bool:
    try:
        from urllib.parse import urlparse
        u = urlparse(url)
        host = (u.netloc or "").lower()
        path = (u.path or "").lower()
        if "/search" in path or u.query:
            return any(h in host for h in _SEARCH_ENGINE_HOSTS)
    except Exception:
        pass
    return False


def _search_query_from_url(url: str) -> str:
    """Pull the ``q=`` query out of a search-engine URL (best-effort)."""
    try:
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(url).query)
        for key in ("q", "query", "p", "text"):
            if qs.get(key):
                return qs[key][0]
    except Exception:
        pass
    return ""


def _ddgs_results_as_content(query: str) -> Optional[str]:
    """Run the keyless ddgs search and format results as readable text — so a model
    that wrongly called web_extract on a search URL still gets real results."""
    try:
        from plugins.web.ddgs.provider import DDGSWebSearchProvider
        res = DDGSWebSearchProvider().search(query, limit=6)
        if not res.get("success"):
            return None
        items = (res.get("data") or {}).get("web") or []
        if not items:
            return None
        lines = []
        for it in items:
            title = it.get("title", "") or ""
            desc = it.get("description", "") or it.get("snippet", "") or ""
            link = it.get("url", "") or it.get("href", "") or ""
            lines.append(f"- {title}: {desc} ({link})")
        return "\n".join(lines)
    except Exception:
        return None


def _fetch_html(url: str, timeout: int = 20) -> Optional[str]:
    """Fetch a URL with a browser UA and return decoded HTML (keyless, no browser).

    Falls back to trafilatura.fetch_url if the direct fetch fails."""
    try:
        req = urllib.request.Request(url, headers=_BROWSER_HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            enc = resp.headers.get_content_charset() or "utf-8"
        return raw.decode(enc, errors="replace")
    except Exception:
        try:
            import trafilatura
            return trafilatura.fetch_url(url)
        except Exception:
            return None


class TrafilaturaWebExtractProvider(WebSearchProvider):
    """Keyless web page extractor backed by the ``trafilatura`` package."""

    @property
    def name(self) -> str:
        return "trafilatura"

    @property
    def display_name(self) -> str:
        return "Trafilatura (free, no key)"

    def is_available(self) -> bool:
        try:
            import trafilatura  # noqa: F401

            return True
        except ImportError:
            return False

    def supports_search(self) -> bool:
        return False

    def supports_extract(self) -> bool:
        return True

    def extract(self, urls: List[str], **kwargs: Any) -> List[Dict[str, Any]]:
        """Fetch + extract clean main text for each URL (keyless, no browser)."""
        import trafilatura

        try:
            max_chars = int(kwargs.get("max_chars") or 0)
        except Exception:
            max_chars = 0

        results: List[Dict[str, Any]] = []
        for url in urls or []:
            try:
                if _is_search_engine_url(url):
                    # The model wrongly aimed web_extract at a search page. Recover by
                    # running the keyless ddgs search for its query and returning the
                    # results as content — so the answer still works.
                    q = _search_query_from_url(url)
                    content = _ddgs_results_as_content(q) if q else None
                    if content:
                        results.append({
                            "url": url, "title": f"Resultados de busca: {q}",
                            "content": content, "raw_content": content,
                            "metadata": {"recovered_via": "ddgs_search"},
                        })
                    else:
                        results.append({
                            "url": url, "title": "", "content": "", "raw_content": "",
                            "error": "This is a search-engine URL — use the web_search tool to find content, then extract an actual result URL.",
                        })
                    continue
                downloaded = _fetch_html(url)
                if not downloaded:
                    results.append(
                        {"url": url, "title": "", "content": "", "raw_content": "", "error": "could not fetch URL"}
                    )
                    continue
                text = (
                    trafilatura.extract(
                        downloaded,
                        include_comments=False,
                        include_tables=True,
                        favor_recall=True,
                        url=url,
                    )
                    or ""
                )
                if max_chars and len(text) > max_chars:
                    text = text[:max_chars]
                title = ""
                meta: Dict[str, Any] = {}
                try:
                    md = trafilatura.extract_metadata(downloaded)
                    if md is not None:
                        title = getattr(md, "title", "") or ""
                        meta = {
                            "author": getattr(md, "author", "") or "",
                            "date": getattr(md, "date", "") or "",
                            "sitename": getattr(md, "sitename", "") or "",
                        }
                except Exception:
                    pass
                results.append(
                    {"url": url, "title": title, "content": text, "raw_content": text, "metadata": meta}
                )
            except Exception as exc:  # per-URL failure — never abort the batch
                logger.warning("trafilatura extract failed for %s: %s", url, exc)
                results.append(
                    {"url": url, "title": "", "content": "", "raw_content": "", "error": str(exc)}
                )
        return results
