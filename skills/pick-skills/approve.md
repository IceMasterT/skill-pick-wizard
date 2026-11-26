# Pick Skills: approve mode

Read this only in approve mode, after showing the list (SKILL.md step 5). `<pick>`, `<cwd>`, the `--session` rule, the list format and the alternatives are as defined in SKILL.md.

## Asking

- Ask each question below with `AskUserQuestion`: one question per call, single select, with the header shown. Every option has the label and description given. "Other" (free text) is added by the tool; never add it yourself.
- **Before asking, count the options.** With 2 to 4, ask. A question with fewer than 2 options is never asked: with exactly 1, take that option as if the user chose it; with none, follow the question's own rule.
- If the user declines or dismisses a question, stop the flow: do not save, do not start the job, and wait for the user's next message.
- If `AskUserQuestion` is not available, or fails with an error that is not the user declining, continue as in show mode: go to SKILL.md step 6 and save with `--mode show`.

## Main question

**Ask** `Use these skills for this job?` (header `Skills`) with these options, in this order:
- `Approve and start`: Save this plan and start the job
- `Swap one`: Replace or drop one skill
- `Add one`: Add a skill by name or by need
- `Deep scan`: Have helper agents read every installed skill, then pick again (about 30 s; type "skip" in Other to skip)

When the deep scan already ran for this list (its header ends in `(deep scan)`), or it was tried this round and could not run, the fourth option is instead:
- `Skip for this session`: Do the job without a skill plan

The question dialog can hide the text above it, so give every option a `preview`: the current list exactly as shown (SKILL.md step 5 format, without the code fence). The user then sees every picked skill while choosing.

Handle the answer:

- **Approve and start:** go to SKILL.md step 6 with the current list.
- **Skip for this session:** run `<pick> skip`, say in one line that skill picking is skipped for this session, then do the job without a plan. SKILL.md steps 6 and 7 do not apply.
- **Swap one:** go to **Swap**.
- **Add one:** go to **Add**.
- **Deep scan:** run SKILL.md step 3 for this job with the same stages; it does not change the saved setting (`/skill-deep-scan on` makes it the default). Add what it finds to each stage's candidates, then redo SKILL.md step 4 over all candidates, but keep every skill the user swapped or added in this approval. Show the new list with `(deep scan)` on its header and ask the main question again. If the deep scan could not run (SKILL.md step 3.6), keep the list as it was and ask the main question again. Save with `--deep-scan` only if it ran.
- **Text typed in "Other":** if it says "skip" (or asks to go without skills), treat it as `Skip for this session`. If it says "deep scan" and the deep scan has not run for this list, treat it as `Deep scan`. If it approves (for example "yes", "go", "looks good"), treat it as `Approve and start`. If it asks for changes (for example "drop X", "use Y instead of X", "add Z"), apply them, resolving any skill it names with **Typed text**. Additions follow the limits of SKILL.md step 4.1 (at most 8 skills, at most 4 per stage): if one would break them, say so in one line and suggest `Swap one` instead. Otherwise answer it in one line.

After every swap, add or change, and after a one-line answer, show the updated list and ask the main question again. The loop has exactly three endings: `Approve and start` (go to SKILL.md step 6), `Skip for this session` or typed "skip" (do the job without a plan), or a declined question (save nothing, start nothing, wait for the user's next message). Every round waits for the user's answer; never answer for them.

## Swap

If the list has no skills, say in one line that there is nothing to swap and ask the main question again.

1. If the list has more than 4 skills, first:

   **Ask** `Which stage?` (header `Stage`) with these options:
   - Up to 4 stages that have skills (label: the stage, description: its skills)

   Text typed in "Other" names a stage or a skill: match it to any stage in the list, including one not shown as an option, and continue with that stage. If it names a skill in the list instead, that skill is the one to replace: go to step 3. If nothing matches, say so in one line and ask the main question again.

2. Then:

   **Ask** `Which skill should be replaced?` (header `Swap`) with these options:
   - Up to 4 skills (label: the invoke, description: its stage): every skill in the list when it has 4 or fewer, otherwise the chosen stage's skills, its first 4 if it has more

   Text typed in "Other" names the skill to replace: match it to a skill in the list. If none matches, say so in one line and ask the main question again.

3. Then, for the chosen skill:

   **Ask** `Replace <invoke> with?` (header `Replace`) with these options:
   - Up to 3 of that stage's alternatives (label: the invoke, description: a short version of what it does)
   - `Keep it`: Leave this skill in the plan (or type "drop" in "Other" to remove it)

   Text typed in "Other": if it asks to remove or drop the skill, drop it. Otherwise resolve it with **Typed text**. If the stage has no alternatives left, the only option is `Keep it`, so the question is not asked: say in one line that this stage has no alternatives left and that the user can, in "Other" on the main question, name a skill to use instead or ask to drop this one.

4. A chosen alternative takes the replaced skill's place. A dropped skill leaves the list, and its stage too if that leaves the stage empty. Either way the old skill becomes one of the stage's alternatives again. `Keep it` changes nothing. Then ask the main question again.

With a single skill in the list or in the chosen stage, the skill question has 1 option and is not asked: that skill is the one to replace.

## Add

If the list already has 8 skills, do not ask: say in one line that the plan is full and that `Swap one` can replace a skill, then ask the main question again.

**Ask** `Which skill should be added?` (header `Add`) with these options:
- Up to 3 of the best-fitting alternatives across all stages (label: the invoke, description: its stage and a short version of what it does)
- `Cancel`: Add nothing

Text typed in "Other" is a skill name or a need: resolve it with **Typed text**. Add the chosen skill to the stage it fits best; it stops being an alternative. If that stage already has 4 skills, say in one line that the stage is full, then ask `Which skill should be replaced?` (see **Swap**) with that stage's skills as the options: the chosen one is replaced by the new skill and becomes an alternative. `Cancel` changes nothing. If no alternatives are left, the only option is `Cancel`, so the question is not asked: say in one line that the user can name a skill or a need in "Other" on the main question. Then ask the main question again.

## Typed text

A skill name or a need the user typed:

1. If it equals an invoke among the candidates, use that skill.
2. Otherwise run `<pick> search --cwd '<cwd>' -- '<their text>'`. If it prints `no matches`, say so in one line, change nothing, and ask the main question again.
3. Otherwise:

   **Ask** `Did you mean?` (header `Match`) with these options:
   - Up to 3 of the top search results (label: the invoke, description: a short version of its description)
   - `None of these`: Change nothing

   A chosen result is the skill. `None of these` changes nothing; ask the main question again. Text typed in "Other" here is resolved again with **Typed text**.
