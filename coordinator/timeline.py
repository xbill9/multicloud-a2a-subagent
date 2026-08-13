"""One run, every call it made, in the order they happened.

The flow lanes on the page group by leg, which is the right shape for "what
does a leg do" and the wrong shape for the question this file answers: *did
the calls actually happen, what came back, and when*. Grouped by leg, three
concurrent legs look exactly like three sequential ones. Sorted by wall clock,
they do not -- and that is the single most useful thing this project has to
show, because "verified latency is max(legs) rather than their sum" is a claim
about scheduling that only a merged timeline can support.

Plain text on purpose. It is the one view that survives a terminal, a log
aggregator, a paste into an issue, and a screenshot, and it needs no browser
to reach a service that is deliberately not public.

    curl -s "$URL/api/timeline" -H "Authorization: Bearer $(gcloud auth print-identity-token)"
"""

from coordinator.models import ResearchRun, TraceStep

#: Right-hand column width for the bar. Small enough that a 25s AgentCore call
#: and a 200ms Cloud Run call fit on one screen at the same scale.
_BAR_WIDTH = 34

_PHASE_MARK = {"credential": "K", "discovery": "D", "invoke": "I"}


def _offset_ms(step: TraceStep, run: ResearchRun) -> float:
    if step.started_at is None:
        return 0.0
    return (step.started_at - run.started_at).total_seconds() * 1000


def _size(step: TraceStep) -> str:
    """What came back. `-` means the provider declared no content-length."""
    if step.bytes is None:
        return "-"
    if step.bytes < 1024:
        return f"{step.bytes}B"
    return f"{step.bytes / 1024:.1f}kB"


def _bar(offset_ms: float, elapsed_ms: float, span_ms: float) -> str:
    """A call's position and duration, to scale, against the whole run.

    Rendered even when a call is too short to fill a cell: a hop that rounds to
    zero width still happened, and a blank row reads as a call that did not.
    """
    if span_ms <= 0:
        return ""
    start = int(_BAR_WIDTH * offset_ms / span_ms)
    width = max(1, round(_BAR_WIDTH * elapsed_ms / span_ms))
    start = min(start, _BAR_WIDTH - 1)
    width = min(width, _BAR_WIDTH - start)
    return " " * start + "#" * width


def render(run: ResearchRun) -> str:
    steps: list[tuple[str, TraceStep]] = [
        (leg, step) for leg, leg_steps in run.traces.items() for step in leg_steps
    ]
    steps.sort(key=lambda pair: _offset_ms(pair[1], run))

    topic = run.request.topic
    header = [
        f'run  {run.run_id}  {run.started_at.isoformat(timespec="seconds")}  "{topic[:60]}"',
        f"     {len(run.participants)} leg(s): {', '.join(run.participants)}"
        f"   elapsed {run.elapsed_ms:.0f}ms",
        "",
    ]

    if not steps:
        header.append("  no HTTP calls were made -- every participant answered in process.")
        header.append("  There is nothing to prove here and the timeline says so rather")
        header.append("  than drawing an empty grid.")
        return "\n".join(header)

    span_ms = max(
        (_offset_ms(step, run) + step.elapsed_ms for _, step in steps),
        default=run.elapsed_ms,
    )
    # Judging happens after every leg, so leaving it out of the scale would
    # squash a slow model judge off the right-hand edge of the chart -- the
    # step most worth seeing, drawn as though it took no time.
    if (judged := run.verdict) is not None and judged.started_at is not None:
        judge_end = (
            judged.started_at - run.started_at
        ).total_seconds() * 1000 + judged.elapsed_ms
        span_ms = max(span_ms, judge_end)

    lines = [
        *header,
        f"  {'at':>8}  {'leg':<6} {'':1} {'host':<44} {'code':>4} {'took':>8} {'back':>7}",
        f"  {'-' * 8}  {'-' * 6} {'-'} {'-' * 44} {'-' * 4} {'-' * 8} {'-' * 7}",
    ]

    for leg, step in steps:
        offset = _offset_ms(step, run)
        target = f"{step.host}{step.path}"
        lines.append(
            f"  {f'+{offset:.0f}ms':>8}  {leg:<6} {_PHASE_MARK.get(step.phase, '?')} "
            f"{target[:44]:<44} {str(step.status or '-'):>4} "
            f"{f'{step.elapsed_ms:.0f}ms':>8} {_size(step):>7}"
            f"  |{_bar(offset, step.elapsed_ms, span_ms)}"
        )
        # The provider's own id for the call, on its own line so the columns
        # above stay narrow enough to read. This is the row someone else can
        # check: every other figure here is this process describing itself.
        if step.request_id:
            lines.append(f"  {'':>8}  {'':<6}   id {step.request_id}")
        if not step.ok and step.detail:
            lines.append(f"  {'':>8}  {'':<6}   -> {step.detail[:96]}")

    # Judging is not a wire row and is not filed as one -- see `Verdict`. It is
    # printed here anyway because it is the step that decides the answer, and a
    # timeline that ends at the last leg leaves the reader to assume the gap
    # between the slowest leg and `elapsed` was nothing in particular.
    if (verdict := run.verdict) is not None and verdict.started_at is not None:
        offset = (verdict.started_at - run.started_at).total_seconds() * 1000
        label = f"{verdict.judge} -> {verdict.winner or 'no winner'}"
        lines.append(
            f"  {f'+{offset:.0f}ms':>8}  {'judge':<6} J "
            f"{label[:44]:<44} {'-':>4} "
            f"{f'{verdict.elapsed_ms:.0f}ms':>8} {'-':>7}"
            f"  |{_bar(offset, verdict.elapsed_ms, span_ms)}"
        )

    lines += [
        "",
        "  K credential   D agent-card discovery   I A2A invocation   J judging",
        "  ids are the provider's own; K/D/I rows were observed on the wire, J was not",
        "",
    ]

    # The conclusion the timeline exists to support, computed rather than
    # asserted. If the legs really did overlap, the sum of their spans exceeds
    # the elapsed time; if they were serialised by some accident of the client
    # stack, it will not, and this line is where that shows up.
    per_leg_span = {}
    for leg, step in steps:
        offset = _offset_ms(step, run)
        low, high = per_leg_span.get(leg, (offset, offset + step.elapsed_ms))
        per_leg_span[leg] = (min(low, offset), max(high, offset + step.elapsed_ms))
    total = sum(high - low for low, high in per_leg_span.values())
    slowest = max((high - low for low, high in per_leg_span.values()), default=0.0)

    lines.append(
        f"  legs summed {total:.0f}ms, slowest leg {slowest:.0f}ms, run {run.elapsed_ms:.0f}ms"
    )
    if total > slowest * 1.2 and run.elapsed_ms < total:
        lines.append("  -> the legs overlapped: the run cost about the slowest, not the sum.")
    else:
        lines.append("  -> too close to call at this scale; the legs are too fast to separate.")

    for name, failure in run.failures.items():
        lines.append(f"  {name} failed: {failure}")

    return "\n".join(lines)


__all__ = ["render"]
