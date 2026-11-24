# Skill Pick: approve mode

Read this only in approve mode, after showing the list (SKILL.md step 5). `<pick>`, `<cwd>`, the `--session` rule, the list format and the alternatives are as defined in SKILL.md.

## Asking

- Ask each question below with `AskUserQuestion`: one question per call, single select, with the header shown. Every option has the label and description given. "Other" (free text) is added by the tool; never add it yourself.
- **Before asking, count the options.** With 2 to 4, ask. A question with fewer than 2 options is never asked: with exactly 1, take that option as if the user chose it; with none, follow the question's own rule.
- If the user declines or dismisses a question, stop the flow: do not save, do not start the job, and wait for the user's next message.
- If `AskUserQuestion` is not available, or fails with an error that is not the user declining, continue as in show mode: go to SKILL.md step 6 and save with `--mode show`.

## Main question

**Ask** `Use these skills for this job?` (header `Skills`) with these options, in this order:
- `Approve and start`: Save this plan and start the job
- `Swap one`: Replace one skill with an alternative
- `Add one`: Add a skill by name or by need
- `Skip for this session`: Do the job without a skill plan

Handle the answer:

- **Approve and start:** go to SKILL.md step 6 with the current list.
- **Skip for this session:** run `<pick> skip`, say in one line that skill picking is skipped for this session, then do the job without a plan. SKILL.md steps 6 and 7 do not apply.
- **Swap one:** go to **Swap**.
- **Add one:** go to **Add**.
- **Text typed in "Other":** if it approves (for example "yes", "go", "looks good"), treat it as `Approve and start`. If it asks for changes (for example "drop X", "use Y instead of X", "add Z"), apply them, resolving any skill it names with **Typed text**. Otherwise answer it in one line.

After every swap, add or change, and after a one-line answer, show the updated list and ask the main question again. Only `Approve and start`, `Skip for this session` or a declined question ends the loop. Every round waits for the user's answer; never answer for them.

## Swap

If the list has no skills, say in one line that there is nothing to swap and ask the main question again.

1. If the list has more than 4 skills, first:

   **Ask** `Which stage?` (header `Stage`) with these options:
   - Up to 4 stages that have skills (label: the stage, description: its skills)

2. Then:

   **Ask** `Which skill should be replaced?` (header `Swap`) with these options:
   - Up to 4 skills (label: the invoke, description: its stage): every skill in the list when it has 4 or fewer, otherwise the chosen stage's skills, its first 4 if it has more

   Text typed in "Other" names the skill to replace: match it to a skill in the list. If none matches, say so in one line and ask the main question again.

3. Then, for the chosen skill:

   **Ask** `Replace <invoke> with?` (header `Replace`) with these options:
   - Up to 3 of that stage's alternatives (label: the invoke, description: a short version of what it does)
   - `Keep it`: Leave this skill in the plan

   Text typed in "Other": resolve it with **Typed text**. If the stage has no alternatives left, the only option is `Keep it`, so the question is not asked: say in one line that this stage has no alternatives left and that the user can name a skill in "Other" on the main question.

4. A chosen alternative takes the replaced skill's place, and the replaced skill becomes one of the stage's alternatives. `Keep it` changes nothing. Then ask the main question again.

With a single skill in the list or in the chosen stage, the skill question has 1 option and is not asked: that skill is the one to replace.

## Add

**Ask** `Which skill should be added?` (header `Add`) with these options:
- Up to 3 of the best-fitting alternatives across all stages (label: the invoke, description: its stage and a short version of what it does)
- `Cancel`: Add nothing

Text typed in "Other" is a skill name or a need: resolve it with **Typed text**. Add the chosen skill to the stage it fits best; it stops being an alternative. `Cancel` changes nothing. If no alternatives are left, the only option is `Cancel`, so the question is not asked: say in one line that the user can name a skill or a need in "Other" on the main question. Then ask the main question again.

## Typed text

A skill name or a need the user typed:

1. If it equals an invoke among the candidates, use that skill.
2. Otherwise run `<pick> search --cwd '<cwd>' -- '<their text>'`. If it prints `no matches`, say so in one line, change nothing, and ask the main question again.
3. Otherwise:

   **Ask** `Did you mean?` (header `Match`) with these options:
   - Up to 3 of the top search results (label: the invoke, description: a short version of its description)
   - `None of these`: Change nothing

   A chosen result is the skill. `None of these` changes nothing; ask the main question again. Text typed in "Other" here is resolved again with **Typed text**.
