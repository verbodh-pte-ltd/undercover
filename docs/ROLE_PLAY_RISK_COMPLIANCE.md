# Role play: UnderCover's head of risk and compliance

Week 3 Day 1's practice role play on Udemy, done in text on 3 November 2026. Alex Morgan, UnderCover's head
of risk and compliance, an AI character, asks the forward deployed engineer to settle three concerns before
the project committee decides whether the claims agent goes into production. The meeting ends once Alex is
satisfied on all three.

**Goals**

1. Explain how customers' data is protected when the agent sends it to an LLM provider.
2. Explain which of the agent's actions need a person's approval.
3. Say who is accountable when the agent gets a claim wrong.

**Outcome:** Alex was satisfied on all three with 1:40 of the 5 minutes left, and said they'd back it at the
committee.

## The conversation

**Alex:** Thanks for making the time. Before I back this at the committee, I need you to settle three
concerns: where our customers' data goes and how much of it the model sees, what this agent can do without
one of our people checking it, and who is accountable when it gets a claim wrong. Let's start with the data.

**FDE:** Happy to. Your claims data stays in your own AWS account: the database is Amazon RDS, the app and
the agent run in AWS, and the keys are held in AWS, not in code. The only thing that leaves is what the model
needs to decide one claim: the claim itself, its documents, the policy and the relevant wording. The agent
never gets the customer's address, phone or date of birth, because it doesn't need them to decide. It goes
over an encrypted connection to one model provider, through OpenRouter, and before production I'd turn on
their zero-data-retention routing so nothing is stored or used for training, and put that in the contract.
Anything the agent remembers about a customer is a short summary in AWS, never the story or the documents.
Would that meet your data protection policy, or does anything need to stay out of the model entirely?

**Alex:** That sounds solid so far. I like that you're minimizing exposure by skipping unnecessary data like
addresses and dates of birth. The encryption and zero-data-retention policy are good moves. But let me push a
bit: how do you verify that the model provider actually sticks to the zero-data-retention clause? Do you
have a process to audit or check compliance on their end?

**FDE:** Honestly, we can't inspect their servers, so it's contract plus evidence, not trust. We'd require
their SOC 2 Type II report and a signed data processing agreement that names zero retention and no training,
check it again at each renewal, and limit routing to providers who have signed it, so a request can't fall
back to one who hasn't. On our side we log every model call with what was sent, so your team can audit
exactly what left. And because the agent only sends what one claim needs, the exposure is small even in the
worst case. Can I move to the second concern? The agent acts on its own only within hard limits enforced in
code, not in its instructions. It can approve and pay up to the $1,000 approval limit. Anything over that
waits for a senior handler, who either signs it off, which pays it, or sends it back with a note and the
agent decides again. The payment itself refuses any amount over the limit, and AWS refuses it a second time
at the gateway, so even a misled agent can't pay more. Any claim whose text tries to instruct the agent is
referred to a handler before the model sees it. And changes to how the agent decides need a handler's
approval before they take effect.

**Alex:** Good, that settles the data question. Let's move to the second concern. I like that you've set a
hard limit at one thousand dollars and built in checks at multiple levels. But what happens if the senior
handler misses something in a claim over the threshold? Is there a process to catch errors in manual
approvals down the line?

**FDE:** Yes, two ways. Every decision is recorded as your claims manual's section 14 asks: the calculation
line by line, the section relied on, the reason, which agent version decided, the manual's version, and who
signed off and when. So your QA team can sample sign-offs, and the agent's decisions under the limit too,
and see exactly why each was made. Second, we score the agent every week against a held-out set of claims
your handlers decided, and a version that gets worse is rolled back. That brings me to accountability.
UnderCover is accountable to the customer for every decision, whoever or whatever made it, as it is today.
Inside UnderCover, I'd propose the head of claims operations owns the agent's decisions under the limit, as
they own their handlers' work, and the handler who signs off owns that payment. My team is accountable for
the agent working as specified, and for fixing it and reporting when it doesn't. When it gets a claim wrong,
the customer has the same complaint and appeal route as for a handler's decision, a handler corrects it, and
the decision record tells us whether it was the instructions, the data or the model. Does that ownership
match how your committee wants it set out?

**Alex:** Great, that covers the workflow and oversight question well. Now onto accountability: I like that
you've aligned it with existing ownership structures, and the escalation path for errors is clear. It sounds
like the responsibilities are well defined and traceable. That settles my third concern. Thanks for walking
me through all of this. It's clear you've thought it through. You have my backing at the committee.
Appreciate your time, and good luck with the next steps!

**FDE:** Thank you, Alex. I'll send you a one-page summary of all three before the committee, with the data
processing agreement and the ownership written out, so you can check it says what we agreed.

**Alex:** Perfect, that'd be really helpful. I'll look out for it. Thanks again for the thorough answers
and for being proactive. Take care!

## What in the answers is built, and what is a commitment

- **Built in UnderCover today:** the data on RDS in AWS with keys in AgentCore Identity; the claim history
  without address, phone or date of birth; memory holding a summary, never the story or documents; the
  $1,000 limit refused by the payment and again by the Gateway's Policy; sign off or send back; the check
  for hidden instructions; versions approved by a handler; the section 14 decision record; evaluations on
  the held-out set with rollback; every model call's tokens and cost recorded.
- **Commitments, not built:** OpenRouter's zero-data-retention routing and a signed data processing
  agreement; the provider's SOC 2 report checked at renewal; a log of what each model call sent (today the
  app records each call's tokens and cost, not its content); weekly scoring (today evaluations run when
  started); QA sampling of decisions; the named owners, which UnderCover's committee decides.
