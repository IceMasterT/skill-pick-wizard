"""Filesystem locations shared by the hook and the CLI."""
import hashlib
import os
from pathlib import Path


def claude_home() -> Path:
    override = os.environ.get("SKILL_PICK_CLAUDE_HOME")
    return Path(override) if override else Path.home() / ".claude"


def data_dir() -> Path:
    override = os.environ.get("SKILL_PICK_WIZARD_HOME")
    path = Path(override) if override else Path.home() / ".local" / "share" / "skill-pick-wizard"
    path.mkdir(parents=True, exist_ok=True)
    return path


def project_key(cwd: Path) -> str:
    resolved = str(Path(cwd).resolve())
    return hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:16]
