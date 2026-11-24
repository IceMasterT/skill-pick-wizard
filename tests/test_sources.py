import json
import os
from pathlib import Path

import pytest
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


def _non_utf8_dir(root: Path, raw: bytes) -> Path:
    """A directory whose name is not valid UTF-8 (Python sees it with surrogate escapes)."""
    root.mkdir(parents=True, exist_ok=True)
    os.mkdir(os.fsencode(root) + b"/" + raw)
    return root / os.fsdecode(raw)


def test_fields_that_are_not_utf8_text_are_rejected_and_logged(env):
    root = env.claude_home / "skills"
    make_skill(root, "good", "description: ok")
    make_skill(root, "lone-desc", 'description: "bad \\uD800 text"')
    make_skill(root, "lone-name", 'name: "n\\uDC00"\ndescription: fine')
    make_skill(root, "lone-tag", 'description: fine\ntags: ["ok", "t\\uD83D"]')
    bad_dir = _non_utf8_dir(root, b"bad\xff")
    (bad_dir / "SKILL.md").write_text("---\ndescription: fine\n---\n", encoding="utf-8")
    assert [s.invoke for s in sources.user_skills(env.claude_home)] == ["good"]
    log = env.data / "errors.log"
    lines = log.read_text(encoding="utf-8").splitlines()  # the log itself stays valid UTF-8
    assert len(lines) == 4 and all("not valid UTF-8 text" in line for line in lines)
    assert any("bad\\udcff" in line for line in lines)
    sources.user_skills(env.claude_home)
    assert log.read_text(encoding="utf-8").splitlines() == lines  # logged once, also for the odd path


def test_plugin_id_that_is_not_utf8_text_is_rejected(env, tmp_path):
    root = make_plugin(tmp_path, "sp", "1.0")
    install(env, {"sp\udc80@m": [{"scope": "user", "installPath": str(root)}]})
    assert sources.plugin_skills(env.claude_home) == []
    assert "not valid UTF-8 text" in (env.data / "errors.log").read_text(encoding="utf-8")


def test_skills_claude_may_not_invoke_are_left_out(env):
    """Claude Code hides a skill from the Skill tool when disable-model-invocation is true, yes, on or 1."""
    root = env.claude_home / "skills"
    hidden = {"t1": "true", "t2": "True", "t3": "yes", "t4": '"on"', "t5": "1", "t6": " TRUE  # user only",
              # YAML numbers whose value is 1: Claude Code turns the number into the string "1"
              "n1": "1.0", "n2": "0x1", "n3": "+1", "n4": "1e0", "n5": "0o1", "n6": "10e-1", "n7": "001"}
    shown = {"f1": "false", "f2": "no", "f3": "0", "f4": "maybe", "f5": "[true]", "f6": "off",
             "f7": "0.0", "f8": "0x0", "f9": "-0",
             # other numbers become "2", "-1", "1.5": not one of Claude Code's true strings
             "f10": "2", "f11": "-1", "f12": "1.5", "f13": "0x10"}
    for name, value in {**hidden, **shown}.items():
        make_skill(root, name, f"description: d\ndisable-model-invocation: {value}")
    make_skill(root, "plain", "description: d")
    assert [s.invoke for s in sources.user_skills(env.claude_home)] == sorted([*shown, "plain"])
    assert not (env.data / "errors.log").exists()  # a deliberate setting, not an error


def test_huge_numeric_invocation_flags_never_crash_discovery(env):
    """Numbers too big for float() or int()'s digit limit are compared exactly, never converted."""
    root = env.claude_home / "skills"
    hidden = {"h1": "0" * 5000 + "1", "h2": "1." + "0" * 5000, "h3": "0x" + "0" * 5000 + "1",
              "h4": "1" + "0" * 5000 + "e-5000"}
    shown = {"s1": "1" + "0" * 400, "s2": "9" * 5000, "s3": "0x" + "f" * 5000, "s4": "1e999",
             "s5": "1" * 5000 + ".0", "s6": "1e-99999999999999999999"}
    for name, value in {**hidden, **shown}.items():
        make_skill(root, name, f"description: d\ndisable-model-invocation: {value}")
    assert [s.invoke for s in sources.user_skills(env.claude_home)] == sorted(shown)


# Plugins from a "directory" marketplace: Claude Code loads them from the marketplace folder,
# not from the installPath cache copy (which goes stale when the folder is edited).
def _known(env, marketplaces: dict) -> None:
    write_json(env.claude_home / "plugins" / "known_marketplaces.json", marketplaces)


def _dir_marketplace(tmp_path: Path, plugins: list, name: str = "local") -> Path:
    loc = tmp_path / f"{name}-market"
    write_json(loc / ".claude-plugin" / "marketplace.json", {"name": name, "plugins": plugins})
    return loc


def _dir_setup(env, tmp_path, source, skill_dirs=("fresh",), plugin="wiz"):
    """A cached install with a stale skill, plus a directory marketplace whose source holds others."""
    cache = make_plugin(tmp_path / "cache", plugin, "0.1.0", dirs=("stale",))
    loc = _dir_marketplace(tmp_path, [{"name": "other", "source": "./nope"}, {"name": plugin, "source": source}])
    install(env, {f"{plugin}@local": [{"scope": "user", "installPath": str(cache)}]})
    _known(env, {"local": {"source": {"source": "directory", "path": str(loc)}, "installLocation": str(loc)}})
    return cache, loc


def _logged(env) -> str:
    log = env.data / "errors.log"
    return log.read_text(encoding="utf-8") if log.exists() else ""


