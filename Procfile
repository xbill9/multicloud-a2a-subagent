# Entrypoint for the source-based Cloud Run deploy. There is no Dockerfile at
# the repo root on purpose: `gcloud run deploy --source` prefers one if it
# finds it, and the GCP researcher's Dockerfile sitting here was enough to
# silently turn every containerless deploy back into a container build of the
# wrong process. The researcher's image recipe now lives at
# infra/Dockerfile.gcp as a fallback, out of the buildpack's way.
#
# `python -m uvicorn` rather than `uvicorn`: only the module form puts the
# working directory on sys.path, and the buildpack installs dependencies but
# not this repo, so `coordinator` is importable by location and nothing else.
#
# The researcher runs from this same build with its entrypoint overridden at
# deploy time (`--command python --args=-m,agents.gcp.server`), so both GCP
# processes are provably the same source.
web: python -m uvicorn coordinator.service:app --host 0.0.0.0 --port ${PORT:-8080}
