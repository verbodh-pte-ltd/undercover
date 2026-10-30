# Process

`uv sync` and `npm install` at the repo root install everything, including a git pre-commit hook. `npm run dev` starts the backend, the dashboard and the agent's service, with the settings in `.env`. `npm run seed` reseeds the database. On this machine the database is the `undercover-postgres` Docker container, which must be running.

`npm run check` runs every check:

- Python: ruff (lint and format), pyright, and pytest with 80% coverage
- TypeScript: ESLint, Prettier, tsc, and vitest with 80% coverage
- Playwright end-to-end tests, on their own SQLite database, with a stand-in for the agent's service on port 8003

The checks never read `.env`, so they need no database, Langfuse or OpenRouter.

The pre-commit hook runs the format, lint, type and unit test checks on every commit.

## Planning

When asked to plan, as in "Please plan the pilot", write the plan to `docs/PLAN.md` and write no code.

- Split the work into phases, usually 5 or 6, each building on the last.
- Give each phase detailed success criteria that you can verify yourself: tests that pass, commands and their output, API calls and their responses, and pages you check in the browser with the Playwright MCP server. Write each one as a checkbox.
- Then stop and wait until you are asked to implement the plan.

## Implementing

When asked to implement the plan, as in "Please implement the plan for the pilot", build every phase in order without stopping. For each phase:

1. Build it, with tests.
2. Check every success criterion and fix whatever fails.
3. Tick off its criteria in `docs/PLAN.md` and update `docs/STATUS.md`.
4. Commit. The pre-commit hook runs the checks; if it fails, fix the problem and commit again.

When the last phase is done, run `npm run check` and report the results.

## Rules

- Add tests with each change. Tests never call a model; use a stub model.
- Outside a plan, commit each change once you have checked it.
- Run `npm run check` before you hand work over.
- Never use `git commit --no-verify`.
- Never lower a threshold, turn off a rule or exclude a file to get a green run. If a rule looks wrong, stop and ask.
- Keep `docs/STATUS.md` up to date.
- Never push.
- Don't open `data/evals/` unless you're asked to.
- Everything must work on Windows: write `package.json` scripts as `uv run` commands or `.mjs` files.

## Live runs with the real model

Reseed once with `npm run seed`, then use the runs the brief asks for: dispatches started from the queue
page, and evaluations from the Scoreboard page, on the evaluation set or the held-out set's five tiny
claims. These replace the old cap of five claims on a run. A live run calls GPT-6 Luna through OpenRouter
and costs a fraction of a cent per claim.
