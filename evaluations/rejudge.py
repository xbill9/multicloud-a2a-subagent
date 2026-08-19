"""Score the stored drafts again with a different judge, and compare.

    python -m evaluations.rejudge --judge llm

The panel view's weakest column is regret, because it is measured in rubric
points and the rubric scores form. The standing objection is that every finding
here is an artifact of one scorer. This answers it with data rather than a
caveat: the drafts are already stored, so a second judge can rank the *same*
corpus and the two panel tables can be put side by side.

**Nothing is mutated.** The recorded runs are the record. This reads them,
re-scores in memory, and writes a separate comparison -- an audit whose history
can be rewritten to agree with a later opinion is not an audit.

**What this does and does not establish.** If both judges agree that no cloud
owns the winner, and see the same availability and comparable regret, then
those findings do not depend on the rubric's weightings, which is the
objection. It does *not*
make the two judges independent in the stronger sense: the `llm` judge is
Gemini, and one participant runs Gemini, so a shared-vendor bias would show up
in both the participant's drafts and the judge's reading of them. That is
narrower than the objection but it is not nothing, and it should be stated
rather than glossed.
"""

import argparse
import asyncio
import json
from pathlib import Path

from coordinator.judge import load_judge
from evaluations import panel
from evaluations.store import load


async def rescore(runs: list, judge_mode: str) -> list:
    """Re-rank every model-backed run with ``judge_mode``, in memory."""
    judge = load_judge(judge_mode)
    rescored = []
    for run in runs:
        scored = [draft for draft in run.drafts if draft.brain == "llm"]
        if not scored or run.verdict is None:
            continue
        verdict = await judge.judge(run.request, scored)
        # A copy, so the original stays exactly as recorded.
        clone = run.model_copy(deep=True)
        clone.verdict = verdict
        clone.rounds = [verdict]
        rescored.append(clone)
    return rescored


def compare(original: dict, second: dict, judge_mode: str) -> str:
    lines = [
        "panel view under two judges",
        "",
        f"  as recorded : {original['runs']} run(s)",
        f"  re-scored   : {second['runs']} run(s) by {judge_mode}",
        "",
        f"{'cloud':<8}{'avail':>8}{'win% A':>9}{'win% B':>9}"
        f"{'regret A':>10}{'regret B':>10}",
        "-" * 54,
    ]
    by_second = {row["cloud"]: row for row in second["clouds"]}
    for row in original["clouds"]:
        other = by_second.get(row["cloud"], {})
        lines.append(
            f"{row['cloud']:<8}"
            f"{(row['availability'] or 0) * 100:>7.0f}%"
            f"{(row['win_rate'] or 0) * 100:>8.0f}%"
            f"{(other.get('win_rate') or 0) * 100:>8.0f}%"
            f"{(row['mean_regret'] or 0):>10.2f}"
            f"{(other.get('mean_regret') or 0):>10.2f}"
        )

    lines += ["", "reading:"]
    if original["no_dominant_cloud"] and second["no_dominant_cloud"]:
        lines.append(
            "  - both judges see a winner that no single cloud owns. That "
            "finding does not depend on the rubric's weightings."
        )
    elif original["no_dominant_cloud"] != second["no_dominant_cloud"]:
        lines.append(
            "  - THE JUDGES DISAGREE about whether any cloud dominates. On this "
            "evidence the best-of-breed claim is a property of the scorer, not "
            "of the clouds, and should not be made."
        )
    lines.append(
        "  - availability is identical under both by construction: it counts "
        "drafts, not scores, and no judge can change whether one existed."
    )
    # Only true of the model judge. Printing it under `rubric` claimed a
    # deterministic scorer was a Gemini call, which is the kind of caveat that
    # discredits the honest ones beside it.
    if judge_mode == "llm":
        lines.append(
            "  - the llm judge is Gemini, and one participant runs Gemini. These "
            "two scorers are not independent in the strongest sense; a "
            "shared-vendor bias would appear in both."
        )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Re-score stored runs with another judge")
    parser.add_argument("--store", type=Path, default=None)
    parser.add_argument("--judge", default="llm", help="rubric or llm")
    parser.add_argument("--json", dest="json_path", help="write both summaries as JSON")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    runs = [run for _recorded, run in load(args.store)]
    original = panel.summarise(runs)
    if not original["runs"]:
        print("no model-backed runs to re-score")
        return 1

    rescored = asyncio.run(rescore(runs, args.judge))
    second = panel.summarise(rescored)
    print(compare(original, second, args.judge))

    if args.json_path:
        Path(args.json_path).write_text(
            json.dumps({"as_recorded": original, "rescored": second}, indent=2)
        )
        print(f"\nwrote {args.json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
