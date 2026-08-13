"""The evidence layer: what each leg did, and what it must never record.

The interesting assertions here are the isolation one and the redaction one.
Everything else is bookkeeping.
"""

import asyncio

import httpx
import pytest

from coordinator import trace
from coordinator.errors import AdapterError, FailureKind
from coordinator.local_adapters import CannedDraftAdapter
from coordinator.mesh import ResearchMesh
from coordinator.models import ResearchRequest
from coordinator.participants import Participant

CARD = "/.well-known/agent-card.json"


def response(url: str, status: int = 200, method: str = "GET", body: str = "") -> httpx.Response:
    request = httpx.Request(method, url)
    return httpx.Response(status, request=request, text=body)


async def test_nothing_is_recorded_outside_a_collect_block():
    """The hooks are attached to every client the mesh builds, including the
    ones the hermetic tests build. Recording unconditionally would mean a
    global list that grows for the life of the process."""
    assert trace.current() is None

    await trace.on_request(httpx.Request("GET", "https://example.com/"))
    await trace.on_response(response("https://example.com/"))
    trace.record_credential("some boundary", response("https://sts.amazonaws.com/"))

    assert trace.current() is None


async def test_a_round_trip_is_recorded_with_its_phase_and_status():
    with trace.collect() as leg:
        request = httpx.Request("GET", f"https://agent.example.com{CARD}")
        await trace.on_request(request)
        await trace.on_response(httpx.Response(200, request=request))

    assert len(leg.steps) == 1
    step = leg.steps[0]
    assert step.phase == "discovery"
    assert step.host == "agent.example.com"
    assert step.path == CARD
    assert step.status == 200
    assert step.ok


async def test_the_card_fetch_is_told_apart_from_the_invocation():
    """A 403 on discovery and a 403 on the call are different bugs -- the first
    means the credential never reached a privileged card endpoint. Mislabelling
    them is what this phase field exists to prevent."""
    with trace.collect() as leg:
        for method, url in (("GET", f"https://a.example.com{CARD}"), ("POST", "https://a.example.com/")):
            request = httpx.Request(method, url)
            await trace.on_request(request)
            await trace.on_response(httpx.Response(200, request=request))

    assert [step.phase for step in leg.steps] == ["discovery", "invoke"]


async def test_a_failed_round_trip_is_recorded_as_not_ok():
    with trace.collect() as leg:
        request = httpx.Request("POST", "https://a.example.com/")
        await trace.on_request(request)
        await trace.on_response(httpx.Response(403, request=request))

    assert leg.steps[0].ok is False
    assert leg.steps[0].status == 403


def test_a_credential_boundary_is_recorded_with_the_providers_own_words():
    """On failure only. The predecessor series lost a day to an adapter that
    reported an HTTP status and discarded the STS body, which was naming the
    unmatched condition the whole time."""
    with trace.collect() as leg:
        trace.record_credential(
            "aws sts AssumeRoleWithWebIdentity",
            response("https://sts.amazonaws.com/", 403, "POST", "Not authorized to perform"),
        )

    step = leg.steps[0]
    assert step.phase == "credential"
    assert step.host == "sts.amazonaws.com"
    assert step.ok is False
    assert "Not authorized to perform" in step.detail


def test_a_successful_credential_records_no_body_at_all():
    """The metadata mint returns the bearer token as its response body. A trace
    that captures it is a trace that cannot be shown to anyone, which makes it
    useless for the thing it was built for."""
    token = "eyJhbGciOiJSUzI1NiJ9.SECRET_PAYLOAD.signature"
    with trace.collect() as leg:
        trace.record_credential(
            "gcp metadata mint (audience=https://x.run.app)",
            response("http://metadata.google.internal/computeMetadata/v1/instance/x", body=token),
        )

    step = leg.steps[0]
    assert step.ok
    assert step.detail == ""
    assert "SECRET_PAYLOAD" not in step.model_dump_json()


async def test_a_query_string_never_reaches_the_trace():
    """`?audience=...&format=full` is the mint's own parameters, and the same
    field on another provider is where a token would sit. Dropped wholesale
    rather than filtered, because an allowlist is a thing to get wrong later."""
    with trace.collect() as leg:
        request = httpx.Request("GET", "https://idp.example.com/token?assertion=SECRET&x=1")
        await trace.on_request(request)
        await trace.on_response(httpx.Response(200, request=request))

    assert "SECRET" not in leg.steps[0].model_dump_json()
    assert leg.steps[0].path == "/token"


async def test_concurrent_legs_do_not_share_a_trace():
    """The claim the whole design rests on.

    Three legs run as three `asyncio.gather` tasks, each with its own copy of
    the context, so a context variable isolates them with no locking. If this
    were a module-level list instead, every leg's trace would be every other
    leg's trace and the report would attribute AWS's STS call to Azure.
    """

    async def leg(name: str, count: int) -> list[str]:
        with trace.collect() as collected:
            for index in range(count):
                request = httpx.Request("GET", f"https://{name}.example.com/{index}")
                await trace.on_request(request)
                await asyncio.sleep(0)  # force interleaving
                await trace.on_response(httpx.Response(200, request=request))
            return [step.host for step in collected.steps]

    results = await asyncio.gather(leg("gcp", 3), leg("aws", 1), leg("azure", 2))

    assert results[0] == ["gcp.example.com"] * 3
    assert results[1] == ["aws.example.com"]
    assert results[2] == ["azure.example.com"] * 2


