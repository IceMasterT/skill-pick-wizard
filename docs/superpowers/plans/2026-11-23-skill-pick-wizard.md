# Skill Pick Wizard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Claude Code plugin that, at the start of each job, searches every installed skill by full description, recommends the best ones grouped by job stage (approve / show / silent modes, optional deep scan), and keeps the approved picks in effect for the whole job.

**Architecture:** A `UserPromptSubmit` hook reads only small state files and injects context that tells Claude to run the plugin's `skill-pick` skill. That skill drives a stdlib-Python CLI (`scripts/pick.py`). The CLI maintains a SQLite FTS5 index of user, project and plugin skills (plus Obsidian vault area tags), answers multi-query searches, writes deep-scan catalog chunks for parallel read-only helper agents, and stores per-session plans. Slash commands are thin markdown prompts over the same CLI.

**Tech Stack:** Python 3 standard library only (`sqlite3` with FTS5, `argparse`, `json`, `dataclasses`), pytest for tests (development only), Claude Code plugin format (`.claude-plugin/plugin.json`, `hooks/hooks.json`, `commands/`, `skills/`, `agents/`).

**Spec:** `docs/superpowers/specs/2026-11-23-skill-pick-wizard-design.md`

## Global Constraints

- Runtime: Python 3 standard library only, no installs. Verified on this machine: Python 3.14.7, SQLite 3.54.1 with FTS5.
- Plugin name: `skill-pick-wizard`. The repo root is the plugin root.
- Skill sources:
  - `<claude_home>/skills/*/SKILL.md`, following symlinks.
  - `<cwd>/.claude/skills/*/SKILL.md`.
  - enabled plugins: the `<installPath>/skills/*/SKILL.md` of each plugin in `<claude_home>/plugins/installed_plugins.json`.
- Invoke names:
  - user and project skills: the **directory name**. Claude Code lists `grok-mcp` under its folder name even though its frontmatter says `name: mcp`.
  - plugin skills: `<plugin>:<directory name>`.
- `enabledPlugins` precedence: `<claude_home>/settings.json` < `<cwd>/.claude/settings.json` < `<cwd>/.claude/settings.local.json`.
- Modes: `approve` (default), `show`, `silent`. Deep scan default: off. Deep scan model default: `sonnet`. Deep scan chunks: 3. Search candidates: 30. Final picks: 3 to 8.
- Default vault path: `~/Documents/obsidian-brain/100-tools/Tool Archive`. It's optional; if it's missing, the plugin works without it. Vault tags used: those starting with `tool/`.
- Data directory: `$SKILL_PICK_WIZARD_HOME` if set, else `~/.local/share/skill-pick-wizard`.
  - Deviation from the spec's "plugin data directory": `CLAUDE_PLUGIN_DATA` exists in hook processes but **not** in the Bash tool's environment. Hook and CLI must share one deterministic location.
