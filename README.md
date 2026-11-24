# skill-pick-wizard

A Claude Code plugin that picks the best installed skills for each job. When you send a task, a hook asks Claude to search the full descriptions of your installed skills (not just their names), choose 3 to 8 that fit, and then keep using them, stage by stage, until the job is done.

Requires Python 3.10 or newer (standard library only, nothing to install).

## Install

```
claude plugin marketplace add /home/artiq/Desktop/skill-pick-wizard
claude plugin install skill-pick-wizard@skill-pick-wizard
```

Or inside Claude Code: `/plugin marketplace add /home/artiq/Desktop/skill-pick-wizard`, then `/plugin install skill-pick-wizard@skill-pick-wizard`.

## Modes

| Mode | What happens |
| --- | --- |
| `approve` (default) | Shows the picked skills and asks before starting |
| `show` | Shows the picked skills and starts without asking |
| `silent` | Picks and starts without showing anything |

`/skill-mode` shows the current mode; `/skill-mode show` (or `approve`, `silent`) changes it.

## Commands

- `/skill-mode [approve|show|silent]`: show or set the mode.
- `/skill-pick [task]`: pick skills for a new job in this session, replacing the saved plan.
- `/autopick [task]`: same, but silent for this job only; the saved mode is unchanged.
- `/skill-deep-scan [on|off]`: show or toggle the deep scan (off by default).

## Deep scan

With the deep scan on, helper agents (default model `sonnet`, 3 chunks) read every installed skill's description and report the ones that fit. It costs about 100,000 tokens per job at about 1,080 skills, so it is off by default.

## Data and configuration

Everything lives in `~/.local/share/skill-pick-wizard` (the skill index, per-session plans, `config.json`, `errors.log`). Set `SKILL_PICK_WIZARD_HOME` to use another location.

`config.json` has a `vault_path` setting (default `~/Documents/obsidian-brain/100-tools/Tool Archive`). If that folder exists, its `tool/...` tags are added to the index to improve search; if it is missing, the plugin works without it.

Skills are read from `~/.claude/skills`, the project's `.claude/skills`, and the enabled plugins' `skills` folders.

## Limits (MVP)

- It scans once per job; follow-up messages do not trigger a re-scan (run `/skill-pick` for a new job).
- Built-in skills that have no files on disk are not indexed.
- It does not install or edit skills.
