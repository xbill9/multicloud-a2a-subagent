"""Citation extraction, and the guard on fetching what a draft cited.

The evidence dimension counts citation *shapes*, and on 2026-08-13 a deployed
draft scored 5.0 of 5 on it having made zero searches. Those five points were
citation-shaped text with nothing behind them. Fetching what a draft cited is
the cheapest way to tell the two apart -- and it is also, on a master that is
open to the internet and holds three clouds' credentials, the most dangerous
endpoint in the project if it is built carelessly.
"""

from datetime import UTC, datetime

import pytest

from coordinator import sources
from coordinator.models import Draft, ResearchRequest, ResearchRun


def draft(body: str, *, source: str = "aws", round_: int = 1) -> Draft:
    return Draft(
        source=source,
        cloud=source,
        title="t",
        body=body,
        observed_at=datetime.now(UTC),
        latency_ms=1.0,
        round=round_,
    )


def run_with(*drafts: Draft) -> ResearchRun:
    return ResearchRun(
        request=ResearchRequest(topic="solid-state batteries"),
        participants=sorted({d.source for d in drafts}),
        drafts=list(drafts),
        versions=list(drafts),
        elapsed_ms=1.0,
    )


def test_a_url_is_found_without_its_trailing_punctuation():
    """"see https://example.org/x." must not resolve to a URL ending in a full
    stop, which 404s and reads as a dead citation."""
    urls = sources.extract_urls("See https://example.org/paper. Also (https://b.org/y);")

    assert urls == ["https://example.org/paper", "https://b.org/y"]


def test_the_same_url_twice_is_one_source():
    assert sources.extract_urls("https://a.org/x and again https://a.org/x") == [
        "https://a.org/x"
    ]


def test_citations_are_reported_per_version_not_per_cloud():
    """A model told its evidence is weak will happily add citations. Adding
    them is not the same as finding them, so the rewrite's sources are the ones
    worth checking."""
    run = run_with(
        draft("no sources here", round_=1),
        draft("now with https://example.org/found", round_=2),
    )

    rows = sources.citations_for(run, "aws")

    assert [row["round"] for row in rows] == [1, 2]
    assert rows[0]["urls"] == []
    assert rows[1]["urls"] == ["https://example.org/found"]


def test_a_gesture_without_a_link_is_counted_separately():
    """"[1]" and "according to" name a source without giving one. They score on
    the evidence dimension and can be checked by nobody."""
    rows = sources.citations_for(
        run_with(draft("According to the filing [1], and per the report [2].")), "aws"
    )

    assert rows[0]["urls"] == []
    assert rows[0]["bare_markers"] >= 2


def test_the_unlinked_marker_count_is_the_rubrics_own():
    """Not a second regex.

    This count is shown beside the rubric's evidence score. A copy would drift,
    and a reader would see "2 unlinked markers" next to a score computed from a
    different number of them, with nothing anywhere to say they disagreed.
    """
    from coordinator.judge import _CITATION_RE

    assert sources._BARE_RE is _CITATION_RE


# --------------------------------------------------------------------------
# The guard
# --------------------------------------------------------------------------


def test_a_url_no_draft_cited_is_refused():
    """The caller does not choose the target; the corpus does. This is the
    allowlist that makes the endpoint safe, and it writes itself."""
    run = run_with(draft("https://example.org/cited"))

    ok, reason = sources.check_url("https://evil.example/", sources.known_urls(run))

    assert ok is False
    assert "not cited" in reason


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/computeMetadata/v1/",
        "http://127.0.0.1:8080/api/health",
        "http://localhost/",
        "http://10.0.0.5/",
        "http://192.168.1.1/",
    ],
)
def test_a_private_or_link_local_target_is_refused_even_when_cited(url):
    """The attack this guard exists for.

    This master is public and holds federated credentials for three clouds, so
    a fetch of 169.254.169.254 is a request for its own metadata service. A
    draft citing such a URL must not be enough to make it happen -- the
    allowlist alone is not the whole guard.
    """
    run = run_with(draft(f"see {url} for details"))

    ok, reason = sources.check_url(url, sources.known_urls(run))

    assert ok is False, f"{url} was allowed"
    assert "public address" in reason or "does not resolve" in reason


def test_a_hostname_that_resolves_to_link_local_is_refused(monkeypatch):
    """Resolved, not pattern-matched.

    `metadata.google.internal` looks entirely ordinary as text and reaches the
    credential endpoint of the machine this runs on. A textual check for
    "169.254" does not catch it; resolving the name does.
    """
    import socket

    def resolves_to_metadata(host, *args, **kwargs):
        return [(socket.AF_INET, None, None, "", ("169.254.169.254", 0))]

    monkeypatch.setattr(sources.socket, "getaddrinfo", resolves_to_metadata)
    run = run_with(draft("see http://harmless.example/x"))

    ok, reason = sources.check_url("http://harmless.example/x", sources.known_urls(run))

    assert ok is False
    assert "169.254.169.254" in reason


@pytest.mark.parametrize("url", ["file:///etc/passwd", "gopher://x/", "ftp://x/"])
def test_only_http_is_fetched(url):
    ok, reason = sources.check_url(url, {url})

    assert ok is False
    assert "scheme" in reason


async def test_a_dead_citation_is_a_result_and_not_an_exception():
    """"This citation does not resolve" is the finding. An exception would make
    a dead link and a broken checker look the same to the caller."""
    url = "https://nx.invalid.example/does-not-exist"

    result = await sources.fetch(url, {url})

    assert result["ok"] is False
    assert result["status"] is None
    assert result["reason"]
