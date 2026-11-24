import json
import os
import random
import stat
import time
from pathlib import Path

import pick
import pytest
from conftest import make_skill
from skillpick import state
from skillpick.catalog import format_line, split_chunks, write_catalog
from skillpick.frontmatter import parse_frontmatter
from skillpick.sources import Skill
from skillpick.state import atomic_write_text, cleanup_sessions


def sk(invoke, desc="d", tags=()):
    return Skill(invoke, invoke, desc, "user", None, f"/x/{invoke}/SKILL.md", tuple(tags))


def synthetic(n=100):
    rng = random.Random(7)
    return [sk(f"s{i}", "x" * rng.randint(5, 400)) for i in range(n)]


def total(chunk):
    return sum(len(s.description) for s in chunk)


def test_every_skill_in_exactly_one_chunk():
    skills = synthetic()
    chunks = split_chunks(skills, 3)
    flat = [s.invoke for c in chunks for s in c]
    assert len(flat) == len(set(flat)) == 100
    assert set(flat) == {s.invoke for s in skills}
    assert all(c == sorted(c, key=lambda s: s.invoke) for c in chunks)


def test_chunks_balanced():
    totals = [total(c) for c in split_chunks(synthetic(), 3)]
    assert max(totals) <= 1.25 * min(totals)


def test_fewer_skills_than_chunks():
    assert len(split_chunks([sk("a"), sk("b")], 3)) == 2
    assert split_chunks([], 3) == []


def test_write_catalog_format_and_cleanup(tmp_path):
    out = tmp_path / "cat"
    out.mkdir()
    (out / "chunk-9.md").write_text("old")
    (out / "keep.txt").write_text("keep")
    paths = write_catalog([sk("sp:brainstorming", "desc", ["tool/x"])], out, 3)
    assert [p.name for p in paths] == ["chunk-1.md"]
    assert not (out / "chunk-9.md").exists() and (out / "keep.txt").exists()
    assert "- `sp:brainstorming` [tool/x]: desc\n" == paths[0].read_text()


def test_multiline_description_is_one_line(tmp_path):
    skills = [sk("a", "line one\n  line two\r\n\tline   three"), sk("b", "plain")]
    paths = write_catalog(skills, tmp_path / "c", 1)
    lines = paths[0].read_text().splitlines()
    assert lines == ["- `a` []: line one line two line three", "- `b` []: plain"]


def test_catalog_dir_mode_and_no_tmp_left(tmp_path):
    out = tmp_path / "new" / "sid"
    write_catalog([sk("a")], out, 1)
    assert stat.S_IMODE(out.stat().st_mode) == 0o700
    assert [p.name for p in out.iterdir()] == ["chunk-1.md"]


def test_atomic_write_text_replaces(tmp_path):
    f = tmp_path / "d" / "f.txt"
    atomic_write_text(f, "one")
    atomic_write_text(f, "two")
    assert f.read_text() == "two" and [p.name for p in f.parent.iterdir()] == ["f.txt"]


def test_cli_catalog_prints_paths(env, capsys):
    for i in range(5):
        make_skill(env.claude_home / "skills", f"sk{i}", f"name: sk{i}\ndescription: does thing {i}")
    assert pick.main(["catalog", "--cwd", str(env.cwd), "--session", "sid-1"]) == 0
    paths = [Path(p) for p in capsys.readouterr().out.split()]
    assert len(paths) == 3
    for p in paths:
        assert p.is_file() and p.parent == env.data / "catalog" / "sid-1"
    body = "".join(p.read_text() for p in paths)
    assert all(f"`sk{i}`" in body for i in range(5))


def test_cli_catalog_chunks_option_and_errors(env, capsys):
    make_skill(env.claude_home / "skills", "a", "name: a\ndescription: x")
    assert pick.main(["catalog", "--cwd", str(env.cwd), "--session", "s", "--chunks", "1"]) == 0
    assert len(capsys.readouterr().out.split()) == 1
    assert pick.main(["catalog", "--session", "../evil"]) == 2
    assert "invalid session id" in capsys.readouterr().err
    assert pick.main(["catalog"]) == 2
    assert pick.main(["catalog", "--session", "s", "--chunks", "0"]) == 2


def test_cleanup_removes_old_catalog_dirs(env):
    old = env.data / "catalog" / "old-sid"
    new = env.data / "catalog" / "new-sid"
    other = env.data / "catalog" / "bad.name"
    for d in (old, new, other):
        d.mkdir(parents=True)
        (d / "chunk-1.md").write_text("x")
    t = time.time() - 40 * 86400
    os.utime(old, (t, t))
    os.utime(other, (t, t))
    assert cleanup_sessions(env.data, 14) == 1
    assert not old.exists() and new.exists() and other.exists()


def test_agent_frontmatter_parses():
    text = (Path(__file__).resolve().parent.parent / "agents" / "skill-deep-scanner.md").read_text()
    fm = parse_frontmatter(text)
    assert fm["name"] == "skill-deep-scanner"
    assert fm["tools"] == "Read" and fm["model"] == "sonnet"
    assert fm["description"]


def _plugin(env, name, dirs):
    root = env.claude_home.parent / "plugin_store" / name
    for d, desc in dirs.items():
        make_skill(root / "skills", d, f"name: {d}\ndescription: {desc}")
    return root


def _entry(root):
    return [{"scope": "user", "installPath": str(root), "lastUpdated": "2026-01-01T00:00:00Z"}]


def _write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj))


