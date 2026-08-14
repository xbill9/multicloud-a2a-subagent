import inspect
from pathlib import Path

import pytest

from coordinator.errors import AdapterError, FailureKind
from coordinator.models import ResearchRequest
from matrix.model import Cell, MatrixReport
from matrix.runner import (
    COORDINATOR_CLOUD_ENV,
    Server,
    coordinator_cloud,
    hop_kind,
    probe,
    render_table,
)

SERVER = Server("azure", "Azure", "agent-framework A2AExecutor", "http://127.0.0.1:10003")
GCP_SERVER = Server("gcp", "Google Cloud", "adk to_a2a", "http://127.0.0.1:10001")


def cell(client_stack: str, server: str, ok: bool, **kwargs) -> Cell:
    return Cell(
        client_stack=client_stack,
        server=server,
        server_cloud=server.upper(),
        server_stack="stack",
        ok=ok,
        **kwargs,
    )


def request() -> ResearchRequest:
    return ResearchRequest(topic="agent-to-agent protocols", max_words=300)


def test_report_preserves_declaration_order():
    report = MatrixReport(
        request_summary="100 USD -> EUR",
        model_mode="direct",
        cells=[
            cell("a2a-sdk", "gcp", True),
            cell("a2a-sdk", "aws", True),
            cell("agent-framework", "gcp", True),
        ],
    )
    assert report.client_stacks == ["a2a-sdk", "agent-framework"]
    assert report.servers == ["gcp", "aws"]


def test_missing_sdk_is_excluded_from_the_success_rate():
    """An uninstalled client SDK is not a protocol failure and must not read as one."""
    report = MatrixReport(
        request_summary="100 USD -> EUR",
        model_mode="direct",
        cells=[
            cell("a2a-sdk", "gcp", True),
            cell("google-adk", "gcp", False, failure_kind="sdk-missing"),
        ],
    )
    assert len(report.attempted) == 1
    table = render_table(report)
    assert "1/1 attempted cells succeeded" in table
    assert "skipped (SDK not installed): google-adk" in table


def test_table_reports_failures_with_detail():
    report = MatrixReport(
        request_summary="100 USD -> EUR",
        model_mode="direct",
        cells=[cell("a2a-sdk", "azure", False, failure_kind="protocol", detail="empty reply")],
    )
    table = render_table(report)
    assert "0/1 attempted cells succeeded" in table
    assert "a2a-sdk -> azure: empty reply" in table


def test_lookup_of_an_absent_cell_is_none():
    report = MatrixReport(request_summary="x", model_mode="direct", cells=[])
    assert report.cell("a2a-sdk", "gcp") is None


async def test_probe_records_adapter_failure_kind(monkeypatch):
    class Failing:
        async def research(self, request):
            raise AdapterError(FailureKind.TRANSPORT, "connection refused")

    monkeypatch.setattr("matrix.runner.load_client", lambda *a, **k: Failing())
    result = await probe("a2a-sdk", SERVER, request(), timeout_s=1)

    assert result.ok is False
    assert result.failure_kind == "transport"
    assert "refused" in result.detail


async def test_probe_records_uninstalled_sdk_without_raising(monkeypatch):
    def missing(*args, **kwargs):
        raise ImportError("No module named 'strands'")

    monkeypatch.setattr("matrix.runner.load_client", missing)
    result = await probe("a2a-sdk", SERVER, request(), timeout_s=1)

    assert result.failure_kind == "sdk-missing"


async def test_probe_catches_exceptions_a_client_failed_to_map(monkeypatch):
    """A vendor SDK can raise outside our error mapping; the matrix must survive it."""

    class Exploding:
        async def research(self, request):
            raise KeyError("supported_interfaces")

    monkeypatch.setattr("matrix.runner.load_client", lambda *a, **k: Exploding())
    result = await probe("a2a-sdk", SERVER, request(), timeout_s=1)

    assert result.failure_kind == "unmapped"
    assert "KeyError" in result.detail


@pytest.mark.parametrize("bad", ["", "not-a-stack"])
def test_render_handles_empty_report(bad):
    report = MatrixReport(request_summary=bad, model_mode="direct", cells=[])
    assert "0/0 attempted cells succeeded" in render_table(report)


