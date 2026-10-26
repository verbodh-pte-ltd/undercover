# Week 2 Day 2 Lab: Agentic RAG, MCP and Memory

Notebook: `labs/week2/week2day2.ipynb`

The lab teaches you to give an agent knowledge it doesn't have, through a long-term memory it searches when it chooses to, and to measure whether that memory helps.

## What it does

- **Brings in claims that need outside facts.** It uses 25 "even harder" claims that turn on facts you won't find in the claim, such as when a storm was named, what a device is, or how deep a dive site goes. The facts are in `data/knowledge.json`, along with some no claim needs.
- **Connects a memory over MCP.** MCP is an open standard for connecting agents to tools. Qdrant's MCP server gives the agent two tools: `qdrant-store` saves text in a vector database and `qdrant-find` searches it by meaning.
- **Handles real-world snags.** One cell fixes a Windows error log problem when launching MCP servers. `ToolRetryMiddleware` retries a search that fails because Qdrant's local mode allows only one process at a time. The tools run only asynchronously, so the lab uses async Python.
- **Teaches the agent.** It tells the agent each fact, one message at a time, in fresh conversations. Then it asks again in new conversations, to show the answers come from memory, not from the chat.
- **Shows search by meaning.** A question can find a fact that shares none of its words.
- **Runs two experiments in Langfuse.** It runs the Day 1 agent with the payout tool, then the same agent with read-only `qdrant-find`. That's agentic RAG: the agent decides when to search and what for. A new score, `found_fact`, checks whether its searches brought back the fact each claim needs.

## Why it matters for UnderCover

- Agentic RAG lets you give an agent expertise about the business, and build its knowledge up over time.
- Measuring retrieval separately (`found_fact`) tells you whether a wrong decision came from a failed search or from bad reasoning.

## The assignment

- Replace Qdrant with a simple word lookup over `data/knowledge.json`, run Experiment 2 again, and compare it with searching by meaning.
- Give the agent a memory for each customer: a Markdown file per customer with tools to read and add notes. Think about what should happen to the notes between runs when `N_TIMES` is above 1.
- Stretch: move retrieval into a sub-agent that the UnderCover agent calls as a tool.
