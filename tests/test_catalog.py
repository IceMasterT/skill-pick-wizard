import os
import random
import stat
import time
from pathlib import Path

import pick
import pytest
from conftest import make_skill
from skillpick.catalog import split_chunks, write_catalog
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