- Claude home: `$SKILL_PICK_CLAUDE_HOME` if set (used by tests), else `~/.claude`.
- Session id: an explicit `--session`, else `$CLAUDE_CODE_SESSION_ID` (present in the Bash tool's environment). It must match `^[A-Za-z0-9_-]{1,128}$`.
- Hook budget: it reads at most `config.json` and one session file. It never touches the index. It never exits non-zero and never blocks a prompt.
- Out of scope (MVP):
  - re-scanning on follow-up messages
  - built-in skills that have no files on disk
  - installing or editing skills
- The repo is not under git yet; Task 1 runs `git init`. Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Odd `SKILL.md` formatting:** CRLF line endings (e.g. `repo-scan`), a UTF-8 BOM, folded (`>`, `>-`) or literal (`|`) descriptions, multi-line plain descriptions, quoted values containing colons. The description must come out as one clean string. Pinned in Task 1.
2. **Broken skill folders:** broken symlinks, a `SKILL.md` that is unreadable or non-UTF-8, missing frontmatter, missing description. These are skipped (logged once to `<data>/errors.log`) and every other skill is still indexed. Pinned in Task 2.
3. **Search queries containing FTS5 syntax:** `"`, `-`, `:`, `(`, `*`, `NEAR`, `AND`, and empty queries. They must never raise a SQLite error. A query of only punctuation returns no hits. Pinned in Task 3.
4. **Hook given garbage:** empty stdin, non-JSON stdin, missing `session_id`, a corrupt `config.json` or session file. The hook exits 0 and prints nothing (or valid JSON), and the prompt goes through. Pinned in Task 7.
5. **Two sessions writing state at once,** or a crash mid-write. Every state and config write is atomic (temp file plus `os.replace`), so a reader never sees half a file. Pinned in Task 4.

---

## File Structure

```
.claude-plugin/plugin.json          plugin manifest
.claude-plugin/marketplace.json     local marketplace so the plugin can be installed from this folder
hooks/hooks.json                    UserPromptSubmit -> scripts/hook.py
scripts/hook.py                     hook entry: stdin JSON -> additionalContext JSON (never fails)
scripts/pick.py                     CLI entry (argparse) used by skill, commands, agents
scripts/skillpick/__init__.py
scripts/skillpick/paths.py          claude_home(), data_dir(), project_key()
scripts/skillpick/frontmatter.py    parse_frontmatter()
scripts/skillpick/sources.py        Skill, discovery of user/project/plugin skills, enabled plugin resolution
scripts/skillpick/vault.py          vault area tags by directory name
scripts/skillpick/index.py          fingerprint(), SkillIndex (build/search), build_fts_query()
scripts/skillpick/state.py          Config, SessionState, Stage, atomic JSON io, cleanup
scripts/skillpick/service.py        fresh_indexes(), search(), known_invokes() glue used by the CLI
scripts/skillpick/catalog.py        split_chunks(), write_catalog()
scripts/skillpick/hookctx.py        build_context() pure function
skills/skill-pick/SKILL.md          the picking flow Claude follows
commands/skill-mode.md
commands/skill-deep-scan.md
commands/autopick.md
commands/skill-pick.md
agents/skill-deep-scanner.md        read-only deep scan helper
tests/conftest.py                   fake claude home / data dir fixtures, make_skill()
tests/test_*.py
pytest.ini                          pythonpath = scripts
README.md                           install and usage
```

---

### Task 1: Scaffold, paths and frontmatter parser

**Files:**
- Create: `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, `pytest.ini`, `.gitignore`, `scripts/skillpick/__init__.py`, `scripts/skillpick/paths.py`, `scripts/skillpick/frontmatter.py`, `tests/conftest.py`
- Test: `tests/test_frontmatter.py`, `tests/test_paths.py`

**Interfaces:**
- Produces:
  - `paths.claude_home() -> Path`
  - `paths.data_dir() -> Path` (creates it)
  - `paths.project_key(cwd: Path) -> str` (first 16 hex chars of sha256 of the resolved path)
  - `frontmatter.parse_frontmatter(text: str) -> dict[str, str | list[str]]`
  - conftest fixtures:
    - `env(tmp_path) -> SimpleNamespace(claude_home: Path, data: Path, cwd: Path)`, which sets both env vars via `monkeypatch`.
    - helper `make_skill(root: Path, dirname: str, fm: str, body: str = "body") -> Path`, which writes `root/dirname/SKILL.md` as `"---\n" + fm + "\n---\n" + body`.

- [ ] **Step 1: Initialize the repo and manifests**

`git init`. Then write:
- `plugin.json`: `{"name": "skill-pick-wizard", "version": "0.1.0", "description": "Recommends the best installed skills for each job and keeps them in use for the whole job.", "license": "MIT"}`.
- `marketplace.json`: `{"name": "skill-pick-wizard", "owner": {"name": "local"}, "plugins": [{"name": "skill-pick-wizard", "source": "./", "description": "<same description>"}]}`.
- `pytest.ini`: `[pytest]\npythonpath = scripts\ntestpaths = tests`.
- `.gitignore`: `__pycache__/`, `.pytest_cache/`.

- [ ] **Step 2: Write failing tests**

```python
# tests/test_frontmatter.py
from skillpick.frontmatter import parse_frontmatter as p

def test_plain_scalars():
    assert p("---\nname: tdd\ndescription: Use when testing\n---\nbody") == {"name": "tdd", "description": "Use when testing"}

def test_crlf_and_bom():
    assert p("﻿---\r\nname: repo-scan\r\ndescription: Audit code\r\n---\r\n")["description"] == "Audit code"

def test_quoted_with_colon():
    assert p('---\ndescription: "Use when: auditing"\n---\n')["description"] == "Use when: auditing"
    assert p("---\ndescription: 'It''s fine'\n---\n")["description"] == "It's fine"

def test_folded_and_literal_blocks_joined_to_one_line():
    t = "---\ndescription: >-\n  Line one\n  line two\nname: x\n---\n"
    assert p(t) == {"description": "Line one line two", "name": "x"}
    assert p("---\ndescription: |\n  a\n  b\n---\n")["description"] == "a b"

def test_multiline_plain_continuation():
    assert p("---\ndescription: first part\n  second part\n---\n")["description"] == "first part second part"

def test_lists_inline_and_block():
    assert p('---\ntags: [a, "b c", tool/x]\n---\n')["tags"] == ["a", "b c", "tool/x"]
    assert p("---\ntags:\n- a\n- tool/security/ti\n---\n")["tags"] == ["a", "tool/security/ti"]

def test_no_frontmatter_returns_empty():
    assert p("# Just a heading\n") == {}
    assert p("---\nname: x\n") == {}   # unterminated

def test_nested_mapping_values_are_ignored_not_crashing():
    assert p("---\nname: x\nmetadata:\n  a: 1\n---\n")["name"] == "x"
```

`tests/test_paths.py`:
- `test_env_overrides`: with both env vars set, `claude_home()` and `data_dir()` return them, and `data_dir()` exists afterwards.
- `test_project_key_stable`: same path gives the same 16-character key; different paths give different keys.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_frontmatter.py tests/test_paths.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'skillpick'`

- [ ] **Step 4: Implement `paths.py` and `frontmatter.py`**

The parser is a line-based YAML subset:
- Strip the BOM and normalize `\r\n` to `\n`.
- Require an opening `---` line and a closing `---` line.
- Top-level `key: value` lines at column 0.
- Indented lines after a block indicator (`>`, `>-`, `|`, `|-`) or after a plain scalar are continuation lines, joined with single spaces and stripped.
- `- item` lines under an empty-valued key form a list.
- Inline `[...]` is split on commas outside quotes.
- Single- or double-quoted values are unquoted (with `''` becoming `'` inside single quotes).
- An indented `k: v` under an empty key (a nested mapping) is dropped.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_frontmatter.py tests/test_paths.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

`git add -A && git commit -m "feat: scaffold plugin, paths, frontmatter parser"`

---

### Task 2: Skill discovery and vault tags

**Files:**
- Create: `scripts/skillpick/sources.py`, `scripts/skillpick/vault.py`
- Test: `tests/test_sources.py`, `tests/test_vault.py`

**Interfaces:**
- Consumes:
  - `parse_frontmatter`
  - `paths.claude_home`, `paths.data_dir`
- Produces:
  - `@dataclass(frozen=True) Skill`
    - `invoke: str`
    - `name: str` (the frontmatter name, or the directory name)
    - `description: str`
    - `source: str` (one of `"user"`, `"project"`, `"plugin"`)
    - `plugin_id: str | None` (e.g. `"superpowers@claude-plugins-official"`)
    - `path: str` (absolute path of `SKILL.md`)
    - `tags: tuple[str, ...]`
  - `sources.read_skill(skill_md: Path, invoke: str, source: str, plugin_id: str | None) -> Skill | None`: returns `None` and appends one line to `<data>/errors.log` when the file is unreadable, has no frontmatter, or has an empty description.
  - `sources.user_skills(claude_home: Path) -> list[Skill]`
  - `sources.project_skills(cwd: Path) -> list[Skill]`
  - `sources.plugin_skills(claude_home: Path) -> list[Skill]`: covers **all** installed plugins, using each plugin's latest user-scope install. Enablement is filtered at search time, so one global index serves every project.
  - `sources.project_plugin_skills(claude_home: Path, cwd: Path) -> list[Skill]`: covers plugins installed with `scope` `project` or `local` whose `projectPath` equals the resolved cwd. These go in that project's index and take precedence over a user-scope install of the same plugin.
  - `sources.enabled_plugin_ids(claude_home: Path, cwd: Path) -> set[str]`
  - `sources.skill_files(claude_home: Path, cwd: Path) -> list[Path]`: every `SKILL.md` path the three sources would read, plus `settings.json` and `installed_plugins.json`. This is the input to the fingerprint.
  - `vault.area_tags(vault_dir: Path | None) -> dict[str, tuple[str, ...]]`: keyed by skill directory name, with values being the `tool/...` tags.

- [ ] **Step 1: Write failing tests** (`tests/test_sources.py`, using `env` and `make_skill`)

- `test_user_skill_invoke_is_dirname`: `make_skill(home/"skills", "grok-mcp", "name: mcp\ndescription: d")` gives `invoke == "grok-mcp"`, `name == "mcp"`, `source == "user"`.
- `test_symlinked_skill_followed_and_broken_symlink_skipped`: a symlink to a real skill dir is indexed. A symlink to a missing target is skipped without raising.
- `test_bad_files_skipped_and_logged`: one dir has no frontmatter, one has `description:` empty, one has `SKILL.md` bytes `b"\xff\xfe\x00"`. The result holds only the good skill. `errors.log` has 3 lines, each containing the path.
- `test_plugin_skills_namespaced`:
  - Setup: `installed_plugins.json` `{"version": 2, "plugins": {"sp@m": [{"scope": "user", "installPath": "<tmp>/sp/1.0", "lastUpdated": "2026-01-01T00:00:00Z"}]}}`, with `<tmp>/sp/1.0/skills/brainstorming/SKILL.md`.
  - Expected: `invoke == "sp:brainstorming"`, `plugin_id == "sp@m"`.
- `test_multiple_installs_prefers_latest_user_scope`: two user-scope entries. Only the one with the later `lastUpdated` is used.
- `test_project_scoped_install_only_for_its_project`: an entry with `scope: "project"` and `projectPath` equal to cwd A. It's returned by `project_plugin_skills(home, A)`, not by `project_plugin_skills(home, B)`, and never by `plugin_skills`.
- `test_enabled_precedence`:
  - Setup: user `{"enabledPlugins": {"a@m": true, "b@m": true}}`; project `.claude/settings.json` `{"enabledPlugins": {"b@m": false}}`; local `settings.local.json` `{"enabledPlugins": {"c@m": true}}`.
  - Expected: `{"a@m", "c@m"}`.
- `test_missing_settings_files_ok`: no settings files gives `set()`, and no exception.
- `test_skill_files_lists_everything_read`: contains each `SKILL.md` path and the two JSON paths.

`tests/test_vault.py`:
- `test_area_tags_by_dirname_including_nested_packs`:
  - Setup: `vault/kill-chain/SKILL.md` with `tags: [x, tool/security/threat-intelligence]`, and `vault/ads/google-ads-api-quickstart/SKILL.md` with `tags:\n- tool/ads`.
  - Expected: `{"kill-chain": ("tool/security/threat-intelligence",), "google-ads-api-quickstart": ("tool/ads",)}`.
- `test_tests_fixture_dirs_ignored`: `vault/SkillSpector-main/tests/fixtures/evil/SKILL.md` gives no `"evil"` key.
- `test_missing_vault_returns_empty`: `area_tags(None) == {}` and `area_tags(tmp/"nope") == {}`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_sources.py tests/test_vault.py -v`
Expected: FAIL, ImportError

- [ ] **Step 3: Implement `sources.py` and `vault.py`**

- Directory iteration: `sorted(root.iterdir())`. Skip entries whose resolved path doesn't exist. Read with `encoding="utf-8"` and catch `OSError` and `UnicodeDecodeError`.
- Frontmatter `tags` (a list or a comma string) go into `Skill.tags`.
- Install selection: among entries of the matching scope, break ties by the latest `lastUpdated`. Entries without a parseable `installPath` directory are skipped.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_sources.py tests/test_vault.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

`git commit -am "feat: discover user, project and plugin skills; vault area tags"` (add the new files first)

---

### Task 3: Search index

**Files:**
- Create: `scripts/skillpick/index.py`
- Test: `tests/test_index.py`

**Interfaces:**
- Consumes: `Skill`
- Produces:
  - `index.fingerprint(files: list[Path]) -> str`: sha256 over sorted `(str(path), st_mtime_ns)` pairs. Missing files contribute `(path, -1)`.
  - `index.build_fts_query(text: str) -> str | None`: words matching `[A-Za-z0-9]+` from the text, lowercased, each double-quoted, joined with ` OR `. Returns `None` when there are no words.
  - `class SkillIndex`:
    - `__init__(self, db_path: Path)`
    - `stored_fingerprint(self) -> str | None`
    - `rebuild(self, skills: list[Skill], fp: str) -> None`: drops and refills in one transaction.
    - `count(self) -> int`
    - `all(self) -> list[Skill]`
    - `get(self, invoke: str) -> Skill | None`
    - `search(self, queries: list[str], limit: int = 30, allow: Callable[[Skill], bool] = lambda s: True) -> list[Hit]`
  - `@dataclass(frozen=True) Hit`: `skill: Skill`, `score: float`, `queries: tuple[str, ...]` (the queries that matched it).

- [ ] **Step 1: Write failing tests**

Fixture: build an index from these skills (tags in brackets):
- `performance-optimization`: "Optimize slow code, speed up rendering and reduce latency"
- `test-driven-development`: "Write tests first, red green refactor" [tool/testing]
- `brainstorming`: "Explore intent and design before building"
- `sp:brainstorming`: plugin `sp@m`, same description
- `stripe-payments`: "Stripe checkout and billing integration"

Tests:
- `test_name_weighted_over_description`: query `"brainstorming"` ranks both brainstorming skills above anything that only mentions "design".
- `test_multi_query_merge_rrf`:
  - Call: `search(["speed up rendering", "write tests first"])`.
  - Expected: the top 2 invokes are `{"performance-optimization", "test-driven-development"}`, and each `Hit.queries` holds the query that hit it.
- `test_skill_hit_by_two_queries_ranks_higher`: a skill matched by both queries outranks one matched by only one of them.
- `test_tags_searchable`: `search(["testing"])` includes `test-driven-development`.
- `test_limit_respected`: `search(["the"], limit=2)` returns at most 2.
- `test_allow_filter`: `allow=lambda s: s.plugin_id != "sp@m"` excludes `sp:brainstorming`.
- `test_fts_special_characters_never_raise` (parametrized over `'"', 'test-driven', 'a:b', '(x', 'speed*', 'NEAR', 'AND OR NOT', '', '!!!'`): no exception. `'!!!'` and `''` give `[]`.
- `test_rebuild_replaces_contents_and_fingerprint`: rebuild with 1 skill gives `count() == 1` and the new `stored_fingerprint()`.
- `test_fingerprint_changes_on_mtime`: touch one file with `os.utime(ns=...)` and the fingerprint changes. A missing file is stable and different from a present one.
- `test_get_and_all`: `get("stripe-payments").description` round-trips. `get("nope") is None`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_index.py -v`
Expected: FAIL, ImportError

- [ ] **Step 3: Implement `index.py`**

- Tables: `meta(k TEXT PRIMARY KEY, v TEXT)`, `skills(rowid INTEGER PRIMARY KEY, invoke TEXT UNIQUE, name, description, source, plugin_id, path, tags_json)`, and `fts USING fts5(invoke, name, description, tags, content='skills', content_rowid='rowid', tokenize='porter unicode61')`.
- For the `invoke` column, insert hyphen- and colon-separated words as space-separated text, so `test-driven-development` matches "driven".
- Per query: run `SELECT rowid FROM fts WHERE fts MATCH ? ORDER BY bm25(fts, 10.0, 8.0, 1.0, 3.0) LIMIT 100`.
- Merge across queries with reciprocal rank fusion:

```python
score[rowid] += 1.0 / (60 + rank)   # rank is 0-based position within that query's results
```

- Then filter with `allow`, sort by score descending then invoke ascending, and apply `limit`.
- Open connections with `timeout=5`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_index.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

`git add -A && git commit -m "feat: FTS5 skill index with multi-query RRF search"`

---

### Task 4: Config and session state

**Files:**
- Create: `scripts/skillpick/state.py`
- Test: `tests/test_state.py`

**Interfaces:**
- Consumes: `paths.data_dir`
- Produces:
  - `MODES = ("approve", "show", "silent")`
  - `@dataclass Config`:
    - `mode: str = "approve"`
    - `deep_scan: bool = False`
    - `deep_scan_model: str = "sonnet"`
    - `vault_path: str = "~/Documents/obsidian-brain/100-tools/Tool Archive"`
  - `load_config(data: Path) -> Config`: defaults on a missing file, invalid JSON, or invalid values (each field validated on its own).
  - `save_config(data: Path, cfg: Config) -> None`
  - `@dataclass Stage`: `stage: str`, `skills: list[str]`
  - `@dataclass SessionState`:
    - `status: str` (`"planned"` or `"skipped"`)
    - `job: str = ""`
    - `stages: list[Stage] = field(default_factory=list)`
    - `mode: str = ""`
    - `deep_scan: bool = False`
    - `updated: float = 0.0`
  - `valid_session_id(sid: str) -> bool`
  - `load_session(data: Path, sid: str) -> SessionState | None`: returns `None` on a missing or corrupt file or an invalid sid.
  - `save_plan(data: Path, sid: str, job: str, stages: list[Stage], mode: str, deep_scan: bool) -> None`
  - `mark_skipped(data: Path, sid: str) -> None`
  - `clear_session(data: Path, sid: str) -> None`
  - `cleanup_sessions(data: Path, max_age_days: int = 14, now: float | None = None) -> int`: returns the number of files removed.
  - `atomic_write_json(path: Path, obj) -> None`
  - Session files live at `<data>/sessions/<sid>.json`. `save_*` and `mark_*` raise `ValueError` on an invalid sid.

- [ ] **Step 1: Write failing tests**

- `test_config_defaults_and_roundtrip`: a missing file gives `Config()`. Save `mode="show", deep_scan=True` and reload: equal.
- `test_config_corrupt_or_invalid_values`:
  - File `"{not json"` gives defaults.
  - File `{"mode": "loud", "deep_scan": "yes", "deep_scan_model": "opus"}` gives `mode == "approve"`, `deep_scan is False`, `deep_scan_model == "opus"`.
- `test_plan_roundtrip`: `save_plan(data, "abc-1", "add checkout", [Stage("Planning", ["brainstorming"])], "approve", False)`, then load: `status == "planned"`, stages equal.
- `test_skip_and_clear`: `mark_skipped` gives `status == "skipped"`. `clear_session` gives `load_session is None`.
- `test_invalid_session_ids` (parametrized over `"../x"`, `"a/b"`, `""`, `"x" * 129`, `"a b"`): `valid_session_id` is False, `load_session` is None, and `save_plan` raises `ValueError`.
- `test_corrupt_session_file_is_none`: write garbage to `sessions/s1.json`. `load_session(data, "s1") is None`.
- `test_atomic_write_leaves_no_temp_and_survives_replace_failure`:
  - Monkeypatch `os.replace` to raise `OSError` once.
  - Expected: `save_config` raises, the old `config.json` content is intact, and no `*.tmp` files remain in `data`.
- `test_concurrent_writers_never_corrupt`: 8 threads × 50 `save_plan` calls on one sid, with a reader thread looping `load_session`. The reader never sees `None` after the first write, and the final file parses.
- `test_cleanup_sessions_by_age`: two files, one with mtime 20 days old. `cleanup_sessions(data, 14)` returns 1 and the newer file remains.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_state.py -v`
Expected: FAIL, ImportError

- [ ] **Step 3: Implement `state.py`**

`atomic_write_json`:
- Use `tempfile.NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False)`, then `json.dump`, `flush`, `os.fsync`, `os.replace`.
- On any exception, unlink the temp file and re-raise.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_state.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

`git add -A && git commit -m "feat: config and per-session plan state with atomic writes"`

---

### Task 5: Service glue and CLI

**Files:**
- Create: `scripts/skillpick/service.py`, `scripts/pick.py`
- Test: `tests/test_service.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: everything from Tasks 1 to 4.
- Produces:
  - `service.fresh_indexes(claude_home: Path, cwd: Path, data: Path, cfg: Config) -> tuple[SkillIndex, SkillIndex]`
    - Returns `(global_index, project_index)`.
    - Global index at `<data>/index/global.sqlite`. It holds user and all-plugin skills, with vault tags merged into `Skill.tags`.
    - Project index at `<data>/index/project-<project_key>.sqlite`. It holds `project_skills` plus `project_plugin_skills`.
    - When the same invoke is in both indexes, the project one wins.
    - Each is rebuilt only when its fingerprint changed. The global fingerprint covers user and plugin skill files, the two JSON files, and the vault `SKILL.md` files. The project fingerprint covers project skill files.
  - `service.search(claude_home, cwd, data, cfg, queries: list[str], limit: int = 30) -> list[Hit]`
    - Queries both indexes with `allow` = (non-plugin) or (`plugin_id in enabled_plugin_ids(claude_home, cwd)`).
    - Merges by score, then trims to `limit`.
  - `service.known_invokes(...) -> set[str]`: the same filter, over `all()`.
  - `pick.py` subcommands. Each prints plain text for Claude to read. Exit 0 on success, exit 2 with a one-line message on stderr for user errors.
    - `status [--session ID]`: prints `mode: <m>`, `deep_scan: on|off`, `deep_scan_model: <m>`, `session: <none|planned|skipped>`, and when planned the plan lines.
    - `search --cwd DIR [--limit N] QUERY [QUERY ...]`: one block per hit:
      ```
      `<invoke>`  [<tags comma-joined>]  (matched: <q1>; <q2>)
          <description>
      ```
    - `save-plan [--session ID] --job TEXT --plan JSON [--mode MODE] [--deep-scan]`
      - `--plan` is a JSON list: `[{"stage": "Planning", "skills": ["brainstorming"]}]`.
      - Every invoke is checked against `known_invokes`. Unknown invokes produce exit 2: `unknown skills: a, b`.
      - `--mode` defaults to the config mode.
    - `skip [--session ID]`, `reset [--session ID]` (`clear_session`)
    - `show-plan [--session ID]`
    - `mode [approve|show|silent]`: with no argument prints the current mode; with an argument saves it and prints `mode: <m>`.
    - `deep-scan [on|off]`: same pattern.
    - `index --cwd DIR`: forces a rebuild and prints `global: <n> skills, project: <n> skills`.
    - `catalog` is added in Task 6.
    - The session comes from `--session`, else `$CLAUDE_CODE_SESSION_ID`, else exit 2 `no session id`.