def test_directory_marketplace_plugin_is_read_from_its_source_folder(env, tmp_path):
    cache, loc = _dir_setup(env, tmp_path, "./")
    for d in ("fresh", "pick-skills"):
        make_skill(loc / "skills", d, f"description: {d} desc")
    assert [s.invoke for s in sources.plugin_skills(env.claude_home)] == ["wiz:fresh", "wiz:pick-skills"]
    assert all(str(loc) in s.path for s in sources.plugin_skills(env.claude_home))
    assert _logged(env) == ""


def test_directory_marketplace_nested_relative_source(env, tmp_path):
    cache, loc = _dir_setup(env, tmp_path, "plugins/wiz")
    make_skill(loc / "plugins" / "wiz" / "skills", "nested", "description: nested desc")
    make_skill(loc / "skills", "top-level", "description: not this plugin's")
    assert [s.invoke for s in sources.plugin_skills(env.claude_home)] == ["wiz:nested"]


@pytest.mark.parametrize("source", ["../x", "/abs/elsewhere", "plugins/../../x", "./missing", 42, ""])
def test_directory_source_that_is_invalid_falls_back_to_install_path_and_logs_once(env, tmp_path, source):
    (tmp_path / "x" / "skills").mkdir(parents=True)  # "../x" exists but is outside the marketplace
    make_skill(tmp_path / "x" / "skills", "escaped", "description: must not be read")
    _dir_setup(env, tmp_path, source)
    for _ in range(2):
        assert [s.invoke for s in sources.plugin_skills(env.claude_home)] == ["wiz:stale"]
        sources.skill_files(env.claude_home)
    lines = [l for l in _logged(env).splitlines() if "wiz@local" in l]
    assert len(lines) == 1 and "falling back to installPath" in lines[0]


def test_directory_symlink_escaping_the_marketplace_falls_back(env, tmp_path):
    outside = tmp_path / "outside"
    make_skill(outside / "skills", "escaped", "description: must not be read")
    cache, loc = _dir_setup(env, tmp_path, "./link")
    (loc / "link").symlink_to(outside)
    assert [s.invoke for s in sources.plugin_skills(env.claude_home)] == ["wiz:stale"]
    assert "wiz@local" in _logged(env)


def test_directory_marketplace_without_marketplace_json_falls_back(env, tmp_path):
    cache, loc = _dir_setup(env, tmp_path, "./")
    (loc / ".claude-plugin" / "marketplace.json").unlink()
    assert [s.invoke for s in sources.plugin_skills(env.claude_home)] == ["wiz:stale"]
    assert "wiz@local" in _logged(env)


def test_plugin_missing_from_marketplace_json_or_known_marketplaces_falls_back(env, tmp_path):
    cache, loc = _dir_setup(env, tmp_path, "./", plugin="wiz")
    write_json(loc / ".claude-plugin" / "marketplace.json", {"plugins": [{"name": "someone-else", "source": "./"}]})
    assert [s.invoke for s in sources.plugin_skills(env.claude_home)] == ["wiz:stale"]
    _known(env, {})
    assert [s.invoke for s in sources.plugin_skills(env.claude_home)] == ["wiz:stale"]
    assert len([l for l in _logged(env).splitlines() if "wiz@local" in l]) == 2  # two different reasons


@pytest.mark.parametrize("source", [{"source": "github", "repo": "o/r"}, {"source": "git", "url": "https://x"}])
def test_non_directory_marketplace_uses_install_path(env, tmp_path, source):
    cache = make_plugin(tmp_path / "cache", "gh", "1.0", dirs=("cached",))
    loc = _dir_marketplace(tmp_path, [{"name": "gh", "source": "./"}], name="remote")
    make_skill(loc / "skills", "source-copy", "description: not loaded for remote marketplaces")
    install(env, {"gh@remote": [{"scope": "user", "installPath": str(cache)}]})
    _known(env, {"remote": {"source": source, "installLocation": str(loc)}})
    assert [s.invoke for s in sources.plugin_skills(env.claude_home)] == ["gh:cached"]
    assert _logged(env) == ""


def test_directory_source_plugin_with_object_source_falls_back(env, tmp_path):
    _dir_setup(env, tmp_path, {"source": "github", "repo": "o/r"})
    assert [s.invoke for s in sources.plugin_skills(env.claude_home)] == ["wiz:stale"]
    assert "wiz@local" in _logged(env)


def test_project_scoped_directory_plugin_uses_source_folder(env, tmp_path):
    cache, loc = _dir_setup(env, tmp_path, "./")
    make_skill(loc / "skills", "fresh", "description: fresh desc")
    install(env, {"wiz@local": [{"scope": "project", "projectPath": str(env.cwd), "installPath": str(cache)}]})
    assert [s.invoke for s in sources.project_plugin_skills(env.claude_home, env.cwd)] == ["wiz:fresh"]
    assert str(loc / "skills" / "fresh" / "SKILL.md") in map(str, sources.project_skill_files(env.claude_home, env.cwd))


def test_fingerprint_inputs_follow_the_source_folder(env, tmp_path):
    from skillpick.index import fingerprint
    cache, loc = _dir_setup(env, tmp_path, "./")
    make_skill(loc / "skills", "fresh", "description: fresh desc")
    before = fingerprint(sources.skill_files(env.claude_home))
    assert str(loc / "skills" / "fresh" / "SKILL.md") in map(str, sources.skill_files(env.claude_home))
    assert not any("stale" in str(p) for p in sources.skill_files(env.claude_home))
    make_skill(loc / "skills", "added-later", "description: new desc")
    assert fingerprint(sources.skill_files(env.claude_home)) != before
