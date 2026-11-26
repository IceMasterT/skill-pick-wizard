"""UserPromptSubmit hook. Never fails, never blocks a prompt, prints one JSON object or nothing."""
from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path

# The scripts folder goes on the path explicitly: with PYTHONSAFEPATH set, Python does not add it.
_SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPTS))

from hookio import emit, fallback_log, read_stdin  # noqa: E402  (standard library only)

CLEANUP_INTERVAL = 86400
_PICK = _SCRIPTS / "pick.py"

try:  # an import failure must never make the hook exit non-zero
    from skillpick.hookctx import build_context
    from skillpick.paths import append_error_log, data_dir
    from skillpick.state import cleanup_sessions, load_config, load_session, valid_session_id
    _IMPORTS_OK = True
    _IMPORT_ERROR = ""
except BaseException:
    _IMPORTS_OK = False
    _IMPORT_ERROR = "".join(traceback.format_exc().splitlines(keepends=True)[-6:])


def _log_error(data: Path | None) -> None:
    try:
        target = data if data is not None else data_dir()
        summary = "".join(traceback.format_exc().splitlines(keepends=True)[-6:])
        append_error_log(target, f"{time.strftime('%Y-%m-%d %H:%M:%S')} hook: {summary}")
    except BaseException:
        pass


def _maybe_cleanup(data: Path) -> None:
    """Run cleanup_sessions at most once per day; failures never propagate."""
    try:
        marker = data / ".last-cleanup"
        try:
            age = time.time() - marker.stat().st_mtime
            if 0 <= age < CLEANUP_INTERVAL:  # a future-dated marker must not suppress cleanup
                return
        except FileNotFoundError:
            pass
        marker.touch()  # claim the slot first so a failing cleanup isn't retried every prompt
        cleanup_sessions(data)
    except BaseException:
        _log_error(data)


def main(stdin_text: str) -> str:
    if not _IMPORTS_OK:
        fallback_log(f"{time.strftime('%Y-%m-%d %H:%M:%S')} hook: import failed: {_IMPORT_ERROR}")
        return ""
    data = None
    try:
        payload = json.loads(stdin_text)
        if not isinstance(payload, dict):
            return ""
        sid, prompt = payload.get("session_id"), payload.get("prompt")
        if not isinstance(prompt, str) or not valid_session_id(sid):
            return ""
        data = data_dir()
        _maybe_cleanup(data)
        ctx = build_context(
            prompt, sid, load_config(data), load_session(data, sid), f'python3 "{_PICK}"'
        )
        if not ctx:
            return ""
        return json.dumps(
            {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": ctx}}
        )
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
