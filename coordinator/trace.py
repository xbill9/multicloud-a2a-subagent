"""Collect what each leg actually did on the wire, without threading it through.

The problem this solves is structural. A leg's HTTP calls are made in three
different places -- the credential mint inside ``coordinator.auth``, the
agent-card fetch inside the vendor's SDK, the A2A call inside the same SDK --
and none of them can see the others. Passing a collector down would mean
changing the signature of every client stack and every auth adapter, and the
SDKs in the middle would have to agree to carry it, which they will not.

So the collector is a context variable. ``clients.base`` opens one per leg;
the httpx event hooks and the auth boundary logger append to whatever is open.
``asyncio.gather`` copies the context into each task, so three legs running
concurrently write into three separate traces with no coordination and no
locking. Nothing outside a ``collect()`` block records anything, which is why
the hermetic tests and the in-process adapters produce empty traces rather
than fictional ones.

**Never record a credential.** No headers, no bodies on success, no query
strings -- Google's metadata mint takes the audience as a query parameter and
returns the token as the body, and a trace that has to be sanitised before it
can be shown to anyone is a trace that will not be shown. The label carries
whatever the calling code chose to name, which is already audience-level
detail written by us rather than by a provider.
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from time import perf_counter

import httpx

from coordinator.models import TraceStep

log = logging.getLogger("trace")

#: Agent-card paths, per the A2A spec and ADK's older spelling. Matched on the
#: path rather than guessed from ordering: the card fetch is not reliably the
#: first request a stack makes, and a discovery step mislabelled as an
#: invocation turns a 403 on discovery -- the single most misleading failure in
#: this space -- back into the thing it took the predecessor series a day to
#: tell apart.
_CARD_PATHS = ("/.well-known/agent-card.json", "/.well-known/agent.json")

#: Headers the three providers use for their own request identifier, in the
#: order they are looked for. This is the only column in a trace that an
#: outside reader can verify: everything else is this process describing its
#: own behaviour, while a request id can be looked up in CloudWatch, Cloud
#: Logging or Azure Monitor and either finds the call or does not. Lower-cased
#: because httpx headers are case-insensitive but the providers are not
#: consistent -- AWS sends `x-amzn-RequestId`, Azure `x-ms-request-id`.
_REQUEST_ID_HEADERS = (
    "x-amzn-requestid",
    "x-amzn-request-id",
    "x-amz-request-id",
    "x-ms-request-id",
    "x-ms-correlation-request-id",
    "x-goog-request-id",
    "x-cloud-trace-context",
    "x-request-id",
)


class LegTrace:
    """The steps recorded for one leg, plus the round trips still in flight."""

    def __init__(self) -> None:
        self.steps: list[TraceStep] = []
        # A list per key, not one entry: a retry, a redirect or a stack that
        # polls the same URL twice puts two identical requests in flight at
        # once, and a single slot means the second overwrites the first's start
        # time. The first response then reports an impossibly short call and
        # the second finds nothing at all, landing at offset 0 -- rendered as
        # a call that happened before the run began.
        self._pending: dict[str, list[tuple[float, datetime]]] = {}

    def start(self, request: httpx.Request) -> None:
        self._pending.setdefault(_key(request), []).append(
            (perf_counter(), datetime.now(UTC))
        )

    def finish(self, response: httpx.Response) -> None:
        request = response.request
        in_flight = self._pending.get(_key(request)) or []
        started = in_flight.pop(0) if in_flight else None
        url = request.url
        path = url.path or "/"
        self.steps.append(
            TraceStep(
                phase="discovery" if path in _CARD_PATHS else "invoke",
                label=f"{request.method} {path}",
                host=url.host or "",
                path=path,
                method=request.method,
                status=response.status_code,
                started_at=started[1] if started else None,
                elapsed_ms=(perf_counter() - started[0]) * 1000 if started else 0.0,
                bytes=_content_length(response),
                request_id=_request_id(response),
                ok=response.is_success,
                # The body is deliberately not read here: an event hook runs
                # before the response is streamed, and reading it would consume
                # the stream the caller is about to parse. Failure bodies are
                # already carried into the raised error by clients.base.
                detail="" if response.is_success else response.reason_phrase,
            )
        )

    def credential(self, boundary: str, response: httpx.Response) -> None:
        """One identity-provider round trip, from an already-read response."""
        url = response.request.url
        self.steps.append(
            TraceStep(
                phase="credential",
                label=boundary,
                host=url.host or "",
                path=url.path or "/",
                method=response.request.method,
                status=response.status_code,
                started_at=datetime.now(UTC) - timedelta(milliseconds=_elapsed_ms(response)),
                elapsed_ms=_elapsed_ms(response),
                bytes=_content_length(response),
                request_id=_request_id(response),
                ok=response.is_success,
                detail="" if response.is_success else response.text[:400],
            )
        )


def _key(request: httpx.Request) -> str:
    return f"{request.method} {request.url}"


def _content_length(response: httpx.Response) -> int | None:
    """How much came back, from the header rather than from the body.

    The body is off limits inside an event hook -- reading it consumes the
    stream the caller is about to parse -- so this is what the provider
    declared, and it is None when the provider declared nothing. Reported as
    unknown rather than as zero: a chunked response with no content-length and
    an empty response are very different answers to "what returned".
    """
    raw = response.headers.get("content-length")
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _request_id(response: httpx.Response) -> str:
    """The provider's own id for this call, or "" if it did not send one.

    Read from the headers, which are already parsed and cost nothing, rather
    than from the body -- an event hook must not touch the stream. Empty is a
    real answer and is rendered as such: a provider that sends no request id
    leaves a call that cannot be cross-checked from the outside, and pretending
    otherwise by synthesising one would defeat the only purpose this field has.
    """
    for header in _REQUEST_ID_HEADERS:
        if value := response.headers.get(header):
            return value.strip()[:120]
    return ""


def _elapsed_ms(response: httpx.Response) -> float:
    """``response.elapsed``, or 0 if httpx will not give it to us yet.

    It raises rather than returning None on an unread response, and a trace is
    never worth an exception on a path that has just successfully minted a
    credential.
    """
    try:
        return response.elapsed.total_seconds() * 1000
    except RuntimeError:
        return 0.0


_current: ContextVar[LegTrace | None] = ContextVar("current_leg_trace", default=None)


@contextmanager
def collect() -> Iterator[LegTrace]:
    """Open a trace for one leg. Nested opens replace, they do not merge."""
    leg = LegTrace()
    token = _current.set(leg)
    try:
        yield leg
    finally:
        _current.reset(token)


def current() -> LegTrace | None:
    return _current.get()


async def on_request(request: httpx.Request) -> None:
    leg = _current.get()
    if leg is not None:
        leg.start(request)


async def on_response(response: httpx.Response) -> None:
    leg = _current.get()
    if leg is not None:
        leg.finish(response)


def record_credential(boundary: str, response: httpx.Response) -> None:
    leg = _current.get()
    if leg is not None:
        leg.credential(boundary, response)


#: Attach to every ``httpx.AsyncClient`` the mesh builds. Hooks rather than a
#: custom transport, because a transport would have to be composed with the
#: SigV4 signing path and the ``local_address`` binding the a2a-sdk stack sets,
#: and there is no reason for an observer to be in that position.
EVENT_HOOKS = {"request": [on_request], "response": [on_response]}


__all__ = [
    "EVENT_HOOKS",
    "LegTrace",
    "collect",
    "current",
    "record_credential",
]
