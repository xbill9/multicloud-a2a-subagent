"""Azure leg: a Microsoft Agent Framework research agent on Foundry, over A2A.

Unlike the AWS leg, this one does not sit on the reference executor: Agent
Framework ships its own ``A2AExecutor``, which converts between framework
Messages and A2A parts and drives the task lifecycle (submit / start_work /
complete) rather than replying with a single Message. That conversion layer is
what this column of the matrix tests, so it stays in the path in both brains --
direct mode swaps the model for a stub agent, not the executor.

    python -m agents.azure.server          # direct mode, no credentials
    RESEARCH_MODEL_MODE=llm python -m agents.azure.server   # Foundry model

Environment: ``PORT`` (10003), ``PUBLIC_URL``, ``FOUNDRY_PROJECT_ENDPOINT``,
``RESEARCH_MODEL_MODE``, and the model: ``RESEARCH_MODEL_AZURE`` or
``AZURE_AI_MODEL_DEPLOYMENT_NAME`` (no default -- a deployment name cannot be
guessed).
"""

import os

from agents.common import (
    AGENT_NAME,
    DESCRIPTION,
    INSTRUCTION,
    INSTRUCTION_VERSION,
    direct_reply,
    model_mode,
    public_url,
    resolve_model,
    wrap_responder,
)
from agents.serving import build_agent_card, build_app
from protocol.research import render_draft
from protocol.search import reset_budget, search_count, search_enabled, web_search
from protocol.telemetry import (
    setup as setup_telemetry,
)

DEFAULT_PORT = 10003
CLOUD = "azure"

# Before anything builds an agent: the instrumented httpx client has to be in
# place before a vendor SDK constructs its own, or that SDK's calls are
# invisible to the trace.
setup_telemetry("research-" + CLOUD)


def model_id() -> str:
    """The Foundry deployment name, or "none" when no model is in the path.

    Read rather than required at import: this module is imported by the test
    suite and by `direct` mode on every cloud, neither of which has a Foundry
    project. `llm` mode fails loudly at build time instead, which is the right
    moment -- a missing deployment name that surfaces on the first request
    looks like a protocol failure to whoever is watching the matrix.

    ``RESEARCH_MODEL_AZURE`` or ``AZURE_AI_MODEL_DEPLOYMENT_NAME`` -- see
    ``resolve_model``. No default, deliberately: a deployment name is an
    account-local string, and a guess would surface as a provider 404.
    """
    return resolve_model(CLOUD, None)


class _DirectAgent:
    """Minimal ``SupportsAgentRun`` stub so A2AExecutor can run without a model.

    Implements only what A2AExecutor calls: ``create_session`` and ``run``
    returning an object with ``.messages``. Keeps Agent Framework's A2A
    conversion in the path while removing the model and its credentials.
    """

    name = AGENT_NAME

    def create_session(self, session_id: str | None = None, **kwargs):
        return None

    async def run(self, query, session=None, stream: bool = False, **kwargs):
        from agent_framework import AgentResponse, Message

        responder = wrap_responder(direct_reply, agent=CLOUD, model="none")
        return AgentResponse(
            messages=[Message(role="assistant", contents=[await responder(str(query))])]
        )


def _foundry_agent():
    """Native brain: a Foundry-hosted model behind an Agent Framework Agent.

    The header is stamped by ``_StampedAgent`` below rather than by
    ``wrap_responder``, because A2AExecutor calls the *agent*, not a responder
    -- there is no function seam on this cloud either, just a different shape
    of one from ADK's.
    """
    from agent_framework import Agent
    from agent_framework.foundry import FoundryChatClient
    from azure.identity import DefaultAzureCredential

    client = FoundryChatClient(
        project_endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
        model=model_id(),
        credential=DefaultAzureCredential(),
    )
    # Agent Framework exports ``SupportsWebSearchTool``, which is a protocol a
    # chat client may declare -- not a tool that can be handed to an agent, and
    # Foundry's own grounding needs a Bing resource connection created out of
    # band. So this cloud gets the same shared function as the other two, bound
    # through Agent Framework's own tool machinery, which is the part that
    # differs and the part the matrix measures.
    return Agent(
        client=client,
        name=AGENT_NAME,
        description=DESCRIPTION,
        instructions=INSTRUCTION,
        tools=[web_search] if search_enabled() else [],
        default_options={"store": False},
    )


class _StampedAgent:
    """Delegate to a real Agent and stamp the serving header on its reply."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self.name = getattr(inner, "name", AGENT_NAME)

    def create_session(self, session_id: str | None = None, **kwargs):
        return self._inner.create_session(session_id=session_id, **kwargs)

    async def run(self, query, session=None, stream: bool = False, **kwargs):
        from agent_framework import AgentResponse, Message

        reset_budget()
        before = search_count()
        response = await self._inner.run(query, session=session, stream=stream, **kwargs)
        body = getattr(response, "text", None) or ""
        return AgentResponse(
            messages=[
                Message(
                    role="assistant",
                    contents=[
                        render_draft(
                            body,
                            agent=CLOUD,
                            model=model_id(),
                            brain=model_mode(),
                            searches=search_count() - before,
                            prompt_version=INSTRUCTION_VERSION,
                        )
                    ],
                )
            ]
        )


def build() -> tuple:
    from agent_framework_a2a import A2AExecutor

    agent = _StampedAgent(_foundry_agent()) if model_mode() == "llm" else _DirectAgent()
    card = build_agent_card(name=AGENT_NAME, url=public_url(DEFAULT_PORT), model=model_id())
    return build_app(A2AExecutor(agent), card, model=model_id()), card


app, _card = build()


def main() -> None:
    import uvicorn

    uvicorn.run(
        app,
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", str(DEFAULT_PORT))),
    )


if __name__ == "__main__":
    main()
