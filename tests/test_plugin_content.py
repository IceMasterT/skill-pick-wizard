"""Static checks on the plugin's markdown: the skill, the slash commands and the agent."""
import json
import re
from pathlib import Path

import pytest

import pick
from skillpick.frontmatter import parse_frontmatter
from skillpick.hookctx import PLUGIN_COMMANDS, build_context
from skillpick.state import Config

REPO = Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "skill-pick" / "SKILL.md"
AGENT = REPO / "agents" / "skill-deep-scanner.md"
COMMANDS = REPO / "commands"
APPROVAL_OPTIONS = ("Approve and start", "Swap one", "Add one", "Skip for this session")
PICK_CMD = 'python3 "${CLAUDE_PLUGIN_ROOT}/scripts/pick.py"'

# `pick.py <sub>` (optionally after a closing quote) and the skill's `<pick> <sub>` placeholder form.
_SUBCOMMAND_RE = re.compile(r"""(?:pick\.py["']?|<pick>)[ \t]+([A-Za-z][\w-]*)""")


def _markdown_files() -> list[Path]:
    files = []
    for folder in ("skills", "commands", "agents"):
        files += sorted((REPO / folder).rglob("*.md"))
    return files


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _subcommands() -> set[str]:
    parser = pick.build_parser()
    sub = next(a for a in parser._actions if a.dest == "command")
    return set(sub.choices)


def _plugin_name() -> str:
    return json.loads(_read(REPO / ".claude-plugin" / "plugin.json"))["name"]


def test_expected_files_exist():
    assert SKILL.is_file()
    assert AGENT.is_file()
    for name in ("skill-mode", "skill-deep-scan", "autopick", "skill-pick"):
        assert (COMMANDS / f"{name}.md").is_file(), name


def test_all_markdown_frontmatter_parses():
    files = _markdown_files()
    assert files
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


def test_skill_name_matches_its_directory():
    assert parse_frontmatter(_read(SKILL))["name"] == SKILL.parent.name == "skill-pick"


def test_skill_description_states_its_triggers():
    desc = parse_frontmatter(_read(SKILL))["description"]
    assert desc.startswith(
        "Use when the Skill Pick Wizard hook says a skill scan is pending, "
        "or when /autopick or /skill-pick runs"
    )


def test_commands_reference_real_subcommands():
    known = _subcommands()
    found = 0
    for path in sorted(COMMANDS.glob("*.md")) + [SKILL]:
        for word in _SUBCOMMAND_RE.findall(_read(path)):
            assert word in known, f"{path.name}: pick.py {word}"
            found += 1
    assert found


def test_skill_uses_every_flow_subcommand():
    used = set(_SUBCOMMAND_RE.findall(_read(SKILL)))
    assert {"status", "search", "catalog", "save-plan", "skip"} <= used


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
        assert "skill-pick-wizard:skill-pick" in text, name
        assert "$ARGUMENTS" in text, name
    assert "--mode silent" in _read(COMMANDS / "autopick.md")


def test_command_files_match_hook_command_list():
    assert {p.stem for p in COMMANDS.glob("*.md")} == set(PLUGIN_COMMANDS)


def test_skill_lists_exact_approval_options():
    text = _read(SKILL)
    assert "AskUserQuestion" in text
    for label in APPROVAL_OPTIONS:
        assert label in text, label


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
