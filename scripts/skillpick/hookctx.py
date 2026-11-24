"""Pure text builder for the UserPromptSubmit hook (no I/O, no index access)."""
from __future__ import annotations

import json
import re

from .state import Config, SessionState

PLUGIN_COMMANDS = ("autopick", "skill-pick", "skill-mode", "skill-deep-scan")

_CMD = "(?:skill-pick-wizard:)?(?:" + "|".join(re.escape(c) for c in PLUGIN_COMMANDS) + ")"
_COMMAND_RE = re.compile(r"/" + _CMD + r"(?=\s|$)", re.IGNORECASE)
# Claude Code rewrites a typed slash command into <command-name>/cmd</command-name> before hooks run.
_EXPANDED_RE = re.compile(r"<command-name>\s*/" + _CMD + r"\s*</command-name>", re.IGNORECASE)
_MAX_FIELD = 200


def _one_line(text: str) -> str:
    """Collapse whitespace so stored text can't add lines to the injected block."""
    flat = " ".join(str(text).split())
    return flat if len(flat) <= _MAX_FIELD else flat[: _MAX_FIELD - 3] + "..."


def _is_plugin_command(prompt: str) -> bool:
    return bool(_COMMAND_RE.match(prompt.strip()) or _EXPANDED_RE.search(prompt))


def build_context(
    prompt: str, sid: str, cfg: Config, session: SessionState | None, pick_cmd: str
) -> str | None:
    if _is_plugin_command(prompt):
        return None
    if session is not None:
        if session.status != "planned" or not session.stages:
            return None
        lines = ["Skill Pick Wizard - active skill plan for this job:"]
        for st in session.stages:
            lines.append(f"{_one_line(st.stage)}: {', '.join(_one_line(s) for s in st.skills)}")
        job = json.dumps(_one_line(session.job), ensure_ascii=False)
        lines.append(
            "Invoke each skill with the Skill tool when the work reaches its stage. "
            f"Use this plan only while the work matches the job {job}. "
            "If the user starts clearly unrelated work, say in one line that /skill-pick "
            "will pick skills for the new job, then continue."
        )
        return "\n".join(lines)
    model = _one_line(cfg.deep_scan_model)
    deep = f"on (model {model})" if cfg.deep_scan else "off"
    return "\n".join(
        [
            "Skill Pick Wizard - skill scan pending.",
            f"session: {sid}",
            f"mode: {cfg.mode}",
            f"deep scan: {deep}",
            f"run with: {pick_cmd}",
            "Invoke the skill-pick-wizard:skill-pick skill now, before any other tool call or "
            "answer. Skip it only if this message is just a greeting, thanks, or a question you "
            "can fully answer in one or two sentences without doing any work.",
        ]
    )
