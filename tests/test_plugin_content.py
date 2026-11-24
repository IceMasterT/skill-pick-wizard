"""Static checks on the plugin's markdown: the skill, the slash commands and the agent."""
import json
import re
import shlex
from pathlib import Path

import pytest

import pick
from skillpick.frontmatter import parse_frontmatter
from skillpick.hookctx import PLUGIN_COMMANDS, build_context
from skillpick.state import Config

REPO = Path(__file__).resolve().parent.parent
SKILL_DIR = REPO / "skills" / "pick-skills"
SKILL = SKILL_DIR / "SKILL.md"
APPROVE = SKILL_DIR / "approve.md"
AGENT = REPO / "agents" / "skill-deep-scanner.md"
COMMANDS = REPO / "commands"
APPROVAL_OPTIONS = ("Approve and start", "Swap one", "Add one", "Skip for this session")
PICK_CMD = 'python3 "${CLAUDE_PLUGIN_ROOT}/scripts/pick.py"'
MAX_HEADER = 12  # AskUserQuestion's header limit
MAX_OPTIONS = 4  # AskUserQuestion allows 2 to 4 options plus its automatic "Other"

# `pick.py <sub>` (optionally after a closing quote) and the skill's `<pick> <sub>` placeholder form.
_SUBCOMMAND_RE = re.compile(r"""(?:pick\.py["']?|<pick>)[ \t]+([A-Za-z][\w-]*)""")
# The start of a runnable command line: the skill's placeholder or the commands' full CLI path.
_COMMAND_START_RE = re.compile(r"""^(?:<pick>|python3 "\$\{CLAUDE_PLUGIN_ROOT\}/scripts/pick\.py")[ \t]+""")
_QUESTION_RE = re.compile(r"^\*\*Ask\*\* `([^`]+)` \(header `([^`]+)`\)")
_FIXED_OPTION_RE = re.compile(r"^- `([^`]+)`: \S")
_DYNAMIC_OPTION_RE = re.compile(r"^- Up to (\d+) \S")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _flow_files() -> list[Path]:
    """Every markdown file whose text Claude follows to run pick.py: the skill folder and the commands."""
    return sorted(SKILL_DIR.rglob("*.md")) + sorted(COMMANDS.glob("*.md"))


def _subcommands() -> set[str]:
    parser = pick.build_parser()
    sub = next(a for a in parser._actions if a.dest == "command")
    return set(sub.choices)


def _plugin_name() -> str:
    return json.loads(_read(REPO / ".claude-plugin" / "plugin.json"))["name"]


def _command_lines(text: str) -> list[str]:
    """pick.py command lines (without the program part) from code spans and fenced code blocks."""
    found = []
    in_fence = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        spans = [line.strip()] if in_fence else re.findall(r"`([^`]*)`", line)
        for span in spans:
            match = _COMMAND_START_RE.match(span)
            if match:
                found.append(span[match.end():])
    return found


def _fill(command: str, arguments: str = "") -> list[str]:
    """Replace placeholders with dummy values and split like a POSIX shell would."""
    command = command.replace("<mode used>", "approve").replace("$ARGUMENTS", arguments)
    return shlex.split(re.sub(r"<[^<>]+>", "x", command))


def _parse(argv: list[str]):
    try:
        return pick.build_parser().parse_args(argv)
    except SystemExit:
        pytest.fail(f"pick.py rejects: {argv}")


def _session_subcommands() -> set[str]:
    match = re.search(r"add `--session <that id>` to every (.+?) command", _read(SKILL))
    assert match, "SKILL.md must say which commands get --session"
    return set(re.findall(r"`([^`]+)`", match.group(1)))


def _questions(text: str) -> list[tuple[str, str, list[str]]]:
    """(question, header, option lines) for each `**Ask** ...` block; options are the `- ` lines below it."""
    lines = [line.strip() for line in text.splitlines()]  # questions may sit inside numbered steps
    out = []
    for i, line in enumerate(lines):
        match = _QUESTION_RE.match(line)
        if not match:
            continue
        options = []
        for follow in lines[i + 1:]:
            if not follow.startswith("- "):
                break
            options.append(follow)
        out.append((match.group(1), match.group(2), options))
    return out


