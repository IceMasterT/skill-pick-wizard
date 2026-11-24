import json
import os
from pathlib import Path

import pytest
from conftest import make_skill
from skillpick import service
from skillpick.index import SkillIndex
from skillpick.state import Config


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def make_plugin(env, name: str, dirs: dict[str, str]) -> Path:
    root = env.claude_home.parent / "plugin_store" / name
    for d, desc in dirs.items():
        make_skill(root / "skills", d, f"name: {d}\ndescription: {desc}")
    return root


def install(env, plugins: dict) -> None:
    write_json(env.claude_home / "plugins" / "installed_plugins.json", {"version": 2, "plugins": plugins})


def user_entry(root: Path) -> list[dict]:
    return [{"scope": "user", "installPath": str(root), "lastUpdated": "2026-01-01T00:00:00Z"}]


def cfg_for(env) -> Config:
    return Config(vault_path=str(env.claude_home.parent / "vault"))


def invokes(hits) -> list[str]:
    return [h.skill.invoke for h in hits]


def test_disabled_plugin_skills_never_returned(env):
    on = make_plugin(env, "on", {"alpha": "wizard magic spells"})
    off = make_plugin(env, "off", {"beta": "wizard magic potions"})
    install(env, {"on@m": user_entry(on), "off@m": user_entry(off)})
    write_json(env.claude_home / "settings.json", {"enabledPlugins": {"on@m": True, "off@m": True}})
    write_json(env.cwd / ".claude" / "settings.json", {"enabledPlugins": {"off@m": False}})
    cfg = cfg_for(env)
    hits = service.search(env.claude_home, env.cwd, env.data, cfg, ["wizard magic"])
    assert invokes(hits) == ["on:alpha"]
    assert service.known_invokes(env.claude_home, env.cwd, env.data, cfg) == {"on:alpha"}


def test_project_skills_only_in_their_project(env):
    other = env.cwd.parent / "other"
    other.mkdir()
    make_skill(env.cwd / ".claude" / "skills", "only-here", "description: unique zebra tool")
    cfg = cfg_for(env)
    assert invokes(service.search(env.claude_home, env.cwd, env.data, cfg, ["zebra"])) == ["only-here"]
    assert service.search(env.claude_home, other, env.data, cfg, ["zebra"]) == []


def test_vault_tags_merged(env):
    make_skill(env.claude_home / "skills", "kill-chain", "description: analyzing the cyber kill chain")
    vault = env.claude_home.parent / "vault"
    make_skill(vault / "pack", "kill-chain", "tags: [x, tool/security/threat-intelligence]\ndescription: d")
    [hit] = service.search(env.claude_home, env.cwd, env.data, cfg_for(env), ["kill chain"])
    assert "tool/security/threat-intelligence" in hit.skill.tags


def test_vault_tag_change_triggers_rebuild(env):
    make_skill(env.claude_home / "skills", "kill-chain", "description: analyzing the cyber kill chain")
    vault = env.claude_home.parent / "vault"
    vmd = make_skill(vault, "kill-chain", "tags: [tool/a]\ndescription: d")
    cfg = cfg_for(env)
    service.search(env.claude_home, env.cwd, env.data, cfg, ["kill"])
    vmd.write_text("---\ntags: [tool/b]\n---\n", encoding="utf-8")
    os.utime(vmd, ns=(1, 2))
    [hit] = service.search(env.claude_home, env.cwd, env.data, cfg, ["kill"])
    assert hit.skill.tags == ("tool/b",)


def test_rebuild_only_when_changed(env, monkeypatch):
    md = make_skill(env.claude_home / "skills", "alpha", "description: first skill")
    calls = []
    original = SkillIndex.replace_segment

    def counting(self, segment, skills, fp, **kw):
        calls.append(segment)
        return original(self, segment, skills, fp, **kw)

    monkeypatch.setattr(SkillIndex, "replace_segment", counting)
    cfg = cfg_for(env)
    service.search(env.claude_home, env.cwd, env.data, cfg, ["first"])
    service.search(env.claude_home, env.cwd, env.data, cfg, ["first"])
    assert calls.count("global") == 1
    os.utime(md, ns=(1, 12345))
    service.search(env.claude_home, env.cwd, env.data, cfg, ["first"])
    assert calls.count("global") == 2


def test_new_skill_appears_after_install(env):
    cfg = cfg_for(env)
    assert service.search(env.claude_home, env.cwd, env.data, cfg, ["fresh"]) == []
    make_skill(env.claude_home / "skills", "newcomer", "description: fresh arrival")
    assert invokes(service.search(env.claude_home, env.cwd, env.data, cfg, ["fresh"])) == ["newcomer"]


def test_project_wins_on_invoke_collision(env):
    make_skill(env.claude_home / "skills", "dup", "description: user version of dup")
    make_skill(env.cwd / ".claude" / "skills", "dup", "description: project version of dup")
    cfg = cfg_for(env)
    [hit] = service.search(env.claude_home, env.cwd, env.data, cfg, ["dup"])
    assert hit.skill.source == "project"
    [s] = [s for s in service.allowed_skills(env.claude_home, env.cwd, env.data, cfg) if s.invoke == "dup"]
    assert s.source == "project"


