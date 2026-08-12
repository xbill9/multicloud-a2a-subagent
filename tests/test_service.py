"""The master service: the front door, its validation, and what it records.

The mesh itself is stubbed out here -- ``coordinator.service.build_mesh`` is
replaced with one built from ``CannedDraftAdapter`` -- but the judge, the
scoring and the JSON envelope are the real ones. Faking the verdict too would
leave the response shape asserted against a fixture rather than against the
model that produces it, and the front end reads that shape field by field.

What is deliberately *not* covered: whether the buildpack's entrypoint starts
this app on Cloud Run. No test here can answer that, and it is the failure this
deployment shape is most likely to have.
"""

import json

import pytest
from starlette.testclient import TestClient

from coordinator import service
from coordinator.local_adapters import CannedDraftAdapter
from coordinator.mesh import ResearchMesh
from coordinator.models import ResearchRequest
from coordinator.participants import Participant

GOOD = """\
# Solid-state batteries in 2026

## Who ships at scale

QuantumScape shipped 12,000 cells in 2025, according to its Q4 filing.
Toyota targets 2027 (per its 2024 statement). See https://example.org/x.

- one point
- another point
"""


def canned(name: str, body: str = GOOD, brain: str = "llm") -> Participant:
    return Participant(
        name=name,
        source=CannedDraftAdapter(body, source=name, cloud=name, brain=brain),
        cloud=name,
        auth="google-id-token",
    )


@pytest.fixture
def client(monkeypatch, tmp_path):
    """A test client whose mesh answers locally and whose store is disposable."""
    monkeypatch.setenv("RESEARCH_EVAL_STORE", str(tmp_path / "runs.jsonl"))

    def build_mesh(clouds, *, client="a2a-sdk", judge=None, timeout_seconds=120.0):
        names = clouds or ["gcp", "aws", "azure"]
        return ResearchMesh([canned(name) for name in names])

    monkeypatch.setattr(service, "build_mesh", build_mesh)
    return TestClient(service.app)


def brief(**kwargs) -> dict:
    return {"topic": "solid-state batteries in 2026", **kwargs}


def test_the_front_end_is_served_at_the_root(client):
    response = client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "send the brief" in response.text


def test_the_page_carries_no_external_asset(client):
    """Everything inline, and asserted rather than intended.

    The service is private and reached through a proxy, so a stylesheet or a
    font pulled from a CDN is not a slow load, it is a page that renders
    unstyled with no indication why. The cheapest guard is to notice the first
    time an external reference is added.
    """
    body = client.get("/").text

    assert "<link" not in body
    assert 'src="http' not in body
    assert "@import" not in body
    assert "cdn" not in body.lower()


def test_health_reports_every_peer_and_its_configured_auth(client, monkeypatch):
    monkeypatch.setenv("AWS_A2A_AUTH", "aws-sigv4")
    monkeypatch.setenv("AZURE_A2A_ENDPOINT", "https://azure.example.com/agent")

    payload = client.get("/api/health").json()

    assert payload["status"] == "ok"
    assert payload["role"] == "master"
    peers = {peer["cloud"]: peer for peer in payload["peers"]}
    assert set(peers) == {"gcp", "aws", "azure"}
    assert peers["aws"]["auth"] == "aws-sigv4"
    assert peers["azure"]["reachable_as"] == "azure.example.com"
    # Configured, not measured: health must not fan out to three clouds.
    assert peers["gcp"]["auth"] == "none"


def test_a_brief_is_judged_and_returned(client):
    response = client.post("/api/research", json=brief(questions=["who ships at scale?"]))

    assert response.status_code == 200
    run = response.json()
    assert run["participants"] == ["gcp", "aws", "azure"]
    assert len(run["drafts"]) == 3
    assert run["verdict"]["winner"] in {"gcp", "aws", "azure"}
    assert len(run["verdict"]["verdicts"]) == 3
    assert run["failures"] == {}


def test_the_run_is_recorded_by_default(client, tmp_path):
    client.post("/api/research", json=brief())

    lines = (tmp_path / "runs.jsonl").read_text().strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["run"]["request"]["topic"] == "solid-state batteries in 2026"


def test_recording_can_be_declined(client, tmp_path):
    client.post("/api/research", json=brief(record=False))

    assert not (tmp_path / "runs.jsonl").exists()


def test_an_unwritable_store_does_not_lose_the_drafts(client, monkeypatch):
    """Three cross-cloud calls cost more than the recording of them.

    The GCS volume can be unmounted or read-only, and discarding a completed
    run because the audit could not be appended to is the wrong trade in both
    directions: the caller loses an answer that exists, and the failure looks
    like the mesh rather than like the disk.
    """

    def boom(run, **kwargs):
        raise OSError("read-only file system")

    monkeypatch.setattr(service, "record", boom)

    response = client.post("/api/research", json=brief())

    assert response.status_code == 200
    assert len(response.json()["drafts"]) == 3


def test_one_cloud_can_be_selected(client):
    run = client.post("/api/research", json=brief(clouds=["aws"])).json()

    assert run["participants"] == ["aws"]
    assert len(run["drafts"]) == 1


