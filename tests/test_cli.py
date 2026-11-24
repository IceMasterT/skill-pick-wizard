import json

import pick
import pytest
from conftest import make_skill
from skillpick.state import Config, load_session, save_config


@pytest.fixture(autouse=True)
def isolated_vault(env):
    """Point the vault at a nonexistent dir so tests never read the real default vault."""
    save_config(env.data, Config(vault_path=str(env.data.parent / "no-vault")))


def run(capsys, *argv):
    code = pick.main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def seed(env):
    make_skill(env.claude_home / "skills", "brainstorming", "description: explore ideas before building")
    make_skill(
        env.claude_home / "skills", "test-driven-development",
        "description: write the failing test first then implement",
    )


PLAN = json.dumps([{"stage": "Planning", "skills": ["brainstorming"]}])


def test_mode_get_set_and_invalid(env, capsys):
    assert run(capsys, "mode") == (0, "mode: approve\n", "")
    assert run(capsys, "mode", "show") == (0, "mode: show\n", "")
    assert run(capsys, "mode")[1] == "mode: show\n"
    code, out, err = run(capsys, "mode", "loud")
    assert code == 2 and out == ""


def test_deep_scan_toggle(env, capsys):
    assert run(capsys, "deep-scan")[1] == "deep_scan: off\n"
    assert run(capsys, "deep-scan", "on") == (0, "deep_scan: on\n", "")
    code, out, _ = run(capsys, "status")
    assert code == 0
    assert "deep_scan: on\n" in out and "deep_scan_model: sonnet\n" in out and "mode: approve\n" in out
    assert "session: none" in out
    assert run(capsys, "deep-scan", "off")[1] == "deep_scan: off\n"


def test_save_plan_rejects_unknown_skills(env, capsys):
    seed(env)
    plan = json.dumps([{"stage": "Planning", "skills": ["brainstorming", "nope", "nada"]}])
    code, out, err = run(capsys, "save-plan", "--session", "s1", "--job", "j", "--plan", plan)
    assert code == 2 and out == ""
    assert err == "unknown skills: nope, nada\n"
    assert not (env.data / "sessions" / "s1.json").exists()


def test_save_plan_then_status_and_show_plan(env, capsys):
    seed(env)
    code, out, err = run(capsys, "save-plan", "--session", "s1", "--job", "build it", "--plan", PLAN,
                         "--cwd", str(env.cwd))
    assert code == 0 and err == ""
    state = load_session(env.data, "s1")
    assert state.status == "planned" and state.job == "build it" and state.mode == "approve"
    code, out, _ = run(capsys, "status", "--session", "s1")
    assert "session: planned" in out and "Planning: brainstorming" in out
    code, out, _ = run(capsys, "show-plan", "--session", "s1")
    assert code == 0 and "Planning: brainstorming" in out


def test_save_plan_mode_and_deep_scan_flags(env, capsys):
    seed(env)
    run(capsys, "mode", "silent")
    run(capsys, "save-plan", "--session", "s1", "--job", "j", "--plan", PLAN, "--cwd", str(env.cwd))
    assert load_session(env.data, "s1").mode == "silent"
    run(capsys, "save-plan", "--session", "s2", "--job", "j", "--plan", PLAN, "--mode", "show",
        "--deep-scan", "--cwd", str(env.cwd))
    s2 = load_session(env.data, "s2")
    assert s2.mode == "show" and s2.deep_scan is True


def test_session_from_env(env, capsys, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "s9")
    assert run(capsys, "skip")[0] == 0
    assert load_session(env.data, "s9").status == "skipped"
    assert run(capsys, "reset")[0] == 0
    assert load_session(env.data, "s9") is None