def test_expected_files_exist():
    assert SKILL.is_file()
    assert APPROVE.is_file()
    assert AGENT.is_file()
    for name in ("skill-mode", "skill-deep-scan", "autopick", "skill-pick"):
        assert (COMMANDS / f"{name}.md").is_file(), name


def test_all_markdown_frontmatter_parses():
    files = sorted((REPO / "skills").rglob("SKILL.md"))
    files += sorted(COMMANDS.glob("*.md")) + sorted((REPO / "agents").glob("*.md"))
    assert len(files) >= 6
    for path in files:
        fm = parse_frontmatter(_read(path))
        desc = fm.get("description")
        assert isinstance(desc, str) and desc.strip(), path
        folder = path.relative_to(REPO).parts[0]
        if folder in ("skills", "agents"):
            assert isinstance(fm.get("name"), str) and fm["name"].strip(), path
        if folder == "commands":
            hint = fm.get("argument-hint")
            assert isinstance(hint, str) and hint.strip(), path


def test_skill_support_files_are_referenced_and_have_no_frontmatter():
    """Files beside SKILL.md are read on demand, so SKILL.md must name them and they are not skills."""
    skill_text = _read(SKILL)
    support = [p for p in SKILL_DIR.rglob("*.md") if p != SKILL]
    assert support
    for path in support:
        assert f"<base directory>/{path.relative_to(SKILL_DIR).as_posix()}" in skill_text, path
        assert parse_frontmatter(_read(path)) == {}, path


def test_skill_name_matches_its_directory():
    assert parse_frontmatter(_read(SKILL))["name"] == SKILL.parent.name == "pick-skills"


def test_skill_description_states_its_triggers():
    desc = parse_frontmatter(_read(SKILL))["description"]
    assert desc.startswith(
        "Use when the Skill Pick Wizard hook says a skill scan is pending, "
        "or when /autopick or /skill-pick runs"
    )


def test_commands_reference_real_subcommands():
    known = _subcommands()
    found = 0
    for path in _flow_files():
        for word in _SUBCOMMAND_RE.findall(_read(path)):
            assert word in known, f"{path.name}: pick.py {word}"
            found += 1
    assert found


def test_every_command_mention_is_a_parseable_code_line():
    """Each `<pick> <sub>` / `pick.py <sub>` mention sits in a code span or block the parser test covers."""
    for path in _flow_files():
        text = _read(path)
        assert len(_command_lines(text)) == len(_SUBCOMMAND_RE.findall(text)), path.name


def test_every_command_line_parses_with_the_real_parser():
    session_subs = _session_subcommands()
    checked = 0
    for path in _flow_files():
        fm = parse_frontmatter(_read(path))
        hint = fm.get("argument-hint", "") if path.parent == COMMANDS else ""
        for command in _command_lines(_read(path)):
            if "$ARGUMENTS" in command:
                values = [""] + re.findall(r"\w[\w-]*", hint)
            else:
                values = [""]
            for value in values:
                argv = _fill(command, value)
                args = _parse(argv)
                checked += 1
                if path.parent == SKILL_DIR and args.command in session_subs:
                    assert _parse(argv + ["--session", "s1"]).session == "s1", command
                if args.command == "save-plan":
                    assert _parse(argv + ["--deep-scan"]).deep_scan is True
                if args.command == "search":
                    assert args.queries and all(q == "x" for q in args.queries), command
    assert checked >= 10


def test_skill_uses_every_flow_subcommand():
    used = set()
    for path in SKILL_DIR.rglob("*.md"):
        used |= {_fill(c)[0] for c in _command_lines(_read(path))}
    assert {"status", "search", "catalog", "save-plan", "skip"} <= used


def test_session_rule_names_commands_the_skill_runs_that_accept_session():
    session_subs = _session_subcommands()
    assert session_subs == {"status", "catalog", "save-plan", "skip"}
    for sub in session_subs:
        extra = ["--job", "j", "--plan", "[]"] if sub == "save-plan" else []
        assert _parse([sub, "--session", "s1"] + extra).session == "s1"


def test_skill_plan_json_example_is_accepted_by_save_plan():
    blocks = re.findall(r"```\n\s*(\[\{.*?\}\])\n\s*```", _read(SKILL), re.S)
    assert blocks
    for block in blocks:
        stages = pick._parse_plan(block)
        assert stages and all(st.skills for st in stages)


