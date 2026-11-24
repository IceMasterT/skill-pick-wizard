import json
import os
from pathlib import Path

from conftest import make_skill
from skillpick import sources


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def install(env, plugins: dict) -> None:
    write_json(env.claude_home / "plugins" / "installed_plugins.json", {"version": 2, "plugins": plugins})


def make_plugin(tmp: Path, name: str, version: str, dirs=("brainstorming",)) -> Path:
    root = tmp / name / version
    for d in dirs:
        make_skill(root / "skills", d, f"name: {d}\ndescription: {d} desc")
    return root


def test_user_skill_invoke_is_dirname(env):
    make_skill(env.claude_home / "skills", "grok-mcp", "name: mcp\ndescription: d")
    [skill] = sources.user_skills(env.claude_home)
    assert (skill.invoke, skill.name, skill.description, skill.source) == ("grok-mcp", "mcp", "d", "user")
    assert skill.plugin_id is None
    assert skill.path == str(env.claude_home / "skills" / "grok-mcp" / "SKILL.md")


def test_name_falls_back_to_dirname_and_tags_parsed(env):
    make_skill(env.claude_home / "skills", "a", "description: d\ntags: [x, y]")
    make_skill(env.claude_home / "skills", "b", "description: d\ntags: p, q")
    a, b = sources.user_skills(env.claude_home)
    assert a.name == "a" and a.tags == ("x", "y")
    assert b.tags == ("p", "q")


def test_crlf_skill_file(env):
    d = env.claude_home / "skills" / "crlf"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_bytes(b"---\r\nname: crlf\r\ndescription: windows style\r\n---\r\nbody\r\n")
    [skill] = sources.user_skills(env.claude_home)
    assert skill.description == "windows style"


def test_symlinked_skill_followed_and_broken_symlink_skipped(env, tmp_path):
    real = tmp_path / "elsewhere"
    make_skill(real, "real", "description: d")
    skills = env.claude_home / "skills"
    skills.mkdir()
    (skills / "linked").symlink_to(real / "real")
    (skills / "broken").symlink_to(tmp_path / "missing")
    result = sources.user_skills(env.claude_home)
    assert [s.invoke for s in result] == ["linked"]
    lines = (env.data / "errors.log").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and str(skills / "broken") in lines[0] and "broken skill directory symlink" in lines[0]


def test_dangling_skill_md_symlink_logged_once_and_skipped(env, tmp_path):
    root = env.claude_home / "skills"
    make_skill(root, "good", "description: d")
    (root / "dangling").mkdir()
    (root / "dangling" / "SKILL.md").symlink_to(tmp_path / "missing.md")
    assert [s.invoke for s in sources.user_skills(env.claude_home)] == ["good"]
    sources.user_skills(env.claude_home)
    lines = (env.data / "errors.log").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert str(root / "dangling" / "SKILL.md") in lines[0] and "broken SKILL.md symlink" in lines[0]


def test_log_escapes_special_characters_in_paths(env):
    root = env.claude_home / "skills"
    for name in ("tab\there", "new\nline"):
        (root / name).mkdir(parents=True)
        (root / name / "SKILL.md").write_text("no frontmatter", encoding="utf-8")
    sources.user_skills(env.claude_home)
    sources.user_skills(env.claude_home)
    lines = (env.data / "errors.log").read_text(encoding="utf-8").split("\n")
    assert len(lines) == 3 and lines[2] == ""
    assert all(len(line.split("\t")) == 4 for line in lines[:2])
    assert any("tab\\there" in line for line in lines)


def test_non_string_description_has_accurate_reason(env):
    make_skill(env.claude_home / "skills", "listy", "description: [a, b]")
    assert sources.user_skills(env.claude_home) == []
    assert "description is not a string" in (env.data / "errors.log").read_text(encoding="utf-8")


def test_missing_skills_dir_is_empty(env):
    assert sources.user_skills(env.claude_home) == []
    assert sources.project_skills(env.cwd) == []
    assert sources.plugin_skills(env.claude_home) == []


def test_bad_files_skipped_and_logged(env):
    root = env.claude_home / "skills"
    make_skill(root, "good", "description: ok")
    nofm = root / "nofm"
    nofm.mkdir(parents=True)
    (nofm / "SKILL.md").write_text("just text", encoding="utf-8")
    make_skill(root, "empty", "description:")
    binary = root / "binary"
    binary.mkdir()
    (binary / "SKILL.md").write_bytes(b"\xff\xfe\x00")
    result = sources.user_skills(env.claude_home)
    assert [s.invoke for s in result] == ["good"]
    lines = (env.data / "errors.log").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    for name in ("nofm", "empty", "binary"):
        assert any(str(root / name / "SKILL.md") in line for line in lines)


