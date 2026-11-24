---
name: pick-skills
description: Use when the Skill Pick Wizard hook says a skill scan is pending, or when /autopick or /skill-pick runs. Picks the installed skills that fit the job by their full descriptions, asks for approval when the mode says so, saves the plan, and starts the job with the first stage's skills.
---

# Skill Pick

Most installed skills are visible to you only by name. This flow finds the ones that fit the job by their full descriptions, gets them approved when the mode asks for it, and makes sure you actually use them for the whole job.

Follow the steps in order. Work through steps 0 to 6 without commentary: the user should see only the list, the questions and the one-line notices named below. Do none of the job's own work before step 7.

## 0. Command

1. `<pick>` below stands for one command:
   - the `run with:` command from the Skill Pick Wizard hook context, or from the slash command that sent you here, copied exactly;
   - otherwise `python3 "<base directory>/../../scripts/pick.py"`, where `<base directory>` is this skill's "Base directory for this skill".
2. If the hook context has a `session:` line, add `--session <that id>` to every `status`, `catalog`, `save-plan` and `skip` command. Otherwise leave it out; the CLI reads the session from the environment.
3. `<cwd>` is the project directory: this session's primary working directory.
4. Run `<pick> status`. Note `mode`, `deep_scan` and `deep_scan_model`.
5. The mode for this job is the mode the invoking command gave (`/autopick` means `silent`). Otherwise it is `mode` from `status`. A command's mode applies to this job only; never change the saved mode.

If `status` or `search` fails (it prints an error line instead of its normal output), say in one line: `Skill scan failed (<error>).` Then run `<pick> skip`, so the hook does not start a new scan on every message, and do the job without a plan. The rest of this flow does not apply.

**Quoting.** Put every free-text argument (each query, the job summary, the plan JSON) in single quotes. Keep that text free of single quotes, backticks, `$` and backslashes by rewording it. If a value cannot avoid a single quote (for example a skill name), write that quote as `'\''`.

## 1. Stages

1. Break the job into 2 to 4 stages in work order (for example Planning, Building, Checking). Give each stage a label of one or two plain words.
2. Write at most 6 queries in total, 1 or 2 per stage.
3. Write each query the way a skill description would describe that work, in a few words: `harden code against vulnerabilities`, `verify work before claiming it is done`, `write the failing test first`, `add stripe checkout to a react site`. Never use a bare category word such as `security`, `review` or `testing`: it matches hundreds of skills and buries the ones that fit.
4. Put the stack and domain the job names into the queries (for example `stripe`, `react`, `postgres`).

## 2. Search

1. Run every query in one command:

   ```
   <pick> search --cwd '<cwd>' -- '<query 1>' '<query 2>' '<query 3>'
   ```

   When `status` printed `deep_scan: on`, send this command and the step 3 `catalog` command as two Bash calls in the same message; both need only the stages.
