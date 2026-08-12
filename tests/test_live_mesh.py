"""End-to-end tests against the local mesh.

Skipped unless all three agents are up (``./infra/run_mesh.sh start``), so the
default test run stays hermetic and credential-free.

These run against `direct`-brain agents, so they assert *reachability and
shape* -- that every client stack round-trips a draft off every serving stack.
They deliberately assert nothing about quality: the agents are returning canned
text, and a test that ranked it would be testing the fixture.
"""

import httpx
import pytest

from clients import CLIENT_STACKS, load_client
from coordinator.errors import FailureKind
from coordinator.mesh import ResearchMesh
from coordinator.models import ResearchRequest
from coordinator.participants import Participant

ENDPOINTS = {
    "gcp": "http://127.0.0.1:10001",
    "aws": "http://127.0.0.1:10002",
    "azure": "http://127.0.0.1:10003",
}


def _up(endpoint: str) -> bool:
    try:
        return httpx.get(f"{endpoint}/health", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


def _degraded(endpoint: str) -> bool:
    """Whether this agent is deliberately returning bad output.

    Asked rather than assumed. ``./infra/demo.sh`` leaves a degraded agent
    behind if it is interrupted before its cleanup trap fires, and a suite
    that silently measured one would report the fault injection as a finding.
    """
    try:
        return bool(httpx.get(f"{endpoint}/health", timeout=2).json().get("degraded"))
    except (httpx.HTTPError, ValueError):
        return False


ANY_DEGRADED = any(_degraded(endpoint) for endpoint in ENDPOINTS.values())


pytestmark = pytest.mark.skipif(
    not all(_up(endpoint) for endpoint in ENDPOINTS.values()),
    reason="local mesh is not running; start it with ./infra/run_mesh.sh start",
)


def request(**kwargs) -> ResearchRequest:
    return ResearchRequest(
        topic="agent-to-agent protocols in multi-cloud systems",
        questions=["what does A2A replace?"],
        **kwargs,
    )


@pytest.mark.parametrize("stack", CLIENT_STACKS)
@pytest.mark.parametrize("cloud", list(ENDPOINTS))
async def test_every_client_reaches_every_cloud(stack, cloud):
    """The interop matrix as an assertion: all nine cells must round-trip."""
    try:
        client = load_client(stack, ENDPOINTS[cloud], source=cloud, cloud=cloud, timeout_s=60)
    except ImportError:
        pytest.skip(f"{stack} SDK is not installed")

    draft = await client.research(request())

    assert draft.body
    assert draft.title
    assert draft.word_count > 0


@pytest.mark.parametrize("cloud", list(ENDPOINTS))
async def test_every_agent_names_itself_in_the_serving_header(cloud):
    """The audit cannot attribute a draft the agent did not sign.

    Worth asserting against the live agents rather than the parser: the header
    is written by three different serving stacks, and ADK's is the one that had
    to be stamped by a wrapper agent rather than a responder.
    """
    client = load_client("a2a-sdk", ENDPOINTS[cloud], source=cloud, cloud=cloud, timeout_s=60)
    draft = await client.research(request())

    assert draft.cloud == cloud
    assert draft.brain == "direct", "run_mesh.sh starts the agents credential-free"


@pytest.mark.skipif(
    ANY_DEGRADED,
    reason="an agent is running with RESEARCH_DRAFT_DEGRADE; restart the mesh",
)
async def test_every_serving_stack_delivers_the_same_canned_draft_once():
    """Regression for the duplicated-reply defect, at the level it was found.

    In `direct` mode all three clouds return byte-identical canned text, so
    three different word counts can only mean a serving stack or a client
    carried it differently -- which is exactly what happened: ADK put the
    draft in the artifact *and* in history, and the GCP draft arrived at twice
    the length. Comparing the counts is the cheapest possible detector, and no
    unit test would have run all three stacks against each other to notice.
    """
    counts = {}
    for cloud, endpoint in ENDPOINTS.items():
        client = load_client("a2a-sdk", endpoint, source=cloud, cloud=cloud, timeout_s=60)
        counts[cloud] = (await client.research(request())).word_count

    assert len(set(counts.values())) == 1, f"same text, different lengths: {counts}"


async def test_three_clouds_return_three_drafts_and_a_verdict():
    participants = [
        Participant(
            name=cloud,
            source=load_client("a2a-sdk", endpoint, source=cloud, cloud=cloud, timeout_s=60),
            cloud=cloud,
        )
        for cloud, endpoint in ENDPOINTS.items()
    ]
    run = await ResearchMesh(participants, timeout_seconds=60).run(request())

    assert run.failures == {}
    assert run.complete
    assert len(run.drafts) == 3
    assert {draft.source for draft in run.drafts} == set(ENDPOINTS)
    assert run.verdict.winner in ENDPOINTS
    # Every agent is direct-brain and returns the same canned text, so the
    # winner is a tie-break rather than a preference. Asserting the warning is
    # what stops this test quietly becoming a quality claim.
    assert any("tie" in warning for warning in run.verdict.warnings)


async def test_mesh_survives_one_unreachable_cloud():
    participants = [
        Participant(
            name=cloud,
            source=load_client(
                "a2a-sdk", ENDPOINTS[cloud], source=cloud, cloud=cloud, timeout_s=60
            ),
            cloud=cloud,
        )
        for cloud in ("gcp", "aws")
    ]
    participants.append(
        Participant(
            name="offline",
            source=load_client(
                "a2a-sdk", "http://127.0.0.1:9", source="offline", cloud="offline", timeout_s=5
            ),
            cloud="offline",
        )
    )
    run = await ResearchMesh(participants, timeout_seconds=15).run(request())

    assert run.succeeded, "two healthy clouds should still produce a verdict"
    assert not run.complete
    assert len(run.drafts) == 2
    assert FailureKind.TRANSPORT.value in run.failures["offline"]
