"""The master: front end, fan-out and judge, on one Cloud Run service.

This is the front door. A brief arrives here, goes out to every researcher
agent over A2A, and the judge -- running in this same process -- reads the
drafts that came back and ranks them.

**Why the judge is in-process and not a fourth agent.** It has to see all three
drafts to rank them, so it is the one step in the system that cannot start
until the slowest cloud has answered. Putting it behind another network hop
would add a leg that can fail *after* every expensive call has already
succeeded, and lose three drafts to a judge timeout. It is a pure function of
the drafts; it does not need its own address. The cost is stated in the README
and not argued away: the judge shares a vendor with one of the three
participants.

**A service, not a job.** This is the coordinator, and the coordinator holds an
address: a person with a question needs somewhere to send it, and a scheduled
run is a POST from Cloud Scheduler with an OIDC token. Deploying a second copy
of this code as a Cloud Run job to do the same work in a different execution
model is how the project ended up, for a while, with a job named
``research-coordinator`` standing in for a front door that had never been
deployed at all.

``coordinator/cli.py`` still exists, for local runs and for the negative
controls, which are the one thing a job is genuinely better at -- they need a
per-execution environment override and an exit code to assert on. Both entry
points build their mesh through
``coordinator.participants.build_participants``, so there is one definition of
what a peer is and they cannot drift apart on which clouds are wired or how a
leg authenticates.

Deployed from source with no Dockerfile -- see ``Procfile`` and
``infra/deploy_gcp.sh``.

    uvicorn coordinator.service:app --port 8080     # local, against the local mesh

Environment: ``PORT``, ``RESEARCH_JUDGE_MODE``, ``RESEARCH_TIMEOUT_SECONDS``,
``RESEARCH_EVAL_STORE``, plus the per-peer ``*_A2A_ENDPOINT`` and
``*_A2A_AUTH`` pairs that ``coordinator.auth`` reads.
"""

import asyncio
import json
import logging
import os
from urllib.parse import urlsplit

from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    StreamingResponse,
)
from starlette.routing import Route

from clients import CLIENT_STACKS
from coordinator.errors import AdapterError
from coordinator.events import BUS
from coordinator.flow import build_flow
from coordinator.frontend import PAGE
from coordinator.judge import JUDGE_MODES, judge_mode, load_judge
from coordinator.mesh import ResearchMesh
from coordinator.models import ResearchRequest
from coordinator.participants import (
    CLOUD_ENDPOINTS,
    build_participants,
    default_timeout_seconds,
    endpoint_for,
)
from coordinator.timeline import render as render_timeline
from evaluations.report import aggregate
from evaluations.report import render as render_audit
from evaluations.store import load as load_runs
from evaluations.store import record, store_path
from protocol.telemetry import (
    instrument_app,
    telemetry_summary,
)
from protocol.telemetry import (
    setup as setup_telemetry,
)

logging.basicConfig(format="[%(levelname)s]: %(message)s", level=logging.INFO)
log = logging.getLogger("master")

# Before the mesh builds any client: httpx has to be instrumented before a
# vendor SDK constructs its own, or that SDK's calls never reach the trace.
setup_telemetry("research-master")

DEFAULT_TIMEOUT = default_timeout_seconds()

#: Serialises appends to the evaluation store. The store is one file appended
#: to by every run, and on Cloud Run it is a GCS volume where a write is a
#: whole-object rewrite rather than an append -- two concurrent runs there lose
#: one of themselves silently, which for an audit is the worst available
#: failure. The deploy also pins the service to one instance; this lock is the
#: half of that guarantee which survives someone raising --max-instances.
_store_lock = asyncio.Lock()


def build_mesh(
    clouds: list[str] | None,
    *,
    client: str = "a2a-sdk",
    judge: str | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT,
) -> ResearchMesh:
    """Assemble the mesh for one request. Replaced wholesale in tests."""
    return ResearchMesh(
        build_participants(clouds, client=client, timeout_seconds=timeout_seconds),
        judge=load_judge(judge),
        timeout_seconds=timeout_seconds,
    )


async def index(request):
    return HTMLResponse(PAGE)