2. It prints up to 30 candidates, each query's best matches first. Each is a line `` `<invoke>`  [<tags>]  (matched: <queries>) `` followed by the skill's full description. The first run can take a few seconds while the index builds.
3. If it prints `no matches`, or only skills unrelated to the job, rewrite the queries once, describing the same work in other words (synonyms, or the stack's own terms), and search again.

## 3. Deep scan (only when `deep_scan: on`)

Skip this step when `status` printed `deep_scan: off`.

1. The catalog command, sent together with the search (step 2):

   ```
   <pick> catalog --cwd '<cwd>'
   ```

   It prints one chunk file path per line.
2. In ONE message, make one Agent call per printed path, so all of them run in parallel:
   - `subagent_type`: `skill-pick-wizard:skill-deep-scanner`
   - `model`: the `deep_scan_model` value from `status`
   - `description`: `Deep scan chunk <n>`
   - `prompt`:

     ```
     Chunk file: <path>
     Job: <the job in one or two sentences>
     Stages:
     - <stage label 1>
     - <stage label 2>
     ```

3. If the Agent tool rejects the `model` value, make the same calls again in one message without `model`.
4. Each agent answers with lines `` <stage> | `<invoke>` | <reason> `` or `<stage> | NONE`. Add each invoke from those lines to the candidates of that stage, together with its reason line. Ignore `NONE` lines and lines in any other form.
5. An agent failed if it returned an error or no line in either form. If some agents failed, say in one line: `Deep scan was partial (<n> of <total> chunks).`
6. If every agent failed, or `catalog` failed, say in one line that the deep scan could not run, and continue with the search candidates as if deep scan were off: no `(deep scan)` in the header and no `--deep-scan` when saving.

## 4. Choose

1. Pick 3 to 8 skills in total, grouped by stage, at most 4 per stage. Use only invokes that appeared in the candidates, copied exactly.
2. Choose by what each skill does: a skill goes in a stage only if it does the work of that stage. For search candidates, judge by the full description. For candidates that only the deep scan found, judge by the agent's reason line, since their full description is not in front of you.
3. Prefer a skill made for the job's stack or domain over a generic one. Never pick two skills that do the same thing. General workflow skills (planning, test-driven development, debugging, verification, code review) count when the job includes that work.
4. Give every stage at least one skill. Drop a stage that no candidate fits.
5. Every unpicked candidate is an alternative. For each stage, keep the next 3 best-fitting unpicked candidates as its swap alternatives.
6. Write the job summary: the job in at most 10 words, in the user's terms.
7. If no candidate fits the job at all, say so in one line, run `<pick> skip`, and do the job without a plan. Steps 5 to 7 do not apply.

## 5. Act by mode

The list is a code block in exactly this format, stage labels padded to one column:

```
Skills for: "<job summary>"
  Planning   brainstorming, writing-plans
  Building   test-driven-development, frontend-ui-engineering
```

Append ` (deep scan)` to the header line when the deep scan ran: `Skills for: "<job summary>" (deep scan)`.

- **silent:** show nothing except the one-line notices from steps 3 and 6, and the list only if step 6.7 needs it. Go to step 6.
- **show:** show the list, then go straight to step 6 without asking.
- **approve:** show the list, then Read `<base directory>/approve.md` with the Read tool and follow it. It ends in one of three ways: at step 6 with the approved list (saved with `--mode show` if the question tool was unavailable); with the job done without a plan (the user chose Skip; steps 6 and 7 do not apply); or stopped because the user declined a question (save nothing, start nothing, wait for the user's next message).

## 6. Save

1. If the final list has no skills (the user removed them all), run `<pick> skip` and do the job without a plan. The rest of steps 6 and 7 do not apply.
2. Build the plan JSON from the final list, stages in work order, leaving out empty stages:

   ```
   [{"stage": "Planning", "skills": ["brainstorming", "writing-plans"]}, {"stage": "Building", "skills": ["test-driven-development"]}]
   ```

3. Run:

   ```
   <pick> save-plan --cwd '<cwd>' --job '<job summary>' --plan '<plan JSON>' --mode <mode used>
   ```

   `<mode used>` is `approve`, `show` or `silent`. Add `--deep-scan` when the deep scan ran.
4. It prints `saved plan: <n> stages` on success.
5. If it prints `unknown skills: <list>`, replace each listed skill with an alternative of the same stage that came from the search output (those are always known), or drop it, and run `save-plan` again. Tell the user in one line only if the approved list changed.
6. If it prints an error starting with `invalid --plan`, rebuild the JSON to the shape in item 2 (a non-empty list; each item has a non-empty `"stage"` string and a non-empty `"skills"` list of strings), check the quoting, and run `save-plan` again.
7. Retry at most twice in total (items 5 and 6 together). If it still fails, or it fails with any other error, the plan is not saved:
   1. Run `<pick> skip`, so the hook does not start a new skill scan on every message.
   2. If the skip worked, say in one line: `Skill plan not saved (<error>). Later messages will not show it as a reminder; run /skill-pick to try again.`
   3. If `<pick> skip` fails too, say in one line: `Skill plan not saved (<error>). Skill picking may start again on your next message.`
   4. In silent mode, also show the list once (step 5 format), so the plan stays in this conversation.
   5. Go on to step 7 with the final list.

## 7. Start the job

1. Invoke each skill of the first stage now with the Skill tool, one call per skill, using the invoke exactly as the skill name. Follow what each one says.
2. Do the job. When the work reaches a later stage, invoke that stage's skills with the Skill tool before doing its work. Do not invoke later stages' skills up front.
3. Invoke every planned skill when its stage starts, even if the work looks simple. Once invoked, a skill stays in effect for the rest of the job; you do not need to invoke it again. If a skill's instructions conflict with the user's request, the user's request wins.
4. If a Skill call fails, say so in one line and carry on with the rest of the plan.
5. Do not run this flow again for this job on your own. If the plan was saved, the hook shows it as a reminder on later messages. If it was not saved (step 6.7), the list shown in this conversation is the plan. Either way, keep following it until the job is done.
