"""Fan one brief out across every participating cloud, then judge the drafts.

All participants are called concurrently and independently: one cloud timing
out or answering with nothing degrades the run to the remaining clouds rather
than failing it. That is the behaviour the mesh exists to demonstrate, so
failures are recorded per participant rather than raised.

The judging step is deliberately *after* the barrier, unlike everything else
here. A judge has to see all the drafts to rank them, so this is the one place
in the system where a slow cloud delays the result rather than just its own
leg -- which is why the per-participant timeout matters more than it looks.
"""

import asyncio
import inspect
import logging
from datetime import UTC, datetime
from time import perf_counter

from coordinator import trace
from coordinator.errors import AdapterError, FailureKind
from coordinator.judge import (
    RubricJudge,
    critique_for,
    needs_revision,
)
from coordinator.judge import max_rounds as judge_max_rounds
from coordinator.judge import pass_mark as judge_pass_mark
from coordinator.models import (
    MAX_TOTAL,
    Draft,
    ResearchRequest,
    ResearchRun,
    TraceStep,
    Verdict,
    new_run_id,
)
from coordinator.participants import Participant, Revision
from protocol.telemetry import span

log = logging.getLogger("mesh")


def _mark_failed(current, kind: str, detail: str) -> None:
    """Put a leg's failure on its span without letting telemetry raise.

    The failure kind is the attribute worth having: `provider` and `protocol`
    are the distinction this project keeps paying to preserve, and a backend
    that can filter on it answers "did AgentCore break A2A or did Bedrock
    refuse the topic" without anyone reading a log.
    """
    if current is None:
        return
    try:
        current.set_attribute("research.failure_kind", kind)
        current.set_attribute("research.failure", detail[:400])
        from opentelemetry.trace import Status, StatusCode

        current.set_status(Status(StatusCode.ERROR, detail[:200]))
    except Exception:  # noqa: BLE001,S110
        pass


