# Three clouds, three research agents, one judge

Three **native agents from Google Cloud, AWS, and Azure** — each built with its
own vendor's agent framework, each served over A2A v1.0 by its own vendor's
stack, each running its own vendor's model — are given the same research brief.
They write independently. A judge reads all three blind, scores them against a
fixed rubric, and names a winner. Every run is appended to an audit that
compares the models over time.

The front door is on Google. A person opens a page, types a brief, and one
Cloud Run service fans it out, collects the drafts and judges them. That
service runs on Cloud Run because that is what makes the whole mesh keyless: it
is the only runtime here proven to mint workload OIDC tokens with an arbitrary
audience, so every outbound leg is federated rather than holding a stored
secret.

```text
                     you, in a browser
                          |
          ....................................
          :  master   (Cloud Run, us-central1) :
          :    front end                       :   deployed from source.
          :    fan-out                         :   no Dockerfile, no image
          :    judge, in-process               :   recipe -- Procfile only
          :.................................. :
                          |
          +---------------+---------------+
          | A2A v1.0      | A2A v1.0      | A2A v1.0
          | ID token      | SigV4         | Entra token
          | IN-CLOUD HOP  | cross-cloud   | cross-cloud
          v               v               v
    Google Cloud      AWS               Azure
    ADK LlmAgent      Strands Agent     Agent Framework Agent
    Gemini            Bedrock           Foundry deployment
    served by         served by         served by
    to_a2a()          a2a-sdk routes    A2AExecutor
    on Cloud Run      on AgentCore      on Container Apps
    us-central1       us-west-2         westus2
          \               |               /
           \______ three drafts, one brief ______/
                          |
                    judge, blind
                          |
              winner + per-dimension scores
                          |
              evaluations/  (append-only, GCS)
```

**The judge runs inside the master, not as a fourth agent.** It has to see all
three drafts before it can rank them, so it is the one step that cannot begin
until the slowest cloud has answered — and a network hop there would add a leg
that fails *after* every expensive call has already succeeded. It is a pure
function of the drafts and does not need its own address.

**The master is not an agent, and nothing here is a subagent** despite the
repository's name. The fan-out is unconditional: every cloud gets a
byte-identical brief on every run. That is what makes the audit mean anything —
hand routing to a model and it may ask two of three, or rephrase the brief per
cloud, and `evaluations/` stops measuring the models and starts measuring the
router.

## Status: read this before anything else

**This is a refactor in progress, and none of the new design has been
deployed.** The repo previously ran a currency-conversion mesh with median
consensus; that version was deployed on all three clouds and measured
(2026-08-07 to 2026-08-09). The research/judge/audit architecture above
replaced it on 2026-08-12 and has only ever run locally.

| | built | tested | run locally | deployed | measured |
|---|---|---|---|---|---|
| Master service — fan-out + judge over HTTP | yes | yes | yes | **no** | n/a |
| Master service — front end page | yes | served, not rendered | served | **no** | n/a |
| Containerless deploy (`Procfile`, source build) | yes | **cannot be** | n/a | **no** | n/a |
| Three research agents, `direct` brain | yes | yes | yes | **no** | n/a |
| Three research agents, `llm` brain | yes | construction only | **no** | **no** | **no** |
| Judge — deterministic rubric | yes | yes | yes | **no** | n/a |
| Judge — model | yes | failure paths only | **no** | **no** | **no** |
| Audit / report | yes | yes | yes (refusing) | **no** | **no** |
| Cross-cloud federation (`coordinator/auth.py`) | unchanged | yes | n/a | **stale** | was, on the old code |

Two rows deserve their wording. **"served, not rendered"**: the page is
returned by the service and its script parses, and nobody has opened it in a
browser — a page can do both of those and still lay out wrong. **"cannot be"**:
no local test can tell you whether the Python buildpack starts
`coordinator.service:app` from the `Procfile` on Cloud Run, and of everything
listed here that is the single most likely thing to be broken on first deploy.