def test_bad_files_logged_once_until_changed(env):
    root = env.claude_home / "skills"
    for name in ("a", "b"):
        (root / name).mkdir(parents=True)
        (root / name / "SKILL.md").write_text("no frontmatter", encoding="utf-8")
    binary = root / "c"
    binary.mkdir()
    (binary / "SKILL.md").write_bytes(b"\xff\xfe\x00")
    log = env.data / "errors.log"
    sources.user_skills(env.claude_home)
    sources.user_skills(env.claude_home)
    assert len(log.read_text(encoding="utf-8").splitlines()) == 3
    md = root / "a" / "SKILL.md"
    stat = md.stat()
    os.utime(md, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000_000))
    sources.user_skills(env.claude_home)
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 4
    assert sum(str(md) in line for line in lines) == 2


def test_project_skills(env):
    make_skill(env.cwd / ".claude" / "skills", "mine", "description: d")
    [skill] = sources.project_skills(env.cwd)
    assert (skill.invoke, skill.source) == ("mine", "project")


def test_plugin_skills_namespaced(env, tmp_path):
    root = make_plugin(tmp_path, "sp", "1.0")
    install(env, {"sp@m": [{"scope": "user", "installPath": str(root), "lastUpdated": "2026-01-01T00:00:00Z"}]})
    [skill] = sources.plugin_skills(env.claude_home)
    assert (skill.invoke, skill.plugin_id, skill.source) == ("sp:brainstorming", "sp@m", "plugin")


def test_multiple_installs_prefers_latest_user_scope(env, tmp_path):
    old = make_plugin(tmp_path, "sp", "1.0", dirs=("old",))
    new = make_plugin(tmp_path, "sp", "2.0", dirs=("new",))
    install(env, {"sp@m": [
        {"scope": "user", "installPath": str(new), "lastUpdated": "2026-03-01T00:00:00Z"},
        {"scope": "user", "installPath": str(old), "lastUpdated": "2026-01-01T00:00:00Z"},
    ]})
    assert [s.invoke for s in sources.plugin_skills(env.claude_home)] == ["sp:new"]


def test_last_updated_compared_as_timestamps(env, tmp_path):
    # Raw string comparison would pick "...00Z" over "...00.5+00:00" ('Z' > '.'), and the +02:00 entry
    # looks latest as text but is the oldest instant.
    fractional = make_plugin(tmp_path, "sp", "frac", dirs=("frac",))
    zulu = make_plugin(tmp_path, "sp", "zulu", dirs=("zulu",))
    offset = make_plugin(tmp_path, "sp", "off", dirs=("off",))
    junk = make_plugin(tmp_path, "sp", "junk", dirs=("junk",))
    install(env, {"sp@m": [
        {"scope": "user", "installPath": str(zulu), "lastUpdated": "2026-03-01T00:00:00Z"},
        {"scope": "user", "installPath": str(fractional), "lastUpdated": "2026-03-01T00:00:00.500+00:00"},
        {"scope": "user", "installPath": str(offset), "lastUpdated": "2026-03-01T01:00:00+02:00"},
        {"scope": "user", "installPath": str(junk), "lastUpdated": "zzz-not-a-date"},
    ]})
    assert [s.invoke for s in sources.plugin_skills(env.claude_home)] == ["sp:frac"]


def test_unparseable_last_updated_sorts_oldest(env, tmp_path):
    old = make_plugin(tmp_path, "sp", "old", dirs=("old",))
    junk = make_plugin(tmp_path, "sp", "junk", dirs=("junk",))
    install(env, {"sp@m": [
        {"scope": "user", "installPath": str(junk), "lastUpdated": "garbage"},
        {"scope": "user", "installPath": str(old), "lastUpdated": "2020-01-01T00:00:00Z"},
    ]})
    assert [s.invoke for s in sources.plugin_skills(env.claude_home)] == ["sp:old"]


def test_plugin_id_without_at_sign(env, tmp_path):
    root = make_plugin(tmp_path, "solo", "1.0", dirs=("thing",))
    install(env, {"solo": [{"scope": "user", "installPath": str(root)}]})
    [skill] = sources.plugin_skills(env.claude_home)
    assert (skill.invoke, skill.plugin_id) == ("solo:thing", "solo")


def test_plugin_with_missing_install_path_skipped(env, tmp_path):
    install(env, {"gone@m": [{"scope": "user", "installPath": str(tmp_path / "nope")}],
                  "bad@m": [{"scope": "user"}], "weird@m": "not a list"})
    assert sources.plugin_skills(env.claude_home) == []


def test_corrupt_installed_plugins_is_empty(env):
    p = env.claude_home / "plugins" / "installed_plugins.json"
    p.parent.mkdir(parents=True)
    p.write_text("{not json", encoding="utf-8")
    assert sources.plugin_skills(env.claude_home) == []


