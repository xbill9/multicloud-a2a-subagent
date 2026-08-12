"""Append-only record of every judged run.

One JSON object per line, never rewritten. The append-only shape is the point:
an audit whose history can be edited in place is not an audit, and the failure
mode it protects against is the ordinary one -- re-running until the result
looks better and keeping only the last run.

Deliberately a file rather than a database. The runs are small, the aggregate
is computed on read, and a file can be committed alongside the code that
produced it, which is what makes a published number checkable by someone else.
"""

import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from coordinator.models import ResearchRun

DEFAULT_STORE = Path(".eval/runs.jsonl")


def store_path() -> Path:
    """Where runs are recorded; ``RESEARCH_EVAL_STORE`` overrides the default."""
    return Path(os.getenv("RESEARCH_EVAL_STORE", str(DEFAULT_STORE)))


def record(run: ResearchRun, *, path: Path | None = None, recorded_at=None) -> Path:
    """Append one run. Returns the file written to.

    The timestamp is stamped here rather than in ``ResearchRun`` because it
    describes the *recording*, not the run: two identical runs recorded on
    different days must be distinguishable in the aggregate, and a run that was
    never recorded should carry no timestamp claiming otherwise.
    """
    target = path or store_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "recorded_at": (recorded_at or datetime.now(UTC)).isoformat(),
        "run": run.model_dump(mode="json"),
    }
    with target.open("a") as handle:
        handle.write(json.dumps(payload) + "\n")
    return target


def load(path: Path | None = None) -> Iterator[tuple[datetime, ResearchRun]]:
    """Every recorded run, oldest first.

    Unreadable lines are skipped rather than raised on. A store is appended to
    by long-running jobs that can be killed mid-write, and one truncated final
    line must not make the entire history unreadable -- losing the last run is
    recoverable, losing all of them is not.
    """
    target = path or store_path()
    if not target.exists():
        return
    with target.open() as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
                recorded_at = datetime.fromisoformat(payload["recorded_at"])
                yield recorded_at, ResearchRun.model_validate(payload["run"])
            except Exception:  # noqa: BLE001, PERF203 - a torn line must not kill the read
                continue
