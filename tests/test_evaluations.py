"""The audit layer: what it records, and what it refuses to report.

Most of these test a *refusal*. The store and the report are the only parts of
this system whose output is a number someone might quote, so the properties
worth pinning down are the ones that stop a number being quoted before it means
anything.
"""

from datetime import UTC, datetime

from coordinator.models import (
    RUBRIC_DIMENSIONS,
    DimensionScore,
    Draft,
    DraftVerdict,
    ResearchRequest,
    ResearchRun,
    Verdict,
)
from evaluations.report import DEFAULT_MIN_RUNS, aggregate, render
from evaluations.store import load, record


def draft(source: str, *, model: str, brain: str = "llm", latency_ms: float = 100.0) -> Draft:
    return Draft(
        source=source,
        cloud=source,
        model=model,
        brain=brain,
        title="t",
        body="# t\n\nbody text here",
        observed_at=datetime.now(UTC),
        latency_ms=latency_ms,
    )


def verdict_for(scores: dict[str, float], winner: str, *, judge: str = "rubric") -> Verdict:
    verdicts = [
        DraftVerdict(
            source=source,
            scores=[
                DimensionScore(dimension=dimension, score=total / len(RUBRIC_DIMENSIONS))
                for dimension in RUBRIC_DIMENSIONS
            ],
            total=total,
            rank=rank,
        )
        for rank, (source, total) in enumerate(
            sorted(scores.items(), key=lambda kv: -kv[1]), start=1
        )
    ]
    return Verdict(winner=winner, verdicts=verdicts, judge=judge)


def run(
    *,
    scores: dict[str, float],
    winner: str,
    models: dict[str, str] | None = None,
    brain: str = "llm",
    judge: str = "rubric",
    failures: dict[str, str] | None = None,
) -> ResearchRun:
    models = models or {source: f"{source}-model" for source in scores}
    return ResearchRun(
        request=ResearchRequest(topic="a topic"),
        participants=list(scores),
        drafts=[draft(source, model=models[source], brain=brain) for source in scores],
        failures=failures or {},
        verdict=verdict_for(scores, winner, judge=judge),
        elapsed_ms=1.0,
    )


def stamped(runs):
    return [(datetime.now(UTC), item) for item in runs]


# --------------------------------------------------------------------------
# The store
# --------------------------------------------------------------------------


def test_a_run_round_trips_through_the_store(tmp_path):
    path = tmp_path / "runs.jsonl"
    record(run(scores={"gcp": 20.0, "aws": 10.0}, winner="gcp"), path=path)

    loaded = list(load(path))

    assert len(loaded) == 1
    _recorded_at, restored = loaded[0]
    assert restored.verdict.winner == "gcp"
    assert restored.drafts[0].model == "gcp-model"


def test_the_store_appends_rather_than_overwrites(tmp_path):
    path = tmp_path / "runs.jsonl"
    record(run(scores={"gcp": 20.0}, winner="gcp"), path=path)
    record(run(scores={"aws": 15.0}, winner="aws"), path=path)

    assert len(list(load(path))) == 2


def test_a_torn_final_line_does_not_destroy_the_history(tmp_path):
    """A killed job leaves a half-written line; losing all of them is worse."""
    path = tmp_path / "runs.jsonl"
    record(run(scores={"gcp": 20.0}, winner="gcp"), path=path)
    with path.open("a") as handle:
        handle.write('{"recorded_at": "2026-08-12T00:00:00+00:00", "run": {"req')

    assert len(list(load(path))) == 1


def test_a_missing_store_reads_as_empty(tmp_path):
    assert list(load(tmp_path / "absent.jsonl")) == []


# --------------------------------------------------------------------------
# The report's refusals
# --------------------------------------------------------------------------


def test_direct_brain_runs_are_excluded_entirely():
    """Canned text is identical on every cloud; averaging it manufactures a result."""
    audit = aggregate(stamped([run(scores={"gcp": 20.0, "aws": 20.0}, winner="gcp", brain="direct")]))

    assert audit.rows == []
    assert audit.direct_runs == 1
    assert "no model-backed runs" in render(audit)


def test_a_thin_row_is_withheld_rather_than_printed():
    audit = aggregate(stamped([run(scores={"gcp": 20.0, "aws": 10.0}, winner="gcp")]))
    rendered = render(audit, min_runs=DEFAULT_MIN_RUNS)

    assert "withheld" in rendered
    assert "100%" not in rendered


def test_a_row_with_enough_runs_reports_its_win_rate():
    runs = [run(scores={"gcp": 20.0, "aws": 10.0}, winner="gcp") for _ in range(5)]
    audit = aggregate(stamped(runs))
    rendered = render(audit, min_runs=5)

    gcp = next(row for row in audit.rows if row.cloud == "gcp")
    assert gcp.runs == 5
    assert gcp.wins == 5
    assert gcp.win_rate == 1.0
    # The word also appears in the caveat block, so assert on the row itself.
    row_line = next(line for line in rendered.splitlines() if line.startswith("gcp/"))
    assert "withheld" not in row_line
    assert "100%" in row_line


def test_a_win_inside_the_narrow_margin_counts_as_a_tie():
    """A 0.1-point edge repeated is not a 100% win rate."""
    runs = [run(scores={"gcp": 20.0, "aws": 19.9}, winner="gcp") for _ in range(5)]
    audit = aggregate(stamped(runs))

    gcp = next(row for row in audit.rows if row.cloud == "gcp")
    assert gcp.wins == 0
    assert gcp.ties == 5
    assert audit.narrow_wins == 5


def test_mixed_judges_are_flagged_as_incomparable():
    runs = [run(scores={"gcp": 20.0, "aws": 10.0}, winner="gcp") for _ in range(3)]
    runs += [
        run(scores={"gcp": 20.0, "aws": 10.0}, winner="gcp", judge="gemini-2.5-pro")
        for _ in range(3)
    ]
    audit = aggregate(stamped(runs))
    rendered = render(audit, min_runs=1)

    assert audit.mixed_judges
    assert "more than one judge" in rendered


def test_the_caveats_are_printed_even_when_nothing_is_wrong():
    """A caveat block that only appears on failure trains the reader to skip it."""
    runs = [run(scores={"gcp": 20.0, "aws": 10.0}, winner="gcp") for _ in range(5)]
    rendered = render(aggregate(stamped(runs)), min_runs=5)

    assert "not a general benchmark" in rendered
    assert "direct-brain drafts are excluded" in rendered


def test_per_dimension_means_are_reported():
    runs = [run(scores={"gcp": 25.0, "aws": 5.0}, winner="gcp") for _ in range(5)]
    audit = aggregate(stamped(runs))

    gcp = next(row for row in audit.rows if row.cloud == "gcp")
    for dimension in RUBRIC_DIMENSIONS:
        assert gcp.mean_dimension(dimension) == 5.0
    assert "coverage" in render(audit, min_runs=5)


def test_models_are_tracked_separately_within_one_cloud():
    """Changing BEDROCK_MODEL_ID must not silently pool two models into one row."""
    runs = [
        run(scores={"aws": 20.0}, winner="aws", models={"aws": "nova-micro"}),
        run(scores={"aws": 20.0}, winner="aws", models={"aws": "nova-pro"}),
    ]
    audit = aggregate(stamped(runs))

    assert {row.key for row in audit.rows} == {"aws/nova-micro", "aws/nova-pro"}
