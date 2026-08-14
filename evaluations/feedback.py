"""What a person thought, recorded beside what the judge thought.

This exists to answer a question the README has carried as an open weakness
since the rubric was written: *nobody has checked that rubric rank correlates
with human rank on even one set of drafts.* The rubric's weightings and
thresholds -- eight specifics per hundred words for full marks, five citation
markers, the asymmetric length penalty -- were chosen by argument. Argument is
how you get a plausible scorer, not a calibrated one.

So a reviewer reads the drafts, ranks them, and says whether each cited source
actually exists. Stored, and then compared: how often the human and the judge
picked the same winner is a number about *the judge*, and it is the only number
here that can tell you the instrument is wrong.

**A separate store from ``runs.jsonl``, deliberately.** A run is machine output
and immutable; feedback is opinion, arrives later, and there may be several for
one run from several people. Mixing them would mean rewriting a recorded run to
attach an opinion to it, and an audit whose history can be edited in place is
not an audit.

**Feedback never changes a verdict.** The judge said what it said and the store
keeps it. This records disagreement rather than resolving it -- the point is to
measure the scorer, and a scorer quietly corrected by its reviewers measures
nothing.
"""

import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

DEFAULT_STORE = Path(".eval/feedback.jsonl")

#: What a reviewer can say about one cited URL.
#:
#: ``unreachable`` is not ``fabricated``: a link can rot, a site can block a
#: datacentre, a paper can move behind a paywall. Collapsing those into one
#: verdict would let honest link rot be counted as a model inventing sources,
#: which is the specific accusation this is here to support or refute.
CITATION_VERDICTS = ("verified", "unreachable", "unrelated", "fabricated", "unchecked")


class CitationFeedback(BaseModel):
    url: str
    verdict: str = "unchecked"
    note: str = ""


class DraftFeedback(BaseModel):
    """One person's read of one cloud's draft."""

    source: str
    #: 1 is best. Optional: a reviewer may rank only the drafts they read.
    rank: int | None = None
    #: Out of 25, to sit beside the judge's total on the same scale. Optional,
    #: because a rank is much easier to give honestly than a number and the
    #: rank is what the correlation needs.
    score: float | None = None
    note: str = ""
    citations: list[CitationFeedback] = Field(default_factory=list)


class HumanReview(BaseModel):
    """One person's read of one run."""

    run_id: str
    reviewer: str = "anonymous"
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    #: Which draft this person would have picked. Compared with the judge's
    #: winner, and the comparison is the point of the whole module.
    winner: str | None = None
    drafts: list[DraftFeedback] = Field(default_factory=list)
    note: str = ""

    @property
    def ranking(self) -> list[str]:
        ranked = [d for d in self.drafts if d.rank is not None]
        return [d.source for d in sorted(ranked, key=lambda d: d.rank or 0)]


def store_path() -> Path:
    return Path(os.getenv("RESEARCH_FEEDBACK_STORE", str(DEFAULT_STORE)))


def record(review: HumanReview, *, path: Path | None = None) -> Path:
    """Append one review. Append-only, like the runs beside it."""
    target = path or store_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a") as handle:
        handle.write(review.model_dump_json() + "\n")
    return target


def load(path: Path | None = None) -> Iterator[HumanReview]:
    """Every review, oldest first. A torn final line is skipped, not raised on."""
    target = path or store_path()
    if not target.exists():
        return
    with target.open() as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                yield HumanReview.model_validate(json.loads(line))
            except Exception:  # noqa: BLE001, S112 - one torn line must not hide the rest
                continue


def for_run(run_id: str, path: Path | None = None) -> list[HumanReview]:
    return [review for review in load(path) if review.run_id == run_id]


def _pairs(order: list[str]) -> dict[tuple[str, str], int]:
    """Every ordered pair in a ranking, as -1/1 by which came first."""
    out: dict[tuple[str, str], int] = {}
    for i, a in enumerate(order):
        for b in order[i + 1 :]:
            key = (a, b) if a < b else (b, a)
            out[key] = 1 if (a, b) == key else -1
    return out


