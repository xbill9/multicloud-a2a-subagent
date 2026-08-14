"""The role boundary: which cloud is allowed to contain what.

Three roles, fixed by what each cloud can do rather than by preference:

**Google is the judge.** It is the only runtime here proven to mint a workload
OIDC token for an arbitrary audience, so the coordinator, the front end, the
judge and the audit live there. It is the only member that calls out.

**AWS and Azure are researchers, and only researchers.** Each answers a brief
with *its own local model* -- Bedrock through native Strands, a Foundry
deployment through native Agent Framework -- and returns markdown over A2A. No
outbound call to another cloud, no judging, no cross-cloud credential.

**A2A is the only glue.** The clouds meet on the wire and nowhere else.

Every one of these is enforced below rather than described somewhere, because
the way this boundary breaks is never a decision. It is one import, one COPY
line, one stray env var -- each of which looks like nothing:

- `agents/common.py` took `ResearchRequest` from `coordinator.models`. One
  line, and because a Python package must be wholly present to import at all,
  both remote Dockerfiles copied the entire `coordinator` package. The judge,
  the master service, the front end and the three-cloud credential adapter were
  deployed to AWS and Azure, and nothing anywhere said so.
- `clients/` was copied into both images and never imported by either.
- `CURRENCY_MODEL_MODE=direct` rode along in both images long after the domain
  changed, naming a variable no code reads.

None of that fails a test suite, or a deploy, or a run. It is only visible if
something looks.
"""

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

#: What a researcher must not need. `coordinator` is the judge, the master
#: service and the credential adapter; `clients` is the outbound A2A stack, and
#: a researcher does not call anyone; `matrix` and `evaluations` are the
#: instrument and the audit, which are the coordinator's business.
COORDINATOR_ONLY = ("coordinator", "clients", "matrix", "evaluations")

RESEARCHER_MODULES = (
    "agents.common",
    "agents.serving",
    "agents.aws.server",
    "agents.azure.server",
    "agents.gcp.server",
    "protocol.research",
    "protocol.models",
    "protocol.errors",
)

#: The images that ship to another vendor's infrastructure.
REMOTE_DOCKERFILES = ("infra/Dockerfile.aws", "infra/Dockerfile.azure")


@pytest.mark.parametrize("module", RESEARCHER_MODULES)
def test_a_researcher_imports_without_the_coordinator(module):
    """The load-bearing assertion.

    Run in a subprocess with the coordinator-side packages blocked at the
    import system, which is the same condition the remote images are actually
    built under -- those directories are not in the container at all. An
    in-process check would pass on a module already imported by an earlier
    test.
    """
    blocked = "\n".join(f"sys.modules[{name!r}] = None" for name in COORDINATOR_ONLY)
    script = f"import sys\n{blocked}\nimport {module}\n"

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=120,
        # The non-zero exit *is* the assertion, and the stderr below is the
        # diagnosis. `check=True` would raise before either could be reported.
        check=False,
    )
    assert result.returncode == 0, (
        f"{module} cannot start without {COORDINATOR_ONLY}.\n"
        f"That import is what puts the judge on someone else's cloud.\n"
        f"{result.stderr[-2000:]}"
    )


@pytest.mark.parametrize("dockerfile", REMOTE_DOCKERFILES)
def test_a_remote_image_copies_only_the_agent_and_the_protocol(dockerfile):
    """What is in the container is the claim; the import graph is only how it
    got there. A researcher image carries the agent and the wire format."""
    copied = {
        line.split()[1]
        for line in (REPO / dockerfile).read_text().splitlines()
        if line.startswith("COPY ") and not line.split()[1].startswith(("pyproject", "README"))
    }
    assert copied == {"agents", "protocol"}, (
        f"{dockerfile} ships {sorted(copied)}. A researcher answers a brief with "
        f"its own cloud's model; it does not judge, call out, or hold a "
        f"cross-cloud credential."
    )