async def health(request):
    """What this master is wired to, without calling anything.

    Configured rather than measured, and named that way in the payload: a
    health check that dials three clouds turns a page load into a fan-out, and
    a peer that is merely *configured* must never render as a peer that
    answered. The mode a leg actually used is reported per run, in
    ``auth_modes``.
    """
    peers = []
    for cloud in CLOUD_ENDPOINTS:
        endpoint = endpoint_for(cloud)
        peers.append(
            {
                "cloud": cloud,
                "endpoint": endpoint,
                "reachable_as": urlsplit(endpoint).netloc or endpoint,
                "auth": os.getenv(f"{cloud.upper()}_A2A_AUTH", "none").strip().lower(),
            }
        )
    return JSONResponse(
        {
            "status": "ok",
            "role": "master",
            "cloud": os.getenv("RESEARCH_COORDINATOR_CLOUD", "unknown"),
            "judge": judge_mode(),
            "timeout_seconds": DEFAULT_TIMEOUT,
            "store": str(store_path()),
            "telemetry": telemetry_summary(),
            "peers": peers,
        }
    )


async def research(request):
    try:
        payload = await request.json()
    except (json.JSONDecodeError, ValueError):
        return JSONResponse({"error": "body must be JSON"}, status_code=400)
    if not isinstance(payload, dict):
        return JSONResponse({"error": "body must be a JSON object"}, status_code=400)

    try:
        brief = ResearchRequest(
            topic=payload.get("topic") or "",
            questions=payload.get("questions") or [],
            max_words=payload.get("max_words") or 600,
        )
    except (ValidationError, ValueError, TypeError) as exc:
        return JSONResponse({"error": f"invalid brief: {exc}"}, status_code=400)

    client = payload.get("client") or "a2a-sdk"
    if client not in CLIENT_STACKS:
        return JSONResponse(
            {"error": f"unknown client stack {client!r} (expected one of {CLIENT_STACKS})"},
            status_code=400,
        )

    judge = payload.get("judge") or None
    if judge is not None and judge not in JUDGE_MODES:
        return JSONResponse(
            {"error": f"unknown judge {judge!r} (expected one of {JUDGE_MODES})"},
            status_code=400,
        )

    clouds = payload.get("clouds") or None
    try:
        mesh = build_mesh(clouds, client=client, judge=judge, timeout_seconds=DEFAULT_TIMEOUT)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except (AdapterError, ImportError) as exc:
        # A credential this master cannot mint, or a client SDK missing from
        # the image. Both are this service's fault rather than the caller's,
        # and both are invisible in a 500 with no body.
        log.exception("could not assemble the mesh")
        return JSONResponse({"error": f"mesh unavailable: {exc}"}, status_code=503)

    run = await mesh.run(brief)

    # The whole timeline into the service log, every run, before the store is
    # touched. Two reasons, both learned here rather than assumed: the store is
    # a mounted bucket whose write failure is caught and logged below, so a run
    # can complete and leave no record at all; and `/api/timeline` reads the
    # store by position, which shifts under every later run. A log line is
    # append-only by construction, timestamped by the platform, and carries the
    # run id that ties it back to the stored copy.
    log.info("run %s timeline:\n%s", run.run_id, render_timeline(run))

    if payload.get("record", True):
        async with _store_lock:
            try:
                record(run)
                log.info("run %s recorded to %s", run.run_id, store_path())
            except OSError as exc:
                # A read-only or unmounted store must not discard a completed
                # run: the drafts cost three cross-cloud calls, the recording
                # did not.
                log.error("could not record run %s: %s", run.run_id, exc)

    return JSONResponse(json.loads(run.model_dump_json()))


async def last_run(request):
    """The most recent recorded run, read back out of the store.

    Read from the store rather than cached in memory on purpose: the instance
    that served a run is not the instance that will be asked about it, and a
    report that only works while the container is warm is a report that fails
    exactly when someone is trying to show it to somebody.
    """
    try:
        runs = list(load_runs())
    except OSError as exc:
        return JSONResponse({"error": f"evaluation store unreadable: {exc}"}, status_code=503)
    if not runs:
        return JSONResponse({"error": "no run has been recorded yet"}, status_code=404)
    return JSONResponse(json.loads(runs[-1][1].model_dump_json()))


