---
title: One brief, three clouds — what actually differs between a Google, an AWS and an Azure agent
published: false
description: A framework for running the same research brief across ADK on Cloud Run, Strands on Bedrock AgentCore and Agent Framework on Container Apps — and the platform and model differences it makes visible once everything else is held constant.
tags: ai, architecture, cloud, python
---

Three AI agents. Same brief, same instruction, same tool, same word budget, same
rubric. Everything else is different, on purpose:

| | Google | AWS | Azure |
|---|---|---|---|
| framework | ADK `LlmAgent` | Strands `Agent` | Agent Framework `Agent` |
| model | `gemini-2.5-flash` | `us.amazon.nova-micro-v1:0` | `gpt-5-mini` on Foundry |
| served by | `to_a2a()` | `a2a-sdk` reference routes | `A2AExecutor` |
| hosted on | Cloud Run, us-central1 | Bedrock AgentCore, us-west-2 | Container Apps, westus2 |

They write independently, never speak to each other, and reach the same
coordinator over **A2A v1.0**. A judge scores the three drafts blind against a
fixed rubric and every run is appended to an audit.

Everything is here:
[github.com/xbill9/multicloud-a2a-subagent](https://github.com/xbill9/multicloud-a2a-subagent).

This article is not about the protocol — A2A worked. It is about what the thing
was built to see: **where these three stacks genuinely differ**, on the platform
side and on the model side, and what a framework has to hold constant before
either kind of difference means anything.

---

## The framework is the part that holds still

There is a version of this project that is a demo: three agents, three SDKs,
three green ticks. It tells you nothing, because when three columns differ in
nine ways you cannot attribute anything to any of them.

So the design rule is one line: **share everything that is not the variable
under test.**

| shared, one implementation | per-cloud, deliberately |
|---|---|
| the brief and the focus questions | the agent framework |
| the instruction (`INSTRUCTION`, versioned) | the model |
| the search tool (`protocol/search.py`) and its six-call budget | the serving stack |
| the rubric and the judge (`RUBRIC_VERSION`) | the hosting platform |
| the wire format — one markdown draft, one stamped header | the credential mechanism |
| the failure taxonomy — `transport`, `protocol`, `timeout`, `authentication`, `provider` | the tool-binding API |

The right-hand column is the finding. The left-hand column is what makes it a
finding rather than an anecdote.

Two of those shared pieces are worth pausing on, because they are the ones most
projects get wrong in the direction that feels generous.

**One search tool, not each vendor's own.** Only Google ships a ready search
tool. Microsoft's Agent Framework exports `SupportsWebSearchTool`, which is a
protocol a chat client may declare — not a tool you can hand an agent — and
Foundry's own grounding needs a Bing resource connection created out of band.
Strands bundles none at all. "Give each cloud its native search" would mean
Gemini grounded against Google's index, a Foundry model against Bing, and
Bedrock against nothing: three retrieval products, and an audit that reports the
gap between them as a gap between models. So the tool is written once and every
cloud gets the same function, hitting the same backend, returning the same text.
**What still differs is how each framework binds and drives it** — which is the
interesting part, and is now isolated.

**The fan-out is unconditional.** Every cloud gets a byte-identical brief on
every run. No router decides who is asked. A router would produce nicer answers
and a worthless record, because the audit would then be measuring the router —
the one component nobody thinks to hold constant while comparing vendors.

---

# Part one: the agents differ

## Building the agent: three shapes for the same object

Here is the entire model-side construction on each cloud, unabridged.

**Google — ADK:**

```python
from google.adk.agents import LlmAgent

LlmAgent(
    model="gemini-2.5-flash",          # a model id string
    name=..., description=...,
    instruction=INSTRUCTION,           # `instruction`
    tools=[web_search],                # a plain callable
)
```

**AWS — Strands:**

```python
from strands import Agent, tool
from strands.models import BedrockModel

Agent(
    model=BedrockModel(model_id="us.amazon.nova-micro-v1:0"),   # a model *object*
    system_prompt=INSTRUCTION,                                  # `system_prompt`
    tools=[tool(web_search)],                                   # explicitly decorated
)
```

**Azure — Agent Framework:**

```python
from agent_framework import Agent
from agent_framework.foundry import FoundryChatClient
from azure.identity import DefaultAzureCredential

Agent(
    client=FoundryChatClient(          # a *chat client*, not a model
        project_endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
        model=model_id(),
        credential=DefaultAzureCredential(),
    ),
    instructions=INSTRUCTION,          # `instructions`, plural
    tools=[web_search],
    default_options={"store": False},
)
```

Three names for the system prompt. Three levels at which the model is
identified: a string, a model object, a client holding an endpoint and a
credential. Three tool conventions — ADK wraps the plain callable itself,
Strands wants an explicit `@tool`, Agent Framework takes the callable and runs
it through its own function machinery.

None of that is hard. All of it is **untranslatable**: there is no adapter that
makes these three the same object, which is why the shared parts of this project
are the *prompt*, the *tool function* and the *wire format*, and never the agent.

## The seam that two of the three do not give you

The AWS agent exposes an obvious seam: an `async (prompt) -> reply` function.
Everything the framework wraps around it can be layered on from outside:

```python
async def respond(prompt: str) -> str:
    return str(await agent.invoke_async(prompt))
```

The other two have no such seam, and they lack it in different ways.

**ADK's `to_a2a()` takes an agent, not a function**, and serialises its event
stream. So the serving metadata has to be written by *another agent* wrapping
the first — a `BaseAgent` that consumes `inner.run_async(ctx)` and yields one
final event.

**Agent Framework's `A2AExecutor` also calls the agent**, so the same job is
done by a delegating class implementing the framework's own `run` contract.

This is not a wrapper-pattern aesthetics complaint. It decides where a fact can
be recorded. Every draft carries a one-line header:

```text
<!-- a2a-research agent=gcp model=gemini-2.5-flash brain=llm -->
```

That line is written by the **server**, never by the model. Ask the model to
emit its own metadata and a model that gets it wrong misattributes a draft in
the audit — the one error an audit cannot detect from the inside. It carries the
two facts the coordinator cannot reconstruct from its side of the wire: which
model actually answered, and whether a model answered at all.

And on ADK, that wrapper turned out to be load-bearing for a second reason. The
first version concatenated the text of every event in the stream, which was
correct while the stream held exactly one. **Attach a tool and it does not.**
The stream then also carries the model's own commentary around each tool call —
"Let me look that up", a summary of what it found — and concatenating those
produces a "draft" that opens with the model narrating its research. The rubric
would have gone on to score the narration: structure, concision and coverage all
read the whole body. Only `event.is_final_response()` is kept now.

> **The general form:** the framework you choose decides *where you are allowed
> to stand* between the model and the wire. On one cloud that is a function
> boundary; on the other two it is inside the framework's own object model, and
> what arrives there is an event stream whose contents change when you add a
> tool.

## Where the reply lands is not the same field

Both ADK and Agent Framework return a `Task` in `TASK_STATE_COMPLETED`. Both are
spec-conformant. They disagree about where the answer goes:

- **ADK** attaches it as an **artifact** — and *also* leaves it in history.
- **Agent Framework's `A2AExecutor`** drives the full lifecycle (`submit` →
  `start_work` → `complete`) and leaves the reply as a `ROLE_AGENT` message in
  **history**, with `artifacts` empty.
