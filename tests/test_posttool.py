"""PostToolUse hook: after a successful `pick.py save-plan`, show the saved list to the user."""
import ast
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from skillpick.state import Stage, mark_skipped, save_plan

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "posttool.py"
SAVED = 'saved plan: 2 stages\n\nSkills for: "j"\n\nPlanning\n  - a: explore first\n  - b\nBuilding\n  - c\n'
LIST = 'Skills for: "add dark mode"\n\nPlanning\n  - a: explore first\n  - b\nBuilding\n  - c'
STAGES = [Stage("Planning", ["a", "b"], {"a": "explore first"}), Stage("Building", ["c"])]


def command(*extra: str, session: str = "s1") -> str:
    base = (f"python3 \"/x/skill-pick-wizard/scripts/pick.py\" save-plan --session {session} --cwd '/p' "
            "--job 'add dark mode' --plan '[{\"stage\": \"Planning\", \"skills\": [\"a\"]}]'")
    return " ".join([base, *extra])


def payload(cmd: str, stdout: str = SAVED, sid: str = "s1", tool: str = "Bash") -> str:
    return json.dumps({
        "session_id": sid, "hook_event_name": "PostToolUse", "tool_name": tool,
        "tool_input": {"command": cmd},
        "tool_response": {"stdout": stdout, "stderr": "", "interrupted": False, "isImage": False},
    })


def run(stdin, script: Path = SCRIPT):
    data = stdin.encode() if isinstance(stdin, str) else stdin
    r = subprocess.run([sys.executable, str(script)], input=data, capture_output=True, cwd=REPO)
    return r.returncode, r.stdout.decode(), r.stderr.decode()


def saved(env, mode="show", deep_scan=False, sid="s1"):
    save_plan(env.data, sid, "add dark mode", STAGES, mode, deep_scan)


@pytest.mark.parametrize("mode", ["show", "approve"])
def test_shows_the_saved_list_to_the_user(env, mode):
    saved(env, mode)
    code, out, err = run(payload(command("--mode", mode)))
    assert (code, err) == (0, "")
    assert json.loads(out) == {"systemMessage": LIST}


def test_silent_mode_shows_nothing(env):
    saved(env, "silent")
    assert run(payload(command("--mode", "silent"))) == (0, "", "")


def test_deep_scan_is_in_the_header(env):
    saved(env, "show", deep_scan=True)
    out = run(payload(command("--deep-scan")))[1]
    assert json.loads(out)["systemMessage"].startswith('Skills for: "add dark mode" (deep scan)\n\nPlanning\n')


def test_session_comes_from_the_command(env):
    saved(env, sid="from-cmd")
    out = run(payload(command(session="from-cmd"), sid="from-payload"))[1]
    assert json.loads(out) == {"systemMessage": LIST}
    out = run(payload(command(session="x").replace("--session x", "--session=from-cmd"), sid="other"))[1]
    assert json.loads(out) == {"systemMessage": LIST}


def test_session_falls_back_to_the_payload(env):
    saved(env, sid="s7")
    cmd = command().replace("--session s1 ", "")
    assert json.loads(run(payload(cmd, sid="s7"))[1]) == {"systemMessage": LIST}


@pytest.mark.parametrize("cmd", [
    "ls -la",
    'python3 "/x/scripts/pick.py" show-plan --session s1',
    "grep save-plan /x/scripts/pick.py",
    "echo save-plan /x/scripts/pick.py",
    "python3 /x/scripts/other.py save-plan --session s1",
    "python3 /x/scripts/pick.py --help save-plan",
    "python3 '/x/scripts/pick.py save-plan --session s1",  # cannot be split
    "",
])
def test_other_bash_commands_show_nothing(env, cmd):
    saved(env)
    assert run(payload(cmd)) == (0, "", "")


@pytest.mark.parametrize("stdout", ["", "unknown skills: x\n", "usage: pick.py\n", "no plan\n"])
def test_failed_saves_show_nothing(env, stdout):
    saved(env)
    assert run(payload(command(), stdout=stdout)) == (0, "", "")


