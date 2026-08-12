"""GCP leg: a Google ADK research agent on Gemini, served over A2A by ``to_a2a()``.

The only one of the three that does not touch the a2a-sdk serving scaffolding
-- ADK builds its own Starlette app. It is also the only one that gives no way
to configure the URL its card advertises: ``to_a2a(host, port)`` writes the
*bind* address into the card, so every remote client must rewrite it. That is
the interop finding this whole exercise started from, and it is left
un-patched here on purpose: the fix belongs in the client, and the matrix
should show which clients can express it.

    python -m agents.gcp.server            # direct mode, no credentials
    RESEARCH_MODEL_MODE=llm python -m agents.gcp.server     # Gemini

Environment: ``PORT`` (10001), ``GENAI_MODEL``, ``RESEARCH_MODEL_MODE``.
"""

import logging
import os
from collections.abc import AsyncGenerator

from starlette.responses import JSONResponse

from agents.common import (
    AGENT_NAME,
    DESCRIPTION,
    INSTRUCTION,
    degrade,
    direct_reply,
    model_mode,
    wrap_responder,
)
from protocol.research import render_draft

logging.basicConfig(format="[%(levelname)s]: %(message)s", level=logging.INFO)

DEFAULT_PORT = 10001
CLOUD = "gcp"


def model_id() -> str:
    """Which Gemini this leg will actually use, or "none" with no model at all."""
    if model_mode() != "llm":
        return "none"
    return os.getenv("GENAI_MODEL", "gemini-2.5-flash")


def _direct_agent():
    """A BaseAgent that answers without a model, so to_a2a() stays in the path."""
    from google.adk.agents import BaseAgent
    from google.adk.events import Event
    from google.genai import types

    responder = wrap_responder(direct_reply, agent=CLOUD, model="none")

    class DirectResearchAgent(BaseAgent):
        async def _run_async_impl(self, ctx) -> AsyncGenerator:
            prompt = ""
            if ctx.user_content and ctx.user_content.parts:
                prompt = "\n".join(part.text or "" for part in ctx.user_content.parts)
            yield Event(
                author=self.name,
                content=types.Content(
                    role="model", parts=[types.Part(text=await responder(prompt))]
                ),
            )

    return DirectResearchAgent(name=AGENT_NAME, description=DESCRIPTION)


def _llm_agent():
    """Native brain: Gemini writing the draft through ADK.

    No tools. The currency agent needed one because a rate is a lookup; a
    research brief is the model's own output, and giving one cloud a search
    tool the others do not have would make the audit a comparison of tool
    access rather than of models. If search is added later it has to be added
    to all three, and the audit's history cut at that point.
    """
    from google.adk.agents import LlmAgent

    return LlmAgent(
        model=model_id(),
        name=f"{AGENT_NAME}_gemini",
        description=DESCRIPTION,
        instruction=INSTRUCTION,
    )


def _stamped(inner):
    """Run the LlmAgent and stamp the serving header on what it produced.

    The other two clouds wrap a plain ``async (prompt) -> reply`` responder;
    ADK offers no such seam, because ``to_a2a()`` takes an *agent* and
    serialises its event stream. So the header is written by an agent in that
    stream instead.

    Done this way rather than by asking Gemini to emit the header itself: a
    model that gets its own metadata line wrong misattributes a draft in the
    audit, and a misattributed draft is the one error the audit cannot detect
    from the inside. Collapsing the inner events into one is safe only because
    this agent has no tools -- give it any and this needs to forward the
    non-text events rather than swallow them.
    """
    from google.adk.agents import BaseAgent
    from google.adk.events import Event
    from google.genai import types

    class HeaderStamped(BaseAgent):
        async def _run_async_impl(self, ctx) -> AsyncGenerator:
            texts: list[str] = []
            async for event in inner.run_async(ctx):
                if event.content and event.content.parts:
                    texts.extend(part.text for part in event.content.parts if part.text)
            body = "\n".join(text for text in texts if text)
            yield Event(
                author=self.name,
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part(
                            text=render_draft(
                                body, agent=CLOUD, model=model_id(), brain=model_mode()
                            )
                        )
                    ],
                ),
            )

    return HeaderStamped(name=AGENT_NAME, description=DESCRIPTION, sub_agents=[inner])


def build():
    from google.adk.a2a.utils.agent_to_a2a import to_a2a

    root_agent = _stamped(_llm_agent()) if model_mode() == "llm" else _direct_agent()
    a2a_app = to_a2a(
        root_agent,
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", str(DEFAULT_PORT))),
    )

    async def health(request):
        # Same contract as agents/serving.py: the agent reports its own brain
        # and model, because the coordinator cannot know them from its own
        # environment.
        return JSONResponse(
            {
                "status": "ok",
                "agent": AGENT_NAME,
                "brain": model_mode(),
                "model": model_id(),
                "degraded": degrade(),
            }
        )

    a2a_app.add_route("/health", health, methods=["GET"])
    return a2a_app


app = build()


def main() -> None:
    import uvicorn

    uvicorn.run(
        app,
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", str(DEFAULT_PORT))),
    )


if __name__ == "__main__":
    main()
