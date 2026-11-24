"""UserPromptSubmit hook. Never fails, never blocks a prompt, prints one JSON object or nothing."""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path

MAX_STDIN_BYTES = 8 * 1024 * 1024
CLEANUP_INTERVAL = 86400
_PICK = Path(__file__).resolve().parent / "pick.py"

try:  # an import failure must never make the hook exit non-zero
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from skillpick.hookctx import build_context
    from skillpick.paths import append_error_log, data_dir
    from skillpick.state import cleanup_sessions, load_config, load_session, valid_session_id
    _IMPORTS_OK = True
    _IMPORT_ERROR = ""
except BaseException:
    _IMPORTS_OK = False
    _IMPORT_ERROR = "".join(traceback.format_exc().splitlines(keepends=True)[-6:])

# Standard-library-only copies of paths.data_dir() and paths.append_error_log(), used only when the
# skillpick package itself cannot be imported (tests keep them in step with paths.py).
_LOG_MAX_BYTES = 1024 * 1024


def _fallback_data_dir() -> Path:
    override = os.environ.get("SKILL_PICK_WIZARD_HOME")
    return Path(override) if override else Path.home() / ".local" / "share" / "skill-pick-wizard"


def _fallback_log(text: str) -> None:
    """Append text to errors.log without the skillpick package; never raises."""
    try:
        import fcntl

        data = _fallback_data_dir()
        data.mkdir(parents=True, exist_ok=True, mode=0o700)
        log = data / "errors.log"
        with open(data / "errors.log.lock", "a") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                if log.stat().st_size > _LOG_MAX_BYTES:
                    os.replace(log, log.with_name("errors.log.1"))
            except FileNotFoundError:
                pass
            with open(log, "a", encoding="utf-8", errors="backslashreplace") as fh:
                fh.write(text)
    except BaseException:
        pass


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
        _fallback_log(f"{time.strftime('%Y-%m-%d %H:%M:%S')} hook: import failed: {_IMPORT_ERROR}")
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


def _emit(out: str) -> None:
    """Write out; if stdout is closed or broken, silence it so interpreter shutdown can't fail (exit 120)."""
    try:
        sys.stdout.write(out)
        sys.stdout.flush()
    except BaseException:
        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, 1)
        except BaseException:
            pass


if __name__ == "__main__":
    try:
        raw = sys.stdin.buffer.read(MAX_STDIN_BYTES)
        out = main(raw.decode("utf-8", errors="replace"))
        if out:
            _emit(out)
    except BaseException:
        pass
    sys.exit(0)