async def test_an_in_process_participant_records_no_trace():
    """A canned draft crossed no network, and a report that draws it a flow
    diagram is claiming an interop result that did not happen."""
    mesh = ResearchMesh(
        [
            Participant(
                name=name,
                source=CannedDraftAdapter("body " * 80, source=name, cloud=name, brain="direct"),
                cloud=name,
            )
            for name in ("gcp", "aws")
        ]
    )

    run = await mesh.run(ResearchRequest(topic="solid-state batteries in 2026"))

    assert run.traces == {}


async def test_a_leg_that_failed_keeps_the_trace_that_shows_where():
    """The failure string says "403"; the trace says whether it was the card
    fetch or the call, which is the half that tells you what to fix."""

    class ForbiddenOnDiscovery:
        async def research(self, request):
            leg = trace.current()
            http = httpx.Request("GET", f"https://aws.example.com{CARD}")
            await trace.on_request(http)
            await trace.on_response(httpx.Response(403, request=http))
            assert leg is not None
            raise AdapterError(FailureKind.AUTHENTICATION, "A2A endpoint returned 403")

    mesh = ResearchMesh([Participant(name="aws", source=ForbiddenOnDiscovery(), cloud="aws")])

    run = await mesh.run(ResearchRequest(topic="solid-state batteries in 2026"))

    assert "aws" in run.failures
    assert run.drafts == []
    assert [step.phase for step in run.traces["aws"]] == ["discovery"]
    assert run.traces["aws"][0].status == 403


async def test_the_trace_survives_serialisation_into_the_store():
    """It is recorded, so it has to round-trip; a report reading the store gets
    the same evidence the live response showed."""

    class Traced:
        async def research(self, request):
            http = httpx.Request("POST", "https://azure.example.com/")
            await trace.on_request(http)
            await trace.on_response(httpx.Response(200, request=http))
            raise AdapterError(FailureKind.PROTOCOL, "deliberate")

    mesh = ResearchMesh([Participant(name="azure", source=Traced(), cloud="azure")])
    run = await mesh.run(ResearchRequest(topic="solid-state batteries in 2026"))

    from coordinator.models import ResearchRun

    revived = ResearchRun.model_validate_json(run.model_dump_json())

    assert revived.traces["azure"][0].host == "azure.example.com"


@pytest.mark.parametrize("status,expected", [(200, True), (204, True), (403, False), (500, False)])
async def test_ok_tracks_the_status_class(status, expected):
    with trace.collect() as leg:
        request = httpx.Request("GET", "https://a.example.com/")
        await trace.on_request(request)
        await trace.on_response(httpx.Response(status, request=request))

    assert leg.steps[0].ok is expected


# --------------------------------------------------------------------------
# The provider's own request id
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "header",
    ["x-amzn-RequestId", "x-ms-request-id", "x-goog-request-id", "x-request-id"],
)
async def test_the_providers_request_id_is_captured_whatever_it_calls_it(header):
    """The only column in a trace an outside reader can verify: every other
    figure is this process describing its own behaviour, while this one either
    finds the same call in the provider's logs or does not."""
    with trace.collect() as leg:
        request = httpx.Request("POST", "https://agent.example.com/")
        await trace.on_request(request)
        await trace.on_response(
            httpx.Response(200, request=request, headers={header: "req-0001"})
        )

    assert leg.steps[0].request_id == "req-0001"


async def test_a_provider_that_sends_no_request_id_gets_an_empty_one():
    """Not a synthesised id. A fabricated identifier that finds nothing in
    CloudWatch is worse than an absent one, because it looks checkable."""
    with trace.collect() as leg:
        request = httpx.Request("POST", "https://agent.example.com/")
        await trace.on_request(request)
        await trace.on_response(httpx.Response(200, request=request))

    assert leg.steps[0].request_id == ""


def test_a_credential_boundary_captures_the_request_id_too():
    """STS and Entra both return one, and an auth failure is the case where
    quoting the provider's own id to the provider matters most."""
    request = httpx.Request("POST", "https://sts.amazonaws.com/")
    reply = httpx.Response(
        403, request=request, text="AccessDenied", headers={"x-amzn-RequestId": "sts-77"}
    )
    with trace.collect() as leg:
        trace.record_credential("gcp -> aws sts", reply)

    assert leg.steps[0].request_id == "sts-77"


# --------------------------------------------------------------------------
# Two identical requests in flight at once
# --------------------------------------------------------------------------


async def test_a_retried_request_does_not_steal_the_first_ones_start_time():
    """A retry, a redirect, or a stack that polls the same URL twice puts two
    identical requests in flight. With one timing slot per URL the second
    overwrote the first, so the first reported an impossibly short call and the
    second found nothing at all -- landing at offset 0, which the timeline
    draws as a call that happened before the run began."""
    url = "https://agent.example.com/"
    with trace.collect() as leg:
        first = httpx.Request("POST", url)
        second = httpx.Request("POST", url)
        await trace.on_request(first)
        await trace.on_request(second)
        await trace.on_response(httpx.Response(200, request=first))
        await trace.on_response(httpx.Response(200, request=second))

    assert len(leg.steps) == 2
    # Both round trips know when they started. Before the fix the second had
    # `started_at is None`, which reads as an untimed call.
    assert all(step.started_at is not None for step in leg.steps)
    assert all(step.elapsed_ms >= 0 for step in leg.steps)
