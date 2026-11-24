"""Glue between discovery, the SQLite indexes and the CLI: fresh indexes, search, allowed skills."""
import time
from dataclasses import replace
from pathlib import Path

from . import vault
from .index import Hit, SkillIndex, fingerprint
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
    index = SkillIndex(Path(data) / "index" / "skills.sqlite")
    vault_dir = _vault_dir(cfg)
    fp = fingerprint(skill_files(claude_home) + vault.skill_files(vault_dir))
    if force or index.stored_fingerprint(GLOBAL) != fp:
        skills = user_skills(claude_home) + plugin_skills(claude_home)
        index.replace_segment(GLOBAL, _with_vault_tags(skills, vault.area_tags(vault_dir)), fp)

    segment = project_segment(cwd)
    index.touch_segment(segment, str(Path(cwd).resolve()), time.time())
    index.prune_segments(segment, time.time(), PROJECT_MAX_AGE_DAYS, lambda p: Path(p).is_dir())
    fp = fingerprint(project_skill_files(claude_home, cwd))
    if force or index.stored_fingerprint(segment) != fp:
        index.replace_segment(segment, project_skills(cwd) + project_plugin_skills(claude_home, cwd), fp)
    return index


class _View:
    """What this project may use: the single place that decides plugin enablement and project shadowing."""

    def __init__(self, claude_home: Path, cwd: Path, data: Path, cfg: Config, force: bool = False):
        self.index = fresh_index(claude_home, cwd, data, cfg, force)
        self.project = project_segment(cwd)
        self._enabled = enabled_plugin_ids(claude_home, cwd)
        self._shadowed = {
            s.invoke for s in self.index.all(self.project) if self._plugin_ok(s)
        }

    def _plugin_ok(self, skill: Skill) -> bool:
        return skill.plugin_id is None or skill.plugin_id in self._enabled

    def allow(self, skill: Skill, segment: str) -> bool:
        if not self._plugin_ok(skill):
            return False
        if segment == self.project:
            return True
        return segment == GLOBAL and skill.invoke not in self._shadowed

    def skills(self) -> list[Skill]:
        found = [s for seg in (GLOBAL, self.project) for s in self.index.all(seg) if self.allow(s, seg)]
        return sorted(found, key=lambda s: s.invoke)


def allowed_skills(claude_home: Path, cwd: Path, data: Path, cfg: Config) -> list[Skill]:
    """Every usable skill: enabled plugins only, and a project skill replaces a global one with the same invoke."""
    return _View(claude_home, cwd, data, cfg).skills()


def known_invokes(claude_home: Path, cwd: Path, data: Path, cfg: Config) -> set[str]:
    return {s.invoke for s in allowed_skills(claude_home, cwd, data, cfg)}


def search(
    claude_home: Path, cwd: Path, data: Path, cfg: Config, queries: list[str], limit: int = 30
) -> list[Hit]:
    view = _View(claude_home, cwd, data, cfg)
    return view.index.search(queries, limit, view.allow)


def index_counts(claude_home: Path, cwd: Path, data: Path, cfg: Config) -> tuple[int, int, int]:
    """Force a rebuild; return (global skills, project skills, searchable skills for this cwd)."""
    view = _View(claude_home, cwd, data, cfg, force=True)
    return view.index.count(GLOBAL), view.index.count(view.project), len(view.skills())
