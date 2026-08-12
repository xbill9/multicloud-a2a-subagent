"""Send one brief to all three clouds, judge the drafts, record the run.

    research-mesh "the state of solid-state batteries in 2026"
    research-mesh "..." --question "who ships at scale?" --judge llm
    research-mesh "..." --client agent-framework --json

Each cloud is reached over A2A through whichever client stack is selected, so
this is also the end-to-end demonstration that three native agents -- Google
ADK, Strands on Bedrock, Agent Framework on Foundry -- answer one brief from
one coordinator, each on its own vendor's model.
"""

import argparse
import asyncio

from pydantic import ValidationError

from clients import CLIENT_STACKS
from coordinator.judge import JUDGE_MODES, load_judge
from coordinator.mesh import ResearchMesh
from coordinator.models import ResearchRequest
from coordinator.participants import CLOUD_ENDPOINTS, Participant, build_participants
from evaluations.store import record


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Send one research brief to three clouds and judge the drafts"
    )
    parser.add_argument("topic", help="what the brief should be about")
    parser.add_argument(
        "--question",
        action="append",
        dest="questions",
        default=[],
        help="a focus question every draft must answer (repeatable)",
    )
    parser.add_argument("--max-words", type=int, default=600)
    parser.add_argument(
        "--cloud",
        action="append",
        choices=list(CLOUD_ENDPOINTS),
        help="restrict to one cloud (repeatable); default is all three",
    )
    parser.add_argument(
        "--client",
        choices=list(CLIENT_STACKS),
        default="a2a-sdk",
        help="A2A client stack used to reach every cloud",
    )
    parser.add_argument(
        "--judge",
        choices=list(JUDGE_MODES),
        default=None,
        help="rubric (deterministic, default) or llm; also RESEARCH_JUDGE_MODE",
    )
    parser.add_argument(
        "--no-record",
        action="store_true",
        help="do not append this run to the evaluation store",
    )
    parser.add_argument("--show-drafts", action="store_true", help="print every draft in full")
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser


def participants_for(args) -> list[Participant]:
    return build_participants(
        args.cloud,
        client=args.client,
        timeout_seconds=args.timeout_seconds,
    )


def render(run, *, show_drafts: bool = False) -> str:
    # Annotate only the authenticated legs, so the all-local run reads exactly
    # as it did before the auth seam existed.
    named = [
        f"{name} ({mode})" if (mode := run.auth_modes.get(name, "none")) != "none" else name
        for name in run.participants
    ]
    lines = [f"participants: {', '.join(named)}", f"brief: {run.request.topic}", ""]

    verdict = run.verdict
    if verdict is None or not run.drafts:
        lines.append("no cloud returned a draft")
    else:
        lines.append(
            f"winner: {verdict.winner}  "
            f"[{len(run.drafts)}/{len(run.participants)} clouds, judge={verdict.judge}"
            f"{', blind' if verdict.blind else ''}]"
        )
        lines.append("")
        by_source = {draft.source: draft for draft in run.drafts}
        for entry in sorted(verdict.verdicts, key=lambda v: v.rank):
            draft = by_source.get(entry.source)
            model = draft.model if draft else "unknown"
            latency = f"{draft.latency_ms:.0f}ms" if draft else "-"
            words = f"{draft.word_count}w" if draft else "-"
            marker = "*" if entry.source == verdict.winner else " "
            lines.append(
                f"  {marker} {entry.rank}. {entry.source:<6} {entry.total:>5.1f}/25  "
                f"{model:<28} {words:>6} {latency:>8}"
            )
            lines.append(f"      {(draft.title if draft else '')[:88]}")
            scores = "  ".join(
                f"{score.dimension[:4]} {score.score:.1f}" for score in entry.scores
            )
            lines.append(f"      {scores}")
        if verdict.rationale:
            lines += ["", f"  {verdict.rationale}"]
        for warning in verdict.warnings:
            lines.append(f"  warning: {warning}")

    for name, failure in run.failures.items():
        lines.append(f"  {name} failure: {failure}")

    if show_drafts:
        for draft in run.drafts:
            lines += ["", "-" * 70, f"{draft.source} / {draft.model}", "-" * 70, draft.body]

    lines += ["", f"elapsed {run.elapsed_ms:.0f}ms"]
    return "\n".join(lines)


async def _run(args) -> int:
    try:
        request = ResearchRequest(
            topic=args.topic,
            questions=args.questions,
            max_words=args.max_words,
        )
    except (ValidationError, ValueError) as exc:
        print(f"invalid request: {exc}")
        return 2

    mesh = ResearchMesh(
        participants_for(args),
        judge=load_judge(args.judge),
        timeout_seconds=args.timeout_seconds,
    )
    run = await mesh.run(request)

    if not args.no_record:
        # Recorded before printing, so a run that produced a verdict is in the
        # audit even if rendering it fails. The store is the artifact; the
        # terminal output is a view of it.
        record(run)

    print(run.model_dump_json(indent=2) if args.as_json else render(run, show_drafts=args.show_drafts))
    return 0 if run.succeeded else 1


def main() -> int:
    return asyncio.run(_run(build_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
