#!/usr/bin/env bash
# Deploy the GCP side of the mesh, containerless: no Dockerfile, no image
# recipe, just source and a Procfile. Cloud Build runs the Python buildpack and
# everything below deploys the image it produced.
#
#   ./infra/deploy_gcp.sh deploy    # master + judge + front end, and the GCP researcher
#   ./infra/deploy_gcp.sh wire      # fold the AWS and Azure legs into both
#   ./infra/deploy_gcp.sh open      # grant yourself access, print the proxy command
#   ./infra/deploy_gcp.sh run       # execute the coordinator job, tail its log
#   ./infra/deploy_gcp.sh matrix    # deploy + run the 3x3 matrix, all servers
#   ./infra/deploy_gcp.sh verify    # negative controls -- run these
#   ./infra/deploy_gcp.sh url
#   ./infra/deploy_gcp.sh destroy
#
# Four processes come out of one build, in dependency order:
#
#   MASTER  Cloud Run service  front end + fan-out + judge   <- the front door
#   SERVICE Cloud Run service  the GCP researcher agent      <- one of three peers
#   JOB     Cloud Run job      the same run, unattended      <- for scheduled runs
#   MATRIX  Cloud Run job      the 3x3 interop grid
#
# Only the master is built from source; the other three deploy the *image that
# build produced*, read back off the master. One build, one digest, so "the
# researcher runs the same code as the master" is a fact about the deployment
# rather than a claim about the repo.
#
# The master runs *on Cloud Run* rather than locally because that is the whole
# point: only a Google runtime can mint a workload OIDC token for an arbitrary
# audience, and there is no local equivalent. A laptop cannot exercise this
# path at all.
#
# `deploy` gives you a one-cloud mesh. `wire` is what makes it three, and it is
# a separate verb because it depends on the other two clouds already existing:
# it reads their endpoints and identifiers back out of them rather than keeping
# a second copy here to drift.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT="${PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${REGION:-us-central1}"
# Renamed off `currency-*` on 2026-08-12, and the reason is isolation rather
# than tidiness. `~/multicloud-adk-a2a-currency` -- the predecessor mesh --
# declares the *same fourteen names* on all three clouds, so both repos were
# deploying over each other: a `deploy` here silently replaced that project's
# live AgentCore runtime and Container App with this one's code.
#
# The service account was the worst of it. `currency-coordinator@` backed three
# different projects' services at once, and because its numeric subject is what
# the AWS trust policy and the Entra FIC pin, all three shared one federated
# identity -- any of them could assume the others' roles. A shared name is a
# nuisance; a shared identity is a security boundary that does not exist.
#
# Renaming the SA is the expensive edit, because that subject is pinned on two
# other clouds. It is nonetheless handled entirely by redeploying: both sibling
# scripts read the number back out of `gcloud iam service-accounts describe` at
# deploy time rather than keeping a copy. Re-run `deploy_aws.sh deploy` and
# `deploy_azure.sh fic` after changing COORDINATOR_SA and the federation
# follows.
REPO_NAME="${REPO_NAME:-research-mesh}"
# The researcher agent on this cloud. Named for the cloud rather than the role,
# matching `research_aws` on AgentCore and `research-azure` on Container Apps:
# across the mesh the cloud is what distinguishes one researcher from another.
SERVICE="${SERVICE:-research-gcp}"
# Renamed from `research-coordinator` on 2026-08-13. Two deployed things named
# for the same role is worse than either name being wrong: `gcloud run jobs
# list` showed `research-coordinator` and `gcloud run services list` showed
# `research-master`, and nothing on either page said which one was the front
# door. This is the headless, scheduled, recorded run -- `coordinator.cli`,
# which is the right shape for a batch and a poor one for a person with a
# question. The service account keeps `coordinator` because that names the
# Python package both entry points share, and because its numeric subject is
# pinned in the AWS trust policy and the Entra FIC. A job name is pinned by
# nothing.
JOB="${JOB:-research-batch}"
MATRIX_JOB="${MATRIX_JOB:-research-matrix}"
# The front door: front end, fan-out and judge on one service.
MASTER="${MASTER:-research-master}"

# The audit outlives the instance that wrote it. Cloud Run's filesystem does
# not, so the append-only store is a GCS volume; without it every recorded run
# is discarded at the next cold start and `evaluations.report` on the deployed
# service reports on whatever has happened since the container last booted,
# which is worse than reporting nothing.
EVAL_BUCKET="${EVAL_BUCKET:-${PROJECT}-research-audit}"
EVAL_MOUNT="/eval"

