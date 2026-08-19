# Runbook

Where the thing is, how to exercise it, and what is actually true about it.
Written 2026-08-14 because the state of this project now lives in more places
than anyone will remember. Claims re-checked against the code and the corpus on
2026-08-18.

`README.md` is the argument. This is the operating manual.

**Published:** https://claude.ai/code/artifact/3617b74c-c6a4-4ada-bf36-985bd142a97b
— linked from the front end's tab bar. Kept outside the service deliberately: a
runbook reachable only from the thing it documents is no use on the morning
that thing will not start.

**The published page is not a render of this file.** Its source is
`docs/runbook-artifact.html`, hand-built, with sections this one does not have —
a ranked open-items table and per-claim status chips. It had no source in the
repo at all until 2026-08-18, which is how the two drifted: the published page
carried a retraction this file was still missing, and this file carried an
INSTRUCTION correction the published page had not been given. **Both, or
neither.** Edit this file and `docs/runbook-artifact.html` together, then
republish the HTML to the same URL.

---

## Where it is

**https://research-master-wgcq55zbfq-uc.a.run.app** — open, no token, no proxy.

| | what | where |
|---|---|---|
| `research-master` | front end, fan-out, judge | Cloud Run, us-central1, **public** |
| `research-gcp` | Gemini researcher (ADK, `to_a2a`) | Cloud Run, us-central1, private |
| `research_aws` | Bedrock researcher (Strands) | AgentCore, us-west-2, private |
| `research-azure` | Foundry researcher (Agent Framework) | Container Apps, westus2, private |
| `research-controls` | negative-controls harness | Cloud Run **job**, created by `verify` only |
| runs + feedback | append-only stores | `gs://aisprint-491218-research-audit/` |

Only the master is public. The three researchers answer 403/401 to anyone
without a federated credential, and that is the line that matters: a public
page that can still only reach its peers with one.

### The tabs

`run a brief` · `last run` · `live` (topology, telemetry, event trace) · `flow`
(lanes × rounds) · `reviews` (per-dimension scores + the critique each cloud was
sent) · `wire` (round trips with provider request ids) · `review`
(human-in-the-loop) · `audit`

---

## Testing it, cheapest first

### 1. Click it — 30 seconds

Open the URL → **live** tab → **run a brief** → send one. Credential mints,
card fetches and invocations animate as they happen; the judge lands at the
end. Then **review** to read the lineage and open the cited sources.

### 2. The suite — 8 seconds, no cloud, no spend

```bash
python3 -m pytest -q      # 349 passed, 15 skipped
ruff check .              # 2 findings, both pre-existing
```

Entirely hermetic. Includes the guards that matter: SSRF refusals on the source
fetcher, the researcher/coordinator role boundary, and `node --check` over the
page's inline JavaScript.

### 3. The local mesh — real A2A, still no spend

```bash
./infra/run_mesh.sh start                  # agents on :10001 :10002 :10003
python3 -m coordinator.cli "your topic"
./infra/run_mesh.sh stop
```

`direct`-brain, so the drafts are canned and identical on all three. This
exercises the protocol and the orchestration, **not** the models.

To watch it in the UI, point a local master at the local mesh:

```bash
python3 -m uvicorn coordinator.service:app --port 8099
# then http://127.0.0.1:8099
```

### 4. The demo — the part worth watching

```bash
./infra/demo.sh
```

Four acts, and acts 3 and 4 are the argument: a cloud goes offline and the run
**degrades instead of failing**, and a cloud phones it in and the judge
**declines to pick it**. Any demo shows three green ticks.

### 5. The controls — the expensive one, and the only one that proves auth

```bash
./infra/deploy_gcp.sh verify        # ~10 minutes, spends money
```

