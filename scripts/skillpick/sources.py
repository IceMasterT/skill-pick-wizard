"""Discovery of user, project and plugin skills, and enabled-plugin resolution."""
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .frontmatter import parse_frontmatter, tag_list
from .paths import append_error_log, data_dir


@dataclass(frozen=True)
class Skill:
    invoke: str
    name: str
    description: str
    source: str  # "user" | "project" | "plugin"
    plugin_id: str | None
    path: str
    tags: tuple[str, ...] = ()


_ESCAPES = {"\\": "\\\\", "\t": "\\t", "\n": "\\n", "\r": "\\r"}


def _encode(field: str) -> str:
    """Escape backslash, tab, newline and CR so a field never breaks the tab/newline line format."""
    return "".join(_ESCAPES.get(ch, ch) for ch in field)


def _timestamp(value) -> float:
    """Seconds since the epoch for an ISO-8601 string; unparseable values sort oldest."""
    if not isinstance(value, str):
        return float("-inf")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return float("-inf")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


class _ErrorLog:
    """Append-only errors.log; one line per (path, mtime_ns, reason), read once per discovery run.

    Fields are backslash-escaped on write (see _encode); keys are compared in encoded form, which is
    equivalent to comparing decoded values because the encoding is injective.
    """

    def __init__(self) -> None:
        self._file = None
        self._dir = None
        self._seen: set[tuple[str, str, str]] = set()
        try:
            self._dir = data_dir()
            self._file = self._dir / "errors.log"
            for line in self._file.read_text(encoding="utf-8").split("\n"):
                parts = line.split("\t")
                if len(parts) == 4:
                    self._seen.add((parts[1], parts[2], parts[3]))
        except (OSError, UnicodeDecodeError):
            pass

    def log(self, path: Path, reason: str) -> None:
        try:
            mtime = str(path.stat().st_mtime_ns)
        except OSError:
            mtime = "missing"
        key = (_encode(str(path)), mtime, _encode(reason))
        if self._file is None or key in self._seen:
            return
        self._seen.add(key)
        try:
            stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            append_error_log(self._dir, f"{stamp}\t{key[0]}\t{mtime}\t{key[2]}\n")
        except OSError:
            pass  # logging must never break discovery


def read_skill(skill_md: Path, invoke: str, source: str, plugin_id: str | None,
               errors: _ErrorLog | None = None) -> Skill | None:
    errors = errors or _ErrorLog()
    try:
        text = skill_md.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        errors.log(skill_md, f"unreadable: {type(exc).__name__}")
        return None
    fm = parse_frontmatter(text)
    if not fm:
        errors.log(skill_md, "no frontmatter")
        return None
    description = fm.get("description")
    if description is not None and not isinstance(description, str):
        errors.log(skill_md, "description is not a string")
        return None
    if description is None or not description.strip():
        errors.log(skill_md, "empty description")
        return None
    name = fm.get("name")
    if not isinstance(name, str) or not name.strip():
        name = skill_md.parent.name
    return Skill(
        invoke=invoke,
        name=name.strip(),
        description=description.strip(),
        source=source,
        plugin_id=plugin_id,
        path=os.path.abspath(skill_md),
        tags=tag_list(fm.get("tags")),
    )


def _skill_mds(skills_root: Path, errors: _ErrorLog | None = None) -> list[Path]:
    """SKILL.md files directly under each child of skills_root (symlinks followed).

    Dangling symlinks (a skill directory or its SKILL.md) are skipped, and logged when `errors` is given.
    """
    try:
        children = sorted(skills_root.iterdir())
    except OSError:
        return []
    found = []
    for child in children:
        if child.is_symlink() and not child.exists():
            if errors:
                errors.log(child, "broken skill directory symlink")
            continue
        md = child / "SKILL.md"
        if md.is_symlink() and not md.exists():
            if errors:
                errors.log(md, "broken SKILL.md symlink")
            continue
        if md.is_file():
            found.append(md)
    return found