def test_local_mesh_classifies_every_leg_as_local(monkeypatch):
    """Unset means loopback: nothing is claimed about crossing a boundary."""
    monkeypatch.delenv(COORDINATOR_CLOUD_ENV, raising=False)
    assert coordinator_cloud() is None
    assert hop_kind(GCP_SERVER, None) == "local"
    assert hop_kind(SERVER, None) == "local"


@pytest.mark.parametrize("value", ["gcp", "GCP", "  gcp  "])
def test_coordinator_cloud_is_normalised(monkeypatch, value):
    monkeypatch.setenv(COORDINATOR_CLOUD_ENV, value)
    assert coordinator_cloud() == "gcp"


def test_blank_coordinator_cloud_is_not_a_cloud_named_empty(monkeypatch):
    monkeypatch.setenv(COORDINATOR_CLOUD_ENV, "   ")
    assert coordinator_cloud() is None


def test_hop_kind_separates_the_coordinators_own_cloud(monkeypatch):
    assert hop_kind(GCP_SERVER, "gcp") == "in-cloud"
    assert hop_kind(SERVER, "gcp") == "cross-cloud"


@pytest.mark.asyncio
async def test_probe_records_the_hop_on_a_failed_cell(monkeypatch):
    """A denied in-cloud cell must still be labelled, or the footnote loses it."""

    def deny(*a, **k):
        raise AdapterError(FailureKind.AUTHENTICATION, "denied")

    monkeypatch.setattr("matrix.runner.credentials_for", deny)
    result = await probe("a2a-sdk", GCP_SERVER, request(), timeout_s=1, hop="in-cloud")

    assert result.ok is False
    assert result.hop == "in-cloud"


def test_in_cloud_cells_are_marked_and_excluded_from_the_interop_count():
    report = MatrixReport(
        request_summary="100 USD -> EUR",
        model_mode="direct",
        cells=[
            cell("a2a-sdk", "gcp", True, hop="in-cloud"),
            cell("a2a-sdk", "aws", True, hop="cross-cloud"),
            cell("a2a-sdk", "azure", True, hop="cross-cloud"),
        ],
    )
    assert report.in_cloud_servers == ["gcp"]

    table = render_table(report)
    assert "3/3 attempted cells succeeded" in table
    assert "of which 2 crossed a cloud boundary and 1 did not" in table
    assert "gcp*" in table
    assert "* in-cloud hop: gcp" in table
    # The columns that did cross must not be marked.
    assert "aws*" not in table
    assert "azure*" not in table


def test_brain_label_comes_from_the_servers_not_the_runner(monkeypatch):
    """The regression: the runner is a different container once deployed.

    Reading the runner's own model-mode variable produced a table that said
    brain=direct while every agent in it was running a model.

    This set `CURRENCY_MODEL_MODE` until 2026-08-13, by which time no code read
    that name -- so the guard was inert and would have passed with the defect
    fully restored. A regression test pinned to a renamed variable does not
    fail; it stops testing, silently, which is the worse of the two.
    """
    monkeypatch.setenv("RESEARCH_MODEL_MODE", "direct")
    report = MatrixReport(
        request_summary="solid-state batteries in 2026",
        model_mode="direct",
        cells=[
            cell("a2a-sdk", "gcp", True, server_brain="llm"),
            cell("a2a-sdk", "aws", True, server_brain="llm"),
            cell("a2a-sdk", "azure", True, server_brain="llm"),
        ],
    )
    assert report.brain_summary == "llm"
    assert "brain=llm" in render_table(report)


def test_a_mixed_mesh_is_not_summarised_as_one_word():
    report = MatrixReport(
        request_summary="100 USD -> EUR",
        model_mode="direct",
        cells=[
            cell("a2a-sdk", "gcp", True, server_brain="llm"),
            cell("a2a-sdk", "aws", True, server_brain="direct"),
        ],
    )
    assert report.brain_summary == "mixed (gcp=llm, aws=direct)"


def test_one_unreachable_server_does_not_get_a_confident_label():
    """'unknown' must not be averaged away into the others' answer."""
    report = MatrixReport(
        request_summary="100 USD -> EUR",
        model_mode="direct",
        cells=[
            cell("a2a-sdk", "gcp", True, server_brain="llm"),
            cell("a2a-sdk", "aws", False, server_brain="unknown"),
        ],
    )
    assert report.brain_summary == "mixed (gcp=llm, aws=unknown)"


