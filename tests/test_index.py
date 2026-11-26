import os
import sqlite3
import time
from contextlib import closing

import pytest

from skillpick.index import _SCHEMA_VERSION, SkillIndex, build_fts_query, fingerprint
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
    i.replace_segment("global", SKILLS, "fp1")
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


# Regression fixture for multi-query fusion: six skills that are each the clear best match for one
# query, buried among decoys that loosely match several queries with generic words.
_TARGETS = {
    "write the failing tests first": _skill(
        "test-driven-development", "Write the failing tests first, then the code that makes them pass"),
    "harden code against vulnerabilities": _skill(
        "security-and-hardening", "Harden code against vulnerabilities: validate input, guard secrets"),
    "verify work before claiming it is done": _skill(
        "verification-before-completion", "Verify work before claiming it is done by running the checks"),
    "build frontend ui components": _skill(
        "frontend-ui-engineering", "Build frontend UI components, layouts and styles for web pages"),
    "brainstorm design options": _skill(
        "brainstorming", "Brainstorm design options and intent before building anything"),
    "stripe checkout payment": _skill(
        "stripe-payments", "Stripe checkout payment and billing integration"),
}
_GENERIC = ["write", "tests", "code", "against", "work", "before", "build", "components",
            "design", "options", "checkout", "payment", "first", "frontend", "done", "verify"]


def _decoys(n=50):
    """Each decoy names many generic query words, so it loosely matches most queries at once."""
    out = []
    for i in range(n):
        words = [_GENERIC[(i + k * 3) % len(_GENERIC)] for k in range(8)]
        out.append(_skill(f"decoy-{i:02d}", "Helps with " + " and ".join(words) + " in general projects"))
    return out


@pytest.fixture
def fusion_idx(tmp_path):
    i = SkillIndex(tmp_path / "fusion.db")
    i.replace_segment("global", list(_TARGETS.values()) + _decoys(), "f")
    return i


def test_each_fixture_query_has_its_target_on_top(fusion_idx):
    for query, target in _TARGETS.items():
        assert invokes(fusion_idx.search([query]))[0] == target.invoke, query


def test_each_query_top_hit_survives_fusion_with_five_others(fusion_idx):
    hits = fusion_idx.search(list(_TARGETS))
    top = invokes(hits[:len(_TARGETS)])
    assert set(top) == {t.invoke for t in _TARGETS.values()}
    by = {h.skill.invoke: h for h in hits}
    for query, target in _TARGETS.items():
        assert query in by[target.invoke].queries


def test_fused_order_is_best_rank_then_rrf_then_invoke(tmp_path):
    i = SkillIndex(tmp_path / "o.db")
    i.replace_segment("global", [
        _skill("alpha-one", "alpha"),
        _skill("beta-one", "beta"),
        _skill("both", "alpha beta words and more words to lower its bm25 rank for each query"),
    ], "f")
    hits = i.search(["alpha", "beta"])
    # alpha-one and beta-one are each rank 0 for one query; "both" is rank 1 in two queries.
    assert invokes(hits) == ["alpha-one", "beta-one", "both"]
    assert hits[2].queries == ("alpha", "beta")
    assert hits[2].score > hits[0].score  # higher RRF sum, still ranked after the rank-0 hits


def test_each_query_list_is_truncated_to_limit(tmp_path):
    i = SkillIndex(tmp_path / "t.db")
    i.replace_segment("global", [_skill(f"s{n:02d}", "common " + "pad " * n) for n in range(10)]
                      + [_skill("other", "rare")], "f")
    hits = i.search(["common", "rare"], limit=3)
    # Each query contributes at most `limit` hits; "other" is rank 0 for "rare", so it is kept.
    assert invokes(hits) == ["other", "s00", "s01"]
    assert all(h.queries == ("common",) for h in hits[1:])


def test_matched_lists_only_queries_whose_top_list_has_the_hit(tmp_path):
    i = SkillIndex(tmp_path / "m.db")
    i.replace_segment("global", [_skill(f"x{n:02d}", "zeta " + "pad " * n) for n in range(5)]
                      + [_skill("y", "zeta pad pad pad pad pad pad pad pad eta")], "f")
    hits = {h.skill.invoke: h for h in i.search(["eta", "zeta"], limit=2)}
    assert hits["y"].queries == ("eta",)  # y matches "zeta" too, but below that query's top 2


