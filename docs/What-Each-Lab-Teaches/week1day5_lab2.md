# Week 1 Day 5 Lab 2: Harness Engineering with Deep Agents

Notebook: `labs/week1/week1day5_lab2.ipynb`

The lab teaches you how an open-source harness is put together, by giving the same job as Lab 1 to LangChain's Deep Agents on a cheap model through OpenRouter.

## What it does

- **Uses the same claims desk as Lab 1.** Five claims, the policy wording and the claims manual, with the instruction to decide every claim. No tools are written by you.
- **Shows the harness is middleware.** Deep Agents is `create_agent` from Day 2 with middleware added. That gives it file tools, a subagent, a to-do list from `TodoListMiddleware` and summarizing of long conversations.
- **Fences the agent in.** `FilesystemBackend` with `virtual_mode=True` shows the desk to the agent as `/`, so it can't reach anything outside it.
- **Runs on any model.** It uses DeepSeek V4.1 Flash, about 3 cents for five claims, against about 40 cents for Lab 1 on Claude Sonnet.
- **Streams each step,** counting model calls and cost as it goes. It then scores the decisions with the same test as Lab 1 and shows the handover note.

## Why it matters for UnderCover

- Deep Agents is open source and runs on any model, so you can pick a cheap one and read exactly what the harness does.
- It previews the Week 1 Day 5 build. That build adds the same pieces to the UnderCover Agent: a to-do list, tools to read the manual, and a subagent for each claim.

## The assignment

- Find where the agent used its to-do list and where it handed work to the subagent.
- Try other models and compare accuracy, cost and time, such as GPT-6 Luna, or Claude Sonnet 5.5 to compare with Lab 1 on the same model.
