"""The skill list shown to the user, shared by the CLI and the PostToolUse hook (stdlib only, no index)."""
from __future__ import annotations

from .state import Stage


def one_line(text: str) -> str:
    """Collapse every run of whitespace (newlines included) so a value can never add a line."""
    return " ".join(text.split())


def plan_list(job: str, stages: list[Stage], deep_scan: bool) -> list[str]:
    """The list in the SKILL.md step 5 format: one skill per line under its stage, every value on one line."""
    header = f'Skills for: "{one_line(job)}"' + (" (deep scan)" if deep_scan else "")
    lines = [header, ""]
    for st in stages:
        lines.append(one_line(st.stage))
        for invoke in st.skills:
            name, why = one_line(invoke), one_line(st.why.get(invoke, ""))
            lines.append(f"  - {name}: {why}" if why else f"  - {name}")
    return lines
