# Week 2 Day 3 Lab: Durable State and Human in the Loop

Notebook: `labs/week2/week2day3.ipynb`

The lab teaches you to build an agent that can stop for a person's approval and carry on hours later, even after a crash, by saving its state in a database after every step.

## What it does

- **Sets up Supabase.** Supabase is a hosted Postgres database. Here it holds the agent's state, and in the product it becomes UnderCover's database. The lab uses the session pooler, which works over IPv4, and keeps its tables in a `lab` schema of their own.
- **Adds Supabase's MCP server to Claude Code.** It's read-only and limited to your project, so Claude Code can look at the tables in the product build.
- **Opens up `create_agent`.** It shows that `create_agent` returns a compiled LangGraph graph, with a model node and a tools node, and draws it as a diagram.
- **Builds the graph by hand.** It covers the three parts of a LangGraph graph: the state, the nodes and the edges, with conditional edges picking the next node. It adds three nodes of its own: decide (a structured decision), approval and pay.
- **Pauses for a handler.** `interrupt()` stops the graph on a payout over $1,000, the approval limit. `Command(resume=...)` carries on with the handler's answer.
- **Shows the re-run trap.** On resume, the interrupted node runs again from the top, so any code before `interrupt()` runs twice. That's why paying is a node of its own.
- **Never pays twice.** The claim id is the payments table's primary key, so a second payment does nothing.
- **Survives a crash.** `PostgresSaver` checkpoints after every step. You restart the kernel on purpose, rebuild the graph, find the paused claim by its thread id, and approve it.
- **Lists the checkpoints** with `get_state_history`, and in Supabase's `checkpoints` table.

## Why it matters for UnderCover

- UnderCover's rules say a payout over $1,000 needs a handler's approval, which can take hours or days. Durable state lets the agent wait and carry on.
- It's the foundation of the Week 2 Day 3 build, which moves the product to Supabase and the claim agent to a LangGraph graph.

## The assignment

- Let the handler reject a payout with a note that goes back to the agent, so it can look at the claim again. Try it on CLM-1011.
- Stretch: on a second computer with the same `DATABASE_URL`, approve a claim that was paused on the first.
