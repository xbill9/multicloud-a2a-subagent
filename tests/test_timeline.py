"""The plain-text timeline: did the calls happen, what came back, and when."""

from datetime import UTC, datetime, timedelta

from coordinator.models import ResearchRequest, ResearchRun, TraceStep
from coordinator.timeline import render

START = datetime(2026, 8, 12, 18, 30, 0, tzinfo=UTC)


def step(offset_ms: float, elapsed_ms: float, **kwargs) -> TraceStep:
    return TraceStep(
        phase=kwargs.pop("phase", "invoke"),
        label=kwargs.pop("label", "POST /"),
        host=kwargs.pop("host", "agent.example.com"),
        path=kwargs.pop("path", "/"),
        status=kwargs.pop("status", 200),
        started_at=START + timedelta(milliseconds=offset_ms),
        elapsed_ms=elapsed_ms,
        **kwargs,
    )


def run(traces, *, elapsed_ms=1000.0, failures=None) -> ResearchRun:
    return ResearchRun(
        request=ResearchRequest(topic="solid-state batteries in 2026"),
        started_at=START,
        participants=list(traces),
        traces=traces,
        failures=failures or {},
        elapsed_ms=elapsed_ms,
    )


def test_calls_are_ordered_by_wall_clock_across_legs():
    """The point of the view. Grouped by leg, three concurrent legs look
    exactly like three sequential ones; interleaved, they do not."""
    text = render(
        run(
            {
                "gcp": [step(0, 50), step(400, 100)],
                "aws": [step(10, 30), step(60, 900)],
            }
        )
    )

    rows = [line for line in text.splitlines() if line.strip().startswith("+")]
    offsets = [int(row.split("ms")[0].strip().lstrip("+")) for row in rows]

    assert offsets == sorted(offsets)
    assert offsets == [0, 10, 60, 400]


def test_each_row_names_the_leg_the_host_and_what_came_back():
    text = render(run({"aws": [step(0, 42, host="bedrock-agentcore.us-west-2.amazonaws.com",
                                   bytes=2048)]}))

    assert "aws" in text
    assert "bedrock-agentcore.us-west-2.amazonaws.com" in text
    assert "42ms" in text
    assert "2.0kB" in text


def test_an_undeclared_content_length_reads_as_unknown_not_zero():
    """A chunked response with no content-length and an empty response are
    different answers to "what came back"."""
    text = render(run({"gcp": [step(0, 10, bytes=None)]}))

    rows = [line for line in text.splitlines() if line.strip().startswith("+")]
    assert rows[0].rstrip().split("|")[0].split()[-1] == "-"


def test_a_zero_byte_response_is_not_rendered_as_unknown():
    text = render(run({"gcp": [step(0, 10, bytes=0)]}))

    rows = [line for line in text.splitlines() if line.strip().startswith("+")]
    assert "0B" in rows[0]


def test_overlapping_legs_are_reported_as_overlapping():
    """Computed from the trace rather than asserted. This is the project's
    headline latency claim and the timeline is what backs it."""
    text = render(
        run(
            {
                "gcp": [step(0, 900)],
                "aws": [step(5, 950)],
                "azure": [step(10, 880)],
            },
            elapsed_ms=980,
        )
    )

    assert "the legs overlapped" in text
    assert "not the sum" in text


def test_legs_too_fast_to_separate_are_not_claimed_as_overlapping():
    text = render(run({"gcp": [step(0, 2)], "aws": [step(1, 2)]}, elapsed_ms=6))

    assert "the legs overlapped" not in text
    assert "too close to call" in text


def test_a_run_that_crossed_no_network_says_so_rather_than_drawing_a_grid():
    text = render(run({}, elapsed_ms=12))

    assert "no HTTP calls were made" in text
    assert "+" not in text.split("elapsed")[-1]


def test_a_failed_hop_carries_the_providers_own_words():
    text = render(
        run(
            {
                "aws": [
                    step(
                        0,
                        120,
                        phase="credential",
                        host="sts.amazonaws.com",
                        status=403,
                        ok=False,
                        detail="Not authorized to perform sts:AssumeRoleWithWebIdentity",
                    )
                ]
            },
            failures={"aws": "authentication: A2A endpoint returned 403"},
        )
    )

    assert "403" in text
    assert "Not authorized to perform" in text
    assert "aws failed:" in text


def test_the_phase_of_every_hop_is_marked_and_the_legend_explains_it():
    text = render(
        run(
            {
                "aws": [
                    step(0, 10, phase="credential", host="sts.amazonaws.com"),
                    step(20, 10, phase="discovery"),
                    step(40, 10, phase="invoke"),
                ]
            }
        )
    )

    assert "K credential" in text
    assert "D agent-card discovery" in text
    assert "I A2A invocation" in text


def test_a_hop_too_short_to_scale_still_draws_a_mark():
    """A blank row reads as a call that did not happen, which is the one thing
    this view must never say about a call that did."""
    text = render(run({"gcp": [step(0, 5000), step(4000, 1)]}, elapsed_ms=5000))

    rows = [line for line in text.splitlines() if line.strip().startswith("+")]
    assert all("#" in row for row in rows)


def test_the_header_carries_the_brief_and_the_legs():
    text = render(run({"gcp": [step(0, 5)], "aws": [step(0, 5)]}, elapsed_ms=77))

    assert "solid-state batteries in 2026" in text
    assert "2 leg(s): gcp, aws" in text
    assert "elapsed 77ms" in text


def test_a_step_with_no_timestamp_does_not_crash_the_render():
    """Old runs in the store predate `started_at`, and the audit is
    append-only -- a report that cannot read its own history is not a report."""
    naked = TraceStep(phase="invoke", label="POST /", host="a.example.com", elapsed_ms=10)

    text = render(run({"gcp": [naked]}))

    assert "a.example.com" in text
