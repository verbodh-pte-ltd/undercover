
Before we dig into using Claude Code,

I want to give a general point that applies to all coding agents

and the way that you use them,

whether it's just to auto-complete a line of code or to build an entire product.

The mantra is that you always need to be the boss.

The coding agent is a tool.

It's a tool that you're using to help with your productivity and to allow you

to deliver more. But ultimately, you need to own everything that gets delivered.

You're accountable for the quality.

And so your role is to act like the manager of the coding agent,

asking questions, digging in,

making sure that you're satisfied with everything that goes into production.

And so how do you do that?

Well, it begins with the brief.

It begins with you documenting the spec, the business requirements,

the style it should conform to, and the success criteria.

And then you want to start simple and work incrementally in phases

with measurable success criteria at each point.

But here's the thing.

It's easy to fall into a trap where you get lazy.

You start to see Claude Code

delivering time after time and you decide, I'm just going to go with it.

It's built a product, it tests okay, it works okay, should be good for me.

You need to resist that temptation.

Don't be lazy. Always dig in.

Always challenge Claude, ask difficult questions, demand evidence.

Satisfy yourself that it's done a good job.

But you might be thinking, OK, Ed, it's easy for you to say that,

but first of all, what if it's working in an area where I don't know the technology?

What if I'm not a front-end person and it builds out lots of front-end?

And what if it's turning out code far faster than I could possibly read through

it? Aha! Well, here's the point.

I'm not suggesting that you read every line.

There's different schools of thought on this,

but I'm not of the school of thought that you should read every line.

Only lines that are very sensitive and need to be checked carefully.

But my point is, you need to behave like a boss.

I've had in my career a number of non-technical managers.

Early on, I used to turn out code super fast.

I remember being so keen.

And I would have a manager who is not necessarily technical,

certainly not in my area,

but really good managers who know how to ask the difficult questions

that make you sweat.

Ask for examples of things.

They want to know how did you make different decisions?

What decisions did you make?

And in what situations would you have made a different decision?

They ask the great questions so that they really understand what you've done.

They look at a few examples.

They satisfy themselves you taking care.

Maybe they ask another person they trust to give a second review

of something you've done.

They do things like that to satisfy themselves

that you've put effort into it and they can stand behind it.

They don't need to read every line of code.

So in my opinion,

the new skill in this new era of coding is not being some kind

of mega code reviewer that can review thousands,

hundreds of thousands of lines of code in an hour.

That's not it. The new skill is knowing how to be the boss.

Knowing how to ask the difficult questions, demand evidence,

and satisfy yourself that a good job has been done without looking

at every single line of code.

Just looking at the ones that really matter and demanding the evidence

that really matters.

That's the skill. Being the boss.

And I cover this in a lot of detail in my AI Coder course.

And at the end of the day, I'll say it again,

you need to own the code that goes to production.

And so, developing the techniques to be like the manager,

the non-technical manager, or the semi-technical manager,

that's able to take accountability for what's delivered without needing

to read every single line.

That's the skill.

And so it's never acceptable to say, that wasn't me, that was Claude.

You can never blame an LLM.

You're responsible for the code that gets delivered.

Don't push slop.

So usually when you start working with coding agents,

you get quite familiar with writing files that are called markdown files.

And you're familiar with markdown files because README is a markdown file,

and there are a lot of them out there.

And they're normally written in block capitals as the name of the file

and .md for markdown.

And they have a particular format.

And the reason we use markdown such a lot is that LLMs were trained of lots

and lots of markdowns, and they love generating markdown.

So markdown is like the native language for LLMs.

They love it. And it's perfectly simple.

You use a single hash to represent a big heading.

Two hashes is like a subheading.

And you can use a dash for bulleted lists or numbered lists.

You've probably seen many examples of it before.

And so you don't need to stick too strictly to this.

It's not like the hierarchy matters hugely.

It just gives a sense of the headings, the structure of the document.

It reads this in as formatted natural language.

And by default,

all the coding agents look for a file that's called AGENTS.md

and they read it into context immediately when they're started up

and every time that the context gets compacted,

they'll always read AGENTS.md in.

It's always there. It used to be the case that Claude Code didn't read that.

It only looked for a file called Claude.md,

but recently they changed it so that if there isn't a Claude.md file,

then it looks for AGENTS.md instead, which is great.

So now all the coding agents do it.

So it's a good place to start with the basic information.

And there's some best practices and pro tips for you.

It's always important to be clear and concise in your markdown files.

Take time over them.

Get them to be really good, really well stated.

And then a small silly thing is that it's generally considered better to focus

on what the LLM should do, the positives,

more than telling it what it should not do, the negatives.

Because with too many things not to do, it can start to get confused.

So better to have more positives.

You can have a few things not to do, for sure.

It's not that important.

And increasingly, the larger models are getting better and better at this as well.

And this is a bit janky,

but sometimes using the word important in block capitals does help.

You've probably seen some examples of this, but saying important,

you must do this, does tend to help.

So you can use that for the most important instructions in your markdown files.

But the single most important tip I have for you for working with these kinds

of markdowns is to be willing to experiment with them.

So many people will give you advice on how to prompt, on prompting techniques,

