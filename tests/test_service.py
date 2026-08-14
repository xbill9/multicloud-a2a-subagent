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


def test_the_timeline_is_plain_text_one_curl_away(client):
    """The simplest proof the mesh is real: no browser, no JSON parsing, and
    it survives a paste into an issue."""
    client.post("/api/research", json=brief())

    response = client.get("/api/timeline")

    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    assert "solid-state batteries in 2026" in response.text


def test_the_timeline_can_walk_back_through_the_store(client):
    client.post("/api/research", json={"topic": "the older brief, chronologically"})
    client.post("/api/research", json={"topic": "the newer brief, chronologically"})

    assert "newer" in client.get("/api/timeline").text
    assert "older" in client.get("/api/timeline?n=2").text


def test_walking_back_past_the_beginning_is_a_404_not_a_wrong_answer(client):
    client.post("/api/research", json=brief())

    response = client.get("/api/timeline?n=9")

    assert response.status_code == 404
    assert "only 1 run" in response.text


def test_a_nonsense_timeline_index_is_rejected(client):
    assert client.get("/api/timeline?n=abc").status_code in (400, 404)


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


# --------------------------------------------------------------------------
# The log copy of the evidence
# --------------------------------------------------------------------------


def test_the_timeline_is_logged_for_every_run(client, caplog):
    """A second copy of the evidence, by a different path from the store.

    The store is one file on a mounted bucket whose write failure is caught and
    logged rather than raised, so a run can complete and leave no record at
    all. `/api/timeline` also reads the store *by position*, which shifts under
    every later run. A log line is append-only by construction and timestamped
    by the platform.
    """
    import logging

    with caplog.at_level(logging.INFO, logger="master"):
        response = client.post("/api/research", json=brief())

    run_id = response.json()["run_id"]
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert run_id in logged
    assert "timeline" in logged


def test_the_log_names_the_run_when_the_store_could_not_be_written(client, monkeypatch, caplog):
    """The case the log copy exists for. If the store write fails, the run id
    in the failure line is the only handle left on what happened."""
    import logging

    def boom(run, **kwargs):
        raise OSError("read-only file system")

    monkeypatch.setattr(service, "record", boom)

    with caplog.at_level(logging.INFO, logger="master"):
        response = client.post("/api/research", json=brief())

    run_id = response.json()["run_id"]
    failures = [
        record.getMessage() for record in caplog.records if record.levelno >= logging.ERROR
    ]
    assert any(run_id in message for message in failures)


def test_the_run_id_is_in_the_api_response(client):
    """So a caller can quote it back when asking what happened."""
    payload = client.post("/api/research", json=brief()).json()
    assert payload["run_id"]


def test_both_entry_points_share_one_timeout(monkeypatch):
    """The CLI and the master must not disagree about how long a leg may take.

    The CLI took its timeout from an argparse default of 120 while the master
    read `RESEARCH_TIMEOUT_SECONDS`, so raising the master's limit left the
    negative-controls harness -- which runs the CLI -- on the old one. Measured
    2026-08-13: the Azure leg failed its positive control at 120s while
    answering the master, at 300s, perfectly. A control that runs a different
    configuration from the thing it controls is not a control.
    """
    from coordinator.cli import build_parser
    from coordinator.participants import default_timeout_seconds

    monkeypatch.setenv("RESEARCH_TIMEOUT_SECONDS", "300")
    assert default_timeout_seconds() == 300.0
    assert build_parser().parse_args(["a topic"]).timeout_seconds == 300.0


def test_a_junk_timeout_falls_back_rather_than_crashing_the_mesh(monkeypatch):
    from coordinator.participants import DEFAULT_TIMEOUT_SECONDS, default_timeout_seconds

    monkeypatch.setenv("RESEARCH_TIMEOUT_SECONDS", "soon")
    assert default_timeout_seconds() == DEFAULT_TIMEOUT_SECONDS