@pytest.mark.parametrize("raw", [
    b"", b"garbage", b"\xff\xfe\x00{", b"[]", b"null", b'{"tool_name": "Bash"}',
    b'{"tool_name": "Bash", "tool_input": "x", "tool_response": {"stdout": "saved plan: 1"}}',
    b'{"tool_name": "Bash", "tool_input": {"command": 5}, "tool_response": {"stdout": "saved plan: 1"}}',
    b'{"tool_name": "Bash", "tool_input": {"command": "python3 /x/scripts/pick.py save-plan --session s1"},'
    b' "tool_response": "saved plan: 1"}',
])
def test_malformed_payloads_show_nothing(env, raw):
    saved(env)
    assert run(raw) == (0, "", "")


def test_other_tools_show_nothing(env):
    saved(env)
    assert run(payload(command(), tool="Read")) == (0, "", "")


@pytest.mark.parametrize("session", ["../x", "none-saved"])
def test_invalid_or_unknown_session_shows_nothing(env, session):
    saved(env)
    assert run(payload(command(session=session))) == (0, "", "")


def test_skipped_session_shows_nothing(env):
    mark_skipped(env.data, "s1")
    assert run(payload(command())) == (0, "", "")


def test_closed_stdout(env):
    saved(env)
    r = subprocess.run(f"{sys.executable} scripts/posttool.py >&-", shell=True, cwd=REPO,
                       input=payload(command()).encode(), capture_output=True)
    assert r.returncode == 0
    p = subprocess.Popen([sys.executable, str(SCRIPT)], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=REPO)
    p.stdout.close()
    _, err = p.communicate(payload(command()).encode())
    assert (p.returncode, err) == (0, b"")


def test_import_failure_is_logged_and_silent(env, tmp_path):
    scripts = tmp_path / "scripts_copy"
    shutil.copytree(REPO / "scripts", scripts, ignore=shutil.ignore_patterns("__pycache__"))
    (scripts / "skillpick" / "paths.py").write_text("raise ImportError('no paths')\n")
    assert run(payload(command()), scripts / "posttool.py") == (0, "", "")
    log = (env.data / "errors.log").read_text(encoding="utf-8")
    assert "posttool: import failed" in log and "ImportError: no paths" in log


def test_unexpected_error_is_silent(env, monkeypatch, tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv("SKILL_PICK_WIZARD_HOME", str(blocker / "data"))  # data_dir() cannot be created
    assert run(payload(command())) == (0, "", "")


def test_unexpected_error_is_logged(env, monkeypatch):
    sys.path.insert(0, str(REPO / "scripts"))
    import posttool

    def boom(*args, **kwargs):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(posttool, "load_session", boom)
    assert posttool.main(payload(command())) == ""
    log = (env.data / "errors.log").read_text(encoding="utf-8")
    assert "posttool:" in log and "RuntimeError: kaboom" in log


def test_does_not_import_index_or_sqlite3(env):
    saved(env)
    code = (
        "import sys; sys.path.insert(0, 'scripts'); import posttool; "
        f"out = posttool.main({payload(command())!r}); "
        "bad = [m for m in ('skillpick.index', 'sqlite3') if m in sys.modules]; "
        "sys.exit(2 if not out else 1 if bad else 0)"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=REPO)
    assert r.returncode == 0, r.stderr


def test_hooks_json_runs_it_after_save_plan_bash_calls():
    cfg = json.loads((REPO / "hooks" / "hooks.json").read_text())
    [entry] = cfg["hooks"]["PostToolUse"]
    assert entry["matcher"] == "Bash"
    [handler] = entry["hooks"]
    assert handler == {
        "type": "command",
        "if": "Bash(python3 *pick.py* save-plan *)",
        "command": 'python3 "${CLAUDE_PLUGIN_ROOT}/scripts/posttool.py"',
        "timeout": 10,
    }


@pytest.mark.parametrize("name", ["posttool.py", "hookio.py", "skillpick/planview.py"])
def test_parses_as_python_3_9(name):
    ast.parse((REPO / "scripts" / name).read_text(encoding="utf-8"), feature_version=(3, 9))
