#!/usr/bin/env python3
"""Turn a dev.to article into its Medium version.

Medium renders no markdown tables, so each one becomes an image. Doing that by
hand is the slow part -- and it is the same tables across three articles, so it
is also the part worth doing once. This holds the whole mapping: which table
becomes which image, and the alt text that goes with it.

**Alt text lives here and nowhere else.** Every table in these pieces is an
image, so without it a screen reader and Medium's search index get nothing from
a third of the article. Medium's caption field takes the same text.

    python3 docs/make_medium.py            # regenerate all three
    python3 docs/make_medium.py gde        # just one

Regenerate after editing any dev.to article, or the two drift.
"""

import re
import sys
from pathlib import Path

DOCS = Path(__file__).parent

#: Every image, with the alt text that states its numbers in words.
IMAGES = {
    "stacks": ("01-three-stacks.png",
        "The three stacks: Google runs an ADK LlmAgent on gemini-2.5-flash, served by to_a2a() "
        "on Cloud Run in us-central1, an in-cloud hop; AWS runs a Strands Agent on nova-micro, "
        "served by a2a-sdk routes on Bedrock AgentCore in us-west-2, cross-cloud; Azure runs an "
        "Agent Framework Agent on gpt-5-mini on Foundry, served by A2AExecutor on Container Apps "
        "in westus2, cross-cloud"),
    "constants": ("02-held-constant.png",
        "What is shared versus what differs. Shared, exactly one implementation: the brief and its "
        "focus questions, the versioned instruction, the search tool and its six-call budget, the "
        "versioned scoring rubric, the wire format of markdown plus one stamped header, and the "
        "failure taxonomy. Different on purpose: the agent framework, the model, the serving stack, "
        "the hosting platform, the credential mechanism, and the tool-binding API"),
    "card": ("03-bad-card.png",
        "Which client survives a card advertising 0.0.0.0:8080. The a2a-sdk client is ok because it "
        "rewrites the interfaces after card resolution. The agent-framework A2AAgent is ok because it "
        "never routes by card, so a bad card is inert. Google ADK's own RemoteA2aAgent fails, because "
        "it routes by card and dials 0.0.0.0:8080"),
    "contracts": ("04-platform-contracts.png",
        "The three runtime contracts. Cloud Run: port $PORT 8080, your own invoke path and health "
        "route, any architecture, source buildpack build, one deploy flag for ingress auth, cold "
        "starts per instance, A2A-Version header forwarded. AgentCore Runtime: port 9000, invoke path "
        "slash with the platform exposing /invocations/, health is GET /ping returning Healthy, ARM64 "
        "required, image build, IAM plus CUSTOM_JWT, cold starts per session as a microVM, and the "
        "A2A-Version header is dropped. Container Apps: port 8080, your own paths, amd64, image build, "
        "ingress auth is a separate deploy step, cold starts per revision replica, header forwarded"),
    "session": ("05-session-cold-start.png",
        "AgentCore session cold starts. With a fresh session id per call, the default, five runs "
        "measured 5926 to 6037 milliseconds. With the session id pinned, two runs measured 704 to 710 "
        "milliseconds. It presented as a fixed per-client cost until the slow cell moved between "
        "clients, and a fixed cost cannot move"),
    "models": ("06-three-models.png",
        "Three deliberately unmatched models. gemini-2.5-flash is a fast general model reached through "
        "ADK to Vertex, and is the ADK path's default. nova-micro is small and cheap, reached through "
        "Strands to Bedrock, inherited from a two-field lookup task and a poor default for prose. "
        "gpt-5-mini is a reasoning deployment reached through Agent Framework to Foundry, forced "
        "because store=False needs encrypted reasoning content"),
    "scorers": ("07-scorer-changes-the-answer.png",
        "Win rate and regret under two scorers, 24 briefs. Azure's gpt-5-mini wins 43 percent under the "
        "rubric and 87 percent under the model judge, with regret 0.97 and 0.52. GCP's gemini-2.5-flash "
        "wins 43 percent under both, with regret 1.54 and 2.21. AWS's nova-micro wins 33 percent under "
        "the rubric and none at all under the model judge, with regret 1.32 rubric and 9.38 under the "
        "model judge"),
    "availability": ("08-availability.png",
        "Availability across the same 24 briefs, identical under both scorers: aws/nova-micro 100 "
        "percent, azure/gpt-5-mini 96 percent, gcp/gemini-2.5-flash 58 percent. The failure recorded "
        "against the 58 percent leg is a Vertex 429, so quota is the documented cause rather than a "
        "proven one"),
    "search": ("09-search-use.png",
        "Zero-search drafts, with the same tool and the same six-call budget on every cloud. AWS under "
        "instruction v1: 7 of 7 drafts made no search. AWS under v2: 2 of 9. AWS under v3: 1 of 7. "
        "Azure across all versions: 1 of 16. GCP under v3: none, because it spends the whole six-call "
        "budget every run"),
    "agentcore": ("10-agentcore-contract.png",
        "The AgentCore Runtime container contract, with Cloud Run and Container Apps as the control "
        "column. Port 9000 rather than 8080. Invoke path slash, with the platform exposing "
        "/invocations/. Health is GET /ping returning Healthy. ARM64 is required. The card sits at "
        "/.well-known/agent-card.json on all three. AgentCore drops the A2A-Version header where the "
        "other two forward it, and its cold-start unit is a session mapped to a microVM rather than an "
        "instance or a revision replica"),
}

