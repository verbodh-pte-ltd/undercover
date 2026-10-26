# Process

`uv sync` and `npm install` at the repo root install everything, including a git pre-commit hook. `npm run dev` starts the backend and the dashboard.

`npm run check` runs every check:

- Python: ruff (lint and format), pyright, and pytest with 80% coverage
- TypeScript: ESLint, Prettier, tsc, and vitest with 80% coverage
- Playwright end-to-end tests

The pre-commit hook runs the format, lint, type and unit test checks on every commit.

## Planning

When asked to plan, as in "Please plan the pilot", write the plan to `docs/PLAN.md` and write no code.

- Split the work into phases, usually 5 or 6, each building on the last.
- Say where the new code goes: which modules, and what each is for. Check that layout against `docs/CODE_STANDARDS.md` (one client per service, tools in `agent/tools.py`, the harness in `agent/harness.py`, business rules in the backend), and check what the framework already provides before planning code of your own.
- Describe agent instructions by their goal and rules, never as a script of steps.
- Give each phase detailed success criteria that you can verify yourself: tests that pass, commands and their output, API calls and their responses, and pages you check in the browser with the Playwright MCP server. Write each one as a checkbox.
- Then stop and wait until you are asked to implement the plan.

## Implementing

When asked to implement the plan, as in "Please implement the plan for the pilot", build every phase in order without stopping. For each phase:

1. Build it, with tests.
2. Check every success criterion and fix whatever fails.
3. Review the phase's code before you commit it, as a reviewer who did not write it would. Read the whole diff (`git diff`), not just the tests' results, and check it against every point in `docs/CODE_STANDARDS.md`. Fix what fails. Passing tests show the code works, not that it is well built.
4. Tick off its criteria in `docs/PLAN.md` and update `docs/STATUS.md`.
5. Commit. The pre-commit hook runs the checks; if it fails, fix the problem and commit again.

When the last phase is done, review the whole build the same way: read every module it changed end to end, as a newcomer would, and fix anything that is hard to follow. Then run `npm run check` and report the results, including what the review found and fixed.

When a build extends earlier code, the earlier code meets the standards too. Fix a shortcut you find, such as a hard-coded address or a second way to call a service, rather than building on it.

## Fixing bugs

When something fails, find the root cause before you change any code:

1. Reproduce it with a failing test or a command, and note the exact error.
2. Prove the cause with evidence, such as a log line, a value or a stack trace. Don't guess.
3. Fix the cause, not the symptom. No workarounds, retries or broad exception handlers to hide it.
4. Check the failing test now passes, run `npm run check`, and commit the test with the fix.
5. If you can't prove the cause after two attempts, stop and report what you tried.

## Rules

- Add tests with each change. Tests never call a model; use a stub model.
- Outside a plan, commit each change once you have checked it.
- Run `npm run check` before you hand work over.
- A green `npm run check` is necessary but not enough: work is done only once it has passed the review against `docs/CODE_STANDARDS.md`.
- For any live run with the real model, reseed first and cap the run at five claims. A change to an agent's instructions is checked with a live run.
- Never use `git commit --no-verify`.
- Never lower a threshold, turn off a rule or exclude a file to get a green run. If a rule looks wrong, stop and ask.
- Keep `docs/STATUS.md` up to date.
- Never push.
- Don't open `data/evals/` unless you're asked to.
- Everything must work on Windows: write `package.json` scripts as `uv run` commands or `.mjs` files.
