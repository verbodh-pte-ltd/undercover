1. Call read_results to see how the current version is doing and which evaluation claims it got wrong or explained poorly.
2. For each of those claims, add a rule to the instructions that would make the agent get that claim right.
3. Call propose_change with the new instructions, keeping the same model and reasoning level.
4. Call run_evaluation to test the new version on both sets.
5. Improve this recipe, so that the next round makes a better change. Look at what happened in this round and in earlier rounds, then call propose_recipe with the new recipe. Keep this as the last step of the new recipe.
