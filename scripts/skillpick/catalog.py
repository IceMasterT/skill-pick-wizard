"""Deep scan catalog: all usable skills split into balanced chunk files, one skill per line."""
import unicodedata
from pathlib import Path

from .sources import Skill
from .state import atomic_write_text


# The Read tool cuts lines longer than 2000 characters; a capped description keeps every catalog
# line whole, so a helper agent always sees the invoke and the start of what the skill does.
MAX_DESCRIPTION = 1500


def one_line(text: str) -> str:
    """Collapse every run of whitespace (newlines included) so one skill is exactly one line."""
    return " ".join(text.split())


def split_chunks(skills: list[Skill], n: int = 3) -> list[list[Skill]]:
    """Greedy balance by description length: biggest first, always into the currently smallest chunk."""
    if n < 1 or not skills:
        return []
    count = min(n, len(skills))
    chunks: list[list[Skill]] = [[] for _ in range(count)]
    totals = [0] * count
    for skill in sorted(skills, key=lambda s: (-len(s.description), s.invoke)):
        i = totals.index(min(totals))
        chunks[i].append(skill)
        totals[i] += len(skill.description)
    return [sorted(c, key=lambda s: s.invoke) for c in chunks]


def copyable(invoke: str) -> bool:
    """True when Claude or a helper agent can copy the invoke exactly from a printed line: no
    whitespace, backtick, control (Cc) or invisible format (Cf, e.g. zero-width or bidi) characters."""
    return bool(invoke) and not any(
        ch.isspace() or ch == "`" or unicodedata.category(ch) in ("Cc", "Cf") for ch in invoke
    )


def format_line(skill: Skill) -> str:
    if not copyable(skill.invoke):
        raise ValueError(f"invoke cannot be written to a catalog line: {skill.invoke!r}")
    tags = one_line(", ".join(skill.tags))
    description = one_line(skill.description)
    if len(description) > MAX_DESCRIPTION:
        description = description[: MAX_DESCRIPTION - 3].rstrip() + "..."
    return f"- `{skill.invoke}` [{tags}]: {description}"


def write_catalog(skills: list[Skill], out_dir: Path, n: int = 3) -> list[Path]:
    """Write out_dir/chunk-<i>.md (1-based) and return the paths.

    Skills whose invoke cannot be copied exactly (whitespace, backtick, control characters)
    are omitted. Nothing is touched when no skill is left. Every new chunk is written atomically
    before any stale chunk-*.md (one the new set does not contain) is removed, so a failure
    part-way never leaves a reader without a usable catalog.
    """
    out_dir = Path(out_dir)
    usable = [s for s in skills if copyable(s.invoke)]
    chunks = split_chunks(usable, n)
    if not chunks:
        return []
    out_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    paths = []
    for i, chunk in enumerate(chunks, start=1):
        path = out_dir / f"chunk-{i}.md"
        atomic_write_text(path, "".join(format_line(s) + "\n" for s in chunk))
        paths.append(path)
    keep = {p.name for p in paths}
    for old in out_dir.glob("chunk-*.md"):
        if old.name not in keep:
            old.unlink(missing_ok=True)
    return paths
