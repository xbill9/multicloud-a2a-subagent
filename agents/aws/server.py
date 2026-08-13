"""AWS leg: a Strands research agent on Bedrock, served over A2A.

Strands ships no A2A server integration, so this agent sits directly on the
``a2a-sdk`` reference routes. That makes it the control column of the interop
matrix: a cell that fails here is a client-side wire problem, because the
server is the protocol's own reference implementation.

    python -m agents.aws.server            # direct mode, no credentials
    RESEARCH_MODEL_MODE=llm python -m agents.aws.server   # Strands on Bedrock

Environment: ``PORT`` (10002), ``PUBLIC_URL``, ``RESEARCH_MODEL_MODE``, and the
model: ``RESEARCH_MODEL_AWS`` or ``BEDROCK_MODEL_ID``.

The model defaults to Nova micro, which is what the currency mesh
proved out on this account and is a *poor* default for drafting prose -- it was
chosen when the task was a two-field lookup. Set it to something larger before
reading anything into this column's scores, and record which model the numbers
came from, because the audit's per-model breakdown is only as honest as this
variable.
"""

import os

from agents.common import (
    AGENT_NAME,
    INSTRUCTION,
    direct_reply,
    model_mode,
    public_url,
    resolve_model,
    wrap_responder,
)
from agents.serving import CallbackExecutor, build_agent_card, build_app

DEFAULT_PORT = 10002
CLOUD = "aws"


def model_id() -> str:
    """Which Bedrock model this leg will actually use, or "none" without one.

    ``RESEARCH_MODEL_AWS`` or ``BEDROCK_MODEL_ID`` -- see ``resolve_model``.
    Note that ``deploy_aws.sh`` scopes the execution role's
    ``bedrock:InvokeModel`` to whatever it was told, so changing this without
    redeploying the role gets an `AccessDenied` naming the model, not a
    silently different answer.
    """
    return resolve_model(CLOUD, "us.amazon.nova-micro-v1:0")


def _strands_responder():
    """Native brain: Strands on Bedrock, writing the draft.

    No tools, for the reason given in the GCP agent: a search tool on one
    cloud and not the others turns the audit into a comparison of tool access.
    """
    from strands import Agent
    from strands.models import BedrockModel

    agent = Agent(
        model=BedrockModel(model_id=model_id()),
        system_prompt=INSTRUCTION,
    )

    async def respond(prompt: str) -> str:
        return str(await agent.invoke_async(prompt))

    return respond


def build() -> tuple:
    inner = _strands_responder() if model_mode() == "llm" else direct_reply
    responder = wrap_responder(inner, agent=CLOUD, model=model_id())
    card = build_agent_card(name=AGENT_NAME, url=public_url(DEFAULT_PORT), model=model_id())
    return build_app(CallbackExecutor(responder), card, model=model_id()), card


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
