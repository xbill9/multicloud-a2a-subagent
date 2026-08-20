#!/usr/bin/env python3
"""Render a Medium article as HTML -- to read privately, or to feed to Medium.

Two modes, because they want opposite things from the images.

**Default: a self-contained proof.** Every PNG inlined as a data URI, written
outside the repo. Read it anywhere, share the link, check the layout before
pasting anything. A generated HTML copy committed beside its markdown is a
second document that drifts -- this repo has already paid for that once with
the runbook -- so the generator is version-controlled and its output is not.

**`--web`: a page Medium can import.** Images stay as relative `<img src>`
paths, and the file lands in `docs/` so GitHub Pages serves it alongside
`docs/img/medium/`. Medium's "Import a story" takes a public URL and pulls the
text *and* the images, which is the only way to avoid placing every image by
hand. Data URIs are no good for that -- importers fetch `src` URLs and skip
inline data -- which is exactly why this mode exists.

    python3 docs/make_preview.py                 # proof of all three, to the scratchpad
    python3 docs/make_preview.py gde             # just one
    python3 docs/make_preview.py --web           # importable pages into docs/
"""

import base64
import re
import sys
from pathlib import Path

import markdown

DOCS = Path(__file__).parent
SLUGS = ("framework", "gde", "aws")
SCRATCH = Path("/tmp/claude-1000/-home-xbill-multicloud-a2a-subagent") \
    / "6ddb71c6-f02a-46a7-ae5e-9981bdd7eead/scratchpad"

CSS = """
:root {
  color-scheme: light;
  --paper:      #fbfbf9;
  --card:       #ffffff;
  --ink:        #14140f;
  --ink-2:      #57564e;
  --ink-3:      #8b8a80;
  --rule:       #e2e1da;
  --rule-soft:  #eeede7;
  --accent:     #2a78d6;
  --accent-2:   #eb6834;
  --code-bg:    #f4f4f0;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --paper:     #191917;
    --card:      #ffffff;
    --ink:       #f3f2ea;
    --ink-2:     #b0aea3;
    --ink-3:     #85847a;
    --rule:      #34342f;
    --rule-soft: #262622;
    --accent:    #6ba6ee;
    --accent-2:  #f0885c;
    --code-bg:   #211f1d;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --paper:     #191917;
  --card:      #ffffff;
  --ink:       #f3f2ea;
  --ink-2:     #b0aea3;
  --ink-3:     #85847a;
  --rule:      #34342f;
  --rule-soft: #262622;
  --accent:    #6ba6ee;
  --accent-2:  #f0885c;
  --code-bg:   #211f1d;
}

* { box-sizing: border-box; }

body {
  margin: 0;
  background: var(--paper);
  color: var(--ink);
  font-family: "Source Serif 4", Charter, Georgia, serif;
  font-size: 19px;
  line-height: 1.65;
  -webkit-font-smoothing: antialiased;
}

.sheet { max-width: 1120px; margin: 0 auto; padding: 0 24px 96px; }

/* ---- masthead: a proof slug, not a hero ---- */
.slug {
  display: flex; flex-wrap: wrap; gap: 10px 22px; align-items: baseline;
  padding: 22px 0 20px; border-bottom: 1px solid var(--rule);
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 11.5px; letter-spacing: .09em; text-transform: uppercase;
  color: var(--ink-3);
}
.slug b { color: var(--accent-2); font-weight: 600; }
.masthead { max-width: 40rem; margin: 0 auto; padding: 60px 0 10px; }
.masthead h1 {
  font-family: "IBM Plex Sans", system-ui, sans-serif;
  font-weight: 600; font-size: 2.6rem; line-height: 1.12;
  letter-spacing: -0.02em; margin: 0 0 22px; text-wrap: balance;
}
.standfirst {
  margin: 0; color: var(--ink-2); font-size: 1.22rem; line-height: 1.5;
  font-style: italic;
}

/* ---- prose ---- */
article { max-width: 40rem; margin: 0 auto; }
article > * { margin-inline: auto; }
article h1, article h2, article h3 {
  font-family: "IBM Plex Sans", system-ui, sans-serif;
  font-weight: 600; letter-spacing: -0.015em; text-wrap: balance;
}
article h2 { font-size: 1.72rem; line-height: 1.2; margin: 3.4rem 0 1.1rem; }
article h3 { font-size: 1.18rem; margin: 2.6rem 0 .8rem; }
article p { margin: 0 0 1.35rem; }
article ul { margin: 0 0 1.35rem; padding-left: 1.2rem; }
article li { margin-bottom: .6rem; }
article strong { font-weight: 600; }
article a { color: var(--accent); text-decoration-thickness: 1px; text-underline-offset: 2px; }
article hr { border: 0; border-top: 1px solid var(--rule); margin: 3rem auto; }

blockquote {
  margin: 0 0 1.35rem; padding: .2rem 0 .2rem 1.3rem;
  border-left: 3px solid var(--accent); color: var(--ink-2);
}
blockquote p:last-child { margin-bottom: 0; }

code {
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: .82em; background: var(--code-bg); color: var(--ink);
  padding: .12em .35em; border-radius: 3px;
}
pre {
  background: var(--code-bg); border: 1px solid var(--rule-soft);
  border-radius: 6px; padding: 16px 18px; overflow-x: auto;
  margin: 0 0 1.6rem; line-height: 1.5;
}
pre code { background: none; padding: 0; font-size: 13px; }

/* ---- figures: the nine images, in upload order ---- */
figure {
  margin: 2.6rem auto; width: min(880px, 100%);
}
figure img {
  display: block; width: 100%; height: auto;
  background: var(--card);
  border: 1px solid var(--rule);
  border-radius: 4px;
}
figcaption {
  display: flex; gap: 14px; align-items: flex-start;
  margin-top: 12px; padding-top: 10px; border-top: 1px solid var(--rule-soft);
  font-family: "IBM Plex Sans", system-ui, sans-serif;
  font-size: 12.5px; line-height: 1.5; color: var(--ink-3);
}
.stamp {
  flex: none;
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 11px; letter-spacing: .06em; text-transform: uppercase;
  color: var(--accent-2); padding-top: 1px; white-space: nowrap;
}
.stamp em { font-style: normal; color: var(--ink-3); }
.alt { margin: 0; }
.alt b {
  display: block; font-weight: 600; color: var(--ink-2); margin-bottom: 2px;
  font-variant-numeric: tabular-nums;
}

@media (max-width: 700px) {
  body { font-size: 17.5px; }
  .masthead h1 { font-size: 2rem; }
  figcaption { flex-direction: column; gap: 6px; }
}
"""


