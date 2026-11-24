# Skill Pick Wizard: design

Date: 2026-11-23
Status: draft, builder-checked, awaiting approval

## Intent

The user has about 1,086 skills installed. Claude regularly uses about 3 of them; the rest are never picked, even when they fit the job.

Root cause, observed in a live session: at this library size Claude Code cannot fit every skill's description into Claude's context, so most skills reach Claude as a bare name with no description. Claude ends up choosing skills by name alone, and mostly doesn't. Written instructions telling Claude to check for skills (`using-superpowers`, `using-agent-skills`) already exist and don't fix it.

The plugin fixes this. At the start of each job it finds the installed skills that best fit, using their full descriptions. It presents them grouped by stage of the job, gets approval (or not, depending on mode), and has Claude use each skill at the stage it belongs to for the whole job.

Success: on real jobs, Claude uses relevant skills well beyond the usual 3, and the user rarely has to name a skill by hand.

## Goals

1. At the start of a job, recommend the best-fitting installed skills, grouped by stage (for example Planning, Building, Checking).
2. Three modes, switchable by slash command, with the setting persisting across sessions:
   - **approve** (default): show the picks and wait. The user can approve, swap a skill for an alternative, or add a skill.
   - **show**: show the picks and start working without waiting.
   - **silent**: pick and start working without showing the picks.
3. `/autopick <task>`: run one job in silent mode without changing the saved mode.
4. Picks come only from skills that are actually installed and enabled.
5. Picks are based on each skill's full description, not its name.
6. The approved picks stay in effect for the whole job: Claude invokes each skill when the job reaches that skill's stage.
7. **Deep scan**, a setting toggled on or off by slash command and saved across sessions. When it's on, helper agents read the full description of every installed skill, so wording that no search query hits can still turn up. Default: off.

## Non-goals (MVP)

- **Re-scanning on follow-up messages.** The user wants this eventually ("the job may require something different at that point"), but the MVP scans once at the start of a job. Design for it to be added later; don't build it now.
- **Indexing Claude Code's built-in skills** that don't exist as files on disk (for example `artifact-design`). Claude still sees those in its normal list.
- **Installing, removing, or editing skills.**
- **Making the Obsidian vault the master skill list.**

## Anti-goals (failure even if it technically works)

- Adding noticeable delay to every prompt. The hook must stay well under a second once the index is built.
- Recommending a skill that isn't installed or is disabled.
- Showing a skill list on messages that aren't the start of a job.
- Loading every picked skill's full text into context up front. Skills are invoked when their stage starts.
- A list so long the user stops reading it. Aim for about 3 to 8 picks total.

## Constraints