"Stale" is the important row. The three federation paths are untouched and were
proven end to end with negative controls under the currency mesh, but the code
around them changed, and nothing has been redeployed since. Treat the keyless
claim as **previously demonstrated, not currently demonstrated**.

Nothing below is a model comparison. Every number on this page was produced by
`direct`-brain agents returning canned text.

## What replaced the median, and what that cost

The currency mesh reconciled three answers with a **median**. That was
arithmetic: a cloud that diverged was *wrong*, and no single cloud could move
the result. Drafts cannot be reconciled that way, so a judge scores them
instead — and the replacement is weaker in ways worth stating plainly:

- A median is a fact. A verdict is one scorer's opinion on one brief.
- The deterministic rubric **measures form, not truth**: headings, figures,
  citation markers, length. A confidently wrong draft in tidy markdown beats a
  hedged correct one, and the rubric cannot tell.
- One run establishes nothing. This is why `evaluations/` exists and why it
  withholds any row with fewer than five runs behind it.

Two things are done to stop the judge quietly manufacturing a result:

**Blind, with rotating labels.** Drafts are relabelled before scoring, and the
label assignment rotates with the topic. A fixed alphabetical assignment would
put the same cloud at position A in every run forever, so any positional bias
in the judge — the normal case, not a pathological one — would be recorded by
the audit as a property of that vendor's model.

**Narrow wins are ties.** A win inside one point of 25 is counted as a tie, not
a win. Otherwise a 0.1-point edge, repeated, becomes "this model wins 100% of
the time".

## The demo

```console
$ ./infra/demo.sh

1. Three clouds, one brief          three vendors' frameworks, three drafts
2. The interop matrix               3 client SDKs x 3 hosted agents
3. A cloud goes offline             the run degrades instead of failing
4. A cloud phones it in             the judge ranks it last
```

Act 4 is the one that means anything, because it is the only act where the
drafts genuinely differ:

```console
winner: azure  [3/3 clouds, judge=rubric, blind]

  * 1. azure   13.3/25  none    98w    20ms
      cove 5.0  spec 3.2  evid 0.0  stru 3.5  conc 1.6
    2. gcp     13.3/25  none    98w   163ms
      cove 5.0  spec 3.2  evid 0.0  stru 3.5  conc 1.6
    3. aws      4.1/25  none   127w    20ms
      cove 0.0  spec 2.0  evid 0.0  stru 0.0  conc 2.1

  warning: winner is ahead by only 0.00 of 25 points; treat this as a tie
```

In acts 1–3 all three clouds return byte-identical canned text, so the winner
is a latency tie-break and the run says so. That warning is the feature.

## The matrix

```console
$ ./infra/run_mesh.sh start
$ python3 -m matrix.runner

A2A interop matrix  (the A2A protocol and why agents need one (<=300w), brain=direct)

client \ server  gcp               aws               azure
-----------------------------------------------------------------------
a2a-sdk          ok 134ms          ok 8ms            ok 8ms
agent-framework  ok 129ms          ok 7ms            ok 8ms
google-adk       ok 920ms          ok 9ms            ok 10ms

9/9 attempted cells succeeded
```

Local, loopback, direct-brain, single runs — they order the stacks and nothing
more. Every cell is one real A2A call, and a failed cell records which layer
broke (`transport`, `protocol`, `timeout`, `authentication`, `provider`) rather
than just failing.

The `provider` kind earns its keep in this domain in a way it did not before: a
model that declines the topic is a provider outcome, and filing it as
`protocol` would turn "Bedrock refused" into "AgentCore broke A2A".

