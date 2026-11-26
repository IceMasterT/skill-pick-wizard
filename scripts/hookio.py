"""Standard-library-only helpers shared by the hook scripts (hook.py, posttool.py).

The fallback copies of paths.data_dir() and paths.append_error_log() are used only when the
skillpick package itself cannot be imported (tests keep them in step with paths.py).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

MAX_STDIN_BYTES = 8 * 1024 * 1024
LOG_MAX_BYTES = 1024 * 1024


def fallback_data_dir() -> Path:
    override = os.environ.get("SKILL_PICK_WIZARD_HOME")
    return Path(override) if override else Path.home() / ".local" / "share" / "skill-pick-wizard"


def fallback_log(text: str) -> None:
    """Append text to errors.log without the skillpick package; never raises."""
    try:
        import fcntl

        data = fallback_data_dir()
        data.mkdir(parents=True, exist_ok=True, mode=0o700)
        log = data / "errors.log"
        with open(data / "errors.log.lock", "a") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                if log.stat().st_size > LOG_MAX_BYTES:
                    os.replace(log, log.with_name("errors.log.1"))
            except FileNotFoundError:
                pass
            with open(log, "a", encoding="utf-8", errors="backslashreplace") as fh:
                fh.write(text)
    except BaseException:
        pass


def read_stdin() -> str:
    """At most MAX_STDIN_BYTES of stdin, decoded leniently (invalid UTF-8 never raises)."""
    return sys.stdin.buffer.read(MAX_STDIN_BYTES).decode("utf-8", errors="replace")


def emit(out: str) -> None:
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
