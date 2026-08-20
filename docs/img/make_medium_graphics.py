#!/usr/bin/env python3
"""Render every table in the Medium article as a PNG.

Medium does not render markdown tables at all -- a pasted one becomes a wall of
pipe characters -- so the Medium version of the article carries images where the
dev.to version carries tables. This script is where those images come from.

**Every number below is hard-coded here and sourced from `README.md`,
`docs/RUNBOOK.md` and `docs/INTEROP.md`, each with the date it was measured. If
a measurement changes there, change it here too.** An image is the one place in
this repo where a stale number cannot be caught by grep, which is the whole risk
of this approach and the reason the script exists rather than a screenshot.

    uv pip install --system matplotlib
    python3 docs/img/make_medium_graphics.py

Rendered 1500px wide, which is enough for Medium's widest layout on a retina
screen. Light surface deliberately: Medium serves one image to both its themes,
and text drawn on transparency is illegible in whichever theme it was not drawn
for. A light card on a dark page is the failure mode everybody already accepts.

Palette: categorical slots 1-3 of the dataviz reference palette, validated on a
`#ffffff` surface with `--pairs all` -- worst CVD pair dE 9.2, worst
normal-vision pair dE 24.0. Aqua sits below 3:1 contrast on white, so every
chart that uses it direct-labels its values, which is the documented relief.
"""

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib import font_manager
from pathlib import Path

OUT = Path(__file__).parent / "medium"
OUT.mkdir(parents=True, exist_ok=True)

# --------------------------------------------------------------------------
# Tokens
# --------------------------------------------------------------------------

SURFACE = "#ffffff"
INK = "#0b0b0b"
INK_2 = "#52514e"
INK_3 = "#8a8a85"
RULE = "#e3e3df"
BLUE = "#2a78d6"      # categorical slot 1
ORANGE = "#eb6834"    # categorical slot 2
AQUA = "#1baf7a"       # categorical slot 3
GRAY = "#b9b9b4"       # de-emphasis

SANS = "Liberation Sans"
MONO = "Liberation Mono"

_available = {f.name for f in font_manager.fontManager.ttflist}
if SANS not in _available:
    SANS = "DejaVu Sans"
if MONO not in _available:
    MONO = "DejaVu Sans Mono"

plt.rcParams["font.family"] = SANS
plt.rcParams["figure.facecolor"] = SURFACE
plt.rcParams["savefig.facecolor"] = SURFACE

WIDTH_IN = 10.0
DPI = 150


def _save(fig, name):
    path = OUT / name
    fig.savefig(path, dpi=DPI, bbox_inches="tight", pad_inches=0.28)
    plt.close(fig)
    print(f"  {path.relative_to(Path(__file__).parent.parent.parent)}")


def _wrap(text, width):
    """Word-wrap to `width` characters, returning a list of lines."""
    if not text:
        return [""]
    words, lines, line = text.split(), [], ""
    for word in words:
        candidate = f"{line} {word}".strip()
        if len(candidate) <= width or not line:
            line = candidate
        else:
            lines.append(line)
            line = word
    lines.append(line)
    return lines


#: Rendered advance width per character at 12.5pt / 150dpi, measured off a
#: render rather than assumed -- and measured twice. The first pass used 8.4 for
#: sans, which is the average glyph and not the budget you need: a line of mixed
#: prose came out at 9.3, so wrapped lines overran their column and printed on
#: top of the next one. These are deliberately a shade wide.
PX_PER_CHAR = {"mono": 16.5, "sans": 9.6}


def _cell_style(text):
    """Any backtick makes the whole cell mono; *emphasis* is bold in the accent hue.

    Whole-cell rather than inline: a cell like "`gpt-5-mini` on Foundry" is
    mostly identifier, and mixing two fonts inside one wrapped line means
    laying out each fragment by hand for no legibility gain. Stripping and
    going mono is the honest simplification -- leaving the backticks in the
    image, which is what the first render did, is not.
    """
    family, weight, color = SANS, "normal", INK
    if "`" in text:
        text, family = text.replace("`", ""), MONO
    if text.startswith("*") and text.endswith("*") and len(text) > 1:
        text, weight, color = text[1:-1], "bold", ORANGE
    return text, family, weight, color


