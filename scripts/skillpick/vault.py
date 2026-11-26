"""Tool-area tags from an optional Obsidian vault of skill copies, keyed by skill directory name."""
from pathlib import Path

from .frontmatter import is_utf8_text, parse_frontmatter, tag_list

_PREFIX = "tool/"


def skill_files(vault_dir: Path | None) -> list[Path]:
    """Every vault SKILL.md that contributes tags (fixture copies under a `tests` directory are skipped)."""
    if vault_dir is None or not Path(vault_dir).is_dir():
        return []
    root = Path(vault_dir)
    return [md for md in sorted(root.rglob("SKILL.md")) if "tests" not in md.relative_to(root).parts]


def area_tags(vault_dir: Path | None) -> dict[str, tuple[str, ...]]:
    result: dict[str, tuple[str, ...]] = {}
    for md in skill_files(vault_dir):
        try:
            fm = parse_frontmatter(md.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
        tags = tuple(t for t in tag_list(fm.get("tags")) if t.startswith(_PREFIX) and is_utf8_text(t))
        if tags:
            result[md.parent.name] = tags
    return result