but at the end of the day, no one's all that sure.

The way that people find out is just by trying out different techniques.

Different coding agents, different models,

different business tasks might lend themselves to different kinds

of approaches. So the most important thing to be willing to do is give it a try,

write some instructions, run your coding agent, and if it doesn't work well,

change it around and try again.

And as I say, Claude Code, Codex, OpenCode, all read AGENTS.md.

I think for a while, Antigravity needed GEMINI.md,

but now they might have also gone to AGENTS.md.

So if in doubt, look up, ask, ask Claude Code,

ask ChatGPT what your coding agent of choice looks for.

But most of them now have converged on AGENTS.md.

And a best practice is to keep it as short as possible,

because it is read in every time.

And rather than including within it all sorts of stuff,

like your coding standards, just tell it where it can find the coding standards.

For example, say, for coding standards,

look in the docs directory and find this file, CODE_STANDARDS.md.

And you can keep going with this idea.

Within CODE_STANDARDS.md, you might have the most essential code standards,

and then you might say, but for front-end specific coding standards,

here is the more detailed coding standards of front-end, and here it is for back-end,

and point to another two documents.

And you can in fact have a whole hierarchy of documents that refer

to the next ones down to get more granular,

so that the agent only needs to read in the details that it needs

for the current task it's working on.

And this strategy goes by a name.

It's known as progressive disclosure.

As I imagine some of you know,

that's a fancy name for a pretty simple idea of just,

rather than inserting everything into one massive document,

break it up and have one document referring to the next,

and do so so that the agent can choose which files it needs to load.

It can just have a tool that allows it to come in and explore the directories,

only loading into context the information it needs for the task at hand,

so that it makes most efficient use of its context.

Now one decision you need to make up front is about how involved you want to be

in the process while Claude Code is working.

And these are called the permission modes.

It controls how much it needs to ask you before it does something.

And originally, the default used to be this thing called manual mode.

And on manual mode, it will ask you every time it edits a file, runs a command,

or looks out on the network.

And so you'd have to sit there saying yes, yes, yes to all, yes to all,

again and again.

And this is no longer very common, but it used to be how everything began.

You can flip between the different modes by pressing Shift+Tab.

That switches you between the different modes.

And then the next mode was called Accept Edits,

which means that it would just change files without you needing to approve it,

but then some of the other things,

commands that weren't just basically changing files,

would still ask you for permission.

Then Plan Mode was a mode where you'd ask it to just build a plan

for what it's going to do, and you will approve that plan before it goes further.

Now this sounds like a really sensible idea,

and for a long time this was very much recommended.

But these days people don't use Plan Mode anymore, because if you tell a model,

if you tell something like Claude Code Opus 5.5 that you want it just

to write a plan, then you can rely on it only doing that.

You don't need to have it in a special Plan Mode,

it definitely will adhere to those kinds of instructions reliably.

So actually, people don't use Plan Mode anymore.

And then comes a new powerful one called Auto Mode.

Well, it's not that new anymore, but what is new is that this is now the default,

and it's what everybody uses.

And on Auto Mode, Claude Code, the harness,

will look at what's going on and will make its first guess as to whether

or not this should be safe to approve it,

or whether it needs to escalate it to you to approve it.

And it does a really fine job.

And so, in some ways, this is something where you're giving it a bit more control,

but if there's something that appears dangerous

to this thing they call the classifier,

that's just trying to judge each of its commands,

then it will raise it to you and you'll have to come in and approve it.

Now, you might think that sounds a bit dangerous because you're relying

on the classifier not to make a mistake there.

But actually, the point that a lot of people have made is that

in many ways this is less dangerous than the manual mode.

Because on the manual mode, it was prompting you all the time for everything.

And people got what's known as decision fatigue,

which is you got so fed up of having to keep saying yes, yes, yes,

that people just sat on the keyboard doing yes, yes, yes.

And it was too easy to miss something that actually was problematic.

With auto mode,

you can be more focused because it's quite rare for you to be asked anything.

But anytime you're asked,

it usually is a decision that you might need to pause over

and be thoughtful about.

So the argument is auto mode is actually more secure because there's no risk

of decision fatigue or less risk of it.

And then last but not least is the one that's called Bypass Permissions,

better known as YOLO mode.

You only live once, which of course was all the rage about a year ago.

Everyone was talking about YOLO mode.

You actually can't even put it

in this mode unless you start Claude a particular way by doing

claude --dangerously-skip-permissions.

And then this is an option for you, YOLO mode, where everything gets approved.

And this was something that was a really big deal for a while

and now it's almost never used.

It's not a big deal because you can just use auto

and it achieves much the same thing,

only raising to you when you really should see it.

The time when you still see people using YOLO mode is when they're running Claude

in a particular kind of sandbox environment where it really can go

and do whatever it wants.

So then you do sometimes see it, but basically auto has taken over.

So we will use auto.

You should use auto if you're using Codex.

It's auto by default.

And in addition to that,

you can also set specific allow and deny rules in your settings.json file

in .claude directory, settings.json.

And you can talk to Claude Code and ask it to write that for you

and tell it what you want it to be able to do.

It will construct that and you can go in and check it.

**So you can get much more granular if that's what you want.**
