"""Aggregate the recorded runs into a per-model comparison.

    python -m evaluations.report
    python -m evaluations.report --json report.json --min-runs 5

What this can support and what it cannot are different things, and the report
prints the difference rather than leaving it to the reader:

- It compares **models as configured here**: one prompt, one rubric, one judge,
  no tools, whatever ``BEDROCK_MODEL_ID`` and the rest happened to be set to.
  It is not a benchmark of Gemini against Nova against a Foundry deployment.
- ``direct``-brain drafts are excluded outright. They are canned text, identical
  on all three clouds, and averaging them into a model's score would manufacture
  a result out of scaffolding.
- Below ``--min-runs`` a row is printed with its numbers withheld. Three runs is
  not a win rate, and a table that shows "100%" over one run will be quoted as
  if it were one.
"""

import argparse
import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from coordinator.models import RUBRIC_DIMENSIONS, ResearchRun
from evaluations.store import load

#: Fewer recorded runs than this and the row's numbers are withheld rather than
#: printed small. Not a statistical threshold -- there is no power calculation
#: behind it -- just a floor low enough to be reachable and high enough that a
#: single lucky run cannot produce a headline.
DEFAULT_MIN_RUNS = 5


@dataclass
class ModelRow:
    """Everything recorded about one (cloud, model) pair."""

    cloud: str
    model: str
    runs: int = 0
    wins: int = 0
    ties: int = 0
    failures: int = 0
    totals: list[float] = field(default_factory=list)
    latencies: list[float] = field(default_factory=list)
    dimensions: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))

    @property
    def key(self) -> str:
        return f"{self.cloud}/{self.model}"

    @property
    def win_rate(self) -> float | None:
        return self.wins / self.runs if self.runs else None

    @property
    def mean_total(self) -> float | None:
        return sum(self.totals) / len(self.totals) if self.totals else None

    @property
    def mean_latency_ms(self) -> float | None:
        return sum(self.latencies) / len(self.latencies) if self.latencies else None

    def mean_dimension(self, dimension: str) -> float | None:
        values = self.dimensions.get(dimension) or []
        return sum(values) / len(values) if values else None


@dataclass
class Audit:
    rows: list[ModelRow] = field(default_factory=list)
    #: Runs read from the store, before any filtering.
    total_runs: int = 0
    #: Runs skipped because no draft in them came from a model.
    direct_runs: int = 0
    #: Judges that produced the verdicts, with a count each. More than one
    #: entry means the rows below mix scorers, which makes them incomparable --
    #: the rubric and a model do not share a scale.
    judges: dict[str, int] = field(default_factory=dict)
    rubric_versions: dict[int, int] = field(default_factory=dict)
    narrow_wins: int = 0

    @property
    def mixed_judges(self) -> bool:
        return len(self.judges) > 1

    @property
    def mixed_rubrics(self) -> bool:
        return len(self.rubric_versions) > 1


def aggregate(runs: list[tuple], *, narrow_margin: float = 1.0) -> Audit:
    """Fold recorded runs into one row per (cloud, model)."""
    audit = Audit()
    rows: dict[str, ModelRow] = {}

    for _recorded_at, run in runs:
        audit.total_runs += 1
        run: ResearchRun

        scored = [draft for draft in run.drafts if draft.brain == "llm"]
        if not scored:
            audit.direct_runs += 1
            continue

        if run.verdict is not None:
            audit.judges[run.verdict.judge] = audit.judges.get(run.verdict.judge, 0) + 1
            version = run.verdict.rubric_version
            audit.rubric_versions[version] = audit.rubric_versions.get(version, 0) + 1

        margin = run.verdict.margin if run.verdict else None
        narrow = margin is not None and margin < narrow_margin
        if narrow:
            audit.narrow_wins += 1

        by_source = {verdict.source: verdict for verdict in (run.verdict.verdicts if run.verdict else [])}

        for draft in scored:
            row = rows.setdefault(
                f"{draft.cloud}/{draft.model}", ModelRow(cloud=draft.cloud, model=draft.model)
            )
            row.runs += 1
            row.latencies.append(draft.latency_ms)

            verdict = by_source.get(draft.source)
            if verdict is not None:
                row.totals.append(verdict.total)
                for dimension in RUBRIC_DIMENSIONS:
                    value = verdict.score_for(dimension)
                    if value is not None:
                        row.dimensions[dimension].append(value)

            if run.verdict and run.verdict.winner == draft.source:
                # A win inside the narrow margin is recorded as a tie instead.
                # Counting it as a win is how a 0.1-point edge, repeated, turns
                # into "this model wins 60% of the time".
                if narrow:
                    row.ties += 1
                else:
                    row.wins += 1

        # A cloud that failed did not produce a draft, so it has no row to
        # attribute the failure to unless one already exists. Recorded against
        # the participant name, which is the only identity a failed leg has.
        for name in run.failures:
            for row in rows.values():
                if row.cloud == name:
                    row.failures += 1

    audit.rows = sorted(rows.values(), key=lambda row: (-(row.win_rate or 0), row.key))
    return audit


