"""Config and per-session plan state, stored as atomically written JSON.

The hook calls load_config and load_session on every prompt, so every load_*
function tolerates missing, corrupt, wrong-typed or partially valid content and
never raises.
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

MODES = ("approve", "show", "silent")
STATUSES = ("planned", "skipped")
_SID_RE = re.compile(r"[A-Za-z0-9_-]{1,128}")
_MODEL_RE = re.compile(r"[A-Za-z0-9._:-]{1,64}")


@dataclass
class Config:
    mode: str = "approve"
    deep_scan: bool = False
    deep_scan_model: str = "sonnet"
    vault_path: str = "~/Documents/obsidian-brain/100-tools/Tool Archive"


@dataclass
class Stage:
    stage: str
    skills: list[str]


@dataclass
class SessionState:
    status: str
    job: str = ""
    stages: list[Stage] = field(default_factory=list)
    mode: str = ""
    deep_scan: bool = False
    updated: float = 0.0


def atomic_write_text(path: Path, text: str) -> None:
    """Write text so a reader sees either the old file or the new one, never a partial."""
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False
    )
    try:
        with tmp:
            tmp.write(text)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp.name, path)
        _fsync_dir(path.parent)
    except BaseException:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        raise


def atomic_write_json(path: Path, obj) -> None:
    """Atomically write obj as JSON (see atomic_write_text)."""
    atomic_write_text(path, json.dumps(obj, ensure_ascii=False))


def _fsync_dir(directory: Path) -> None:
    """Best effort: make the rename itself durable. Failure never undoes the write."""
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return None


def load_config(data: Path) -> Config:
    cfg = Config()
    raw = _read_json(Path(data) / "config.json")
    if not isinstance(raw, dict):
        return cfg
    if raw.get("mode") in MODES:
        cfg.mode = raw["mode"]
    if isinstance(raw.get("deep_scan"), bool):
        cfg.deep_scan = raw["deep_scan"]
    model = raw.get("deep_scan_model")
    if isinstance(model, str) and _MODEL_RE.fullmatch(model.strip()):
        cfg.deep_scan_model = model.strip()
    vault = raw.get("vault_path")
    if isinstance(vault, str) and vault.strip():
        cfg.vault_path = vault.strip()
    return cfg


def save_config(data: Path, cfg: Config) -> None:
    atomic_write_json(Path(data) / "config.json", asdict(cfg))


def valid_session_id(sid: str) -> bool:
    return isinstance(sid, str) and _SID_RE.fullmatch(sid) is not None


def _session_path(data: Path, sid: str) -> Path:
    if not valid_session_id(sid):
        raise ValueError(f"invalid session id: {sid!r}")
    return Path(data) / "sessions" / f"{sid}.json"


def _parse_stages(raw) -> list[Stage]:
    if not isinstance(raw, list):
        return []
    stages = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name, skills = item.get("stage"), item.get("skills")
        if isinstance(name, str) and isinstance(skills, list) and all(
            isinstance(s, str) for s in skills
        ):
            stages.append(Stage(name, list(skills)))
    return stages


def _finite_float(value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    try:
        result = float(value)
    except OverflowError:
        return 0.0
    return result if math.isfinite(result) else 0.0


def load_session(data: Path, sid: str) -> SessionState | None:
    if not valid_session_id(sid):
        return None
    raw = _read_json(Path(data) / "sessions" / f"{sid}.json")
    if not isinstance(raw, dict) or raw.get("status") not in STATUSES:
        return None
    updated = _finite_float(raw.get("updated"))
    return SessionState(
        status=raw["status"],
        job=raw["job"] if isinstance(raw.get("job"), str) else "",
        stages=_parse_stages(raw.get("stages")),
        mode=raw["mode"] if raw.get("mode") in MODES else "",
        deep_scan=raw["deep_scan"] if isinstance(raw.get("deep_scan"), bool) else False,
        updated=updated,
    )


def _save_session(data: Path, sid: str, state: SessionState) -> None:
    atomic_write_json(_session_path(data, sid), asdict(state))


def save_plan(
    data: Path, sid: str, job: str, stages: list[Stage], mode: str, deep_scan: bool
) -> None:
    _session_path(data, sid)  # validate before building anything
    _save_session(
        data,
        sid,
        SessionState(
            status="planned",
            job=job,
            stages=list(stages),
            mode=mode,
            deep_scan=deep_scan,
            updated=time.time(),
        ),
    )


def mark_skipped(data: Path, sid: str) -> None:
    _save_session(data, sid, SessionState(status="skipped", updated=time.time()))


def clear_session(data: Path, sid: str) -> None:
    if not valid_session_id(sid):
        return
    try:
        _session_path(data, sid).unlink()
    except FileNotFoundError:
        pass


def cleanup_sessions(data: Path, max_age_days: int = 14, now: float | None = None) -> int:
    """Remove stale session files, catalog/<sid>/ directories and orphaned temp files; returns how many were removed.

    Session files (<valid sid>.json in sessions/) and *.tmp files (left behind by a
    killed writer, in sessions/ and the data dir) and catalog/<valid sid>/ directories older than max_age_days are removed.
    A catalog directory ages by its own mtime, which every catalog write refreshes (files are
    replaced and removed inside it), so a session that keeps scanning keeps its catalog.
    Only files whose mtime is past the cutoff are touched, so a live writer's fresh
    temp file or session is never removed. A save landing between the stat and the
    unlink of an already stale file would be lost; that race is accepted because the
    state is disposable and the window is microseconds.
    """
    now = time.time() if now is None else now
    cutoff = now - max_age_days * 86400
    data = Path(data)
    candidates = [
        f for f in (data / "sessions").glob("*.json") if valid_session_id(f.stem)
    ]
    candidates += (data / "sessions").glob("*.tmp")
    candidates += data.glob("*.tmp")
    removed = 0
    for d in (data / "catalog").glob("*"):
        try:
            if (
                valid_session_id(d.name)
                and d.is_dir()
                and not d.is_symlink()
                and d.stat().st_mtime < cutoff
            ):
                shutil.rmtree(d)
                removed += 1
        except OSError:
            continue
    for f in candidates:
        try:
            if f.stat().st_mtime >= cutoff:
                continue
            f.unlink()
            removed += 1
        except OSError:
            continue
    return removed
