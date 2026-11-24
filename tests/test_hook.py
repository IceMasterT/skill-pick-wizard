import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from skillpick.hookctx import PLUGIN_COMMANDS, build_context
from skillpick.state import Config, SessionState, Stage

import hook

PICK = 'python3 "/x/pick.py"'
REPO = Path(__file__).resolve().parent.parent


def planned():
    return SessionState(
        status="planned",
        job="build a thing",
        stages=[Stage("Planning", ["brainstorming", "writing-plans"])],
    )


def test_no_session_injects_scan_instruction():
    text = build_context("build x", "s1", Config(), None, PICK)
    assert text.startswith("Skill Pick Wizard - skill scan pending.")
    for part in ("skill scan pending", "session: s1", "mode: approve",
                 "deep scan: off", "skill-pick-wizard:skill-pick", f"run with: {PICK}"):
        assert part in text


def test_deep_scan_on_shows_model():
    cfg = Config(deep_scan=True, deep_scan_model="sonnet")
    assert "deep scan: on (model sonnet)" in build_context("x", "s1", cfg, None, PICK)


def test_planned_session_injects_reminder_only():
    text = build_context("x", "s1", Config(), planned(), PICK)
    assert text.startswith("Skill Pick Wizard - active skill plan for this job:")
    assert "Planning: brainstorming, writing-plans" in text
    assert "scan pending" not in text
    assert "/skill-pick" in text
    assert 'the job "build a thing"' in text


def test_skipped_session_silent():
    assert build_context("x", "s1", Config(), SessionState(status="skipped"), PICK) is None


@pytest.mark.parametrize(
    "prompt",
    ["/autopick add x", "/skill-pick", "/skill-pick-wizard:skill-mode show", "  /skill-deep-scan on"],
)
def test_plugin_commands_silent(prompt):
    assert build_context(prompt, "s1", Config(), None, PICK) is None
    assert build_context(prompt, "s1", Config(), planned(), PICK) is None


def test_plugin_commands_constant():
    assert PLUGIN_COMMANDS == ("autopick", "skill-pick", "skill-mode", "skill-deep-scan")


def test_hook_main_outputs_valid_json(env):
    out = json.loads(hook.main('{"session_id":"s1","prompt":"build a thing"}'))
    assert out["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert "session: s1" in out["hookSpecificOutput"]["additionalContext"]


@pytest.mark.parametrize(
    "stdin",
    [
        "", "not json", "[]", '{"prompt":"x"}', '{"session_id":"../../etc","prompt":"x"}',
        '{"session_id":5,"prompt":"x"}', '{"session_id":"s1","prompt":5}',
        '{"session_id":"s1"}', "null", '"str"', "\x00\x01", "[" * 100000,
    ],
)
def test_hook_never_fails(env, stdin):
    assert hook.main(stdin) == ""


def test_hook_corrupt_config_uses_defaults(env):
    env.data.mkdir(parents=True, exist_ok=True)
    (env.data / "config.json").write_text("{garbage", encoding="utf-8")
    ctx = json.loads(hook.main('{"session_id":"s1","prompt":"x"}'))["hookSpecificOutput"]["additionalContext"]
    assert "mode: approve" in ctx


def test_hook_does_not_touch_index(env, monkeypatch):
    import skillpick.index

    def boom(*a, **k):
        raise AssertionError("index touched")

    monkeypatch.setattr(skillpick.index.SkillIndex, "__init__", boom)
    assert "scan pending" in hook.main('{"session_id":"s1","prompt":"x"}')


def test_hook_errors_logged_not_raised(env, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(hook, "load_config", boom)
    assert hook.main('{"session_id":"s1","prompt":"x"}') == ""
    assert "kaboom" in (env.data / "errors.log").read_text(encoding="utf-8")


def test_cleanup_runs_at_most_once_per_day(env, monkeypatch):
    calls = []
    monkeypatch.setattr(hook, "cleanup_sessions", lambda d: calls.append(d))
    payload = '{"session_id":"s1","prompt":"x"}'
    hook.main(payload)
    hook.main(payload)
    assert len(calls) == 1
    marker = env.data / ".last-cleanup"
    assert marker.exists()
    old = time.time() - 2 * 86400
    import os
    os.utime(marker, (old, old))
    hook.main(payload)
    assert len(calls) == 2


def test_cleanup_failure_does_not_affect_output(env, monkeypatch):
    def boom(d):
        raise OSError("nope")

    monkeypatch.setattr(hook, "cleanup_sessions", boom)
    assert "scan pending" in hook.main('{"session_id":"s1","prompt":"x"}')


def test_planned_session_end_to_end(env):
    from skillpick.state import save_plan
    save_plan(env.data, "s1", "job", [Stage("Planning", ["a"])], "approve", False)
    ctx = json.loads(hook.main('{"session_id":"s1","prompt":"x"}'))["hookSpecificOutput"]["additionalContext"]
    assert "Planning: a" in ctx


def test_hook_script_subprocess_exit_zero(env):
    r = subprocess.run([sys.executable, "scripts/hook.py"], input="garbage",
                       capture_output=True, text=True, cwd=REPO)
    assert r.returncode == 0 and r.stdout == ""


def test_hook_script_subprocess_binary_stdin(env):
    r = subprocess.run([sys.executable, "scripts/hook.py"], input=b"\xff\xfe\x00{",
                       capture_output=True, cwd=REPO)
    assert r.returncode == 0 and r.stdout == b""


def test_hook_script_subprocess_valid(env):
    r = subprocess.run([sys.executable, "scripts/hook.py"],
                       input='{"session_id":"s1","prompt":"build"}',
                       capture_output=True, text=True, cwd=REPO)
    assert r.returncode == 0
    assert json.loads(r.stdout)["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"


def test_hooks_json():
    cfg = json.loads((REPO / "hooks" / "hooks.json").read_text())
    h = cfg["hooks"]["UserPromptSubmit"][0]["hooks"][0]
    assert h["command"] == 'python3 "${CLAUDE_PLUGIN_ROOT}/scripts/hook.py"'
    assert h["timeout"] == 10


def test_hook_fast(env):
    payload = '{"session_id":"s1","prompt":"build a thing"}'
    hook.main(payload)
    t = time.perf_counter()
    hook.main(payload)
    assert time.perf_counter() - t < 0.2
