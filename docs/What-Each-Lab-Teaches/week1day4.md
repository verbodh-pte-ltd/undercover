# Week 1 Day 4 Lab: Open-Source, Local, Token Cost

Notebook: `labs/week1/week1day4.ipynb`

The lab teaches you to cut token cost with a smaller prompt, and to weigh open-weight models, hosted or on your own computer, on accuracy, cost and speed.

## What it does

- **Shrinks the prompt in three ways.** It uses a short version of the policy wording, keeps only the sections that matter for each claim type, and sends the policy and claim as compact JSON without ids or document text. It then shows how many tokens that saves against Day 2's prompt.
- **Leaves out the 5 manual claims.** They need the claims manual or the customer's history, which the policy wording alone can't settle. 12 of the remaining 20 are approvals, so approving everything would score 60%.
- **Runs six open-weight models on OpenRouter.** They are MiMo, GLM, Kimi, DeepSeek, Nemotron and Mistral, each at its lowest reasoning level. It records which provider answered, and uses `require_parameters` so only providers that support Structured Outputs are picked.
- **Shows provider quirks.** One GLM provider ignored the low reasoning setting and thought for up to 3,000 tokens, so the lab skips that provider with `ignore`.
- **Runs three models locally with Ollama.** They are Qwen 3.5 4B, Gemma 4 E2B and, optionally, Gemma 4 E4B. Ollama holds the reply to the Pydantic schema, so even small models answer in valid JSON.
- **Calls a provider directly (optional).** It runs Qwen 3.8 27B on Cerebras, and works out the cost from token counts and prices because Cerebras doesn't report it.
- **Charts the trade-offs.** It plots accuracy against cost, accuracy against speed, and all three at once.

## Why it matters for UnderCover

- Weighing accuracy, cost and latency for a business task is a core job of an AI Solutions Architect and FDE.
- Some customers need their data to stay on their own hardware, which is where local models come in.
- A smaller prompt costs less and runs faster, but it can leave out something the model needs, so it has to be measured.

## The assignment

- Find new ways to shorten the prompt that keep the accuracy, and try more models on OpenRouter and locally.
- Stretch: change the Day 3 product to use local models through Ollama.
- Stretch stretch: rent a GPU on RunPod, run a vLLM server and run the product from it.