async def timeline(request):
    """The last run's calls in wall-clock order, as plain text.

    The simplest thing that proves the mesh is real: one request, no browser,
    and it shows every call that was made, what came back and when. `?n=2`
    walks back through the store for the run before it.
    """
    try:
        runs = list(load_runs())
    except OSError as exc:
        return PlainTextResponse(f"evaluation store unreadable: {exc}", status_code=503)
    if not runs:
        return PlainTextResponse("no run has been recorded yet", status_code=404)

    try:
        index = max(1, int(request.query_params.get("n", "1")))
    except ValueError:
        return PlainTextResponse("n must be a positive integer", status_code=400)
    if index > len(runs):
        return PlainTextResponse(
            f"only {len(runs)} run(s) recorded", status_code=404
        )

    return PlainTextResponse(render_timeline(runs[-index][1]))


async def ping(request):
    """Echo, touching nothing.

    The page samples this once a second to measure its own round trip to the
    master, and the number is worth having for a reason beyond curiosity: every
    latency on this page is measured from the browser, so without it a 400ms
    reading cannot be told apart from a 300ms leg behind a 100ms link. Subtract
    this and what is left is the mesh.

    Deliberately does no work -- no store read, no peer lookup, nothing that
    could make the ping report on anything but the link.
    """
    return JSONResponse({"pong": request.query_params.get("t", "")})


async def stream(request):
    """Server-sent events for a run in progress.

    A run with models in the path takes 30 seconds to two minutes, and the page
    used to show a spinner for all of it while the interesting part -- a
    credential minted, a card fetched, a leg answering, a draft sent back --
    happened invisibly in the service log.

    Replays the recent buffer first, so a page opened mid-run shows what has
    already happened rather than an empty log.
    """

    async def events():
        # A comment frame up front: it commits the response and defeats any
        # proxy buffering that would otherwise hold the stream until the first
        # real event, which on a cold mesh can be seconds away.
        yield ": open\n\n"
        # No handler for CancelledError: the client going away is how this
        # generator is *meant* to end, and catching it only to re-raise adds a
        # frame that says nothing.
        async for event in BUS.subscribe():
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def flow(request):
    """One run, shaped for the debug view. `?n=2` walks back through the store.

    Shaped on the server rather than in the page, and the reason is narrow: the
    critique shown against each draft is rebuilt with the same
    `judge.critique_for` the mesh called when it sent that draft back. A second
    implementation in the browser would drift, and the drift would be invisible
    -- the page would show a plausible critique no agent ever received.
    """
    try:
        runs = list(load_runs())
    except OSError as exc:
        return JSONResponse({"error": f"evaluation store unreadable: {exc}"}, status_code=503)
    if not runs:
        return JSONResponse({"error": "no run has been recorded yet"}, status_code=404)

    try:
        index = max(1, int(request.query_params.get("n", "1")))
    except ValueError:
        return JSONResponse({"error": "n must be a positive integer"}, status_code=400)
    if index > len(runs):
        return JSONResponse({"error": f"only {len(runs)} run(s) recorded"}, status_code=404)

    return JSONResponse({"total_runs": len(runs), **build_flow(runs[-index][1])})


async def audit(request):
    try:
        text = render_audit(aggregate(list(load_runs())))
    except OSError as exc:
        return PlainTextResponse(f"evaluation store unreadable: {exc}", status_code=503)
    return PlainTextResponse(text)


app = Starlette(
    routes=[
        Route("/", index),
        Route("/health", health),
        Route("/api/health", health),
        Route("/api/research", research, methods=["POST"]),
        Route("/api/last", last_run),
        Route("/api/timeline", timeline),
        Route("/api/ping", ping),
        Route("/api/stream", stream),
        Route("/api/flow", flow),
        Route("/api/audit", audit),
    ]
)
instrument_app(app)


def main() -> None:
    import uvicorn

    uvicorn.run(
        app,
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8080")),
    )


if __name__ == "__main__":
    main()
