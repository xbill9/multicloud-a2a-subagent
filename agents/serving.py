"""a2a-sdk 1.x serving scaffolding shared by the AWS and Azure agents.

Both sit on the reference Starlette routes; they differ in the executor that
turns a request into a reply. Google's agent does not use this module at all
-- ADK's ``to_a2a()`` builds its own app, which is precisely the difference
the matrix is looking for.
"""

from collections.abc import Awaitable, Callable

from a2a.helpers import new_text_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill, Role
from a2a.utils.constants import PROTOCOL_VERSION_CURRENT, VERSION_HEADER
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.routing import Route

from agents.common import (
    DESCRIPTION,
    INSTRUCTION,
    degrade,
    direct_reply,
    model_mode,
    wrap_responder,
)
from protocol.search import search_summary
from protocol.telemetry import instrument_app, telemetry_summary

Responder = Callable[[str], Awaitable[str]]


class CallbackExecutor(AgentExecutor):
    """Adapt a plain ``async (prompt) -> reply`` function to the A2A executor API.

    Enqueues a single Message, the immediate-response workflow the executor
    contract documents, which is all a one-shot draft needs.
    """

    def __init__(self, responder: Responder) -> None:
        self._responder = responder

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        prompt = context.get_user_input()
        reply = await self._responder(prompt)
        await event_queue.enqueue_event(new_text_message(reply, role=Role.ROLE_AGENT))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        await event_queue.enqueue_event(
            new_text_message("research drafts are not cancellable", role=Role.ROLE_AGENT)
        )


def direct_executor(*, agent: str) -> CallbackExecutor:
    """The credential-free brain: a canned draft assembled from the brief."""
    return CallbackExecutor(wrap_responder(direct_reply, agent=agent, model="none"))


def build_agent_card(
    *, name: str, url: str, model: str, version: str = "0.1.0"
) -> AgentCard:
    return AgentCard(
        name=name,
        description=DESCRIPTION,
        version=version,
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        capabilities=AgentCapabilities(streaming=False),
        # The advertised URL is configuration, never the bind address.
        supported_interfaces=[AgentInterface(url=url, protocol_binding="JSONRPC")],
        skills=[
            AgentSkill(
                id="research_brief",
                name="research brief",
                description=INSTRUCTION,
                # `brain:` and `model:` ride on the card because /health is not
                # reachable for every stack: AgentCore exposes only its own
                # /invocations and /ping contract, so an arbitrary path on the
                # container is unreachable from outside. The card is fetched
                # over the same authenticated path the calls use, so it always
                # is. `model:` matters more here than `brain:` ever did -- an
                # audit that cannot say which model wrote a draft is not an
                # audit.
                tags=["research", "writing", "analysis", f"brain:{model_mode()}", f"model:{model}"],
            )
        ],
    )


def build_app(executor: AgentExecutor, card: AgentCard, *, model: str = "unknown") -> Starlette:
    handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )

    async def health(request):
        # `brain`, `model` and `degraded` are reported by the agent because
        # only the agent knows them. The matrix used to print the mode from its
        # *own* process, which is a different container once deployed -- a run
        # against three `llm` agents duly printed brain=direct.
        #
        # `degraded` is the same lesson applied to fault injection: an agent
        # deliberately returning bad output must say so, or a measurement run
        # can include one without anybody noticing. That is not hypothetical --
        # the local suite failed exactly this way, against a mesh left running
        # in degraded mode from the previous demo.
        return JSONResponse(
            {
                "status": "ok",
                "agent": card.name,
                "brain": model_mode(),
                "model": model,
                "degraded": degrade(),
                # Whether this agent researched or recalled, and how
                # often it looked. The coordinator cannot see a
                # researcher's outbound search calls -- they happen on
                # another cloud, outside any trace it opened -- so this
                # is the only place that fact is observable.
                "search": search_summary(),
                "telemetry": telemetry_summary(),
            }
        )

    async def ping(request):
        """AgentCore Runtime's health contract.

        Required verbatim: ``status`` must be ``Healthy`` or ``HealthyBusy``.
        Deliberately omits ``time_of_last_update`` -- a timestamp that advances
        on every ping reads as a continuous status change, which stops the idle
        session timeout from ever firing and leaks sessions until MaxLifetime.
        Omitting it lets the platform track changes itself.
        """
        return JSONResponse({"status": "Healthy"})

    app = Starlette(
        routes=[
            *create_agent_card_routes(card),
            *create_jsonrpc_routes(handler, "/"),
            Route("/health", health, methods=["GET"]),
            Route("/ping", ping, methods=["GET"]),
        ],
        middleware=[Middleware(_AssumeCurrentProtocolVersion)],
    )
    # Continues the caller's trace rather than starting a new one: the
    # coordinator's leg span and this agent's work belong in the same trace, and
    # the `traceparent` header that joins them is already on the request.
    instrument_app(app)
    return app


class _AssumeCurrentProtocolVersion(BaseHTTPMiddleware):
    """Treat a missing ``A2A-Version`` header as the current version, not 0.3.

    a2a-sdk reads the protocol version from that header and, when it is absent,
    assumes ``0.3`` and then rejects the request its own handler cannot serve:
    ``A2A version '0.3' is not supported by this handler. Expected version
    '1.0'``. A missing header is not evidence of an old client -- it is no
    evidence at all.

    This matters because **AgentCore does not forward the header**. Cloud Run
    and Container Apps pass it through untouched, so the same client, the same
    a2a-sdk on both ends, and the same server code succeed on two clouds and
    fail on the third. It surfaced the moment the AWS image was rebuilt onto a
    current a2a-sdk (2026-08-09); the previously deployed image predated the
    version check, which is why the leg had been green while carrying it.

    Scoped deliberately to *absent*: a header that says 0.3 is a real client
    statement and is still rejected.
    """

    async def dispatch(self, request, call_next):
        if VERSION_HEADER.lower() not in {k.lower() for k in request.headers}:
            request.scope["headers"] = [
                *request.scope["headers"],
                (VERSION_HEADER.lower().encode(), PROTOCOL_VERSION_CURRENT.encode()),
            ]
        return await call_next(request)