Each leg alone with its credentials, then each with its credential removed.
Reads the **container's** exit code: `0` answered, `3` denied
(`coordinator.cli`'s `NO_DRAFTS_EXIT`), anything else prints
`THE CONTROL DID NOT RUN`.

Trust this one least on faith and most in practice. On 2026-08-13 it caught the
Azure agent serving the internet while every other signal in the project was
green — and it had itself been reporting false passes for two unrelated reasons
before that. See the README section "The controls, and the hole they found".

---

## Testing the *instrument*, not the mesh

Whether the mesh runs is the easy question. Whether the **judge** is any good is
the open one, and the review tab is the answer.

```bash
URL=https://research-master-wgcq55zbfq-uc.a.run.app
curl -s $URL/api/calibration | python3 -m json.tool
```

Rank a few runs by hand in the **review** tab and `agreement_rate` becomes a
real number. If the human and the judge diverge, the rubric is wrong — which is
a finding, and currently the biggest untested claim here.

`reviewed: 1` today. One review is not a calibration and the field says so.

---

## Reading results

| where | what |
|---|---|
| **last run** tab | the drafts themselves, full markdown |
| **review** tab | each cloud's chain, the critique it earned, its sources |
| `GET /api/last` | the whole run as JSON, draft bodies included |
| `GET /api/lineage` | every version + critique + citations |
| `GET /api/timeline` | wall-clock wire timeline, plain text |
| `GET /api/audit` | cross-run aggregate per `cloud/model` |
| `gs://aisprint-491218-research-audit/runs.jsonl` | every run ever, append-only |

```bash
# the winning draft as markdown
curl -s $URL/api/last | python3 -c \
  "import json,sys;r=json.load(sys.stdin);print([d['body'] for d in r['drafts'] if d['source']==r['verdict']['winner']][0])"

# the whole history, read with the same tooling the service uses
gcloud storage cp gs://aisprint-491218-research-audit/runs.jsonl .
RESEARCH_EVAL_STORE=./runs.jsonl python3 -m evaluations.report
```

`?n=2` walks back a run on `/api/flow`, `/api/lineage`, `/api/timeline`,
`/api/source`.

---

## Deploying

```bash
# the whole GCP side; PUBLIC=1 opens the front end
PUBLIC=1 MODEL_MODE=llm OTEL_TRACES_EXPORTER=gcp ./infra/deploy_gcp.sh deploy
./infra/deploy_gcp.sh wire          # fold the AWS and Azure legs in

MODEL_MODE=llm ./infra/deploy_aws.sh deploy
MODEL_MODE=llm ./infra/deploy_azure.sh deploy
./infra/deploy_azure.sh foundry     # the model deployment
./infra/deploy_azure.sh fic         # Entra federated identity credential
./infra/deploy_azure.sh auth        # ENFORCE Entra on the ingress
```

**`deploy_azure.sh auth` is not optional and is easy to skip.** `fic` decides
who can *obtain* a token; `auth` decides whether the app *demands* one. Ship
only the first and the leg reports `entra-fic` while answering anyone who asks.
That is exactly what happened on 2026-08-13.

To close the front end again: unset `PUBLIC` and redeploy.

### The knobs that change what a run means

| variable | default | effect |
|---|---|---|
| `MODEL_MODE` | `direct` | `llm` puts each cloud's real model in the path |
| `RESEARCH_MODEL_{GCP,AWS,AZURE}` | per cloud | which model that cloud runs |
| `RESEARCH_JUDGE_MODE` | `rubric` | `llm` puts Gemini 2.5 Pro in the judge seat |
| `RESEARCH_MAX_ROUNDS` | 3 | `1` is the pre-loop behaviour, and the control |
| `RESEARCH_PASS_MARK` | 18.0 | below this a draft is sent back |
| `RESEARCH_SEARCH_PROVIDER` | `duckduckgo` | `tavily`/`brave` need a key; `none` disables |
| `RESEARCH_TIMEOUT_SECONDS` | 300 | per leg; both entry points read it |
| `RESEARCH_MAX_RUNS_PER_HOUR` | 30 | only enforced when public |
| `OTEL_TRACES_EXPORTER` | `none` | `gcp` → Cloud Trace, needs no endpoint |

Changing the judge, the rubric, the pass mark, **the instruction** or the
search provider changes what every subsequent run *means*.
`evaluations.report` warns on mixed judges, mixed rubric versions and mixed
prompt versions; it cannot warn about the rest.

The instruction is at **v2** since 2026-08-14 (`INSTRUCTION_VERSION`, carried
per draft as `prompt_version`). v1 said nothing about searching and two of
three models never called the tool they were given — AWS made zero searches in
7 of 7 drafts. v2 requires a search before writing and forbids citing a URL
that did not appear in the results. **Runs either side of it are not
comparable.**

---

## What is true, and what is not

Believe these — they are measured and deployed:

- three native agents on three clouds, three federation modes, each with a
  **positive and a negative control** behind it as of 2026-08-13, plus a
  wrong-audience probe on the GCP leg
- the judge loop gates a draft and sends it back with a critique; the drafts
  come back rewritten and the audit records both versions
- traces are live: wire events reach the view before their leg finishes
- OpenTelemetry to Cloud Trace on the master and the GCP researcher

Do not believe these yet:

- **The scores are not a model comparison.** 24 runs, all of them technology
  surveys, spanning two changes to the instruction.
- **"The loop lifted Gemini from 13.8 to 21.1 and changed the winner" is
  retracted.** Draft versions were not stored on 2026-08-13, so round 1 of that
  run cannot be inspected. Of the rewrites that can be, two began from a
  provider error that had been scored as a draft. The loop is demonstrated to
  retry a failed leg, not yet to improve a weak one.
- **Search use tracks the prompt, and the corpus is more modest than the
  headline.** Under `INSTRUCTION` v1 AWS made zero searches in 7 of 7 drafts;
  under v2, 2 of 9; under v3, 1 of 7. Azure has one zero-search draft in 16.
  The instruction now opens with ALWAYS SEARCH, so "the shared `INSTRUCTION`
  never tells a researcher to search" is no longer true — but *never to
  usually* is the claim, not never to always. Gemini spends the full six-search
  budget in every v3 run, so the ceiling is binding on it.
- **The rubric is uncalibrated.** Its weightings were chosen by argument.
  `agreement_rate` is the check and it stands at one review.
- **The two judges disagree.** `python -m evaluations.rejudge --judge llm` gives
  Azure 87% of the wins and AWS none, where the rubric gives no cloud a
  majority. The best-of-breed claim is a property of the scorer until a human
  says which scorer is right.
- **The model judge has judged the stored corpus, never a live run.** `rejudge`
  loads the same `LlmJudge` the service would, offline. `JUDGE_MODE` defaults
  to `rubric`, so it has never gated, critiqued or driven the loop.
- **The controls have not been re-run since the agents were rebuilt for
  `INSTRUCTION` v2 and v3.** They passed against an earlier image.
- **Nobody has opened the page in a browser from a session that could report
  back on how it looks.** The script parses, every id exists, every field is
  produced — that is not the same as it looking right.

---

## When something is wrong

```bash
# what a service thinks it is
curl -s $URL/api/health | python3 -m json.tool     # public?, limits, telemetry, peers

# each agent reports its own brain, model, search config and telemetry
TOK=$(gcloud auth print-identity-token)
curl -s -H "Authorization: Bearer $TOK" https://research-gcp-wgcq55zbfq-uc.a.run.app/health

# the last run's calls, with provider request ids
curl -s $URL/api/timeline
```

Failure kinds are load-bearing and worth reading precisely. `provider` means
the remote answered and declined; `protocol` means the reply never arrived.
Filing the first as the second turns "Bedrock refused the topic" into
"AgentCore broke A2A".

`request_id` in the timeline is the provider's own — paste it into CloudWatch
or Cloud Logging. It is the only column here that someone outside this process
can check.

### Traps this project has already paid for

- **A control that cannot run is not a control that passed.** Two separate
  causes produced six clean-looking denials while nothing was tested.
- **Never edit a script while `verify` is running.** Bash reads a script
  incrementally; the edit corrupts the run in progress.
- **The controls job runs `built_image()`** — the image from the last `deploy`.
  A code change is not in it until you redeploy.
- **`gcloud run jobs execute --wait` prints nothing on stdout.** Use `--async`
  if you need the execution name.
- **Work that is deployed but not committed does not exist.** This repo lost a
  day's work to exactly that.
