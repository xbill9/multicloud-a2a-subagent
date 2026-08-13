"""The two types that cross the wire: the brief going out, the draft coming back.

Everything else the mesh models -- verdicts, traces, the run envelope -- is
something the *coordinator* concluded, and belongs to the coordinator. These
two are the contract between a researcher and whoever asked it a question, so
they live in ``protocol`` alongside the prompt construction and parsing that
both ends already share.

**The split is a deployment fact, not a taste in packaging.** A researcher on
AgentCore or Container Apps is a researcher: it answers a brief with its own
cloud's model and nothing else. It has no business carrying the judge, the
front end, the audit, or a credential adapter that mints tokens for three
clouds -- and it did carry all of them, because ``agents/common.py`` imported
``ResearchRequest`` from ``coordinator.models`` and the Dockerfiles therefore
copied the whole ``coordinator`` package into both remote images. One import
put the entire coordinator on two other vendors' infrastructure.

``coordinator.models`` re-exports both names, so the coordinator's own
vocabulary is unchanged and reads as it always did.
"""

from datetime import UTC, datetime

from pydantic import BaseModel, Field, field_validator


class ResearchRequest(BaseModel):
    """One brief, sent identically to every participating cloud.

    Identical is the point. The variable under test is the model, so varying
    the prompt per cloud -- giving each a different angle, which is the obvious
    way to make the drafts more interesting -- would confound the one thing the
    audit is trying to measure.
    """

    topic: str = Field(min_length=3)
    #: Optional focus questions. They also become the `coverage` dimension's
    #: answer key, which is the only part of the deterministic rubric that
    #: measures something the requester actually asked for.
    questions: list[str] = Field(default_factory=list)
    max_words: int = Field(default=600, gt=0)

    @field_validator("topic")
    @classmethod
    def strip_topic(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("topic must not be blank")
        return stripped

    @field_validator("questions")
    @classmethod
    def strip_questions(cls, values: list[str]) -> list[str]:
        return [value.strip() for value in values if value.strip()]


class Draft(BaseModel):
    """One cloud's answer to the brief."""

    source: str
    cloud: str = "unknown"
    #: What the agent said it ran, e.g. "gemini-2.5-flash". Reported by the
    #: agent rather than inferred from config, for the same reason `brain` is:
    #: the coordinator is a different container and its environment describes
    #: itself, not the agent. "unknown" when the agent did not say.
    model: str = "unknown"
    #: "direct" (canned draft, no model) or "llm". An audit that averages a
    #: canned draft into a model's score is reporting fiction, so this is
    #: carried per draft and filtered on in `evaluations.report`.
    brain: str = "unknown"
    title: str
    body: str
    observed_at: datetime
    latency_ms: float = Field(ge=0)
    #: Which round of the judge loop produced this draft. 1 is a first attempt;
    #: anything higher means the judge sent it back with a critique and this is
    #: the rewrite. Carried on the draft rather than derived, because the audit's
    #: most useful question is per-cloud: *how many rounds did this model need to
    #: clear the bar*, which is a measurement in a way that one blind score is
    #: not.
    round: int = Field(default=1, ge=1)

    @property
    def word_count(self) -> int:
        return len(self.body.split())

    @field_validator("observed_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        """Treat a timestamp without an offset as UTC so age math cannot crash."""
        return value if value.tzinfo else value.replace(tzinfo=UTC)


__all__ = ["Draft", "ResearchRequest"]