def _chars_for(span, family):
    """How many characters fit in a column, given the font actually used."""
    px = span * WIDTH_IN * DPI
    return max(6, int(px / PX_PER_CHAR["mono" if family == MONO else "sans"]) - 1)


def table(name, title, subtitle, columns, rows, widths, chars=None):
    """One typeset table, drawn as text on a blank canvas.

    matplotlib's own table artist is not usable here: it centres everything,
    cannot mix fonts within a row, and sizes cells to the widest string. These
    are reference tables read left to right, so they are drawn by hand.
    """
    # Height is computed from wrapped content, so no table is ever clipped.
    # Each column's wrap budget comes from its own pixel span and the font that
    # column's cell actually renders in -- not from a hand-tuned count.
    spans = [
        # 0.045 is the inter-column gutter. At 0.012 a full-width wrapped line
        # ended flush against the next column's first character, which reads as
        # one run-on string even though nothing overlaps.
        (widths[i + 1] if i + 1 < len(widths) else 1.0) - widths[i] - 0.045
        for i in range(len(widths))
    ]
    wrapped = []
    for row in rows:
        cells = []
        for i, cell in enumerate(row):
            text, family, _, _ = _cell_style(cell)
            cells.append(_wrap(text, _chars_for(spans[i], family)))
        wrapped.append(cells)
    row_lines = [max(len(cell) for cell in row) for row in wrapped]

    line_h = 0.245
    row_pad = 0.16
    head_h = 0.78 + (0.26 * len(_wrap(subtitle, 150)) + 0.08 if subtitle else 0)
    body_h = sum(n * line_h + row_pad for n in row_lines)
    fig_h = head_h + 0.42 + body_h + 0.15

    fig = plt.figure(figsize=(WIDTH_IN, fig_h))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 1)
    ax.set_ylim(0, fig_h)
    ax.axis("off")

    y = fig_h - 0.30
    ax.text(0, y, title, fontsize=19, fontweight="bold", color=INK, va="top")
    y -= 0.42
    sub_lines = _wrap(subtitle, 150) if subtitle else []
    for line in sub_lines:
        ax.text(0, y, line, fontsize=11.5, color=INK_2, va="top")
        y -= 0.26
    if sub_lines:
        y -= 0.08

    # Column header. Run through the same cell parser as the body, or a header
    # naming a model prints its own backticks.
    y -= 0.10
    for x, label in zip(widths, columns):
        text, family, _, _ = _cell_style(label)
        ax.text(x, y, text, fontsize=11.5, family=family, color=INK_3, va="top")
    y -= 0.30
    ax.plot([0, 1], [y, y], color=RULE, lw=1.2, solid_capstyle="butt")
    y -= 0.12

    for row, cells, n_lines in zip(rows, wrapped, row_lines):
        top = y
        for i, (raw, lines) in enumerate(zip(row, cells)):
            _, family, weight, color = _cell_style(raw)
            for j, line in enumerate(lines):
                ax.text(
                    widths[i], top - j * line_h, line,
                    fontsize=12.5, family=family, fontweight=weight,
                    color=color, va="top",
                )
        y = top - n_lines * line_h - row_pad

    _save(fig, name)


#: Left margin reserved for row labels. Axes start here rather than at 0.0
#: because `bbox_inches="tight"` crops to drawn content: a y-tick label sitting
#: at negative axes coordinates expands the canvas leftwards and silently
#: indents the title relative to every table image in the same article.
LABEL_GUTTER = 0.26


def _bare(ax):
    ax.set_facecolor(SURFACE)
    for side in ax.spines.values():
        side.set_visible(False)
    ax.tick_params(length=0)
    return ax


def _chart_axes(fig_h, title, subtitle):
    fig = plt.figure(figsize=(WIDTH_IN, fig_h))
    ax = _bare(fig.add_axes([LABEL_GUTTER, 0.16, 1.0 - LABEL_GUTTER, 0.60]))
    fig.text(0.0, 0.965, title, fontsize=19, fontweight="bold", color=INK, va="top")
    if subtitle:
        fig.text(0.0, 0.885, subtitle, fontsize=11.5, color=INK_2, va="top")
    return fig, ax


# --------------------------------------------------------------------------
# 01 -- the three stacks
# --------------------------------------------------------------------------

