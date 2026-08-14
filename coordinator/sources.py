"""The sources a draft cited, and a way to actually open them.

The rubric's ``evidence`` dimension counts citation *shapes* -- a URL, a `[1]`,
an "according to". It cannot tell a real source from an invented one, and on
2026-08-13 that stopped being a theoretical weakness: a draft scored 5.0 of 5
on evidence having made **zero** searches. Those five points were
citation-shaped text with nothing behind them.

This module is the cheapest available answer. Pull the URLs out of a draft,
fetch them, and report what came back. A fabricated citation is usually a dead
one, and a dead one is visible in a way a plausible sentence is not.

**The fetch is the dangerous part and is guarded accordingly.** This master is
deployed open to the internet and holds federated credentials for three clouds,
so an endpoint that fetches an arbitrary URL on request is a server-side
request forgery hole pointed directly at a credentialed host -- the cloud
metadata service on 169.254.169.254 being the obvious first stop. Two rules
make it safe, and both are enforced here rather than in the route:

1. **Only a URL that appears in a stored draft can be fetched.** The caller
   does not choose the target; the corpus does. This is an allowlist that
   writes itself and it is exactly the feature's scope -- "read the papers this
   review cited", never "fetch a thing I name".
2. **Private, loopback, link-local and reserved addresses are refused**, after
   resolution rather than before, because a hostname that resolves to
   169.254.169.254 is the attack and a textual check for "169.254" does not
   catch it.
"""

import ipaddress
import re
import socket
from urllib.parse import urlsplit

import httpx

from coordinator.judge import _CITATION_RE
from coordinator.models import ResearchRun

#: Trailing punctuation a model puts after a URL in prose. Stripped so
#: "see https://example.org/x." resolves rather than 404ing on the full stop.
_TRAILING = ".,);:]}'\"<>"

_URL_RE = re.compile(r"https?://[^\s<>\"'\)\]]+", re.IGNORECASE)

#: A non-URL citation gesture: "[1]", "et al.", "according to". Counted but
#: never fetched -- it names a source without giving one, which is its own
#: finding, because it scores on the evidence dimension and can be checked by
#: nobody.
#:
#: Imported from the judge rather than copied. This count sits next to the
#: rubric's evidence score on the page, and a second regex would drift from the
#: first -- showing a reader "2 unlinked markers" beside a score computed from
#: a different number of them, with nothing to say they disagreed.
_BARE_RE = _CITATION_RE

FETCH_TIMEOUT_S = 12.0
#: Enough to see what a page is; not enough to make this a download service.
MAX_BYTES = 200_000

_TAG_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
_ANY_TAG_RE = re.compile(r"<[^>]+>")
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.DOTALL | re.IGNORECASE)
_WS_RE = re.compile(r"\s+")


def extract_urls(text: str) -> list[str]:
    """Every distinct URL in a draft, in the order it appears."""
    seen: dict[str, None] = {}
    for raw in _URL_RE.findall(text or ""):
        url = raw.rstrip(_TRAILING)
        if url and url not in seen:
            seen[url] = None
    return list(seen)


def citations_for(run: ResearchRun, source: str) -> list[dict]:
    """What each version of one cloud's draft cited.

    Per version rather than per cloud, because the interesting question in a
    rewritten draft is whether the *new* sources are real -- a model told its
    evidence was weak will happily add citations, and adding them is not the
    same as finding them.
    """
    out = []
    for draft in run.lineage(source):
        urls = extract_urls(draft.body)
        out.append(
            {
                "source": source,
                "round": draft.round,
                "searches": draft.searches,
                "urls": urls,
                # Gestures without a link. Scored by the rubric, checkable by
                # nobody, and worth showing beside the count of real ones.
                "bare_markers": len(_BARE_RE.findall(draft.body)),
            }
        )
    return out


def known_urls(run: ResearchRun) -> set[str]:
    """Every URL any version of any draft in this run cited.

    The allowlist for fetching. It is derived from the corpus, so it cannot be
    widened by a caller.
    """
    urls: set[str] = set()
    for draft in [*run.versions, *run.drafts]:
        urls.update(extract_urls(draft.body))
    return urls


def _is_public_address(host: str) -> tuple[bool, str]:
    """Whether ``host`` resolves only to addresses that are safe to fetch.

    Resolved rather than pattern-matched. `http://metadata.google.internal/` and
    a hostname whose A record is 169.254.169.254 both look perfectly ordinary as
    text, and both reach the credential endpoint of the machine this runs on.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as exc:
        return False, f"{host} does not resolve ({exc.__class__.__name__})"

    for info in infos:
        address = info[4][0]
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError:
            return False, f"{host} resolved to something unreadable"
        if (
            parsed.is_private
            or parsed.is_loopback
            or parsed.is_link_local
            or parsed.is_reserved
            or parsed.is_multicast
            or parsed.is_unspecified
        ):
            return False, f"{host} resolves to {address}, which is not a public address"
    return True, ""


def check_url(url: str, allowed: set[str]) -> tuple[bool, str]:
    """Whether this URL may be fetched at all, and why not if not."""
    if url not in allowed:
        return False, "that URL is not cited by any draft in this run"
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return False, f"{parts.scheme or 'that'} is not a scheme this will fetch"
    if not parts.hostname:
        return False, "no host in that URL"
    return _is_public_address(parts.hostname)


def _readable(body: str) -> tuple[str, str]:
    """A page's title and a plain-text excerpt, without a parser dependency."""
    title_match = _TITLE_RE.search(body)
    title = _WS_RE.sub(" ", _ANY_TAG_RE.sub("", title_match.group(1))).strip() if title_match else ""
    stripped = _ANY_TAG_RE.sub(" ", _TAG_RE.sub(" ", body))
    return title[:300], _WS_RE.sub(" ", stripped).strip()[:4000]


async def fetch(url: str, allowed: set[str]) -> dict:
    """Fetch one cited URL and report what came back.

    Never raises. Every outcome is a *result* here -- a dead link, a refusal, a
    timeout -- because the whole point is to distinguish a citation that
    resolves from one that does not, and an exception would make those two look
    the same to the caller.
    """
    permitted, reason = check_url(url, allowed)
    if not permitted:
        return {"url": url, "ok": False, "status": None, "reason": reason}

    try:
        async with httpx.AsyncClient(
            timeout=FETCH_TIMEOUT_S,
            follow_redirects=True,
            headers={"User-Agent": "a2a-research/1.0 (citation check)"},
        ) as client:
            response = await client.get(url)
            body = response.text[:MAX_BYTES]
    except Exception as exc:  # noqa: BLE001 - a dead citation is a result, not a fault
        return {
            "url": url,
            "ok": False,
            "status": None,
            "reason": f"{type(exc).__name__}: {exc}",
        }

    # A redirect that left the allowlist is reported rather than followed
    # silently: "this citation now points somewhere else" is a finding.
    final = str(response.url)
    title, text = _readable(body)
    return {
        "url": url,
        "final_url": final if final != url else "",
        "ok": response.is_success,
        "status": response.status_code,
        "title": title,
        "excerpt": text,
        "bytes": len(body),
        "reason": "" if response.is_success else response.reason_phrase,
    }


__all__ = ["check_url", "citations_for", "extract_urls", "fetch", "known_urls"]