# A three-cloud fan-out to two *hosted agent runtimes* took 18-25s per leg in
# the predecessor series, and the judge only starts once the slowest has
# answered. Cloud Run's 300s default is enough until one cloud is merely slow
# rather than down, at which point it turns a degraded run into a 504.
MASTER_TIMEOUT="${MASTER_TIMEOUT:-900}"
COORDINATOR_SA="${COORDINATOR_SA:-research-coordinator@${PROJECT}.iam.gserviceaccount.com}"
#: No --cloud flag, so coordinator.cli defaults to all three participants.
THREE_CLOUD_ARGS="-m,coordinator.cli,how agent-to-agent protocols change multi-cloud architecture"

# `direct` stays the default, for the reason in docs/DEPLOYMENT_PLAN.md: the
# matrix is a protocol instrument, and a model in the path makes a red cell
# ambiguous. MODEL_MODE=llm deploys the brain. ADK does not use ADC for
# Gemini, hence GOOGLE_GENAI_USE_VERTEXAI and the project/location pair --
# without them it asks for an API key and fails inside a task body with
# HTTP 200.
MODEL_MODE="${MODEL_MODE:-direct}"

# Which Gemini the researcher runs in llm mode. This was the one cloud whose
# model could not be set from its deploy script: the agent read `GENAI_MODEL`
# and nothing ever passed it, so the GCP row of the audit was pinned to the
# default while the other two were configurable. An audit keyed on
# `cloud/model` cannot afford one leg that ignores the knob.
RESEARCH_MODEL_GCP="${RESEARCH_MODEL_GCP:-${GENAI_MODEL:-gemini-2.5-flash}}"

# The judge the master runs by default. `rubric` for the same reason
# MODEL_MODE defaults to `direct`: it is deterministic and credential-free, so
# a bad verdict is a bug in the scorer rather than an opinion. JUDGE_MODE=llm
# puts Gemini in the judge seat -- and see the README on the bias that creates,
# because the judge then shares a vendor with one of the three participants.
JUDGE_MODE="${JUDGE_MODE:-rubric}"
service_url() {
  gcloud run services describe "$SERVICE" \
    --region "$REGION" --project "$PROJECT" --format='value(status.url)'
}

master_url() {
  gcloud run services describe "$MASTER" \
    --region "$REGION" --project "$PROJECT" --format='value(status.url)'
}

# The image the source build produced, by digest. Read back off the deployed
# master rather than reconstructed from a tag: a tag is a moving pointer, and
# the claim being made here is that four processes are the same *build*.
built_image() {
  gcloud run services describe "$MASTER" \
    --region "$REGION" --project "$PROJECT" \
    --format='value(spec.template.spec.containers[0].image)'
}