def stacks():
    table(
        "01-three-stacks.png",
        "One research agent, built three times",
        "Same brief, same instruction, same search tool, same word budget. Everything below differs on purpose.",
        ["", "Google", "AWS", "Azure"],
        [
            ["framework", "`ADK LlmAgent`", "`Strands Agent`", "`Agent Framework Agent`"],
            ["model", "`gemini-2.5-flash`", "`nova-micro`", "`gpt-5-mini` on Foundry"],
            ["served by", "`to_a2a()`", "`a2a-sdk` routes", "`A2AExecutor`"],
            ["hosted on", "Cloud Run, us-central1", "Bedrock AgentCore, us-west-2", "Container Apps, westus2"],
            ["A2A leg", "in-cloud hop", "cross-cloud", "cross-cloud"],
        ],
        widths=[0.0, 0.16, 0.44, 0.72],
    )


# --------------------------------------------------------------------------
# 02 -- what is held constant
# --------------------------------------------------------------------------

def constants():
    table(
        "02-held-constant.png",
        "Share everything that is not the variable under test",
        "When three columns differ in nine ways, no result can be attributed to any of them.",
        ["Shared, exactly one implementation", "Different, on purpose"],
        [
            ["the brief and its focus questions", "the agent framework"],
            ["the instruction, versioned", "the model"],
            ["the search tool and its six-call budget", "the serving stack"],
            ["the scoring rubric, versioned", "the hosting platform"],
            ["the wire format: markdown, one stamped header", "the credential mechanism"],
            ["the failure taxonomy", "the tool-binding API"],
        ],
        widths=[0.0, 0.55],
    )


# --------------------------------------------------------------------------
# 03 -- the bad card
# --------------------------------------------------------------------------

def bad_card():
    table(
        "03-bad-card.png",
        "Which client survives a card advertising 0.0.0.0:8080",
        "ADK's to_a2a() writes the bind address into the agent card. It cannot reproduce locally, where bind and dial addresses are the same string.",
        ["client", "against the deployed ADK server", "why"],
        [
            ["`a2a-sdk`", "*ok*", "rewrites the interfaces after card resolution"],
            ["`agent-framework A2AAgent`", "*ok*", "never routes by card, so a bad card is inert"],
            ["`google-adk RemoteA2aAgent`", "*fails*", "routes by card, dials 0.0.0.0:8080"],
        ],
        widths=[0.0, 0.30, 0.46],
    )


# --------------------------------------------------------------------------
# 04 -- platform contracts
# --------------------------------------------------------------------------

def contracts():
    table(
        "04-platform-contracts.png",
        "Each runtime imposes its own contract on the container",
        "The cells in orange are the ones that cost real time.",
        ["", "Cloud Run", "AgentCore Runtime", "Container Apps"],
        [
            ["port", "$PORT, 8080", "*9000*", "8080"],
            ["invoke path", "yours", "*/ (platform exposes /invocations/)*", "yours"],
            ["health", "yours", "*GET /ping -> Healthy*", "yours"],
            ["architecture", "any", "*ARM64, required*", "amd64"],
            ["build", "source, buildpack", "image", "image"],
            ["ingress auth", "one deploy flag", "IAM + CUSTOM_JWT", "*a separate step*"],
            ["cold-start unit", "instance", "*session -> microVM*", "revision replica"],
            ["A2A-Version header", "forwarded", "*dropped*", "forwarded"],
        ],
        widths=[0.0, 0.20, 0.40, 0.74],
    )


# --------------------------------------------------------------------------
# 05 -- AgentCore session cold start
# --------------------------------------------------------------------------

