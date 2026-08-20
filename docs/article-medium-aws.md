# Strands on AgentCore, Answering Two Other Clouds: The Contract, the Header, and the microVM

### A Strands agent on Bedrock AgentCore, answering callers on Google Cloud and Azure — the container contract, the header the platform drops, and the session that is a cold start

Strands ships no A2A server integration, which turns out to be the easiest starting position of the three frameworks I used: you drop the agent behind the `a2a-sdk` reference routes and you are serving the protocol's own implementation.

The hard part was never Strands. It was **AgentCore Runtime** — a container contract that is narrow, mostly undocumented in one place, and different from every other runtime in the mesh.

I ran that agent alongside a Google ADK agent on Cloud Run and a Microsoft Agent Framework agent on Container Apps, all three answering the same research brief over A2A v1.0 to one coordinator. Everything below is the AWS side of it.

The code: [github.com/xbill9/multicloud-a2a-subagent](https://github.com/xbill9/multicloud-a2a-subagent).

---

## The contract, in one place

Every clause here is load-bearing, and each one differs from what the other two runtimes want:

![The AgentCore Runtime container contract, with Cloud Run and Container Apps as the control column. Port 9000 rather than 8080. Invoke path slash, with the platform exposing /invocations/. Health is GET /ping returning Healthy. ARM64 is required. The card sits at /.well-known/agent-card.json on all three. AgentCore drops the A2A-Version header where the other two forward it, and its cold-start unit is a session mapped to a microVM rather than an instance or a revision replica](img/medium/10-agentcore-contract.png)

Port 9000, not 8080 — 8080 is the HTTP protocol's port. Path `/`, not `/invocations` — that is the path the *platform* exposes to your callers, not the one your container serves. A Starlette app that already serves JSON-RPC at `/` and the card at the well-known path satisfies this unmodified.

`/ping` is the one you have to add, and it is not your existing `/health`. A container that fails the ping never reaches the point of being invoked. One detail worth copying: my `/ping` deliberately **omits** `time_of_last_update`. A timestamp that advances on every poll reads as a continuous status change, which stops the idle session timeout from ever firing and leaks sessions until MaxLifetime.

---

## AgentCore drops the `A2A-Version` header

This is the finding I would most want another AWS builder to have for free.

`a2a-sdk` reads the protocol version from an `A2A-Version` request header. When the header is **absent** it assumes `0.3`, and then rejects the request its own handler cannot serve:

```text
A2A version '0.3' is not supported by this handler. Expected version '1.0'.
```

Cloud Run and Container Apps forward that header untouched. **AgentCore does not.** So the same client, the same `a2a-sdk` on both ends, and the same server code succeed on two clouds and fail on the third — with an error that blames the protocol version and names nothing about the platform that removed it.

The fix is a middleware that fills the header when it is missing, and *only* when it is missing:

```python
if VERSION_HEADER.lower() not in {k.lower() for k in request.headers}:
    request.scope["headers"] = [
        *request.scope["headers"],
        (VERSION_HEADER.lower().encode(), PROTOCOL_VERSION_CURRENT.encode()),
    ]
```

A header that *says* `0.3` is a real client statement and should still be rejected. **An absent header is not evidence of an old client; it is no evidence at all.**

It had also been latent for a week. My deployed image predated the version check in `a2a-sdk`, so the leg had been green for a reason that stopped being true the moment I rebuilt it onto a current SDK.

---

## A session is a microVM, and you are probably minting one per call

My coordinator generated a fresh AgentCore session id for every call. AgentCore gives each session its own microVM, so every call was paying for a microVM start.

It presented as a **fixed per-client cost** — one client's cell was always slow — until I noticed the slow cell *moved between clients*. A fixed per-client cost cannot move. Something per-call can:

![AgentCore session cold starts. With a fresh session id per call, the default, five runs measured 5926 to 6037 milliseconds. With the session id pinned, two runs measured 704 to 710 milliseconds. It presented as a fixed per-client cost until the slow cell moved between clients, and a fixed cost cannot move](img/medium/05-session-cold-start.png)

The two conditions were interleaved in time, so this is not warming drift. Releasing the pin brings it straight back, and the whole column drops with the pin because none of the cells is paying for a new microVM.

Set your session id explicitly unless you actually want per-call isolation. What is *not* established here: why cold capacity lands on one call rather than another, or how many warm sessions AgentCore keeps. Only the cause of the latency is proven.

---

## Least privilege: discovery is a separate action

The predecessor to this project left an open question: scoping `bedrock-agentcore:InvokeAgentRuntime` to `runtime/<id>` and `runtime/<id>/*` was denied **403 on the agent-card fetch**, and only `Resource: "*"` worked. That is an unpleasant thing to leave in a policy.

The resource scope was never the problem. **Discovery is a separate action.** This policy works — card fetch and invocation both 200, no wildcard resource anywhere:

```json
{
  "Action": ["bedrock-agentcore:InvokeAgentRuntime",
             "bedrock-agentcore:GetAgentCard"],
  "Resource": ["arn:aws:bedrock-agentcore:us-west-2:<account>:runtime/research_aws-<id>",
               "arn:aws:bedrock-agentcore:us-west-2:<account>:runtime/research_aws-<id>/*"]
}
```

A policy granting only `InvokeAgentRuntime` denies the card fetch however the resources are written. Widening to `Resource: "*"` appeared to fix it because a wildcard with the wrong action set still fails — something else in that policy differed. Honest limit on the claim: the original failing policy lives in another repo and is not in hand, so "the missing element was the action" is a strong inference from a live measurement rather than a diff of the two.

The general shape holds on all three clouds: **discovery is privileged separately from invocation**, and a credential that reaches the call but not the card fails somewhere that looks nothing like auth. Which is why the credential in this project is attached to the httpx *client* rather than to a single request.

---

## Calling Bedrock from another cloud with no access key

The coordinator runs on Google Cloud and calls this agent. There is no AWS access key anywhere in it.

The chain: Cloud Run mints a workload OIDC token for an audience you choose → the coordinator presents that token to STS `AssumeRoleWithWebIdentity` → temporary credentials come back → the request is signed with SigV4.

Four traps, each of which looks like correct configuration:

**AWS federates with `accounts.google.com` natively.** Creating an explicit IAM OIDC identity provider for it *breaks* federation with `InvalidIdentityToken`. Azure's Entra is the opposite — there you must create one. Same-looking task, opposite rules.

**The condition keys do not mean what they are named.** `accounts.google.com:oaud` is the token's `aud`. `accounts.google.com:aud` is the token's `azp`, which is a number. Putting an audience string in `:aud` can never match.

**Audience alone is not authorization.** The audience is caller-chosen, so an audience-only condition proves only that *some* identity in that IdP minted the token. Pin the subject too, using the immutable numeric ID rather than an email, which can be freed and re-bound.

**`InvalidIdentityToken` and `AccessDenied` are different worlds.** The first means the token could not be validated at all — a provider-setup problem. The second means it validated fine and your conditions did not match — a policy problem. Nothing else in the response tells you which one you are in, and this single distinction has saved me more time than the federation work itself took.

One habit follows from that, and it is worth more than the rest: **log the raw provider response at every auth boundary.** In an agent system an error comes back as a tool result, and a model in the middle will happily paraphrase `AccessDenied: condition ... did not match` into "there was an issue with the credentials." A raised message is not an observable. That decision is what eventually answered the `GetAgentCard` question above — **AWS had been naming the missing action in the response body all along**, and the earlier adapter kept the status code and threw the body away.

---

## Strands is the one framework that gives you a seam

Worth saying, because it is a real advantage and it only becomes visible next to the other two.

```python
agent = Agent(
    model=BedrockModel(model_id="us.amazon.nova-micro-v1:0"),
    system_prompt=INSTRUCTION,
    tools=[tool(web_search)],
)

async def respond(prompt: str) -> str:
    return str(await agent.invoke_async(prompt))
```

That `async (prompt) -> reply` function is a boundary you can wrap from outside — provenance stamping, search accounting, error typing, all of it layered on without touching the agent.

Neither of the other two frameworks has one. ADK's `to_a2a()` takes an *agent* and serialises its event stream; Agent Framework's `A2AExecutor` calls the agent too. On both of those, anything you want to do between the model and the wire has to be done inside that framework's own object model.

The one place Strands costs you something: it **bundles no web search**. `strands-agents-tools` is a separate distribution and carries `http_request`, not a search API. Since giving one cloud a retrieval product the others lack would have turned my model comparison into a comparison of search backends, all three clouds got the same plain function — bound through Strands' own `@tool` decorator, which is genuinely a different tool-call implementation from the other two and is the part worth measuring.

---

## How Nova micro actually did

Twenty-four briefs, each answered by all three clouds, scored twice — once by a deterministic rubric, once by re-ranking the same stored drafts with a model judge:

![Win rate and regret under two scorers, 24 briefs. Azure's gpt-5-mini wins 43 percent under the rubric and 87 percent under the model judge, with regret 0.97 and 0.52. GCP's gemini-2.5-flash wins 43 percent under both, with regret 1.54 and 2.21. AWS's nova-micro wins 33 percent under the rubric and none at all under the model judge, with regret 1.32 rubric and 9.38 under the model judge](img/medium/07-scorer-changes-the-answer.png)

![Availability across the same 24 briefs, identical under both scorers: aws/nova-micro 100 percent, azure/gpt-5-mini 96 percent, gcp/gemini-2.5-flash 58 percent. The failure recorded against the 58 percent leg is a Vertex 429, so quota is the documented cause rather than a proven one](img/medium/08-availability.png)

Take the good news and the bad news in that order, because both are real.

**Bedrock was the only leg that answered every single time.** 100% availability, where Gemini managed 58% — it hit Vertex quota — and Azure 96%. On this corpus the most valuable property any participant had was *producing an answer at all*, and the AWS leg is the only one that never failed to. It was also consistently the fastest brain in the mesh.

**And it wrote the worst drafts, by a margin the rubric could not see.** Under a deterministic rubric Nova sits 1.32 points behind the panel's best; under a model judge it sits **9.38** behind, and wins none of the 24 briefs. It also ignored its search tool entirely in **7 of 7** drafts under my first instruction, improving to 1 in 7 only after two prompt revisions.

Before anyone reads that as a Bedrock result: `us.amazon.nova-micro-v1:0` is a small, cheap model that I inherited as a default from an earlier project where the task was a **two-field currency lookup**. It is a poor default for drafting prose and I would not have chosen it for this. What that row measures is a default nobody revisited — which is its own lesson, because `BEDROCK_MODEL_ID` is one environment variable and the audit keys its rows on the model name, so changing it starts a new row rather than pooling two models into one.

Twenty-four briefs is enough to compute a rate and not enough to trust one. All of mine were technology surveys. I would not quote these as a model comparison.

---

## Quick reference, AWS side

**`A2A version '0.3' is not supported by this handler`.** AgentCore dropped the `A2A-Version` header. Fill it server-side when absent, and only when absent.

**One leg costs ~6s and the slow call moves around.** A fresh session id per call, each getting its own microVM. Pin the session id.

**403 on the agent card, 200 on the invocation.** `GetAgentCard` is a separate IAM action from `InvokeAgentRuntime`. Grant both; you do not need `Resource: "*"`.

**`InvalidIdentityToken` from STS.** The token could not be validated at all — provider setup. If you created an IAM OIDC provider for `accounts.google.com`, delete it; AWS federates with Google natively.

**`AccessDenied` from STS.** The token validated and your trust conditions did not match. Check `oaud` versus `aud` before anything else.

**The container never gets invoked.** Check `/ping` returns `{"status": "Healthy"}` on port 9000, and that the image is ARM64.

**Your brain probe reports `unknown`.** AgentCore's endpoint ends in `/invocations/`, so an arbitrary path like `/health` is not reachable from outside. Put what you need on the agent card instead — it is fetched over the same authenticated path the calls use.

---

## The short version

- **The AgentCore contract is narrow and every clause is load-bearing.** Port 9000, path `/`, `GET /ping`, ARM64. Get one wrong and the container never reaches invocation.
- **AgentCore drops `A2A-Version`.** Two other clouds forward it. Treat an absent header as current, never as old.
- **A session is a microVM.** Minting one per call buys you a ~6s cold start you will misattribute to the client, the model, or the distance.
- **Grant `GetAgentCard` alongside `InvokeAgentRuntime`.** Discovery is privileged separately, and you do not need a wildcard resource.
- **You can reach Bedrock from another cloud with no stored key.** Google-minted OIDC → STS → SigV4, and do not create an IAM OIDC provider for Google.
- **Log the provider's own words at every auth boundary.** AWS names the missing action in the response body; an adapter that keeps only the status code throws away the answer.
- **Revisit your model default before you publish numbers about it.** Mine was inherited from a task that was a two-field lookup, and it shows.

A2A did what it promised. Everything above is what sits just outside the protocol — and on this cloud, most of it is the platform rather than the framework.

---

**Repo:** [github.com/xbill9/multicloud-a2a-subagent](https://github.com/xbill9/multicloud-a2a-subagent) — the three agents, the coordinator and judge, the 3×3 interop matrix, the negative controls and the deploy scripts. `docs/INTEROP.md` carries each finding with the date it was measured.

---

*Every table in this piece is an image, because Medium renders no markdown tables. They are generated from the measured numbers by `docs/img/make_medium_graphics.py`, so they cannot drift from the results they describe without the script drifting too.*