def test_brain_defaults_to_unknown_rather_than_direct():
    """A cell nobody asked must not read as a deliberate 'direct'."""
    assert cell("a2a-sdk", "gcp", True).server_brain == "unknown"


@pytest.mark.asyncio
async def test_server_brain_reads_the_health_endpoint():
    import httpx

    from matrix import runner as runner_module

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/health")
        return httpx.Response(200, json={"status": "ok", "agent": "x", "brain": "llm"})

    original = httpx.AsyncClient

    def fake_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return original(*args, **kwargs)

    runner_module.httpx.AsyncClient = fake_client
    try:
        assert await runner_module.server_brain(GCP_SERVER) == "llm"
    finally:
        runner_module.httpx.AsyncClient = original


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        {"status": "ok"},
        {"status": "ok", "brain": ""},
        {"status": "ok", "brain": 7},
    ],
)
async def test_a_health_reply_without_a_usable_brain_is_unknown(response):
    import httpx

    from matrix import runner as runner_module

    original = httpx.AsyncClient

    def fake_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(
            lambda request: httpx.Response(200, json=response)
        )
        return original(*args, **kwargs)

    runner_module.httpx.AsyncClient = fake_client
    try:
        assert await runner_module.server_brain(GCP_SERVER) == "unknown"
    finally:
        runner_module.httpx.AsyncClient = original


@pytest.mark.asyncio
async def test_an_unreachable_agent_is_unknown_not_a_crash():
    """The label must never fail the run: it is a label."""
    from matrix.runner import server_brain

    unreachable = Server("gcp", "Google Cloud", "adk to_a2a", "http://127.0.0.1:9")
    assert await server_brain(unreachable, timeout_s=1.0) == "unknown"


def test_local_report_says_nothing_about_boundaries():
    """The local matrix reads exactly as it always did -- no footnote, no stars."""
    report = MatrixReport(
        request_summary="100 USD -> EUR",
        model_mode="direct",
        cells=[cell("a2a-sdk", "gcp", True), cell("a2a-sdk", "aws", True)],
    )
    table = render_table(report)
    assert "2/2 attempted cells succeeded" in table
    assert "*" not in table
    assert "crossed a cloud boundary" not in table


def test_a_missing_version_header_is_not_read_as_a_0_3_client():
    """AgentCore does not forward A2A-Version; absent must not mean 0.3.

    a2a-sdk defaults a missing header to 0.3 and then rejects it as
    unsupported, so the same code passed on Cloud Run and Container Apps and
    failed behind AgentCore with
    "A2A version '0.3' is not supported by this handler".
    """
    from a2a.utils.constants import PROTOCOL_VERSION_CURRENT, VERSION_HEADER
    from starlette.testclient import TestClient

    from agents.serving import build_agent_card, build_app, direct_executor

    card = build_agent_card(name="research_agent", url="http://testserver/", model="none")
    seen: dict[str, str] = {}

    app = build_app(direct_executor(agent="aws"), card)

    async def echo(request):
        from starlette.responses import JSONResponse

        seen["version"] = request.headers.get(VERSION_HEADER, "<absent>")
        return JSONResponse({"v": seen["version"]})

    app.router.add_route("/echo-version", echo, methods=["GET"])

    with TestClient(app) as client:
        # No A2A-Version header, exactly as AgentCore delivers it.
        body = client.get("/echo-version").json()

    assert body["v"] == PROTOCOL_VERSION_CURRENT


def test_an_explicit_old_version_is_still_rejected():
    """The middleware fills a gap; it must not overwrite a real client claim."""
    from a2a.utils.constants import VERSION_HEADER
    from starlette.responses import JSONResponse
    from starlette.testclient import TestClient

    from agents.serving import build_agent_card, build_app, direct_executor

    app = build_app(
        direct_executor(agent="aws"),
        build_agent_card(name="research_agent", url="http://testserver/", model="none"),
    )

    async def echo(request):
        return JSONResponse({"v": request.headers.get(VERSION_HEADER, "<absent>")})

    app.router.add_route("/echo-version", echo, methods=["GET"])

    with TestClient(app) as client:
        body = client.get("/echo-version", headers={VERSION_HEADER: "0.3"}).json()

    assert body["v"] == "0.3"


