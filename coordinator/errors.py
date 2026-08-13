"""The failure taxonomy, re-exported.

It moved to ``protocol.errors`` so a researcher agent can raise and classify
failures without importing the coordinator -- see ``protocol/models.py`` for
why that mattered. Re-exported here because ``coordinator.errors`` is what
every module on this side already says, and rewriting forty import lines to
prove a point about packaging is churn, not clarity.
"""

from protocol.errors import AdapterError, FailureKind

__all__ = ["AdapterError", "FailureKind"]