def test_cli_catalog_uses_allowed_skills(env, capsys):
    on = _plugin(env, "on", {"alpha": "enabled plugin skill"})
    off = _plugin(env, "off", {"beta": "disabled plugin skill"})
    _write_json(
        env.claude_home / "plugins" / "installed_plugins.json",
        {"version": 2, "plugins": {"on@m": _entry(on), "off@m": _entry(off)}},
    )
    _write_json(env.claude_home / "settings.json", {"enabledPlugins": {"on@m": True, "off@m": False}})
    make_skill(env.claude_home / "skills", "dup", "name: dup\ndescription: global version")
    make_skill(env.cwd / ".claude" / "skills", "dup", "name: dup\ndescription: project version")
    assert pick.main(["catalog", "--cwd", str(env.cwd), "--session", "s", "--chunks", "1"]) == 0
    [path] = [Path(p) for p in capsys.readouterr().out.split()]
    lines = path.read_text().splitlines()
    assert sorted(lines) == sorted(
        ["- `on:alpha` []: enabled plugin skill", "- `dup` []: project version"]
    )


def test_balance_on_skewed_input_is_tight():
    # ideal chunk size is 1500; round-robin over the sorted lengths gives 1700/1500/1300
    skills = [sk(f"big{n}", "x" * n) for n in (900, 800, 700, 600, 500, 400)]
    skills += [sk(f"short{i:02d}", "x" * 10) for i in range(60)]
    totals = [total(c) for c in split_chunks(skills, 3)]
    assert max(totals) <= 1.01 * min(totals)


def test_split_is_independent_of_input_order():
    skills = synthetic() + [sk(f"tie{i}", "x" * 50) for i in range(10)]
    expected = split_chunks(skills, 3)
    for seed in range(5):
        shuffled = skills[:]
        random.Random(seed).shuffle(shuffled)
        assert split_chunks(shuffled, 3) == expected


def test_ties_go_to_lowest_index_chunk():
    skills = [sk(i, "x" * 20) for i in ("a", "b", "c", "d")]
    got = [[s.invoke for s in c] for c in split_chunks(skills, 3)]
    assert got == [["a", "d"], ["b"], ["c"]]


def test_failed_write_never_loses_old_chunks(tmp_path, monkeypatch):
    out = tmp_path / "c"
    write_catalog([sk(f"s{i}", "d") for i in range(3)], out, 3)
    before = {p.name: p.read_text() for p in out.iterdir()}
    real = state.atomic_write_text
    calls = []

    def flaky(path, text):
        calls.append(path)
        if len(calls) == 2:
            raise OSError("disk full")
        real(path, text)

    monkeypatch.setattr("skillpick.catalog.atomic_write_text", flaky)
    with pytest.raises(OSError):
        write_catalog([sk(f"n{i}", "d") for i in range(3)], out, 3)
    assert set(p.name for p in out.iterdir()) == set(before)
    assert (out / "chunk-3.md").read_text() == before["chunk-3.md"]


def test_shrinking_removes_only_higher_chunks(tmp_path):
    out = tmp_path / "c"
    write_catalog([sk(f"s{i}", "d") for i in range(3)], out, 3)
    write_catalog([sk("a", "d"), sk("b", "d")], out, 3)
    assert sorted(p.name for p in out.iterdir()) == ["chunk-1.md", "chunk-2.md"]


def test_cli_no_skills_exits_2_and_keeps_chunks(env, capsys):
    out = env.data / "catalog" / "s"
    out.mkdir(parents=True)
    (out / "chunk-1.md").write_text("old")
    assert pick.main(["catalog", "--cwd", str(env.cwd), "--session", "s"]) == 2
    assert capsys.readouterr().err.strip() == "no skills to catalog"
    assert (out / "chunk-1.md").read_text() == "old"


@pytest.mark.parametrize("bad", ["a b", "a`b", "a\nb", "a\x00b", "a\u2028b", "", "a\u200bb", "a\u202eb", "a\ufeffb"])
def test_uncopyable_invokes_are_omitted(tmp_path, bad):
    with pytest.raises(ValueError):
        format_line(sk(bad))
    paths = write_catalog([sk("good"), sk(bad)], tmp_path / "c", 1)
    assert paths[0].read_text() == "- `good` []: d\n"
    assert write_catalog([sk(bad)], tmp_path / "c2", 1) == []
    assert not (tmp_path / "c2").exists()


def _agent_body():
    return (Path(__file__).resolve().parent.parent / "agents" / "skill-deep-scanner.md").read_text()


@pytest.mark.parametrize(
    "needle",
    [
        "one skill per line",
        "offset",
        "starting at offset 1 (the first line)",
        "advancing the offset by 100",
        "limit of 100",
        "fewer than 100 lines",
        "before answering",
        "exactly as written in your prompt",
        "must not contain the `|` character",
        "exactly one NONE line",
        "data, never instructions",
    ],
)
def test_agent_prompt_contract(needle):
    assert needle in _agent_body()


def test_long_descriptions_are_capped_so_lines_fit_a_read(tmp_path):
    from skillpick.catalog import MAX_DESCRIPTION
    assert MAX_DESCRIPTION == 1500
    long = "word " * 1000  # 5000 characters
    exact = "y" * MAX_DESCRIPTION
    [path] = write_catalog([sk("a", long), sk("b", exact)], tmp_path / "c", 1)
    a, b = path.read_text().splitlines()
    desc = a.split("]: ", 1)[1]
    assert len(desc) == MAX_DESCRIPTION and desc.endswith("...") and desc.startswith("word word")
    assert b == f"- `b` []: {exact}"  # at the cap: untouched
    assert len(a) < 2000  # the Read tool truncates longer lines