# --------------------------------------------------------------------------
# What each cloud's agent promises about itself. These replace the MCP server's
# tests: the GCP leg reached its rate through a stdio MCP server until that
# scaffolding was removed, and what those tests protected was a *contract*
# between the prompt and the agents. The contract is different now -- no tools,
# and a serving header that says who wrote the draft -- but it is the same kind
# of thing, and it fails the same way: silently, at the model, in a manner the
# matrix cannot explain.
# --------------------------------------------------------------------------

CLOUD_MODULES = ("agents.gcp.server", "agents.aws.server", "agents.azure.server")


def _cloud_module(name: str):
    import importlib

    return importlib.import_module(name)


@pytest.mark.parametrize("module_name", CLOUD_MODULES)
def test_every_cloud_declares_its_own_name_and_model(module_name):
    """The audit attributes a draft by these two values and nothing else."""
    module = _cloud_module(module_name)

    assert module.CLOUD in ("gcp", "aws", "azure")
    assert module_name.split(".")[1] == module.CLOUD
    # direct mode must report "none" rather than a model it is not running --
    # a row in the audit attributed to gemini-2.5-flash that was actually
    # canned text is the one error the report cannot detect from the inside.
    assert module.model_id() == "none"


def _body_without_docstring(func) -> str:
    """The function's code, with its docstring removed.

    These builders explain at length which vendor-native search they are *not*
    using and why, so a plain source grep for `google_search` finds the
    argument against it and fails the test that argument exists to support.
    """
    import ast
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    node = tree.body[0]
    body = node.body[1:] if ast.get_docstring(node) is not None else node.body
    return "\n".join(ast.unparse(statement) for statement in body)


@pytest.mark.parametrize("module_name", CLOUD_MODULES)
def test_every_cloud_gives_its_model_the_same_tool(module_name):
    """Tool *parity*, which is the inverse of what this test asserted until
    2026-08-13 and is enforced for the same reason.

    The old rule was that no cloud may have a tool, because a search tool on
    one leg and not the others turns the audit into a comparison of tool
    access. The rule was right; the resolution was to remove search everywhere,
    which left three "researchers" writing from model recall while the rubric
    scored them on citation markers they had no way to earn honestly.

    So all three have search now, and it is the *same* search:
    `protocol.search.web_search`, one implementation against one backend. Only
    Google ships a usable native search tool -- Agent Framework exports a
    protocol rather than a tool and Strands bundles none -- so per-vendor
    search would have made this an audit of retrieval products.

    What stays native, and is what the matrix measures, is each framework's own
    tool binding and tool-call loop.
    """
    module = _cloud_module(module_name)
    builder = {
        "gcp": "_llm_agent",
        "aws": "_strands_responder",
        "azure": "_foundry_agent",
    }[module.CLOUD]
    source = _body_without_docstring(getattr(module, builder))

    assert "tools=" in source, f"{module.CLOUD} gives its model no tool; the others have search"
    assert "web_search" in source, (
        f"{module.CLOUD} has a tool that is not the shared web_search. "
        f"A per-vendor search makes the audit a comparison of retrieval products."
    )
    # The native search each vendor offers, named so a future edit that reaches
    # for one trips here rather than silently unbalancing the comparison. Read
    # from the body only: these docstrings discuss the vendor tools at length,
    # explaining precisely why they are not used.
    for vendor_tool in ("google_search", "enterprise_web_search", "HostedWebSearchTool"):
        assert vendor_tool not in source, (
            f"{module.CLOUD} uses {vendor_tool}, which only one vendor has"
        )


def test_search_is_all_on_or_all_off():
    """There is no per-cloud switch, by construction.

    `RESEARCH_SEARCH_PROVIDER` is read once in `protocol.search` and every
    agent asks the same function. A per-cloud variable would be the obvious
    next feature request and the fastest way back to an unbalanced audit.
    """
    from protocol import search

    source = inspect.getsource(search)
    for cloud in ("GCP", "AWS", "AZURE"):
        assert f"RESEARCH_SEARCH_{cloud}" not in source


def test_the_shared_instruction_asks_for_a_brief_not_a_tool_call():
    from agents.common import INSTRUCTION

    assert "research" in INSTRUCTION.lower()
    assert "markdown H1" in INSTRUCTION