@pytest.mark.parametrize(
    "name, line",
    [
        ("skill-mode", f"{PICK_CMD} mode $ARGUMENTS"),
        ("skill-deep-scan", f"{PICK_CMD} deep-scan $ARGUMENTS"),
        ("autopick", f"{PICK_CMD} reset"),
        ("skill-pick", f"{PICK_CMD} reset"),
    ],
)
def test_command_runs_the_plugin_cli(name, line):
    assert line in _read(COMMANDS / f"{name}.md")


def test_flow_commands_follow_the_skill():
    for name in ("autopick", "skill-pick"):
        text = _read(COMMANDS / f"{name}.md")
        assert "skill-pick-wizard:pick-skills" in text, name
        assert "$ARGUMENTS" in text, name
    assert "--mode silent" in _read(COMMANDS / "autopick.md")


def test_command_files_match_hook_command_list():
    assert {p.stem for p in COMMANDS.glob("*.md")} == set(PLUGIN_COMMANDS)


def test_skill_lists_exact_approval_options():
    questions = _questions(_read(APPROVE))
    main = [q for q in questions if q[0] == "Use these skills for this job?"]
    assert len(main) == 1
    labels = [_FIXED_OPTION_RE.match(o).group(1) for o in main[0][2]]
    assert labels == list(APPROVAL_OPTIONS)


def test_every_question_fits_ask_user_question_limits():
    questions = _questions(_read(APPROVE))
    assert len(questions) >= 6
    for question, header, options in questions:
        assert 0 < len(header) <= MAX_HEADER, question
        assert options, question
        most = 0
        for option in options:
            fixed, dynamic = _FIXED_OPTION_RE.match(option), _DYNAMIC_OPTION_RE.match(option)
            assert fixed or dynamic, f"{question}: unparseable option line {option!r}"
            most += int(dynamic.group(1)) if dynamic else 1
        assert most <= MAX_OPTIONS, question
    # Dynamic option lists can shrink below 2, so the file must say what happens then.
    assert "fewer than 2 options" in _read(APPROVE)


def test_approve_mode_is_read_only_in_approve_mode():
    skill_text = _read(SKILL)
    assert "AskUserQuestion" not in skill_text
    assert "AskUserQuestion" in _read(APPROVE)
    assert re.search(r"\*\*approve:\*\*.*Read `<base directory>/approve\.md`", skill_text)


def test_approve_flow_handles_declined_and_unavailable_question_differently():
    text = _read(APPROVE)
    assert "declines or dismisses" in text
    assert "not available" in text


def test_skill_pins_deep_scan_and_save_fallbacks():
    text = _read(SKILL)
    assert "If every agent failed" in text
    assert "reason line" in text
    assert "same message" in text
    assert re.search(r"any other error.*<pick> skip", text, re.I | re.S)


def test_skill_shows_the_spec_list_header():
    text = _read(SKILL)
    assert 'Skills for: "<job summary>"' in text
    assert "(deep scan)" in text


def test_skill_references_agent_by_plugin_name():
    agent_fm = parse_frontmatter(_read(AGENT))
    assert f"{_plugin_name()}:{agent_fm['name']}" == "skill-pick-wizard:skill-deep-scanner"
    assert "skill-pick-wizard:skill-deep-scanner" in _read(SKILL)
    assert agent_fm.get("tools") == "Read"


def test_hook_wording_matches_skill_name():
    text = build_context("build x", "s1", Config(), None, 'python3 "/x/pick.py"')
    match = re.search(r"Invoke the (\S+) skill", text)
    assert match
    assert match.group(1) == f"{_plugin_name()}:" + parse_frontmatter(_read(SKILL))["name"]


def _section(text: str, start: str, end: str | None = None) -> str:
    """Text from the first `start` up to the next `end` after it (or to the end of the text)."""
    i = text.index(start)
    j = text.index(end, i + len(start)) if end else len(text)
    return text[i:j]


