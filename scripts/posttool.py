"""PostToolUse hook for Bash: after a successful `pick.py save-plan`, show the saved skill list to the user.

The list goes out as a `systemMessage`, which Claude Code shows to the user; Claude itself does not have
to write it (live runs showed a brevity output style can stop it from doing so). Silent mode shows
nothing. Like hook.py it never fails: no non-zero exit, nothing on stdout unless it emits, errors logged.
hooks.json runs it only for Bash calls matching `python3 *pick.py* save-plan *`, but when Claude Code
cannot split a command it runs the hook anyway, so every condition is checked again here.
"""
from __future__ import annotations

import json
import shlex
import sys
import time
import traceback
from pathlib import Path

# The scripts folder goes on the path explicitly: with PYTHONSAFEPATH set, Python does not add it.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from hookio import emit, fallback_log, read_stdin  # noqa: E402  (standard library only)

try:  # an import failure must never make the hook exit non-zero
    from skillpick.paths import append_error_log, data_dir
    from skillpick.planview import plan_list
    from skillpick.state import load_config, load_session, valid_session_id
    _IMPORTS_OK = True
    _IMPORT_ERROR = ""
except BaseException:
    _IMPORTS_OK = False
    _IMPORT_ERROR = "".join(traceback.format_exc().splitlines(keepends=True)[-6:])

_SHELL_SEPARATORS = {";", "&&", "||", "|", "&"}


def _log_error(data: Path | None) -> None:
    try:
        target = data if data is not None else data_dir()
        summary = "".join(traceback.format_exc().splitlines(keepends=True)[-6:])
        append_error_log(target, f"{time.strftime('%Y-%m-%d %H:%M:%S')} posttool: {summary}")
    except BaseException:
        pass


def save_plan_args(command: str) -> list[str] | None:
    """The arguments after `<...>scripts/pick.py save-plan` in command, up to a shell separator, or None.

    None when the command does not run pick.py's save-plan (for example when `save-plan` is only an
    argument of another program) or cannot be split like a shell would.
    """
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    for i in range(len(tokens) - 1):
        if tokens[i].endswith("scripts/pick.py") and tokens[i + 1] == "save-plan":
            args = []
            for token in tokens[i + 2:]:
                if token in _SHELL_SEPARATORS:
                    break
                args.append(token)
            return args
    return None


def _session_arg(args: list[str]) -> str | None:
    """The value of save-plan's --session option (either `--session X` or `--session=X`), if given."""
    for i, arg in enumerate(args):
        if arg == "--session":
            return args[i + 1] if i + 1 < len(args) else None
        if arg.startswith("--session="):
            return arg[len("--session="):]
    return None


def main(stdin_text: str) -> str:
    if not _IMPORTS_OK:
        fallback_log(f"{time.strftime('%Y-%m-%d %H:%M:%S')} posttool: import failed: {_IMPORT_ERROR}")
        return ""
    data = None
    try:
        payload = json.loads(stdin_text)
        if not isinstance(payload, dict) or payload.get("tool_name") != "Bash":
            return ""
        tool_input, response = payload.get("tool_input"), payload.get("tool_response")
        if not isinstance(tool_input, dict) or not isinstance(response, dict):
            return ""
        command, stdout = tool_input.get("command"), response.get("stdout")
        if not isinstance(command, str) or not isinstance(stdout, str):
            return ""
        if not stdout.startswith("saved plan:"):
            return ""
        args = save_plan_args(command)
        if args is None:
            return ""
        sid = _session_arg(args)
        if sid is None:
            sid = payload.get("session_id")
        if not valid_session_id(sid):
            return ""
        data = data_dir()
        state = load_session(data, sid)
        if state is None or state.status != "planned" or not state.stages:
            return ""
        if (state.mode or load_config(data).mode) == "silent":
            return ""
        return json.dumps({"systemMessage": "\n".join(plan_list(state.job, state.stages, state.deep_scan))})
    except BaseException:
        _log_error(data)
        return ""


if __name__ == "__main__":
    try:
        out = main(read_stdin())
        if out:
            emit(out)
    except BaseException:
        pass
    sys.exit(0)
