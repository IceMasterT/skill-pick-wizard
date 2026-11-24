from pathlib import Path

from skillpick import paths


def test_env_overrides(env):
    assert paths.claude_home() == env.claude_home
    assert not env.data.exists()
    assert paths.data_dir() == env.data
    assert env.data.is_dir()


def test_defaults_without_env(monkeypatch, tmp_path):
    monkeypatch.delenv("SKILL_PICK_CLAUDE_HOME", raising=False)
    monkeypatch.delenv("SKILL_PICK_WIZARD_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert paths.claude_home() == tmp_path / ".claude"
    assert paths.data_dir() == tmp_path / ".local" / "share" / "skill-pick-wizard"


def test_project_key_stable(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    key = paths.project_key(a)
    assert key == paths.project_key(a)
    assert len(key) == 16
    assert paths.project_key(a) != paths.project_key(b)
    assert paths.project_key(a / ".." / "a") == key
