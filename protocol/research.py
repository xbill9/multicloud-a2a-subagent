"""Prompt construction and draft parsing, shared by all three A2A client stacks.

Keeping these pure and transport-free is what makes the interop matrix
meaningful: when a cell fails, the failure is in that SDK's wire handling, not
in three subtly different parsers. It also keeps parsing testable without a
network or credentials.

**The wire format changed shape when the domain did, and it is worth saying
why.** The currency version asked three models to emit strict JSON and treated
any deviation as a protocol failure -- reasonable for a decimal, hopeless for
an essay, and it would have made "Gemini used a code fence" indistinguishable
from "AgentCore dropped a header". A draft is therefore just markdown, and the
only structured part is a one-line header this repo's own serving code writes
around the model's output:

    <!-- a2a-research agent=gcp model=gemini-2.5-flash brain=llm -->

That line is emitted by `render_draft` on the server, never by the model, so no
amount of model waywardness can corrupt it. It carries the two facts the audit
cannot reconstruct from the coordinator's side: which model actually answered,
and whether a model answered at all.
"""

import re
from datetime import datetime

from coordinator.errors import AdapterError, FailureKind
from coordinator.models import Draft, ResearchRequest

#: A reply shorter than this is not a draft. It is almost always a refusal, a
#: safety response, or a model answering the meta-question ("Sure, I can write
#: about that!") -- which is a provider outcome, not a wire failure, and the
#: matrix must not file it as one.
MIN_DRAFT_WORDS = 25

_HEADER_RE = re.compile(r"<!--\s*a2a-research\s+(?P<fields>[^>]*?)-->\s*", re.IGNORECASE)
_FIELD_RE = re.compile(r"(\w+)=(\S+)")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(?P<title>.+?)\s*#*\s*$", re.MULTILINE)

_PROMPT_TEMPLATE = """\
Write a short research brief on the following topic.

Topic: {topic}

{questions}Requirements:
- Open with a single markdown H1 title line, then the body.
- Use markdown headings and keep it under {max_words} words.
- Be specific: name things, give figures and dates where you know them, and
  say plainly when you do not know something rather than filling the space.
- Do not describe what you are about to do, and do not append a summary of
  your own process. Return the brief and nothing else.\
"""


def build_prompt(request: ResearchRequest) -> str:
    """The brief, identical for every cloud.

    Identical by construction rather than by convention: every participant is
    handed the string this function returns, so the audit's central claim --
    that the model is the only thing that varied -- is a property of the code
    rather than an assurance.
    """
    questions = ""
    if request.questions:
        listed = "\n".join(f"- {question}" for question in request.questions)
        questions = f"Answer each of these explicitly:\n{listed}\n\n"
    return _PROMPT_TEMPLATE.format(
        topic=request.topic,
        questions=questions,
        max_words=request.max_words,
    )


def parse_brief(text: str) -> ResearchRequest | None:
    """Recover the structured brief from the prompt template.

    Only used by `direct` mode, where there is no model to do the reading.
    Both sides of this prompt live in this repo, so a strict template match is
    appropriate; anything else returns None and the agent declines rather than
    inventing a topic.
    """
    topic_match = re.search(r"^Topic:\s*(?P<topic>.+?)\s*$", text, re.MULTILINE)
    if not topic_match:
        return None

    questions = re.findall(r"^-\s+(?P<question>.+?)\s*$", text, re.MULTILINE)
    words_match = re.search(r"under\s+(?P<words>\d+)\s+words", text)

    # The template's own "Requirements:" bullets are dashes too, and they sit
    # after the questions block. Cutting at the requirements header is what
    # keeps "Open with a single markdown H1 title line" from being scored as a
    # research question the agent failed to answer.
    requirements_at = text.find("Requirements:")
    if requirements_at != -1:
        before = text[:requirements_at]
        questions = re.findall(r"^-\s+(?P<question>.+?)\s*$", before, re.MULTILINE)

    try:
        return ResearchRequest(
            topic=topic_match.group("topic"),
            questions=questions,
            max_words=int(words_match.group("words")) if words_match else 600,
        )
    except ValueError:
        return None


def render_draft(body: str, *, agent: str, model: str, brain: str) -> str:
    """Wrap an agent's output in the header the coordinator reads back.

    Called on the *serving* side, around whatever the model produced. Written
    as an HTML comment so it survives every markdown renderer a draft might
    later pass through, and so a human reading the raw reply sees the draft
    rather than a metadata banner.
    """
    return f"<!-- a2a-research agent={agent} model={model} brain={brain} -->\n{body.lstrip()}"


def parse_header(text: str) -> tuple[dict[str, str], str]:
    """Split the serving header off the draft body.

    A missing header is not an error. Any A2A server can answer this brief --
    that is rather the point of using a standard protocol -- and one that does
    not run this repo's serving code simply reports "unknown" for the fields it
    could not have known to send.
    """
    match = _HEADER_RE.match(text.lstrip())
    if not match:
        return {}, text.strip()
    fields = dict(_FIELD_RE.findall(match.group("fields")))
    return fields, text.lstrip()[match.end() :].strip()


def extract_title(body: str) -> str:
    """The draft's H1, or its opening line if it did not use one.

    Deliberately forgiving. A missing title is a style miss, not a failed call,
    and three model families will not agree on markdown conventions; the
    `structure` rubric dimension is where that gets scored, not here.
    """
    heading = _HEADING_RE.search(body)
    if heading:
        return heading.group("title").strip()[:200]
    for line in body.splitlines():
        if stripped := line.strip():
            return stripped.lstrip("#").strip()[:200]
    return "untitled"


def parse_draft(
    text: str,
    request: ResearchRequest,
    *,
    source: str,
    cloud: str,
    latency_ms: float,
    observed_at: datetime,
) -> Draft:
    """Map one agent's reply onto a Draft, or say which layer failed.

    The two failure kinds here are deliberately different and the distinction
    cost real time in the predecessor series: an empty reply means the wire
    carried nothing and is a `protocol` fault, while a short one means the
    remote answered and declined, which is a `provider` fault. Filing the
    second as the first turns "Bedrock refused the topic" into "AgentCore broke
    A2A", and that is the one kind of wrong answer this instrument must never
    give.
    """
    if not text or not text.strip():
        raise AdapterError(
            FailureKind.PROTOCOL,
            f"{source} returned an empty reply; no draft on the wire",
        )

    fields, body = parse_header(text)
    if not body:
        raise AdapterError(
            FailureKind.PROTOCOL,
            f"{source} returned a header with no draft behind it",
        )

    words = len(body.split())
    if words < MIN_DRAFT_WORDS:
        raise AdapterError(
            FailureKind.PROVIDER,
            f"{source} answered with {words} words, below the {MIN_DRAFT_WORDS} "
            f"needed to count as a draft: {body[:200]!r}",
        )

    return Draft(
        source=source,
        cloud=fields.get("agent", cloud),
        model=fields.get("model", "unknown"),
        brain=fields.get("brain", "unknown"),
        title=extract_title(body),
        body=body,
        observed_at=observed_at,
        latency_ms=latency_ms,
    )
