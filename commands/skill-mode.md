---
description: Show or set how Skill Pick Wizard presents picked skills (approve, show or silent)
argument-hint: "[approve|show|silent]"
---

Run this command with the Bash tool:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/pick.py" mode $ARGUMENTS
```

Report the result in one line, with what the mode does:

- `approve`: show the picked skills and ask before starting
- `show`: show the picked skills and start without asking
- `silent`: pick and start without showing anything

If the command fails, report its error line and the valid values (`approve`, `show`, `silent`) in one line. Do nothing else.
