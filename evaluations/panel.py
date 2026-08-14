"""What the panel buys over any one cloud in it.

This is the project's actual claim, and it is not a benchmark. A benchmark asks
which model is best; that question is unanswerable here and uninteresting
anyway, because the three models are deliberately not matched -- a small fast
one, a mid one, and a reasoning deployment. Heterogeneity is the asset, not a
confound.

The claim is narrower and testable: **a brief answered by three clouds beats
the same brief answered by any one of them**, and it does so for two separate
reasons that must not be added together.

``rotation``
    How often each cloud produced the best draft. If one cloud won almost
    always, a panel would be waste and the honest recommendation would be to
    use that cloud. Rotation is what justifies the architecture, and it is the
    first thing to check before anything else here is worth reading.

``regret``
    Had you committed to one cloud, how far below the panel's best would you
    have landed, per brief, on average. A within-subjects measure: every cloud
    answered the *same* brief, so no separate control arm is needed and none of
    the usual between-groups objections apply.

``availability``
    How often a given cloud produced nothing at all while the panel still
    answered. This is the strongest of the three and the least sensitive to the
    rubric's weaknesses -- it does not ask how good a draft was, only whether
    there was one.

**Regret inherits the rubric.** It is measured in rubric points, and the rubric
scores form. Until human review says the rubric tracks judgement, regret is
"how far below the panel's best-scoring draft", not "how much worse an answer
you would have got". Availability and rotation do not have that problem;
rotation only needs the scores to be *ordered* correctly, and availability does
not use them at all.
"""

from collections import defaultdict
from dataclasses import dataclass, field

from coordinator.models import ResearchRun


@dataclass
class CloudPanelStats:
    cloud: str
    #: Runs where this cloud was in the participant list.
    invited: int = 0
    #: Runs where it produced a scored draft.
    answered: int = 0
    #: Runs where its draft scored highest.
    best: int = 0
    #: Panel best minus this cloud's score, per run it answered.
    gaps: list[float] = field(default_factory=list)

    @property
    def win_rate(self) -> float | None:
        return self.best / self.answered if self.answered else None

    @property
    def availability(self) -> float | None:
        return self.answered / self.invited if self.invited else None

    @property
    def mean_regret(self) -> float | None:
        return sum(self.gaps) / len(self.gaps) if self.gaps else None

    @property
    def worst_regret(self) -> float | None:
        return max(self.gaps) if self.gaps else None


def summarise(runs: list[ResearchRun]) -> dict:
    """Rotation, regret and availability across a set of runs.

    Only runs with a model in the path and a verdict count. A ``direct`` run has
    three identical canned drafts, so its "winner" is a latency tie-break and
    folding it in would manufacture rotation out of scheduling noise.
    """
    stats: dict[str, CloudPanelStats] = {}
    counted = 0
    panel_answered = 0
    panel_silent = 0

    for run in runs:
        scored = [d for d in run.drafts if d.brain == "llm"]
        if not scored or run.verdict is None or not run.verdict.verdicts:
            continue
        counted += 1

        totals = {entry.source: entry.total for entry in run.verdict.verdicts}
        if totals:
            panel_answered += 1
        else:
            panel_silent += 1
        best = max(totals.values()) if totals else None

        for name in run.participants:
            row = stats.setdefault(name, CloudPanelStats(cloud=name))
            row.invited += 1
            if name not in totals:
                continue
            row.answered += 1
            if best is not None:
                row.gaps.append(best - totals[name])
                if totals[name] >= best:
                    row.best += 1

    ordered = sorted(stats.values(), key=lambda r: (-(r.win_rate or 0), r.cloud))
    return {
        "runs": counted,
        "panel_answered": panel_answered,
        "panel_silent": panel_silent,
        "clouds": [
            {
                "cloud": row.cloud,
                "invited": row.invited,
                "answered": row.answered,
                "availability": row.availability,
                "best": row.best,
                "win_rate": row.win_rate,
                "mean_regret": row.mean_regret,
                "worst_regret": row.worst_regret,
            }
            for row in ordered
        ],
        # The one-line reading. Stated rather than left to be inferred, because
        # the whole argument turns on it: a panel is justified when no member
        # dominates, and pointless when one does.
        "rotates": (
            max((row.win_rate or 0) for row in ordered) < 0.75 if ordered else None
        ),
    }


def render(summary: dict) -> str:
    if not summary["runs"]:
        return (
            "no model-backed runs recorded.\n"
            "Run the mesh with RESEARCH_MODEL_MODE=llm before expecting a panel view."
        )

    lines = [
        f"panel view  ({summary['runs']} model-backed run(s))",
        "",
        f"{'cloud':<8}{'answered':>10}{'avail':>8}{'best':>6}{'win%':>7}"
        f"{'regret':>9}{'worst':>8}",
        "-" * 56,
    ]
    for row in summary["clouds"]:
        lines.append(
            f"{row['cloud']:<8}{row['answered']:>4}/{row['invited']:<5}"
            f"{(row['availability'] or 0) * 100:>7.0f}%"
            f"{row['best']:>6}"
            f"{(row['win_rate'] or 0) * 100:>6.0f}%"
            f"{(row['mean_regret'] or 0):>9.2f}"
            f"{(row['worst_regret'] or 0):>8.2f}"
        )

    lines += ["", "notes:"]
    if summary["rotates"]:
        lines.append(
            "  - the best draft rotates between clouds, which is what justifies "
            "a panel at all. Were one cloud winning nearly every brief, the "
            "honest recommendation would be to use that cloud."
        )
    else:
        lines.append(
            "  - one cloud wins nearly every brief. On this evidence a panel is "
            "not earning its cost, and the recommendation is to use that cloud."
        )
    lines += [
        "  - regret is the panel's best score minus this cloud's, per brief it "
        "answered: what committing to it alone would have cost.",
        "  - regret is measured in rubric points, and the rubric scores form. "
        "It is a lower bound on nothing until human review says the rubric "
        "tracks judgement.",
        "  - availability is the strongest column here: it does not ask how good "
        "a draft was, only whether there was one.",
    ]
    return "\n".join(lines)


__all__ = ["CloudPanelStats", "render", "summarise"]
