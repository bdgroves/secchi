# How AI is used here, and the standard it follows

*Written 2026-10-21.* Most of secchi's code was written with Claude Code, an
AI coding assistant, working in this repo with Brooks directing,
reviewing and deciding. This page says plainly how that works, what went
wrong, and what caught it. It's also a draft of the standard Brooks would
bring to a GIS team adopting AI-assisted development.

The short version: **AI writes fast; tests and checks decide.** A change is
trusted because a test, a reconcile or a parallel run proved it, not
because of who typed it.

## Who does what

| | Brooks | Claude Code |
|---|---|---|
| What to build, and why | decides | proposes options |
| Code, SQL, docs | reviews, asks for changes | drafts and edits |
| Running things | accounts, passwords, approvals, anything on his own machine he wants to do himself | runs tests and CI, queries the data, reports results |
| Whether it's right | decides, from the evidence | gathers the evidence: test runs, counts, reconcile reports |
| Credentials | types them himself | never sees or handles them |

## The standard

1. **A person owns every change.** AI drafts; a named person reviews it,
   merges it, and answers for it.
2. **Tests decide, not the author.** Every change runs the full test suite
   (201 tests today) before it's committed. Migrations and ports also get a
   parallel run or a reconcile against the original, and a sabotage test:
   break something on purpose and require the check to catch it.
3. **Everything lives in version control**, including the rules the AI
   follows. [CLAUDE.md](../CLAUDE.md) holds this repo's rules, and the AI
   reads it at the start of every session. When a mistake teaches a
   lesson, the lesson becomes a rule there.
4. **Verify against the real thing.** Query the database, count the rows,
   run the check. A log line or a summary isn't evidence, whether a person
   or an AI wrote it.
5. **Restricted data never goes into an AI tool**, and neither do secrets.
   secchi's data is public. On a team handling criminal justice, health or
   personal data, that data stays out of prompts, test copies and logs,
   and the AI works on code, schemas and synthetic samples instead.
6. **Say plainly when something was wrong.** Corrections stay in the
   record, next to the claim they correct, rather than being quietly
   overwritten. Several of secchi's findings were corrected after the
   first real run, and the corrections are part of the story.
7. **Start with low-risk uses**, and widen them as the checks prove
   themselves (below).
8. **Watch the cost**, of the AI tool and of anything it starts in the
   cloud. Measure it; don't estimate it.

## Good first uses for a GIS team

These are low-risk because a person can check the output, and nothing
reaches production without that check:

- **Reading and documenting legacy code** (arcpy scripts, C#, COBOL, SQL
  jobs) while the people who wrote it can still review the write-up. See
  the [SIERRA-FLOW handoff](https://brooksgroves.com/blog/sierra-flow-handoff-post.html).
- **Writing tests** for code that has none, starting with the cases people
  already check by hand.
- **Code review**: a second reader on every change, looking for the
  mistakes a tired person misses.
- **Metadata and data dictionaries** drafted from the actual tables, then
  checked by the data's owner.
- **Migration scripts and reconcile checks**: drafted by AI, proven by the
  reconcile itself ([runbook](cloud/runbook.md)).

Not for, without more safeguards: decisions about people, anything
touching restricted data, publishing without review, or changing
production systems directly.

## What went wrong here, and what caught it

The honest record. Each of these was the AI's mistake or a shared one. The
first was caught too late, and the checks that exist now are the result.

| What happened | What caught it | What changed |
|---|---|---|
| A file rebuilt from an older copy silently dropped a feature: the catchment layer vanished from the live page (2026-09-22, [write-up](bundle-regressions.md)). It happened more than once | at first, nothing: the tests checked the new work, not what was removed | A capability test that lists what must exist, and a rule: edit files in place, never recreate one from an older copy |
| The migration check allowed a tiny rounding difference per row, and a deliberate +1 change slipped through | the sabotage test | The check became exact ([cloud.md](cloud.md), "A correction") |
| Two database passwords were exposed in the session: one showed in a screenshot of a login screen, and a QGIS error message printed the other | noticed the same day | Both rotated. Error output is scrubbed before it's shown, and passwords are typed into a prompt the AI never sees |
| A count of gauge-days came out 2 short | comparing against the Azure copy | The mistake was in the check's time zone, not the data. Both now agree: 322 |

## Why this matters

AI-assisted development is fast enough that the bottleneck moves to
checking. A team that adopts the tools without the checks gets more code
and less trust. A team that keeps the checks gets both: secchi has 12
million readings in Azure, proven to match the source exactly, month by
month, by code that was mostly typed by an AI and entirely checked by tests.