def test_no_session_id_errors(env, capsys, monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    code, out, err = run(capsys, "skip")
    assert code == 2 and err == "no session id\n"
    assert run(capsys, "show-plan")[0] == 2


def test_invalid_session_id(env, capsys):
    code, _, err = run(capsys, "skip", "--session", "../x")
    assert code == 2 and err == "invalid session id\n"


def test_show_plan_without_plan(env, capsys):
    code, out, _ = run(capsys, "show-plan", "--session", "none-yet")
    assert code == 0 and out == "no plan\n"


def test_search_output_format(env, capsys):
    seed(env)
    code, out, _ = run(capsys, "search", "--cwd", str(env.cwd), "failing test first", "implement")
    assert code == 0
    lines = out.splitlines()
    assert lines[0].startswith("`test-driven-development`  []  (matched: ")
    assert "failing test first" in lines[0]
    assert lines[1] == "    write the failing test first then implement"


def test_search_limit_and_no_matches(env, capsys):
    seed(env)
    assert run(capsys, "search", "--cwd", str(env.cwd), "zzzzqq")[1] == "no matches\n"
    code, out, _ = run(capsys, "search", "--cwd", str(env.cwd), "--limit", "1", "test explore")
    assert out.count("`") == 2


def test_save_plan_bad_json(env, capsys):
    code, out, err = run(capsys, "save-plan", "--session", "s1", "--job", "j", "--plan", "{")
    assert code == 2 and err == "invalid --plan JSON\n"


def test_save_plan_bad_shape(env, capsys):
    for bad in ('{"a": 1}', "[1]", '[{"stage": "x"}]', '[{"stage": "x", "skills": [1]}]', "[]"):
        code, _, err = run(capsys, "save-plan", "--session", "s1", "--job", "j", "--plan", bad)
        assert code == 2 and err.startswith("invalid --plan") and err.count("\n") == 1


def test_index_forces_rebuild(env, capsys):
    seed(env)
    make_skill(env.cwd / ".claude" / "skills", "p1", "description: project one")
    code, out, _ = run(capsys, "index", "--cwd", str(env.cwd))
    assert (code, out) == (0, "global: 2 skills, project: 1 skills\nsearchable: 3\n")


def test_build_parser_exposes_subcommands():
    parser = pick.build_parser()
    sub = next(a for a in parser._actions if a.dest == "command")
    assert {"status", "search", "save-plan", "skip", "reset", "show-plan", "mode", "deep-scan", "index"} <= set(sub.choices)


def test_limit_must_be_positive(env, capsys):
    for bad in ("0", "-3", "x"):
        code, out, err = run(capsys, "search", "--limit", bad, "q")
        assert code == 2 and out == "" and err.count("\n") == 1


def test_missing_cwd_exits_2_and_creates_nothing(env, capsys):
    ghost = env.data.parent / "ghost"
    cmds = (
        ["search", "--cwd", str(ghost), "q"],
        ["index", "--cwd", str(ghost)],
        ["save-plan", "--cwd", str(ghost), "--session", "s1", "--job", "j", "--plan", PLAN],
    )
    for argv in cmds:
        code, out, err = run(capsys, *argv)
        assert code == 2 and err == f"no such directory: {ghost}\n"
    assert not ghost.exists() and not (env.data / "index").exists()


def test_argparse_error_is_one_line(env, capsys):
    for argv in (["bogus"], [], ["mode", "loud"], ["save-plan"]):
        code, out, err = run(capsys, *argv)
        assert code == 2 and out == "" and err.count("\n") == 1 and "Traceback" not in err


def test_status_invalid_session_id(env, capsys):
    code, out, err = run(capsys, "status", "--session", "../x")
    assert code == 2 and err == "invalid session id\n"


def test_save_plan_rejects_empty_stage_or_skills(env, capsys):
    for bad in ('[{"stage": "", "skills": ["a"]}]', '[{"stage": "  ", "skills": ["a"]}]',
                '[{"stage": "x", "skills": []}]', '[{"stage": "x", "skills": [""]}]'):
        code, _, err = run(capsys, "save-plan", "--session", "s1", "--job", "j", "--plan", bad)
        assert code == 2 and err.startswith("invalid --plan") and err.count("\n") == 1


def test_pick_rejects_old_python_before_importing_skillpick(tmp_path):
    import shutil
    import subprocess
    import sys
    from pathlib import Path
    scripts = tmp_path / "s"
    shutil.copytree(Path(__file__).resolve().parent.parent / "scripts", scripts,
                    ignore=shutil.ignore_patterns("__pycache__"))
    (scripts / "skillpick" / "__init__.py").write_text("raise RuntimeError('imported')\n")
    src = (scripts / "pick.py").read_text().replace("(3, 10)", "(99, 0)", 1)
    (scripts / "pick.py").write_text(src)
    r = subprocess.run([sys.executable, str(scripts / "pick.py"), "--help"],
                       capture_output=True, text=True)
    assert r.returncode == 2
    assert "skill-pick-wizard needs Python 3.10 or newer" in r.stderr
    assert "imported" not in r.stderr
