#!/usr/bin/env bash
# The whole thing, one command.
#
#   ./infra/demo.sh
#
# Four acts, in the order the claims depend on each other:
#
#   1. three native agents answer one brief and are ranked
#   2. the 3x3 interop matrix -- every client SDK against every serving stack
#   3. a cloud goes offline and the run degrades instead of failing
#   4. a cloud phones it in and the judge declines to pick it
#
# Acts 3 and 4 are the point. Any demo can show three green ticks; the claim
# this project actually makes is about what happens when one participant is
# bad, and that is only worth anything if you watch it happen.
#
# LOCAL, direct-brain. This is a protocol and orchestration demo, not a model
# comparison -- nothing here crosses a cloud boundary, the latencies are
# loopback, and NO MODEL IS IN THE PATH, so the rankings below rank canned
# text. See docs/DEPLOYMENT_PLAN.md for what deployment adds.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-python3}"
MESH="$REPO/infra/run_mesh.sh"

# The demo must never pollute the audit store: these runs are direct-brain and
# would be recorded as data. --no-record on every invocation below, and a
# throwaway store as a second line of defence.
export RESEARCH_EVAL_STORE="${TMPDIR:-/tmp}/research-demo-runs.jsonl"

# ADK emits [EXPERIMENTAL] warnings on every call (finding 3) to stderr.
# Drop stderr rather than filtering stdout: the per-participant lines are
# indented, so any leading-whitespace filter eats the actual result.
run() { "$@" 2>/dev/null; }

rule() { printf '\n\033[1m%s\033[0m\n%s\n' "$1" "$(printf '─%.0s' $(seq 1 68))"; }

cleanup() { "$MESH" stop >/dev/null 2>&1 || true; }
trap cleanup EXIT

cleanup
rule "Starting three native agents"
"$MESH" start 2>&1 | grep -E 'starting|ready'
"$MESH" status

BRIEF="how agent-to-agent protocols change multi-cloud architecture"

rule "1. Three clouds, one brief"
echo "Google ADK, AWS Strands, Azure Agent Framework -- three vendors'"
echo "frameworks, three serving stacks, one brief, three drafts, one winner."
echo
run "$PYTHON" -m coordinator.cli "$BRIEF" \
  --question "what does A2A replace?" --no-record

rule "2. The interop matrix"
echo "Three client SDKs x three natively-served agents. Every cell is a real"
echo "A2A call; a failure records which layer broke."
echo
run "$PYTHON" -m matrix.runner

rule "3. A cloud goes offline"
echo "Killing the AWS agent. A lost participant degrades the field instead of"
echo "failing the run -- and the failure names its layer."
echo
"$MESH" kill aws
sleep 1
run "$PYTHON" -m coordinator.cli "$BRIEF" \
  --question "what does A2A replace?" --no-record

rule "4. A cloud phones it in"
echo "Restarting AWS in degraded mode: it returns fluent padding that answers"
echo "nothing -- which is what phoning it in actually looks like, not a"
echo "one-liner. The judge scores it last rather than being told to skip it."
echo
"$MESH" stop >/dev/null 2>&1
RESEARCH_DRAFT_DEGRADE_AWS=1 "$MESH" start >/dev/null 2>&1
sleep 1
run "$PYTHON" -m coordinator.cli "$BRIEF" \
  --question "what does A2A replace?" --no-record

rule "What this demo does not show"
cat <<'EOF'
  - NO MODEL RAN. Every agent above is in `direct` mode, returning canned text
    assembled from the brief. The ranking in act 1 is therefore a ranking of
    scaffolding, and the only act that means anything is 4, where the
    difference between the drafts is real. `RESEARCH_MODEL_MODE=llm` on the
    agents puts Gemini, Bedrock and Foundry in the path; that needs each
    cloud's credentials.
  - Nothing in THIS SCRIPT is deployed. All three agents here are local and no
    measurement above crosses a cloud boundary.
  - Latencies are loopback and direct-brain. They measure protocol and
    framework overhead, nothing else.
  - One run is not an evaluation. `python -m evaluations.report` aggregates
    recorded runs and withholds any row with fewer than five behind it.
EOF
echo