#: Which table becomes which image, in document order. A slot may hold two
#: images: the six-column results table reads better split into the comparison
#: and the availability bars than squeezed into one figure.
ARTICLES = {
    "framework": {
        "standfirst": "The same research agent built three times — ADK on Cloud Run, Strands on "
                      "Bedrock AgentCore, Agent Framework on Container Apps — and what only shows up "
                      "once you hold everything else still",
        "tables": ["stacks", "constants", "card", "contracts", "session", "models",
                   ["scorers", "availability"], "search"],
    },
    "gde": {
        "standfirst": "One ADK agent on Cloud Run, serving A2A to clients that are not ADK — and the "
                      "Google-side findings that only a deployment and a foreign caller can produce",
        "tables": ["card", ["scorers", "availability"]],
    },
    "aws": {
        "standfirst": "A Strands agent on Bedrock AgentCore, answering callers on Google Cloud and "
                      "Azure — the container contract, the header the platform drops, and the session "
                      "that is a cold start",
        "tables": ["agentcore", "session", ["scorers", "availability"]],
    },
}

TABLE_RE = re.compile(r"(?:^\|.*\n)+", re.M)


def figure(key: str) -> str:
    name, alt = IMAGES[key]
    return f"![{alt}](img/medium/{name})"


def convert(slug: str) -> Path:
    spec = ARTICLES[slug]
    source = DOCS / f"article-devto-{slug}.md"
    front, body = re.match(r"^---\n(.*?)\n---\n(.*)$", source.read_text(), re.S).groups()
    title = re.search(r'^title:\s*"?(.+?)"?$', front, re.M).group(1)

    slots = list(spec["tables"])
    found = TABLE_RE.findall(body)
    if len(found) != len(slots):
        raise SystemExit(
            f"{source.name}: {len(found)} tables but {len(slots)} image slots. "
            f"A table was added or removed -- update ARTICLES[{slug!r}]."
        )

    def replace(_match, _i=[-1]):
        _i[0] += 1
        keys = slots[_i[0]]
        keys = keys if isinstance(keys, list) else [keys]
        return "\n\n".join(figure(k) for k in keys) + "\n"

    out = TABLE_RE.sub(replace, body).strip()
    page = (
        f"# {title}\n\n### {spec['standfirst']}\n\n{out}\n\n---\n\n"
        "*Every table in this piece is an image, because Medium renders no markdown tables. They are "
        "generated from the measured numbers by `docs/img/make_medium_graphics.py`, so they cannot "
        "drift from the results they describe without the script drifting too.*\n"
    )
    target = DOCS / f"article-medium-{slug}.md"
    target.write_text(page)
    return target


if __name__ == "__main__":
    wanted = sys.argv[1:] or list(ARTICLES)
    for slug in wanted:
        path = convert(slug)
        images = path.read_text().count("![")
        print(f"  {path.relative_to(DOCS.parent)}  ({images} images)")