def test_no_drafts_has_an_exit_code_only_the_cli_can_emit():
    """A denial must be a claim nothing else can make.

    The negative-controls harness reads this CLI's exit code as the verdict:
    the mesh ran and no cloud answered means the leg was correctly refused. It
    returned 1, which is also what a crashed interpreter and an expired gcloud
    credential return -- and on 2026-08-13 the harness reported "denied, as
    required" for every negative control twice, once on a container that could
    not start (127) and once on gcloud credentials that expired mid-run (1).
    Both times nothing had been tested.
    """
    from coordinator.cli import NO_DRAFTS_EXIT

    assert NO_DRAFTS_EXIT == 3
    assert NO_DRAFTS_EXIT not in (0, 1, 2, 126, 127)


# --------------------------------------------------------------------------
# The debug view
# --------------------------------------------------------------------------


def test_flow_is_404_until_something_is_recorded(client):
    assert client.get("/api/flow").status_code == 404


def test_flow_shapes_a_run_for_the_view(client):
    client.post("/api/research", json=brief())
    payload = client.get("/api/flow").json()

    assert payload["run_id"]
    assert payload["participants"]
    assert payload["dimensions"]
    assert payload["max_total"] == 25.0
    assert {lane["source"] for lane in payload["lanes"]} == set(payload["participants"])
    assert payload["reviews"], "a judged run has at least one review"


def test_every_lane_reports_its_auth_mode(client):
    """The page shows it beside the cloud, so it has to come from the run
    rather than from the master's own configuration -- a leg that fell back is
    the case worth seeing."""
    client.post("/api/research", json=brief())
    for lane in client.get("/api/flow").json()["lanes"]:
        assert "auth" in lane


def test_the_critique_shown_is_the_one_the_mesh_would_send(client):
    """Not a second implementation in the page.

    The view rebuilds it with `judge.critique_for`, the same function the mesh
    calls when it sends a draft back. A reimplementation in JavaScript would
    drift, and the drift would be invisible: a plausible critique that no agent
    ever received renders exactly like a real one.
    """
    import asyncio

    from coordinator.flow import build_flow
    from coordinator.judge import RubricJudge, critique_for
    from coordinator.mesh import ResearchMesh
    from coordinator.models import ResearchRequest
    from coordinator.participants import Participant
    from tests.test_mesh import STRONG, WEAK, Improving

    source = Improving("aws", [WEAK, STRONG])
    mesh = ResearchMesh(
        [Participant(name="aws", source=source, cloud="aws")], max_rounds=3, judge=RubricJudge()
    )
    run = asyncio.run(mesh.run(ResearchRequest(topic="agent-to-agent protocols", max_words=300)))

    view = build_flow(run)
    first_round = view["reviews"][0]["entries"][0]
    assert first_round["critique_sent"] is True
    assert first_round["critique"] == critique_for(run.rounds[0].verdicts[0])
    # And it is the text the agent actually got.
    assert source.revisions[0].critique == first_round["critique"]


def test_a_final_round_failure_is_not_shown_as_a_critique_that_was_sent(client):
    """"Below the bar on the last round" and "sent back" are different facts.
    Rendering the first as the second invents a message."""
    import asyncio

    from coordinator.flow import build_flow
    from coordinator.mesh import ResearchMesh
    from coordinator.models import ResearchRequest
    from coordinator.participants import Participant
    from tests.test_mesh import WEAK, Improving

    run = asyncio.run(
        ResearchMesh(
            [Participant(name="aws", source=Improving("aws", [WEAK]), cloud="aws")],
            max_rounds=1,
        ).run(ResearchRequest(topic="agent-to-agent protocols", max_words=300))
    )

    entry = build_flow(run)["reviews"][-1]["entries"][0]
    assert entry["below_pass_mark"] is True
    assert entry["critique_sent"] is False
    assert entry["critique"] == ""


def test_the_page_serves_the_debug_tabs(client):
    body = client.get("/").text
    for tab in ("flow", "reviews", "wire"):
        assert f'data-tab="{tab}"' in body
        assert f'id="tab-{tab}"' in body


def test_the_pages_javascript_parses():
    """A syntax error in the inline script is a blank page with a console
    message nobody sees.

    The page is a string in a Python module, so nothing type-checks it, nothing
    bundles it, and the test suite happily passes with it broken. `node --check`
    is the cheapest thing that would have caught that, and this repo has
    carried "served, not rendered" as a known gap for weeks.
    """
    import re
    import shutil
    import subprocess
    import tempfile
    from pathlib import Path

    from coordinator.frontend import PAGE

    scripts = re.findall(r"<script>(.*?)</script>", PAGE, re.DOTALL)
    assert scripts, "the page has no script block"

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed; cannot syntax-check the page")

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "page.js"
        path.write_text("\n".join(scripts))
        result = subprocess.run(
            [node, "--check", str(path)], capture_output=True, text=True, check=False
        )
    assert result.returncode == 0, result.stderr[-2000:]


