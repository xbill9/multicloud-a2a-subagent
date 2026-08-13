"""The fan-out: three clouds, one brief, and what happens when one fails."""

from coordinator.errors import AdapterError, FailureKind
from coordinator.judge import RubricJudge
from coordinator.local_adapters import CannedDraftAdapter
from coordinator.mesh import ResearchMesh
from coordinator.models import ResearchRequest
from coordinator.participants import Participant

GOOD = """\
# Title

## Section

QuantumScape shipped 12,000 cells in 2025, according to its Q4 filing.
Toyota targets 2027 (per its 2024 statement). See https://example.org/x.

- one point
- another point
"""

#: Padding, not a one-liner, and the difference matters. A 13-word non-answer
#: never reaches the judge -- `parse_draft` rejects it as below
#: MIN_DRAFT_WORDS -- so a test built on one would assert the parser's
#: behaviour while claiming to assert the judge's. This is the shape
#: `RESEARCH_DRAFT_DEGRADE` actually produces.
THIN = (
    "This is an interesting and complex area with many considerations to weigh "
    "up. There are a number of different perspectives, and reasonable people "
    "disagree about the best way forward. Various factors are involved and the "
    "situation continues to evolve in ways that are difficult to predict with "
    "confidence. It is important to consider the broader context as well as "
    "the specific details, and to bear in mind that circumstances may change "
    "as the landscape matures and more information becomes available."
)


def participant(name: str, body: str = GOOD, **kwargs) -> Participant:
    return Participant(
        name=name,
        source=CannedDraftAdapter(body, source=name, cloud=name, brain="llm", **kwargs),
        cloud=name,
    )


def request(**kwargs) -> ResearchRequest:
    return ResearchRequest(topic="solid-state batteries in 2026", **kwargs)


async def test_three_participants_each_return_a_draft():
    mesh = ResearchMesh([participant("gcp"), participant("aws"), participant("azure")])
    run = await mesh.run(request())

    assert run.succeeded
    assert run.complete
    assert run.failures == {}
    assert len(run.drafts) == 3
    assert run.verdict.winner in {"gcp", "aws", "azure"}
    assert len(run.verdict.verdicts) == 3


async def test_one_cloud_failing_degrades_to_the_rest():
    mesh = ResearchMesh(
        [
            participant("gcp"),
            participant("aws"),
            participant(
                "azure",
                failure=AdapterError(FailureKind.PROVIDER, "model unavailable"),
            ),
        ]
    )
    run = await mesh.run(request())

    assert run.succeeded
    assert not run.complete, "a run missing a participant is not a complete comparison"
    assert len(run.drafts) == 2
    assert "azure" in run.failures
    assert "provider" in run.failures["azure"]
    assert run.verdict.winner in {"gcp", "aws"}


async def test_every_cloud_failing_is_not_a_success():
    """Regression, carried over from the currency mesh.

    A verdict object exists whenever judging was attempted, so testing for one
    reported a totally failed run as green -- and the deployed coordinator's
    exit status is that boolean.
    """
    mesh = ResearchMesh(
        [
            participant("gcp", failure=AdapterError(FailureKind.TRANSPORT, "refused")),
            participant("aws", failure=AdapterError(FailureKind.TRANSPORT, "refused")),
        ]
    )
    run = await mesh.run(request())

    assert run.verdict is not None
    assert run.verdict.winner is None
    assert not run.succeeded
    assert not run.complete


async def test_a_slow_cloud_times_out_without_taking_the_run_with_it():
    mesh = ResearchMesh(
        [participant("gcp"), participant("aws", delay_ms=400)],
        timeout_seconds=0.1,
    )
    run = await mesh.run(request())

    assert run.succeeded
    assert [draft.source for draft in run.drafts] == ["gcp"]
    assert "timeout" in run.failures["aws"]


async def test_the_legs_are_concurrent_not_sequential():
    """Elapsed must track the slowest leg, not their sum.

    The currency mesh's headline claim was max(legs) rather than sum(legs), and
    it is worth keeping an assertion behind it: a refactor that awaits the
    participants in a loop still passes every other test in this file.
    """
    # `max_rounds=1` because this asserts the *fan-out* is concurrent, not
    # anything about the judge loop. Left on the default, three canned drafts
    # below the pass mark would each be sent back once before converging, and
    # the elapsed figure would be measuring two rounds of a thing this test is
    # not about.
    mesh = ResearchMesh(
        [participant(name, delay_ms=120) for name in ("gcp", "aws", "azure")],
        timeout_seconds=5,
        max_rounds=1,
    )
    run = await mesh.run(request())

    assert len(run.drafts) == 3
    assert run.elapsed_ms < 300, f"three 120ms legs took {run.elapsed_ms:.0f}ms; not concurrent"