def test_stop_words_are_dropped_unless_nothing_remains():
    from skillpick.index import query_terms
    assert query_terms("how to write the tests for it") == ["write", "tests"]
    assert query_terms("the and of") == ["the", "and", "of"]
    assert query_terms("Tests tests TESTS") == ["tests"]
    assert query_terms("!!!") == []


def test_hit_must_match_at_least_half_of_the_query_terms(tmp_path):
    i = SkillIndex(tmp_path / "h.db")
    i.replace_segment("global", [
        _skill("one-of-four", "kubernetes only"),
        _skill("two-of-four", "kubernetes cluster"),
        _skill("all-four", "kubernetes cluster autoscaling costs"),
    ], "f")
    assert invokes(i.search(["kubernetes cluster autoscaling costs"])) == ["all-four", "two-of-four"]
    # Three terms need two matches.
    assert invokes(i.search(["kubernetes cluster nothing"])) == ["two-of-four", "all-four"]


def test_query_without_a_real_match_returns_nothing(idx):
    # Only stop words match broadly; with the real terms gone nothing is returned.
    assert idx.search(["the quantum entanglement of it"]) == []


def test_tags_searchable(idx):
    assert "test-driven-development" in invokes(idx.search(["testing"]))


def test_invoke_words_searchable(idx):
    assert "test-driven-development" in invokes(idx.search(["driven"]))
    assert "sp:brainstorming" in invokes(idx.search(["sp"]))


def test_limit_respected(idx):
    result = idx.search(["and"], limit=2)
    assert len(result) == 2


def test_allow_filter(idx):
    hits = idx.search(["brainstorming"], allow=lambda s, seg: s.plugin_id != "sp@m")
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
    idx.replace_segment("global", [SKILLS[-1]], "fp2")
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
    assert build_fts_query("Speed-up: rendering!") == '"speed" OR "rendering"'
    assert build_fts_query("AND") == '"and"'
    assert build_fts_query("!!! ") is None
    assert build_fts_query("") is None


def test_search_is_fast_on_large_index(tmp_path):
    big = [_skill(f"skill-{i}", f"description number {i} about topic{i % 50} and things") for i in range(1100)]
    i = SkillIndex(tmp_path / "big.db")
    i.replace_segment("global", big, "x")
    i.search(["warm"])
    t = time.perf_counter()
    for q in ("topic7 things", "description", "skill-5"):
        i.search([q, "and"])
    assert time.perf_counter() - t < 2.0


def test_failed_rebuild_keeps_old_contents(idx):
    bad = [_skill("good", "fine"), _skill("bad", "boom", tags=(object(),))]
    with pytest.raises(TypeError):
        idx.replace_segment("global", bad, "fp-bad")
    assert idx.count() == len(SKILLS)
    assert idx.stored_fingerprint() == "fp1"
    assert "stripe-payments" in invokes(idx.search(["stripe"]))
    assert idx.get("good") is None


def test_search_correct_after_rebuild_on_other_connection(idx):
    other = SkillIndex(idx.db_path)
    assert invokes(idx.search(["stripe"])) == ["stripe-payments"]
    other.replace_segment("global", [_skill("fresh-one", "Stripe refunds and disputes")], "fp9")
    assert invokes(idx.search(["stripe"])) == ["fresh-one"]
    assert idx.stored_fingerprint() == "fp9"
    assert idx.get("stripe-payments") is None


def _db(path):
    """A connection that is closed (and committed) when the block ends; sqlite3's own context
    manager only commits."""
    return closing(sqlite3.connect(path, isolation_level=None))


def test_wal_mode_enabled(idx):
    with _db(idx.db_path) as c:
        assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_fts_is_self_contained(idx):
    with _db(idx.db_path) as c:
        c.execute("INSERT INTO fts(fts) VALUES('rebuild')")
        c.execute("SELECT highlight(fts, 2, '[', ']') FROM fts WHERE fts MATCH 'stripe'").fetchall()


def test_duplicate_queries_not_double_counted(idx):
    one = idx.search(["stripe"])[0]
    two = idx.search(["stripe", "stripe"])[0]
    assert one.score == two.score and two.queries == ("stripe",)


