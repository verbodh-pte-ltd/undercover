# Week 1 Day 3: the pilot, with Run and Stop in the dashboard and an Insights page

Darko Martinovic

My build of the claims pilot from `docs/SPEC.md`, made with Claude Code, and an Insights page I added after it. The plan I built the pilot from is next to this file as `PLAN.md`. The code is in my fork:

- The pilot as delivered: [week1day3-end](https://github.com/Darko-Martinovic/undercover/tree/week1day3-end)
- Insights, built after the pilot: [docs/PLAN_INSIGHTS.md](https://github.com/Darko-Martinovic/undercover/blob/main/docs/PLAN_INSIGHTS.md), in PRs [#12](https://github.com/Darko-Martinovic/undercover/pull/12), [#13](https://github.com/Darko-Martinovic/undercover/pull/13) and [#14](https://github.com/Darko-Martinovic/undercover/pull/14)

![Insights after a run of the queue](https://raw.githubusercontent.com/Darko-Martinovic/undercover/e9293cdde478f156745897a1f36a2cfa9bdbce8a/assets/insights.png)

## How it differs from Ed's `week1day3-end`

| | Ed's build | Mine |
|---|---|---|
| Running the agent | Command line | Command line, and Run queue, Run claim and Stop in the dashboard. The agent runs as a separate process, one run at a time. Stop ends it at once and puts the claim back to `waiting`, keeping its steps |
| Referrals | A referral is final (`decided`) | A fifth state, `referred`, and a form where a handler approves or declines the claim. The spec names four states, so this one is my addition |
| Queue | Counts by state and a line per claim | Tiles per state that filter the table, money totals, search, a type filter and sortable columns |
| After a run | Each decision printed, then a count | Insights, below |
| API | 13 operations | 20, with a Postman collection that runs through the workflow and starts and stops the agent |

Ed's build does two things mine didn't until Insights: a run of all 25 claims, and an end-to-end test of the agent's steps on a claim page.

## Insights

Watching a claim page shows one claim at a time. Insights answers how the whole run went. It is worked out from records the pilot already keeps (steps with their times, who made each decision, who authorized each payment, and refused calls, which come back as `Refused (...)`), so it needed one read-only endpoint, `get_insights`, and no new tables:

- Straight through: claims the agent finished with no handler, the spec's stated goal
- Time per claim, from the first to the last step of its latest run
- A timeline of the latest run, a bar per claim, colored by decision
- Tool calls per tool, with the calls the backend refused
- Decisions by claim type, policy sections cited, and the money: claimed, approved, paid and held for sign-off

Charts are plain CSS in the brand's colors, with no chart library.

## A run of the queue

GPT-6 Luna from the dashboard. I stopped it on CLM-1022 after 13 minutes, so it covers 21 claims.

| | Ed's run, all 25 claims | Mine, 21 claims |
|---|---|---|
| Approve, decline, refer | 17, 5, 3 | 13, 5, 3 |
| Held for sign-off | CLM-1003, CLM-1008, CLM-1011 | The same three and CLM-1021 |
| Straight through | 19 of 25 (14 paid by the agent, 5 declined), worked out from Ed's `STATUS.md` | 14 of 21 (9 paid by the agent, 5 declined), paying $3,537.00 |
| Time | 2 min 47 s | 13 min, median 25 s a claim |

Neither run is scored against `data/evals/` yet.

## Takeaways

- **The backend held the rules.** On CLM-1011 the agent tried to decide again after its approval had gone to sign-off. The backend refused, the refusal came back to the agent as text, and the run carried on.
- **Stop needs a separate process.** Stopping mid-claim ended the model call at once, and CLM-1022 went back to `waiting` with its steps kept.
- **The records were enough for a first view.** Timed steps answered how long, how many calls and what was refused, before any tracing.
- **Measuring showed what watching didn't.** My run is about four times slower than Ed's. I haven't found why yet, and I wouldn't have known without the timeline.
