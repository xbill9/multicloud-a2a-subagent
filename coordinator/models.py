"""Domain models for the multi-cloud research mesh.

The predecessor of this file described currency conversions, because currency
was the sample task the cross-cloud auth work was built against. That task is
gone; what it was scaffolding for -- one coordinator minting three different
credentials to reach three vendors' agent runtimes -- is not.

What replaces it is a different shape of question. Three research agents, one
per cloud and each on its own vendor's model, are given the same brief and
write independent drafts. There is no median to take: drafts are prose, not
decimals, and the interesting disagreement is in quality rather than in value.
So a judge scores them and names a winner, and the scores are what the audit
layer aggregates across runs.

The consequence worth stating: **the old model could tell you the mesh was
wrong, and this one cannot.** Three clouds disagreeing on a number is a fact;
three clouds writing different essays is the expected outcome. A verdict here
is one judge's opinion on one brief, which is why `evaluations/` exists at all
-- a single run says nothing, and only the aggregate is evidence.
"""

from datetime import UTC, datetime

from pydantic import BaseModel, Field, field_validator

#: Rubric dimensions, in the order they are asked for and reported. Fixed
#: rather than configurable: an audit that compares runs scored on different
#: dimensions is comparing nothing, and making it a parameter invites exactly
#: that. Changing this list invalidates the stored history, which is why
#: `evaluations.store` records the rubric version alongside every run.
RUBRIC_DIMENSIONS = ("coverage", "specificity", "evidence", "structure", "concision")

#: Bumped whenever RUBRIC_DIMENSIONS or the scoring changes, so old runs can be
#: excluded from an aggregate rather than silently averaged into it.
RUBRIC_VERSION = 1

MAX_DIMENSION_SCORE = 5.0


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


class TraceStep(BaseModel):
    """One HTTP round trip made on one leg's behalf.

    The evidence layer. Everything else in this file is what the mesh
    *concluded*; this is what it *did*, and the difference matters because the
    interesting claims here are not about draft quality at all -- they are
    "this call crossed a cloud boundary" and "this call was authenticated".
    Both are invisible in a Draft, which looks identical whether it came from
    Bedrock over SigV4 or from a canned string two lines away.

    Deliberately never carries a token, a header or a response body. A trace
    that must be redacted before it can be shown is a trace nobody shows.
    """

    #: "credential" (an identity provider), "discovery" (agent-card fetch) or
    #: "invoke" (the A2A call itself). Enough to order a flow diagram without
    #: parsing URLs at render time.
    phase: str
    #: Human-readable, written by the code that made the call -- e.g. the auth
    #: boundary string, which already names the audience being asked for.
    label: str
    host: str
    path: str = ""
    method: str = "GET"
    status: int | None = None
    #: Wall clock, when the request left. Carried alongside `elapsed_ms`
    #: because the two answer different questions: elapsed says how long a hop
    #: took, and this says *when it happened relative to the others*, which is
    #: the only way to see that three legs really did run concurrently rather
    #: than one after another. A per-leg duration cannot show that.
    started_at: datetime | None = None
    elapsed_ms: float = 0.0
    #: Response body size from `content-length`, or None when the provider did
    #: not send one. Not measured by reading the body: an event hook runs
    #: before the stream is consumed, and reading it there would take the
    #: response away from the parser that is about to need it.
    bytes: int | None = None
    ok: bool = True
    #: Only on failure, and only the provider's own words. This is the field
    #: the predecessor series kept discarding and kept paying for.
    detail: str = ""


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

    @property
    def word_count(self) -> int:
        return len(self.body.split())

    @field_validator("observed_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        """Treat a timestamp without an offset as UTC so age math cannot crash."""
        return value if value.tzinfo else value.replace(tzinfo=UTC)


class DimensionScore(BaseModel):
    dimension: str
    score: float = Field(ge=0, le=MAX_DIMENSION_SCORE)
    rationale: str = ""


class DraftVerdict(BaseModel):
    """How one draft scored, and where it placed."""

    source: str
    scores: list[DimensionScore] = Field(default_factory=list)
    total: float = 0.0
    rank: int = 0
    notes: str = ""

    def score_for(self, dimension: str) -> float | None:
        for score in self.scores:
            if score.dimension == dimension:
                return score.score
        return None


class Verdict(BaseModel):
    """The judge's read on one set of drafts."""

    #: Participant name of the winning draft, or None when nothing was judgeable.
    winner: str | None = None
    verdicts: list[DraftVerdict] = Field(default_factory=list)
    #: "rubric" for the deterministic scorer, otherwise the judge model's id.
    judge: str = "rubric"
    #: Whether the judge saw which cloud wrote which draft. False is the
    #: default and the only defensible setting for an audit: a model asked to
    #: rank three drafts, one of them labelled as its own vendor's, is being
    #: asked a different question.
    blind: bool = True
    rubric_version: int = RUBRIC_VERSION
    rationale: str = ""
    warnings: list[str] = Field(default_factory=list)

    @property
    def ranking(self) -> list[str]:
        return [verdict.source for verdict in sorted(self.verdicts, key=lambda v: v.rank)]

    @property
    def margin(self) -> float | None:
        """Winner's total minus runner-up's, or None below two drafts.

        Worth carrying because a 0.1 win and a 2.0 win are the same `winner`
        field and very different results. The report treats a margin under
        `NARROW_MARGIN` as a tie rather than a ranking.
        """
        if len(self.verdicts) < 2:
            return None
        totals = sorted((verdict.total for verdict in self.verdicts), reverse=True)
        return totals[0] - totals[1]


class ResearchRun(BaseModel):
    """Stable result envelope for one brief fanned out across the mesh."""

    request: ResearchRequest
    #: When the fan-out began. The origin every trace step's offset is measured
    #: from, so a timeline can be rendered from a stored run and not only from
    #: the live response.
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    participants: list[str] = Field(default_factory=list)
    #: Participant name -> auth mode actually used on that leg. Recorded in the
    #: run rather than inferred from config afterwards: "which legs were
    #: keyless" is a claim the artifact has to be able to back on its own.
    auth_modes: dict[str, str] = Field(default_factory=dict)
    drafts: list[Draft] = Field(default_factory=list)
    failures: dict[str, str] = Field(default_factory=dict)
    #: Participant name -> every HTTP round trip made on its behalf, in order.
    #: Recorded for legs that failed as well as legs that answered, which is
    #: the whole point: a leg with a 403 on the agent-card fetch and a leg that
    #: was never dialled produce the same one-line failure string and very
    #: different traces. Empty for in-process adapters, which is the honest
    #: answer -- a canned draft crossed no network and must not be able to
    #: render a flow diagram claiming it did.
    traces: dict[str, list[TraceStep]] = Field(default_factory=dict)
    verdict: Verdict | None = None
    elapsed_ms: float = Field(ge=0)

    @property
    def succeeded(self) -> bool:
        """True when at least one cloud returned a draft.

        Not `bool(self.drafts) or bool(self.verdict)`: a verdict object always
        exists once judging has been attempted, so testing for it reported a
        totally failed run as green -- the exact defect the currency version
        of this property was fixed for, preserved here because the deployed
        coordinator's exit status is still the health signal.
        """
        return bool(self.drafts)

    @property
    def complete(self) -> bool:
        """True when every participant answered and a winner was named."""
        return (
            len(self.drafts) == len(self.participants)
            and self.verdict is not None
            and self.verdict.winner is not None
        )