- **The `a2a-sdk` reference executor** used by the AWS agent enqueues a single
  `Message` — the immediate-response workflow, no task lifecycle at all.

So the obvious client — read `task.artifacts` — works perfectly against Google
and returns an **empty string** against Microsoft. Not an error, not a timeout: a
successful call with no content, which then fails downstream as a parse error
pointing at the wrong layer.

Read every carrier the spec allows and you hit the mirror-image bug: ADK's reply
arrives **twice**, once per envelope. In the predecessor mesh — three clouds
returning a currency rate — that was invisible, because the parser indexed quotes
by target currency and the duplicate silently overwrote its twin. Under a
research draft the body doubles, the word count doubles, and the rubric's
`concision` dimension scores a compliant draft as a 100% overrun.

It was found by running the mesh and noticing that one cloud returned 202 words
of text the other two returned in 98. **Not by any test.** The suite was green
throughout. There is now a live test asserting all three serving stacks return
the same canned text at the same length, which is the cheapest available detector
for the whole class.

**"The call succeeded" and "you received the answer" are different claims in
A2A**, and a client written against one vendor's server passes its own tests
while silently dropping another vendor's replies.

## The agent card, and the address that is not yours

`to_a2a(agent, host, port)` writes the **bind** address straight into the card:

```console
$ curl -s https://<the-adk-agent>.run.app/.well-known/agent-card.json
{"url": null,
 "additionalInterfaces": [{"url": "http://0.0.0.0:8080", "protocolBinding": "JSONRPC"}]}
```

A public HTTPS endpoint advertising unroutable plaintext. The AWS and Azure
agents take a `PUBLIC_URL` and advertise that instead — which is the behaviour
ADK is missing, not anything clever. It does not reproduce locally, where bind
address and dial address coincide, which is exactly how it survives into
production.

