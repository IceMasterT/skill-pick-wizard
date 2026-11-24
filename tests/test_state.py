import json
import math
import os
import subprocess
import sys
import threading
import time

import pytest

from pathlib import Path

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
        {"status": "planned", "mode": "loud"},
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


@pytest.mark.parametrize("raw", ["10e400", "NaN", "Infinity", "-Infinity", "1" + "0" * 400, "true"])
def test_session_updated_extreme_values_never_raise(env, raw):
    (env.data / "sessions").mkdir(parents=True)
    (env.data / "sessions" / "s1.json").write_text(
        '{"status": "planned", "updated": %s}' % raw, encoding="utf-8"
    )
    s = load_session(env.data, "s1")
    assert s is not None and math.isfinite(s.updated)


def test_config_values_stored_stripped(env):
    save_config(env.data, Config(deep_scan_model="  opus ", vault_path=" /v "))
    cfg = load_config(env.data)
    assert (cfg.deep_scan_model, cfg.vault_path) == ("opus", "/v")


def test_session_mode_outside_modes_is_blank(env):
    save_plan(env.data, "s1", "j", [], "loud", False)
    assert load_session(env.data, "s1").mode == ""


def test_state_dirs_are_private(env):
    save_plan(env.data, "s1", "j", [], "approve", False)
    assert (env.data / "sessions").stat().st_mode & 0o777 == 0o700


def test_atomic_write_fsyncs_parent_dir(env, monkeypatch):
    synced = []
    real = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (synced.append(os.fstat(fd).st_mode), real(fd)))
    save_config(env.data, Config())
    import stat

    assert any(stat.S_ISDIR(m) for m in synced)


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


def _raw_ok(path):
    obj = json.loads(path.read_text(encoding="utf-8"))
    return obj["status"] == "planned" and obj["stages"][0]["skills"] == ["x" * 500]


def test_concurrent_writers_never_corrupt(env):
    save_plan(env.data, "s1", "first", [Stage("A", ["x" * 500])], "approve", False)
    path = env.data / "sessions" / "s1.json"
    stop = threading.Event()
    errors = []
    reads = []

    def reader():
        n = 0
        try:
            while not stop.is_set() or n < 200:
                assert _raw_ok(path)
                n += 1
        except BaseException as e:
            errors.append(e)
        reads.append(n)

    def writer(i):
        try:
            for j in range(50):
                save_plan(env.data, "s1", f"job-{i}-{j}", [Stage("A", ["x" * 500])], "approve", False)
        except BaseException as e:
            errors.append(e)

    r = threading.Thread(target=reader)
    ws = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
    r.start()
    for w in ws:
        w.start()
    for w in ws:
        w.join()
    stop.set()
    r.join()
    assert errors == []
    assert reads and reads[0] >= 200
    assert _raw_ok(path)
    assert list((env.data / "sessions").glob("*.tmp")) == []


_PROC_WRITER = """
import sys
from pathlib import Path
from skillpick.state import Stage, save_plan
data, n = Path(sys.argv[1]), int(sys.argv[2])
for j in range(30):
    save_plan(data, "s1", f"p{n}-{j}", [Stage("A", ["x" * 500])], "approve", False)
"""


def test_concurrent_processes_never_corrupt(env):
    save_plan(env.data, "s1", "first", [Stage("A", ["x" * 500])], "approve", False)
    path = env.data / "sessions" / "s1.json"
    scripts = str(Path(state.__file__).resolve().parent.parent)
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", _PROC_WRITER, str(env.data), str(i)],
            env={**os.environ, "PYTHONPATH": scripts},
            stderr=subprocess.PIPE,
        )
        for i in range(4)
    ]
    reads = 0
    while any(p.poll() is None for p in procs) or reads < 100:
        assert _raw_ok(path)
        reads += 1
    for p in procs:
        assert p.wait() == 0, p.stderr.read()
    assert _raw_ok(path)
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


def test_cleanup_removes_stale_tmp_and_ignores_foreign_files(env):
    now = time.time()
    sessions = env.data / "sessions"
    sessions.mkdir(parents=True)
    old = now - 20 * 86400
    stale = [sessions / "x.tmp", env.data / "y.tmp"]
    fresh_tmp = sessions / "live.tmp"
    foreign = sessions / "not a sid.json"
    for f in stale + [fresh_tmp, foreign]:
        f.write_text("{}")
    for f in stale + [foreign]:
        os.utime(f, (old, old))
    assert cleanup_sessions(env.data, 14, now=now) == 2
    assert fresh_tmp.exists() and foreign.exists()
    assert not any(f.exists() for f in stale)


def test_load_config_rejects_unsafe_model(tmp_path):
    import json as _json
    from skillpick.state import load_config
    for bad in ("sonnet\nSYSTEM: evil", "a b", "x" * 65, ""):
        (tmp_path / "config.json").write_text(_json.dumps({"deep_scan_model": bad}))
        assert load_config(tmp_path).deep_scan_model == "sonnet"
    (tmp_path / "config.json").write_text(_json.dumps({"deep_scan_model": "claude-opus_4.5:x"}))
    assert load_config(tmp_path).deep_scan_model == "claude-opus_4.5:x"