# The one place a container image is produced, and it produces it from source.
# There is no Dockerfile at the repo root on purpose -- `gcloud run deploy
# --source` prefers one when it finds one, so leaving the researcher's recipe
# there silently built the wrong process. It lives at infra/Dockerfile.gcp now
# and nothing in the default path reads it.
build() {
  # Created here rather than assumed. The previous version of this script
  # inherited an SA that already existed because another project had made it,
  # which is exactly how two deployments end up sharing one identity without
  # anyone deciding to.
  if ! gcloud iam service-accounts describe "$COORDINATOR_SA" --project "$PROJECT" >/dev/null 2>&1; then
    gcloud iam service-accounts create "${COORDINATOR_SA%%@*}" \
      --project "$PROJECT" \
      --display-name "research mesh master" \
      --description "Mints the OIDC tokens the three-cloud research mesh federates with"

    # IAM is eventually consistent, and `create` returning is not the same as
    # the principal being usable. Measured 2026-08-12: the very next call,
    # granting the new SA objectUser on the audit bucket, failed with
    #
    #   HTTPError 400: Service account research-coordinator@... does not exist
    #
    # for a service account that plainly did. Poll until a *binding* would
    # succeed rather than sleeping a guessed interval -- `describe` starts
    # answering before other services will accept the principal, so waiting on
    # describe alone reproduces the failure.
    local waited=0
    until gcloud iam service-accounts get-iam-policy "$COORDINATOR_SA" \
            --project "$PROJECT" >/dev/null 2>&1 || [[ "$waited" -ge 60 ]]; do
      sleep 5; waited=$((waited + 5))
    done
    echo "service account ${COORDINATOR_SA} ready after ${waited}s"
    # Even then the grant below can lose the race, so it is retried rather than
    # trusted. This is the whole cost of not reusing another project's SA.
    sleep 10
  fi

  gcloud storage buckets describe "gs://${EVAL_BUCKET}" --project "$PROJECT" >/dev/null 2>&1 || \
    gcloud storage buckets create "gs://${EVAL_BUCKET}" \
      --project "$PROJECT" --location "$REGION" --uniform-bucket-level-access

  local attempt=1
  until gcloud storage buckets add-iam-policy-binding "gs://${EVAL_BUCKET}" \
          --project "$PROJECT" \
          --member "serviceAccount:${COORDINATOR_SA}" \
          --role roles/storage.objectUser --quiet >/dev/null 2>&1; do
    if [[ "$attempt" -ge 12 ]]; then
      echo "could not grant ${COORDINATOR_SA} objectUser on gs://${EVAL_BUCKET}" >&2
      echo "after 12 attempts; the audit would silently not be written." >&2
      exit 1
    fi
    echo "  bucket grant attempt ${attempt} failed (IAM propagation); retrying"
    attempt=$((attempt + 1))
    sleep 10
  done

  # --no-allow-unauthenticated on the front end too. It is a page a person
  # opens, which is exactly the argument for making it public and exactly why
  # it is not: this service holds the credentials for three clouds, and
  # "unauthenticated but nobody knows the URL" is not an access control. Use
  # `open` to reach it. PUBLIC=1 overrides, deliberately loudly.
  local ingress="--no-allow-unauthenticated"
  if [[ "${PUBLIC:-}" == "1" ]]; then
    ingress="--allow-unauthenticated"
    echo "WARNING: PUBLIC=1 -- deploying the front end open to the internet." >&2
  fi

  gcloud run deploy "$MASTER" \
    --source "$REPO" \
    --region "$REGION" --project "$PROJECT" \
    --service-account "$COORDINATOR_SA" \
    "$ingress" \
    --timeout "$MASTER_TIMEOUT" \
    --set-env-vars "RESEARCH_COORDINATOR_CLOUD=gcp,RESEARCH_JUDGE_MODE=${JUDGE_MODE},RESEARCH_EVAL_STORE=${EVAL_MOUNT}/runs.jsonl,GOOGLE_GENAI_USE_VERTEXAI=true,GOOGLE_CLOUD_PROJECT=${PROJECT},GOOGLE_CLOUD_LOCATION=${REGION}" \
    --add-volume "name=eval,type=cloud-storage,bucket=${EVAL_BUCKET}" \
    --add-volume-mount "volume=eval,mount-path=${EVAL_MOUNT}" \
    --min-instances 0 --max-instances 1 \
    --quiet

  echo "master: $(master_url)"
}