@pytest.mark.parametrize("dockerfile", REMOTE_DOCKERFILES)
def test_no_remote_image_carries_a_currency_artifact(dockerfile):
    """`../multicloud-adk-a2a-currency` is a reference for auth patterns. Only
    the patterns cross over -- never a name, a variable or a default.

    Both of these images set `CURRENCY_MODEL_MODE=direct` until 2026-08-13,
    which read as the deployed default and was in fact dead: the code reads
    `RESEARCH_MODEL_MODE`, so the one thing the line appeared to guarantee was
    the one thing it could not.
    """
    text = (REPO / dockerfile).read_text()
    assert "CURRENCY" not in text.upper(), f"{dockerfile} still names a currency artifact"


def test_a_researcher_never_calls_another_cloud():
    """AWS and Azure hold no outbound client and no credential adapter.

    The whole point of the topology: a researcher is reachable over A2A and
    reaches nothing. If one of them grew an outbound leg, the mesh would stop
    being a star with the judge at the centre and the audit would stop
    measuring what it says it measures.
    """
    for path in sorted((REPO / "agents").rglob("*.py")):
        source = path.read_text()
        for forbidden in ("coordinator.auth", "credentials_for", "load_client", "clients."):
            assert forbidden not in source, (
                f"{path.relative_to(REPO)} references {forbidden!r}: a researcher "
                f"does not call out."
            )


def test_each_researcher_runs_its_own_vendors_stack():
    """Native Strands on AWS, native Agent Framework on Azure, native ADK on
    GCP. A2A is the glue and the *only* glue -- if one cloud's agent were built
    on another's framework, the mesh would be one stack wearing three hats and
    every interop result would be worthless."""
    expected = {
        "agents/aws/server.py": ("strands", "BedrockModel"),
        "agents/azure/server.py": ("agent_framework", "FoundryChatClient"),
        "agents/gcp/server.py": ("google.adk", "LlmAgent"),
    }
    for path, needles in expected.items():
        source = (REPO / path).read_text()
        for needle in needles:
            assert needle in source, f"{path} no longer builds on {needle}"


# --------------------------------------------------------------------------
# Telemetry
# --------------------------------------------------------------------------


def test_telemetry_is_off_unless_configured(monkeypatch):
    """No exporter named means no exporter installed.

    A default that reaches for a collector nobody runs turns every process into
    one that logs an export failure per batch, forever, and the first thing
    anyone does about that is turn telemetry off entirely.
    """
    import importlib

    from protocol import telemetry

    monkeypatch.delenv("OTEL_TRACES_EXPORTER", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    reloaded = importlib.reload(telemetry)
    summary = reloaded.setup("research-test")

    assert summary["enabled"] is False
    assert summary["exporter"] == "none"
    assert "OTEL" in summary["reason"]


def test_a_span_is_a_no_op_when_telemetry_is_off(monkeypatch):
    """The instrumentation must cost nothing when nobody asked for it, and it
    must not change behaviour -- the mesh's own evidence trace is what the
    results rest on, and telemetry is an addition to it, never a replacement."""
    import importlib

    from protocol import telemetry

    monkeypatch.delenv("OTEL_TRACES_EXPORTER", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    reloaded = importlib.reload(telemetry)
    reloaded.setup("research-test")

    with reloaded.span("research.test", **{"research.x": 1}):
        pass


def test_a_broken_exporter_does_not_stop_a_process_serving(monkeypatch):
    """An agent that will not start because it cannot report that it started is
    the wrong trade in every direction."""
    import importlib

    from protocol import telemetry

    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "otlp")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:1/v1/traces")
    reloaded = importlib.reload(telemetry)
    summary = reloaded.setup("research-test")

    # Either it configured (the exporter fails lazily on export) or it reported
    # why it could not. It must never raise.
    assert "enabled" in summary
    with reloaded.span("research.test"):
        pass


def test_a_researcher_can_import_telemetry_without_the_coordinator():
    """Telemetry lives in `protocol` for the same reason the wire types do."""
    import subprocess
    import sys

    blocked = "\n".join(f"sys.modules[{n!r}] = None" for n in COORDINATOR_ONLY)
    result = subprocess.run(
        [sys.executable, "-c", f"import sys\n{blocked}\nimport protocol.telemetry\n"],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-1500:]