class JudgeRanking(BaseModel):
    """What the judge decided on one run, and whether that run was whole."""

    ranking: list[str] = Field(default_factory=list)
    #: Every participant answered. Only a complete run may calibrate.
    complete: bool = True


def agreement(
    judge_rankings: dict[str, JudgeRanking], path: Path | None = None
) -> dict:
    """How far a human and the judge agree, on two measures.

    ``judge_rankings`` maps run_id -> what the judge decided. Read by the caller
    from the run store; this module deliberately does not import it, so feedback
    stays readable without the runs beside it.

    **An incomplete run cannot calibrate anything, and is excluded.** A run
    where a leg timed out is missing a draft, and it is never missing a *random*
    one -- on 2026-08-14 three consecutive runs lost the GCP leg to the same
    defect, so every pair they could contribute was `aws` against `azure`, the
    same comparison repeated. Counting those would have measured one matchup
    several times and reported it as coverage. The judge already refuses to call
    a single draft a comparison; this is the same rule one participant further
    along.

    **Winner agreement** is the headline and the weaker of the two. It is one
    bit per run, and with three clouds a coin lands on it a third of the time,
    so it takes a lot of afternoons to say anything.

    **Pair concordance** is what makes a review worth reviewing. Every run
    yields one comparison per *pair* of drafts -- three for a three-cloud run --
    and asks the narrow question the rubric actually needs answered: when the
    judge put A above B, did the person? Five reviewed runs give fifteen
    comparisons rather than five, which is the difference between a number and
    an anecdote.

    Neither is reported as a rate until something has been reviewed: "they never
    agree" and "nobody has looked" are opposite claims and must not render as
    the same figure.
    """
    agreed = 0
    disagreed = 0
    concordant = 0
    discordant = 0
    unreviewed = 0
    citations = {verdict: 0 for verdict in CITATION_VERDICTS}

    seen: set[str] = set()
    incomplete = 0
    for review in load(path):
        decided = judge_rankings.get(review.run_id)
        judge_order = decided.ranking if decided is not None else None
        for draft in review.drafts:
            for citation in draft.citations:
                if citation.verdict in citations:
                    citations[citation.verdict] += 1
        if decided is None:
            unreviewed += 1
            continue
        if not decided.complete:
            # Reviewed, and deliberately not counted. Reported so the exclusion
            # is visible rather than silently shrinking the sample.
            incomplete += 1
            continue
        seen.add(review.run_id)

        if review.winner is not None and judge_order:
            if review.winner == judge_order[0]:
                agreed += 1
            else:
                disagreed += 1

        human_pairs = _pairs(review.ranking)
        judge_pairs = _pairs(list(judge_order))
        for key, human in human_pairs.items():
            judge = judge_pairs.get(key)
            if judge is None:
                continue
            if judge == human:
                concordant += 1
            else:
                discordant += 1

    total = agreed + disagreed
    pairs = concordant + discordant
    return {
        "reviewed": len(seen),
        "with_a_winner": total,
        "agreed": agreed,
        "disagreed": disagreed,
        "agreement_rate": (agreed / total) if total else None,
        # The measure worth watching. 1.0 is perfect agreement on ordering,
        # 0.5 is a coin, 0.0 is a judge that is exactly backwards -- which would
        # be a more useful finding than anything in between.
        "pairs_compared": pairs,
        "concordant": concordant,
        "discordant": discordant,
        "concordance": (concordant / pairs) if pairs else None,
        "citations": citations,
        "reviews_for_unknown_runs": unreviewed,
        "excluded_incomplete_runs": incomplete,
    }


__all__ = [
    "CITATION_VERDICTS",
    "CitationFeedback",
    "DraftFeedback",
    "HumanReview",
    "JudgeRanking",
    "agreement",
    "for_run",
    "load",
    "record",
    "store_path",
]
