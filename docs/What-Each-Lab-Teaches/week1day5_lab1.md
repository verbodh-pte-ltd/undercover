# Week 1 Day 5 Lab 1: Harness Engineering with the Claude Agent SDK

Notebook: `labs/week1/week1day5_lab1.ipynb`

The lab teaches you what a ready-made agent harness gives you, by letting the Claude Agent SDK work a queue of claims on its own, with no tools written by you.

## What it does

- **Sets up a claims desk.** It gives the agent a folder holding five claims, every policy and customer, the policy wording, the claims manual and the company's limits. The handlers' decisions stay out of the folder and are used only to score it at the end.
- **Picks the harness's built-in tools.** The agent can read, search and write files, keep a task list, and hand work to a subagent. The shell and the web are left out.
- **Keeps the run clean and capped.** It keeps your own Claude Code settings, memory and connectors out of the run, and `max_budget_usd` stops it above a dollar.
- **Works around Windows.** Inside Jupyter on Windows, the SDK can't start Claude Code, so one cell gives it an event loop in a background thread.
- **Prints each step as it works,** with the subagent's steps indented. It then scores the decision files against the handlers and shows the note the agent left for the handler.

## Why it matters for UnderCover

- It's the build-or-buy question. A ready-made harness gets you a capable agent with very little code. But this one runs only Claude models and starts a Claude Code program for every run.
- Weighing that for a customer is a big part of an AI Solutions Architect's job.

## The assignment

- Find where the agent used its task list and where it handed work to the subagent.
- Make one change and see what it does to accuracy and cost: a different effort level, a different model such as Claude Haiku, or new instructions.
