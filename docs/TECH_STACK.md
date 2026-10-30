# Tech stack

- `backend/`: FastAPI and SQLAlchemy Core. With `DATABASE_URL` set it uses Postgres (on this machine, local Postgres in Docker, not Supabase) in the `undercover` schema, with at most 4 connections; without it, SQLite, which the tests use. Every API operation has an `operation_id`, a one-line docstring and no optional inputs. Paying a claim is a separate operation that takes the amount. The payout rules are `backend/payout.py`, behind `work_out_payout`.
- `agent/`: two agents on OpenRouter. The queue agent is LangChain's `create_agent`: it works a run up to a cap, keeps a to-do list in the backend and hands each claim to the claim agent through its `work_claim` tool. The claim agent is a hand-written LangGraph graph that waits for a handler's sign-off with `interrupt()` and pays as a step of its own. Both save their progress after every step with LangGraph's synchronous `PostgresSaver` in the same database. Failed model calls are retried. Each claim is traced in Langfuse, in a session named after its thread.
- `agent/service.py`: the agent's own small FastAPI service on port 8002 (`UNDERCOVER_AGENT`). The backend calls it after each sign-off, and it picks that claim back up. Both agents reach claims, documents and their instructions only through the backend's API. They use `deepseek/deepseek-v4.1-flash` with low reasoning effort by default, or the model in the environment variable `MODEL_NAME` if set, such as `openrouter/free`.
- `dashboard/`: React, Vite and TypeScript.
- `e2e/`: Playwright tests.
- `data/`: the business data the backend starts from. The agent never sees `data/evals/`, which holds the answer key.

Python 3.12 with uv, all in the root `pyproject.toml`. Node 26. The OpenRouter key is `OPENROUTER_API_KEY` in `.env`.