async def test_a_degraded_cloud_is_ranked_last_rather_than_excluded():
    """Demo act 4, as an assertion.

    The claim is not that a bad draft is filtered out -- nothing filters it --
    but that the judge sees it and places it last.
    """
    mesh = ResearchMesh([participant("gcp"), participant("aws", THIN)])
    run = await mesh.run(request())

    assert run.verdict.ranking[-1] == "aws"
    assert run.verdict.winner == "gcp"


async def test_auth_modes_are_recorded_per_leg():
    participants = [
        Participant(
            name="gcp",
            source=CannedDraftAdapter(GOOD, source="gcp", cloud="gcp"),
            cloud="gcp",
            auth="google-id-token",
        ),
        Participant(
            name="aws",
            source=CannedDraftAdapter(GOOD, source="aws", cloud="aws"),
            cloud="aws",
            auth="aws-sigv4",
        ),
    ]
    run = await ResearchMesh(participants).run(request())

    assert run.auth_modes == {"gcp": "google-id-token", "aws": "aws-sigv4"}


async def test_an_empty_participant_list_is_rejected():
    try:
        ResearchMesh([])
    except ValueError as exc:
        assert "at least one participant" in str(exc)
    else:  # pragma: no cover - the constructor must raise
        raise AssertionError("an empty mesh should not be constructible")


async def test_the_judge_is_injectable():
    """The mesh must not hard-wire a judge; the audit depends on swapping it."""
    calls = []

    class RecordingJudge(RubricJudge):
        async def judge(self, request, drafts):
            calls.append(len(drafts))
            return await super().judge(request, drafts)

    mesh = ResearchMesh([participant("gcp")], judge=RecordingJudge(), max_rounds=1)
    await mesh.run(request())

    assert calls == [1]


# --------------------------------------------------------------------------
# Evidence: what the run can prove about itself afterwards
# --------------------------------------------------------------------------


async def test_every_run_carries_an_identifier():
    """Positions move. `/api/timeline?n=2` names a different run after the next
    one is recorded, so a log line and a stored row can only be tied together
    -- or to a provider's own record of the call -- by a string that does not."""
    mesh = ResearchMesh([participant("gcp")])
    first = await mesh.run(ResearchRequest(topic="solid-state batteries"))
    second = await mesh.run(ResearchRequest(topic="solid-state batteries"))

    assert first.run_id
    assert first.run_id != second.run_id


async def test_the_judge_step_is_timed_separately_from_the_legs():
    """Judging is the only step after the barrier. It is invisible in every
    per-leg figure and shows up in `elapsed_ms` as an unexplained gap -- which
    is tolerable while the rubric takes microseconds and misleading the moment
    a model takes the seat."""
    run = await ResearchMesh([participant("gcp"), participant("aws")]).run(
        ResearchRequest(topic="solid-state batteries")
    )

    assert run.verdict is not None
    assert run.verdict.started_at is not None
    assert run.verdict.started_at >= run.started_at
    assert run.verdict.elapsed_ms >= 0


async def test_judging_begins_only_after_every_leg_has_answered():
    """The barrier, asserted rather than assumed: a judge that started early
    would be ranking a partial field, and the timestamps are the only thing
    that would show it."""
    run = await ResearchMesh([participant("gcp"), participant("aws")]).run(
        ResearchRequest(topic="solid-state batteries")
    )

    assert run.verdict is not None
    for draft in run.drafts:
        assert run.verdict.started_at >= draft.observed_at


async def test_the_run_identifier_survives_the_store(tmp_path):
    from evaluations.store import load, record

    run = await ResearchMesh([participant("gcp")]).run(
        ResearchRequest(topic="solid-state batteries")
    )
    path = tmp_path / "runs.jsonl"
    record(run, path=path)

    (_recorded_at, restored), = list(load(path))
    assert restored.run_id == run.run_id
    assert restored.verdict.elapsed_ms == run.verdict.elapsed_ms