The consequence is the sharpest single result in the project, and it is
off-diagonal:

| client | against the deployed ADK server |
|---|---|
| `a2a-sdk` | **ok** — rewrites the interfaces after resolution |
| `agent-framework` `A2AAgent` | **ok** — never routes by card, so a bad card is inert |
| `google-adk` `RemoteA2aAgent` | **fails** — routes by card, dials `0.0.0.0:8080` |

**ADK's own client cannot reach ADK's own server once hosted.** Both halves ship
green in Google's tests, because locally the two addresses are identical. And
`agent-framework` cannot even *express* the workaround the `a2a-sdk` client uses
— it has no seam to patch a resolved card — yet it does not need one, because it
dials the URL it was constructed with.

Then the failure is reported at the wrong layer. Having dialled `0.0.0.0:8080`
and failed, `RemoteA2aAgent` raises:

```text
AttributeError: 'A2AClientError' object has no attribute 'status_code'
```

The error handler assumes any `A2AClientError` carries a status code, which a
transport failure does not. The actual cause — `All connection attempts failed` —
appears only on a separate log line. Two defects compounding: the first sends
the client to an unroutable address, the second removes the evidence of where it
went.

## What the platform does to your request

The hosting is not a deployment detail either. Each platform imposes a contract
on the container, and they do not agree:

| | Cloud Run | AgentCore Runtime | Container Apps |
|---|---|---|---|
| port | `$PORT`, 8080 | **9000** | 8080 |
| invoke path | yours | **`/`** (the platform exposes `/invocations/`) | yours |
| health | yours | **`GET /ping` → `{"status": "Healthy"}`** | yours |
| architecture | any | **ARM64, required** | amd64 |
| build | source, buildpack, no Dockerfile | image | image |
| auth on ingress | `--no-allow-unauthenticated` | IAM + `CUSTOM_JWT` | a **separate deploy step** |
| cold-start unit | instance | **session → microVM** | revision replica |

Three of those rows have teeth.

**AgentCore drops the `A2A-Version` header.** `a2a-sdk` reads the protocol
version from that header and, when it is *absent*, assumes `0.3` — then rejects
the request its own handler cannot serve. Cloud Run and Container Apps forward
the header untouched. So the same client, the same `a2a-sdk` on both ends, and
the same server code succeed on two clouds and fail on the third, with an error
that blames the protocol version and names nothing about the platform that
removed it. The fix is to treat a missing header as the current version, scoped
deliberately to *absent* — a header that says `0.3` is a real client statement
and is still rejected. **Absent is not evidence of an old client; it is no
evidence at all.**

**An AgentCore session gets its own microVM.** The coordinator was minting a
fresh session id per call, so every call paid for a microVM start. It presented
as a fixed per-client cost until somebody noticed the slow cell *moved* between
clients, and a fixed cost cannot move:

| `google-adk` → AWS | runs | measured |
|---|---|---|
| fresh session id per call (default) | 5 | 5953, 5970, 5926, 5984, 6037ms |
| session id pinned | 2 | **710, 704ms** |

That is a platform property with no equivalent on the other two clouds, and it
is invisible in every per-leg average.

**Container Apps splits "who may get a token" from "who must present one."** One
deploy step creates the federated credential; a *separate* one enforces identity
on the ingress. Ship only the first and the leg reports its auth mode happily
while answering anyone who asks. That is not hypothetical — on 2026-08-13 the
negative control for that leg answered **without a credential**, and a direct
check confirmed `/health`, the agent card *and* the JSON-RPC invoke endpoint all
returned 200 to an anonymous caller, on an agent that invokes a billable model.
Every other signal in the project was green at the time.

## Calling them: three client SDKs, three different kinds of object

The framework difference exists on the calling side too, and it is not
symmetrical with correctness — it decides what you are able to fix:

- **`agent-framework` `A2AAgent`** — construct from a URL, `await .run(prompt)`,
  read `.text`. Two lines. Card resolution and transport selection are internal,
  which is ergonomic and leaves no seam when a server advertises a bad card.
- **`a2a-sdk`** — resolve the card, mutate it, build a client, iterate typed
  chunks, close it. Verbose, and the only stack low-level enough to work around
  the card defect above.
- **`google-adk` `RemoteA2aAgent`** — a `BaseAgent` meant to sit inside an agent
  tree. Using it as a plain client means standing up a `Runner`, a session
  service and a session, per request. **The ADK stack assumes A2A is something an
  agent does, not something a program does.**

Every client against every server, local and direct-brain so that no model is in
the path:

