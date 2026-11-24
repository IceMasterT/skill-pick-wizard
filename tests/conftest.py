from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def env(tmp_path, monkeypatch):
    claude_home = tmp_path / "claude_home"
    data = tmp_path / "data"
    cwd = tmp_path / "project"
    for d in (claude_home, cwd):
        d.mkdir()
    monkeypatch.setenv("SKILL_PICK_CLAUDE_HOME", str(claude_home))
    monkeypatch.setenv("SKILL_PICK_WIZARD_HOME", str(data))
    return SimpleNamespace(claude_home=claude_home, data=data, cwd=cwd)


def make_skill(root: Path, dirname: str, fm: str, body: str = "body") -> Path:
    d = root / dirname
    d.mkdir(parents=True, exist_ok=True)
    f = d / "SKILL.md"
    f.write_text("---\n" + fm + "\n---\n" + body, encoding="utf-8")
    return f