def test_allowed_skills_excludes_disabled_and_merges_sources(env):
    on = make_plugin(env, "on", {"alpha": "a"})
    off = make_plugin(env, "off", {"beta": "b"})
    install(env, {"on@m": user_entry(on), "off@m": user_entry(off)})
    write_json(env.claude_home / "settings.json", {"enabledPlugins": {"on@m": True}})
    make_skill(env.claude_home / "skills", "u", "description: u")
    make_skill(env.cwd / ".claude" / "skills", "p", "description: p")
    got = {s.invoke for s in service.allowed_skills(env.claude_home, env.cwd, env.data, cfg_for(env))}
    assert got == {"on:alpha", "u", "p"}


def test_force_rebuild(env, monkeypatch):
    make_skill(env.claude_home / "skills", "alpha", "description: first skill")
    cfg = cfg_for(env)
    service.fresh_index(env.claude_home, env.cwd, env.data, cfg)
    calls = []
    original = SkillIndex.replace_segment
    monkeypatch.setattr(
        SkillIndex, "replace_segment", lambda self, seg, s, fp, **kw: (calls.append(seg), original(self, seg, s, fp, **kw))
    )
    service.fresh_index(env.claude_home, env.cwd, env.data, cfg)
    assert calls == []
    service.fresh_index(env.claude_home, env.cwd, env.data, cfg, force=True)
    assert calls == ["global", service.project_segment(env.cwd)]


def test_project_rebuild_independent_of_global(env, monkeypatch):
    make_skill(env.claude_home / "skills", "alpha", "description: first skill")
    md = make_skill(env.cwd / ".claude" / "skills", "proj", "description: second skill")
    cfg = cfg_for(env)
    service.fresh_index(env.claude_home, env.cwd, env.data, cfg)
    calls = []
    original = SkillIndex.replace_segment
    monkeypatch.setattr(
        SkillIndex, "replace_segment", lambda self, seg, s, fp, **kw: (calls.append(seg), original(self, seg, s, fp, **kw))
    )
    os.utime(md, ns=(1, 777))
    service.fresh_index(env.claude_home, env.cwd, env.data, cfg)
    assert calls == [service.project_segment(env.cwd)]


def test_one_ranking_across_global_and_project(env):
    # Project segment is built first, so only bm25 (not rowid order) can demote it.
    make_skill(env.cwd / ".claude" / "skills", "aa-proj", "description: does many things and mentions alpha in passing")
    service.fresh_index(env.claude_home, env.cwd, env.data, cfg_for(env))
    for n in range(3):
        make_skill(env.claude_home / "skills", f"widget-{n}", "description: alpha widget")
    hits = invokes(service.search(env.claude_home, env.cwd, env.data, cfg_for(env), ["alpha"]))
    assert hits[-1] == "aa-proj"
    assert set(hits[:3]) == {"widget-0", "widget-1", "widget-2"}


def test_other_projects_segment_not_visible(env):
    other = env.cwd.parent / "other"
    other.mkdir()
    make_skill(other / ".claude" / "skills", "theirs", "description: unique zebra tool")
    cfg = cfg_for(env)
    assert invokes(service.search(env.claude_home, other, env.data, cfg, ["zebra"])) == ["theirs"]
    assert service.search(env.claude_home, env.cwd, env.data, cfg, ["zebra"]) == []
    assert "theirs" not in service.known_invokes(env.claude_home, env.cwd, env.data, cfg)


def test_deleted_project_dir_pruned_recent_and_current_kept(env):
    import shutil
    cfg = cfg_for(env)
    gone = env.cwd.parent / "gone"
    kept = env.cwd.parent / "kept"
    for d, name in ((gone, "gone-skill"), (kept, "kept-skill")):
        make_skill(d / ".claude" / "skills", name, "description: pruning check")
        service.search(env.claude_home, d, env.data, cfg, ["pruning"])
    make_skill(env.cwd / ".claude" / "skills", "cur-skill", "description: pruning check")
    index = service.fresh_index(env.claude_home, env.cwd, env.data, cfg)
    assert index.count(service.project_segment(gone)) == 1
    shutil.rmtree(gone)
    index = service.fresh_index(env.claude_home, env.cwd, env.data, cfg)
    assert index.count(service.project_segment(gone)) == 0
    assert index.count(service.project_segment(kept)) == 1
    assert index.count(service.project_segment(env.cwd)) == 1


def test_project_unused_for_30_days_pruned(env, monkeypatch):
    import time as real_time
    base = real_time.time()
    cfg = cfg_for(env)
    old = env.cwd.parent / "old"
    make_skill(old / ".claude" / "skills", "old-skill", "description: pruning check")
    service.search(env.claude_home, old, env.data, cfg, ["pruning"])
    monkeypatch.setattr(service.time, "time", lambda: base + 31 * 86400)
    index = service.fresh_index(env.claude_home, env.cwd, env.data, cfg)
    assert index.count(service.project_segment(old)) == 0