def session():
    fresh = [5953, 5970, 5926, 5984, 6037]
    pinned = [710, 704]

    fig, ax = _chart_axes(
        3.5,
        "An AgentCore session gets its own microVM",
        "google-adk to the AWS agent, same code both sides, conditions interleaved in time. Milliseconds.",
    )

    ax.scatter(fresh, [1] * len(fresh), s=120, color=ORANGE, zorder=3, clip_on=False)
    ax.scatter(pinned, [0] * len(pinned), s=120, color=BLUE, zorder=3, clip_on=False)

    ax.text(6037 + 120, 1, "5926-6037ms", va="center", fontsize=13,
            color=ORANGE, fontweight="bold")
    ax.text(710 + 120, 0, "704-710ms", va="center", fontsize=13,
            color=BLUE, fontweight="bold")

    ax.set_yticks([0, 1])
    ax.set_yticklabels(["session id pinned\n2 runs", "fresh session id per call\n5 runs, the default"],
                       fontsize=12, color=INK)
    ax.set_ylim(-0.6, 1.6)
    ax.set_xlim(0, 7600)
    ax.set_xticks([0, 2000, 4000, 6000])
    ax.set_xticklabels(["0", "2000", "4000", "6000ms"], fontsize=11, color=INK_3)
    ax.grid(axis="x", color=RULE, lw=1)
    ax.set_axisbelow(True)

    fig.text(0.0, 0.045,
             "It presented as a fixed per-client cost until the slow cell moved between clients. A fixed cost cannot move.",
             fontsize=11, color=INK_2)
    _save(fig, "05-session-cold-start.png")


# --------------------------------------------------------------------------
# 06 -- three unmatched models
# --------------------------------------------------------------------------

def models():
    table(
        "06-three-models.png",
        "Three models, deliberately unmatched",
        "The heterogeneity is the asset, not a confound.",
        ["", "`gemini-2.5-flash`", "`nova-micro`", "`gpt-5-mini`"],
        [
            ["what it is", "fast general model", "small and cheap", "reasoning deployment"],
            ["reached through", "ADK -> Vertex", "Strands -> Bedrock", "Agent Framework -> Foundry"],
            ["why this one", "the ADK path's default", "inherited from a two-field lookup task, a poor default for prose", "forced: store=False needs encrypted reasoning content"],
        ],
        widths=[0.0, 0.17, 0.42, 0.70],
    )


# --------------------------------------------------------------------------
# 07 -- the scorer changes the answer
# --------------------------------------------------------------------------

def scorers():
    """Grouped bars rather than a dumbbell.

    The dumbbell was the right form for "before -> after per item" and the wrong
    one for this data: azure's two regret values are 0.45 apart on a 0-10 scale,
    so the two dots and both value labels landed on top of each other, and gcp's
    win rate is identical under both scorers, which a dumbbell draws as a single
    smudge. Grouped bars cannot collide.
    """
    clouds = ["azure / gpt-5-mini", "gcp / gemini-2.5-flash", "aws / nova-micro"]
    win = [(43, 87), (43, 43), (33, 0)]
    regret = [(0.97, 0.52), (1.54, 2.21), (1.32, 9.38)]

    fig = plt.figure(figsize=(WIDTH_IN, 4.2))
    fig.text(0.0, 0.965, "Which model looks best is a property of the scorer",
             fontsize=19, fontweight="bold", color=INK, va="top")
    fig.text(0.0, 0.885,
             "24 briefs, 2026-08-14. The same stored drafts, scored by a deterministic rubric and then re-ranked by a model judge.",
             fontsize=11.5, color=INK_2, va="top")

    panels = (
        (win, "win rate", 100, lambda v: f"{v:.0f}%"),
        (regret, "regret   rubric points below the panel's best", 9.38, lambda v: f"{v:.2f}"),
    )
    for k, (data, label, xmax, fmt) in enumerate(panels):
        left = LABEL_GUTTER if k == 0 else 0.60
        ax = _bare(fig.add_axes([left, 0.16, 0.36, 0.56]))
        for i, (rubric, llm) in enumerate(data):
            y = len(data) - 1 - i
            for value, color, offset in ((rubric, BLUE, 0.16), (llm, ORANGE, -0.16)):
                ax.barh(y + offset, value, height=0.29, color=color, zorder=2)
                ax.text(value + xmax * 0.02, y + offset, fmt(value), va="center",
                        fontsize=11.5, color=color, fontweight="bold")
        ax.set_yticks(range(len(data)))
        if k == 0:
            ax.set_yticklabels(list(reversed(clouds)), fontsize=12, color=INK)
        else:
            ax.set_yticklabels([])
        ax.set_xticks([])
        ax.set_xlim(0, xmax * 1.20)
        ax.set_ylim(-0.62, len(data) - 0.38)
        ax.set_title(label, fontsize=12, color=INK_2, loc="left", pad=12)

    fig.text(LABEL_GUTTER, 0.04, "rubric", fontsize=12, color=BLUE, fontweight="bold")
    fig.text(LABEL_GUTTER + 0.075, 0.04, "model judge", fontsize=12, color=ORANGE,
             fontweight="bold")
    fig.text(0.60, 0.04,
             "Under the rubric no model dominates. Under the model judge one takes 87% and one takes none.",
             fontsize=11, color=INK_2)
    _save(fig, "07-scorer-changes-the-answer.png")


