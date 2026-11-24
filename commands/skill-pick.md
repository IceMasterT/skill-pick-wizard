---
description: Pick skills for a new job in this session, replacing the saved plan and clearing any skip
argument-hint: "[task]"
---

Task: $ARGUMENTS

1. If the task above is empty, the task is the user's most recent request in this conversation before this command. If there is none, reply with one line asking the user to send `/skill-pick <task>`, and stop.
2. Run this command with the Bash tool:

   ```
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/pick.py" reset
   ```

3. Invoke the `skill-pick-wizard:skill-pick` skill with the Skill tool and follow it for the task, with these settings:
   - mode: the saved mode that `status` prints
   - run with: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/pick.py"`
4. Do the task, using the picked skills as the skill says.