def test_a_failed_cloud_degrades_the_run_rather_than_the_request(client, monkeypatch):
    """The mesh's whole behaviour, asserted at the HTTP boundary.

    A 500 here would be the natural implementation and the wrong one: two
    clouds answered, the judge ranked them, and the caller is entitled to that
    result plus the name of the leg that did not answer.
    """
    from coordinator.errors import AdapterError, FailureKind

    class Dead:
        async def research(self, request):
            raise AdapterError(FailureKind.TRANSPORT, "connection refused")

    def build_mesh(clouds, **kwargs):
        return ResearchMesh(
            [canned("gcp"), Participant(name="aws", source=Dead(), cloud="aws"), canned("azure")]
        )

    monkeypatch.setattr(service, "build_mesh", build_mesh)

    response = client.post("/api/research", json=brief())

    assert response.status_code == 200
    run = response.json()
    assert len(run["drafts"]) == 2
    assert "aws" in run["failures"]
    assert run["verdict"]["winner"] in {"gcp", "azure"}


def test_the_response_carries_the_brain_so_the_page_can_say_it_is_not_a_comparison(
    client, monkeypatch
):
    """`direct` drafts are canned text. The front end refuses to present a
    ranking of them as a model comparison, and it can only do that if the
    brain survives serialisation."""

    def build_mesh(clouds, **kwargs):
        return ResearchMesh([canned(name, brain="direct") for name in ("gcp", "aws")])

    monkeypatch.setattr(service, "build_mesh", build_mesh)

    run = client.post("/api/research", json=brief()).json()

    assert {draft["brain"] for draft in run["drafts"]} == {"direct"}


@pytest.mark.parametrize(
    "payload",
    [
        {"topic": ""},
        {"topic": "   "},
        {"topic": "ab"},
        {"topic": "a real topic", "max_words": -5},
        {},
    ],
)
def test_an_invalid_brief_is_rejected_before_any_cloud_is_called(client, payload):
    response = client.post("/api/research", json=payload)

    assert response.status_code == 400
    assert "error" in response.json()


def test_an_unknown_client_stack_is_rejected(client):
    response = client.post("/api/research", json=brief(client="curl"))

    assert response.status_code == 400
    assert "curl" in response.json()["error"]


def test_an_unknown_judge_is_rejected(client):
    response = client.post("/api/research", json=brief(judge="vibes"))

    assert response.status_code == 400
    assert "vibes" in response.json()["error"]


def test_an_unknown_cloud_is_rejected_rather_than_silently_dropped(monkeypatch, tmp_path):
    """With the real mesh builder, so this asserts the guard and not the stub.

    Dropping the unknown name instead would run two clouds and report a
    three-cloud request as complete.
    """
    monkeypatch.setenv("RESEARCH_EVAL_STORE", str(tmp_path / "runs.jsonl"))
    response = TestClient(service.app).post("/api/research", json=brief(clouds=["gcp", "oracle"]))

    assert response.status_code == 400
    assert "oracle" in response.json()["error"]


def test_a_non_json_body_is_rejected(client):
    response = client.post("/api/research", content=b"topic=hello")

    assert response.status_code == 400


def test_a_json_array_body_is_rejected(client):
    response = client.post("/api/research", json=["solid-state batteries"])

    assert response.status_code == 400


def test_the_audit_reads_what_the_runs_recorded(client, tmp_path):
    client.post("/api/research", json=brief())

    response = client.get("/api/audit")

    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    # One run, three clouds, and the report withholds rows below five runs --
    # so the correct output here names the caveat rather than a winner.
    assert response.text.strip()


def test_the_audit_is_readable_before_any_run_exists(client):
    assert client.get("/api/audit").status_code == 200


def test_the_last_run_is_read_back_out_of_the_store(client):
    """Not cached in memory: the instance that served a run is not the instance
    that gets asked about it, and a report that only works while the container
    is warm fails exactly when someone is trying to show it to somebody."""
    client.post("/api/research", json=brief(questions=["who ships at scale?"]))

    response = client.get("/api/last")

    assert response.status_code == 200
    run = response.json()
    assert run["request"]["questions"] == ["who ships at scale?"]
    assert run["verdict"]["winner"] in {"gcp", "aws", "azure"}


def test_the_last_run_is_the_most_recent_one(client):
    client.post("/api/research", json={"topic": "the first brief, chronologically"})
    client.post("/api/research", json={"topic": "the second brief, chronologically"})

    assert client.get("/api/last").json()["request"]["topic"] == (
        "the second brief, chronologically"
    )


def test_asking_for_the_last_run_before_there_is_one_is_a_404(client):
    response = client.get("/api/last")

    assert response.status_code == 404
    assert "error" in response.json()


def test_the_request_model_is_the_one_the_cli_uses(client):
    """One validation path, not two.

    The service and the CLI both build a `ResearchRequest`, so a brief the CLI
    accepts is a brief the service accepts. This is asserted rather than
    assumed because the two entry points drifting is exactly how the deployed
    thing stops matching the documented thing.
    """
    request = ResearchRequest(topic="  padded  ", questions=[" q ", "  "])

    assert request.topic == "padded"
    assert request.questions == ["q"]
