"""The wire format: the brief out, the draft back.

Both halves live in this repo, so these tests are checking a contract with
itself -- but it is the contract three separately-written agents and three
separately-written clients all depend on, and it is where the currency version
of this code was most fragile.
"""

from datetime import UTC, datetime

import pytest

from coordinator.errors import AdapterError, FailureKind
from coordinator.models import ResearchRequest
from protocol.research import (
    MIN_DRAFT_WORDS,
    build_prompt,
    extract_title,
    parse_brief,
    parse_draft,
    parse_header,
    render_draft,
)

TOPIC = "solid-state battery manufacturing"


def request(**kwargs) -> ResearchRequest:
    return ResearchRequest(topic=TOPIC, **kwargs)


def body(words: int = 60) -> str:
    return "# A Title\n\n## Section\n\n" + " ".join(f"word{n}" for n in range(words))


def parse(text: str, *, source: str = "gcp", req: ResearchRequest | None = None):
    return parse_draft(
        text,
        req or request(),
        source=source,
        cloud=source,
        latency_ms=1.0,
        observed_at=datetime.now(UTC),
    )


# --------------------------------------------------------------------------
# The brief
# --------------------------------------------------------------------------


def test_the_prompt_carries_the_topic_and_the_budget():
    prompt = build_prompt(request(max_words=250))

    assert TOPIC in prompt
    assert "250 words" in prompt


def test_focus_questions_are_asked_explicitly():
    prompt = build_prompt(request(questions=["who ships at scale?", "what is the cost floor?"]))

    assert "who ships at scale?" in prompt
    assert "what is the cost floor?" in prompt


def test_the_brief_round_trips_through_the_prompt():
    """direct mode reads the prompt back; the two halves must agree."""
    original = request(questions=["who ships at scale?"], max_words=400)
    recovered = parse_brief(build_prompt(original))

    assert recovered is not None
    assert recovered.topic == original.topic
    assert recovered.questions == original.questions
    assert recovered.max_words == original.max_words


def test_the_templates_own_bullets_are_not_read_as_questions():
    """Regression: the Requirements block is dashes too.

    Without the cut at "Requirements:", "Open with a single markdown H1 title
    line" was recovered as a research question -- which direct mode would then
    dutifully write a section about, and the coverage score would grade.
    """
    recovered = parse_brief(build_prompt(request()))

    assert recovered is not None
    assert recovered.questions == []


def test_an_unrecognisable_prompt_yields_no_brief():
    assert parse_brief("please write me something nice") is None


# --------------------------------------------------------------------------
# The draft
# --------------------------------------------------------------------------


def test_the_serving_header_round_trips():
    text = render_draft(body(), agent="aws", model="nova", brain="llm")
    fields, remainder = parse_header(text)

    assert fields == {"agent": "aws", "model": "nova", "brain": "llm", "searches": "0"}
    assert remainder.startswith("# A Title")


def test_a_draft_carries_the_model_that_wrote_it():
    draft = parse(render_draft(body(), agent="aws", model="nova-micro", brain="llm"))

    assert draft.model == "nova-micro"
    assert draft.brain == "llm"
    assert draft.cloud == "aws"


def test_a_draft_without_a_header_is_still_a_draft():
    """Any A2A server can answer this brief; only ours sends the header."""
    draft = parse(body())

    assert draft.model == "unknown"
    assert draft.brain == "unknown"
    assert draft.title == "A Title"


def test_the_title_comes_from_the_h1():
    assert extract_title("# Solid-state at scale\n\nbody") == "Solid-state at scale"


def test_a_draft_with_no_heading_falls_back_to_its_first_line():
    assert extract_title("Solid-state at scale\n\nmore") == "Solid-state at scale"


def test_an_empty_reply_is_a_protocol_failure():
    with pytest.raises(AdapterError) as exc:
        parse("")

    assert exc.value.kind is FailureKind.PROTOCOL


def test_a_header_with_nothing_behind_it_is_a_protocol_failure():
    with pytest.raises(AdapterError) as exc:
        parse(render_draft("", agent="gcp", model="gemini", brain="llm"))

    assert exc.value.kind is FailureKind.PROTOCOL


def test_a_refusal_is_a_provider_failure_not_a_protocol_one():
    """The distinction the matrix depends on.

    "Bedrock declined the topic" and "AgentCore broke A2A" must never land in
    the same bucket: one is a fact about a model, the other is an interop
    finding, and filing the first as the second is how this instrument would
    manufacture a defect that does not exist.
    """
    with pytest.raises(AdapterError) as exc:
        parse("I cannot help with that topic.")

    assert exc.value.kind is FailureKind.PROVIDER
    assert str(MIN_DRAFT_WORDS) in str(exc.value)


def test_word_count_ignores_the_header():
    draft = parse(render_draft(body(words=50), agent="gcp", model="gemini", brain="llm"))

    # 50 filler words plus the heading tokens "#", "A", "Title", "##",
    # "Section" -- word_count is str.split(), so markdown punctuation counts.
    # Stated rather than tidied away: `concision` scores against this number.
    assert draft.word_count == 55


def test_the_search_count_travels_with_the_draft():
    """Per draft, not per process.

    The agents also expose a search counter on /health, and it is useless for
    this: it accumulates across every request the container ever served, and
    both AgentCore and Cloud Run run many containers -- so a health check
    answered by a cold instance reports 0 for a draft that was thoroughly
    researched. Measured live on 2026-08-13: the GCP researcher reported
    `searches: 0` immediately after producing a draft scoring 5.0 on evidence.
    """
    text = render_draft("# T\n\n" + "word " * 40, agent="gcp", model="g", brain="llm", searches=7)
    draft = parse_draft(
        text,
        ResearchRequest(topic="solid-state batteries"),
        source="gcp",
        cloud="gcp",
        latency_ms=1.0,
        observed_at=datetime.now(UTC),
    )
    assert draft.searches == 7


def test_a_draft_from_an_agent_that_reports_no_count_is_not_reported_as_zero():
    """-1 and 0 are different claims. Zero says the draft was written without
    looking anything up, which is exactly the finding worth catching; absent
    says the agent never told us, which is what any third-party A2A server
    that never heard of this repo will do."""
    draft = parse_draft(
        "<!-- a2a-research agent=x model=y brain=llm -->\n# T\n\n" + "word " * 40,
        ResearchRequest(topic="solid-state batteries"),
        source="x",
        cloud="x",
        latency_ms=1.0,
        observed_at=datetime.now(UTC),
    )
    assert draft.searches == -1
