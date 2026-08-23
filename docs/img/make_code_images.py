#!/usr/bin/env python3
"""Render every gist'd code block as a PNG, because Medium imports images.

    uv pip install --system matplotlib
    python3 docs/img/make_code_images.py

**Why, measured rather than assumed.** Medium's importer cannot be made to
embed a gist. One page carrying the same gist in five markups was imported
2026-08-23 and inspected: a bare URL in a paragraph, an anchor, a figure
wrapping an anchor and a figure carrying `data-oembed-url` all arrived as plain
links; an `<iframe>` to the gist `.pibb` endpoint was dropped entirely. Zero
iframes on the imported page. The importer creates no embeds from any markup.

Images are the one thing that survives an import perfectly -- Medium fetches
them, rehosts them at 800px and takes the `<figcaption>` as the caption -- which
is already why every table in these articles is a PNG. Code now goes the same
way, for the same reason.

**The gist is still the point.** Each image is captioned with a link to the gist
it was rendered from, so the code stays copyable and stays one click away. The
image guarantees it renders; the gist guarantees it is usable. Neither alone
does both.

Same tokens as the tables, imported rather than restated. 1500px wide, light
surface, 72-column budget already enforced upstream by the articles.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))   # docs/, for make_gists

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt

import make_gists
from make_medium_graphics import DPI, INK, INK_2, INK_3, MONO, RULE, SURFACE

OUT = Path(__file__).parent / "medium" / "code"

PANEL = "#f7f7f4"        # the code surface, a shade off the page
FONT_PT = 13.5
LINE_PT = FONT_PT * 1.62
PAD_PT = 26.0
HEAD_PT = 30.0           # room for the filename strip
WIDTH_IN = 10.0
PT_PER_IN = 72.0


def render(block: dict, out: Path) -> Path:
    lines = block["code"].rstrip("\n").split("\n")
    body_pt = len(lines) * LINE_PT
    height_in = (HEAD_PT + body_pt + 2 * PAD_PT) / PT_PER_IN

    fig = plt.figure(figsize=(WIDTH_IN, height_in))
    fig.patch.set_facecolor(SURFACE)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, WIDTH_IN * PT_PER_IN)
    ax.set_ylim(0, height_in * PT_PER_IN)
    ax.axis("off")

    total_w = WIDTH_IN * PT_PER_IN
    total_h = height_in * PT_PER_IN
    ax.add_patch(plt.Rectangle((1, 1), total_w - 2, total_h - 2,
                               facecolor=PANEL, edgecolor=RULE, lw=1.2))

    # The filename inside the image as well as in the caption. A caption can be
    # lost in a paste or a crop; the image should still say what it is.
    ax.text(PAD_PT, total_h - PAD_PT, block["filename"], fontsize=10.5,
            color=INK_3, family=MONO, va="top")

    y = total_h - PAD_PT - HEAD_PT
    for line in lines:
        ax.text(PAD_PT, y, line.rstrip(), fontsize=FONT_PT,
                color=INK if line.strip() else INK_2, family=MONO, va="top")
        y -= LINE_PT

    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=DPI, facecolor=SURFACE)
    plt.close(fig)
    return out


def main():
    manifest = make_gists.load()
    if not manifest:
        raise SystemExit("no docs/gists.json -- run make_gists.py first")
    n = 0
    for slug in make_gists.SLUGS:
        for block in make_gists.blocks(slug):
            path = render(block, OUT / f"{block['key']}.png")
            n += 1
            print(f"  {path.relative_to(Path(__file__).parent.parent.parent)}"
                  f"  ({len(block['code'].rstrip().splitlines())} lines)")
    print(f"\n{n} code images")


if __name__ == "__main__":
    main()
