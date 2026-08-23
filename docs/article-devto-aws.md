---
title: "Mix and Match: Serving a Bedrock Agent to Google and Azure"
published: false
description: A step by step guide to running a Strands agent on Amazon Bedrock AgentCore Runtime, serving the A2A protocol to callers on Google Cloud and Azure, with no AWS access key anywhere.
tags: aws, bedrock, a2a, aiagents
---

This article provides a step by step guide for running a Strands agent on Amazon Bedrock AgentCore Runtime, and serving it over the A2A protocol to callers on Google Cloud and Azure.

The code is here:

[github.com/xbill9/multicloud-a2a-subagent](https://github.com/xbill9/multicloud-a2a-subagent)

#### What is this project trying to Do?

This project aims to answer one research brief with three agents on three clouds, over one protocol, with no stored credentials between them.

Google runs an ADK agent on Cloud Run. Azure runs an Agent Framework agent on Container Apps. AWS runs a Strands agent on AgentCore. A coordinator asks all three the same question and takes the median of what comes back.

Everything below is the AWS side of it.

#### Aren't Those Agents on the Wrong Clouds?

Mix and Match — again. The predecessor project put six directed A2A edges between Bedrock AgentCore, Microsoft Foundry and Google ADK:

[xbill9/cross-cloud-a2a-rollup](https://github.com/xbill9/cross-cloud-a2a-rollup)

That one proved the edges. This one holds the work still — same brief, same tool, same rubric — and lets the cloud be the only thing that moves.

So let's give Strands a shot at answering two other vendors' clients.

### Bedrock AgentCore Runtime

Amazon Bedrock AgentCore is a set of services for deploying and operating AI agents at scale. AgentCore Runtime is the hosting piece: a serverless runtime that runs your agent container, isolates each session in its own microVM, and handles identity and scaling. It is framework agnostic — Strands, LangGraph, CrewAI, or your own code.

More information is available here:

[Amazon Bedrock AgentCore](https://aws.amazon.com/bedrock/agentcore/)

AgentCore Runtime, not Lambda. An agent runtime is where an AWS agent goes, and hosting this one on generic compute would have made the mesh two agent runtimes and a function.

#### What is A2A

A2A (Agent2Agent) is an open protocol for agents built by different teams, on different frameworks, to call each other. An agent publishes a card at `/.well-known/agent-card.json` describing what it does and how to reach it, and speaks JSON-RPC over HTTP. This project runs A2A v1.0.

More details are available here:

[Agent2Agent (A2A) Protocol](https://a2a-protocol.org/)

#### What is Strands

Strands Agents is AWS's open source agent SDK. You give it a model, a system prompt and some tools, and it runs the loop. It is a few lines to a working agent and it does not assume Bedrock — but on Bedrock it is the shortest path there is.

More details are available here:

[Strands Agents](https://strandsagents.com/)

```python
agent = Agent(
    model=BedrockModel(model_id="us.amazon.nova-micro-v1:0"),
    system_prompt=INSTRUCTION,
    tools=[tool(web_search)],
)

async def respond(prompt: str) -> str:
    return str(await agent.invoke_async(prompt))
```

Note- Strands ships no A2A server integration, which turns out to be the easiest starting position of the three frameworks here. You drop that `respond` function behind the `a2a-sdk` reference routes and you are serving the protocol's own implementation. No adapter, no event-stream translation.

The hard part was never Strands. It was the runtime.

#### The AgentCore Contract

AgentCore Runtime's container contract is narrow, it is not written down in one place, and every clause of it differs from what the other two runtimes want:

| | AgentCore Runtime | Cloud Run | Container Apps |
|---|---|---|---|
| port | **9000** | `$PORT`, 8080 | 8080 |
| invoke path | **`/`** | yours | yours |
| health | **`GET /ping` → `{"status": "Healthy"}`** | yours | yours |
| architecture | **ARM64, required** | any | amd64 |
| card | `GET /.well-known/agent-card.json` | same | same |

Port 9000, not 8080. 8080 is the HTTP protocol's port.

Path `/`, not `/invocations`. That is the path the platform exposes to your callers, not the one your container serves.

`/ping` is the one thing you have to add, and it is not your existing `/health`. A container that fails the ping never reaches the point of being invoked:

```python
async def ping(request):
    return JSONResponse({"status": "Healthy"})
```

Note- that response deliberately omits `time_of_last_update`. A timestamp that advances on every poll reads as a continuous status change, which stops the idle session timeout from ever firing and leaks sessions until MaxLifetime.

#### Building the Image

ARM64 is required, not preferred. On an x86 host that means buildx:

```shell
docker buildx build --platform linux/arm64 --load -f infra/Dockerfile.aws .
```

The rest of the Dockerfile is ordinary. Bind to `0.0.0.0`, expose 9000, and let the deploy step set `PUBLIC_URL` once the runtime ARN exists:

```dockerfile
ENV HOST=0.0.0.0 \
    PORT=9000 \
    RESEARCH_MODEL_MODE=direct

EXPOSE 9000
CMD ["python", "-m", "agents.aws.server"]
```

`PUBLIC_URL` has to be set after the ARN exists, because the card must advertise the AgentCore invocations URL rather than the container's bind address. Leave it out and the card advertises `0.0.0.0:9000`, which is the defect that breaks Google ADK's own A2A client against a hosted server.

#### Deploying the Agent

One command builds the ARM64 image, pushes it to ECR, creates the runtime and both IAM roles:

```shell
MODEL_MODE=llm ./infra/deploy_aws.sh deploy
```

Then get the endpoint:

```shell
./infra/deploy_aws.sh url
```

```plaintext
https://bedrock-agentcore.us-west-2.amazonaws.com/runtimes/<arn>/invocations/
```

#### Verify The Installation

An authenticated leg is unproven without negative controls, so the deploy script carries them as a verb:

```shell
./infra/deploy_aws.sh verify
```

Two of them are just curl, and both must fail:

```shell
# 1. no signature -> expect 403
curl -s -o /dev/null -w '%{http_code}\n' -X POST "$URL"

# 2. unsigned agent-card fetch -> expect 403
curl -s -o /dev/null -w '%{http_code}\n' "${URL}.well-known/agent-card.json"
```

SigV4 runtimes return `ACCESS_DENIED` with no `WWW-Authenticate` header, unlike an OAuth-configured one, which 401s and advertises its authorization server.

The positive control is the coordinator on Google Cloud, because it is the only principal that can mint a token this role's trust policy accepts.

#### Test The Cross-Cloud Integration

This step tests the whole path: a coordinator on Cloud Run, minting its own OIDC tokens, calling all three agents. `GET /api/timeline` prints one run's HTTP calls in wall-clock order.

These are the AWS rows of the first three-cloud run from the deployed master, 2026-08-12. The `leg` column, the other two legs and the overlap bars are trimmed here to fit; the full eleven-line transcript is in the repo README:

```plaintext
run  2026-08-12T23:11:25+00:00   3 leg(s)   elapsed 6700ms

      at    host                                         code    took
--------  - -------------------------------------------- ---- -------
  +715ms  K metadata.google.internal/computeMetadata/v1/  200   120ms
  +860ms  K sts.us-west-2.amazonaws.com/                  200   221ms
 +1082ms  I bedrock-agentcore.us-west-2.amazonaws.com/ru  200   666ms
 +1749ms  I bedrock-agentcore.us-west-2.amazonaws.com/ru  200   164ms

  K credential   I A2A invocation
```

Read top to bottom and the whole leg is there. A token minted at `metadata.google.internal`, presented to `sts.us-west-2.amazonaws.com`, then two calls to `bedrock-agentcore.us-west-2.amazonaws.com`. Two calls, one credential — which is why the IAM policy below needs two actions and the credential is attached to the HTTP client rather than to a request.

Note- that transcript was accurate when captured. The runtime id in it is not the one deployed now, which is the argument for reading a transcript as a record and not as configuration.

#### The Header AgentCore Drops

This is the finding I would most want another AWS builder to have for free.

`a2a-sdk` reads the protocol version from an `A2A-Version` request header. When that header is absent it assumes `0.3`, and then rejects the request its own handler cannot serve:

```plaintext
A2A version '0.3' is not supported by this handler. Expected version '1.0'.
```

Cloud Run forwards the header. Container Apps forwards the header. AgentCore does not.

So the same client, the same `a2a-sdk` on both ends, and the same server code succeed on two clouds and fail on the third — with an error that blames the protocol version and names nothing about the platform that removed it.

The fix is a middleware that fills the header when it is missing, and only when it is missing:

```python
if VERSION_HEADER.lower() not in {k.lower() for k in request.headers}:
    request.scope["headers"] = [
        *request.scope["headers"],
        (VERSION_HEADER.lower().encode(), PROTOCOL_VERSION_CURRENT.encode()),
    ]
```

A header that says `0.3` is a real client statement and should still be rejected. An absent header is not evidence of an old client. It is no evidence at all.

It had also been latent for a week. The deployed image predated the version check in `a2a-sdk`, so the leg had been green for a reason that stopped being true the moment it was rebuilt.

#### A Session is a microVM

The coordinator generated a fresh AgentCore session id for every call. AgentCore gives each session its own microVM. So every call was paying for a microVM start.

It presented as a fixed per-client cost — one client's cell was always slow — until the slow cell moved between clients. A fixed per-client cost cannot move. Something per-call can:

| `google-adk` → the AWS agent | runs | measured |
|---|---|---|
| fresh session id per call (default) | 5 | 5953, 5970, 5926, 5984, 6037ms |
| session id pinned | 2 | **710, 704ms** |

The two conditions were interleaved in time, so this is not warming drift. Releasing the pin brings it straight back.

Set your session id explicitly unless you actually want per-call isolation.

Note- what is proven here is the cause of the latency, and only that. Why cold capacity lands on one call rather than another, and how many warm sessions AgentCore keeps, are still open.

#### Least Privilege: Discovery is a Separate Action

The predecessor project left an open question. Scoping `bedrock-agentcore:InvokeAgentRuntime` to `runtime/<id>` and `runtime/<id>/*` was denied 403 on the agent-card fetch, and only `Resource: "*"` worked.

The resource scope was never the problem. Discovery is a separate action.

This policy works. Card fetch and invocation both 200, no wildcard resource anywhere:

```json
{
  "Action": [
    "bedrock-agentcore:InvokeAgentRuntime",
    "bedrock-agentcore:GetAgentCard"
  ],
  "Resource": [
    "arn:aws:bedrock-agentcore:us-west-2:<acct>:runtime/research_aws-<id>",
    "arn:aws:bedrock-agentcore:us-west-2:<acct>:runtime/research_aws-<id>/*"
  ]
}
```

A missing action and a too-narrow resource both produce 403, which is exactly why widening the resource looked like the fix. Because the claim is about a live policy, it is re-checkable rather than remembered:

```shell
./infra/deploy_aws.sh scope-test
```

Note- data-plane denials do not reach CloudTrail by default, so a 403 from either cause is near-invisible until you turn them on.

The general shape holds on all three clouds. Discovery is privileged separately from invocation, and a credential that reaches the call but not the card fails somewhere that looks nothing like auth. Which is why the credential here is attached to the httpx client rather than to a single request.

#### Reaching Bedrock With No Access Key

The coordinator runs on Google Cloud and calls this agent. There is no AWS access key anywhere in it.

The chain: Cloud Run mints a workload OIDC token for an audience you choose → the coordinator presents that token to STS `AssumeRoleWithWebIdentity` → temporary credentials come back → the request is signed with SigV4.

Four traps, each of which looks like correct configuration:

**AWS federates with `accounts.google.com` natively.** Creating an explicit IAM OIDC identity provider for it breaks federation with `InvalidIdentityToken`. Azure's Entra is the opposite — there you must create one. Same-looking task, opposite rules.

**The condition keys do not mean what they are named.** `accounts.google.com:oaud` is the token's `aud`. `accounts.google.com:aud` is the token's `azp`, which is a number. Putting an audience string in `:aud` can never match.

**Audience alone is not authorization.** The audience is caller-chosen, so an audience-only condition proves only that some identity in that IdP minted the token. Pin the subject too, using the immutable numeric ID rather than an email, which can be freed and re-bound.

**`InvalidIdentityToken` and `AccessDenied` are different worlds.** The first means the token could not be validated at all, which is provider setup. The second means it validated fine and your conditions did not match, which is policy.

One habit follows from all of that, and it is worth more than the rest: log the raw provider response at every auth boundary.

In an agent system an error comes back as a tool result, and a model in the middle will happily paraphrase `AccessDenied: condition ... did not match` into "there was an issue with the credentials." A raised message is not an observable.

That habit is what answered the `GetAgentCard` question above. AWS had been naming the missing action in the response body all along, and the earlier adapter kept the status code and threw the body away.

#### How Did Nova Micro Do?

Twenty-four briefs, each answered by all three clouds, scored twice. Once by a deterministic rubric, once by re-ranking the same stored drafts with a model judge:

| cloud / model | availability | win% rubric → judge | regret rubric → judge |
|---|---|---|---|
| aws / `nova-micro` | **100%** | 33% → **0%** | 1.32 → 9.38 |
| azure / `gpt-5-mini` | 96% | 43% → 87% | 0.97 → 0.52 |
| gcp / `gemini-2.5-flash` | 58% | 43% → 43% | 1.54 → 2.21 |

Take the good news and the bad news in that order, because both are real.

Bedrock was the only leg that answered every single time. 100% availability, where Gemini managed 58% — it hit Vertex quota — and Azure 96%. On this corpus the most valuable property any participant had was producing an answer at all, and the AWS leg is the only one that never failed to. It was also consistently the fastest brain in the mesh.

And it wrote the worst drafts, by a margin the rubric could not see. Under a deterministic rubric Nova sits 1.32 points behind the panel's best. Under a model judge it sits 9.38 behind, and wins none of the 24 briefs. It also ignored its search tool entirely in 7 of 7 drafts under the first instruction, improving to 1 in 7 only after two prompt revisions.

Note- do not read that as a Bedrock result. `us.amazon.nova-micro-v1:0` is a small, cheap model inherited as a default from an earlier project where the task was a two-field currency lookup. It is a poor default for drafting prose and nobody would have chosen it for this.

What that row measures is a default nobody revisited. Twenty-four briefs is enough to compute a rate and not enough to trust one, and all of them were technology surveys. I would not quote these as a model comparison.

#### So Why? Just Why? Why Serve One Agent to Two Other Clouds?

Because the alternative is a claim rather than a measurement.

The timeline above is a record, and none of it is asserted by the page that prints it. Three separate credential mints, one per leg, each to a different audience. Two federations, no stored secret, visible as hostnames rather than as a badge next to a logo. Calls that landed on `bedrock-agentcore.us-west-2.amazonaws.com`, which is not a thing a demo can fake.

The legs overlapped, too — summed spans 9148ms against a 6700ms run. Fan-out costs about the slowest leg, not the sum.

And every finding in this article came from deploying it. The header AgentCore drops, the session that is a microVM, the IAM action nobody grants: none of them can be reproduced by a green local test suite, because locally there is no platform in the middle to edit your request.

#### Quick Reference, AWS Side

**`A2A version '0.3' is not supported by this handler`.** AgentCore dropped the `A2A-Version` header. Fill it server-side when absent, and only when absent.

**One leg costs ~6s and the slow call moves around.** A fresh session id per call, each getting its own microVM. Pin the session id.

**403 on the agent card, 200 on the invocation.** `GetAgentCard` is a separate IAM action from `InvokeAgentRuntime`. Grant both; you do not need `Resource: "*"`.

**`InvalidIdentityToken` from STS.** The token could not be validated at all, so this is provider setup. If you created an IAM OIDC provider for `accounts.google.com`, delete it. AWS federates with Google natively.

**`AccessDenied` from STS.** The token validated and your trust conditions did not match. Check `oaud` versus `aud` before anything else.

**The container never gets invoked.** Check that `/ping` returns `{"status": "Healthy"}` on port 9000, and that the image is ARM64.

**Your health probe reports `unknown`.** AgentCore's endpoint ends in `/invocations/`, so an arbitrary path like `/health` is not reachable from outside. Put what you need on the agent card instead — it is fetched over the same authenticated path the calls use.

#### Summary

Bedrock AgentCore Runtime provides a serverless home for an agent that other clouds can call over an open protocol. With a narrow container contract, one middleware, two IAM actions and a federated role, a Strands agent answers Google Cloud and Azure with no access key stored anywhere.

A2A did what it promised. Everything above is what sits just outside the protocol, and on this cloud most of it is the platform rather than the framework.

The repo carries the three agents, the coordinator and judge, the 3×3 interop matrix, the negative controls and the deploy scripts. `docs/INTEROP.md` carries each finding with the date it was measured:

[github.com/xbill9/multicloud-a2a-subagent](https://github.com/xbill9/multicloud-a2a-subagent)
