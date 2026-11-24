from pathlib import Path

from skillpick import vault


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_area_tags_by_dirname_including_nested_packs(tmp_path):
    v = tmp_path / "vault"
    write(v / "kill-chain" / "SKILL.md", "---\ntags: [x, tool/security/threat-intelligence]\n---\n")
    write(v / "ads" / "google-ads-api-quickstart" / "SKILL.md", "---\ntags:\n- tool/ads\n---\n")
    assert vault.area_tags(v) == {
        "kill-chain": ("tool/security/threat-intelligence",),
        "google-ads-api-quickstart": ("tool/ads",),
    }


def test_tests_fixture_dirs_ignored(tmp_path):
    v = tmp_path / "vault"
    write(v / "SkillSpector-main" / "tests" / "fixtures" / "evil" / "SKILL.md", "---\ntags: [tool/evil]\n---\n")
    assert "evil" not in vault.area_tags(v)


def test_skills_without_tool_tags_or_unreadable_omitted(tmp_path):
    v = tmp_path / "vault"
    write(v / "plain" / "SKILL.md", "---\ntags: [x]\n---\n")
    write(v / "notags" / "SKILL.md", "---\nname: n\n---\n")
    (v / "binary").mkdir()
    (v / "binary" / "SKILL.md").write_bytes(b"\xff\xfe\x00")
    assert vault.area_tags(v) == {}


def test_missing_vault_returns_empty(tmp_path):
    assert vault.area_tags(None) == {}
    assert vault.area_tags(tmp_path / "nope") == {}
