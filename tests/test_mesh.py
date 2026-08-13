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
    mesh = ResearchMesh(
        [participant(name, delay_ms=120) for name in ("gcp", "aws", "azure")],
        timeout_seconds=5,
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

    mesh = ResearchMesh([participant("gcp")], judge=RecordingJudge())
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
