"""The single interface every cloud plugs into.

The two-cloud benchmarks had two protocols with identical shapes, an artifact
of two transports being wired in one at a time. A mesh has no such asymmetry:
an in-process stub and a remote agent on another continent are both just named
draft sources.

``DraftSource`` deliberately says nothing about credentials. The credential is
a property of the *leg*, not of the conversion, and it is resolved once when
the source is constructed -- ``credentials_for(peer, endpoint)`` in
``coordinator.auth``, re-exported here because this is the interface it hangs
off. One adapter, three implementations, one shape; adding a fourth cloud
means adding a mode, not a code path.

The alternative is what the predecessor series did: three bespoke auth paths
retrofitted after the fact, one per repo, which is why its findings ended up
scattered across six of them.
"""

from dataclasses import dataclass
from typing import Protocol

from coordinator.auth import auth_mode, credentials_for
from coordinator.models import Draft, ResearchRequest


class DraftSource(Protocol):
    async def research(self, request: ResearchRequest) -> Draft: ...


@dataclass(frozen=True)
class Participant:
    """A named draft source, plus the metadata the reports need."""

    name: str
    source: DraftSource
    cloud: str = "local"
    stack: str = "in-process"
    #: How this leg authenticates: one of ``coordinator.auth.AUTH_MODES``.
    #: Reported rather than inferred, so a leg that silently fell back to an
    #: unauthenticated call cannot be mistaken for a federated one.
    auth: str = "none"

    def __str__(self) -> str:
        return self.name


__all__ = ["DraftSource", "Participant", "auth_mode", "credentials_for"]
