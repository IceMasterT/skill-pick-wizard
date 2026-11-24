"""Tool-area tags from an optional Obsidian vault of skill copies, keyed by skill directory name."""
from pathlib import Path

from .frontmatter import parse_frontmatter, tag_list

_PREFIX = "tool/"


def area_tags(vault_dir: Path | None) -> dict[str, tuple[str, ...]]:
    if vault_dir is None or not Path(vault_dir).is_dir():
        return {}
    root = Path(vault_dir)
    result: dict[str, tuple[str, ...]] = {}
    for md in sorted(root.rglob("SKILL.md")):
        if "tests" in md.relative_to(root).parts:
            continue
        try:
            fm = parse_frontmatter(md.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
        tags = tuple(t for t in tag_list(fm.get("tags")) if t.startswith(_PREFIX))
        if tags:
            result[md.parent.name] = tags
    return result
