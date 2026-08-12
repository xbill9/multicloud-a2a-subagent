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

**Why a service and not the job.** ``coordinator/cli.py`` still exists and
still runs as a Cloud Run job, which is the right shape for a scheduled,
reproducible, recorded run. It is a poor shape for a person with a question.
Both build their mesh through ``coordinator.participants.build_participants``,
so there is one definition of what a peer is and the two entry points cannot
drift apart on which clouds are wired or how a leg authenticates.

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
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse
from starlette.routing import Route

from clients import CLIENT_STACKS
from coordinator.errors import AdapterError
from coordinator.frontend import PAGE
from coordinator.judge import JUDGE_MODES, judge_mode, load_judge
from coordinator.mesh import ResearchMesh
from coordinator.models import ResearchRequest
from coordinator.participants import CLOUD_ENDPOINTS, build_participants, endpoint_for
from evaluations.report import aggregate
from evaluations.report import render as render_audit
from evaluations.store import load as load_runs
from evaluations.store import record, store_path

logging.basicConfig(format="[%(levelname)s]: %(message)s", level=logging.INFO)
log = logging.getLogger("master")

DEFAULT_TIMEOUT = float(os.getenv("RESEARCH_TIMEOUT_SECONDS", "120"))

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

    if payload.get("record", True):
        async with _store_lock:
            try:
                record(run)
            except OSError as exc:
                # A read-only or unmounted store must not discard a completed
                # run: the drafts cost three cross-cloud calls, the recording
                # did not.
                log.error("could not record run: %s", exc)

    return JSONResponse(json.loads(run.model_dump_json()))


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
        Route("/api/audit", audit),
    ]
)


def main() -> None:
    import uvicorn

    uvicorn.run(
        app,
        host=os.getenv("HOST", "0.0.0.0"),  # noqa: S104 - Cloud Run requires it
        port=int(os.getenv("PORT", "8080")),
    )


if __name__ == "__main__":
    main()