async def test_the_gcp_direct_agent_stamps_the_header():
    """ADK has no responder seam, so its header is written by a wrapper agent.

    That makes GCP the only cloud where the stamping could break independently
    of the parser, which is why it is asserted here as well as in the live
    suite.
    """
    import agents.gcp.server as gcp
    from coordinator.models import ResearchRequest
    from protocol.research import build_prompt, parse_header

    agent = gcp._direct_agent()
    prompt = build_prompt(ResearchRequest(topic="a topic"))

    class _Ctx:
        class user_content:
            parts = [type("P", (), {"text": prompt})()]

    events = [event async for event in agent._run_async_impl(_Ctx())]
    text = events[0].content.parts[0].text
    fields, body = parse_header(text)

    assert fields["agent"] == "gcp"
    assert fields["brain"] == "direct"
    assert "a topic" in body


def test_no_mcp_scaffolding_remains():
    """The stdio MCP server and its client are gone from this repo.

    Asserted rather than trusted because the removal also dropped the `mcp<2`
    pin from the root Dockerfile: anything that reintroduces an MCP import
    reintroduces that pin's failure, and the container start it fails at
    reports only "failed to start and listen on port 8080".

    Checked against the source tree rather than by importing, because
    ``import mcp_server`` still succeeds on a developer machine that has a
    predecessor repo editable-installed -- its finder claims ``mcp_server``
    and ``coordinator.*`` and serves them from *that* checkout. An import
    probe here would be testing the machine, not the repo.
    """
    root = Path(__file__).resolve().parent.parent

    assert not (root / "mcp_server").exists()
    assert not (root / "coordinator" / "mcp_stdio.py").exists()
    assert "mcp" not in (root / "pyproject.toml").read_text()


# --------------------------------------------------------------------------
# Which model each cloud runs, and how it is set
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("module_name", "uniform", "native"),
    [
        ("agents.gcp.server", "RESEARCH_MODEL_GCP", "GENAI_MODEL"),
        ("agents.aws.server", "RESEARCH_MODEL_AWS", "BEDROCK_MODEL_ID"),
        (
            "agents.azure.server",
            "RESEARCH_MODEL_AZURE",
            "AZURE_AI_MODEL_DEPLOYMENT_NAME",
        ),
    ],
)
def test_every_cloud_answers_the_same_model_knob(module_name, uniform, native, monkeypatch):
    """One name that reaches all three. The clouds spell the model three ways
    natively, and a knob that reaches two agents out of three produces an audit
    comparing a changed model against two unchanged ones -- keyed on
    `cloud/model`, so it looks like a result rather than a misconfiguration."""
    module = _cloud_module(module_name)
    monkeypatch.setenv("RESEARCH_MODEL_MODE", "llm")
    monkeypatch.setenv(uniform, "some-model-v9")
    assert module.model_id() == "some-model-v9"


@pytest.mark.parametrize(
    ("module_name", "uniform", "native"),
    [
        ("agents.gcp.server", "RESEARCH_MODEL_GCP", "GENAI_MODEL"),
        ("agents.aws.server", "RESEARCH_MODEL_AWS", "BEDROCK_MODEL_ID"),
        (
            "agents.azure.server",
            "RESEARCH_MODEL_AZURE",
            "AZURE_AI_MODEL_DEPLOYMENT_NAME",
        ),
    ],
)
def test_the_vendor_native_name_still_works(module_name, uniform, native, monkeypatch):
    """Kept because it is what a reader following Bedrock or Foundry docs will
    reach for, and because deploy_aws.sh scopes the IAM policy by it."""
    module = _cloud_module(module_name)
    monkeypatch.setenv("RESEARCH_MODEL_MODE", "llm")
    monkeypatch.delenv(uniform, raising=False)
    monkeypatch.setenv(native, "native-name-v2")
    assert module.model_id() == "native-name-v2"


