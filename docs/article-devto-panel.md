---
title: A mixture of experts with no gate — three clouds write, one judge ranks
published: false
description: Three vendor-native agents on Google, AWS and Azure answer the same brief; a judge scores them blind against a fixed rubric. The design decisions that mattered were about the gate, the scorer and what the audit measures — not the protocol.
tags: ai, architecture, cloud, python
---

Three AI agents, each built with a different vendor's framework, each hosted by
that vendor, each running that vendor's own model, all answering the same
research brief at the same time:

- **Google** — an ADK `LlmAgent` on Cloud Run, Gemini, served by `to_a2a()`
- **AWS** — a Strands agent on Bedrock AgentCore, Nova, served by `a2a-sdk` routes
- **Azure** — an Agent Framework agent on Container Apps, a Foundry deployment,
  served by `A2AExecutor`

They write independently and never speak to each other. A judge reads all three
blind, scores them against a fixed rubric, and names a winner. Every run is
appended to an audit that compares the models over time.

```text
                     you, in a browser
                          |
            master  (Cloud Run, us-central1)
              front end / fan-out / judge, in-process
                          |
          +---------------+---------------+
          | A2A v1.0      | A2A v1.0      | A2A v1.0
          | ID token      | SigV4         | Entra token
          v               v               v
    Google Cloud      AWS               Azure
    ADK LlmAgent      Strands Agent     Agent Framework Agent
    Gemini            Bedrock           Foundry deployment
    us-central1       us-west-2         westus2
          \               |               /
           \____ three drafts, one brief ____/
                          |
                    judge, blind
                          |
              winner + per-dimension scores
                          |
              evaluations/  (append-only, GCS)
```

