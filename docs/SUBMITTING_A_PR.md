# Submitting a PR to Ed's repo

These are the steps that sent PRs #44, #45, #50 and #68 to https://github.com/ed-donner/undercover.
The general guide is
`guides/for_agents/AGENT_GIT_GUIDE.md`. This file records what is different for this machine.

## Remotes on this machine

Check with `git remote -v` before you start. On this machine they are:

| Remote | Repo | Use |
|---|---|---|
| `origin` | `ed-donner/undercover` | Ed's repo. The PR targets its `main`. |
| `pte-fork` | `verbodh-pte-ltd/undercover-fork` (public) | A real fork of Ed's repo. PR branches go here. |
| `verbodh` | `verbodh-pte-ltd/undercover` (private) | Our own work. It is not a GitHub fork. |

GitHub opens a PR only from a real fork. A push to `verbodh` cannot make a PR to Ed's repo.
If you try, `gh pr create` fails with "Head repository can't be blank".

If a clone has other names for these remotes, use its names in the commands below. The repos are
what matter, not the names.

## One-time setup

You need these steps only if a remote is missing.

1. Make the fork. The org already has a repo named `undercover`, so give the fork a new name:
   ```bash
   gh repo fork ed-donner/undercover --org verbodh-pte-ltd --fork-name undercover-fork --default-branch-only --clone=false
   ```
2. Add the fork as a remote: `git remote add pte-fork git@github-verbodh:verbodh-pte-ltd/undercover-fork.git`

Do not add `--remote` to step 1. `gh` refuses it when you give a repository name.

## Before the PR: run the notebook

1. Copy the lab into `community_contributions/verbodh/` and give the file a name that says what it is,
   such as `week2day2_word_lookup_memory.ipynb`. The folder is two levels down, like `labs/week2/`, so
   `../../data` paths still work.
2. Find the lab's own modules in either labs layout. Ed's `main` keeps them in `labs/`, and later
   branches in `labs/week2/`:
   ```python
   sys.path += [str(Path("../../labs").resolve()), str(Path("../../labs/week2").resolve())]
   ```
3. Behind Cloudflare WARP, put `truststore.inject_into_ssl()` at the very top of the imports cell, before
   anything opens a connection. A program the notebook starts, such as an MCP server, needs the Windows
   certificates as a file; the Windows adjustment cell in
   `community_contributions/verbodh/week2day2_word_lookup_memory.ipynb` (PR #68) does this.
4. Set `TINY = True` (5 claims) unless you want all 25. All 25 takes about 5 times as long and costs
   about 5 times as much.
5. Run it from the top. Outside VS Code:
   ```bash
   uv run --with nbconvert jupyter nbconvert --to notebook --execute --inplace --ExecutePreprocessor.timeout=1800 community_contributions/verbodh/<file>.ipynb
   ```
6. Write what you found in a markdown cell at the end, from the run's numbers.
7. Keep files the notebook makes out of git with a `.gitignore` in `community_contributions/verbodh/`.
   The root `.gitignore` already covers `qdrant_data/`.

## Each PR

Make the PR in a separate worktree. Then your branch and any notebook you have open stay as they are,
and nothing needs stashing.

1. Clear the notebook outputs:
   ```bash
   uv run python -c "import sys, nbformat; p = sys.argv[1]; nb = nbformat.read(p, as_version=4); [c.update(outputs=[], execution_count=None) for c in nb.cells if c.cell_type == 'code']; nbformat.write(nb, p)" community_contributions/verbodh/<file>.ipynb
   ```
2. Get Ed's latest code: `git fetch origin`
3. Make a worktree with a new branch from Ed's `main`:
   ```bash
   git worktree add --no-track -b verbodh-<topic> ../undercover-pr origin/main
   ```
4. Copy only the new files into it, then commit there:
   ```bash
   cp community_contributions/verbodh/<file>.ipynb community_contributions/verbodh/.gitignore ../undercover-pr/community_contributions/verbodh/
   cd ../undercover-pr
   git add community_contributions/verbodh
   git commit -m "<what it is>"
   ```
5. Run the checks below.
6. Push the branch to the fork: `git push -u pte-fork verbodh-<topic>`
7. Open the PR. Write the body to a file first, so tables and backticks survive the shell:
   ```bash
   gh pr create --repo ed-donner/undercover --base main --head verbodh-pte-ltd:verbodh-<topic> --title "<title>" --body-file <body>.md
   ```
8. Go back and remove the worktree. Close any VS Code window on `../undercover-pr` first, or Windows
   keeps the folder locked:
   ```bash
   cd ../undercover
   git worktree remove ../undercover-pr
   ```

## Checks before step 6

Run these in the worktree:

- `git diff --numstat origin/main...HEAD` shows fewer than 5 files and fewer than 2,000 lines.
- Every file is inside `community_contributions/`.
- The PR deletes no lines: the second number on every line of that output is 0.
- The files contain no API keys and no `.env` contents:
  `grep -E 'sk-or-|_API_KEY=|pk-lf-|sk-lf-' <files>` prints nothing.

## If you pushed to the wrong remote

Delete the branch from that remote. The PR uses the copy on `pte-fork`, so the PR does not change:

```bash
git push verbodh --delete verbodh-<topic>
```
