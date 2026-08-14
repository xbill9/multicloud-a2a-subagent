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

Environment: ``PORT`` (10001), ``RESEARCH_MODEL_MODE``, and the model:
``RESEARCH_MODEL_GCP`` or ``GENAI_MODEL`` (default ``gemini-2.5-flash``).
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
    resolve_model,
    wrap_responder,
)
from protocol.research import render_draft
from protocol.search import (
    search_count,
    search_enabled,
    search_summary,
    web_search,
)
from protocol.telemetry import (
    instrument_app,
    telemetry_summary,
)
from protocol.telemetry import (
    setup as setup_telemetry,
)

logging.basicConfig(format="[%(levelname)s]: %(message)s", level=logging.INFO)

DEFAULT_PORT = 10001
CLOUD = "gcp"

# Before anything builds an agent: the instrumented httpx client has to be in
# place before a vendor SDK constructs its own, or that SDK's calls are
# invisible to the trace.
setup_telemetry("research-" + CLOUD)


def model_id() -> str:
    """Which Gemini this leg will actually use, or "none" with no model at all.

    ``RESEARCH_MODEL_GCP`` or ``GENAI_MODEL`` -- see ``resolve_model``.
    """
    return resolve_model(CLOUD, "gemini-2.5-flash")


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
    """Native brain: Gemini writing the draft through ADK, with search.

    The tool is ``protocol.search.web_search``, the same function the other two
    clouds get, and *not* ADK's built-in ``google_search``. Using each vendor's
    own search would make this row Gemini-grounded-against-Google and the AWS
    row Bedrock-against-nothing, and the audit would report the gap between two
    retrieval products as a gap between two models.

    What is still native here is the part worth measuring: ADK wraps the plain
    callable itself and runs its own tool-calling loop, which is a different
    implementation from Strands' and from Agent Framework's.
    """
    from google.adk.agents import LlmAgent

    return LlmAgent(
        model=model_id(),
        name=f"{AGENT_NAME}_gemini",
        description=DESCRIPTION,
        instruction=INSTRUCTION,
        tools=[web_search] if search_enabled() else [],
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
    from the inside.

    **Only the final response is kept, and that became load-bearing the moment
    this agent got a tool.** The previous version concatenated the text of every
    event in the stream, which was correct while the stream held exactly one --
    and its docstring said so, in as many words, as a warning to whoever added
    tools. With ``web_search`` attached the stream also carries the model's
    own commentary around each tool call ("Let me look that up", a summary of
    what it found), and concatenating those produced a "draft" that opened with
    the model narrating its research. The rubric would then have scored the
    narration: structure, concision and coverage all read the whole body.
    """
    from google.adk.agents import BaseAgent
    from google.adk.events import Event
    from google.genai import types

    class HeaderStamped(BaseAgent):
        async def _run_async_impl(self, ctx) -> AsyncGenerator:
            # Delta across this one run, for the reason in `wrap_responder`:
            # the counter is per process and Cloud Run runs many.
            before = search_count()
            texts: list[str] = []
            async for event in inner.run_async(ctx):
                # Function calls and their results have no text and are skipped
                # by the `part.text` filter anyway; what has to be excluded
                # explicitly is the *model text* that accompanies them.
                if not event.is_final_response():
                    continue
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
                                body,
                                agent=CLOUD,
                                model=model_id(),
                                brain=model_mode(),
                                searches=search_count() - before,
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
                # Whether this agent researched or recalled, and how
                # often it looked. The coordinator cannot see a
                # researcher's outbound search calls -- they happen on
                # another cloud, outside any trace it opened -- so this
                # is the only place that fact is observable.
                "search": search_summary(),
                "telemetry": telemetry_summary(),
            }
        )

    a2a_app.add_route("/health", health, methods=["GET"])
    instrument_app(a2a_app)
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
