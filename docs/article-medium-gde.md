# ADK Beyond Its Own Tests: What Happens When Your Agent Answers a Client That Is Not ADK

### One ADK agent on Cloud Run, serving A2A to clients that are not ADK — and the Google-side findings that only a deployment and a foreign caller can produce

`to_a2a()` turns an ADK agent into an A2A server in one line. That is genuinely the shortest path from an `LlmAgent` to something another vendor's agent can call, and it is the reason I started here.

Then I put that agent in a mesh where the callers are **not** ADK — a Strands agent on Bedrock AgentCore and a Microsoft Agent Framework agent on Azure Container Apps, all three given the same research brief, all three answering over A2A v1.0 to one coordinator on Cloud Run.

Everything below is what showed up on the Google side of that, and none of it is visible from a laptop.

The code: [github.com/xbill9/multicloud-a2a-subagent](https://github.com/xbill9/multicloud-a2a-subagent).

---

## The agent card advertises your bind address

`to_a2a(agent, host, port)` writes `host:port` straight into the agent card's interface URL. On Cloud Run the process binds `0.0.0.0:8080`, so the deployed card says this:

```console
$ curl -s https://<the-adk-agent>.run.app/.well-known/agent-card.json
{"url": null,
 "additionalInterfaces": [{"url": "http://0.0.0.0:8080", "protocolBinding": "JSONRPC"}]}
```

A public HTTPS endpoint advertising unroutable plaintext.

**It cannot reproduce locally.** On a laptop the bind address and the dial address are the same string, which is exactly how it survives into a deployment. My other two agents take a `PUBLIC_URL` and advertise that — the behaviour ADK is missing, not anything clever.

Which clients survive it is the part worth knowing:

![Which client survives a card advertising 0.0.0.0:8080. The a2a-sdk client is ok because it rewrites the interfaces after card resolution. The agent-framework A2AAgent is ok because it never routes by card, so a bad card is inert. Google ADK's own RemoteA2aAgent fails, because it routes by card and dials 0.0.0.0:8080](img/medium/03-bad-card.png)

**ADK's own client cannot reach ADK's own server once hosted.** Both halves pass Google's own tests, because locally the two addresses are identical. The one pairing that is entirely first-party code is the one that cannot complete a hop.

And the failure is reported at the wrong layer. Having dialled `0.0.0.0:8080` and failed, `RemoteA2aAgent` raises:

```text
AttributeError: 'A2AClientError' object has no attribute 'status_code'
```

The error handler assumes any `A2AClientError` carries a status code, which a transport failure does not. The real cause — `All connection attempts failed` — goes to a separate log line. Two defects compounding: the first sends the client somewhere unroutable, the second deletes the evidence of where it went.

If you serve with `to_a2a()` today: fetch your own card after deploying, and if you cannot fix the card, make sure your callers rewrite the interface URL after resolution rather than routing by it.

---

## The same reply, delivered twice

ADK's executor attaches the reply as a task **artifact** — and also leaves a copy in task **history**.

That only matters when you talk to someone else. Microsoft's `A2AExecutor` drives the full task lifecycle and leaves the reply *only* in history, with artifacts empty. So a client that reads artifacts alone — the obvious implementation, and the one that works perfectly against ADK — returns an **empty string** against Agent Framework. Not an error, not a timeout: a successful call with no content.

Fix that by reading every carrier the spec allows, and ADK's reply now arrives twice.

In an earlier version of this project the agents returned an exchange rate, and the duplicate was invisible: the parser indexed quotes by target currency, so the second copy overwrote the first and the answer was correct. Change the payload to a written draft and the body doubles, the word count doubles, and the scorer downstream marks a compliant draft as a 100% length overrun.

I found it by reading output — one cloud returned 202 words of text the other two returned in 98. **No test caught it.** The suite was green throughout.

---

## There is no function seam, and that decides where facts get recorded

Strands hands you `async (prompt) -> reply`. You can wrap that from outside and be done.

`to_a2a()` takes an **agent** and serialises its event stream, so anything you need to do between the model and the wire has to happen inside ADK's object model — in my case a `BaseAgent` that consumes `inner.run_async(ctx)` and yields one final event.

Why bother: every draft in this system carries one line, written by the server and never by the model.

```text
<!-- a2a-research agent=gcp model=gemini-2.5-flash brain=llm -->
```

It carries the two things the coordinator cannot reconstruct from its side of the wire — which model actually answered, and whether a model answered at all. Ask Gemini to emit its own metadata and a model that gets it wrong misattributes a draft in the audit, which is the one error an audit cannot detect from the inside.

**That wrapper became load-bearing the moment I attached a tool.** The first version concatenated the text of every event in the stream, which is correct while the stream holds exactly one event. With a `web_search` tool bound, the stream also carries the model's commentary around each tool call — "Let me look that up", a summary of what it found — and concatenating those produces a draft that opens with Gemini narrating its own research. The rubric then scores the narration.

```python
async for event in inner.run_async(ctx):
    if not event.is_final_response():
        continue          # <- the whole fix
    ...
```

Function calls and their results have no text and were already skipped by a `part.text` filter. What has to be excluded explicitly is the *model text* that accompanies them.

---

## The tool is deliberately not `google_search`

ADK gives you `google_search` as native hosted grounding, and I did not use it.

The other two clouds cannot match it. Microsoft's Agent Framework exports `SupportsWebSearchTool`, which is a protocol a chat client may declare rather than a tool you can hand an agent, and Foundry's own grounding wants a Bing resource connection created out of band. Strands bundles no search at all. Using each vendor's native option would have meant Gemini grounded against Google's index and Bedrock grounded against nothing — three retrieval products, and a comparison that reports the difference between *them* as a difference between models.

So all three get the same plain function against the same backend. What stays native is the part worth measuring: **ADK wraps the plain callable itself and runs its own tool-calling loop**, which is a genuinely different implementation from Strands' `@tool` decorator and Agent Framework's function machinery.

---

## `RemoteA2aAgent` assumes A2A is something an agent does

As a client, ADK is the heaviest of the three stacks by a wide margin. `RemoteA2aAgent` is a `BaseAgent` meant to sit inside an agent tree, so using it as a plain client means standing up a `Runner`, an `InMemorySessionService` and a session — per request.

Local, direct-brain, no model in the path, every client against every server:

```console
client \ server  gcp               aws               azure
-----------------------------------------------------------------------
a2a-sdk          ok 134ms          ok 8ms            ok 8ms
agent-framework  ok 129ms          ok 7ms            ok 8ms
google-adk       ok 920ms          ok 9ms            ok 10ms
```

Single loopback runs, so read it as an ordering and nothing more. The shape holds anyway: against the ADK server the ADK client costs about seven times what the other two do, and it still emits `[EXPERIMENTAL]` warnings on every call. If you are writing a program that calls an agent rather than an agent that calls an agent, the reference `a2a-sdk` client is the lighter object.

---

## What Cloud Run brings, and one thing it costs

Two Cloud Run properties shaped this whole project.

**It mints workload OIDC tokens for an audience you choose.** That is what makes the mesh keyless: the coordinator takes a Google-minted token to AWS STS for `AssumeRoleWithWebIdentity` and to Entra as a client assertion, so there is no stored secret on any leg. It is also why the coordinator lives on Google rather than anywhere else — the other two runtimes' minting ability was never confirmed, so hosting it there would have meant storing a credential. **Where your coordinator runs decides how many secrets your system has.**

**A cold start is visible in the wrong place.** On a scale-to-zero service the first thing a caller touches is not your agent, it is your agent *card*:

```text
+792ms  gcp  D research-gcp-....run.app/.well-known/agent-ca  200   5849ms
...
+6643ms gcp  I research-gcp-....run.app/                      200     55ms
```

5849ms on discovery, 55ms on the actual invocation once warm. A single per-leg number would have recorded that run as "Gemini is slow."

And one genuine cost of the containerless path, which cost me a deploy cycle. The GCP side is built once from source and deployed several times, with the entrypoint overridden to run a different process out of the same image. The documented override fails:

```console
--command python --args="-m,agents.gcp.server"

failed to resolve binary path: error finding executable "python" in PATH
```

A buildpack image keeps its interpreter in CNB layers, and `/cnb/lifecycle/launcher` is what puts them on `PATH` before exec'ing your process. Overriding the entrypoint **replaces the launcher**, so the override runs where Python does not exist. Run it through the launcher instead:

```console
--command /cnb/lifecycle/launcher --args="python,-m,agents.gcp.server"
```

Cloud Run reports the failure as *"the user-provided container failed to start and listen on the port defined by PORT=8080"*, which is the symptom of any startup crash and names the one subsystem that was fine.

---

## How Gemini actually behaved next to two other models

Twenty-four briefs, each answered by all three clouds, scored twice — once by a deterministic rubric, once by re-ranking the same stored drafts with a model judge:

![Win rate and regret under two scorers, 24 briefs. Azure's gpt-5-mini wins 43 percent under the rubric and 87 percent under the model judge, with regret 0.97 and 0.52. GCP's gemini-2.5-flash wins 43 percent under both, with regret 1.54 and 2.21. AWS's nova-micro wins 33 percent under the rubric and none at all under the model judge, with regret 1.32 rubric and 9.38 under the model judge](img/medium/07-scorer-changes-the-answer.png)

![Availability across the same 24 briefs, identical under both scorers: aws/nova-micro 100 percent, azure/gpt-5-mini 96 percent, gcp/gemini-2.5-flash 58 percent. The failure recorded against the 58 percent leg is a Vertex 429, so quota is the documented cause rather than a proven one](img/medium/08-availability.png)

Three things about the Gemini row.

**It answered 58% of the briefs it was invited to** — the lowest of the three, on the one leg that never leaves its own cloud and is otherwise the most reliable path in the mesh. The failure recorded against it is a Vertex `429`. Quota is the documented cause rather than a proven one; I have not attributed the ten missing drafts individually. Either way, the largest difference between these three models on this corpus is not prose quality, it is whether a draft came back.

**It is the only one that spends its entire search budget, every run.** All three agents get six searches. Under the current instruction Gemini uses all six in every single run, where the Bedrock model still skips searching entirely in one run out of seven. That is a real behavioural difference — and it also means the ceiling is now shaping the drafts I am comparing. A model that always spends its last search would spend more if it had it.

**The middle column is where it gets uncomfortable.** Under a model judge, Gemini wins 43% while `gpt-5-mini` takes 87%. And the judge is itself Gemini 2.5 Pro, which is worth saying plainly: **my judge shares a vendor with one of the participants.** That is a real bias risk, it is recorded on every verdict, and I am not arguing it away — though it is notable that the Gemini judge ranks the Gemini participant *below* Azure rather than above it, which is the opposite of the failure mode you would expect.

Twenty-four briefs is enough to compute a rate and not enough to trust one. All of mine were technology surveys, and the instruction changed twice inside them. I would not quote these as a model comparison.

---

## Quick reference, Google side

**Your deployed card advertises `0.0.0.0:8080`.** `to_a2a()` writes the bind address. Rewrite interfaces client-side after resolution, or serve a card you control.

**`AttributeError: 'A2AClientError' object has no attribute 'status_code'`.** `RemoteA2aAgent` dialled a card address it could not reach. The real cause is on another log line.

**A remote client gets your reply twice.** ADK sends it as artifact *and* history. The client should deduplicate; one reply in two envelopes is one reply.

**Your draft opens with the model narrating its research.** The event stream carries commentary around each tool call. Keep only `is_final_response()`.

**`error finding executable "python" in PATH` on a buildpack image.** The entrypoint override replaced `/cnb/lifecycle/launcher`. Run the command through the launcher.

**A leg looks slow but the model is fine.** Check whether the time is in the agent-card fetch — on scale-to-zero that is where the cold start lands.

**`No API key was provided`, inside a task body, with HTTP 200.** ADK does not use application-default credentials for Gemini. Set `GOOGLE_GENAI_USE_VERTEXAI=true` with project and location, or an API key.

---

## The short version

- **Fetch your own agent card after every deploy.** `to_a2a()` advertises where it bound, not where you are reachable, and the one client that honours it is Google's own.
- **Assume your reply is read by something that is not ADK.** It arrives in two envelopes; a careful client will deduplicate, and a careless one will double your payload.
- **Decide where you stand between the model and the wire before you attach a tool.** ADK gives you an event stream, not a function, and the stream's contents change the moment a tool exists.
- **Give every cloud the same tool, or measure retrieval instead of models.** `google_search` is better than what I used; it is also unmatchable on the other two clouds.
- **Let Cloud Run's OIDC minting decide your topology.** It is the reason this mesh has no stored secrets anywhere.
- **Expect availability, not eloquence, to be your biggest model difference.** Mine was a quota error, and no rubric can see one.

A2A did what it promised — every one of these is a thing that sits just outside the protocol, and every one of them needed a deployment and a caller that was not Google's to show up at all.

---

**Repo:** [github.com/xbill9/multicloud-a2a-subagent](https://github.com/xbill9/multicloud-a2a-subagent) — the three agents, the shared instruction and tool, the coordinator and judge, the 3×3 interop matrix and the deploy scripts. `docs/INTEROP.md` carries each finding with the date it was measured.

---

*Every table in this piece is an image, because Medium renders no markdown tables. They are generated from the measured numbers by `docs/img/make_medium_graphics.py`, so they cannot drift from the results they describe without the script drifting too.*