```console
A2A interop matrix  (the A2A protocol and why agents need one (<=300w), brain=direct)

client \ server  gcp               aws               azure
-----------------------------------------------------------------------
a2a-sdk          ok 134ms          ok 8ms            ok 8ms
agent-framework  ok 129ms          ok 7ms            ok 8ms
google-adk       ok 920ms          ok 9ms            ok 10ms

9/9 attempted cells succeeded
```

Read that as an ordering of stacks and nothing more — single local runs on
loopback. The honest dependency is worth stating: all three client stacks resolve
to the same `a2a-sdk` wire implementation underneath, and two of the three
servers share serving scaffolding. **Nine cells is a presentation, not nine
independent experiments** — and shared implementation on both ends should have
made interop trivial, which is what makes the failures above interesting rather
than expected.

---

# Part two: the models differ

Now hold the frameworks constant and look at the other axis. Three models, chosen
to be *unmatched* — heterogeneity is the asset here, not a confound:

| | `gemini-2.5-flash` | `us.amazon.nova-micro-v1:0` | `gpt-5-mini` |
|---|---|---|---|
| what it is | fast general model | small, cheap | reasoning deployment |
| how it is reached | ADK → Vertex | Strands → Bedrock | Agent Framework → Foundry |
| why this one | default for the ADK path | inherited from a two-field lookup task, and a **poor** default for drafting prose | forced: `store=False` needs `reasoning.encrypted_content`, which `gpt-4.1-mini` rejects |

That last cell is a good example of a model choice that is not a preference.
`FoundryChatClient` speaks the OpenAI Responses API; passing `store=False` to
avoid server-side storage makes the framework request encrypted reasoning
content, and only a reasoning model accepts it. The region is forced too — the
Container App lives in westus2, which offers no Azure OpenAI models, so the call
crosses to westus3. **Latency on that leg is a consequence of two constraints
that had nothing to do with the model's quality.**

Measured across 24 model-backed runs on 2026-08-14, scored twice — once by the
deterministic rubric, once by re-ranking the same stored drafts with a model
judge:

| cloud / model | availability | win% rubric | win% llm | regret rubric | regret llm |
|---|---|---|---|---|---|
| azure / `gpt-5-mini` | 96% | 43% | **87%** | 0.97 | 0.52 |
| gcp / `gemini-2.5-flash` | 58% | 43% | 43% | 1.54 | 2.21 |
| aws / `nova-micro` | 100% | 33% | **0%** | 1.32 | 9.38 |

Four differences fall out of that table, and only one of them is about writing
quality.

**Availability is a model-platform property, not a network one.** Gemini answered
58% of the briefs it was invited to — the lowest of the three, on the one leg
that never crosses a cloud boundary and is otherwise the most reliable path in
the mesh. The failure recorded against that leg is a Vertex `429`, so quota is
the documented cause rather than a proven one; nobody has attributed the ten
missing drafts individually. Either way, a rate limit is a difference between
vendors that no essay-scoring rubric will ever capture, and on this corpus it
moves the result more than prose quality does.

**Retrieval behaviour differs more than prose does.** All three have the same
tool. Use of it splits by model and by prompt version:

| | zero-search drafts |
|---|---|
| aws, instruction v1 | 7 of 7 |
| aws, v2 | 2 of 9 |
| aws, v3 | 1 of 7 |
| azure, all versions | 1 of 16 |
| gcp, v3 | none — it spends the whole six-call budget every run |

Two ends of the same finding. Nova had to be *told*, twice, and still skips a
run in seven. Gemini sits on the budget ceiling in every single v3 run — which
means the bound is now shaping the drafts being compared, and a model that always
spends its last search would spend more if it had it. **Tool parity in
availability is not tool parity in use.**

And the first model-backed run produced the sharpest version of it:

```text
azure  searches=2   evidence 0.0
gcp    searches=0   evidence 5.0
aws    searches=0   evidence 0.0
```

**The model that scored full marks on evidence never searched.** Five points of
citation-shaped text with nothing behind it. The rubric counts the gesture — a
known weakness, now a measured one — and the model differences it was
supposedly measuring were, that day, mostly differences in willingness to invent
a citation.

**The scorer changes which model looks good, and by a lot.** The rubric puts
Nova 1.32 points behind the panel's best; the model judge puts it **9.38**
behind. A small, cheap model asked to write prose plausibly *is* much further
behind than a form-counting rubric can see — which is direct evidence that the
rubric compresses a real quality difference, now with a number on it. The
inverse also holds: under the rubric no model dominates, under the model judge
`gpt-5-mini` wins 87% and Nova wins none.

