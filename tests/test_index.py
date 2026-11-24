import os
import time

import pytest

from skillpick.index import SkillIndex, build_fts_query, fingerprint
from skillpick.sources import Skill


def _skill(invoke, desc, plugin=None, tags=()):
    return Skill(
        invoke=invoke,
        name=invoke.split(":")[-1],
        description=desc,
        source="plugin" if plugin else "user",
        plugin_id=plugin,
        path=f"/x/{invoke}/SKILL.md",
        tags=tags,
    )


SKILLS = [
    _skill("performance-optimization", "Optimize slow code, speed up rendering and reduce latency"),
    _skill("test-driven-development", "Write tests first, red green refactor", tags=("tool/testing",)),
    _skill("brainstorming", "Explore intent and design before building"),
    _skill("sp:brainstorming", "Explore intent and design before building", plugin="sp@m"),
    _skill("zz-distractor", "Mentions brainstorming only in the description of this unrelated skill"),
    _skill("stripe-payments", "Stripe checkout and billing integration"),
]


@pytest.fixture
def idx(tmp_path):
    i = SkillIndex(tmp_path / "idx.db")
    i.rebuild(SKILLS, "fp1")
    return i


def invokes(hits):
    return [h.skill.invoke for h in hits]


def test_name_weighted_over_description(idx):
    hits = invokes(idx.search(["brainstorming"]))
    assert "zz-distractor" in hits
    assert set(hits[:2]) == {"brainstorming", "sp:brainstorming"}


def test_multi_query_merge_rrf(idx):
    qs = ["speed up rendering", "write tests first"]
    hits = idx.search(qs)
    assert {h.skill.invoke for h in hits[:2]} == {"performance-optimization", "test-driven-development"}
    by = {h.skill.invoke: h for h in hits}
    assert by["performance-optimization"].queries == ("speed up rendering",)
    assert by["test-driven-development"].queries == ("write tests first",)


def test_skill_hit_by_two_queries_ranks_higher(idx):
    hits = idx.search(["speed up rendering", "rendering latency", "write tests first"])
    assert hits[0].skill.invoke == "performance-optimization"
    assert hits[0].queries == ("speed up rendering", "rendering latency")
    assert hits[0].score > hits[1].score


def test_tags_searchable(idx):
    assert "test-driven-development" in invokes(idx.search(["testing"]))


def test_invoke_words_searchable(idx):
    assert "test-driven-development" in invokes(idx.search(["driven"]))
    assert "sp:brainstorming" in invokes(idx.search(["sp"]))


def test_limit_respected(idx):
    result = idx.search(["and"], limit=2)
    assert len(result) == 2


def test_allow_filter(idx):
    hits = idx.search(["brainstorming"], allow=lambda s: s.plugin_id != "sp@m")
    assert invokes(hits) == ["brainstorming", "zz-distractor"]
    assert "sp:brainstorming" not in invokes(hits)


@pytest.mark.parametrize(
    "q", ['"', "test-driven", "a:b", "(x", "speed*", "NEAR", "AND OR NOT", "", "!!!"]
)
def test_fts_special_characters_never_raise(idx, q):
    result = idx.search([q])
    if q in ("", "!!!"):
        assert result == []


def test_special_char_queries_still_match(idx):
    assert "test-driven-development" in invokes(idx.search(["test-driven"]))
    assert idx.search([]) == []


def test_rebuild_replaces_contents_and_fingerprint(idx):
    assert idx.count() == len(SKILLS) and idx.stored_fingerprint() == "fp1"
    idx.rebuild([SKILLS[-1]], "fp2")
    assert idx.count() == 1
    assert idx.stored_fingerprint() == "fp2"
    assert idx.search(["brainstorming"]) == []
    assert invokes(idx.search(["stripe"])) == ["stripe-payments"]


def test_fresh_index_is_empty(tmp_path):
    i = SkillIndex(tmp_path / "sub" / "new.db")
    assert i.stored_fingerprint() is None
    assert i.count() == 0
    assert i.all() == []
    assert i.search(["x"]) == []