def _read_dir(skills_root: Path, source: str, prefix: str = "", plugin_id: str | None = None,
              errors: _ErrorLog | None = None) -> list[Skill]:
    errors = errors or _ErrorLog()
    skills = []
    for md in _skill_mds(skills_root, errors):
        skill = read_skill(md, prefix + md.parent.name, source, plugin_id, errors)
        if skill:
            skills.append(skill)
    return skills


def user_skills(claude_home: Path) -> list[Skill]:
    return _read_dir(claude_home / "skills", "user")


def project_skills(cwd: Path) -> list[Skill]:
    return _read_dir(cwd / ".claude" / "skills", "project")


def _load_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _plugin_roots(claude_home: Path, cwd: Path | None) -> list[tuple[str, Path]]:
    """(plugin_id, install dir) per plugin: user-scope installs when cwd is None, else installs scoped to cwd."""
    plugins = _load_json(claude_home / "plugins" / "installed_plugins.json").get("plugins")
    if not isinstance(plugins, dict):
        return []
    project = str(Path(cwd).resolve()) if cwd is not None else None
    roots = []
    for plugin_id, entries in sorted(plugins.items()):
        if not isinstance(entries, list):
            continue
        best = None
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if project is None:
                if entry.get("scope") != "user":
                    continue
            else:
                if entry.get("scope") not in ("project", "local"):
                    continue
                path = entry.get("projectPath")
                if not isinstance(path, str) or str(Path(path).resolve()) != project:
                    continue
            install = entry.get("installPath")
            if not isinstance(install, str) or not Path(install).is_dir():
                continue
            key = _timestamp(entry.get("lastUpdated"))
            if best is None or key > best[0]:
                best = (key, Path(install))
        if best:
            roots.append((plugin_id, best[1]))
    return roots


def _plugin_skills(claude_home: Path, cwd: Path | None) -> list[Skill]:
    skills = []
    errors = _ErrorLog()
    for plugin_id, root in _plugin_roots(claude_home, cwd):
        prefix = plugin_id.split("@", 1)[0] + ":"
        skills.extend(_read_dir(root / "skills", "plugin", prefix, plugin_id, errors))
    return skills


def plugin_skills(claude_home: Path) -> list[Skill]:
    """Skills of every installed plugin (latest user-scope install). Enablement is filtered at search time."""
    return _plugin_skills(claude_home, None)


def project_plugin_skills(claude_home: Path, cwd: Path) -> list[Skill]:
    """Skills of plugins installed with project/local scope for exactly this cwd."""
    return _plugin_skills(claude_home, cwd)


def enabled_plugin_ids(claude_home: Path, cwd: Path) -> set[str]:
    enabled: dict[str, bool] = {}
    for settings in (claude_home / "settings.json",
                     cwd / ".claude" / "settings.json",
                     cwd / ".claude" / "settings.local.json"):
        plugins = _load_json(settings).get("enabledPlugins")
        if isinstance(plugins, dict):
            enabled.update({k: v is True for k, v in plugins.items()})
    return {k for k, v in enabled.items() if v}


def skill_files(claude_home: Path) -> list[Path]:
    """Global fingerprint inputs: every SKILL.md read for user/plugin skills plus the two global JSON files."""
    files = _skill_mds(claude_home / "skills")
    for _, root in _plugin_roots(claude_home, None):
        files.extend(_skill_mds(root / "skills"))
    files += [claude_home / "settings.json", claude_home / "plugins" / "installed_plugins.json"]
    return files


def project_skill_files(claude_home: Path, cwd: Path) -> list[Path]:
    """Per-project fingerprint inputs: project and project-scoped plugin SKILL.md files plus project settings."""
    files = _skill_mds(cwd / ".claude" / "skills")
    for _, root in _plugin_roots(claude_home, cwd):
        files.extend(_skill_mds(root / "skills"))
    files += [cwd / ".claude" / "settings.json", cwd / ".claude" / "settings.local.json"]
    return files
