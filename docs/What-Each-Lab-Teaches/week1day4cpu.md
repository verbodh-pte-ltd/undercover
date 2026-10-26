# Week 1 Day 4 Lab, CPU version: Open-Source, Local, Token Cost

Notebook: `labs/week1/week1day4cpu.ipynb`

The same lesson as `week1day4.md`, for a computer without a GPU: cut token cost with a smaller prompt, and see what very small local models can and can't do.

## What's different from the full version

- **Free models in place of paid ones.** It uses `openrouter/free`, which picks a free model for each request, and records which model answered.
- **Handles unreliable free models.** A request can hit a rate limit, return an error, come back empty or run on too long. A helper retries up to three times, and `max_tokens=8000` stops a reply that keeps writing.
- **Three tiny local models on the CPU.** They are Qwen 3.5 0.8B, Gemma 3 1B and IBM's Granite 4 350M, each 1 billion parameters or fewer.
- **Guards against runaway replies.** `num_predict` cuts each local reply at 300 tokens. A cut-off reply or a repeating model counts as "no answer", which is wrong.
- **Measures memory in place of cost.** It records how much memory Ollama uses for each model, which decides whether it fits on your computer. Each uses under 2 GB.
- **Charts accuracy against speed and memory.**

## Why it matters for UnderCover

- It shows the floor: how small a model can get before its answers stop being useful, and what it takes to make free and tiny models reliable enough to measure.

## The assignment

- The same as the full version: shorten the prompt without losing accuracy, and try more models.