deploy() {
  build

  local image
  image="$(built_image)"
  echo "image:  $image"

  # --no-allow-unauthenticated is the point of the exercise: the researcher
  # rejects anything without a valid Google ID token whose audience is this
  # service's own URL.
  #
  # Same image as the master, different entrypoint -- through the buildpack's
  # launcher, which is the whole finding here.
  #
  # `--command python --args=-m,agents.gcp.server` deploys and then fails the
  # startup probe with:
  #
  #   failed to resolve binary path: error finding executable "python" in PATH
  #   [/cnb/process /cnb/lifecycle /usr/local/sbin /usr/local/bin /usr/sbin
  #    /usr/bin /sbin /bin]
  #
  # A buildpack image keeps its interpreter and its installed packages in CNB
  # layers, and it is `/cnb/lifecycle/launcher` that puts them on PATH before
  # exec'ing the process. Overriding the entrypoint replaces the launcher, so
  # the override runs in an environment where the language the image was built
  # for does not exist. Run the command *through* the launcher instead and the
  # layer environment is applied first.
  #
  # Measured 2026-08-12 on this project. Note the failure has nothing to do
  # with ports despite what Cloud Run's error says -- "failed to start and
  # listen on the port" is the symptom of any startup crash.
  gcloud run deploy "$SERVICE" \
    --image "$image" \
    --region "$REGION" --project "$PROJECT" \
    --no-allow-unauthenticated \
    --port 8080 \
    --command /cnb/lifecycle/launcher --args="python,-m,agents.gcp.server" \
    --set-env-vars "RESEARCH_MODEL_MODE=${MODEL_MODE},RESEARCH_MODEL_GCP=${RESEARCH_MODEL_GCP},HOST=0.0.0.0,GOOGLE_GENAI_USE_VERTEXAI=true,GOOGLE_CLOUD_PROJECT=${PROJECT},GOOGLE_CLOUD_LOCATION=${REGION}" \
    --min-instances 0 --max-instances 2 \
    --quiet

  local url
  url="$(service_url)"
  echo "researcher: $url"
  echo "  brain ${MODEL_MODE}, model ${RESEARCH_MODEL_GCP}"

  # The master is deployed before the researcher exists, so it cannot be told
  # where the researcher is until now. This is the in-cloud leg -- master and
  # researcher are both on Cloud Run -- and it is still authenticated, because
  # an unauthenticated hop that happens to stay inside one cloud is not a
  # demonstration of anything.
  gcloud run services update "$MASTER" \
    --region "$REGION" --project "$PROJECT" \
    --update-env-vars "GCP_A2A_ENDPOINT=${url},GCP_A2A_AUTH=google-id-token" \
    --quiet >/dev/null

  # Audience alone is not authorization -- it is caller-chosen. This IAM
  # binding is what actually authorizes the call; the token only proves who
  # is asking.
  gcloud run services add-iam-policy-binding "$SERVICE" \
    --region "$REGION" --project "$PROJECT" \
    --member "serviceAccount:${COORDINATOR_SA}" \
    --role roles/run.invoker --quiet >/dev/null
  echo "granted roles/run.invoker to ${COORDINATOR_SA}"

  gcloud run jobs deploy "$JOB" \
    --image "$image" \
    --region "$REGION" --project "$PROJECT" \
    --service-account "$COORDINATOR_SA" \
    --set-env-vars "GCP_A2A_ENDPOINT=${url},GCP_A2A_AUTH=google-id-token,RESEARCH_COORDINATOR_CLOUD=gcp" \
    --command /cnb/lifecycle/launcher \
    --args="python,-m,coordinator.cli,how agent-to-agent protocols change multi-cloud architecture,--cloud,gcp" \
    --max-retries 0 --task-timeout 300s \
    --quiet
}

# The three-cloud env, assembled from the sibling scripts rather than restated
# here. Each cloud's identifiers live in exactly one place -- the script that
# created them -- so a redeployed AgentCore runtime (whose URL contains its own
# ARN) or a recreated app registration cannot leave a stale copy behind.
peer_env() {
  echo "GCP_A2A_ENDPOINT=$(service_url)"
  echo "GCP_A2A_AUTH=google-id-token"
  # Both jobs run on Cloud Run, so the gcp leg never leaves Google Cloud. The
  # matrix marks that column rather than counting it toward the interop claim;
  # unset (the local mesh) means the distinction does not arise.
  echo "RESEARCH_COORDINATOR_CLOUD=gcp"
  # An empty *value* is the dangerous case, not empty output. A sibling that
  # cannot resolve its endpoint used to emit `AWS_A2A_ENDPOINT=` and exit 0;
  # the old guard only checked that *some* assignment came back, so the empty
  # one sailed through and got pushed to the live job. The coordinator then
  # degrades over the dead leg and exits 0 too. Refuse instead.
  #
  # stderr is deliberately no longer swallowed: the sibling script explains
  # *why* it could not resolve, and that message is the entire diagnosis.
  local script block
  for script in deploy_aws deploy_azure; do
    if ! block="$("$REPO/infra/${script}.sh" env)"; then
      echo "error: ${script}.sh env failed; refusing to wire a partial mesh." >&2
      echo "       set ALLOW_PARTIAL_MESH=1 to wire the legs that do resolve." >&2
      [[ "${ALLOW_PARTIAL_MESH:-}" == "1" ]] || return 1
      continue
    fi
    block="$(printf '%s\n' "$block" | grep -E '^[A-Z][A-Z0-9_]*=' || true)"
    if printf '%s\n' "$block" | grep -qE '^[A-Z][A-Z0-9_]*=$'; then
      echo "error: ${script}.sh env resolved these to nothing:" >&2
      printf '%s\n' "$block" | grep -E '^[A-Z][A-Z0-9_]*=$' | sed 's/^/         /' >&2
      echo "       refusing to wire a leg the coordinator cannot reach." >&2
      echo "       set ALLOW_PARTIAL_MESH=1 to wire it anyway." >&2
      [[ "${ALLOW_PARTIAL_MESH:-}" == "1" ]] || return 1
    fi
    [[ -n "$block" ]] && printf '%s\n' "$block"
  done
}

