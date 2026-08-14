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


def agreement(runs_by_id: dict[str, str], path: Path | None = None) -> dict:
    """How often the judge and a human picked the same winner.

    ``runs_by_id`` maps run_id -> the judge's winner, which the caller reads
    from the run store; this module deliberately does not import it, so
    feedback stays readable without the runs beside it.

    The number to distrust first is ``reviewed``. Agreement over three runs is
    not a calibration, and the caller is told the count so it can decline to
    report a rate -- the same rule `evaluations.report` applies to win rates.
    """
    agreed = 0
    disagreed = 0
    unreviewed = 0
    citations = {verdict: 0 for verdict in CITATION_VERDICTS}

    seen: set[str] = set()
    for review in load(path):
        judge_winner = runs_by_id.get(review.run_id)
        for draft in review.drafts:
            for citation in draft.citations:
                if citation.verdict in citations:
                    citations[citation.verdict] += 1
        if judge_winner is None:
            unreviewed += 1
            continue
        seen.add(review.run_id)
        if review.winner is None:
            continue
        if review.winner == judge_winner:
            agreed += 1
        else:
            disagreed += 1

    total = agreed + disagreed
    return {
        "reviewed": len(seen),
        "with_a_winner": total,
        "agreed": agreed,
        "disagreed": disagreed,
        # None rather than 0.0 when nothing has been reviewed: "the judge and a
        # human never agree" and "nobody has looked" are opposite claims.
        "agreement_rate": (agreed / total) if total else None,
        "citations": citations,
        "reviews_for_unknown_runs": unreviewed,
    }


__all__ = [
    "CITATION_VERDICTS",
    "CitationFeedback",
    "DraftFeedback",
    "HumanReview",
    "agreement",
    "for_run",
    "load",
    "record",
    "store_path",
]
