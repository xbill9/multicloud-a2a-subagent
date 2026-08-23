#!/usr/bin/env python3
"""Put every multi-line code block into a gist, so Medium can render it.

**Why this exists.** Medium's importer flattens code. Measured 2026-08-23:
`medium-aws.html` imported cleanly -- prose, headings, links and all four
images, which Medium rehosted itself -- and every one of the fourteen fenced
blocks arrived as a single horizontally-scrolling line. A nine-line Dockerfile
became `ENV HOST=0.0.0.0 \\ PORT=9000 \\ ... EXPOSE 9000 CMD [...]`. Emitting
`<br>` instead of newlines does not help; the importer strips those too,
measured the same day on a second import.

Gists are the way out, because Medium embeds them natively rather than parsing
them. So the `--web` pages swap each multi-line block for its gist embed, and
the import arrives complete.

**Single-line blocks are left alone.** There is nothing to flatten in a
one-line `curl`, and a gist for it would be worse than the code. Of the 34
blocks across the three articles, 20 are multi-line and get gists.

**This is idempotent.** `gists.json` maps a stable key -- article slug plus the
block's position -- to the gist it lives in, and records a hash of the content.
Re-running touches nothing unless a block's text changed, in which case it
edits that gist in place so the URL already pasted into Medium stays valid.
A block that disappears leaves an orphan; this prints it rather than deleting
it, because deleting someone's gist is not a generator's decision.

    python3 docs/make_gists.py --dry-run    # what would change
    python3 docs/make_gists.py              # create/update, write the manifest
"""

import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

DOCS = Path(__file__).parent
MANIFEST = DOCS / "gists.json"
SLUGS = ("framework", "gde", "aws")

#: Fence language -> the extension that makes the gist highlight correctly.
#: `console` is deliberately `.txt` rather than `.sh`: it is captured output,
#: not a script, and GitHub highlighting a transcript as shell is worse than
#: not highlighting it.
EXT = {
    "python": "py", "json": "json", "dockerfile": "dockerfile",
    "shell": "sh", "bash": "sh",
    "console": "txt", "plaintext": "txt", "text": "txt", "": "txt",
}

BLOCK_RE = re.compile(r"^```(\w*)\n(.*?)^```", re.S | re.M)
HEADING_RE = re.compile(r"^#{1,6} (.+)$", re.M)


def needs_image(code: str) -> bool:
    """Whether a fenced block has to become an image to survive an import.

    Multi-line, because the importer flattens `<pre>` to one line. Or
    comment-like, because its sanitiser strips `<!-- ... -->` even when the page
    serves it correctly escaped. Everything else -- a one-line `curl` -- imports
    intact as a Medium code block and is better left as text.

    Shared with make_preview.py so the page and the gists cannot disagree about
    which blocks are which.
    """
    return code.strip().count("\n") >= 1 or "<!--" in code


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "block"


def blocks(slug: str) -> list[dict]:
    """Every multi-line fenced block in one article, in document order.

    The heading above a block becomes part of the gist's filename, because the
    filename is the one piece of the gist Medium's embed puts on screen. A
    reader who meets `aws-04-building-the-image.dockerfile` in the middle of the
    piece knows what they are looking at; `gistfile1.txt` tells them nothing.
    """
    text = (DOCS / f"article-medium-{slug}.md").read_text()
    out, n, c = [], 0, 0
    for m in BLOCK_RE.finditer(text):
        lang, code = m.group(1), m.group(2)
        multiline = code.strip().count("\n") >= 1
        if not needs_image(code):
            continue
        # Comment blocks are numbered in their own `h` series so that adding one
        # cannot renumber the multi-line blocks -- a positional key that shifts
        # would repoint every gist after it.
        if multiline:
            n += 1
            key = f"{slug}-{n:02d}"
        else:
            c += 1
            key = f"{slug}-h{c:02d}"
        heads = HEADING_RE.findall(text[: m.start()])
        hint = slugify(heads[-1]) if heads else "block"
        out.append({
            "key": key,
            "lang": lang,
            "code": code,
            "filename": f"{key}-{hint}.{EXT.get(lang, 'txt')}",
            "description": f"{slug}: {heads[-1] if heads else slug}",
            "sha": hashlib.sha256(f"{lang}\0{code}".encode()).hexdigest()[:16],
        })
    return out


def gh(*args: str) -> str:
    r = subprocess.run(["gh", *args], capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"gh {' '.join(args)} failed:\n{r.stderr.strip()}")
    return r.stdout.strip()


def sync(dry: bool = False) -> dict:
    manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
    seen = set()

    for slug in SLUGS:
        for b in blocks(slug):
            seen.add(b["key"])
            have = manifest.get(b["key"])
            if have and have["sha"] == b["sha"]:
                continue
            action = "update" if have else "create"
            print(f"  {action:6} {b['key']}  {b['filename']}")
            if dry:
                continue
            with tempfile.TemporaryDirectory() as d:
                f = Path(d) / b["filename"]
                f.write_text(b["code"])
                if have:
                    gh("gist", "edit", have["id"], "-f", b["filename"], str(f))
                    url = have["url"]
                else:
                    # Public, because Medium cannot embed a secret gist.
                    url = gh("gist", "create", "--public",
                             "-d", b["description"], str(f))
            manifest[b["key"]] = {
                "id": url.rstrip("/").split("/")[-1],
                "url": url,
                "filename": b["filename"],
                "sha": b["sha"],
                "description": b["description"],
            }

    for orphan in sorted(set(manifest) - seen):
        print(f"  ORPHAN {orphan}  {manifest[orphan]['url']}"
              f"  (block gone; delete the gist by hand if you want it gone)")

    if not dry:
        MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def load() -> dict:
    """The manifest, for make_preview.py. Empty if gists have not been made."""
    return json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    m = sync(dry=dry)
    print(f"\n{len(m)} gists in {MANIFEST.relative_to(DOCS.parent)}"
          f"{' (dry run, nothing written)' if dry else ''}")