wire() {
  local vars
  # ^@^ makes @ the separator: gcloud splits --set-env-vars on commas by
  # default, and a percent-encoded ARN is exactly the kind of value that
  # eventually contains one.
  vars="$(peer_env | paste -sd'@' -)"
  [[ -z "$vars" ]] && { echo "nothing to wire" >&2; exit 1; }

  # The args matter as much as the env. `deploy` pins --cloud gcp because at
  # that point one cloud is all there is; wiring is precisely the step that
  # stops being true, and leaving the flag behind produced a run that reported
  # "1/1 clouds, unverified" and exited 0 with three legs correctly configured
  # underneath it -- the env said three clouds and the args said one.
  # (`verify` also passes --args, but to `jobs execute`, which is an
  # execution-scoped override and leaves the job spec alone.)
  gcloud run jobs update "$JOB" \
    --region "$REGION" --project "$PROJECT" \
    --set-env-vars "^@^${vars}" \
    --args="$THREE_CLOUD_ARGS" --quiet >/dev/null

  # The master takes the same peers, and takes them as an *update* rather than
  # a set: `deploy` put its judge, store path and Vertex configuration in the
  # environment, and --set-env-vars here would silently drop all of it. The
  # job has no such state, which is why the two calls differ.
  gcloud run services update "$MASTER" \
    --region "$REGION" --project "$PROJECT" \
    --update-env-vars "^@^${vars}" --quiet >/dev/null

  echo "master and coordinator job wired:"
  peer_env | sed 's/^/  /'
  echo "  args: ${THREE_CLOUD_ARGS//,/ }"
  echo "  front end: $(master_url)"
}

# The front end is private, so reaching it is a step rather than a URL. The
# proxy is the honest way in: it authenticates as *you*, with your own
# roles/run.invoker, and nothing about the service is loosened to allow it.
open_ui() {
  local account
  account="$(gcloud config get-value account 2>/dev/null)"
  [[ -z "$account" ]] && { echo "no active gcloud account" >&2; exit 1; }

  gcloud run services add-iam-policy-binding "$MASTER" \
    --region "$REGION" --project "$PROJECT" \
    --member "user:${account}" \
    --role roles/run.invoker --quiet >/dev/null
  echo "granted roles/run.invoker on ${MASTER} to ${account}"
  echo
  echo "  gcloud run services proxy ${MASTER} --region ${REGION} --project ${PROJECT}"
  echo
  echo "then open http://localhost:8080"
}

run() {
  gcloud run jobs execute "$JOB" \
    --region "$REGION" --project "$PROJECT" --wait --quiet
}

# The matrix job carries the same env as the coordinator, because the whole
# point of the 3x3 grid is that every client stack drives every server through
# the same credential seam. A matrix wired to fewer peers than the coordinator
# measures a different mesh than the one the consensus run measured.
matrix() {
  local vars
  vars="$(peer_env | paste -sd'@' -)"
  gcloud run jobs deploy "$MATRIX_JOB" \
    --image "$(built_image)" \
    --region "$REGION" --project "$PROJECT" \
    --service-account "$COORDINATOR_SA" \
    --set-env-vars "^@^${vars}" \
    --command /cnb/lifecycle/launcher \
    --args="python,-m,matrix.runner" \
    --max-retries 0 --task-timeout 600s \
    --quiet >/dev/null
  gcloud run jobs execute "$MATRIX_JOB" \
    --region "$REGION" --project "$PROJECT" --wait --quiet
}