def build(slug: str, out_path: Path, *, web: bool) -> Path:
    text = (DOCS / f"article-medium-{slug}.md").read_text()

    title = re.search(r"^# (.+)$", text, re.M).group(1)
    standfirst = re.search(r"^### (.+)$", text, re.M).group(1)
    text = re.sub(r"^# .+$", "", text, count=1, flags=re.M)
    text = re.sub(r"^### .+$", "", text, count=1, flags=re.M)

    html = markdown.markdown(text, extensions=["fenced_code", "attr_list", "sane_lists"])
    count = html.count("<img")

    def figure(match, _n=[0]):
        _n[0] += 1
        alt, src = match.group("alt"), match.group("src")
        if web:
            # A path Medium's fetcher can resolve against the page URL. Pages
            # serves docs/ at the site root, so this is unchanged from source.
            source = src
        else:
            data = base64.b64encode((DOCS / src).read_bytes()).decode()
            source = f"data:image/png;base64,{data}"
        return (
            f'<figure><img src="{source}" alt="{alt}">'
            f'<figcaption><span class="stamp">{_n[0]:02d}<em>/{count:02d}</em></span>'
            f'<p class="alt"><b>{Path(src).name}</b>{alt}</p></figcaption></figure>'
        )

    html = re.sub(r'<img alt="(?P<alt>[^"]*)" src="(?P<src>[^"]*)"\s*/?>', figure, html)
    html = html.replace("<p><figure>", "<figure>").replace("</figure></p>", "</figure>")

    slug_line = (
        f'<span>Medium proof</span><span>docs/article-medium-{slug}.md</span>'
        f'<span><b>{count} images</b> — tables rendered, captions ready to paste</span>'
    )
    page = f"""<title>Three Clouds, One Brief</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans:wght@400;600&family=Source+Serif+4:ital,opsz,wght@0,8..60,400;0,8..60,600;1,8..60,400&display=swap">
<style>{CSS}</style>
<div class="sheet">
  <div class="slug">{slug_line}</div>
  <header class="masthead">
    <h1>{title}</h1>
    <p class="standfirst">{standfirst}</p>
  </header>
  <article>{html}</article>
</div>
"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(page)
    return out_path


if __name__ == "__main__":
    args = sys.argv[1:]
    web = "--web" in args
    slugs = [a for a in args if not a.startswith("-")] or list(SLUGS)
    for slug in slugs:
        out = (DOCS / f"medium-{slug}.html") if web else (SCRATCH / f"medium-proof-{slug}.html")
        written = build(slug, out, web=web)
        print(f"  {written}  ({written.stat().st_size / 1024:.0f} KB)")