def test_cli_error_prefixes_the_save_step_handles(env, capsys):
    """The save step reacts to these exact stderr prefixes, so pin them against the real CLI."""
    base = ["save-plan", "--session", "s1", "--cwd", str(env.cwd), "--job", "j"]
    assert pick.main(base + ["--plan", '{"stage": "x"}']) == 2
    assert capsys.readouterr().err.startswith("invalid --plan")
    assert pick.main(base + ["--plan", '[{"stage": "A", "skills": ["no-such-skill"]}]']) == 2
    assert capsys.readouterr().err.startswith("unknown skills: no-such-skill")
    save = _section(_read(SKILL), "## 6. Save", "## 7.")
    assert "`invalid --plan`" in save and "`unknown skills: <list>`" in save


def test_fixable_save_errors_are_retried_before_any_skip():
    save = _section(_read(SKILL), "## 6. Save", "## 7.")
    first_skip_after_save = save.index("<pick> skip", save.index("<pick> save-plan"))
    for error in ("`unknown skills: <list>`", "`invalid --plan`"):
        at = save.index(error)
        retry = save.index("`save-plan` again", at)
        assert at < retry < first_skip_after_save, error
    assert "at most twice" in save


def test_unsaved_plan_path_is_explicit():
    save = _section(_read(SKILL), "## 6. Save", "## 7.")
    assert "/skill-pick to try again" in save  # reminders will not appear; how to retry
    assert "If `<pick> skip` fails too" in save
    assert re.search(r"In silent mode, also show the list once", save)
    start = _section(_read(SKILL), "## 7.")
    assert "On later messages the hook shows the plan as a reminder" not in start
    assert "If the plan was saved" in start and "not saved" in start


def test_approve_handoff_names_all_three_endings():
    bullet = next(l for l in _read(SKILL).splitlines() if l.startswith("- **approve:**"))
    assert "step 6" in bullet
    assert "without a plan" in bullet
    assert re.search(r"declined.*save nothing.*start nothing.*wait", bullet)
    assert "three" in _section(_read(APPROVE), "After every swap", "## Swap")


def test_swap_can_drop_a_skill():
    approve = _read(APPROVE)
    main = next(q for q in _questions(approve) if q[0] == "Use these skills for this job?")
    assert "drop" in next(o for o in main[2] if "`Swap one`" in o)
    replace = _section(approve, "**Ask** `Replace <invoke> with?`", "\n4. ")
    assert re.search(r'"Other".*(remove|drop)', replace, re.S)
    assert replace.count("drop") >= 2  # the Other rule and the no-alternatives notice


def test_which_stage_handles_typed_text():
    stage = _section(_read(APPROVE), "**Ask** `Which stage?`", "\n2. ")
    assert '"Other"' in stage and "any stage" in stage


def test_add_respects_the_pick_limits():
    add = _section(_read(APPROVE), "## Add", "## Typed text")
    assert "8 skills" in add and "4 skills" in add and "Which skill should be replaced?" in add
    other = _section(_read(APPROVE), '**Text typed in "Other":**', "After every swap")
    assert "SKILL.md step 4.1" in other


def test_skill_name_does_not_collide_with_a_command():
    # A command and a skill with the same name share one invoke name (plugin:name); the Skill tool then
    # launches the command body instead of the skill, so the flow never loads.
    commands = {p.stem for p in COMMANDS.glob("*.md")}
    skills = {p.name for p in (REPO / "skills").iterdir() if p.is_dir()}
    assert commands.isdisjoint(skills), commands & skills


def test_query_guidance_caps_queries_and_asks_for_descriptive_wording():
    stages = _section(_read(SKILL), "## 1. Stages", "## 2.")
    assert "at most 6 queries" in stages
    assert "1 or 2 per stage" in stages
    assert "the way a skill description would" in stages
    for bare in ("`security`", "`review`", "`testing`"):
        assert bare in stages  # named as the bare category words to avoid
    search = _section(_read(SKILL), "## 2. Search", "## 3.")
    assert "broader words" not in search  # the retry must not fall back to bare category words
    assert "planning`, `testing`" not in search


def test_failed_status_or_search_skips_and_does_the_job():
    text = _read(SKILL)
    rule = re.search(r"If `status` or `search` fails.*?\n\n", text, re.S)
    assert rule, "SKILL.md must say what to do when status or search fails"
    assert "`Skill scan failed (<error>).`" in rule.group(0)
    assert "`<pick> skip`" in rule.group(0) and "do the job without a plan" in rule.group(0)