So **best-of-breed is currently a property of the scorer, not of the models.**
Worth noting that the model judge is Gemini and it ranks the Gemini participant
at 43% while putting Azure at 87%, which weakens the obvious vendor-bias
objection without removing it.

**What no scorer can move is whether a draft existed.** The availability column
is identical under both, which makes it the only claim in the table that does not
wait on calibrating the rubric against human review — a check that currently
stands at one review, which is not a calibration.

### What these numbers are not

Twenty-four runs, all technology surveys — batteries, hydrogen, solar, reactors.
The instruction changed twice inside them, so they are not one population. Nova is
running as an inherited default nobody would choose for prose. And the deployed
timings elsewhere in this project are single cold runs: two runs of *identical
canned text* produced different winners purely on scheduling noise.

This is a framework for producing model comparisons. It is not yet a model
comparison.

---

## What the framework has to do so any of this is legible

Every difference above only reads as a difference because something else was
pinned. The mechanisms, in the order they earned their place:

**Version the prompt like you version the rubric.** `INSTRUCTION_VERSION` travels
on every draft next to `RUBRIC_VERSION`. Change the instruction and runs either
side are answering different questions — v2 said "one search for each specific
figure", Gemini read it literally and spent 24 searches on a 300-word brief,
which is 25 model calls and enough to exhaust the project's Vertex quota alone.
v3 names the six-call budget the tool enforces, so the model plans against the
bound instead of being cut off by it. An audit that averages across that change
reports a prompt edit as a change in the models.

**Label the brain and exclude the scaffolding.** Every agent has a
credential-free `direct` mode returning canned text, which is what makes a failed
matrix cell unambiguously a protocol failure. `Draft.brain` travels with the
draft and the audit drops anything that is not `llm`. Averaging canned text into
a model's score manufactures a result out of scaffolding.

**Let the agent report its own facts.** `brain`, `model`, `degraded` and the
search count are served by the agent, because only the agent knows them. The
matrix used to print the mode from its *own* process — a different container once
deployed — and duly reported `brain=direct` for a mesh of three `llm` agents.

**Type the failures.** `transport`, `protocol`, `timeout`, `authentication`,
`provider`. The one that earns its keep in this domain is `provider`: a model
that declines the topic is a provider outcome, and filing it as `protocol` turns
"Bedrock refused" into "AgentCore broke A2A". It fired on the first deployed run
— both remote legs returned `200` and then ten words of refusal, because they
were still running the *previous* project's agents from a week earlier.

**Guard against a provider error being scored as an answer.** A Vertex `429`
arrived as a 31-word body, cleared the minimum word count, was stamped as a
draft, scored 7.97 of 25, judged, and sent back for a rewrite — and the run
reported **3/3 clouds answering**. A quota error was recorded in the audit as a
score for `gemini-2.5-flash`.

That last one is the pattern this project keeps paying for, on every cloud:
**broken things report success.** An agent serving the internet behind a healthy
`/health`. A control harness reporting six clean denials while nothing was tested.
An ADK agent starting with zero tools registered because the tool connection
failed as a `WARNING`. The response is not more tests — it is that anything which
can degrade must say what it degraded to, and any component that can be absent
must be probed alone.

---

## If you are building one of these

**Share the prompt, the tool and the wire format. Never try to share the agent.**
Three frameworks do not reduce to one object, and the adapter you write to
pretend otherwise becomes the thing you are actually measuring.

**Decide where you stand between the model and the wire, per framework.** One of
them will give you a function; the others will hand you an event stream or their
own `run` contract. Stamp provenance there, server-side, and never ask the model
to describe itself.

**Assume the platform edits your request.** A header that survives two clouds
will be dropped by the third, and the resulting error will blame your protocol
version.

**Read the card as configuration, not as truth.** One framework writes its bind
address into it, one client honours that, and locally the two are identical.

**Expect the largest model difference to be availability**, not eloquence. Quota,
region constraints and a required reasoning mode moved more here than prose
quality did, and none of them show up in a rubric.

**Write down what your scorer cannot see.** Every upstream defect — a duplicated
reply, a narrated tool call, a provider error — arrives at the judge disguised as
a quality difference.

---

**Repo:**
[github.com/xbill9/multicloud-a2a-subagent](https://github.com/xbill9/multicloud-a2a-subagent)
— the three agents, the shared instruction and tool, the coordinator and judge,
the 3×3 interop matrix, the negative controls and the deploy scripts.
`docs/INTEROP.md` holds the measured findings with the dates they were taken, and
`docs/RUNBOOK.md` lists which claims here are measured and which are still open.