# --------------------------------------------------------------------------
# The judge loop
# --------------------------------------------------------------------------


class Improving:
    """A source that gets better each time it is sent back.

    The thing a canned adapter cannot be, and the loop is untestable without
    it: with a source that returns the same text forever, every assertion about
    "the rewrite scored higher" is really an assertion about the convergence
    guard.
    """

    def __init__(self, source: str, bodies: list[str]) -> None:
        self.source = source
        self._bodies = bodies
        self.prompts: list[str] = []
        self.revisions: list = []

    async def research(self, request, revision=None):
        from datetime import UTC, datetime

        from coordinator.models import Draft
        from protocol.research import extract_title

        index = min(len(self.revisions), len(self._bodies) - 1)
        if revision is not None:
            self.revisions.append(revision)
            index = min(len(self.revisions), len(self._bodies) - 1)
        body = self._bodies[index]
        return Draft(
            source=self.source,
            cloud=self.source,
            model="test",
            brain="llm",
            title=extract_title(body),
            body=body,
            observed_at=datetime.now(UTC),
            latency_ms=1.0,
            round=revision.round if revision is not None else 1,
        )


#: Scores 23.18 of 25 on the deterministic rubric, measured rather than
#: assumed. `GOOD` was used here first and scores 11.0 -- below the default
#: pass mark -- so every "the rewrite passed" assertion built on it was really
#: asserting that the loop runs out of rounds.
STRONG = """\
# Agent-to-agent protocols in 2026

## Adoption

The A2A specification reached v1.0 in 2025 and is now implemented by Google's
ADK, Microsoft's Agent Framework and AWS Bedrock AgentCore [1]. Linux
Foundation took stewardship in 2025 (per the project announcement), and the
registry listed 1,400 agents by March 2026 [2].

## Interop in practice

- Discovery is privileged separately from invocation on all three clouds [3]
- AgentCore strips the A2A-Version header, per the 2026 conformance notes [4]
- ADK's to_a2a() advertises the container bind address, according to issue 812

## Cost

Hosted agent runtimes answered in 18.8-25.1 s against 1.7-2.1 s for a plain
container, a 10x gap (source: cross-cloud rollup, 2026). See
https://example.org/a2a-rollup for the measured tables.
"""
#: Scores badly on evidence and structure -- no headings, no citations.
WEAK = (
    "There are many considerations here and reasonable people disagree about "
    "the best way forward. Various factors are involved and the situation "
    "continues to evolve in ways that are difficult to predict. Some observers "
    "believe the trend will continue while others are more cautious about it. "
    "It is important to consider the broader context as well as the specifics."
)


def loop_request() -> ResearchRequest:
    """The brief the loop tests use, with the budget STRONG was measured against.

    `request()` leaves `max_words` at 600, and STRONG is 118 words, so on that
    brief it loses most of the concision dimension and totals 16.22 -- under
    the default pass mark. The loop then never converges and every assertion
    about a rewrite passing is really an assertion about running out of rounds.
    At 300 it scores 23.18, which is what these tests mean by "passes".
    """
    return ResearchRequest(topic="agent-to-agent protocols", max_words=300)


async def test_a_failing_draft_is_sent_back_and_the_rewrite_is_used():
    source = Improving("aws", [WEAK, STRONG])
    # Explicit bar. On the default, whether this test passes depends on whether
    # a fixture happens to clear 18 of 25 -- which is a fact about the fixture,
    # not about the loop.
    mesh = ResearchMesh([Participant(name="aws", source=source, cloud="aws")], max_rounds=3)

    run = await mesh.run(loop_request())

    assert len(source.revisions) == 1, "the failing draft was never sent back"
    assert run.drafts[0].body == STRONG, "the rewrite did not replace the draft"
    assert run.drafts[0].round == 2


async def test_the_critique_carries_the_score_and_the_weakest_dimensions():
    """A rewrite prompt saying only "do better" produces a draft that is no
    better. What makes the loop work is telling the model *which* dimension and
    *how far* below."""
    source = Improving("aws", [WEAK, STRONG])
    await ResearchMesh([Participant(name="aws", source=source, cloud="aws")]).run(loop_request())

    revision = source.revisions[0]
    assert revision.previous == WEAK
    assert revision.round == 2
    assert revision.maximum == 25.0
    assert 0 <= revision.score < 18.0
    # The critique names dimensions and numbers, not adjectives.
    assert "evidence" in revision.critique or "structure" in revision.critique
    assert " of 5" in revision.critique


