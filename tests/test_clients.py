"""Transport-free tests of the shared client behaviour.

Every stack inherits timing, error mapping, and parsing from A2AResearchClient,
so exercising the base against a stubbed ``_send`` covers all three without a
network, credentials, or vendor SDKs.
"""

import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import pytest

from clients import CLIENT_STACKS, load_client
from clients.base import A2AResearchClient
from coordinator.errors import AdapterError, FailureKind
from coordinator.models import ResearchRequest
from protocol.research import render_draft

BODY = """\
# Solid-state batteries

## Where production stands

QuantumScape shipped 12,000 cells in 2025. Toyota targets 2027 for a limited
launch, and Samsung SDI runs a pilot line at roughly 200 MWh a year.
"""

REPLY = render_draft(BODY, agent="gcp", model="gemini-2.5-flash", brain="llm")


class StubClient(A2AResearchClient):
    stack = "stub"

    def __init__(self, reply=REPLY, *, raises=None, **kwargs):
        super().__init__("http://stub.invalid", **kwargs)
        self._reply = reply
        self._raises = raises

    async def _send(self, prompt: str) -> str:
        self.last_prompt = prompt
        if self._raises:
            raise self._raises
        return self._reply


def request(**kwargs) -> ResearchRequest:
    return ResearchRequest(topic="solid-state batteries in 2026", **kwargs)


async def test_parses_a_draft_with_its_provenance():
    draft = await StubClient().research(request())

    assert draft.title == "Solid-state batteries"
    assert draft.model == "gemini-2.5-flash"
    assert draft.brain == "llm"
    assert draft.latency_ms >= 0


async def test_the_prompt_carries_the_brief():
    client = StubClient()
    await client.research(request(questions=["who ships at scale?"], max_words=250))

    assert "solid-state batteries in 2026" in client.last_prompt
    assert "who ships at scale?" in client.last_prompt
    assert "250 words" in client.last_prompt


async def test_a_reply_without_the_serving_header_still_parses():
    """Any A2A server can answer the brief; only ours sends the header."""
    draft = await StubClient(BODY).research(request())

    assert draft.title == "Solid-state batteries"
    assert draft.model == "unknown"


async def test_a_refusal_is_a_provider_failure_not_a_protocol_one():
    with pytest.raises(AdapterError) as exc:
        await StubClient("I cannot help with that.").research(request())

    assert exc.value.kind is FailureKind.PROVIDER


async def test_http_401_maps_to_authentication():
    failure = httpx.HTTPStatusError(
        "denied",
        request=httpx.Request("POST", "http://stub.invalid"),
        response=httpx.Response(401),
    )
    with pytest.raises(AdapterError) as exc:
        await StubClient(raises=failure).research(request())

    assert exc.value.kind is FailureKind.AUTHENTICATION


async def test_http_503_maps_to_transport():
    failure = httpx.HTTPStatusError(
        "unavailable",
        request=httpx.Request("POST", "http://stub.invalid"),
        response=httpx.Response(503),
    )
    with pytest.raises(AdapterError) as exc:
        await StubClient(raises=failure).research(request())

    assert exc.value.kind is FailureKind.TRANSPORT


async def test_connection_refused_maps_to_transport():
    with pytest.raises(AdapterError) as exc:
        await StubClient(raises=httpx.ConnectError("refused")).research(request())

    assert exc.value.kind is FailureKind.TRANSPORT


async def test_unknown_sdk_exception_maps_to_protocol_with_its_type():
    with pytest.raises(AdapterError) as exc:
        await StubClient(raises=RuntimeError("card is malformed")).research(request())

    assert exc.value.kind is FailureKind.PROTOCOL
    assert "RuntimeError" in str(exc.value)


def test_registry_rejects_unknown_stack():
    with pytest.raises(ValueError, match="unknown client stack"):
        load_client("langchain", "http://stub.invalid")


def test_every_declared_stack_is_constructible_or_reports_a_missing_sdk():
    """A stack whose SDK is absent must raise ImportError, not ValueError."""
    for stack in CLIENT_STACKS:
        try:
            client = load_client(stack, "http://stub.invalid")
        except ImportError:
            continue
        assert client.stack == stack
        assert client.endpoint == "http://stub.invalid"


class _Unauthorized(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(401)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


@contextmanager
def unauthorized_server():
    server = HTTPServer(("127.0.0.1", 0), _Unauthorized)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()


async def test_a_401_on_the_card_fetch_is_authentication_not_protocol():
    """Regression from the first authenticated deploy.

    a2a-sdk wraps the card fetch's HTTPStatusError in its own
    AgentCardResolutionError, so catching only the httpx type filed a real 401
    as a protocol failure -- the matrix pointing at the wrong layer entirely.
    Driven against a real 401 rather than a synthetic exception chain, because
    the shape of that chain is the vendor's choice and can change.
    """
    a2a_sdk = pytest.importorskip("clients.a2a_sdk")

    with unauthorized_server() as endpoint:
        client = a2a_sdk.A2ASdkClient(endpoint, timeout_s=10)
        with pytest.raises(AdapterError) as exc:
            await client.research(request())

    assert exc.value.kind is FailureKind.AUTHENTICATION
    # The URL distinguishes a privileged discovery endpoint from a privileged
    # message endpoint, which are different fixes.
    assert "agent-card.json" in str(exc.value)


def test_a_reply_carried_in_two_envelopes_is_read_once(monkeypatch):
    """Regression, found by running the mesh rather than by any test here.

    ADK's `to_a2a()` attaches the reply as an artifact *and* leaves it in task
    history. `_task_texts` reads every carrier the spec allows -- it has to, or
    Agent Framework's replies come back empty -- so the draft arrived twice and
    the body doubled. The currency parser never noticed: it indexed quotes by
    target currency, so the duplicate silently overwrote its twin.

    The a2a types are protobuf-backed, so the two helpers are stubbed rather
    than constructing real messages; what is under test is the dedupe, not the
    SDK's accessors.
    """
    a2a_sdk = pytest.importorskip("clients.a2a_sdk")
    from a2a.types import Role

    monkeypatch.setattr("a2a.helpers.get_text_parts", lambda parts: list(parts))
    monkeypatch.setattr("a2a.helpers.get_message_text", lambda message: message.text)

    class _Status:
        def HasField(self, _name):
            return False

    class _Message:
        def __init__(self, text):
            self.text = text
            self.role = Role.ROLE_AGENT

    class _Artifact:
        def __init__(self, parts):
            self.parts = parts

    class _Task:
        # ADK's shape: the same text in the artifact and in history.
        artifacts = [_Artifact(["the draft"])]
        status = _Status()
        history = [_Message("the draft")]

    assert a2a_sdk._task_texts(_Task()) == ["the draft"]


def test_distinct_texts_from_different_carriers_are_all_kept(monkeypatch):
    """Dedupe must not swallow a genuine second part."""
    a2a_sdk = pytest.importorskip("clients.a2a_sdk")
    from a2a.types import Role

    monkeypatch.setattr("a2a.helpers.get_text_parts", lambda parts: list(parts))
    monkeypatch.setattr("a2a.helpers.get_message_text", lambda message: message.text)

    class _Status:
        def HasField(self, _name):
            return False

    class _Message:
        def __init__(self, text):
            self.text = text
            self.role = Role.ROLE_AGENT

    class _Artifact:
        def __init__(self, parts):
            self.parts = parts

    class _Task:
        artifacts = [_Artifact(["part one"])]
        status = _Status()
        history = [_Message("part two")]

    assert a2a_sdk._task_texts(_Task()) == ["part one", "part two"]