# --------------------------------------------------------------------------
# 08 -- availability
# --------------------------------------------------------------------------

def availability():
    rows = [("aws / nova-micro", 100), ("azure / gpt-5-mini", 96), ("gcp / gemini-2.5-flash", 58)]

    fig, ax = _chart_axes(
        3.3,
        "The column no judge can move",
        "How often each cloud produced a draft at all, across the same 24 briefs. Identical under both scorers.",
    )
    for i, (label, value) in enumerate(rows):
        y = len(rows) - 1 - i
        color = ORANGE if value < 90 else BLUE
        ax.barh(y, value, height=0.5, color=color, zorder=2)
        ax.text(value + 1.5, y, f"{value}%", va="center", fontsize=13.5,
                fontweight="bold", color=color)
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([r[0] for r in reversed(rows)], fontsize=12, color=INK)
    ax.set_xticks([0, 25, 50, 75, 100])
    ax.set_xticklabels(["0", "25", "50", "75", "100%"], fontsize=11, color=INK_3)
    ax.set_xlim(0, 112)
    ax.set_ylim(-0.6, len(rows) - 0.4)
    ax.grid(axis="x", color=RULE, lw=1)
    ax.set_axisbelow(True)

    fig.text(0.0, 0.045,
             "The failure recorded against the 58% leg is a Vertex 429 -- quota is the documented cause, not a proven one.",
             fontsize=11, color=INK_2)
    _save(fig, "08-availability.png")


# --------------------------------------------------------------------------
# 09 -- tool use by instruction version
# --------------------------------------------------------------------------

def tool_use():
    table(
        "09-search-use.png",
        "Tool parity in availability is not tool parity in use",
        "Drafts that made zero searches, with the same tool and the same six-call budget on every cloud.",
        ["", "zero-search drafts", ""],
        [
            ["aws, instruction v1", "*7 of 7*", "the instruction said nothing about searching"],
            ["aws, v2", "2 of 9", "search first, cite only what you opened"],
            ["aws, v3", "1 of 7", "same requirement, bounded to the budget"],
            ["azure, all versions", "1 of 16", ""],
            ["gcp, v3", "*none*", "spends the whole six-call budget every run"],
        ],
        widths=[0.0, 0.26, 0.46],
    )


# --------------------------------------------------------------------------
# 10 -- the AgentCore contract, AWS-first
# --------------------------------------------------------------------------

def agentcore_contract():
    """The same contract as 04, ordered for a reader who starts on AWS.

    A separate image rather than a reuse: in the AWS article AgentCore is the
    subject and the other two runtimes are the control column, so AgentCore
    reads first. Re-ordering the columns of 04 in the reader's head is work the
    image should be doing.
    """
    table(
        "10-agentcore-contract.png",
        "The AgentCore Runtime container contract",
        "Every clause is load-bearing. The other two runtimes are the control column.",
        ["", "*AgentCore Runtime*", "Cloud Run", "Container Apps"],
        [
            ["port", "*9000*", "$PORT, 8080", "8080"],
            ["invoke path", "*/  (platform exposes /invocations/)*", "yours", "yours"],
            ["health", "*GET /ping -> Healthy*", "yours", "yours"],
            ["architecture", "*ARM64, required*", "any", "amd64"],
            ["card", "/.well-known/agent-card.json", "same", "same"],
            ["A2A-Version header", "*dropped*", "forwarded", "forwarded"],
            ["cold-start unit", "*session -> microVM*", "instance", "revision replica"],
        ],
        widths=[0.0, 0.19, 0.50, 0.76],
    )


if __name__ == "__main__":
    print("rendering:")
    stacks()
    constants()
    bad_card()
    contracts()
    session()
    models()
    scorers()
    availability()
    tool_use()
    agentcore_contract()
    print("done.")
