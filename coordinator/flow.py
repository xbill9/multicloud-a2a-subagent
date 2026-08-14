"""Shape one run for the debug view: what happened, to whom, and why.

The page could compute most of this in JavaScript from ``/api/last``, and the
one thing it must not is the reason this module exists. **The critique is
rebuilt with ``judge.critique_for`` -- the same function the mesh actually
called when it sent a draft back.** A second implementation in the browser
would drift from the first, and the drift would be invisible: the page would
show a plausible critique that no agent ever received, which is precisely the
kind of confident fiction this project spends its effort avoiding.

Everything here is derived from a stored ``ResearchRun``. Nothing is recomputed
from the drafts, nothing is inferred, and where a fact was never recorded the
field says so rather than guessing -- ``searches`` is ``-1`` for an agent that
did not report one, and the view renders that as "not reported" rather than as
zero.
"""

from coordinator.judge import critique_for, needs_revision
from coordinator.models import MAX_TOTAL, RUBRIC_DIMENSIONS, ResearchRun


def _draft_view(run: ResearchRun, source: str) -> dict | None:
    for draft in run.drafts:
        if draft.source == source:
            return {
                "source": draft.source,
                "cloud": draft.cloud,
                "model": draft.model,
                "brain": draft.brain,
                "title": draft.title,
                "body": draft.body,
                "words": draft.word_count,
                "latency_ms": draft.latency_ms,
                "round": draft.round,
                # -1 means the agent did not report a count. Kept distinct from
                # 0, which is a finding: a draft written without looking
                # anything up.
                "searches": draft.searches,
            }
    return None


def _reviews(run: ResearchRun) -> list[dict]:
    """Every round's verdict, with the critique each cloud was actually sent.

    A draft that scored below the bar in round N was sent back with
    ``critique_for(entry)``; one that passed was not sent anything. Both are
    recorded here, because "this cloud was told nothing because it passed" and
    "this cloud was told nothing because the loop had run out of rounds" look
    identical in a score table and are different facts.
    """
    rounds = run.rounds or ([run.verdict] if run.verdict else [])
    failing_by_round = []
    for verdict in rounds:
        failing_by_round.append({entry.source for entry in needs_revision(verdict)})

    last_index = len(rounds) - 1
    reviews = []
    for index, verdict in enumerate(rounds):
        entries = []
        for entry in sorted(verdict.verdicts, key=lambda e: e.rank or 99):
            below = entry.source in failing_by_round[index]
            # The critique only travelled if there was a later round to carry
            # it. On the final round nothing was sent back, however low the
            # score, and saying otherwise would invent a message.
            sent = below and index < last_index
            entries.append(
                {
                    "source": entry.source,
                    "rank": entry.rank,
                    "total": entry.total,
                    "max": MAX_TOTAL,
                    "notes": entry.notes,
                    "scores": [
                        {
                            "dimension": score.dimension,
                            "score": score.score,
                            "rationale": score.rationale,
                        }
                        for score in entry.scores
                    ],
                    "below_pass_mark": below,
                    "critique_sent": sent,
                    "critique": critique_for(entry) if sent else "",
                }
            )
        reviews.append(
            {
                "round": index + 1,
                "judge": verdict.judge,
                "winner": verdict.winner,
                "blind": verdict.blind,
                "rationale": verdict.rationale,
                "warnings": verdict.warnings,
                "elapsed_ms": verdict.elapsed_ms,
                "started_at": verdict.started_at.isoformat() if verdict.started_at else None,
                "entries": entries,
            }
        )
    return reviews


def _lanes(run: ResearchRun) -> list[dict]:
    """One lane per participant: what it did, round by round, and on the wire.

    Ordered by ``run.participants`` rather than by score, so the view does not
    reorder itself between rounds while someone is reading it.
    """
    scores_by_round: dict[str, list[float | None]] = {}
    for verdict in run.rounds or ([run.verdict] if run.verdict else []):
        totals = {entry.source: entry.total for entry in verdict.verdicts}
        for name in run.participants:
            scores_by_round.setdefault(name, []).append(totals.get(name))

    lanes = []
    for name in run.participants:
        steps = run.traces.get(name, [])
        lanes.append(
            {
                "source": name,
                "auth": run.auth_modes.get(name, "none"),
                "draft": _draft_view(run, name),
                "failure": run.failures.get(name, ""),
                "rounds_used": run.rounds_used(name),
                "scores": scores_by_round.get(name, []),
                "calls": [
                    {
                        "phase": step.phase,
                        "label": step.label,
                        "host": step.host,
                        "path": step.path,
                        "method": step.method,
                        "status": step.status,
                        "elapsed_ms": step.elapsed_ms,
                        "bytes": step.bytes,
                        "request_id": step.request_id,
                        "ok": step.ok,
                        "detail": step.detail,
                        "offset_ms": (
                            (step.started_at - run.started_at).total_seconds() * 1000
                            if step.started_at
                            else 0.0
                        ),
                    }
                    for step in steps
                ],
            }
        )
    return lanes


def build_flow(run: ResearchRun) -> dict:
    """The whole view for one run."""
    return {
        "run_id": run.run_id,
        "started_at": run.started_at.isoformat(),
        "elapsed_ms": run.elapsed_ms,
        "topic": run.request.topic,
        "questions": run.request.questions,
        "max_words": run.request.max_words,
        "participants": run.participants,
        "rounds": run.round_count,
        "dimensions": list(RUBRIC_DIMENSIONS),
        "max_total": MAX_TOTAL,
        "winner": run.verdict.winner if run.verdict else None,
        "judge": run.verdict.judge if run.verdict else "",
        "complete": run.complete,
        "lanes": _lanes(run),
        "reviews": _reviews(run),
        # The honest header for the whole view. A run where no draft came from a
        # model is not a comparison, and the page says so above the scores
        # rather than in a footnote.
        "brains": sorted({draft.brain for draft in run.drafts}) or ["none"],
    }


__all__ = ["build_flow"]
