---
description: Show or turn on or off the Skill Pick Wizard deep scan, which has helper agents read every installed skill's description
argument-hint: "[on|off]"
---

Run this command with the Bash tool:

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/pick.py" deep-scan $ARGUMENTS
```

Report the result in one line (for example `Deep scan: on`). If the command fails, report its error line and the valid values (`on`, `off`) in one line. Do nothing else.
