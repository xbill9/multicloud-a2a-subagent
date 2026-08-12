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
from datetime import UTC, datetime
from time import perf_counter

from coordinator import trace
from coordinator.errors import AdapterError, FailureKind
from coordinator.judge import RubricJudge
from coordinator.models import Draft, ResearchRequest, ResearchRun, TraceStep
from coordinator.participants import Participant


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
        failures: dict[str, str] = {}
        traces: dict[str, list[TraceStep]] = {}

        gathered = await asyncio.gather(
            *(
                self._call(participant, request, failures, traces)
                for participant in self._participants
            )
        )
        drafts = [draft for draft in gathered if draft is not None]

        verdict = await self._judge.judge(request, drafts)

        return ResearchRun(
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
        return None