def test_fingerprint_changes_on_mtime(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_text("1")
    b.write_text("2")
    f1 = fingerprint([a, b])
    assert fingerprint([b, a]) == f1
    st = a.stat()
    os.utime(a, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    f2 = fingerprint([a, b])
    assert f2 != f1
    missing = tmp_path / "gone"
    assert fingerprint([a, missing]) == fingerprint([a, missing])
    assert fingerprint([a, missing]) != fingerprint([a])


def test_get_and_all(idx):
    assert idx.get("stripe-payments").description == "Stripe checkout and billing integration"
    assert idx.get("nope") is None
    got = idx.get("test-driven-development")
    assert got == SKILLS[1] and isinstance(got.tags, tuple)
    assert sorted(s.invoke for s in idx.all()) == sorted(s.invoke for s in SKILLS)


def test_build_fts_query():
    assert build_fts_query("Speed-up: rendering!") == '"speed" OR "up" OR "rendering"'
    assert build_fts_query("AND") == '"and"'
    assert build_fts_query("!!! ") is None
    assert build_fts_query("") is None


def test_search_is_fast_on_large_index(tmp_path):
    big = [_skill(f"skill-{i}", f"description number {i} about topic{i % 50} and things") for i in range(1100)]
    i = SkillIndex(tmp_path / "big.db")
    i.rebuild(big, "x")
    t = time.perf_counter()
    for q in ("topic7 things", "description", "skill-5"):
        i.search([q, "and"])
    assert time.perf_counter() - t < 0.5


def test_failed_rebuild_keeps_old_contents(idx):
    bad = [_skill("good", "fine"), _skill("bad", "boom", tags=(object(),))]
    with pytest.raises(TypeError):
        idx.rebuild(bad, "fp-bad")
    assert idx.count() == len(SKILLS)
    assert idx.stored_fingerprint() == "fp1"
    assert "stripe-payments" in invokes(idx.search(["stripe"]))
    assert idx.get("good") is None


def test_search_correct_after_rebuild_on_other_connection(idx):
    other = SkillIndex(idx.db_path)
    assert invokes(idx.search(["stripe"])) == ["stripe-payments"]
    other.rebuild([_skill("fresh-one", "Stripe refunds and disputes")], "fp9")
    assert invokes(idx.search(["stripe"])) == ["fresh-one"]
    assert idx.stored_fingerprint() == "fp9"
    assert idx.get("stripe-payments") is None


def test_wal_mode_enabled(idx):
    import sqlite3
    with sqlite3.connect(idx.db_path) as c:
        assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_fts_is_self_contained(idx):
    import sqlite3
    with sqlite3.connect(idx.db_path) as c:
        c.execute("INSERT INTO fts(fts) VALUES('rebuild')")
        c.execute("SELECT highlight(fts, 2, '[', ']') FROM fts WHERE fts MATCH 'stripe'").fetchall()


def test_duplicate_queries_not_double_counted(idx):
    one = idx.search(["stripe"])[0]
    two = idx.search(["stripe", "stripe"])[0]
    assert one.score == two.score and two.queries == ("stripe",)


def test_allow_applied_before_ranking_and_limit(tmp_path):
    many = [_skill(f"s{i:03d}", "common words here") for i in range(150)]
    i = SkillIndex(tmp_path / "m.db")
    i.rebuild(many, "f")
    hits = i.search(["common"], limit=30, allow=lambda s: s.invoke >= "s140" or s.invoke == "s000")
    assert len(hits) == 11
    assert hits[0].score == pytest.approx(1 / 60)


def test_unicode_words():
    assert build_fts_query("Café_au LAIT") == '"café" OR "au" OR "lait"'


def test_unicode_search(tmp_path):
    i = SkillIndex(tmp_path / "u.db")
    i.rebuild([_skill("cafe-menu", "Plan a café menu")], "f")
    assert invokes(i.search(["café"])) == ["cafe-menu"]
