# skill-pick-wizard

A Claude Code plugin that picks the best installed skills for each job. When you send a task, a hook asks Claude to search the full descriptions of your installed skills (not just their names), choose 3 to 8 that fit, and then keep using them, stage by stage, until the job is done.

Requires Python 3.10 or newer (standard library only, nothing to install).

## Install

```
claude plugin marketplace add <path-to-clone>
claude plugin install skill-pick-wizard@skill-pick-wizard
```

Or inside Claude Code: `/plugin marketplace add <path-to-clone>`, then `/plugin install skill-pick-wizard@skill-pick-wizard`. `<path-to-clone>` is the folder this README is in.

## Permissions

The flow runs the plugin's CLI with the Bash tool, so Claude Code asks for permission the first time each kind of command runs (`status`, `search`, `save-plan`, ...). With the deep scan on, the helper agents also Read catalog files in the data directory, which is outside your project, so that may prompt too. To allow both for good, add these rules to `permissions.allow` in `~/.claude/settings.json` (or answer "always allow" at the prompt):

```json
{
  "permissions": {
    "allow": [
      "Bash(python3 *skill-pick-wizard*/scripts/pick.py*)",
      "Read(~/.local/share/skill-pick-wizard/**)"
    ]
  }
}
```

If you set `SKILL_PICK_WIZARD_HOME`, use that folder in the `Read` rule instead.

## Modes

| Mode | What happens |
| --- | --- |
| `approve` (default) | Lists the picked skills (one per line, with why) and asks: approve, swap one, add one, or deep scan (type "skip" to skip) |
| `show` | Shows the picked skills and starts without asking |
| `silent` | Picks and starts without showing anything |

Once a plan is saved in approve or show mode, the plugin shows the saved list as a notice (one skill per line, under its stage, with why it fits). Silent mode shows nothing.

`/skill-mode` shows the current mode; `/skill-mode show` (or `approve`, `silent`) changes it.

## Commands

- `/skill-mode [approve|show|silent]`: show or set the mode.
- `/skill-pick [task]`: pick skills for a new job in this session, replacing the saved plan.
- `/autopick [task]`: same, but silent for this job only; the saved mode is unchanged.
- `/skill-deep-scan [on|off]`: show or toggle the deep scan (off by default).

## Deep scan

With the deep scan on, helper agents (default model `sonnet`, 3 chunks) read every installed skill's description and report the ones that fit. It costs about 100,000 tokens per job at about 1,080 skills, so it is off by default. In approve mode you can also run it for one job from the approval question (`Deep scan`) without turning it on.

## Data and configuration

Everything lives in `~/.local/share/skill-pick-wizard` (the skill index, per-session plans, `config.json`, `errors.log`). Set `SKILL_PICK_WIZARD_HOME` to use another location.

`config.json` keys (a missing or invalid value falls back to its default):

| Key | Default | Meaning |
| --- | --- | --- |
| `mode` | `approve` | `approve`, `show` or `silent`; set it with `/skill-mode` |
| `deep_scan` | `false` | `true` turns the deep scan on; set it with `/skill-deep-scan` |
| `deep_scan_model` | `sonnet` | model for the deep scan helper agents (letters, digits and `._:-`, up to 64 characters) |
| `vault_path` | `~/Documents/obsidian-brain/100-tools/Tool Archive` | optional Obsidian vault of skill copies; if the folder exists, its `tool/...` tags are added to the index to improve search, and if it is missing the plugin works without it |

Skills are read from `~/.claude/skills`, the project's `.claude/skills`, and the enabled plugins' `skills` folders.

## Limits (MVP)

- It scans once per job; follow-up messages do not trigger a re-scan (run `/skill-pick` for a new job).
- Built-in skills that have no files on disk are not indexed.
- Skills with `disable-model-invocation: true` are never offered, since Claude cannot invoke them.
- Skills that cannot be read, have no description, or whose folder name or text is not valid UTF-8 are skipped and logged once to `errors.log`.
- It does not install or edit skills.