Everything is here:
[github.com/xbill9/multicloud-a2a-subagent](https://github.com/xbill9/multicloud-a2a-subagent).

The protocol was not the interesting part. A2A worked. Every decision that
turned out to matter was about the *shape of the panel*: whether anything routes,
who is allowed to judge, what the scorer can actually see, and what the audit is
measuring when nobody is watching it.

---

## "Mixture of experts" is the right analogy and the wrong mechanism

The phrase people reach for here is mixture of experts, and it is worth being
precise about which half of it applies.

In an MoE model, a **gate** picks which experts see the input. That is the whole
economic point: you own eight experts and pay for two. This system has the
experts and deliberately does **not** have the gate. The fan-out is
unconditional — every cloud gets a byte-identical brief on every run.

That is not an omission. It is the property the rest of the system is built on.

**Hand routing to a model and the audit stops measuring the models.** A router
that asks two clouds out of three, or rephrases the brief per cloud so each gets
the version it handles best, produces a nicer answer and a worthless record.
`evaluations/` would then be measuring the router — and the router is the one
component nobody would think to hold constant while comparing vendors.

There is a second reason, and it is the one that survives contact with
production. A gate is a component that can be wrong *silently*. When a bad
router drops the cloud that would have written the best draft, nothing anywhere
reports an error. The run is green, the winner is plausible, and the counterfactual
is unobservable. An unconditional fan-out has no such failure mode: either three
drafts came back or the run says how many did.

What it costs is honest and unglamorous: three model calls per brief minimum,
and more once the revision loop runs. You are buying a comparison, and a
comparison of three is three calls. If your goal is the cheapest good answer
rather than a defensible record of which vendor writes them, build the gate —
and then do not call the result an evaluation.

> **Worth noticing:** the repository is named `multicloud-a2a-subagent` and
> nothing in it is a subagent. The master does not decide anything; it fans out,
> collects and scores. The name is older than the design, which is a small
> example of the general problem this article keeps returning to — a label that
> was true once, still being read as a claim.

---

## The role boundary is decided by who can mint a token

Three roles, and none of them were assigned by preference:

| | role | why it is there |
|---|---|---|
| **Google** | judge, front end, fan-out, audit | the only runtime here *proven* to mint a workload OIDC token for an arbitrary audience, so it is the only member that can call out to the other two without a stored secret |
| **AWS** | researcher, and only a researcher | answers a brief with its own local Bedrock model, over A2A, and calls nobody |
| **Azure** | researcher, and only a researcher | answers a brief with its own Foundry deployment, over A2A, and calls nobody |

**A2A is the only glue.** The clouds meet on the wire and nowhere else.

That boundary is enforced by a test rather than described in a document, and the
reason is that the way it breaks is never a decision:

- `agents/common.py` imported `ResearchRequest` from `coordinator.models`. One
  line — and because a Python package has to be wholly present to import at all,
  both remote Dockerfiles copied the entire `coordinator` package. The judge, the
  front end and the three-cloud credential adapter were deployed to AWS and Azure,
  and nothing anywhere said so.
- The outbound A2A client stack was copied into both remote images and never
  imported by either.

None of that fails a suite, a deploy or a run. It is only visible if something
looks, so `tests/test_roles.py` looks: it imports each researcher module in a
subprocess with the coordinator packages made unimportable, and it greps both
remote Dockerfiles for the `COPY` lines that would smuggle them back in.

**Generalise it as: a role boundary that is not executable is a comment.** In a
multi-cloud mesh the cost of getting it wrong is not architectural tidiness — it
is a copy of your credential adapter sitting on someone else's infrastructure.

---

## The judge is not a fourth agent, and that is a design decision

The obvious symmetry is four agents: three writers and a critic, all reachable
over A2A. It is the wrong shape here, for two reasons.

**The judge is the barrier.** Every other step is per-leg and concurrent: one
cloud timing out degrades the run to the remaining clouds rather than failing it.
The judge cannot start until the slowest cloud has answered, because ranking
three drafts requires three drafts. It is the one place in the system where a
slow cloud delays the *result* rather than just its own leg.

**A hop there fails after every expensive call has already succeeded.** Three
model calls, three credential mints, three round trips across three vendors —
and then a network boundary in front of the one step that is a pure function of
data already in memory. The judge does not need an address. It needs the drafts.

Here is the wire timeline from a deployed three-cloud run, which is also the
clearest picture of why the barrier sits where it does:

```console
run  "how agent-to-agent protocols change multi-cloud architecture"
     3 leg(s): gcp, aws, azure   elapsed 6700ms

        at  leg      host                                         code     took
  --------  ------ - -------------------------------------------- ---- --------
    +637ms  gcp    K metadata.google.internal/computeMetadata/v1/  200    154ms
    +715ms  aws    K metadata.google.internal/computeMetadata/v1/  200    120ms
    +765ms  azure  K metadata.google.internal/computeMetadata/v1/  200     33ms
    +792ms  gcp    D research-gcp-...run.app/.well-known/agent-ca  200   5849ms
    +834ms  azure  K login.microsoftonline.com/40482c55-...        200    208ms
    +860ms  aws    K sts.us-west-2.amazonaws.com/                  200    221ms
   +1042ms  azure  D research-azure-...azurecontainerapps.io/      200   1545ms
   +1082ms  aws    I bedrock-agentcore.us-west-2.amazonaws.com/ru  200    666ms
   +1749ms  aws    I bedrock-agentcore.us-west-2.amazonaws.com/ru  200    164ms
   +2588ms  azure  I research-azure-...azurecontainerapps.io/      200     65ms
   +6643ms  gcp    I research-gcp-...run.app/                      200     55ms

  K credential   D agent-card discovery   I A2A invocation

  legs summed 9148ms, slowest leg 6060ms, run 6700ms
  -> the legs overlapped: the run cost about the slowest, not the sum.
```

Sorted by wall clock rather than grouped by leg, deliberately: grouped by leg,
three concurrent legs look exactly like three sequential ones.

The 5849ms is not the model and not the protocol. It is a scale-to-zero
service's **agent-card fetch** — cold start, on a service four minutes old. Its
actual invocation, once warm, took 55ms. A single elapsed figure per leg would
have filed that run as "GCP is slow" and the audit would have believed it.

---

## Two brains and two judges, because the pipeline has to run without a credential

Every researcher runs one of two ways, and so does the judge.

| | no model in the path | model in the path |
|---|---|---|
| **researcher** | `direct` — a canned draft assembled from the brief | `llm` — the cloud's native model through its native framework |
| **judge** | `rubric` — deterministic scoring | `llm` — Gemini 2.5 Pro reads all three drafts |

The credential-free halves are not toys and are not a fallback. They are what
makes the interop instrument mean anything: when a matrix cell fails in `direct`
mode, the failure is unambiguously in the protocol, because there is no model,
no upstream and no credential to blame. And a deterministic judge means the
**entire** revision loop — gate, critique, rewrite, re-judge — can be exercised
end to end with no model anywhere, which is the only way its failure modes get
tested before they happen in a deployed run.

The discipline that keeps the two apart is one field. `Draft.brain` travels with
every draft, and the audit excludes anything that is not `llm`. In `direct` mode
all three clouds return byte-identical canned text, so the judge is ranking three
identical drafts and the winner is a latency tie-break. Averaging that into a
model's score would manufacture a result out of scaffolding.

The run says so out loud rather than leaving it to be inferred:

```console
winner: azure  [3/3 clouds, judge=rubric, blind]

  * 1. azure   13.3/25  none    98w    20ms
    2. gcp     13.3/25  none    98w   163ms
    3. aws      4.1/25  none   127w    20ms

  warning: winner is ahead by only 0.00 of 25 points; treat this as a tie
```

That warning is the feature. A 0.1-point edge, repeated across an audit, becomes
"this model wins 100% of the time" — so any win inside one point of 25 is counted
as a tie, and any run without a model in it puts a banner above the result saying
this is not a model comparison.

---

## What the rubric can see, and what it cannot

Five dimensions, five points each:

| dimension | what it actually measures |
|---|---|
| coverage | term overlap with the brief's own focus questions |
| specificity | figures and proper nouns per 100 words |
| evidence | citation-*shaped* markers — URLs, `[1]`, "according to" |
| structure | an H1, headings, list items |
| concision | landing inside the word budget, overrun penalised harder |

Read that column honestly and the rubric **measures form, not truth**. A
confidently wrong draft in tidy markdown beats a hedged correct one, and the
rubric has no way to notice. Every module in the repo that touches it says so;
the `evidence` docstring reads *"counts the gesture, not the source."*

This matters more than a caveat, because a scorer that measures form will be
gamed by anything that changes form for unrelated reasons. It happened here:

**ADK's `to_a2a()` returns the same reply twice** — once as a task artifact and
once in task history. The client reads every carrier the spec allows, because it
has to: Agent Framework's `A2AExecutor` leaves the reply *only* in history, so a
client that reads artifacts alone gets an empty string from Microsoft. Read both
and concatenate, and Google's draft arrives doubled.

In the predecessor mesh — three clouds returning a currency rate — this was
invisible: the parser indexed quotes by target currency and the duplicate
silently overwrote its twin, so the answer was correct. Under a research draft
the body doubles, the word count doubles, and `concision` scores a compliant
draft as a 100% overrun.

It was found by running the mesh and noticing one cloud returned 202 words of
text the other two returned in 98. **Not by any test.** The suite was green
throughout. There is now a live test asserting all three serving stacks return
the same canned text at the same length, which is the cheapest available
detector for the whole class.

> **The general form:** when your scorer is a proxy for quality, every transport
> bug upstream of it arrives disguised as a quality difference. The currency
> domain had a parser that could not express the defect. The essay domain has a
> scorer that reports it as the model being verbose.

---

## Blind, and rotated

Drafts are relabelled `A`, `B`, `C` before scoring, and the label assignment
rotates with the topic.

Blind is the obvious half. The rotation is the half that gets skipped, and
skipping it is expensive in a way that only shows up months later. Assign labels
alphabetically and the same cloud sits at position A in every run forever — so
any positional bias in the judge, which is the normal case and not a pathological
one, gets faithfully recorded by the audit as a property of that vendor's model.
The assignment is deterministic on the topic, so a disputed verdict can still be
reproduced exactly; it just is not *constant*.

There is one bias the blinding does not touch, and it is not argued away: **the
judge shares a vendor with one of the three participants.** It runs on ADK
alongside the coordinator because that is where a Gemini call needs no stored
credential. It is recorded on every verdict via the `judge` field. Moving it to a
fourth deployed agent on a neutral cloud is the obvious mitigation and has not
been done.

---

## The loop, and the result I withdrew

Scoring three drafts and stopping is a leaderboard. The system does one more
thing: a draft below **18 of 25** is sent back to the cloud that wrote it, with a
critique built from its own three weakest dimensions, and rewritten. Up to three
rounds.

Two details are load-bearing:

**The critique is built from the scores, not free text.** That is what lets the
deterministic rubric drive the loop with no model in the path. When the model
judge runs, its per-draft `notes` are *appended* to the dimension scores rather
than replacing them — better feedback, and also the part that can be absent,
wrong or flattering.

**The gate is per cloud, not global.** Only the failing drafts go back. A mesh
where one model nailed it first time and another needed three attempts is exactly
the signal worth keeping, and a global pass/fail throws it away. Sending back
only the failures also means the passing draft is not rewritten into something
worse, which a revise-everything loop does surprisingly often.

Now the retraction, because it is the most useful thing in this section.

This project published a result: on a deployed three-cloud run, Gemini's first
draft scored 13.8, was sent back with a critique naming its weakest dimensions,
and returned at 21.1 — which changed the winner. It read as a clean demonstration
that the loop improves drafts.

**Draft versions were not being stored on the day that run happened.** What round
one actually contained cannot be recovered. Of the three later rewrites that
*can* be inspected, two began from a 31-word Vertex `429` error that had been
scored as a draft — 7.97 of 25 — and "improved" to 23 by being retried once quota
returned. That is the loop recovering from a failure, not improving a draft. One
case looks genuine: 100 words and no sources at 12.67, rewritten to 489 words
with eleven URLs at 24.0. One case is not evidence.

So the claim is now narrower and stays narrow until it is re-earned: **the loop
is demonstrated to retry a failed leg. It is not yet demonstrated to improve a
draft that was merely weak.**

The mechanism behind it is worth stealing regardless of the retraction. A
provider error is not short, and a minimum word count is the only thing that was
looking. Three costs, none visible without reading the draft: a quota error was
recorded in the audit as a score for `gemini-2.5-flash`, a round of the loop was
spent rewriting it, and the run reported **3/3 clouds answering**. The guard now
matches provider-error signatures only in a reply that has no markdown heading at
all — every brief this instruction asks for opens with an H1, so a real draft
about rate limits keeps its score.

---

## What the panel actually buys, measured

This is the claim the architecture has to earn, and it is not "the panel writes
better briefs". It is three separate things that must not be added together:

- **rotation** — how often each cloud produced the best draft. If one cloud won
  almost always, a panel would be waste and the honest recommendation would be to
  use that cloud.
- **regret** — had you committed to one cloud, how far below the panel's best you
  would have landed, per brief. Within-subjects: every cloud answered the *same*
  brief, so there is no control arm to argue about.
- **availability** — how often a cloud produced nothing at all while the panel
  still answered.

Measured over 24 model-backed runs on 2026-08-14, scored twice — once by the
deterministic rubric, once by re-ranking the same stored corpus with the model
judge:

| cloud | availability | win% rubric | win% llm | regret rubric | regret llm |
|---|---|---|---|---|---|
| azure | 96% | 43% | **87%** | 0.97 | 0.52 |
| gcp | 58% | 43% | 43% | 1.54 | 2.21 |
| aws | 100% | 33% | **0%** | 1.32 | 9.38 |

**The two scorers do not agree about whether the architecture is justified.**
Under the rubric no cloud owns the winner, and a panel is the right call. Under
the model judge, Azure wins 87% of the briefs it answered and AWS wins none —
which by this project's own rule reads *one cloud wins nearly every brief; use
that cloud.*

So the best-of-breed claim is a **property of the scorer, not yet of the clouds**,
and it should not be made until a human review says which scorer is right. That
check exists — `agreement_rate` between human ranking and judge ranking — and it
currently stands at one review, which is not a calibration.

Two things follow anyway.

The rubric appears to be **compressing a real quality difference**: it puts
Nova micro 1.32 points behind the panel's best, the model judge puts it 9.38
behind. A small cheap model asked to write prose plausibly *is* much further
behind than "measures form" can see.

And the model judge is not simply favouring its own vendor — it is Gemini, and it
ranks the Gemini participant at 43% while putting Azure at 87%. That weakens the
obvious objection without removing the shared-vendor caveat.

**Even the rotation claim had to be weakened.** "The winner rotates" was too
strong: the winner went 10/8/6 across 24 runs, which is chi-squared 1.00 on 2
degrees of freedom — indistinguishable from a coin. What the corpus supports is
that the winner is *unpredictable*, and that is the better argument for a panel
anyway. A systematic rotation would mean you could route by topic and skip the
panel entirely.

**What survives untouched is availability.** No judge can change whether a draft
existed, and the column is identical under both scorers. On this corpus the
strongest argument for the panel is not that it produces better answers — that is
contested — but that it produces **an answer at all** when a member cannot.

That is a duller claim than the one I wanted, and it is the one that is true.

---

## Parity of tools is not parity of use

All three researchers get a `web_search` tool. Not each vendor's own: only Google
ships a ready search tool, Microsoft's Agent Framework exports a protocol a
client may declare rather than a tool you can hand an agent, and Strands bundles
none at all. "Give each cloud its native search" would mean Gemini grounded
against Google's index, a Foundry model against Bing, and Bedrock against
nothing — three retrieval products, and an audit that reports the difference
between them as a difference between models.

So the tool is written once, and every cloud gets the same function against the
same backend returning the same text. What still differs is the part worth
measuring: **how each framework binds and drives a tool.** ADK wraps a plain
callable, Strands takes an `@tool`-decorated function, Agent Framework takes an
`ai_function`. Three genuinely separate tool-call implementations, one tool.

Then the first model-backed run produced this:

```text
azure  searches=2   evidence 0.0
gcp    searches=0   evidence 5.0
aws    searches=0   evidence 0.0
```

**The model that scored full marks on evidence never searched.** Five points of
citation-shaped text with nothing behind it. The rubric counts the gesture — that
was written down as a known weakness before search existed, and this is it as a
measured one.

The cause was upstream of all of it: the shared instruction never told a
researcher to search. It does now, and the correction has been through two more
versions:

| | zero-search drafts |
|---|---|
| aws, instruction v1 | 7 of 7 |
| aws, v2 | 2 of 9 |
| aws, v3 | 1 of 7 |
| azure, all versions | 1 of 16 |
| gcp, v3 | none — it spends the whole six-search budget every run |

So the finding is **never to *usually*, not never to always** — the
stronger-sounding version is not what the corpus says. And v2 is a cautionary
tale in the other direction: it said "one search for each specific figure",
Gemini read that literally and spent 24 searches on a 300-word brief, which is 25
model calls and enough to exhaust the project's Vertex quota on its own. v3 names
the six-search budget the tool now enforces, so the model plans against the bound
instead of being cut off by it.

Which creates the next open question, honestly: Gemini sits on the budget ceiling
in **every** v3 run, so the bound is now shaping the drafts being compared.

`INSTRUCTION_VERSION` travels on every draft for exactly the same reason
`RUBRIC_VERSION` does. Change the instruction and runs either side of it are
answering different questions; an audit that averages across the change reports a
prompt edit as a change in the models.

---

## The failure shape worth generalising

One pattern shows up more than any bug in this project: **broken things report
success.**

- A cloud's negative control answered *without a credential*. Direct checks
  confirmed `/health`, the agent card and the JSON-RPC invoke endpoint all
  returned 200 to an anonymous caller — on an agent that invokes a billable
  model. The deploy script has a separate step that enforces identity on the
  ingress, and the sequence used had skipped it. Every positive signal in the
  project was green throughout.
- A provider error was scored as a draft, and the run reported 3/3 clouds
  answering.
- The controls harness that eventually found the open ingress had **five defects
  of its own, four of which produced false passes** — including inferring the
  verdict from the deploy CLI's exit code, which conflates a denial with a dead
  credential and a crashed container.

The design response is not more tests. It is that anything which can degrade must
say **what it degraded to**, and any component that can be absent must be probed
alone.

That last one is specific to this architecture and easy to get wrong. The mesh
degrades on purpose: lose a cloud and the other two still produce a verdict and
the run exits 0. Now try testing your auth by removing one leg's credential from
a three-cloud run. It still exits 0. That reads as "no denial happened", and what
actually happened is "the denial was absorbed". So every leg gets probed alone,
and the exit code that means *denied* is a distinct one — `3`, no cloud returned
a draft — rather than "non-zero".

**Any system with graceful degradation needs its controls scoped to a single
component, or the degradation hides exactly the failure you are testing for.**

---

## What this does not show

One deployment, one operator, a few days, and a corpus of 24 model-backed runs
that are all technology surveys — batteries, hydrogen, solar, reactors. Nothing
here has been tried on a brief that is not a technology survey, and the
instruction changed twice inside those 24 runs, so they are not one population.

The rubric is uncalibrated: its weightings and thresholds — eight specifics per
hundred words for full marks, five citation markers, the asymmetric length
penalty — were chosen by argument, not measurement.

The model judge has judged the stored corpus offline and has **never been the
in-run judge**, so it has never gated a draft, never written a critique that was
sent to a researcher, and never driven the loop.

The controls passed on 2026-08-13 and have not been re-run since the agents were
rebuilt for two instruction changes. A control that passed against an earlier
image is a claim about that image.

And the deployed timings above are single cold runs. Two runs of *identical
canned text* produced different winners purely on scheduling noise, which is the
clearest possible demonstration that they are not latencies. Do not quote them.

---

## If you are building one of these

**Decide whether you want a gate before you write a line.** A gate buys cost; an
unconditional fan-out buys a record. You cannot have both from the same run, and
the failure mode of a bad gate is silence.

**Let the runtime that can mint tokens hold the privileged roles.** Which cloud
judges is not a preference, it is a consequence of which one can authenticate
outward without a stored secret.

**Make the role boundary executable.** It breaks by one import, not by a
decision.

**Keep the credential-free path working forever.** The deterministic scorer is
not a fallback; it is what lets you test the whole loop, including its failure
modes, before those failures happen in a deployed run.

**Write down what your scorer cannot see**, and expect every upstream defect to
arrive dressed as a quality difference.

**Version the prompt like you version the rubric.** Both are independent
variables. Runs either side of a change are not comparable, and nothing will warn
you.

And when the aggregate stops supporting the claim you built the thing to make,
publish the narrower claim. On this corpus the panel's defensible benefit is
availability, not quality — the least exciting of the three columns, and the only
one no judge can move.

---

**Repo:**
[github.com/xbill9/multicloud-a2a-subagent](https://github.com/xbill9/multicloud-a2a-subagent)
— the three agents, the master, the judge and the rubric, the revision loop, the
audit, the interop matrix, and the deploy scripts. `docs/RUNBOOK.md` is the
operating manual, including a list of which claims here are measured and which
are still open.