**How independent the axes are.** Unchanged from the currency version and still
worth stating: the servers are two stacks, not three (`agents/aws/server.py`
and `agents/azure/server.py` both build on `agents/serving.py`, differing only
in executor; only ADK's `to_a2a()` is separate), and all three client stacks
resolve to the same `a2a-sdk` wire implementation. Nine cells is a
presentation, not nine independent experiments.

## Interop finding from this refactor

**ADK's `to_a2a()` returns the same reply twice** — once as a task artifact and
once in task history. `clients/a2a_sdk.py` reads every carrier the spec allows,
because it has to: Agent Framework's `A2AExecutor` leaves the reply *only* in
history with artifacts empty, so a client that reads artifacts alone gets an
empty string from Microsoft. Reading both and concatenating gets the draft
twice from Google.

Under the currency domain this was invisible: the parser indexed quotes by
target currency, so the duplicate silently overwrote its twin and the answer
was correct. Under a research draft the body doubles, the word count doubles,
and `concision` scores a compliant draft as a 100% overrun. Found by running
the mesh and noticing GCP returned 202 words of text the other two returned in
98 — **not by any test**, and the local suite was green throughout.

Fixed by deduplicating in `_task_texts`: one reply carried in two envelopes is
one reply. There is now a live test asserting all three serving stacks return
the same canned text at the same length, which is the cheapest detector for the
whole class.

## Two brains

Every agent runs one of two ways, set by `RESEARCH_MODEL_MODE`:

- **`direct`** (default) — a canned draft assembled from the brief. No model,
  no credentials, no upstream. A failed matrix cell is then unambiguously a
  protocol failure. **Not an evaluation**: the canned text is identical on all
  three clouds, so the judge ranks three identical drafts and says so.
- **`llm`** — the cloud's native model through its native framework: Gemini via
  ADK, Bedrock via Strands, a Foundry deployment via Agent Framework. Requires
  that cloud's credentials. **Never run.**

`Draft.brain` travels with every draft, and `evaluations/report.py` excludes
anything that is not `llm`. Averaging canned text into a model's score would
manufacture a result out of scaffolding.

## No tools, deliberately

None of the three agents gives its model a tool. The currency agent needed one
because a rate is a lookup; a research brief is the model's own output, and
giving one cloud a search tool the others lack would make the audit a
comparison of tool access rather than of models. There is a test asserting no
`tools=` appears in any of the three builders.

If search is added later it has to be added to all three, and the audit's
history has to be cut at that point.

## The audit

```console
$ python3 -m evaluations.report

model audit  (12 runs recorded, 12 with a model in the path)

cloud/model                        runs  wins  ties   win%   score       ms
--------------------------------------------------------------------------
gcp/gemini-2.5-flash                 12     7     2    58%    18.4     4210
aws/us.amazon.nova-micro-v1:0        12     2     2    17%    12.1     2260
azure/gpt-5-mini                     12     3     2    25%    16.8     5980
```

**That table is an illustration of the format, not a result.** No `llm` run has
ever been recorded. Run it today against this repo's own store and it prints
`no model-backed runs recorded`, which is the correct answer.

The report withholds any row with fewer than five runs, counts narrow wins as
ties, warns when runs were scored by more than one judge (the rubric and a
model do not share a scale), and prints its caveats every time rather than only
when something is wrong — a caveat block that appears only on failure trains
the reader to skip it.

`BEDROCK_MODEL_ID` defaults to Nova micro, which was chosen when the task was a
two-field lookup and is a poor default for drafting prose. Rows are keyed on
`cloud/model`, so changing it starts a new row rather than pooling two models
into one.

## Where the judge sits, and the bias that creates

The judge runs on ADK alongside the coordinator, on Cloud Run, because that is
where a Gemini call needs no stored credential. **This means the judge shares a
vendor with one of the three participants.** That is a real bias risk, it is
recorded on every verdict via the `judge` field, and it is not argued away.
Moving the judge to a fourth deployed agent on a neutral cloud is the obvious
mitigation and has not been done.

The blind labelling and the rotating positions address a different bias
(position), not this one.

## Setup

Requires Python 3.13 and `uv`.

```bash
uv pip install --system \
  "a2a-sdk[http-server]" google-adk \
  agent-framework-a2a agent-framework-core \
  pydantic httpx starlette uvicorn pytest pytest-asyncio
uv pip install --system -e .
```

`requirements.txt` is the same list minus the test tools, and exists because
the Cloud Run buildpack reads it and does not read `pyproject.toml`.

Latest of everything, no virtualenv — see `CLAUDE.md`. Nothing is pinned. The
last pin, `mcp<2`, left with the MCP scaffolding it existed for.

`strands-agents` is needed only for the AWS agent's `llm` mode; every other
path runs without it.

## Run

```bash
./infra/run_mesh.sh start        # three agents on :10001 :10002 :10003

python3 -m uvicorn coordinator.service:app --port 8099   # the master, at /

python3 -m matrix.runner --json report.json
python3 -m coordinator.cli "solid-state batteries in 2026" \
    --question "who ships at scale?" --show-drafts
python3 -m evaluations.report
./infra/run_mesh.sh stop
```

The service and the CLI are two front ends on one mesh, not two meshes: both
build their participants through `coordinator.participants.build_participants`,
so they cannot disagree about which clouds are wired or how a leg
authenticates. The CLI is still the right shape for a scheduled, recorded run
and a poor one for a person with a question.

Local, against the local mesh, all three legs `direct`:

```console
$ curl -s -XPOST localhost:8099/api/research -H 'content-type: application/json' \
    -d '{"topic":"solid-state batteries in 2026","questions":["who ships at scale?"]}'

winner: azure  [3/3 clouds, judge=rubric, blind]
  1. azure  15.0/25  brain=direct  92w   13ms
  2. aws    15.0/25  brain=direct  92w   18ms
  3. gcp    15.0/25  brain=direct  92w  162ms
warning: winner is ahead by only 0.00 of 25 points; treat this as a tie
elapsed 163ms
```

Three identical canned drafts, ranked by latency, and the run says so. Elapsed
tracks the slowest leg rather than their sum, which is the fan-out working.
Not an evaluation, and the page refuses to present it as one: any draft whose
`brain` is not `llm` puts a banner above the result saying this is not a model
comparison.

Tests are hermetic by default; the live suite skips itself unless the mesh is
up, and the duplicate-reply test skips itself if any agent is running degraded.

```bash
python3 -m pytest tests/ -q     # 183 passed with the mesh up, 168 without
```

## Deployed

**Nothing in this architecture is deployed yet.** The AWS and Azure scripts are
carried over from the currency mesh with their environment variables renamed
(`RESEARCH_MODEL_MODE`, `RESEARCH_COORDINATOR_CLOUD`) and have not been run
since. `deploy_gcp.sh` is new work on top of them: it now deploys the master
and it deploys from source.

```bash
./infra/deploy_aws.sh   deploy   # AgentCore Runtime + the federated role
./infra/deploy_azure.sh deploy   # Container App
./infra/deploy_azure.sh fic      # Entra app registration + FIC on Google's issuer
./infra/deploy_azure.sh auth     # make the ingress demand it

./infra/deploy_gcp.sh deploy     # master + GCP researcher + jobs, one source build
./infra/deploy_gcp.sh wire       # fold the AWS and Azure legs into both
./infra/deploy_gcp.sh open       # grant yourself run.invoker, print the proxy command
./infra/deploy_gcp.sh run        # one brief, three clouds, unattended
./infra/deploy_gcp.sh matrix     # the 3x3, every client against every hosted server
./infra/deploy_gcp.sh verify     # the negative controls
```

**Containerless, and specifically what that cost.** There is no Dockerfile at
the repo root any more. `gcloud run deploy --source` prefers a root Dockerfile
when it finds one, so the GCP researcher's recipe sitting there would silently
turn every source deploy into a container build of the wrong process; it lives
at `infra/Dockerfile.gcp` now as a fallback nothing reads. The buildpack reads
`requirements.txt` and not `pyproject.toml`, so GCP dependencies are declared
in two places by necessity. Only the master is built; the researcher and both
jobs deploy *the image that build produced*, read back off the master by
digest, so "the researcher runs the same code as the master" is a fact about
the deployment rather than a claim about the repo.

**The front end is private.** `--no-allow-unauthenticated`, like everything
else here, reached through `gcloud run services proxy`. It holds credentials
for three clouds and is the only surface a person is meant to open, which is
exactly the combination that gets something made public to try it once.
`PUBLIC=1` overrides and says so on stderr; `verify` checks the page 403s.

**The audit lives in GCS.** Cloud Run's filesystem does not outlive the
instance, so `evaluations/` is a bucket mounted at `/eval` and the service is
pinned to one instance — a GCS volume write is a whole-object rewrite, not an
append, so two concurrent runs would lose one of themselves. `destroy` leaves
the bucket behind deliberately.

The `currency-*` cloud resource names are deliberately **not** renamed. They
are deployed identities, not labels: the coordinator service account's numeric
subject is pinned in the AWS role's trust policy and in the Entra federated
credential, so renaming it means re-provisioning federation on two other
clouds. The names are now wrong about what the mesh does, which is the cheaper
of the two problems.

`verify` is the part worth running twice, and it is now **overdue**: every leg
is probed alone, because the mesh degrades on purpose and a run with one
credential removed still reaches a verdict on the other two and exits 0.

## What carried over unchanged

The parts the currency domain was scaffolding for:

| Layer | Module |
|---|---|
| One credential seam for three clouds | `coordinator/auth.py` |
| Cloud-agnostic participant interface | `coordinator/participants.py` |
| Concurrent fan-out with per-leg failure isolation | `coordinator/mesh.py` |
| Three client stacks | `clients/` |
| Interop matrix | `matrix/` |

`coordinator/auth.py` still mints a Google ID token, an STS
`AssumeRoleWithWebIdentity` exchange into SigV4, and an Entra federated
exchange, behind a single `httpx.Auth` attached to the client so the agent-card
fetch is authenticated too. It still logs the raw provider response at every
auth boundary — the decision that eventually explained the AgentCore
least-privilege question, because AWS had been naming the missing action in the
response body all along.

See [`docs/INTEROP.md`](docs/INTEROP.md) for the measured findings, most of
which predate this refactor and are labelled with the dates they were taken.
[`docs/FOLLOWUP.md`](docs/FOLLOWUP.md) is a critical read of what the currency
version demonstrated versus what it claimed; its structural criticisms of the
matrix axes apply unchanged.

## Not done

- **Nothing is deployed.** The whole table at the top of this file. The
  buildpack path in particular has never been exercised: the `Procfile`
  entrypoint, the `--command` override that turns the same image into the
  researcher, the GCS volume mount, and reading the built image back off the
  master by digest are four things that can only be found broken by running
  `./infra/deploy_gcp.sh deploy`.
- **Nobody has opened the front end in a browser.** It is served, its script
  parses, and every field it reads is asserted in `tests/test_service.py`. That
  is not the same as it looking right.
- **No model has ever written a draft here.** `llm` mode is built on all three
  clouds and has been constructed in a test; it has not answered a brief.
- **The model judge has never judged.** Its failure paths are covered — an
  unreadable verdict, a raising judge, a partial verdict that would drop a
  participant, a judge contradicting its own scores — and all four fall back to
  the deterministic rubric. The success path has never run against a model.
- **The rubric is unvalidated.** Its weightings and thresholds — eight
  specifics per hundred words for full marks, five citation markers, the
  asymmetric length penalty — were chosen by argument, not calibration. Nobody
  has checked that rubric rank correlates with human rank on even one set of
  drafts.
- **The judge sits on one participant's cloud.** See above.
- **`docs/DEPLOYMENT_PLAN.md` and `docs/ARTICLE_PLAN.md` still describe the
  currency mesh.** They are accurate about what was deployed and stale about
  what this repo now is.