def test_every_element_the_script_reaches_for_exists():
    """`$('flow')` on an id that is not in the markup is a silent no-op that
    renders an empty tab."""
    from coordinator.frontend import PAGE

    for element_id in ("tab-flow", "tab-reviews", "tab-wire", "flow", "reviews", "wire", "flowNav"):
        assert f'id="{element_id}"' in PAGE, element_id


# --------------------------------------------------------------------------
# Live telemetry: the ping and the event stream
# --------------------------------------------------------------------------


def test_ping_touches_nothing(client, monkeypatch):
    """The page subtracts this from every latency it shows, so it has to
    measure the link and nothing else.

    If it read the store or looked up a peer, a slow disk would be reported as
    a slow network and then subtracted from the mesh's own numbers -- making
    the mesh look faster exactly when the machine was struggling.
    """

    def explode(*args, **kwargs):
        raise AssertionError("ping did work")

    monkeypatch.setattr(service, "load_runs", explode)
    monkeypatch.setattr(service, "build_mesh", explode)

    response = client.get("/api/ping?t=123")

    assert response.status_code == 200
    assert response.json() == {"pong": "123"}


def test_a_run_emits_events_for_what_it_is_doing(client):
    """A run takes 30 seconds to two minutes with models in it. Everything
    interesting was already happening and reached only the service log."""
    from coordinator.events import BUS

    # A marker rather than a prior length: the replay is a *bounded* deque, so
    # once earlier tests have filled it the old events are evicted and slicing
    # by the previous count silently yields nothing. The test then passes or
    # fails on how many tests ran before it, which is the worst kind of flake.
    BUS.publish("run", "--- marker ---")
    marker = BUS.replay()[-1]
    client.post("/api/research", json=brief())
    replay = BUS.replay()
    published = replay[replay.index(marker) + 1 :]

    kinds = {event["kind"] for event in published}
    assert "run" in kinds, "no event for the run starting"
    assert "leg" in kinds, "no event for a cloud being dialled"
    assert "judge" in kinds, "no event for the verdict"
    assert all("t" in event and "text" in event for event in published)


def test_a_slow_subscriber_loses_history_not_the_present():
    """A forgotten browser tab must not grow memory without bound and must
    never block a run. Losing the oldest events is the right failure: the
    newest are the ones being watched."""
    import asyncio

    from coordinator.events import SUBSCRIBER_BUFFER, EventBus

    async def scenario():
        bus = EventBus()
        stream = bus.subscribe()
        await stream.__anext__.__self__.asend(None) if False else None
        # Subscribe without reading, then flood well past the buffer.
        agen = bus.subscribe()
        task = asyncio.ensure_future(agen.__anext__())
        await asyncio.sleep(0)
        for index in range(SUBSCRIBER_BUFFER * 2):
            bus.publish("wire", f"event {index}")
        first = await task
        await agen.aclose()
        return first

    first = asyncio.run(scenario())
    # It kept going and handed over an event rather than raising or hanging.
    assert first["kind"] == "wire"


def test_publishing_never_raises_even_with_a_broken_subscriber():
    """An observer that can take the mesh down is not an observer."""
    from coordinator.events import EventBus

    bus = EventBus()

    class Hostile:
        def put_nowait(self, item):
            raise RuntimeError("nope")

    bus._subscribers.add(Hostile())
    bus.publish("run", "started")  # must not raise

    assert bus.replay()[-1]["text"] == "started"


def test_the_page_has_the_live_panels():
    from coordinator.frontend import PAGE

    assert 'id="tab-live"' in PAGE
    assert 'id="telem"' in PAGE
    assert 'id="evt"' in PAGE
    # The ping loop and the stream are what make it live rather than a snapshot.
    assert "api/ping" in PAGE
    assert "api/stream" in PAGE
