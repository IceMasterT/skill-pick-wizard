"""Pure text builder for the UserPromptSubmit hook (no I/O, no index access)."""
import re

from .state import Config, SessionState

PLUGIN_COMMANDS = ("autopick", "skill-pick", "skill-mode", "skill-deep-scan")

_COMMAND_RE = re.compile(
    r"/(?:skill-pick-wizard:)?(?:" + "|".join(re.escape(c) for c in PLUGIN_COMMANDS) + r")(?=\s|$)"
)
_MAX_FIELD = 200


def _one_line(text: str) -> str:
    """Collapse whitespace so stored text can't add lines to the injected block."""
    flat = " ".join(str(text).split())
    return flat if len(flat) <= _MAX_FIELD else flat[: _MAX_FIELD - 3] + "..."


def build_context(
    prompt: str, sid: str, cfg: Config, session: SessionState | None, pick_cmd: str
) -> str | None:
    if _COMMAND_RE.match(prompt.strip()):
        return None
    if session is not None:
        if session.status != "planned":
            return None
        lines = ["Skill Pick Wizard - active skill plan for this job:"]
        for st in session.stages:
            lines.append(f"{_one_line(st.stage)}: {', '.join(_one_line(s) for s in st.skills)}")
        lines.append(
            "Invoke each skill with the Skill tool when the work reaches its stage. "
            f'Use this plan only while the work matches the job "{_one_line(session.job)}". '
            "If the user starts clearly unrelated work, say in one line that /skill-pick "
            "will pick skills for the new job, then continue."
        )
        return "\n".join(lines)
    deep = f"on (model {cfg.deep_scan_model})" if cfg.deep_scan else "off"
    return "\n".join(
        [
            "Skill Pick Wizard - skill scan pending.",
            f"session: {sid}",
            f"mode: {cfg.mode}",
            f"deep scan: {deep}",
            f"run with: {pick_cmd}",
            "If this message asks for real work, invoke the skill-pick-wizard:skill-pick skill "
            "before doing anything else. If it is only a greeting or a quick question, "
            "answer normally and do not run the scan.",
        ]
    )