def test_project_scoped_install_only_for_its_project(env, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    root = make_plugin(tmp_path, "pp", "1.0", dirs=("s",))
    install(env, {"pp@m": [{"scope": "project", "projectPath": str(env.cwd), "installPath": str(root),
                            "lastUpdated": "2026-01-01T00:00:00Z"}]})
    [skill] = sources.project_plugin_skills(env.claude_home, env.cwd)
    assert (skill.invoke, skill.plugin_id) == ("pp:s", "pp@m")
    assert sources.project_plugin_skills(env.claude_home, other) == []
    assert sources.plugin_skills(env.claude_home) == []


def test_local_scope_and_project_install_beats_user_install(env, tmp_path):
    user_root = make_plugin(tmp_path, "pp", "1.0", dirs=("u",))
    local_root = make_plugin(tmp_path, "pp", "2.0", dirs=("l",))
    install(env, {"pp@m": [
        {"scope": "user", "installPath": str(user_root), "lastUpdated": "2026-09-01T00:00:00Z"},
        {"scope": "local", "projectPath": str(env.cwd), "installPath": str(local_root),
         "lastUpdated": "2026-01-01T00:00:00Z"},
    ]})
    assert [s.invoke for s in sources.plugin_skills(env.claude_home)] == ["pp:u"]
    assert [s.invoke for s in sources.project_plugin_skills(env.claude_home, env.cwd)] == ["pp:l"]


def test_enabled_precedence(env):
    write_json(env.claude_home / "settings.json", {"enabledPlugins": {"a@m": True, "b@m": True}})
    write_json(env.cwd / ".claude" / "settings.json", {"enabledPlugins": {"b@m": False}})
    write_json(env.cwd / ".claude" / "settings.local.json", {"enabledPlugins": {"c@m": True}})
    assert sources.enabled_plugin_ids(env.claude_home, env.cwd) == {"a@m", "c@m"}


def test_missing_or_corrupt_settings_ok(env):
    assert sources.enabled_plugin_ids(env.claude_home, env.cwd) == set()
    (env.claude_home / "settings.json").write_text("{oops", encoding="utf-8")
    write_json(env.cwd / ".claude" / "settings.json", {"enabledPlugins": []})
    assert sources.enabled_plugin_ids(env.claude_home, env.cwd) == set()


def test_skill_files_lists_everything_read(env, tmp_path):
    user_md = make_skill(env.claude_home / "skills", "u", "description: d")
    root = make_plugin(tmp_path, "sp", "1.0")
    install(env, {"sp@m": [{"scope": "user", "installPath": str(root), "lastUpdated": "2026-01-01T00:00:00Z"}]})
    files = sources.skill_files(env.claude_home)
    assert user_md in files
    assert root / "skills" / "brainstorming" / "SKILL.md" in files
    assert env.claude_home / "settings.json" in files
    assert env.claude_home / "plugins" / "installed_plugins.json" in files
    assert all(isinstance(f, Path) for f in files)


def test_skill_files_includes_unparseable_skill(env):
    bad = env.claude_home / "skills" / "bad"
    bad.mkdir(parents=True)
    (bad / "SKILL.md").write_text("nothing", encoding="utf-8")
    assert bad / "SKILL.md" in sources.skill_files(env.claude_home)


def test_project_skill_files_lists_everything_read(env, tmp_path):
    proj_md = make_skill(env.cwd / ".claude" / "skills", "p", "description: d")
    root = make_plugin(tmp_path, "pp", "1.0", dirs=("s",))
    install(env, {"pp@m": [{"scope": "project", "projectPath": str(env.cwd), "installPath": str(root),
                            "lastUpdated": "2026-01-01T00:00:00Z"}]})
    files = sources.project_skill_files(env.claude_home, env.cwd)
    assert proj_md in files
    assert root / "skills" / "s" / "SKILL.md" in files
    assert env.cwd / ".claude" / "settings.json" in files
    assert env.cwd / ".claude" / "settings.local.json" in files
    assert env.claude_home / "settings.json" not in files


def test_sources_error_log_rotates(env):
    env.data.mkdir(parents=True, exist_ok=True)
    (env.data / "errors.log").write_text("x" * (1024 * 1024 + 1), encoding="utf-8")
    skills = env.claude_home / "skills"
    (skills / "bad").mkdir(parents=True)
    (skills / "bad" / "SKILL.md").write_text("no frontmatter", encoding="utf-8")
    from skillpick import sources
    sources._ErrorLog().log(skills / "bad" / "SKILL.md", "no frontmatter")
    assert (env.data / "errors.log.1").exists()
    assert "no frontmatter" in (env.data / "errors.log").read_text(encoding="utf-8")
    assert (env.data / "errors.log").stat().st_size < 1024