- Claude Code plugin: `.claude-plugin/plugin.json`, `hooks/hooks.json`, `commands/`, `skills/`, using `${CLAUDE_PLUGIN_ROOT}`. Match the layout of installed plugins such as `superpowers` 7.0.2.
- A plugin cannot load skills into Claude by itself. It can only add context through hooks and give Claude tools or instructions to call. So the trigger must be a hook (it always runs), and the picking is Claude running the plugin's search script.
- The approval prompt uses Claude Code's `AskUserQuestion` tool: 2 to 4 options per question, plus a free-text "Other".
- Linux, Python 3 with `sqlite3` and FTS5 (already used by the user's vault tooling). Standard library only, no installs.
- Skill sources to index:
  - user skills: `~/.claude/skills/*/SKILL.md` (63 of these are symlinks; follow them)
  - project skills: `<cwd>/.claude/skills/*/SKILL.md`
  - enabled plugins' skills: install paths from `~/.claude/plugins/installed_plugins.json`, filtered by `enabledPlugins` in `~/.claude/settings.json`, scanning `<installPath>/skills/*/SKILL.md`. Plugin skills are invoked as `plugin:skill`.
- Optional extra signal: the user's Obsidian vault at `/home/artiq/Documents/obsidian-brain/100-tools/Tool Archive/` holds copies of most skills, with area tags in the `SKILL.md` frontmatter (`tool/<area>` or `tool/security/<area>`). Read these tags by skill name to help grouping and ranking. The vault path is configurable. If the vault is missing, the plugin works without it.

## Approaches considered

- **A. Local search, with Claude writing the queries (chosen).** A local SQLite FTS5 index of every installed skill (name, full description, vault area tags). At the start of a job Claude writes several queries, one per stage of the job, and gets back about 30 candidates with full descriptions. Claude picks the final set. It's fast, free, offline, and works in any project. Because Claude writes the queries, synonyms work ("make it faster" finds `performance-optimization`). Risk: missing a skill whose description uses wording no query hit.
- **B. Helper agents read the whole catalog.** Best recall, but the catalog is about 390,000 characters (around 100,000 tokens), and reading it adds 20 to 40 seconds per job. The user asked for this as an optional deep scan they can toggle on top of A.
- **C. Local semantic search with an embedding model.** Needs about 500 MB of model downloads and adds setup friction. Since Claude already writes the queries in A, C adds little. Dropped.

The user picked A as the core, with B added as an optional deep scan they can toggle.

## How it works (decided)

### Trigger: start of a job

- A `UserPromptSubmit` hook runs on every prompt. It does nothing if the session already has a saved job plan or the user skipped the scan for this session.
- Otherwise it adds context telling Claude to run the skill-pick flow before doing anything else, and which mode is active.
- "Start of a job" means the first message in a session that asks for real work. If the first message is a greeting or a quick question, Claude answers it normally and writes nothing. The session still has no plan, so the hook adds its (short, invisible) context again on the next message. This repeats until a plan is saved or the user skips. The hook never shows anything to the user itself.
- **Skipping:** in approve mode the fourth option of the approval question is *Skip for this session*. It runs `pick.py skip`, which marks the session skipped so the hook stays quiet. `/skill-pick` clears the skip.
- `/autopick <task>` always starts a new job in silent mode. The command's text tells Claude to run the flow with the mode overridden to silent (`pick.py save-plan --mode silent`). This replaces any existing plan and clears any skip for the session. The saved mode in `config.json` is not touched.
- **A second job in the same session:** the plan from the first job stays saved. The per-prompt reminder tells Claude to use the plan only while the work matches it. If the user starts clearly unrelated work, Claude says in one line that `/skill-pick` will pick skills for the new job, and carries on. Detecting new jobs automatically is part of the deferred re-scanning work.

### Picking (Claude follows the plugin's `skill-pick` skill)

1. Break the job into stages, and write 1 to 3 search queries per stage in the job's own terms.
2. Run `pick.py search` with those queries. It returns ranked candidates: name, how to invoke it, full description, and area tags.
3. Choose 3 to 8 skills total, grouped by stage. For each stage, also keep the next 3 best candidates as swap alternatives.
4. Act according to the mode (below), then save the plan with `pick.py save-plan`.

### Deep scan (when toggled on)

- Runs alongside step 2, not instead of it. The final candidate pool is the search results plus the deep-scan results, with duplicates removed.
- `pick.py catalog` writes the full catalog (name, how to invoke it, description, area tags) split into about 3 roughly equal chunks.
- Chunks are written as files in the plugin data directory, and each agent gets a file path, never the catalog text itself, so it stays out of the main conversation. The agent definition allows only `Read`.
- Claude dispatches one read-only helper agent per chunk, in parallel, using an agent definition the plugin ships. Each agent gets the job description and the stage breakdown, and returns its best matches per stage with a one-line reason. This keeps the 100,000 tokens of catalog out of the main conversation.
- The model is configurable in `config.json`. The default is Sonnet (my call: quality over cost, matching the user's preference for the best result).
- If an agent fails or times out, picking goes on with what came back and the user is told the deep scan was partial.
- In approve and show modes the list header says "deep scan" so the user knows which kind of scan ran.

### Modes

- **approve:** show the grouped list, then ask with `AskUserQuestion`: *Approve and start* / *Swap one* / *Add one*.
  - The question's four options are *Approve and start* / *Swap one* / *Add one* / *Skip for this session*.
  - **Swap:** a second question asks which skill to replace. If there are 4 or fewer picks they are the options; otherwise the user types the name in "Other". A third question offers that stage's 3 alternatives as options, with "Other" for a free-text search.
  - **Add:** the user types a skill name or describes what they need. Claude searches and confirms the match.
  - The question repeats until the user approves.
- **show:** show the grouped list and start the job in the same turn.
- **silent:** save the plan and start the job without showing it.

The list looks like this:

```
Skills for: "add Stripe checkout to the site"
  Planning   brainstorming, writing-plans
  Building   test-driven-development, frontend-ui-engineering
  Checking   security-and-hardening, verification-before-completion
```

### Keeping the plan in effect for the whole job

- `save-plan` stores the approved stages and skills for the session.
- On later prompts in that session, the hook adds a short reminder of the plan (a few lines, not a re-scan). Claude invokes each skill with the `Skill` tool when the job reaches its stage.

### Commands

- `/skill-mode [approve|show|silent]` sets and saves the mode. With no argument, it shows the current mode.
- `/autopick <task>` runs this one job in silent mode.
- `/skill-deep-scan [on|off]` turns deep scan on or off and saves the setting. With no argument, it shows the current setting.
- `/skill-pick` re-runs the flow for a new job in the current session, replacing the saved plan and clearing any skip. Ships in the MVP.

### Index

- `pick.py index` builds the SQLite FTS5 index in the plugin's data directory.
- It rebuilds automatically when any skill source changes, detected by a fingerprint of skill directory paths and modification times.
- Ranking uses BM25 over name, description and tags, with the name weighted highest. Candidates are merged across queries.
- Duplicate skill names across sources are kept, each with its own invoke name.
- **Which plugins count as enabled:** `enabledPlugins` is merged with Claude Code's normal precedence. User settings (`~/.claude/settings.json`) are overridden by project settings (`<cwd>/.claude/settings.json`), which are overridden by local settings (`<cwd>/.claude/settings.local.json`). When `installed_plugins.json` lists several installs of one plugin, use the one whose scope matches the active settings layer, falling back to the most recently updated.
- **Global and project skills:** the main index holds user and plugin skills. Project skills are indexed separately, keyed by project path, and merged in at search time, so each project only sees its own.
- **The hook never builds or checks the index.** It only reads the config and the session's state file, so it stays fast. `pick.py search` and `pick.py catalog` check the fingerprint and rebuild first if needed. A first-run build happening inside the search step is acceptable.
- Unreadable or malformed `SKILL.md` files are skipped and logged, never fatal.

### State

- In the plugin data directory:
  - `config.json`: mode, deep scan on or off, deep scan model, and vault path.
  - `index.sqlite`: the search index.
  - per-session plan files keyed by `session_id`, cleaned up after a set age.

### Language and tests

- Python 3, standard library only.
- Automated tests cover: frontmatter parsing, source discovery (including symlinks and disabled plugins), the rebuild check, search ranking, the catalog split (every skill lands in exactly one chunk, chunks roughly equal), merging deep-scan results with search results, and the hook's output for each state (first prompt, plan saved, skipped, each mode, deep scan on and off).

## Left to the builder

- Exact file layout inside the plugin, and the script's command-line interface.
- The wording of the injected context and of the `skill-pick` skill, as long as it produces the behavior above.
- How old a per-session plan file gets before cleanup.
- Hook payload field names and `CLAUDE_PLUGIN_DATA` (or the equivalent): confirm against the current Claude Code documentation before relying on them.
- The candidate count, as long as it stays around 30 total.
