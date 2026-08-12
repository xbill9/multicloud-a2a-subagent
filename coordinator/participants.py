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

import os
from dataclasses import dataclass
from typing import Protocol

from clients import load_client
from coordinator.auth import auth_mode, credentials_for
from coordinator.models import Draft, ResearchRequest

#: Every cloud the mesh can call, and the environment variable that points at
#: its agent. One definition, shared by the CLI, the master service and the
#: matrix, because the alternative -- a copy per entry point -- is how a fourth
#: cloud ends up wired into two of the three and silently missing from the
#: third.
CLOUD_ENDPOINTS: dict[str, tuple[str, str]] = {
    "gcp": ("GCP_A2A_ENDPOINT", "http://127.0.0.1:10001"),
    "aws": ("AWS_A2A_ENDPOINT", "http://127.0.0.1:10002"),
    "azure": ("AZURE_A2A_ENDPOINT", "http://127.0.0.1:10003"),
}


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


def endpoint_for(cloud: str) -> str:
    """Where ``cloud``'s researcher agent lives, defaulting to the local mesh."""
    env_var, default = CLOUD_ENDPOINTS[cloud]
    return os.getenv(env_var, default)


def build_participants(
    clouds: list[str] | None = None,
    *,
    client: str = "a2a-sdk",
    timeout_seconds: float = 120.0,
) -> list[Participant]:
    """One participant per cloud, each with its own leg's credential resolved.

    The credential is resolved *here*, at construction, rather than per call:
    a leg that cannot mint its credential should fail while the mesh is being
    assembled, not halfway through a fan-out where it is indistinguishable
    from the remote being down.
    """
    selected = clouds or list(CLOUD_ENDPOINTS)
    unknown = [cloud for cloud in selected if cloud not in CLOUD_ENDPOINTS]
    if unknown:
        raise ValueError(f"unknown cloud(s): {', '.join(unknown)}")

    participants: list[Participant] = []
    for cloud in selected:
        endpoint = endpoint_for(cloud)
        auth = credentials_for(cloud, endpoint)
        participants.append(
            Participant(
                name=cloud,
                source=load_client(
                    client,
                    endpoint,
                    source=cloud,
                    cloud=cloud,
                    timeout_s=timeout_seconds,
                    auth=auth,
                ),
                cloud=cloud,
                stack=client,
                auth=auth_mode(auth),
            )
        )
    return participants


__all__ = [
    "CLOUD_ENDPOINTS",
    "DraftSource",
    "Participant",
    "auth_mode",
    "build_participants",
    "credentials_for",
    "endpoint_for",
]
