# Week 1 Day 2 Lab: Introducing UnderCover

Notebook: `labs/week1/week1day2.ipynb`

The lab teaches you to put LLMs and an agent on a real business task, and to judge them on accuracy, cost and speed together.

## What it does

- **Explores the data first.** It loads the queue of 25 claims and the handlers' decisions, and charts them by claim type and by difficulty (routine, hard and manual).
- **Gets structured answers from a model.** It uses Structured Outputs with a Pydantic class, so each model replies with a decision, a rationale and a payout. The rationale comes before the decision, so the model reasons before it decides.
- **Compares models.** It runs GPT-6 Luna at low and medium reasoning and Gemini 3.5 Flash Lite, scores each against the handlers, and shows where each one went wrong.
- **Prices each run.** It puts claims right, cost and time for the whole queue side by side.
- **Tries a decision model (optional).** Jev returns a probability for each decision in a fraction of a second, but no rationale or payout. Its confident answers could be kept, with the rest sent to a stronger model.
- **Builds a first agent.** LangChain's `create_agent` runs an LLM with tools in a loop. Its two tools look up the policy and record the decision, so you can compare the agent with a single model call.

## Why it matters for UnderCover

- Accuracy, cost and speed are the numbers a customer asks about. Cost per claim is easiest to compare with what a handler's time costs.
- The kind of mistake matters too: a wrong approval pays money the policy doesn't owe, a wrong decline lets a customer down, and a wrong referral costs a handler's time.
- The tool-using agent is the starting point for the UnderCover Agent built on Day 3.

## The assignment

- Using the agent, find the cheapest model that gets at least 15 of the 25 claims right, consistently.