def test_allow_applied_before_ranking_and_limit(tmp_path):
    many = [_skill(f"s{i:03d}", "common words here") for i in range(150)]
    i = SkillIndex(tmp_path / "m.db")
    i.replace_segment("global", many, "f")
    hits = i.search(["common"], limit=30, allow=lambda s, seg: s.invoke >= "s140" or s.invoke == "s000")
    assert len(hits) == 11
    assert hits[0].score == pytest.approx(1 / 60)


def test_unicode_words():
    assert build_fts_query("Café_au LAIT") == '"café" OR "au" OR "lait"'


def test_unicode_search(tmp_path):
    i = SkillIndex(tmp_path / "u.db")
    i.replace_segment("global", [_skill("cafe-menu", "Plan a café menu")], "f")
    assert invokes(i.search(["café"])) == ["cafe-menu"]


def test_fresh_database_gets_the_current_schema_version(tmp_path):
    db = tmp_path / "new.db"
    assert SkillIndex(db).count() == 0
    with _db(db) as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == _SCHEMA_VERSION


def test_stale_schema_is_recreated(tmp_path):
    db = tmp_path / "old.db"
    with _db(db) as c:
        c.execute("CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT)")
        c.execute("INSERT INTO meta VALUES ('fingerprint', 'stale')")
        c.execute("CREATE TABLE skills(rowid INTEGER PRIMARY KEY, invoke TEXT UNIQUE)")
        c.execute("CREATE VIRTUAL TABLE fts USING fts5(invoke, content='skills', content_rowid='rowid')")
        c.execute("PRAGMA user_version = 1")
    i = SkillIndex(db)
    assert i.stored_fingerprint() is None
    assert i.count() == 0
    i.replace_segment("global", SKILLS, "new")
    assert i.get("stripe-payments").tags == ()
    assert invokes(i.search(["stripe"])) == ["stripe-payments"]
    with _db(db) as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == _SCHEMA_VERSION


def test_segments_are_independent(tmp_path):
    i = SkillIndex(tmp_path / "seg.db")
    i.replace_segment("global", [_skill("g1", "alpha widget")], "fg")
    i.replace_segment("project:a", [_skill("p1", "beta gadget")], "fa")
    i.replace_segment("project:b", [_skill("p1", "gamma gizmo")], "fb")
    assert (i.stored_fingerprint("global"), i.stored_fingerprint("project:a")) == ("fg", "fa")
    assert i.stored_fingerprint("project:zzz") is None
    i.replace_segment("project:a", [_skill("p2", "delta")], "fa2")
    assert i.stored_fingerprint("project:a") == "fa2" and i.stored_fingerprint("global") == "fg"
    assert [s.invoke for s in i.all("project:a")] == ["p2"]
    assert [s.invoke for s in i.all("project:b")] == ["p1"]
    assert i.count() == 3 and i.count("global") == 1
    assert invokes(i.search(["beta"])) == []
    assert invokes(i.search(["gamma"])) == ["p1"]
    assert i.get("p1", "project:b").description == "gamma gizmo"
    assert i.get("p1") is None


def test_same_invoke_in_two_segments_and_allow_sees_segment(tmp_path):
    i = SkillIndex(tmp_path / "seg.db")
    i.replace_segment("global", [_skill("dup", "common words")], "f")
    i.replace_segment("project:a", [_skill("dup", "common words")], "f")
    assert len(i.search(["common"], allow=lambda s, seg: seg == "global")) == 1
    [hit] = i.search(["common"], allow=lambda s, seg: seg == "project:a")
    assert hit.skill.invoke == "dup"


def test_failed_segment_replace_keeps_other_and_old_rows(tmp_path):
    i = SkillIndex(tmp_path / "seg.db")
    i.replace_segment("global", [_skill("g1", "alpha")], "fg")
    i.replace_segment("project:a", [_skill("p1", "beta")], "fa")
    with pytest.raises(TypeError):
        i.replace_segment("project:a", [_skill("bad", "x", tags=(object(),))], "fx")
    assert [s.invoke for s in i.all("project:a")] == ["p1"]
    assert i.stored_fingerprint("project:a") == "fa"
    assert invokes(i.search(["alpha"])) == ["g1"]


