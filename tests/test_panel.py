"""The panel view: what three clouds buy over any one of them.

Not a benchmark. The three models are deliberately unmatched -- a small fast
one, a mid one, a reasoning deployment -- so "which is best" is both
unanswerable and uninteresting. The testable claim is that the panel beats any
member, for two reasons that must not be added together: the best draft rotates,
and a cloud that produces nothing is an outage for anyone who committed to it.
"""

from datetime import UTC, datetime

from coordinator.models import (
    Draft,
    DraftVerdict,
    ResearchRequest,
    ResearchRun,
    Verdict,
)
from evaluations import panel


def run(scores: dict[str, float], *, participants=None, brain="llm") -> ResearchRun:
    parts = participants or sorted(scores)
    return ResearchRun(
        request=ResearchRequest(topic="solid-state batteries"),
        participants=parts,
        drafts=[
            Draft(
                source=name, cloud=name, brain=brain, title="t", body="b " * 40,
                observed_at=datetime.now(UTC), latency_ms=1.0,
            )
            for name in scores
        ],
        verdict=Verdict(
            winner=max(scores, key=scores.get) if scores else None,
            verdicts=[DraftVerdict(source=n, total=v) for n, v in scores.items()],
        ),
        elapsed_ms=1.0,
    )


def test_rotation_is_what_justifies_a_panel():
    """If one cloud won nearly every brief the honest recommendation would be
    to use that cloud. Rotation is the first thing to check."""
    summary = panel.summarise(
        [run({"aws": 20, "gcp": 18}), run({"aws": 17, "gcp": 21}), run({"aws": 19, "gcp": 15})]
    )

    assert summary["rotates"] is True
    assert "rotates between clouds" in panel.render(summary)


def test_a_dominant_cloud_is_reported_as_not_earning_the_panel():
    """The finding that would argue against this whole architecture, and it has
    to be able to come out."""
    summary = panel.summarise([run({"aws": 20, "gcp": 10}) for _ in range(4)])

    assert summary["rotates"] is False
    assert "not earning its cost" in panel.render(summary)


def test_regret_is_what_committing_to_one_cloud_would_have_cost():
    """Within-subjects: every cloud answered the same brief, so no separate
    control arm is needed."""
    summary = panel.summarise([run({"aws": 20, "gcp": 16}), run({"aws": 18, "gcp": 22})])

    by = {row["cloud"]: row for row in summary["clouds"]}
    # aws: 0 behind on run 1, 4 behind on run 2 -> mean 2.0
    assert by["aws"]["mean_regret"] == 2.0
    assert by["gcp"]["mean_regret"] == 2.0
    assert by["aws"]["worst_regret"] == 4.0


def test_a_cloud_that_answered_nothing_shows_as_unavailable():
    """The strongest column, and the one the rubric cannot weaken: it does not
    ask how good a draft was, only whether there was one.

    Measured on the deployed mesh: GCP produced nothing on 10 of 24 runs while
    the panel answered every time. For anyone who had committed to GCP alone
    that is a 42% outage; for the panel it was a degraded run.
    """
    summary = panel.summarise(
        [
            run({"aws": 20, "gcp": 18}, participants=["aws", "gcp"]),
            run({"aws": 19}, participants=["aws", "gcp"]),
            run({"aws": 21}, participants=["aws", "gcp"]),
        ]
    )

    by = {row["cloud"]: row for row in summary["clouds"]}
    assert by["gcp"]["invited"] == 3
    assert by["gcp"]["answered"] == 1
    assert by["gcp"]["availability"] == 1 / 3
    assert by["aws"]["availability"] == 1.0
    assert summary["panel_answered"] == 3, "the panel answered every brief"


def test_direct_runs_are_excluded():
    """Three identical canned drafts make the winner a latency tie-break.
    Folding those in would manufacture rotation out of scheduling noise."""
    summary = panel.summarise([run({"aws": 13, "gcp": 13}, brain="direct")])

    assert summary["runs"] == 0
    assert "no model-backed runs" in panel.render(summary)
