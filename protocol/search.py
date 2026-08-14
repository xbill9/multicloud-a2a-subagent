"""One web search, given identically to all three researchers.

**Why one implementation and not each vendor's own.** Only Google ships a
ready search tool: ADK exports ``google_search`` as native hosted grounding.
Microsoft's Agent Framework exports ``SupportsWebSearchTool``, which is a
protocol a client may declare and not a tool you can hand an agent. Strands
bundles none at all. So "give each cloud its native search" would mean Gemini
grounded against Google's index, a Foundry model against Bing through a
resource connection somebody has to create, and Bedrock against nothing --
three different retrieval products, and an audit that reports the difference
between them as a difference between models.

The variable under test is the model. So the tool is written once, here, and
every cloud gets the same function against the same backend returning the same
text. What still differs is the part worth measuring: **how each framework
binds and drives a tool.** ADK wraps a plain callable and runs its own
tool-calling loop; Strands takes an ``@tool``-decorated function; Agent
Framework takes an ``ai_function``. Three genuinely separate tool-call
implementations exercised by one tool is more interop surface than this repo
had before search, not less.

**No key by default.** The mesh's central property is that no long-lived
secret is needed anywhere, so the default backend is keyless. A keyed provider
is available and is the right choice for a measurement run -- see
``SEARCH_PROVIDER``. A search key is not a federation credential and does not
weaken the keyless claim about the *A2A legs*, but it is a stored secret and
the README should not pretend otherwise.

**This module makes an outbound HTTP call from a researcher.** That does not
breach the role boundary in ``tests/test_roles.py``: a researcher must not call
another *mesh member*, mint a cross-cloud credential, or judge. Calling a
search endpoint is what having a tool means.
"""

import os
import re
from html import unescape

import httpx

from protocol.telemetry import span

#: Which backend ``web_search`` hits, from ``RESEARCH_SEARCH_PROVIDER``.
#:
#: ``duckduckgo``  keyless, the default. Costs nothing and needs no setup,
#:                 and is the least reliable: it is an HTML endpoint, not an
#:                 API, and it rate-limits or blocks datacenter egress often
#:                 enough that a deployed agent can get an empty result where a
#:                 laptop got ten. An empty result is reported as such rather
#:                 than raised -- see ``web_search``.
#: ``tavily``      needs ``TAVILY_API_KEY``. An answer-oriented search API.
#: ``brave``       needs ``BRAVE_API_KEY``.
#: ``none``        the tool is not attached at all. This is the pre-2026-08-13
#:                 behaviour and stays reachable, because a run with no tools
#:                 on any cloud is the control for a run with tools on all
#:                 three.
SEARCH_PROVIDERS = ("duckduckgo", "tavily", "brave", "none")

DEFAULT_PROVIDER = "duckduckgo"
DEFAULT_MAX_RESULTS = 5
#: Kept short on purpose. A researcher that spends its budget waiting on a
#: search backend returns nothing at all, and a draft written without the
#: search is worth more than a timeout: the failure is reported in the text the
#: model sees, so the model can say it could not check something.
SEARCH_TIMEOUT_S = float(os.getenv("RESEARCH_SEARCH_TIMEOUT_S", "12"))

_DDG_ENDPOINT = "https://html.duckduckgo.com/html/"
_TAVILY_ENDPOINT = "https://api.tavily.com/search"
_BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"

_TAG_RE = re.compile(r"<[^>]+>")
_DDG_RESULT_RE = re.compile(
    r'<a[^>]+class="result__a"[^>]*href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>'
    r'.*?class="result__snippet"[^>]*>(?P<snippet>.*?)</a>',
    re.DOTALL,
)


def search_provider() -> str:
    provider = os.getenv("RESEARCH_SEARCH_PROVIDER", DEFAULT_PROVIDER).strip().lower()
    return provider if provider in SEARCH_PROVIDERS else DEFAULT_PROVIDER


def search_enabled() -> bool:
    return search_provider() != "none"


def _strip(markup: str) -> str:
    return unescape(_TAG_RE.sub("", markup)).strip()


def _render(results: list[dict]) -> str:
    """Results as text the model reads, with the URL on every entry.

    The URL is the point. A researcher told to cite its sources and handed
    snippets without them will invent citations that look exactly like real
    ones -- and the rubric's ``evidence`` dimension counts citation *shapes*,
    so a fabricated source scores identically to a real one. Carrying the URL
    into the model's context is the cheapest thing that makes a real citation
    easier to produce than an invented one.
    """
    if not results:
        return (
            "NO RESULTS. The search returned nothing. Do not invent sources or "
            "figures to fill the gap; say plainly in the draft that you could "
            "not verify this point."
        )
    lines = []
    for index, result in enumerate(results, start=1):
        lines.append(f"[{index}] {result['title']}\n    {result['url']}\n    {result['snippet']}")
    return "\n".join(lines)