def test_single_ranking_across_segments(tmp_path):
    """A passing mention in a project skill must not outrank real matches in the global segment."""
    i = SkillIndex(tmp_path / "seg.db")
    # Project segment first: rowid order favours it, so only bm25 can demote it.
    i.replace_segment("project:a", [_skill("aa-proj", "Does many other things, mentions alpha in passing")], "fa")
    i.replace_segment("global", [_skill(f"widget-{n}", "alpha widget") for n in range(3)], "fg")
    hits = invokes(i.search(["alpha"]))
    assert hits[-1] == "aa-proj" and set(hits[:3]) == {"widget-0", "widget-1", "widget-2"}


def _seeded(tmp_path, paths):
    i = SkillIndex(tmp_path / "prune.db")
    i.replace_segment("global", [_skill("g1", "alpha")], "fg")
    for seg, path in paths.items():
        i.replace_segment(seg, [_skill(f"{seg[-1]}-skill", "beta")], "f")
        i.touch_segment(seg, path, 1_000_000.0)
    return i


def test_prune_drops_missing_path_keeps_others(tmp_path):
    i = _seeded(tmp_path, {"project:a": "/gone", "project:b": "/here", "project:c": "/here2"})
    dropped = i.prune_segments("project:c", 1_000_100.0, 30, lambda p: p != "/gone")
    assert dropped == ["project:a"]
    assert i.all("project:a") == [] and i.stored_fingerprint("project:a") is None
    assert i.count("project:b") == 1 and i.count("global") == 1


def test_prune_drops_unused_for_30_days_keeps_recent(tmp_path):
    i = _seeded(tmp_path, {"project:a": "/p", "project:b": "/p2"})
    i.touch_segment("project:b", "/p2", 1_000_000.0 + 29 * 86400)
    now = 1_000_000.0 + 31 * 86400
    assert i.prune_segments("project:z", now, 30, lambda p: True) == ["project:a"]
    assert i.count("project:b") == 1


def test_prune_never_drops_current_or_global(tmp_path):
    i = _seeded(tmp_path, {"project:a": "/gone"})
    assert i.prune_segments("project:a", 1_000_000.0 + 99 * 86400, 30, lambda p: False) == []
    assert i.count("project:a") == 1 and i.count("global") == 1
    assert invokes(i.search(["alpha"])) == ["g1"]


def test_touch_writes_only_when_stale(tmp_path):
    i = _seeded(tmp_path, {"project:a": "/p"})
    i.touch_segment("project:a", "/p", 1_000_010.0)  # within an hour: no rewrite
    assert i.prune_segments("x", 1_000_000.0 + 31 * 86400, 30, lambda p: True) == ["project:a"]


def test_same_invoke_in_two_segments_stays_two_candidates(tmp_path):
    i = SkillIndex(tmp_path / "n1.db")
    i.replace_segment("global", [_skill("dup", "common words")], "f")
    i.replace_segment("project:a", [_skill("dup", "common words")], "f")
    hits = i.search(["common"])
    assert len(hits) == 2
    assert all(h.score == pytest.approx(1 / 60) or h.score == pytest.approx(1 / 61) for h in hits)
    assert len(i.visible()) == 2


def test_override_segment_shadows_inside_snapshot(tmp_path):
    i = SkillIndex(tmp_path / "n1.db")
    i.replace_segment("global", [_skill("dup", "common words"), _skill("only-g", "common words")], "f")
    i.replace_segment("project:a", [_skill("dup", "common words proj", plugin="p@m")], "f")
    hits = i.search(["common"], override_segment="project:a")
    assert sorted(h.skill.description for h in hits) == ["common words", "common words proj"]
    assert {h.skill.invoke for h in hits} == {"dup", "only-g"}
    assert [s.source for s in i.visible(override_segment="project:a") if s.invoke == "dup"] == ["plugin"]
    # a shadower rejected by allow does not shadow
    deny = lambda s, seg: s.plugin_id is None
    assert [s.source for s in i.visible(deny, "project:a") if s.invoke == "dup"] == ["user"]
    assert [h.skill.source for h in i.search(["common"], allow=deny, override_segment="project:a") if h.skill.invoke == "dup"] == ["user"]


def test_replace_segment_records_usage_and_metadataless_project_segment_pruned(tmp_path):
    i = SkillIndex(tmp_path / "n2.db")
    i.replace_segment("project:a", [_skill("a1", "x")], "f", path="/p", now=1_000_000.0)
    i.replace_segment("project:b", [_skill("b1", "x")], "f")  # no metadata
    assert i.prune_segments("project:z", 1_000_100.0, 30, lambda p: True) == ["project:b"]
    assert i.count("project:a") == 1 and i.count("project:b") == 0


