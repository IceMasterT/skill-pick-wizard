---
description: Pick skills for one task without asking (silent mode for this job only), then do the task
argument-hint: "<task>"
---

Task: $ARGUMENTS

1. If the task above is empty, reply with one line asking the user to send `/autopick <task>`, and stop.
2. Run this command with the Bash tool:

   ```
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/pick.py" reset
   ```

3. Invoke the `skill-pick-wizard:pick-skills` skill with the Skill tool and follow it for the task above, with these settings:
   - mode: `silent` for this job only (save the plan with `--mode silent`; do not change the saved mode)
   - run with: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/pick.py"`
4. Do the task, using the picked skills as the skill says.