async def _duckduckgo(query: str, max_results: int) -> list[dict]:
    async with httpx.AsyncClient(timeout=SEARCH_TIMEOUT_S, follow_redirects=True) as client:
        response = await client.post(
            _DDG_ENDPOINT,
            data={"q": query},
            # Without a browser-ish UA the endpoint returns a challenge page,
            # which parses to zero results and looks identical to "nothing was
            # found" -- the failure this whole module is careful about.
            headers={"User-Agent": "Mozilla/5.0 (compatible; a2a-research/1.0)"},
        )
        response.raise_for_status()

    results = []
    for match in _DDG_RESULT_RE.finditer(response.text):
        results.append(
            {
                "title": _strip(match.group("title")),
                "url": unescape(match.group("url")),
                "snippet": _strip(match.group("snippet")),
            }
        )
        if len(results) >= max_results:
            break
    return results


async def _tavily(query: str, max_results: int) -> list[dict]:
    key = os.getenv("TAVILY_API_KEY", "").strip()
    if not key:
        raise RuntimeError("RESEARCH_SEARCH_PROVIDER=tavily but TAVILY_API_KEY is unset")

    async with httpx.AsyncClient(timeout=SEARCH_TIMEOUT_S) as client:
        response = await client.post(
            _TAVILY_ENDPOINT,
            json={"api_key": key, "query": query, "max_results": max_results},
        )
        response.raise_for_status()
        payload = response.json()

    return [
        {
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "snippet": item.get("content", ""),
        }
        for item in payload.get("results", [])[:max_results]
    ]


async def _brave(query: str, max_results: int) -> list[dict]:
    key = os.getenv("BRAVE_API_KEY", "").strip()
    if not key:
        raise RuntimeError("RESEARCH_SEARCH_PROVIDER=brave but BRAVE_API_KEY is unset")

    async with httpx.AsyncClient(timeout=SEARCH_TIMEOUT_S) as client:
        response = await client.get(
            _BRAVE_ENDPOINT,
            params={"q": query, "count": max_results},
            headers={"X-Subscription-Token": key, "Accept": "application/json"},
        )
        response.raise_for_status()
        payload = response.json()

    return [
        {
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "snippet": _strip(item.get("description", "")),
        }
        for item in (payload.get("web", {}).get("results") or [])[:max_results]
    ]


_BACKENDS = {"duckduckgo": _duckduckgo, "tavily": _tavily, "brave": _brave}

#: Counts searches per process, reported on the agent's /health and in the
#: serving header. The coordinator cannot see a researcher's outbound calls --
#: they happen on another cloud, outside any trace this process opens -- so
#: this is the only evidence that a draft was researched rather than recalled.
_searches = 0


def search_count() -> int:
    return _searches


async def web_search(query: str, max_results: int = DEFAULT_MAX_RESULTS) -> str:
    """Search the web and return numbered results with their URLs.

    Use this to check any fact, figure or date before stating it, and to find
    sources you can cite. Prefer several narrow queries over one broad one.

    Args:
        query: What to search for.
        max_results: How many results to return, at most.

    Returns:
        Numbered results, each with a title, a URL and a snippet.
    """
    global _searches

    backend = _BACKENDS.get(search_provider())
    if backend is None:
        return "Search is disabled for this run."

    _searches += 1
    # One span per search. This is the half of the picture the coordinator can
    # never see: a researcher's retrieval happens on another cloud, outside any
    # trace this mesh opens, and until now the only evidence it happened at all
    # was a count in the draft header.
    try:
        with span(
            "research.search",
            **{
                "research.search_provider": search_provider(),
                "research.search_query": query[:200],
            },
        ) as current:
            results = await backend(query, max(1, min(max_results, 10)))
            if current is not None:
                try:
                    current.set_attribute("research.search_results", len(results))
                except Exception:  # noqa: BLE001,S110
                    pass
    except Exception as exc:  # noqa: BLE001 - a failed search must not fail the draft
        # Returned to the model rather than raised. A researcher whose search
        # failed can still write, and should say what it could not check; a
        # researcher whose agent loop crashed returns nothing at all, and the
        # coordinator files that as a provider failure on a leg that was
        # working fine.
        return (
            f"SEARCH FAILED ({type(exc).__name__}: {exc}). Write the brief from "
            f"what you already know, and say plainly which points you could not "
            f"verify. Do not invent sources."
        )
    return _render(results)


def search_summary() -> dict:
    """What this agent's search is configured to do, for /health and the card."""
    return {
        "provider": search_provider(),
        "enabled": search_enabled(),
        "searches": _searches,
    }


__all__ = [
    "DEFAULT_MAX_RESULTS",
    "SEARCH_PROVIDERS",
    "search_count",
    "search_enabled",
    "search_provider",
    "search_summary",
    "web_search",
]
