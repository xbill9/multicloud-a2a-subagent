"""Transport-independent A2A wire helpers shared by every client stack.

Named ``protocol`` rather than ``a2a`` so it cannot shadow the installed
``a2a-sdk`` package, which one of the three client stacks imports.
"""

from protocol.research import build_prompt, parse_brief, parse_draft, render_draft

__all__ = ["build_prompt", "parse_brief", "parse_draft", "render_draft"]
