"""Deep scan catalog: all usable skills split into balanced chunk files, one skill per line."""
from pathlib import Path

from .sources import Skill
from .state import atomic_write_text


def _one_line(text: str) -> str:
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


def format_line(skill: Skill) -> str:
    tags = _one_line(", ".join(skill.tags))
    return f"- `{skill.invoke}` [{tags}]: {_one_line(skill.description)}"


def write_catalog(skills: list[Skill], out_dir: Path, n: int = 3) -> list[Path]:
    """Write out_dir/chunk-<i>.md (1-based), replacing any old chunk files."""
    out_dir = Path(out_dir)
    out_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    for old in out_dir.glob("chunk-*.md"):
        old.unlink(missing_ok=True)
    paths = []
    for i, chunk in enumerate(split_chunks(skills, n), start=1):
        path = out_dir / f"chunk-{i}.md"
        atomic_write_text(path, "".join(format_line(s) + "\n" for s in chunk))
        paths.append(path)
    return paths