class ResearchMesh:
    def __init__(
        self,
        participants: list[Participant],
        *,
        judge=None,
        timeout_seconds: float = 120,
        max_rounds: int | None = None,
        pass_mark: float | None = None,
    ) -> None:
        if not participants:
            raise ValueError("a mesh needs at least one participant")
        self._participants = participants
        self._judge = judge or RubricJudge()
        self._timeout_seconds = timeout_seconds
        # `max_rounds=1` is the pre-loop behaviour exactly -- fan out, judge,
        # stop -- and is the control any claim about the loop has to be
        # compared against.
        self._max_rounds = max_rounds if max_rounds is not None else judge_max_rounds()
        self._pass_mark = pass_mark if pass_mark is not None else judge_pass_mark()

    async def run(self, request: ResearchRequest) -> ResearchRun:
        started = perf_counter()
        # Stamped here rather than left to the model's default, which would
        # fire when the run is *constructed* -- after every leg has finished.
        # Every trace offset is measured from this, so a default would put the
        # whole timeline in negative territory.
        started_at = datetime.now(UTC)
        run_id = new_run_id()
        failures: dict[str, str] = {}
        traces: dict[str, list[TraceStep]] = {}

        log.info(
            "run %s started: %d participant(s) %s, topic %r",
            run_id,
            len(self._participants),
            [participant.name for participant in self._participants],
            request.topic[:120],
        )

        # One span for the run, with every leg and judge round nested under it.
        # `run_id` is on the span as well as in the store, which is the join
        # between a distributed trace and the recorded evidence -- see
        # protocol/telemetry.py on why both exist.
        with span(
            "research.run",
            **{
                "research.run_id": run_id,
                "research.topic": request.topic[:200],
                "research.participants": len(self._participants),
                "research.max_rounds": self._max_rounds,
                "research.pass_mark": self._pass_mark,
            },
        ):
            return await self._rounds(
                request, run_id, started, started_at, failures, traces
            )

    async def _rounds(
        self,
        request: ResearchRequest,
        run_id: str,
        started: float,
        started_at: datetime,
        failures: dict[str, str],
        traces: dict[str, list[TraceStep]],
    ) -> ResearchRun:
        with span("research.round", **{"research.round": 1}):
            gathered = await asyncio.gather(
                *(
                    self._call(participant, request, failures, traces, run_id)
                    for participant in self._participants
                )
            )
        drafts = {draft.source: draft for draft in gathered if draft is not None}

        verdict = await self._judge_drafts(request, drafts, run_id)
        rounds = [verdict]
        #: Each source's score at the point it was last sent back, so a rewrite
        #: that did not help can be recognised. See the convergence guard below.
        sent_back_at: dict[str, float] = {}

        # The loop. Everything above is round 1; each pass below sends the
        # drafts that did not clear the bar back to *their own* cloud with the
        # judge's critique, and re-judges the whole field.
        #
        # Only the failures are revised. Rewriting a draft that already passed
        # is not free -- models asked to improve something good routinely
        # return something worse -- and the point of the loop is to lift the
        # floor, not to churn the ceiling.
        #
        # Re-judging *everything* rather than just the rewrites is deliberate
        # and costs a judge call: the rubric is comparative on rank and the
        # model judge sees all drafts at once, so a verdict over a mixed field
        # of old and new drafts is the only one that is internally consistent.
        for round_number in range(2, self._max_rounds + 1):
            failing = needs_revision(verdict, mark=self._pass_mark)
            if not failing:
                break

            # Two guards, and both were found by running the loop rather than
            # by reasoning about it.
            #
            # **Convergence.** A source whose rewrite scored no better than the
            # draft it replaced is not asked again. Without this the loop
            # always runs to `max_rounds`, because "below the bar" is a
            # standing condition and nothing about being asked twice makes a
            # model that cannot clear it clear it. The `direct` brain exposes
            # this immediately -- its draft is byte-identical every round -- but
            # it is not a test artefact: a small model on a hard brief does the
            # same thing more slowly and more expensively.
            #
            # **Capability.** A source whose `research` does not accept a
            # revision is left alone. Any A2A server can answer this brief,
            # which is the point of using a standard protocol, and one that
            # never heard of this repo cannot be sent a critique. Calling it
            # with one raises a TypeError that would surface as a leg failure
            # on a leg that is working perfectly.
            candidates = []
            for entry in failing:
                if entry.source not in drafts:
                    continue
                if not self._accepts_revision(entry.source):
                    continue
                previous = sent_back_at.get(entry.source)
                if previous is not None and entry.total <= previous:
                    log.info(
                        "run %s round %d: %s scored %.1f after a rewrite from %.1f; "
                        "not asking again",
                        run_id,
                        round_number,
                        entry.source,
                        entry.total,
                        previous,
                    )
                    continue
                candidates.append(entry)

            if not candidates:
                break

            revisions = {
                entry.source: Revision(
                    previous=drafts[entry.source].body,
                    critique=critique_for(entry),
                    score=entry.total,
                    maximum=MAX_TOTAL,
                    round=round_number,
                )
                for entry in candidates
            }
            for entry in candidates:
                sent_back_at[entry.source] = entry.total

            log.info(
                "run %s round %d: %s below %.1f, sending back",
                run_id,
                round_number,
                ", ".join(f"{name}={revisions[name].score:.1f}" for name in revisions),
                self._pass_mark,
            )

            by_name = {participant.name: participant for participant in self._participants}
            revision_span = span(
                "research.round",
                **{
                    "research.round": round_number,
                    "research.revising": ",".join(sorted(revisions)),
                },
            )
            with revision_span:
                revised = await asyncio.gather(
                *(
                        self._call(
                            by_name[name], request, failures, traces, run_id, revision=revision
                        )
                        for name, revision in revisions.items()
                        if name in by_name
                    )
                )
            # A leg that failed on a rewrite keeps the draft it already had.
            # Losing a scored draft because its *improvement* timed out would
            # make the loop able to make a run worse, which is the one thing a
            # quality loop must not do.
            for draft in revised:
                if draft is not None:
                    drafts[draft.source] = draft

            verdict = await self._judge_drafts(request, drafts, run_id)
            rounds.append(verdict)

        drafts_final = list(drafts.values())

        log.info(
            "run %s final: judged by %s, winner %s after %d round(s), "
            "%d draft(s), %d failure(s)%s",
            run_id,
            verdict.judge,
            verdict.winner or "none",
            len(rounds),
            len(drafts_final),
            len(failures),
            "".join(f" | warning: {warning}" for warning in verdict.warnings),
        )
        for name, failure in failures.items():
            log.warning("run %s leg %s failed: %s", run_id, name, failure)

        return ResearchRun(
            run_id=run_id,
            request=request,
            started_at=started_at,
            participants=[participant.name for participant in self._participants],
            auth_modes={
                participant.name: participant.auth for participant in self._participants
            },
            drafts=drafts_final,
            failures=failures,
            traces=traces,
            verdict=verdict,
            rounds=rounds,
            elapsed_ms=(perf_counter() - started) * 1000,
        )

    def _accepts_revision(self, name: str) -> bool:
        """Whether this participant's source can be sent a revision at all.

        Checked by signature rather than by catching TypeError, because a
        TypeError raised *inside* a source that does accept revisions would be
        swallowed by the catch and reported as "cannot revise" -- turning a real
        bug into a silently shortened loop.
        """
        for participant in self._participants:
            if participant.name != name:
                continue
            try:
                signature = inspect.signature(participant.source.research)
            except (TypeError, ValueError):
                return False
            parameters = list(signature.parameters.values())
            if any(p.kind is inspect.Parameter.VAR_POSITIONAL for p in parameters):
                return True
            if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters):
                return True
            return len(parameters) >= 2
        return False

    async def _judge_drafts(
        self, request: ResearchRequest, drafts: dict[str, Draft], run_id: str
    ) -> "Verdict":
        """Judge the current field, timing the call.

        Judging is the only step after the barrier, so it is invisible in every
        per-leg figure and shows up in `elapsed_ms` as an unexplained gap --
        tolerable while the rubric takes microseconds, misleading the moment a
        model takes the seat and becomes the slowest thing in the run. With a
        loop there is now more than one of these, so each carries its own.
        """
        started_at = datetime.now(UTC)
        started = perf_counter()
        with span(
            "research.judge",
            **{
                "research.run_id": run_id,
                "research.drafts": len(drafts),
            },
        ) as judge_span:
            verdict = await self._judge.judge(request, list(drafts.values()))
            if judge_span is not None:
                try:
                    judge_span.set_attribute("research.judge_name", verdict.judge)
                    judge_span.set_attribute("research.winner", verdict.winner or "none")
                except Exception:  # noqa: BLE001,S110
                    pass
        verdict.started_at = started_at
        verdict.elapsed_ms = (perf_counter() - started) * 1000
        log.info(
            "run %s judged by %s in %.0fms: winner %s%s",
            run_id,
            verdict.judge,
            verdict.elapsed_ms,
            verdict.winner or "none",
            "".join(f" | warning: {warning}" for warning in verdict.warnings),
        )
        return verdict

    async def _call(
        self,
        participant: Participant,
        request: ResearchRequest,
        failures: dict[str, str],
        traces: dict[str, list[TraceStep]],
        run_id: str,
        revision=None,
    ) -> Draft | None:
        # The trace is opened here rather than inside the client stacks because
        # this is the only scope that spans all three places a leg makes an
        # HTTP call -- the credential mint, the card fetch, and the invocation
        # -- and because a leg that *failed* has a trace worth keeping and no
        # draft to hang it off. `asyncio.gather` gives each of these coroutines
        # its own context copy, so three legs fill three traces with no locking.
        with trace.collect() as leg, span(
            "research.leg",
            **{
                "research.run_id": run_id,
                "research.cloud": participant.name,
                "research.auth_mode": participant.auth,
                "research.stack": participant.stack,
                "research.round": revision.round if revision is not None else 1,
                "research.revision": revision is not None,
            },
        ) as leg_span:
            try:
                async with asyncio.timeout(self._timeout_seconds):
                    if revision is None:
                        # Not `research(request, None)`: a source written before
                        # the loop takes one argument, and every A2A server that
                        # is not this repo's is such a source.
                        return await participant.source.research(request)
                    return await participant.source.research(request, revision)
            except TimeoutError:
                message = AdapterError(
                    FailureKind.TIMEOUT, f"exceeded {self._timeout_seconds}s"
                ).safe_message()
                failures[participant.name] = message
                _mark_failed(leg_span, "timeout", message)
            except AdapterError as exc:
                failures[participant.name] = exc.safe_message()
                _mark_failed(leg_span, exc.kind.value, exc.safe_message())
            except Exception as exc:  # noqa: BLE001 - adapter boundary converts SDK failures
                message = AdapterError(FailureKind.PROTOCOL, str(exc)).safe_message()
                failures[participant.name] = message
                _mark_failed(leg_span, "protocol", message)
            finally:
                # Recorded even on the success path's return, which is why this
                # is a finally and not a line after the try.
                if leg.steps:
                    # Extend, never assign. Each round opens a fresh
                    # `trace.collect()`, so assigning here made round 2's calls
                    # *replace* round 1's -- a two-round run rendered a timeline
                    # showing one A2A call per leg, which is the shape of a run
                    # that never looped. Losing evidence is the one failure this
                    # layer exists to prevent.
                    traces.setdefault(participant.name, []).extend(leg.steps)
                # One line per round trip, to the service log. The store holds
                # the same steps in more detail, but the store is one file on a
                # mounted bucket that a failed write drops with an `OSError` the
                # caller only logs -- so the evidence for a run would live
                # entirely in the artifact most likely to be missing. These
                # lines land in Cloud Logging by a different path, and carry the
                # provider's own request id, which is the part someone else can
                # check.
                for step in leg.steps:
                    log.info(
                        "run %s leg %s %s %s %s%s -> %s in %.0fms%s",
                        run_id,
                        participant.name,
                        step.phase,
                        step.method,
                        step.host,
                        step.path,
                        step.status if step.status is not None else "-",
                        step.elapsed_ms,
                        f" [{step.request_id}]" if step.request_id else "",
                    )
        return None
