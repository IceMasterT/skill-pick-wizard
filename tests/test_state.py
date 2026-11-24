import json
import os
import threading
import time

import pytest

from skillpick import state
from skillpick.state import (
    Config,
    Stage,
    atomic_write_json,
    cleanup_sessions,
    clear_session,
    load_config,
    load_session,
    mark_skipped,
    save_config,
    save_plan,
    valid_session_id,
)


def test_config_defaults_and_roundtrip(env):
    assert load_config(env.data) == Config()
    cfg = Config(mode="show", deep_scan=True)
    save_config(env.data, cfg)
    assert load_config(env.data) == cfg


def test_config_corrupt_or_invalid_values(env):
    env.data.mkdir(parents=True, exist_ok=True)
    path = env.data / "config.json"
    path.write_text("{not json", encoding="utf-8")
    assert load_config(env.data) == Config()
    path.write_text(
        json.dumps({"mode": "loud", "deep_scan": "yes", "deep_scan_model": "opus"}),
        encoding="utf-8",
    )
    cfg = load_config(env.data)
    assert cfg.mode == "approve"
    assert cfg.deep_scan is False
    assert cfg.deep_scan_model == "opus"


@pytest.mark.parametrize("payload", ["[]", "null", "3", '"x"', '{"vault_path": 5, "deep_scan_model": ""}'])
def test_config_wrong_typed_content_gives_defaults(env, payload):
    env.data.mkdir(parents=True, exist_ok=True)
    (env.data / "config.json").write_text(payload, encoding="utf-8")
    assert load_config(env.data) == Config()


def test_config_non_utf8_gives_defaults(env):
    env.data.mkdir(parents=True, exist_ok=True)
    (env.data / "config.json").write_bytes(b"\xff\xfe\x00bad")
    assert load_config(env.data) == Config()


def test_plan_roundtrip(env):
    stages = [Stage("Planning", ["brainstorming"])]
    save_plan(env.data, "abc-1", "add checkout", stages, "approve", False)
    s = load_session(env.data, "abc-1")
    assert s.status == "planned"
    assert s.job == "add checkout"
    assert s.stages == stages
    assert s.mode == "approve"
    assert s.deep_scan is False
    assert s.updated > 0


def test_skip_and_clear(env):
    mark_skipped(env.data, "s1")
    assert load_session(env.data, "s1").status == "skipped"
    clear_session(env.data, "s1")
    assert load_session(env.data, "s1") is None
    clear_session(env.data, "s1")  # idempotent
    clear_session(env.data, "../x")  # invalid id is a no-op


@pytest.mark.parametrize("sid", ["../x", "a/b", "", "x" * 129, "a b"])
def test_invalid_session_ids(env, sid):
    assert valid_session_id(sid) is False
    assert load_session(env.data, sid) is None
    with pytest.raises(ValueError):
        save_plan(env.data, sid, "j", [], "approve", False)
    with pytest.raises(ValueError):
        mark_skipped(env.data, sid)


def test_valid_session_ids():
    assert valid_session_id("abc-1_Z")
    assert valid_session_id("x" * 128)
    assert not valid_session_id("a\n")
    assert not valid_session_id(None)


def test_corrupt_session_file_is_none(env):
    (env.data / "sessions").mkdir(parents=True)
    (env.data / "sessions" / "s1.json").write_text("garbage", encoding="utf-8")
    assert load_session(env.data, "s1") is None


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"status": "weird"},
        {"status": "planned", "stages": "nope"},
        {"status": "planned", "stages": [{"stage": "A"}]},
        {"status": "planned", "stages": [{"stage": "A", "skills": "x"}]},
        {"status": "planned", "stages": [{"stage": "A", "skills": [1]}]},
        {"status": "planned", "stages": [5, None]},
        {"status": "planned", "job": 3, "mode": 4, "deep_scan": "x", "updated": "y"},
    ],
)
def test_wrong_typed_session_never_raises(env, payload):
    (env.data / "sessions").mkdir(parents=True)
    (env.data / "sessions" / "s1.json").write_text(json.dumps(payload), encoding="utf-8")
    s = load_session(env.data, "s1")
    if s is not None:
        assert s.status in ("planned", "skipped")
        assert all(
            isinstance(st.stage, str) and all(isinstance(k, str) for k in st.skills)
            for st in s.stages
        )
        assert isinstance(s.job, str) and isinstance(s.mode, str)
        assert isinstance(s.deep_scan, bool) and isinstance(s.updated, float)


def test_session_keeps_valid_stages_drops_bad(env):
    (env.data / "sessions").mkdir(parents=True)
    payload = {"status": "planned", "stages": [{"stage": "A", "skills": ["x"]}, {"stage": "B"}]}
    (env.data / "sessions" / "s1.json").write_text(json.dumps(payload), encoding="utf-8")
    assert load_session(env.data, "s1").stages == [Stage("A", ["x"])]


def test_atomic_write_leaves_no_temp_and_survives_replace_failure(env, monkeypatch):
    save_config(env.data, Config(mode="show"))
    real = os.replace
    calls = {"n": 0}

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("boom")
        return real(src, dst)

    monkeypatch.setattr(os, "replace", flaky)
    with pytest.raises(OSError):
        save_config(env.data, Config(mode="silent"))
    assert load_config(env.data).mode == "show"
    assert list(env.data.glob("*.tmp")) == []
    save_config(env.data, Config(mode="silent"))
    assert load_config(env.data).mode == "silent"


def test_atomic_write_cleans_temp_on_serialization_failure(env):
    with pytest.raises(TypeError):
        atomic_write_json(env.data / "x.json", {"a": object()})
    assert list(env.data.glob("*.tmp")) == []
    assert not (env.data / "x.json").exists()


def test_concurrent_writers_never_corrupt(env):
    save_plan(env.data, "s1", "first", [Stage("A", ["x"])], "approve", False)
    stop = threading.Event()
    bad = []

    def reader():
        while not stop.is_set():
            if load_session(env.data, "s1") is None:
                bad.append(1)

    def writer(i):
        for j in range(50):
            save_plan(env.data, "s1", f"job-{i}-{j}", [Stage("A", ["x" * 500])], "approve", False)

    r = threading.Thread(target=reader)
    ws = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
    r.start()
    for w in ws:
        w.start()
    for w in ws:
        w.join()
    stop.set()
    r.join()
    assert bad == []
    final = json.loads((env.data / "sessions" / "s1.json").read_text(encoding="utf-8"))
    assert final["status"] == "planned"
    assert list((env.data / "sessions").glob("*.tmp")) == []


def test_cleanup_sessions_by_age(env):
    now = time.time()
    save_plan(env.data, "new", "j", [], "approve", False)
    save_plan(env.data, "old", "j", [], "approve", False)
    old = env.data / "sessions" / "old.json"
    os.utime(old, (now - 20 * 86400, now - 20 * 86400))
    assert cleanup_sessions(env.data, 14, now=now) == 1
    assert not old.exists()
    assert (env.data / "sessions" / "new.json").exists()


def test_cleanup_sessions_without_directory(env):
    assert cleanup_sessions(env.data) == 0