# Negative controls, run where the credentials actually are. Everything here
# uses execution-time env overrides rather than a redeploy, so the job that
# proves the denial is bit-for-bit the job that proves the success -- otherwise
# the control tests a configuration nothing else ever runs.
#
# A control that "fails" is a control that passed. Read the exit codes below as
# the assertions they are.
# Every probe is restricted to ONE cloud with --cloud. That is not tidiness: the
# mesh is a median and degrades on purpose, so a three-cloud run with one leg's
# credential removed still reaches quorum on the other two and exits 0. Read as
# a control, that exit code says "the denial was absorbed" while looking exactly
# like "there was no denial". One cloud per probe is what makes the exit code
# mean something.
probe() {
  local label cloud expect; label="$1"; cloud="$2"; expect="$3"; shift 3
  local rc=0
  echo
  echo "--- ${label}"
  gcloud run jobs execute "$JOB" \
    --region "$REGION" --project "$PROJECT" --wait --quiet \
    --args="-m,coordinator.cli,how agent-to-agent protocols change multi-cloud architecture,--cloud,${cloud}" \
    ${1+--update-env-vars "$*"} >/dev/null 2>&1 || rc=$?

  if [[ "$expect" == "deny" ]]; then
    [[ "$rc" -ne 0 ]] \
      && echo "    exit ${rc} -- denied, as required" \
      || echo "    exit 0 -- ANSWERED WITHOUT THE CREDENTIAL. This control failed:
    the leg's auth mode is a label, not a control."
  else
    [[ "$rc" -eq 0 ]] \
      && echo "    exit 0 -- answered, as required" \
      || echo "    exit ${rc} -- the POSITIVE control failed; the leg is broken
    independently of auth, and the denials above prove nothing until it is fixed."
  fi
}

verify() {
  local url; url="$(service_url)"

  echo "1. unauthenticated, from here -- no Google token at all"
  echo "   researcher /health    -> $(curl -s -o /dev/null -w '%{http_code}' -m 25 "${url}/health")   (expect 403)"
  echo "   researcher card       -> $(curl -s -o /dev/null -w '%{http_code}' -m 25 "${url}/.well-known/agent-card.json")   (expect 403)"
  # The front end is the softest target in the mesh: it holds credentials for
  # all three clouds and it is the only surface a human is meant to open, which
  # is exactly the combination that gets something made public "just to try
  # it". A 200 here means PUBLIC=1 is deployed.
  echo "   master front end      -> $(curl -s -o /dev/null -w '%{http_code}' -m 25 "$(master_url)/")   (expect 403)"
  echo
  echo "2. positive controls -- each leg alone, credentials as deployed."
  echo "   These come first: a denial only means something once you know the"
  echo "   leg answers at all."

  probe "GCP leg, as deployed"   gcp   allow
  probe "AWS leg, as deployed"   aws   allow
  probe "Azure leg, as deployed" azure allow

  echo
  echo
  echo "3. negative controls -- each leg alone, credential removed."

  probe "GCP leg, auth mode forced to none"   gcp   deny GCP_A2A_AUTH=none
  probe "AWS leg, auth mode forced to none"   aws   deny AWS_A2A_AUTH=none
  probe "Azure leg, auth mode forced to none" azure deny AZURE_A2A_AUTH=none

  # Audience is caller-chosen, so this proves less than it looks like it does --
  # see the note in docs/DEPLOYMENT_PLAN.md about the user token Cloud Run
  # accepted with gcloud's own client ID as its audience. It is still worth
  # running: it separates "the token was rejected" from "no token was sent".
  probe "GCP leg, right identity, wrong audience" gcp deny \
    GCP_A2A_AUDIENCE=https://not-this-service.example.com
}

# The bucket is deliberately not deleted. It holds the audit -- every run ever
# recorded -- and that is the one artifact here that cannot be rebuilt by
# redeploying. Remove it by hand if you mean to.
destroy() {
  gcloud run jobs delete "$MATRIX_JOB" --region "$REGION" --project "$PROJECT" --quiet || true
  gcloud run jobs delete "$JOB" --region "$REGION" --project "$PROJECT" --quiet || true
  # The pre-2026-08-13 name for the job above. A rename leaves the old resource
  # behind, still holding the coordinator service account and still wired to
  # every peer -- an orphan that can be executed by anyone who finds it and
  # will keep answering long after this repo stops mentioning it.
  gcloud run jobs delete research-coordinator \
    --region "$REGION" --project "$PROJECT" --quiet 2>/dev/null || true
  gcloud run services delete "$SERVICE" --region "$REGION" --project "$PROJECT" --quiet || true
  gcloud run services delete "$MASTER" --region "$REGION" --project "$PROJECT" --quiet || true
  echo "kept gs://${EVAL_BUCKET} -- it holds the audit; delete it by hand."
}

case "${1:-deploy}" in
  build) build ;;
  deploy) deploy ;;
  wire) wire ;;
  open) open_ui ;;
  run) run ;;
  matrix) matrix ;;
  verify) verify ;;
  url) master_url ;;
  agent-url) service_url ;;
  destroy) destroy ;;
  *) echo "usage: $0 {build|deploy|wire|open|run|matrix|verify|url|agent-url|destroy}" >&2; exit 2 ;;
esac