- [ ] **Step 1: Write failing tests**

`tests/test_service.py` (on the `env` fixture, with user, project, two plugins one disabled at project level, and a vault dir pointed to by `cfg.vault_path`):
- `test_disabled_plugin_skills_never_returned`: the disabled plugin's skill is absent from `search` and from `known_invokes`.
- `test_project_skills_only_in_their_project`: a project skill in cwd A isn't returned when searching with cwd B.
- `test_vault_tags_merged`: a user skill with a vault entry has `tool/...` in `Hit.skill.tags`.
- `test_rebuild_only_when_changed`:
  - Monkeypatch `SkillIndex.rebuild` to count calls.
  - Expected: two `search` calls give 1 global rebuild. Touching a user `SKILL.md` gives 2.
- `test_new_skill_appears_after_install`: add a skill dir between searches. The second search finds it.

`tests/test_cli.py` (runs `pick.main(argv) -> int` in-process with `capsys`, so there's no subprocess):
- `test_mode_get_set_and_invalid`: `["mode"]` prints `mode: approve`. `["mode", "show"]` persists. `["mode", "loud"]` gives argparse exit 2.
- `test_deep_scan_toggle`: `["deep-scan", "on"]` makes `status` print `deep_scan: on`.
- `test_save_plan_rejects_unknown_skills`: exit 2, stderr contains `unknown skills: nope`, no session file written.
- `test_save_plan_then_status_and_show_plan`: `status` prints `session: planned` and `Planning: brainstorming`.
- `test_session_from_env`: with `CLAUDE_CODE_SESSION_ID=s9`, `["skip"]` makes `load_session(data, "s9").status == "skipped"`.
- `test_no_session_id_errors`: env unset and no flag gives exit 2, `no session id`.
- `test_search_output_format`: output contains `` `test-driven-development` `` and the full description on the next line.
- `test_save_plan_bad_json`: `--plan "{"` gives exit 2 `invalid --plan JSON`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_service.py tests/test_cli.py -v`
Expected: FAIL, ImportError

- [ ] **Step 3: Implement `service.py` and `pick.py`**

- `pick.py` puts its own directory on `sys.path` (`sys.path.insert(0, str(Path(__file__).resolve().parent))`) so it runs from any cwd.
- `main(argv=None) -> int`, with `if __name__ == "__main__": sys.exit(main())`.
- Expand `cfg.vault_path` with `expanduser`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest -v`
Expected: all PASS

- [ ] **Step 5: Commit**

`git add -A && git commit -m "feat: pick.py CLI with search, plans, modes and deep-scan toggle"`

---

### Task 6: Deep scan catalog and helper agent

**Files:**
- Create: `scripts/skillpick/catalog.py`, `agents/skill-deep-scanner.md`
- Modify: `scripts/pick.py` (add the `catalog` subcommand)
- Test: `tests/test_catalog.py`

**Interfaces:**
- Consumes:
  - `service.fresh_indexes`
  - the same enabled filter as `service.known_invokes` (expose it as `service.allowed_skills(...) -> list[Skill]`)
- Produces:
  - `catalog.split_chunks(skills: list[Skill], n: int = 3) -> list[list[Skill]]`
    - Greedy: sort by `len(description)` descending, assign each skill to the currently smallest chunk (by total characters, ties to the lowest index), then sort each chunk by invoke.
    - Returns `min(n, len(skills))` non-empty chunks.
  - `catalog.write_catalog(skills: list[Skill], out_dir: Path, n: int = 3) -> list[Path]`
    - Writes `out_dir/chunk-<i>.md` (1-based).
    - Each line: `` - `<invoke>` [<tags>]: <description> ``
    - It first removes old `chunk-*.md` files in `out_dir`.
  - CLI `catalog --cwd DIR [--session ID] [--chunks 3]`: writes to `<data>/catalog/<session>/` and prints one absolute path per line.
  - Agent `skill-pick-wizard:skill-deep-scanner`, with frontmatter `name: skill-deep-scanner`, `description: ...`, `tools: Read`, `model: sonnet`.

- [ ] **Step 1: Write failing tests**

- `test_every_skill_in_exactly_one_chunk`: 100 synthetic skills with random description lengths (seeded) and `n=3`. The union equals the input and there are no duplicates.
- `test_chunks_balanced`: the largest chunk's character total is ≤ 1.25 × the smallest's for the 100-skill case.
- `test_fewer_skills_than_chunks`: 2 skills with `n=3` gives 2 chunks.
- `test_write_catalog_format_and_cleanup`: a pre-existing `chunk-9.md` is removed. The line for `sp:brainstorming` equals `` - `sp:brainstorming` [tool/x]: desc ``.
- `test_cli_catalog_prints_paths`: prints 3 existing paths under `<data>/catalog/<sid>/`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_catalog.py -v`
Expected: FAIL, ImportError

- [ ] **Step 3: Implement `catalog.py` and the CLI subcommand, and write `agents/skill-deep-scanner.md`**

The agent body instructs it to:
1. `Read` the chunk file path it's given (all of it, using offsets if the file is long).
2. Consider the job description and stage list in its prompt.
3. Return at most 8 matches per stage as lines of the form `` <stage> | `<invoke>` | <one-line reason> ``.
4. Copy invokes exactly as written in the file, and never invent skills.
5. Return `NONE` for a stage with no good match.
6. Not use any other tool.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest -v`
Expected: all PASS

- [ ] **Step 5: Commit**

`git add -A && git commit -m "feat: deep scan catalog chunks and read-only scanner agent"`

---

### Task 7: UserPromptSubmit hook

**Files:**
- Create: `scripts/skillpick/hookctx.py`, `scripts/hook.py`, `hooks/hooks.json`
- Test: `tests/test_hook.py`

**Interfaces:**
- Consumes: `load_config`, `load_session`, `cleanup_sessions`, `valid_session_id`
- Produces:
  - `hookctx.PLUGIN_COMMANDS = ("autopick", "skill-pick", "skill-mode", "skill-deep-scan")`
  - `hookctx.build_context(prompt: str, sid: str, cfg: Config, session: SessionState | None, pick_cmd: str) -> str | None`:
    - **Plugin command:** `None` when the stripped prompt starts with `/<cmd>` or `/skill-pick-wizard:<cmd>` for any of `PLUGIN_COMMANDS`. The command's own prompt drives the flow.
    - **Skipped session:** `None`.
    - **Planned session:** a reminder that starts with `Skill Pick Wizard - active skill plan for this job:`. It then has one line per stage, `<stage>: <invokes comma-joined>`, and ends with the instruction: *Invoke each skill with the Skill tool when the work reaches its stage. Use this plan only while the work matches the job "<job>". If the user starts clearly unrelated work, say in one line that /skill-pick will pick skills for the new job, then continue.*
    - **No session:** a scan instruction that starts with `Skill Pick Wizard - skill scan pending.` and contains `session: <sid>`, `mode: <mode>`, `deep scan: on (model <m>)|off`, and `run with: <pick_cmd>`. It ends with the instruction: *If this message asks for real work, invoke the skill-pick-wizard:skill-pick skill before doing anything else. If it is only a greeting or a quick question, answer normally and do not run the scan.*
  - `scripts/hook.py`:
    - Reads stdin JSON (`session_id`, `prompt`).
    - `pick_cmd = f'python3 "{Path(__file__).resolve().parent / "pick.py"}"'`.
    - Runs `cleanup_sessions` at most once per day, tracked by the marker file `<data>/.last-cleanup` mtime.
    - Prints `{"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": ctx}}` when `ctx` isn't empty; otherwise prints nothing.
    - Wraps everything in `try/except BaseException`. On error it appends a traceback summary to `<data>/errors.log` and exits 0 silently.
    - Has `main(stdin_text: str) -> str`, which returns the stdout text, for testing.
  - `hooks/hooks.json`:

```json
{"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/scripts/hook.py\"", "timeout": 10}]}]}}
```

- [ ] **Step 1: Write failing tests**

- `test_no_session_injects_scan_instruction`: the text contains `skill scan pending`, `session: s1`, `mode: approve`, `deep scan: off`, and `skill-pick-wizard:skill-pick`.
- `test_deep_scan_on_shows_model`: contains `deep scan: on (model sonnet)`.
- `test_planned_session_injects_reminder_only`: contains `Planning: brainstorming, writing-plans`, does not contain `scan pending`, and contains `/skill-pick`.
- `test_skipped_session_silent`: returns `None`.
- `test_plugin_commands_silent` (parametrized over `/autopick add x`, `/skill-pick`, `/skill-pick-wizard:skill-mode show`, `  /skill-deep-scan on`): returns `None`.
- `test_hook_main_outputs_valid_json`: `json.loads(main('{"session_id":"s1","prompt":"build a thing"}'))["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"`.
- `test_hook_never_fails` (parametrized over `""`, `"not json"`, `"[]"`, `'{"prompt":"x"}'`, `'{"session_id":"../../etc","prompt":"x"}'`): `main()` returns `""` and doesn't raise. Invalid or missing sid gives no output.
- `test_hook_corrupt_config_uses_defaults`: garbage `config.json` still yields a scan instruction with `mode: approve`.
- `test_hook_does_not_touch_index`: monkeypatch `skillpick.index.SkillIndex.__init__` to raise. `main()` still returns a scan instruction.
- `test_hook_script_subprocess_exit_zero`: `subprocess.run([sys.executable, "scripts/hook.py"], input="garbage", ...)` gives `returncode == 0`.
- `test_hook_fast`: `main()` on a valid payload takes under 0.2 s (measured with `time.perf_counter`, after one warm-up call).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_hook.py -v`
Expected: FAIL, ImportError

- [ ] **Step 3: Implement `hookctx.py`, `hook.py` and `hooks/hooks.json`**

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest -v`
Expected: all PASS

- [ ] **Step 5: Commit**

`git add -A && git commit -m "feat: UserPromptSubmit hook that triggers the scan and keeps the plan in view"`

---

### Task 8: The `skill-pick` skill and slash commands

**Files:**
- Create: `skills/skill-pick/SKILL.md`, `commands/skill-mode.md`, `commands/skill-deep-scan.md`, `commands/autopick.md`, `commands/skill-pick.md`
- Test: `tests/test_plugin_content.py`

**Interfaces:**
- Consumes: the CLI subcommands from Tasks 5 and 6, the agent from Task 6, and the hook context wording from Task 7.
- Produces: the user-facing behavior described in the spec's "Modes" and "Commands" sections.

**`skills/skill-pick/SKILL.md` content requirements.** The frontmatter is `name: skill-pick` with a description starting "Use when the Skill Pick Wizard hook says a skill scan is pending, or when /autopick or /skill-pick runs". The body has these steps in this order:

0. **Command.** Use the `run with:` command from the hook context. Otherwise use `python3 "<this skill's base directory>/../../scripts/pick.py"`. Run `status` and note the mode, deep scan setting and model. A mode given by the invoking command (`/autopick` means silent) overrides the saved mode for this job only.
1. **Stages.** Break the job into 2 to 4 stages. Write 1 to 3 search queries per stage in the job's own terms, plus synonyms. Include the stack and domain named in the job (e.g. "stripe", "react").
2. **Search.** Run `search --cwd "<cwd>" <queries...>`. This returns about 30 candidates.
3. **Deep scan (only if on).** Run `catalog --cwd "<cwd>"`. Then dispatch one `skill-pick-wizard:skill-deep-scanner` agent per printed path, **all in one message so they run in parallel**, with `model` set to the configured model. Each prompt contains the job, the stages and that chunk's path. Merge the agents' results with the search candidates. If an agent failed, say the deep scan was partial.
4. **Choose.** Pick 3 to 8 skills total, grouped by stage, using only invokes that appeared in the candidates. Keep the next 3 candidates per stage as swap alternatives.
5. **Act by mode.**
   - **approve:** show the list in the spec's exact format. The header is `Skills for: "<job summary>"`, with `(deep scan)` appended when it ran. Then call `AskUserQuestion` with exactly the options *Approve and start* / *Swap one* / *Add one* / *Skip for this session*. Swap and Add work as in the spec. Repeat until the user approves or skips. Skip runs `skip` and continues with the job without a plan.
   - **show:** show the list, then continue immediately.
   - **silent:** show nothing.
6. **Save.** Run `save-plan --job "<summary>" --plan '<json>' --mode <mode used>`, plus `--deep-scan` if it ran. If it reports unknown skills, drop or replace them and save again.
7. **Start the job.** Invoke the first stage's skills with the Skill tool now. Invoke the later stages' skills when the work reaches them, not up front.

**Commands.** Each has `description` and `argument-hint` frontmatter. Bodies tell Claude to run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/pick.py" ...` and report the result in one line:
- `skill-mode.md`: `mode $ARGUMENTS`
- `skill-deep-scan.md`: `deep-scan $ARGUMENTS`
- `autopick.md`: run `reset`, then follow the `skill-pick-wizard:skill-pick` skill for the task `$ARGUMENTS` with mode `silent` (save with `--mode silent`), then do the task. If `$ARGUMENTS` is empty, ask for the task in one line.
- `skill-pick.md`: run `reset`, then follow the skill with the saved mode for `$ARGUMENTS`. If that's empty, use the user's most recent request.

- [ ] **Step 1: Write failing tests** (`tests/test_plugin_content.py`, static checks on the markdown files)

- `test_all_markdown_frontmatter_parses`: every file under `skills/`, `commands/` and `agents/` parses with `parse_frontmatter` and has a non-empty `description`. Skill and agent files also have `name`.
- `test_commands_reference_real_subcommands`: every `pick.py <word>` occurrence in `commands/` and `skills/` names a subcommand in `pick.build_parser()`'s subparser choices.
- `test_skill_lists_exact_approval_options`: the skill contains the four option labels verbatim.
- `test_skill_references_agent_by_plugin_name`: contains `skill-pick-wizard:skill-deep-scanner`, and `agents/skill-deep-scanner.md` has `tools: Read`.
- `test_hook_wording_matches_skill_name`: the skill name in `hookctx`'s scan instruction equals `skill-pick-wizard:` + the skill's frontmatter `name`.

(This requires `pick.build_parser() -> argparse.ArgumentParser`, so expose it from Task 5's `pick.py` if it isn't already public.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_plugin_content.py -v`
Expected: FAIL (files missing)

- [ ] **Step 3: Write the skill and the four commands to the requirements above**

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest -v`
Expected: all PASS

- [ ] **Step 5: Commit**

`git add -A && git commit -m "feat: skill-pick flow skill and slash commands"`

---

### Task 9: Install, end-to-end check, README

**Files:**
- Create: `README.md`
- Test: manual end-to-end (below). It spends real tokens: one short headless session.

**Interfaces:**
- Consumes: the whole plugin.

- [ ] **Step 1: Validate and index against the real machine**

Run: `claude plugin validate /home/artiq/Desktop/skill-pick-wizard` (if this Claude Code version lacks `validate`, skip it and rely on step 3)
Expected: no errors

Run: `python3 scripts/pick.py index --cwd /home/artiq/Desktop/skill-pick-wizard`
Expected: `global: <roughly 1100 or more> skills, project: 0 skills` (1,086 user skills plus enabled-or-not plugin skills; malformed ones go to `errors.log`)

Run: `python3 scripts/pick.py search --cwd . "add stripe checkout" "write tests first" "security review"`
Expected: `test-driven-development` and `security-and-hardening` are among the hits. Time the second run: under 1 s.

- [ ] **Step 2: Install from the local marketplace**

Run: `claude plugin marketplace add /home/artiq/Desktop/skill-pick-wizard && claude plugin install skill-pick-wizard@skill-pick-wizard`
Expected: installed, and `~/.claude/settings.json` `enabledPlugins` has `"skill-pick-wizard@skill-pick-wizard": true`.

If the CLI subcommands aren't available, stop and ask the user to run `/plugin marketplace add /home/artiq/Desktop/skill-pick-wizard` and then `/plugin install skill-pick-wizard@skill-pick-wizard`.

- [ ] **Step 3: Headless end-to-end in silent mode**

Run:
- `python3 scripts/pick.py mode silent`
- in a fresh temp dir: `claude -p "Plan (do not implement) adding a dark mode toggle to a React settings page." --output-format json`

Expected:
- `<data>/sessions/<session_id>.json` exists with `status: planned`.
- It has 3 to 8 skills, every one listed by `pick.py search` or the index.
- The run's output shows the Skill tool was invoked for a first-stage skill.

Then run `python3 scripts/pick.py mode approve` to restore the default.

- [ ] **Step 4: Write `README.md`**

Cover:
- what it does
- install (the two commands from step 2)
- the three modes and `/skill-mode`
- `/autopick`, `/skill-pick` and `/skill-deep-scan`, with the deep scan cost: about 100,000 tokens per job at about 1,080 skills
- where data lives (`~/.local/share/skill-pick-wizard`, overridable with `SKILL_PICK_WIZARD_HOME`)
- the vault path setting in `config.json`
- MVP limits: it scans once per job, and doesn't index built-in skills

- [ ] **Step 5: Commit**

`git add -A && git commit -m "docs: README; verified end-to-end install"`