async def test_a_passing_draft_is_never_sent_back():
    """Rewriting something good routinely makes it worse. The loop lifts the
    floor; it must not churn the ceiling."""
    source = Improving("gcp", [STRONG, WEAK])
    run = await ResearchMesh(
        [Participant(name="gcp", source=source, cloud="gcp")], pass_mark=5.0
    ).run(loop_request())

    assert source.revisions == []
    assert run.drafts[0].body == STRONG
    assert run.drafts[0].round == 1


async def test_the_loop_stops_when_a_rewrite_does_not_improve():
    """The convergence guard. "Below the bar" is a standing condition, so
    without this the loop always runs to max_rounds -- paying for a model to
    fail at the same thing repeatedly."""
    source = Improving("aws", [WEAK, WEAK, WEAK])
    run = await ResearchMesh(
        [Participant(name="aws", source=source, cloud="aws")], max_rounds=6
    ).run(loop_request())

    # Asked once, saw no improvement, stopped. Not five more times.
    assert len(source.revisions) == 1
    assert run.round_count == 2


async def test_max_rounds_of_one_is_the_pre_loop_behaviour():
    """The control. Any claim that the loop improves anything is measured
    against this, so it has to stay reachable and stay exact."""
    source = Improving("aws", [WEAK, STRONG])
    run = await ResearchMesh(
        [Participant(name="aws", source=source, cloud="aws")], max_rounds=1
    ).run(loop_request())

    assert source.revisions == []
    assert run.drafts[0].body == WEAK
    assert run.round_count == 1


async def test_only_the_failing_cloud_is_asked_again():
    strong = Improving("gcp", [STRONG])
    weak = Improving("aws", [WEAK, STRONG])
    mesh = ResearchMesh(
        [
            Participant(name="gcp", source=strong, cloud="gcp"),
            Participant(name="aws", source=weak, cloud="aws"),
        ],
    )

    await mesh.run(loop_request())

    assert strong.revisions == [], "a passing cloud was asked to rewrite"
    assert len(weak.revisions) == 1


async def test_a_source_that_cannot_take_a_revision_is_left_alone():
    """Any A2A server can answer this brief -- that is the point of a standard
    protocol -- and one that never heard of this repo cannot be sent a
    critique. Calling it with one raises a TypeError that would be recorded as
    a failure on a leg that is working perfectly."""

    class LegacySource:
        async def research(self, request):
            return await CannedDraftAdapter(WEAK, source="azure", brain="llm").research(request)

    mesh = ResearchMesh(
        [Participant(name="azure", source=LegacySource(), cloud="azure")], max_rounds=3
    )
    run = await mesh.run(loop_request())

    assert run.failures == {}, f"a legacy source was broken by the loop: {run.failures}"
    assert len(run.drafts) == 1
    assert run.drafts[0].round == 1


async def test_a_leg_that_fails_its_rewrite_keeps_the_draft_it_had():
    """The loop must never be able to make a run worse. Losing a scored draft
    because its *improvement* timed out would do exactly that."""

    class FailsOnRevision(Improving):
        async def research(self, request, revision=None):
            if revision is not None:
                raise AdapterError(FailureKind.TIMEOUT, "the rewrite timed out")
            return await super().research(request)

    source = FailsOnRevision("aws", [WEAK])
    run = await ResearchMesh(
        [Participant(name="aws", source=source, cloud="aws")], max_rounds=3
    ).run(loop_request())

    assert len(run.drafts) == 1
    assert run.drafts[0].body == WEAK
    assert "aws" in run.failures


async def test_every_round_is_recorded_with_its_own_verdict():
    """The loop's evidence. A cloud that passed first time and one that needed
    three attempts are indistinguishable in a final score."""
    source = Improving("aws", [WEAK, STRONG])
    run = await ResearchMesh(
        [Participant(name="aws", source=source, cloud="aws")], max_rounds=3
    ).run(loop_request())

    assert run.round_count == 2
    assert len(run.rounds) == 2
    assert run.rounds[-1] is run.verdict or run.rounds[-1].winner == run.verdict.winner
    # The trajectory is the point: round 2 scored better than round 1.
    assert run.rounds[1].verdicts[0].total > run.rounds[0].verdicts[0].total
    assert run.rounds_used("aws") == 2
