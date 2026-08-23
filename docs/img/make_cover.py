#!/usr/bin/env python3
"""Render the cover image for the framework article.

    uv pip install --system matplotlib
    python3 docs/img/make_cover.py

Tokens, palette and fonts are imported from `make_medium_graphics.py` rather
than restated, so the cover cannot drift from the nine figures it sits above.
That import is safe: that module renders nothing unless run as main.

1500x750 -- 2:1, the safe ratio for a Medium cover, and the same 1500px width
as every other image here. Light surface for the same reason as the rest:
Medium serves one image to both its themes, so a light card that stays legible
on a dark page beats transparency that is illegible in one of them.

**The three accents are categorical, not a cloud encoding.** Nothing else in
this repo colours by cloud -- in the charts, blue and orange mean rubric and
model judge -- so the cover must not be the one place that teaches a
cloud/colour mapping the reader will then look for and not find. They are three
column accents and nothing more, which is the use those slots were validated
for. No text is set in aqua, which sits below 3:1 on white; the accent carries
no meaning that its column heading does not already state.

The claim on the cover is the article's thesis and has to stay true to it: one
agent, three clouds, one protocol. The three columns name what actually
differs, which is the same list `01-three-stacks.png` carries.
"""

from pathlib import Path

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Rectangle

from make_medium_graphics import (
    AQUA, BLUE, DPI, INK, INK_2, INK_3, MONO, ORANGE, RULE, SANS, SURFACE,
)

OUT = Path(__file__).parent / "medium"

#: Column accent, cloud, framework, model, runtime. The four rows under each
#: heading are the four that differ; they match 01-three-stacks.png exactly,
#: because a cover that disagrees with the article's own table is worse than
#: no cover.
COLUMNS = [
    (BLUE,   "Google", "ADK LlmAgent",           "gemini-2.5-flash", "Cloud Run"),
    (ORANGE, "AWS",    "Strands Agent",          "nova-micro",       "Bedrock AgentCore"),
    (AQUA,   "Azure",  "Agent Framework Agent",  "gpt-5-mini",       "Container Apps"),
]


def cover():
    fig = plt.figure(figsize=(10.0, 5.0))
    fig.patch.set_facecolor(SURFACE)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    left, right = 0.062, 0.938

    # ---- masthead ----------------------------------------------------
    ax.text(left, 0.905, "M I X   A N D   M A T C H", fontsize=11.5,
            color=INK_3, family=MONO, va="center")
    ax.text(left, 0.792, "One agent, three clouds,", fontsize=37,
            fontweight="bold", color=INK, va="center")
    ax.text(left, 0.673, "one protocol", fontsize=37,
            fontweight="bold", color=INK, va="center")
    ax.plot([left, right], [0.585, 0.585], color=RULE, lw=1.1)

    # ---- three columns -----------------------------------------------
    centres = []
    span = right - left
    gap = 0.024
    w = (span - 2 * gap) / 3
    top, bot = 0.508, 0.215

    for i, (accent, cloud, framework, model, runtime) in enumerate(COLUMNS):
        x = left + i * (w + gap)

        ax.add_patch(FancyBboxPatch(
            (x, bot), w, top - bot,
            boxstyle="round,pad=0,rounding_size=0.008",
            facecolor=SURFACE, edgecolor=RULE, lw=1.1, zorder=1))
        # The accent is a bar, not a fill: a tinted card would make the three
        # columns look like three categories of result rather than three
        # instances of one thing.
        ax.add_patch(Rectangle((x, top - 0.011), w, 0.011,
                               facecolor=accent, edgecolor="none", zorder=2))

        pad = 0.022
        ax.text(x + pad, top - 0.062, cloud, fontsize=17, fontweight="bold",
                color=INK, va="center")
        ax.text(x + pad, top - 0.125, framework, fontsize=12.5, color=INK,
                va="center", family=MONO)
        ax.text(x + pad, top - 0.183, model, fontsize=12.5, color=INK_2,
                va="center", family=MONO)
        ax.text(x + pad, top - 0.253, runtime, fontsize=12.5, color=INK_2,
                va="center")

        centres.append(x + w / 2)

    # The protocol as a bus the three columns sit on. The first version drew a
    # stub from each column down to it, which only a render showed to be wrong:
    # stub + stub + bus + the card edges above closed into two rectangles, and
    # the whole thing read as a broken table rather than as three things
    # touching one line. Dots make the same point and cannot enclose anything.
    ax.plot([left, right], [0.132, 0.132], color=INK_3, lw=1.4, zorder=0)
    for centre, (accent, *_rest) in zip(centres, COLUMNS):
        ax.plot([centre], [0.132], marker="o", markersize=8, color=accent,
                markeredgecolor=SURFACE, markeredgewidth=2.2, zorder=3)

    ax.text(left, 0.052, "A2A v1.0", fontsize=13.5, color=INK,
            va="center", family=MONO, fontweight="bold")
    ax.text(right, 0.052,
            "same brief  ·  same instruction  ·  same search tool  ·  same rubric",
            fontsize=12.5, color=INK_2, ha="right", va="center")

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "00-cover-framework.png"
    fig.savefig(path, dpi=DPI, facecolor=SURFACE)
    plt.close(fig)
    return path


if __name__ == "__main__":
    p = cover()
    print(f"  {p}")
