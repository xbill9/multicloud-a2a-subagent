"""Shared pieces of every cloud's native research agent.

Each cloud contributes its own *serving* stack and its own *model* -- those are
the two things the mesh is measuring -- but they all receive the same brief and
return the same shape of answer, and they all support two brains:

``RESEARCH_MODEL_MODE=direct``
    Return a canned draft assembled from the brief. No model, no credentials,
    no upstream. Lets the interop matrix isolate protocol behaviour: a failed
    cell is unambiguously the wire, never a model that refused the topic or an
    expired key.

``RESEARCH_MODEL_MODE=llm``
    Answer with the cloud's native model through its native agent framework.
    The real configuration, and the only one whose scores mean anything.

**`direct` mode is not an evaluation and must never be reported as one.** The
canned draft is the same text on all three clouds, so the judge will rank three
identical drafts and the audit will record a meaningless tie. That is why
`Draft.brain` travels with every draft and why `evaluations.report` refuses to
aggregate anything but `llm`.
"""

import logging
import os

from coordinator.models import ResearchRequest
from protocol.research import parse_brief, render_draft

#: The A2A card name every cloud advertises. Deliberately the same on all
#: three: the clients dial a card name, and a per-cloud name would mean three
#: client configurations for what is meant to be one interchangeable role.
#: Which cloud answered travels in the draft header instead.
AGENT_NAME = "research_agent"

DESCRIPTION = "An agent that writes a short, sourced research brief on a given topic"

INSTRUCTION = (
    "You are a research assistant. Given a topic, you write one short research "
    "brief and nothing else. "
    "Open with a single markdown H1 title line, then the body under markdown "
    "headings. "
    "Be specific: name organisations, people, systems and figures, give dates, "
    "and prefer a concrete number to a hedge. "
    "Where you are unsure, say so in one clause and move on -- do not pad, and "
    "do not invent a citation. "
    "If the topic is one you cannot write about, say why in one sentence "
    "instead of writing a brief about something adjacent."
)

log = logging.getLogger("agents")


def model_mode() -> str:
    """``direct`` for the credential-free protocol probe, ``llm`` for the real thing."""
    return os.getenv("RESEARCH_MODEL_MODE", "direct").strip().lower()


#: The vendor-native variable each cloud's SDK documentation names, per cloud.
#: Kept because they are what a reader looking at Bedrock or Foundry docs will
#: expect, and because `deploy_aws.sh` needs `BEDROCK_MODEL_ID` anyway to scope
#: the IAM policy to the one model.
NATIVE_MODEL_ENV = {
    "gcp": "GENAI_MODEL",
    "aws": "BEDROCK_MODEL_ID",
    "azure": "AZURE_AI_MODEL_DEPLOYMENT_NAME",
}


def resolve_model(cloud: str, default: str | None = None) -> str:
    """Which model this cloud's agent will run, in ``llm`` mode.

    Three names per cloud, in order: ``RESEARCH_MODEL_<CLOUD>``, then the
    vendor-native variable above, then the default. The uniform name exists
    because the audit's rows are keyed on ``cloud/model`` and changing the
    model is therefore how a comparison is *configured* -- and a knob spelled
    three different ways is one that gets set on two clouds out of three, which
    silently produces an audit comparing a changed model against two unchanged
    ones. The vendor-native spelling still wins over the default, so nothing
    that reads like the provider's own documentation stops working.

    Returns ``"none"`` outside ``llm`` mode, and that string is not cosmetic:
    it travels in the draft header into ``Draft.model``, where
    ``evaluations.report`` uses it to keep canned text out of a model's row.
    """
    if model_mode() != "llm":
        return "none"

    uniform = os.getenv(f"RESEARCH_MODEL_{cloud.upper()}", "").strip()
    if uniform:
        return uniform

    native = os.getenv(NATIVE_MODEL_ENV[cloud], "").strip()
    if native:
        return native

    if default is None:
        # Azure has no default: a Foundry deployment name is an account-local
        # string this repo cannot guess, and guessing would turn a missing
        # setting into a confusing 404 from the provider.
        raise RuntimeError(
            f"no model configured for {cloud}: set RESEARCH_MODEL_{cloud.upper()} "
            f"or {NATIVE_MODEL_ENV[cloud]}"
        )
    return default


