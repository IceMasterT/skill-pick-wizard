"""UserPromptSubmit hook. Never fails, never blocks a prompt, prints one JSON object or nothing."""
import json
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from skillpick.hookctx import build_context  # noqa: E402
from skillpick.paths import data_dir  # noqa: E402
from skillpick.state import (  # noqa: E402
    cleanup_sessions,
    load_config,
    load_session,
    valid_session_id,
)

MAX_STDIN_BYTES = 8 * 1024 * 1024
CLEANUP_INTERVAL = 86400
_PICK = Path(__file__).resolve().parent / "pick.py"


def _log_error(data: Path | None) -> None:
    try:
        target = data if data is not None else data_dir()
        summary = "".join(traceback.format_exc().splitlines(keepends=True)[-6:])
        with open(Path(target) / "errors.log", "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} hook: {summary}")
    except BaseException:
        pass


def _maybe_cleanup(data: Path) -> None:
    """Run cleanup_sessions at most once per day; failures never propagate."""
    try:
        marker = data / ".last-cleanup"
        try:
            if time.time() - marker.stat().st_mtime < CLEANUP_INTERVAL:
                return
        except FileNotFoundError:
            pass
        marker.touch()  # claim the slot first so a failing cleanup isn't retried every prompt
        cleanup_sessions(data)
    except BaseException:
        _log_error(data)


def main(stdin_text: str) -> str:
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
        raw = sys.stdin.buffer.read(MAX_STDIN_BYTES)
        out = main(raw.decode("utf-8", errors="replace"))
        if out:
            sys.stdout.write(out)
            sys.stdout.flush()
    except BaseException:
        pass
    sys.exit(0)