def test_prune_takes_no_write_lock_when_nothing_stale(tmp_path):
    i = SkillIndex(tmp_path / "n3.db")
    i.replace_segment("project:a", [_skill("a1", "x")], "f", path="/p", now=1_000_000.0)
    checked = []

    def exists(path):
        other = sqlite3.connect(i.db_path, timeout=0.1, isolation_level=None)
        try:
            other.execute("BEGIN IMMEDIATE")  # raises "database is locked" if prune held a write lock
            other.execute("ROLLBACK")
        finally:
            other.close()
        checked.append(path)
        return True

    assert i.prune_segments("project:z", 1_000_100.0, 30, exists) == []
    assert checked == ["/p"]


def test_count_matches_all(tmp_path):
    i = SkillIndex(tmp_path / "c.db")
    i.replace_segment("global", [_skill("a", "x"), _skill("b", "x")], "f")
    i.replace_segment("project:a", [_skill("c", "x")], "f", path="/p")
    assert (i.count(), i.count("global"), i.count("project:a"), i.count("nope")) == (3, 2, 1, 0)


def test_segment_is_the_final_tie_break(tmp_path):
    i = SkillIndex(tmp_path / "tie.db")
    i.replace_segment("project:a", [_skill("dup", "beta")], "f")
    i.replace_segment("global", [_skill("dup", "alpha")], "f")
    for queries in (["beta", "alpha"], ["alpha", "beta"]):
        hits = i.search(queries)
        assert [h.skill.description for h in hits] == ["alpha", "beta"], queries  # "global" < "project:a"


def test_reset_deletes_the_database_and_its_side_files(idx):
    for suffix in ("-wal", "-shm", "-journal"):
        idx.db_path.with_name(idx.db_path.name + suffix).write_bytes(b"x")
    idx.reset()
    assert sorted(p.name for p in idx.db_path.parent.iterdir()) == []
    assert idx.count() == 0  # usable again: a fresh, empty index


def test_is_corruption_only_for_corrupt_files(tmp_path):
    from skillpick.index import is_corruption
    db = tmp_path / "junk.db"
    db.write_bytes(os.urandom(4096))
    with pytest.raises(sqlite3.DatabaseError) as info:
        SkillIndex(db).count()
    assert is_corruption(info.value)
    assert not is_corruption(sqlite3.OperationalError("database is locked"))
    assert not is_corruption(sqlite3.IntegrityError("UNIQUE constraint failed"))


def test_visible_reads_only_the_given_segments_and_decodes_each_row_once(tmp_path, monkeypatch):
    from skillpick import index as index_mod
    i = SkillIndex(tmp_path / "v.db")
    i.replace_segment("global", [_skill("dup", "g"), _skill("g-only", "g")], "f")
    i.replace_segment("project:a", [_skill("dup", "a")], "f")
    i.replace_segment("project:b", [_skill("b-only", "b")], "f")
    decoded = []
    real = index_mod._row_to_skill
    monkeypatch.setattr(index_mod, "_row_to_skill", lambda row: (decoded.append(row[0]), real(row))[1])
    got = i.visible(override_segment="project:a", segments=("global", "project:a"))
    assert [(s.invoke, s.description) for s in got] == [("dup", "a"), ("g-only", "g")]
    assert sorted(decoded) == ["dup", "dup", "g-only"]



def test_terms_that_share_a_token_count_once_for_the_half_terms_floor(tmp_path):
    """The floor counts distinct index tokens: "tests" and "testing" are both the porter token "test"."""
    from skillpick.index import distinct_terms
    assert distinct_terms(["tests", "testing", "zzqq"]) == ["tests", "zzqq"]
    assert distinct_terms(["Café", "cafe", "run", "running"]) == ["Café", "run"]
    i = SkillIndex(tmp_path / "tok.db")
    i.replace_segment("global", [_skill("only-test", "test")], "f")
    assert invokes(i.search(["tests testing zzqq"])) == ["only-test"]  # 1 of 2 distinct tokens: half
    assert i.search(["test zzqq kkww"]) == []  # 1 of 3: below half
    assert i.search(["tests testing zzqq kkww"]) == []  # same 3 tokens: must not pass by double counting
