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
import logging
from datetime import UTC, datetime
from time import perf_counter

from coordinator import trace
from coordinator.errors import AdapterError, FailureKind
from coordinator.judge import RubricJudge
from coordinator.models import (
    Draft,
    ResearchRequest,
    ResearchRun,
    TraceStep,
    new_run_id,
)
from coordinator.participants import Participant

log = logging.getLogger("mesh")


class ResearchMesh:
    def __init__(
        self,
        participants: list[Participant],
        *,
        judge=None,
        timeout_seconds: float = 120,
    ) -> None:
        if not participants:
            raise ValueError("a mesh needs at least one participant")
        self._participants = participants
        self._judge = judge or RubricJudge()
        self._timeout_seconds = timeout_seconds

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

        gathered = await asyncio.gather(
            *(
                self._call(participant, request, failures, traces, run_id)
                for participant in self._participants
            )
        )
        drafts = [draft for draft in gathered if draft is not None]

        # Timed separately from the legs. Judging is the only step after the
        # barrier, so it is invisible in every per-leg figure and shows up in
        # `elapsed_ms` as an unexplained gap -- which is tolerable while the
        # rubric judge takes microseconds and misleading the moment a model
        # takes the seat, because then the slowest thing in the run is the step
        # with no evidence behind it.
        judge_started_at = datetime.now(UTC)
        judge_started = perf_counter()
        verdict = await self._judge.judge(request, drafts)
        verdict.started_at = judge_started_at
        verdict.elapsed_ms = (perf_counter() - judge_started) * 1000

        log.info(
            "run %s judged by %s in %.0fms: winner %s, %d draft(s), %d failure(s)%s",
            run_id,
            verdict.judge,
            verdict.elapsed_ms,
            verdict.winner or "none",
            len(drafts),
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
            drafts=drafts,
            failures=failures,
            traces=traces,
            verdict=verdict,
            elapsed_ms=(perf_counter() - started) * 1000,
        )

    async def _call(
        self,
        participant: Participant,
        request: ResearchRequest,
        failures: dict[str, str],
        traces: dict[str, list[TraceStep]],
        run_id: str,
    ) -> Draft | None:
        # The trace is opened here rather than inside the client stacks because
        # this is the only scope that spans all three places a leg makes an
        # HTTP call -- the credential mint, the card fetch, and the invocation
        # -- and because a leg that *failed* has a trace worth keeping and no
        # draft to hang it off. `asyncio.gather` gives each of these coroutines
        # its own context copy, so three legs fill three traces with no locking.
        with trace.collect() as leg:
            try:
                async with asyncio.timeout(self._timeout_seconds):
                    return await participant.source.research(request)
            except TimeoutError:
                failures[participant.name] = AdapterError(
                    FailureKind.TIMEOUT, f"exceeded {self._timeout_seconds}s"
                ).safe_message()
            except AdapterError as exc:
                failures[participant.name] = exc.safe_message()
            except Exception as exc:  # noqa: BLE001 - adapter boundary converts SDK failures
                failures[participant.name] = AdapterError(
                    FailureKind.PROTOCOL, str(exc)
                ).safe_message()
            finally:
                # Recorded even on the success path's return, which is why this
                # is a finally and not a line after the try.
                if leg.steps:
                    traces[participant.name] = leg.steps
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