def test_shadowing_consistent_and_disabled_project_plugin_does_not_shadow(env):
    on = make_plugin(env, "pp", {"dup": "common words"})
    write_json(env.cwd / ".claude" / "settings.json", {"enabledPlugins": {"pp@m": False}})
    install(env, {"pp@m": [{"scope": "project", "projectPath": str(env.cwd), "installPath": str(on),
                            "lastUpdated": "2026-01-01T00:00:00Z"}]})
    make_skill(env.claude_home / "skills", "pp:dup", "description: common words")
    cfg = cfg_for(env)
    [hit] = service.search(env.claude_home, env.cwd, env.data, cfg, ["common"])
    assert hit.skill.source == "user"  # the disabled project-scope plugin skill must not shadow it
    assert [s.source for s in service.allowed_skills(env.claude_home, env.cwd, env.data, cfg)] == ["user"]


def test_skills_with_text_sqlite_cannot_store_never_break_search(env):
    root = env.claude_home / "skills"
    make_skill(root, "good-one", "description: wizard magic spells")
    make_skill(root, "lone-desc", 'description: "wizard magic \\uD800"')
    os.mkdir(os.fsencode(root) + b"/wizard\xff")
    (root / os.fsdecode(b"wizard\xff") / "SKILL.md").write_text("---\ndescription: wizard magic\n---\n")
    vault = env.claude_home.parent / "vault"
    make_skill(vault, "good-one", 'tags: ["tool/ok", "tool/x\\uDC00"]\ndescription: d')
    cfg = cfg_for(env)
    [hit] = service.search(env.claude_home, env.cwd, env.data, cfg, ["wizard magic"])
    assert hit.skill.invoke == "good-one" and hit.skill.tags == ("tool/ok",)
    assert service.known_invokes(env.claude_home, env.cwd, env.data, cfg) == {"good-one"}


def test_project_dir_whose_name_is_not_utf8_still_searches(env):
    make_skill(env.claude_home / "skills", "good-one", "description: wizard magic spells")
    os.mkdir(os.fsencode(env.cwd) + b"/proj\xfe")
    odd = env.cwd / os.fsdecode(b"proj\xfe")
    cfg = cfg_for(env)
    for _ in range(2):  # the second run reads back the segment's recorded path
        assert invokes(service.search(env.claude_home, odd, env.data, cfg, ["wizard magic"])) == ["good-one"]
    assert service.known_invokes(env.claude_home, env.cwd, env.data, cfg) == {"good-one"}


def _corrupt(db: Path) -> None:
    db.write_bytes(os.urandom(8192))
    for suffix in ("-wal", "-shm"):
        db.with_name(db.name + suffix).write_bytes(os.urandom(512))


def test_corrupt_index_file_is_deleted_and_rebuilt(env):
    make_skill(env.claude_home / "skills", "alpha", "description: wizard magic spells")
    cfg = cfg_for(env)
    db = service.fresh_index(env.claude_home, env.cwd, env.data, cfg).db_path
    _corrupt(db)
    assert invokes(service.search(env.claude_home, env.cwd, env.data, cfg, ["wizard magic"])) == ["alpha"]
    _corrupt(db)
    assert service.known_invokes(env.claude_home, env.cwd, env.data, cfg) == {"alpha"}
    _corrupt(db)
    assert service.index_counts(env.claude_home, env.cwd, env.data, cfg) == (1, 0, 1)


def test_corruption_found_mid_query_rebuilds_once(env, monkeypatch):
    import sqlite3
    make_skill(env.claude_home / "skills", "alpha", "description: wizard magic spells")
    cfg = cfg_for(env)
    calls = []
    original = SkillIndex.search

    def flaky(self, *args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise sqlite3.DatabaseError("database disk image is malformed")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(SkillIndex, "search", flaky)
    assert invokes(service.search(env.claude_home, env.cwd, env.data, cfg, ["wizard"])) == ["alpha"]
    assert len(calls) == 2


def test_persistent_corruption_is_raised_after_one_rebuild(env, monkeypatch):
    import sqlite3
    make_skill(env.claude_home / "skills", "alpha", "description: wizard magic spells")
    calls = []

    def broken(self, *args, **kwargs):
        calls.append(1)
        raise sqlite3.DatabaseError("database disk image is malformed")

    monkeypatch.setattr(SkillIndex, "search", broken)
    with pytest.raises(sqlite3.DatabaseError):
        service.search(env.claude_home, env.cwd, env.data, cfg_for(env), ["wizard"])
    assert len(calls) == 2


def test_locked_index_is_never_deleted(env, monkeypatch):
    import sqlite3
    make_skill(env.claude_home / "skills", "alpha", "description: wizard magic spells")
    cfg = cfg_for(env)
    db = service.fresh_index(env.claude_home, env.cwd, env.data, cfg).db_path
    before = db.read_bytes()

    def locked(self, *args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(SkillIndex, "search", locked)
    with pytest.raises(sqlite3.OperationalError):
        service.search(env.claude_home, env.cwd, env.data, cfg, ["wizard"])
    assert db.read_bytes() == before
