# Getting the article into Medium

## Why this file exists

There are two versions of the same article and exactly one reason for that:
**Medium does not render markdown tables at all.** Pasting one produces a wall
of pipe characters. So the Medium version carries the same prose with every
table rendered as an image instead.

| file | venue | tables |
|---|---|---|
| `article-devto-framework.md` | dev.to | markdown tables, rendered natively |
| `article-medium-framework.md` | Medium | nine images, generated from the same numbers |

The prose is the same argument, word for word, with two deliberate exceptions
noted at the bottom.

> The older pair, `article-medium.md` and `article-cross-cloud-auth.md`,
> describe the **predecessor** currency mesh. They are accurate about what was
> deployed then and stale about what this repo now is. The steps below apply to
> them too; the image list does not.

## The steps

1. Open a new Medium story and paste the body of `article-medium-framework.md`,
   starting at the H1. Medium keeps `#`/`##`, `>`, backtick fences, bold and
   italics from pasted markdown. It drops image references, because it cannot
   resolve a relative path.

2. Upload the nine images by hand, in order, at the point each `![...]` line
   sits. Delete the `![...]` line once its image is in place.

   | # | file | section |
   |---|---|---|
   | 1 | `img/medium/01-three-stacks.png` | opening |
   | 2 | `img/medium/02-held-constant.png` | What actually has to be the same |
   | 3 | `img/medium/03-bad-card.png` | The agent card advertises an address you cannot dial |
   | 4 | `img/medium/04-platform-contracts.png` | The platform edits your request |
   | 5 | `img/medium/05-session-cold-start.png` | The platform edits your request |
   | 6 | `img/medium/06-three-models.png` | The models differ mostly where a rubric cannot see |
   | 7 | `img/medium/07-scorer-changes-the-answer.png` | same section |
   | 8 | `img/medium/08-availability.png` | same section |
   | 9 | `img/medium/09-search-use.png` | Tool parity in availability is not tool parity in use |

3. **Paste each image's alt text into Medium's caption field.** The alt text in
   the article states every number in words. Every table in this piece is an
   image, so without captions a screen reader — and Medium's own search index —
   gets nothing from a third of the article. The alt-text field is behind the
   image's settings control; the caption is the visible line under it. Use both.

4. Set the images to full width. They render 1500px wide, which is enough for
   Medium's largest layout on a retina screen.

5. The `###` line under the H1 becomes Medium's subtitle if pasted as the second
   block. Check that it did — Medium sometimes takes the first paragraph instead.

6. Leave the console blocks as text. Medium renders fenced code fine, and the
   two in this piece (the interop matrix and the version-mismatch error) are
   narrow enough not to wrap.

## Regenerating the graphics

```bash
uv pip install --system matplotlib
python3 docs/img/make_medium_graphics.py
```

Every number in those images is hard-coded in that script, sourced from
`README.md`, `docs/RUNBOOK.md` and `docs/INTEROP.md`. **If a measurement changes
there, change it in the script too** — an image is the one place in this repo
where a stale number cannot be caught by grep.

## What was checked

- **Palette**: categorical slots 1–3 of the dataviz reference palette — blue
  `#2a78d6`, orange `#eb6834`, aqua `#1baf7a` — validated on a `#ffffff` surface
  with `--pairs all`: lightness band, chroma floor, CVD separation (worst pair
  ΔE 9.2 deutan), normal-vision floor (worst ΔE 24.0), contrast. Aqua sits below
  3:1 on white, so every chart direct-labels its values, which is the documented
  relief for that WARN.
- **Light surface, deliberately.** Medium serves one image to both its themes.
  Text drawn on transparency is illegible in whichever theme it was not drawn
  for, so these are light cards that stay readable on a dark page.
- **Every image was rendered and looked at**, which is not the same as assuming
  the code was right. Four defects only a render could show: column headers kept
  their backticks; an inline `code` span inside a longer cell kept its backticks
  too; the per-character width used for wrapping was too small, so cells overran
  into the next column; and the first `07` was a dumbbell whose two dots landed
  on top of each other wherever the two scorers agreed. The dumbbell is now
  grouped bars, which cannot collide.

## The two places the prose differs from the dev.to version

Both are consequences of the format, not edits to the argument:

- **The troubleshooting reference is a list, not an image.** It is eleven rows
  of three prose columns. As a 1500px image the text lands around 11px on a
  phone, which is where a reference table stops being usable. Bold symptom, then
  cause and fix in prose, is legible at any width.
- **One sentence reads "those two" instead of "that"**, because the single
  results table became two images.