def degrade() -> bool:
    """Whether this agent should deliberately return a poor draft.

    Fault injection, opt-in and loud. The currency mesh could make a cloud
    *wrong* by skewing its rate; a research mesh has no equivalent, because a
    differently-argued draft is not a wrong one. What it can do is make one
    cloud visibly phone it in, so the judge can be watched declining to pick it
    rather than asserted to.

    Never set in a measurement run -- ``./infra/demo.sh`` is what it is for.
    """
    if os.getenv("RESEARCH_DRAFT_DEGRADE", "").strip().lower() in ("1", "true", "yes"):
        log.warning(
            "RESEARCH_DRAFT_DEGRADE is set -- this agent is deliberately "
            "returning a low-quality draft. Not a valid measurement configuration."
        )
        return True
    return False


def canned_draft(request: ResearchRequest) -> str:
    """A deterministic draft assembled from the brief, with no model involved.

    Written to score middlingly on the deterministic rubric rather than well:
    it has structure and covers the questions, but its specificity is fixed and
    shallow. A canned draft that topped the rubric would make every `direct`
    run look like a model had won, which is precisely the confusion
    ``Draft.brain`` exists to prevent.
    """
    if degrade():
        # Padding, not a one-liner. A 13-word non-answer is rejected by
        # `parse_draft` as below MIN_DRAFT_WORDS and never reaches the judge,
        # which demonstrates the *parser* working and says nothing about the
        # judge. It is also not what phoning it in looks like: models do not
        # return thirteen words, they return four hundred words of nothing.
        # This has no headings, no figures and no citations, so it clears the
        # length floor and then scores near zero on three of five dimensions.
        return (
            f"{request.topic} is an interesting and complex area with many "
            f"considerations to weigh up. There are a number of different "
            f"perspectives on {request.topic}, and reasonable people disagree "
            f"about the best way forward. Various factors are involved, and "
            f"the situation continues to evolve in ways that are difficult to "
            f"predict with confidence. Some observers believe the trend will "
            f"continue, while others are more cautious. It is important to "
            f"consider the broader context as well as the specific details, "
            f"and to bear in mind that circumstances may change. Overall, "
            f"{request.topic} remains a topic worth watching closely, and "
            f"further developments are likely in due course as the landscape "
            f"matures and more information becomes available to everyone."
        )

    sections = [f"# {request.topic}", "", "## Summary", ""]
    sections.append(
        f"This brief covers {request.topic}. It was assembled without a model, "
        f"from the structure of the brief alone, and states no findings of its "
        f"own. Treat every claim below as a placeholder."
    )
    for index, question in enumerate(request.questions, start=1):
        sections += [
            "",
            f"## {index}. {question}",
            "",
            (
                f"No model answered this question. A response would address "
                f"{question.rstrip('?').lower()} in the context of "
                f"{request.topic}."
            ),
        ]
    sections += [
        "",
        "## Confidence",
        "",
        "None. This is canned text produced by the agent's `direct` brain, "
        "which exists so the A2A path can be exercised without credentials.",
    ]
    return "\n".join(sections)


async def direct_reply(prompt: str) -> str:
    """Answer a brief from the canned template, in the wire format."""
    request = parse_brief(prompt)
    if request is None:
        return (
            "I can only answer a research brief in the expected format. "
            "No topic was found in the request."
        )
    return canned_draft(request)


def wrap_responder(inner, *, agent: str, model: str):
    """Attach the serving header to whatever a responder produced.

    Applied on the server so the header is written by this repo's code rather
    than by the model. A model asked to emit its own metadata line gets it
    wrong often enough to matter, and a corrupted header would misattribute a
    draft in the audit -- the one error the audit cannot detect from the inside.
    """

    async def respond(prompt: str) -> str:
        body = await inner(prompt)
        return render_draft(body, agent=agent, model=model, brain=model_mode())

    return respond


def public_url(default_port: int) -> str:
    """The URL this agent advertises on its card.

    Deliberately explicit. An agent behind Cloud Run, AgentCore, or Container
    Apps is reached at a hostname it cannot infer from its own socket, and a
    card that advertises the bind address is unreachable to every remote client
    -- the first interop bug this project hit.
    """
    if url := os.getenv("PUBLIC_URL"):
        return url.rstrip("/")
    host = os.getenv("HOST", "127.0.0.1")
    port = os.getenv("PORT", str(default_port))
    return f"http://{host}:{port}"
