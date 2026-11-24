"""Glue between discovery, the SQLite indexes and the CLI: fresh indexes, search, allowed skills."""
import sqlite3
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import TypeVar

from . import vault
from .index import Hit, SkillIndex, fingerprint, index_path, is_corruption
from .paths import project_key
from .sources import (
    Skill,
    enabled_plugin_ids,
    plugin_skills,
    project_plugin_skills,
    project_skill_files,
    project_skills,
    skill_files,
    user_skills,
)
from .state import Config

T = TypeVar("T")


def _vault_dir(cfg: Config) -> Path:
    return Path(cfg.vault_path).expanduser()


def _with_vault_tags(skills: list[Skill], tags: dict[str, tuple[str, ...]]) -> list[Skill]:
    merged = []
    for s in skills:
        extra = tuple(t for t in tags.get(Path(s.path).parent.name, ()) if t not in s.tags)
        merged.append(replace(s, tags=s.tags + extra) if extra else s)
    return merged


GLOBAL = "global"
PROJECT_MAX_AGE_DAYS = 30


def project_segment(cwd: Path) -> str:
    return f"project:{project_key(cwd)}"


def fresh_index(
    claude_home: Path, cwd: Path, data: Path, cfg: Config, force: bool = False
) -> SkillIndex:
    """The single index, with the global and this project's segments rebuilt when their fingerprints changed."""
    index = SkillIndex(index_path(data))
    vault_dir = _vault_dir(cfg)
    fp = fingerprint(skill_files(claude_home) + vault.skill_files(vault_dir))
    if force or index.stored_fingerprint(GLOBAL) != fp:
        skills = user_skills(claude_home) + plugin_skills(claude_home)
        index.replace_segment(GLOBAL, _with_vault_tags(skills, vault.area_tags(vault_dir)), fp)

    segment = project_segment(cwd)
    project_path = str(Path(cwd).resolve())
    index.touch_segment(segment, project_path, time.time())
    index.prune_segments(segment, time.time(), PROJECT_MAX_AGE_DAYS, lambda p: Path(p).is_dir())
    fp = fingerprint(project_skill_files(claude_home, cwd))
    if force or index.stored_fingerprint(segment) != fp:
        index.replace_segment(
            segment, project_skills(cwd) + project_plugin_skills(claude_home, cwd), fp, path=project_path
        )
    return index


class _View:
    """What this project may use. Plugin enablement is decided here; project-over-global shadowing is
    applied by the index inside the same read snapshot as the query."""

    def __init__(self, claude_home: Path, cwd: Path, data: Path, cfg: Config, force: bool = False):
        self.index = fresh_index(claude_home, cwd, data, cfg, force)
        self.project = project_segment(cwd)
        self._enabled = enabled_plugin_ids(claude_home, cwd)

    def allow(self, skill: Skill, segment: str) -> bool:
        if segment not in (GLOBAL, self.project):
            return False
        return skill.plugin_id is None or skill.plugin_id in self._enabled

    def skills(self) -> list[Skill]:
        return self.index.visible(self.allow, self.project)


def _with_view(claude_home: Path, cwd: Path, data: Path, cfg: Config, op: Callable[[_View], T],
               force: bool = False) -> T:
    """Run op on a fresh view. The index is a rebuildable cache: if SQLite reports it corrupt (when
    opening it or in the middle of a query), its files are deleted and op runs once more on a full
    rebuild. Any other error, or corruption that survives the rebuild, propagates."""
    try:
        return op(_View(claude_home, cwd, data, cfg, force))
    except sqlite3.DatabaseError as exc:
        if not is_corruption(exc):
            raise
    SkillIndex(index_path(data)).reset()
    return op(_View(claude_home, cwd, data, cfg, force=True))


def allowed_skills(claude_home: Path, cwd: Path, data: Path, cfg: Config) -> list[Skill]:
    """Every usable skill: enabled plugins only, and a project skill replaces a global one with the same invoke."""
    return _with_view(claude_home, cwd, data, cfg, lambda view: view.skills())


def known_invokes(claude_home: Path, cwd: Path, data: Path, cfg: Config) -> set[str]:
    return {s.invoke for s in allowed_skills(claude_home, cwd, data, cfg)}


MIN_CANDIDATES = 30
CANDIDATES_PER_QUERY = 5


def candidate_limit(queries: list[str]) -> int:
    """Default number of search candidates: 30, or 5 per distinct query when there are more than 6."""
    return max(MIN_CANDIDATES, CANDIDATES_PER_QUERY * len(dict.fromkeys(queries)))


def search(
    claude_home: Path, cwd: Path, data: Path, cfg: Config, queries: list[str], limit: int | None = None
) -> list[Hit]:
    """Search candidates; `limit` defaults to candidate_limit(queries)."""
    if limit is None:
        limit = candidate_limit(queries)
    return _with_view(
        claude_home, cwd, data, cfg, lambda view: view.index.search(queries, limit, view.allow, view.project)
    )


def index_counts(claude_home: Path, cwd: Path, data: Path, cfg: Config) -> tuple[int, int, int]:
    """Force a rebuild; return (global skills, project skills, searchable skills for this cwd)."""
    return _with_view(
        claude_home, cwd, data, cfg,
        lambda view: (view.index.count(GLOBAL), view.index.count(view.project), len(view.skills())),
        force=True,
    )
