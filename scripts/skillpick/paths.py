"""Filesystem locations shared by the hook and the CLI."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path


def claude_home() -> Path:
    override = os.environ.get("SKILL_PICK_CLAUDE_HOME")
    return Path(override) if override else Path.home() / ".claude"


def data_dir() -> Path:
    override = os.environ.get("SKILL_PICK_WIZARD_HOME")
    path = Path(override) if override else Path.home() / ".local" / "share" / "skill-pick-wizard"
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def project_key(cwd: Path) -> str:
    """Short hash of the resolved project path's bytes (any file name, UTF-8 or not)."""
    return hashlib.sha256(os.fsencode(Path(cwd).resolve())).hexdigest()[:16]


ERROR_LOG_MAX_BYTES = 1024 * 1024


def append_error_log(directory: Path, text: str) -> None:
    """Append text to <directory>/errors.log, rotating it to errors.log.1 once it exceeds 1 MB.

    Raises OSError on failure; callers decide whether that matters.
    """
    log = Path(directory) / "errors.log"
    try:
        if log.stat().st_size > ERROR_LOG_MAX_BYTES:
            os.replace(log, log.with_name("errors.log.1"))
    except FileNotFoundError:
        pass
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(text)
