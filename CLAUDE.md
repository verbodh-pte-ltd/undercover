# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

@AGENTS.md

The course context, the build-day workflow, the branch rules and the list of `docs/` files to read
before any product work are in `AGENTS.md` above. This file adds the commands and the architecture.

## Commands

Run everything from the repo root. Use `uv run`, never `python3` (`docs/CODE_STANDARDS.md`).

```bash
uv sync && npm install      # installs Python, Node, the lefthook pre-commit hook and Playwright's Chromium
npm run dev                 # backend on :8000 (uvicorn --reload) and dashboard on :5173 (Vite)
npm run check               # every check: check:python, check:ts, then e2e
npm run check:python        # ruff format --check, ruff check, pyright, pytest (80% coverage)
npm run check:ts            # prettier, eslint, tsc, vitest with coverage (80% statements)
npm run e2e                 # Playwright; starts backend and dashboard itself, or reuses running ones
npm run format              # ruff format and prettier --write
uv run monitor.py           # AWS spend, for Week 3 labs
```

A single test:

```bash
uv run pytest agent/tests/test_agent.py::test_run -q --no-cov
npx vitest run --root dashboard src/App.test.tsx
npx playwright test e2e/health.spec.ts
```

`pytest` adds `--cov` through `addopts` in `pyproject.toml`, and the 80% `fail_under` makes a
single-file run fail on coverage; pass `--no-cov` when running one test.

## Architecture

- **Three parts, one direction of calls.** The dashboard (React 19, Vite, TypeScript) calls the
  backend through Vite's `/api` proxy to `127.0.0.1:8000`. The agent (`agent/main.py`) also reaches
  claims and its instructions only through the backend's HTTP API, never the database or `data/`.
- **Backend** (`backend/main.py`): FastAPI, with SQLite as the planned store. Every route has an
  `operation_id`, a one-line docstring and no optional inputs, because those become the agent's tool
  names and descriptions. Paying a claim is its own operation that takes the amount.
- **Agent**: LangChain `create_agent` on OpenRouter through `ChatOpenAI`, as two agents.
  `run_queue(cap, llm=None, claim_llm=None)` runs the queue agent; its `work_claim` tool calls
  `work_claim(claim_id, run_id, llm=None)`, which starts the claim and runs a fresh claim agent on it.
  The model is `MODEL_NAME` if set (for example `openrouter/free`), else `deepseek/deepseek-v4.1-flash`
  with low reasoning effort. Needs `OPENROUTER_API_KEY` in `.env`.
- **Test pattern.** Tests never call a model. `agent/tests/test_agent.py` shows the pattern to copy:
  a `GenericFakeChatModel` subclass fed a `Script` of `AIMessage` replies (an exception in the script
  is raised as a model error), and `main.backend` monkeypatched to a FastAPI `TestClient(app)` so tools
  hit the real app in process.
- **Data.** `data/` is the business data the backend loads from. `data/evals/` is the answer key:
  the agent must never see it, and do not open it unless asked. `context/` is used from Week 3 Day 3.
- **Python layout.** One root `pyproject.toml` for `backend/`, `agent/` and the notebooks in `labs/`.
  Ruff, pyright and coverage cover only `backend/` and `agent/`.

## Rules that are easy to miss

- The pre-commit hook (`lefthook.yml`) formats staged files, then runs `check:python` or
  `check:ts` for the part that changed. Never `--no-verify`. Never lower a threshold, turn off a
  rule or exclude a file to get green; stop and ask instead.
- Never push. Commit each checked change.
- Keep `docs/STATUS.md` current; it is the record of what is built and how to run it.
- Everything must run on Windows: `package.json` scripts are `uv run` commands or `.mjs` files.
- TypeScript is strict; never `any`.