def render(audit: Audit, *, min_runs: int = DEFAULT_MIN_RUNS) -> str:
    if not audit.rows:
        return (
            f"no model-backed runs recorded "
            f"({audit.total_runs} run(s) read, {audit.direct_runs} of them direct-brain)\n"
            f"Run the mesh with RESEARCH_MODEL_MODE=llm on the agents before "
            f"expecting a comparison."
        )

    lines = [
        f"model audit  ({audit.total_runs} runs recorded, "
        f"{audit.total_runs - audit.direct_runs} with a model in the path)",
        "",
    ]

    header = f"{'cloud/model':<34}{'runs':>5}{'wins':>6}{'ties':>6}{'win%':>7}{'score':>8}{'ms':>9}"
    lines += [header, "-" * len(header)]

    for row in audit.rows:
        if row.runs < min_runs:
            lines.append(
                f"{row.key:<34}{row.runs:>5}{'':>6}{'':>6}"
                f"{'  withheld':>7}{'':>8}{'':>9}"
            )
            continue
        lines.append(
            f"{row.key:<34}{row.runs:>5}{row.wins:>6}{row.ties:>6}"
            f"{row.win_rate * 100:>6.0f}%"
            f"{row.mean_total:>8.1f}"
            f"{row.mean_latency_ms:>9.0f}"
        )

    reported = [row for row in audit.rows if row.runs >= min_runs]
    if reported:
        lines += ["", "mean score by dimension (0-5 each)", ""]
        dim_header = f"{'cloud/model':<34}" + "".join(f"{d[:9]:>11}" for d in RUBRIC_DIMENSIONS)
        lines += [dim_header, "-" * len(dim_header)]
        for row in reported:
            cells = "".join(
                f"{(row.mean_dimension(d) or 0):>11.1f}" for d in RUBRIC_DIMENSIONS
            )
            lines.append(f"{row.key:<34}{cells}")

    lines += ["", *_caveats(audit, min_runs)]
    return "\n".join(lines)


def _caveats(audit: Audit, min_runs: int) -> list[str]:
    """The part of the report that keeps it from being over-read.

    Printed every time, including when everything is fine. A caveat block that
    only appears when something is wrong trains the reader to skip it.
    """
    notes = [
        "notes:",
        f"  - rows with fewer than {min_runs} runs are withheld, not hidden: "
        f"a win rate over one run is not a win rate",
        "  - direct-brain drafts are excluded; they are canned text, identical on every cloud",
        "  - this compares these models as configured here -- one prompt, no tools, "
        "one judge. It is not a general benchmark",
    ]
    if audit.narrow_wins:
        notes.append(
            f"  - {audit.narrow_wins} run(s) were decided by less than a point and are "
            f"counted as ties rather than wins"
        )
    if audit.mixed_judges:
        detail = ", ".join(f"{judge}={count}" for judge, count in sorted(audit.judges.items()))
        notes.append(
            f"  - WARNING: these runs were scored by more than one judge ({detail}). "
            f"The rubric and a model do not share a scale, so these rows are not comparable"
        )
    if audit.mixed_rubrics:
        detail = ", ".join(f"v{v}={c}" for v, c in sorted(audit.rubric_versions.items()))
        notes.append(
            f"  - WARNING: mixed rubric versions ({detail}). Scores from different "
            f"rubrics have been averaged together and should not be"
        )
    if audit.direct_runs == audit.total_runs and audit.total_runs:
        notes.append("  - every recorded run was direct-brain, so nothing here is a model result")
    return notes


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Aggregate recorded research runs by model")
    parser.add_argument("--store", type=Path, default=None, help="path to runs.jsonl")
    parser.add_argument("--min-runs", type=int, default=DEFAULT_MIN_RUNS)
    parser.add_argument("--json", dest="json_path", help="also write the aggregate as JSON")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    audit = aggregate(list(load(args.store)))
    print(render(audit, min_runs=args.min_runs))

    if args.json_path:
        payload = {
            "total_runs": audit.total_runs,
            "direct_runs": audit.direct_runs,
            "judges": audit.judges,
            "rubric_versions": {str(k): v for k, v in audit.rubric_versions.items()},
            "rows": [
                {
                    "cloud": row.cloud,
                    "model": row.model,
                    "runs": row.runs,
                    "wins": row.wins,
                    "ties": row.ties,
                    "failures": row.failures,
                    "win_rate": row.win_rate,
                    "mean_total": row.mean_total,
                    "mean_latency_ms": row.mean_latency_ms,
                    "dimensions": {
                        dimension: row.mean_dimension(dimension)
                        for dimension in RUBRIC_DIMENSIONS
                    },
                    "reported": row.runs >= args.min_runs,
                }
                for row in audit.rows
            ],
        }
        Path(args.json_path).write_text(json.dumps(payload, indent=2))
        print(f"\nwrote {args.json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