@pytest.mark.parametrize(
    ("module_name", "uniform", "native"),
    [
        ("agents.gcp.server", "RESEARCH_MODEL_GCP", "GENAI_MODEL"),
        ("agents.aws.server", "RESEARCH_MODEL_AWS", "BEDROCK_MODEL_ID"),
    ],
)
def test_the_uniform_name_wins_over_the_native_one(module_name, uniform, native, monkeypatch):
    monkeypatch.setenv("RESEARCH_MODEL_MODE", "llm")
    monkeypatch.setenv(native, "native-name-v2")
    monkeypatch.setenv(uniform, "uniform-name-v3")
    assert _cloud_module(module_name).model_id() == "uniform-name-v3"


def test_azure_refuses_to_guess_a_deployment_name(monkeypatch):
    """A Foundry deployment name is account-local. Guessing turns a missing
    setting into a provider 404, which reads as a protocol failure to whoever
    is watching the matrix."""
    module = _cloud_module("agents.azure.server")
    monkeypatch.setenv("RESEARCH_MODEL_MODE", "llm")
    monkeypatch.delenv("RESEARCH_MODEL_AZURE", raising=False)
    monkeypatch.delenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", raising=False)
    with pytest.raises(RuntimeError, match="no model configured for azure"):
        module.model_id()


@pytest.mark.parametrize("module_name", CLOUD_MODULES)
def test_a_configured_model_is_still_none_in_direct_mode(module_name, monkeypatch):
    """`Draft.model` is what `evaluations.report` keys a row on. A cloud that
    reported a configured model while returning canned text would put
    scaffolding into that model's score."""
    module = _cloud_module(module_name)
    monkeypatch.setenv("RESEARCH_MODEL_MODE", "direct")
    monkeypatch.setenv(f"RESEARCH_MODEL_{module.CLOUD.upper()}", "some-model-v9")
    assert module.model_id() == "none"


# --------------------------------------------------------------------------
# The instruction that makes a researcher research
# --------------------------------------------------------------------------


def test_the_instruction_tells_every_cloud_to_search():
    """All three get a search tool; until 2026-08-14 nothing told them to use
    it, and two of three never did.

    Measured on the deployed mesh: a draft scored 5.0 of 5 on `evidence` having
    made zero searches, and on the next day AWS scored 19.0 of 25 with zero
    searches and zero citations. Tool parity in *availability* is not parity in
    *use*.
    """
    from agents.common import INSTRUCTION

    lowered = INSTRUCTION.lower()
    assert "search" in lowered
    assert "always search" in lowered, "the instruction does not require it"
    assert "before you write" in lowered


def test_the_instruction_forbids_citing_a_url_it_did_not_open():
    """The failure this is aimed at is specific: a model that cites a
    plausible-looking URL it reconstructed from memory. One such citation was
    unresolvable from two independent networks on 2026-08-14."""
    from agents.common import INSTRUCTION

    lowered = INSTRUCTION.lower()
    assert "only cite a url that appeared in your search results" in lowered
    assert "not open is not a source" in lowered


def test_the_instruction_carries_a_version():
    """The prompt is the experiment's independent variable, exactly as the
    rubric is. Change it and runs either side are not comparable, and an audit
    that averages them reports a change in the prompt as a change in the
    models."""
    from agents.common import INSTRUCTION_VERSION

    assert INSTRUCTION_VERSION >= 2


async def test_a_draft_carries_the_prompt_version_that_produced_it():
    """Behavioural, not a source grep.

    AWS stamps through `wrap_responder` while GCP and Azure stamp inline -- the
    two frameworks give no responder seam -- so grepping each module for the
    constant fails on the one that is doing it correctly by delegation. What
    matters is what comes out on the wire.
    """
    from agents.common import INSTRUCTION_VERSION, direct_reply, wrap_responder
    from protocol.research import build_prompt, parse_header
    from protocol.models import ResearchRequest

    responder = wrap_responder(direct_reply, agent="aws", model="nova")
    reply = await responder(build_prompt(ResearchRequest(topic="solid-state batteries")))
    fields, _body = parse_header(reply)

    assert fields["pv"] == str(INSTRUCTION_VERSION)


@pytest.mark.parametrize("module_name", ("agents.gcp.server", "agents.azure.server"))
def test_the_two_clouds_that_stamp_inline_use_the_shared_version(module_name):
    """ADK and Agent Framework offer no responder seam, so these two write the
    header themselves. They must read the same constant rather than a literal
    that can drift from it."""
    source = inspect.getsource(_cloud_module(module_name))

    assert "INSTRUCTION_VERSION" in source
