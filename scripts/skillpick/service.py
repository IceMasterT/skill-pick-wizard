"""Glue between discovery, the SQLite indexes and the CLI: fresh indexes, search, allowed skills."""
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


def fresh_indexes(
    claude_home: Path, cwd: Path, data: Path, cfg: Config, force: bool = False
) -> tuple[SkillIndex, SkillIndex]:
    """Return (global_index, project_index), rebuilding each only when its fingerprint changed (or force)."""
    vault_dir = _vault_dir(cfg)
    global_index = SkillIndex(Path(data) / "index" / "global.sqlite")
    fp = fingerprint(skill_files(claude_home) + vault.skill_files(vault_dir))
    if force or global_index.stored_fingerprint() != fp:
        skills = user_skills(claude_home) + plugin_skills(claude_home)
        global_index.rebuild(_with_vault_tags(skills, vault.area_tags(vault_dir)), fp)

    project_index = SkillIndex(Path(data) / "index" / f"project-{project_key(cwd)}.sqlite")
    fp = fingerprint(project_skill_files(claude_home, cwd))
    if force or project_index.stored_fingerprint() != fp:
        project_index.rebuild(project_skills(cwd) + project_plugin_skills(claude_home, cwd), fp)
    return global_index, project_index


def _is_allowed(enabled: set[str]):
    return lambda s: s.plugin_id is None or s.plugin_id in enabled


def allowed_skills(claude_home: Path, cwd: Path, data: Path, cfg: Config) -> list[Skill]:
    """Every usable skill: enabled plugins only, and a project skill replaces a global one with the same invoke."""
    global_index, project_index = fresh_indexes(claude_home, cwd, data, cfg)
    allow = _is_allowed(enabled_plugin_ids(claude_home, cwd))
    project = [s for s in project_index.all() if allow(s)]
    shadowed = {s.invoke for s in project}
    return sorted(
        [s for s in global_index.all() if allow(s) and s.invoke not in shadowed] + project,
        key=lambda s: s.invoke,
    )


def known_invokes(claude_home: Path, cwd: Path, data: Path, cfg: Config) -> set[str]:
    return {s.invoke for s in allowed_skills(claude_home, cwd, data, cfg)}


def search(
    claude_home: Path, cwd: Path, data: Path, cfg: Config, queries: list[str], limit: int = 30
) -> list[Hit]:
    global_index, project_index = fresh_indexes(claude_home, cwd, data, cfg)
    allow = _is_allowed(enabled_plugin_ids(claude_home, cwd))
    shadowed = {s.invoke for s in project_index.all() if allow(s)}
    hits = project_index.search(queries, limit, allow)
    hits += global_index.search(queries, limit, lambda s: allow(s) and s.invoke not in shadowed)
    hits.sort(key=lambda h: (-h.score, h.skill.invoke))
    return hits[:limit]
