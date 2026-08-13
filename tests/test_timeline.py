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


# --------------------------------------------------------------------------
# Evidence a reader outside this process can check
# --------------------------------------------------------------------------


def test_run_id_is_in_the_header():
    """Every other handle on a run is positional. `/api/timeline?n=2` counts
    backwards from the end of the store, so the same URL names a different run
    after the next one is recorded -- and a log line, a stored row and a
    provider's own record can only be tied together by a string that does not
    move."""
    rendered = render(run({"gcp": [step(0, 10)]}))
    run_id = run({"gcp": [step(0, 10)]}).run_id
    assert rendered.splitlines()[0].startswith("run  ")
    # Two runs constructed separately must not share an id.
    assert run_id != run({"gcp": [step(0, 10)]}).run_id


def test_provider_request_id_is_rendered_when_the_provider_sent_one():
    text = render(run({"aws": [step(0, 40, request_id="abc-123-def")]}))
    assert "id abc-123-def" in text


def test_no_request_id_line_when_the_provider_sent_none():
    """Absence is reported by saying nothing, not by printing an empty id. A
    blank id row would read as a call that has one and lost it."""
    text = render(run({"aws": [step(0, 40)]}))
    assert "id " not in text


def test_the_judge_is_on_the_timeline_with_its_own_timing():
    """Judging is the only step after the barrier, so it is invisible in every
    per-leg figure and shows up in `elapsed` as an unexplained gap."""
    from coordinator.models import Verdict

    judged = run({"gcp": [step(0, 100)]}, elapsed_ms=900.0)
    judged.verdict = Verdict(
        judge="gemini-2.5-pro",
        winner="gcp",
        started_at=START + timedelta(milliseconds=120),
        elapsed_ms=700.0,
    )
    text = render(judged)
    assert "judge" in text
    assert "gemini-2.5-pro -> gcp" in text
    assert "+120ms" in text
    assert "700ms" in text


def test_the_judge_row_is_marked_as_not_observed_on_the_wire():
    """The rubric judge makes no HTTP call and the model judge's calls happen
    inside ADK's transport, where this process's hooks cannot see them. The
    row is printed because it decides the answer; the legend says what it is
    not, so a `J` is never read as a round trip that was measured."""
    from coordinator.models import Verdict

    judged = run({"gcp": [step(0, 100)]})
    judged.verdict = Verdict(judge="rubric", winner="gcp", started_at=START, elapsed_ms=1.0)
    text = render(judged)
    assert "J judging" in text
    assert "was not" in text


def test_a_slow_judge_does_not_fall_off_the_right_hand_edge():
    """The bar scale is computed from the wire steps, which all finish before
    judging begins. Left out, a judge slower than the whole fan-out would be
    drawn as though it took no time -- the step most worth seeing, erased by
    the chart's own axis."""
    from coordinator.models import Verdict

    judged = run({"gcp": [step(0, 50)]}, elapsed_ms=5000.0)
    judged.verdict = Verdict(
        judge="gemini-2.5-pro",
        winner="gcp",
        started_at=START + timedelta(milliseconds=60),
        elapsed_ms=4000.0,
    )
    lines = [line for line in render(judged).splitlines() if "judge  J " in line]
    assert len(lines) == 1
    # The judge took 80% of the span, so its bar must be substantial rather
    # than the single clamped cell a too-small span would give it.
    assert lines[0].count("#") > 10


def test_the_overlap_conclusion_ignores_the_judge():
    """The legs-overlapped line is a claim about the fan-out. Judging runs
    strictly after it, so folding it in would inflate the summed span and let
    a slow judge argue the legs were concurrent when they were not."""
    from coordinator.models import Verdict

    judged = run(
        {"gcp": [step(0, 400)], "aws": [step(10, 400)], "azure": [step(20, 400)]},
        elapsed_ms=500.0,
    )
    judged.verdict = Verdict(
        judge="rubric", winner="gcp", started_at=START + timedelta(milliseconds=430),
        elapsed_ms=9000.0,
    )
    text = render(judged)
    assert "legs summed 1200ms" in text
