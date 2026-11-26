from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def env(tmp_path, monkeypatch):
    claude_home = tmp_path / "claude_home"
    data = tmp_path / "data"
    cwd = tmp_path / "project"
    home = tmp_path / "home"
    for d in (claude_home, cwd, home):
        d.mkdir()
    # Hermetic: Path.home(), Path.cwd() and the default vault path can never reach real user dirs.
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("SKILL_PICK_CLAUDE_HOME", str(claude_home))
    monkeypatch.setenv("SKILL_PICK_WIZARD_HOME", str(data))
    return SimpleNamespace(claude_home=claude_home, data=data, cwd=cwd)


def make_skill(root: Path, dirname: str, fm: str, body: str = "body") -> Path:
    d = root / dirname
    d.mkdir(parents=True, exist_ok=True)
    f = d / "SKILL.md"
    f.write_text("---\n" + fm + "\n---\n" + body, encoding="utf-8")
    return f
