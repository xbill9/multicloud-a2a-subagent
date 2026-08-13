"""In-process draft sources, for tests and for a fourth opinion with no cloud.

Nothing here crosses a network. It exists so the mesh, the judge and the audit
can be exercised deterministically -- including the failure paths, which are
the ones a live mesh is worst at producing on demand.
"""

import asyncio
from datetime import UTC, datetime
from time import perf_counter

from coordinator.errors import AdapterError
from coordinator.models import Draft, ResearchRequest
from protocol.research import extract_title


class CannedDraftAdapter:
    """Returns a fixed draft, optionally slowly, optionally by failing.

    ``delay_ms`` and ``failure`` are what make the degradation tests possible:
    a mesh that keeps going when one cloud times out is the claim, and the only
    honest way to test it is to have a participant that really does time out.
    """

    def __init__(
        self,
        body: str,
        *,
        source: str,
        cloud: str = "local",
        model: str = "none",
        brain: str = "direct",
        delay_ms: float = 0,
        failure: AdapterError | None = None,
    ) -> None:
        self._body = body
        self._source = source
        self._cloud = cloud
        self._model = model
        self._brain = brain
        self._delay_ms = delay_ms
        self._failure = failure

    async def research(self, request: ResearchRequest, revision=None) -> Draft:
        started = perf_counter()
        if self._delay_ms:
            await asyncio.sleep(self._delay_ms / 1000)
        if self._failure:
            raise self._failure
        return Draft(
            source=self._source,
            cloud=self._cloud,
            model=self._model,
            brain=self._brain,
            title=extract_title(self._body),
            body=self._body,
            observed_at=datetime.now(UTC),
            latency_ms=(perf_counter() - started) * 1000,
            round=revision.round if revision is not None else 1,
        )
