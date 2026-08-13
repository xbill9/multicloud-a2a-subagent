"""The failure taxonomy, shared by both ends of the wire.

Lives here rather than under ``coordinator`` because a researcher agent needs
it and must not need the coordinator. The distinction these kinds draw is the
one this project keeps paying for when it is lost: a remote that answered and
declined is a ``provider`` fault, a remote whose reply never arrived is a
``protocol`` fault, and filing the first as the second turns "Bedrock refused
the topic" into "AgentCore broke A2A".
"""

from enum import StrEnum


class FailureKind(StrEnum):
    VALIDATION = "validation"
    PROVIDER = "provider"
    AUTHENTICATION = "authentication"
    TRANSPORT = "transport"
    TIMEOUT = "timeout"
    PROTOCOL = "protocol"


class AdapterError(RuntimeError):
    def __init__(self, kind: FailureKind, message: str) -> None:
        super().__init__(message)
        self.kind = kind

    def safe_message(self) -> str:
        return f"{self.kind.value}: {self}"


__all__ = ["AdapterError", "FailureKind"]
