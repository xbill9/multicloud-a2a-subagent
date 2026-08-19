---
title: "Three Clouds, One Brief: What Actually Differs Between ADK, Strands and Agent Framework"
published: false
description: The same research agent built three times — Google ADK on Cloud Run, Strands on Bedrock AgentCore, Agent Framework on Container Apps — all speaking A2A v1.0. Where the platforms differ, where the models differ, and what has to be held constant before either is readable.
tags: ai, architecture, cloud, python
---

All three hyperscalers now ship an agent framework, and all three speak A2A. The
protocol page will tell you that is the interoperability story finished:

> **In a world where agents are built using diverse frameworks and by different
> vendors, A2A provides the definitive common language for agent
> interoperability.**
>
> — [a2a-protocol.org](https://a2a-protocol.org/latest/)

That is true on the wire, and the wire is not the whole job. So I built the
same agent three times — one research agent, one instruction, one search tool, one word
budget — on Google ADK, on AWS Strands and on Microsoft Agent Framework, hosted
on each vendor's own runtime, and had one coordinator fan the same brief out to
all three and score what came back.

| | Google | AWS | Azure |
|---|---|---|---|
| framework | ADK `LlmAgent` | Strands `Agent` | Agent Framework `Agent` |
| model | `gemini-2.5-flash` | `us.amazon.nova-micro-v1:0` | `gpt-5-mini` on Foundry |
| served by | `to_a2a()` | `a2a-sdk` reference routes | `A2AExecutor` |
| hosted on | Cloud Run, us-central1 | Bedrock AgentCore, us-west-2 | Container Apps, westus2 |

The code is all here:
[github.com/xbill9/multicloud-a2a-subagent](https://github.com/xbill9/multicloud-a2a-subagent).

Nothing below is about A2A being broken. A2A worked. This is about the nine
other things that differ once it does — and about the two questions worth
separating, which almost nobody separates: **what differs because of the
platform**, and **what differs because of the model**.

---

## What actually has to be the same

The first version of this was a demo: three agents, three SDKs, three green
ticks. It told me nothing. When three columns differ in nine ways, you cannot
attribute any result to any of them.

So the rule became one line: **share everything that is not the variable under
test.**

| shared, exactly one implementation | different, on purpose |
|---|---|
| the brief and its focus questions | the agent framework |
| the instruction, versioned | the model |
| the search tool and its six-call budget | the serving stack |
| the scoring rubric, versioned | the hosting platform |
| the wire format — markdown, one stamped header | the credential mechanism |
| the failure taxonomy | the tool-binding API |

The right column is the article. The left column is what makes it evidence
instead of an anecdote.

The one people argue with is the search tool. **I gave all three clouds the same
search function rather than each vendor's own**, and it is the decision I would
defend hardest. Only Google ships a ready search tool. Microsoft's Agent
Framework exports `SupportsWebSearchTool`, which is a protocol a chat client may
declare — not a tool you can hand an agent — and Foundry's own grounding wants a
Bing resource connection created out of band. Strands bundles none at all.
"Native search everywhere" would have meant Gemini grounded against Google's
index, a Foundry model against Bing, and Bedrock against nothing: three
retrieval products, and a comparison that reports the gap between *them* as a
gap between models.

What is still native is the part I wanted to see anyway — how each framework
binds and drives a tool. That part is now the only part that varies.

---

## Three frameworks, three shapes for the same agent

Here is the entire model-side construction on each cloud. Not excerpts — this is
all of it.

**Google, ADK:**

```python
from google.adk.agents import LlmAgent

LlmAgent(
    model="gemini-2.5-flash",          # a model id string
    name=..., description=...,
    instruction=INSTRUCTION,           # `instruction`
    tools=[web_search],                # a plain callable
)
```

**AWS, Strands:**

```python
from strands import Agent, tool
from strands.models import BedrockModel

Agent(
    model=BedrockModel(model_id="us.amazon.nova-micro-v1:0"),   # a model *object*
    system_prompt=INSTRUCTION,                                  # `system_prompt`
    tools=[tool(web_search)],                                   # explicitly decorated
)
```

**Azure, Agent Framework:**

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

Three names for the system prompt. Three levels at which the model is named: a
string, a model object, a client holding an endpoint and a credential. Three
tool conventions — ADK wraps the plain callable itself, Strands wants an explicit
`@tool`, Agent Framework takes the callable and runs it through its own function
machinery.

None of that is hard. All of it is **untranslatable**. There is no adapter that
turns these into one object, and every hour I have seen spent trying to build
one produced a fourth thing to maintain that then became what was actually under
test. Share the prompt, the tool and the wire format. Do not try to share the
agent.

---

## Two of the three give you nowhere to stand

Strands hands you a function. Everything else can wrap it from outside:

```python
async def respond(prompt: str) -> str:
    return str(await agent.invoke_async(prompt))
```

ADK and Agent Framework do not. **`to_a2a()` takes an agent and serialises its
event stream**, and Agent Framework's `A2AExecutor` calls the agent too. Neither
gives you an `(prompt) -> reply` boundary, so anything you need to do between the
model and the wire has to be done inside that framework's own object model — a
`BaseAgent` wrapping the first agent on one cloud, a delegating class
implementing `run` on the other.

This is not a style complaint. It decides where a fact can be recorded. Every
draft in this system carries one line:

```text
<!-- a2a-research agent=gcp model=gemini-2.5-flash brain=llm -->
```

That line is written by the **server**, never by the model. It carries the two
things the coordinator cannot reconstruct from its own side of the wire — which
model actually answered, and whether a model answered at all. Ask the model to
emit its own metadata and a model that gets it wrong misattributes a draft in
the audit, which is the one error an audit cannot detect from the inside.

**And on ADK that wrapper became load-bearing the moment I added a tool.** The
first version concatenated the text of every event in the stream, which was
correct while the stream held exactly one event. With `web_search` attached the
stream also carries the model's commentary around each tool call — "Let me look
that up", a summary of what it found — and concatenating those produces a draft
that opens with the model narrating its own research. The scorer downstream then
grades the narration. Keep only `event.is_final_response()`.

---

## A completed task does not mean you got the answer

ADK and Agent Framework both return a `Task` in `TASK_STATE_COMPLETED`. Both are
spec-conformant. They disagree about where the reply goes:

- **ADK** attaches it as an **artifact** — and also leaves a copy in history.
- **Agent Framework's `A2AExecutor`** drives the full lifecycle (`submit` →
  `start_work` → `complete`) and leaves the reply as a `ROLE_AGENT` message in
  **history**, with `artifacts` empty.
- **The `a2a-sdk` reference executor**, which is what my AWS agent sits on,
  enqueues a single `Message` and runs no task lifecycle at all.

So the obvious client — read `task.artifacts` — works perfectly against Google
and returns an **empty string** against Microsoft. Not an error. Not a timeout. A
successful call with no content, which then fails somewhere downstream as a parse
error pointing at the wrong layer.

Read every carrier the spec allows and you get the mirror-image bug: ADK's reply
arrives twice, once per envelope.

That one is worth dwelling on, because of *how* it stayed hidden. In the
predecessor version of this project the agents returned an exchange rate, and
the parser indexed quotes by target currency — so a duplicate object quietly
overwrote its twin and the answer was correct. Change the domain to a written
draft and the body doubles, the word count doubles, and the scorer marks a
compliant draft as a 100% length overrun.

I found it by reading output: one cloud returned 202 words of text the other two
returned in 98. **No test caught it, and the suite was green throughout.** There
is now a live test asserting all three serving stacks return the same canned text
at the same length, which is the cheapest detector I know for the whole class.

"The call succeeded" and "you received the answer" are different claims in A2A.
A client written against one vendor's server will pass that vendor's tests while
silently dropping another vendor's replies.

---

## The agent card advertises an address you cannot dial

`to_a2a(agent, host, port)` writes the **bind** address straight into the card:

```console
$ curl -s https://<the-adk-agent>.run.app/.well-known/agent-card.json
{"url": null,
 "additionalInterfaces": [{"url": "http://0.0.0.0:8080", "protocolBinding": "JSONRPC"}]}
```

A public HTTPS endpoint advertising unroutable plaintext. My AWS and Azure agents
take a `PUBLIC_URL` and advertise that — the behaviour ADK is missing, not
anything clever.

**It cannot reproduce locally**, because on a laptop the bind address and the
dial address are the same string. It needs a deployment, which is exactly how it
survives into one.

Which clients survive it is the opposite of what the ergonomics would predict:

| client | against the deployed ADK server |
|---|---|
| `a2a-sdk` | **ok** — rewrites the interfaces after card resolution |
| `agent-framework` `A2AAgent` | **ok** — never routes by card, so a bad card is inert |
| `google-adk` `RemoteA2aAgent` | **fails** — routes by card, dials `0.0.0.0:8080` |

**ADK's own client cannot reach ADK's own server once hosted.** Both halves ship
green in Google's own tests, because locally the two addresses are identical.
And the stack that has no seam to patch a resolved card is the one that never
needed it, because it dials the URL you constructed it with.

Then the failure is reported at the wrong layer. Having dialled `0.0.0.0:8080`
and failed, `RemoteA2aAgent` raises this:

```text
AttributeError: 'A2AClientError' object has no attribute 'status_code'
```

The error handler assumes any `A2AClientError` carries a status code, which a
transport failure does not. The real cause — `All connection attempts failed` —
lands on a separate log line. Two defects compounding: the first sends the client
to an unroutable address, the second deletes the evidence of where it went.

---

## The platform edits your request

The runtime is not a deployment detail either. Each imposes a contract on the
container, and they do not agree:

| | Cloud Run | AgentCore Runtime | Container Apps |
|---|---|---|---|
| port | `$PORT`, 8080 | **9000** | 8080 |
| invoke path | yours | **`/`** (platform exposes `/invocations/`) | yours |
| health | yours | **`GET /ping` → `{"status": "Healthy"}`** | yours |
| architecture | any | **ARM64, required** | amd64 |
| build | source, buildpack, no Dockerfile | image | image |
| ingress auth | one deploy flag | IAM + `CUSTOM_JWT` | **a separate step** |
| cold-start unit | instance | **session → microVM** | revision replica |

Three of those rows cost me real time.

**AgentCore does not forward the `A2A-Version` header.** `a2a-sdk` reads the
protocol version from that header and, when it is *absent*, assumes `0.3` — then
rejects the request its own handler cannot serve:

```text
A2A version '0.3' is not supported by this handler. Expected version '1.0'.
```

Cloud Run and Container Apps pass it through untouched. So the same client, the
same `a2a-sdk` on both ends, the same server code, and the third cloud fails with
an error that blames the protocol version and names nothing about the platform
that removed it. The fix is to assume the current version when the header is
missing, and only when it is missing — a header that *says* `0.3` is a real
client statement and should still be rejected. **Absent is not evidence of an old
client. It is no evidence at all.**

It had also been latent for a week. The deployed image predated the version
check, so that leg had been green for a reason that stopped being true the moment
I rebuilt it.

**An AgentCore session gets its own microVM.** I was minting a fresh session id
per call, so every call paid for a microVM start. It presented as a fixed
per-client cost until I noticed the slow cell *moved between clients* — and a
fixed per-client cost cannot move. Something per-call can:

| `google-adk` → AWS | runs | measured |
|---|---|---|
| fresh session id per call (the default) | 5 | 5953, 5970, 5926, 5984, 6037ms |
| session id pinned | 2 | **710, 704ms** |

Pin the session id unless you actually want per-call isolation. There is no
equivalent knob on the other two clouds, and this cost is invisible in any
per-leg average.

**Container Apps splits "who may get a token" from "who must present one."** One
deploy step creates the federated credential; a *separate* step enforces identity
on the ingress. Ship only the first and the leg reports its auth mode happily
while answering anybody who asks.

That is not hypothetical. On 2026-08-13 the negative control for that leg
answered **without a credential**, and a direct check confirmed `/health`, the
agent card *and* the JSON-RPC invoke endpoint all returned 200 to an anonymous
caller — on an agent that invokes a billable model. Every other signal in the
project was green at the time, which is the entire argument for having negative
controls at all.

---

## Calling them is three different jobs

The framework difference exists on the client side too, and it decides what you
are able to fix:

- **`agent-framework` `A2AAgent`** — build from a URL, `await .run(prompt)`, read
  `.text`. Two lines. Card resolution and transport are internal, which is
  ergonomic right up to the moment a server advertises a bad card.
- **`a2a-sdk`** — resolve the card, mutate it, build a client, iterate typed
  chunks, close it. Verbose, and the only one low-level enough to work around the
  card defect above.
- **`google-adk` `RemoteA2aAgent`** — a `BaseAgent` meant to live inside an agent
  tree. Using it as a plain client means standing up a `Runner`, a session
  service and a session, per request. **The ADK stack assumes A2A is something an
  agent does, not something a program does.**

Every client against every server, local and with no model in the path:

```console
A2A interop matrix  (the A2A protocol and why agents need one (<=300w), brain=direct)

client \ server  gcp               aws               azure
-----------------------------------------------------------------------
a2a-sdk          ok 134ms          ok 8ms            ok 8ms
agent-framework  ok 129ms          ok 7ms            ok 8ms
google-adk       ok 920ms          ok 9ms            ok 10ms

9/9 attempted cells succeeded
```

Read that as an ordering and nothing more — single runs on loopback. And read it
with the honest dependency in front of you: all three client stacks resolve to
the same `a2a-sdk` wire implementation underneath, and two of my three servers
share serving scaffolding. **Nine cells is a presentation, not nine independent
experiments** — which is what makes the failures above interesting. Shared
implementation on both ends should have made all of this trivial.

---

## The models differ mostly where a rubric cannot see

Now hold the frameworks still and look at the other axis. Three models, chosen to
be unmatched — the heterogeneity is the point, not a confound:

| | `gemini-2.5-flash` | `nova-micro` | `gpt-5-mini` |
|---|---|---|---|
| what it is | fast general model | small and cheap | reasoning deployment |
| reached through | ADK → Vertex | Strands → Bedrock | Agent Framework → Foundry |
| why this one | the ADK path's default | inherited from a two-field lookup task, and a **poor** default for prose | forced, see below |

That last cell is my favourite example of a model choice that is not a
preference. `FoundryChatClient` speaks the OpenAI Responses API. Passing
`store=False` to keep anything from being stored server-side makes the framework
request `reasoning.encrypted_content`, and `gpt-4.1-mini` rejects that outright —
only a reasoning model accepts it. The region is forced too: the Container App
lives in westus2, which offers no Azure OpenAI models, so the call crosses to
westus3. **Two constraints that have nothing to do with writing quality decide
both the model and the latency on that leg.**

Twenty-four briefs, each answered by all three, scored twice — once by a
deterministic rubric, once by re-ranking the same stored drafts with a model
judge:

| cloud / model | availability | win% rubric | win% llm | regret rubric | regret llm |
|---|---|---|---|---|---|
| azure / `gpt-5-mini` | 96% | 43% | **87%** | 0.97 | 0.52 |
| gcp / `gemini-2.5-flash` | 58% | 43% | 43% | 1.54 | 2.21 |
| aws / `nova-micro` | 100% | 33% | **0%** | 1.32 | 9.38 |

Four things fall out of that, and only one of them is about writing.

**Availability moved more than eloquence did.** Gemini answered 58% of the briefs
it was invited to — the lowest of the three, on the one leg that never leaves its
own cloud and is otherwise the most reliable path in the mesh. The failure
recorded against it is a Vertex `429`, so quota is the documented cause rather
than a proven one; I have not attributed the ten missing drafts individually.
Either way, a rate limit is a vendor difference no essay-scoring rubric will ever
capture, and on this corpus it dominates.

**The scorer changes which model looks good, and by a lot.** The rubric puts Nova
1.32 points behind the panel's best; the model judge puts it **9.38** behind. A
small cheap model asked to write prose plausibly *is* much further behind than a
form-counting rubric can see. Under the rubric no model dominates; under the
model judge `gpt-5-mini` takes 87% and Nova takes none. So best-of-breed is
currently a property of the scorer, not of the models — and the model judge here
is Gemini, ranking the Gemini participant at 43% while putting Azure at 87%,
which weakens the obvious vendor-bias objection without removing it.

**Latency is a runtime fact before it is a model fact.** The slowest leg is a
reasoning model called across regions because of a storage flag. The fastest is a
tiny model on the platform that also charges you a microVM start when you forget
to pin a session.

**What no scorer can move is whether a draft existed at all.** The availability
column is identical under both judges, which makes it the only column that does
not wait on calibrating the rubric against human review.

---

## Tool parity in availability is not tool parity in use

All three got the same tool, at the same time, with the same six-call budget.
Use of it split by model and by prompt version:

| | zero-search drafts |
|---|---|
| aws, instruction v1 | 7 of 7 |
| aws, v2 | 2 of 9 |
| aws, v3 | 1 of 7 |
| azure, all versions | 1 of 16 |
| gcp, v3 | none — it spends the whole six-call budget every run |

Two ends of one finding. Nova had to be *told*, twice, and still skips a run in
seven. Gemini sits on the ceiling in every single v3 run, which means the budget
is now shaping the drafts I am comparing — a model that always spends its last
search would spend more if it had it.

And the first model-backed run gave me the sharpest version of it:

```text
azure  searches=2   evidence 0.0
gcp    searches=0   evidence 5.0
aws    searches=0   evidence 0.0
```

**The model that scored full marks on evidence never searched.** Five points of
citation-shaped text with nothing behind it. The rubric counts the gesture, which
I had written down as a known weakness before search existed and now had as a
measured one.

The cause was upstream of the models: the shared instruction never told anyone to
search. Fixing it took three versions, and v2 is a warning in the other
direction — it said "one search for each specific figure", Gemini read that
literally and spent 24 searches on a 300-word brief, which is 25 model calls and
enough to exhaust the project's Vertex quota on its own. v3 names the budget the
tool enforces, so the model plans against the bound instead of being cut off by
it.

**Version the instruction like you version the rubric.** Runs either side of a
prompt change are answering different questions, and an audit that averages
across one reports a prompt edit as a change in the models. Mine carries
`INSTRUCTION_VERSION` on every draft next to `RUBRIC_VERSION`, for exactly that
reason.

---

## Almost nothing on this path fails loudly

The recurring shape, across all three clouds:

- An agent served `llm` mode with **zero tools registered**, because the tool
  connection failed as a `WARNING`, and answered `/health` with 200 the whole
  time.
- A provider quota error came back as a 31-word body, cleared the minimum word
  count, was stamped as a draft, scored 7.97 of 25, and sent back for a rewrite —
  and the run reported **3/3 clouds answering**.
- An agent served the public internet for days behind a correctly configured
  federated credential, because enforcement was a second step.
- The control harness that eventually found that had **five defects of its own,
  four of which produced false passes**.
- The first deployed run had both remote legs return `200` and then ten words of
  refusal, because they were still running the *previous* project's agents from a
  week earlier.

Two habits came out of that and I would carry both to any mesh like this.

**Type your failures.** `transport`, `protocol`, `timeout`, `authentication`,
`provider`. The one that earns its keep here is `provider`: a model that declines
the topic is a provider outcome, and filing it as `protocol` turns "Bedrock
refused" into "AgentCore broke A2A."

**Let the agent report its own facts.** Brain, model, degraded flag and search
count are served by the agent, because only the agent knows them. My matrix used
to print the mode from its *own* process — a different container once deployed —
and duly reported `brain=direct` for a mesh of three model-backed agents.

---

## Troubleshooting quick reference

| symptom | what it actually is | fix |
|---|---|---|
| HTTP 200, task `COMPLETED`, reply is an empty string | the reply is in `task.history`, not `artifacts` (Agent Framework) | read every carrier the spec allows |
| the draft arrives twice and word count doubles | ADK returns it as artifact **and** history | deduplicate; one reply in two envelopes is one reply |
| `A2A version '0.3' is not supported by this handler` | AgentCore dropped the `A2A-Version` header | assume the current version when the header is absent only |
| `AttributeError: 'A2AClientError' object has no attribute 'status_code'` | the ADK client dialled the card's bind address and could not connect | advertise `PUBLIC_URL`; rewrite interfaces after resolution |
| one leg costs ~6s and the slow leg moves between clients | a fresh AgentCore session id per call, each getting a microVM | pin the session id |
| the leg reports federated auth and answers anonymous callers | ingress enforcement is a separate deploy step | run it, then probe the leg with no credential |
| 403 from inference although the role assignment looks right | the container holds a managed-identity token minted before the grant | restart the revision |
| the draft opens with the model narrating its research | ADK's event stream carries tool-call commentary | keep only `is_final_response()` |
| a model scores full marks for evidence with zero searches | your scorer counts citation-shaped text | record searches per draft and read them next to the score |
| a quota error is recorded in the audit as a score | a provider error is not short, and only a word count was looking | detect provider signatures before stamping a draft |
| discovery 403s while invocation would have worked | the agent card is behind the same authorization as the agent | attach the credential to the **client**, not the request |

---

## The short version

- **Share the prompt, the tool and the wire format. Never share the agent.** The
  three frameworks do not reduce to one object, and the adapter that pretends
  otherwise becomes the thing under test.
- **Two of the three give you no function seam.** Decide early where you stand
  between the model and the wire, because on ADK and Agent Framework that place
  is inside their object model — and what arrives there changes when you add a
  tool.
- **A completed task is not a delivered answer.** Read every carrier, then
  deduplicate.
- **Treat the agent card as configuration, not as truth.** One framework writes
  its bind address into it, one client honours that, and locally they are the
  same string.
- **Assume the platform edits your request.** A header that survives two clouds
  is dropped by the third, and the error will blame your protocol version.
- **Expect the biggest model difference to be availability**, not eloquence.
  Quota, a forced region and a required reasoning mode moved my results more than
  writing quality did.
- **Write down what your scorer cannot see.** Every upstream defect — a doubled
  reply, a narrated tool call, a provider error — reaches the judge disguised as
  a quality difference.

Twenty-four briefs is enough to compute a rate and not enough to trust one, and
all of mine were technology surveys. I would not quote these numbers as a model
comparison and I do not. What I would claim is the shape: the platform
differences are structural and repeatable, the model differences are mostly about
whether you get an answer at all, and every framework will hide a different one
from you.

A2A did the thing it promised. Everything above is what is left over — and it
will be different again for whoever wires the fourth cloud in, which is rather
the point of writing it down.

---

**Repo:**
[github.com/xbill9/multicloud-a2a-subagent](https://github.com/xbill9/multicloud-a2a-subagent)
— three agents, the shared instruction and tool, the coordinator and judge, the
3×3 interop matrix, the negative controls and the deploy scripts.
`docs/INTEROP.md` carries every finding above with the date it was measured, and
`docs/RUNBOOK.md` lists which claims are measured and which are still open.
