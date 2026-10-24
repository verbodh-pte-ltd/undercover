# Submitting a PR to Ed's repo

These are the steps we followed for PR #44
(https://github.com/ed-donner/undercover/pull/44). The general guide is
`guides/for_agents/AGENT_GIT_GUIDE.md`. This file records what is different for this machine.

## Remotes on this machine

| Remote | Repo | Use |
|---|---|---|
| `origin` | `verbodh-pte-ltd/undercover` (private) | Our own work. It is not a GitHub fork. |
| `upstream` | `ed-donner/undercover` | Ed's repo. The PR targets its `main`. |
| `fork` | `verbodh-pte-ltd/undercover-fork` (public) | A real fork of Ed's repo. PR branches go here. |

GitHub opens a PR only from a real fork. A push to `origin` cannot make a PR to Ed's repo.
If you try, `gh pr create` fails with "Head repository can't be blank".

## One-time setup

You need these steps only if a remote is missing. Check with `git remote -v`.

1. Add Ed's repo: `git remote add upstream https://github.com/ed-donner/undercover.git`
2. Make the fork. The org already has a repo named `undercover`, so give the fork a new name:
   ```bash
   gh repo fork ed-donner/undercover --org verbodh-pte-ltd --fork-name undercover-fork --default-branch-only --clone=false
   ```
3. Add the fork as a remote: `git remote add fork git@github.com:verbodh-pte-ltd/undercover-fork.git`

Do not add `--remote` to step 2. `gh` refuses it when you give a repository name.

## Each PR

1. Copy your work into `community_contributions/verbodh/`. Give the file a name that says what it is.
2. Clear the notebook outputs:
   ```bash
   uv run python -c "import sys, nbformat; p = sys.argv[1]; nb = nbformat.read(p, as_version=4); [c.update(outputs=[], execution_count=None) for c in nb.cells if c.cell_type == 'code']; nbformat.write(nb, p)" community_contributions/verbodh/<file>.ipynb
   ```
3. Stash your other changes, so they stay out of the PR: `git stash push <changed files>`
4. Get Ed's latest code: `git fetch upstream`
5. Make a branch from Ed's `main`. The new folder comes with you:
   ```bash
   git checkout --no-track -b verbodh-<topic> upstream/main
   ```
6. Commit the folder:
   ```bash
   git add community_contributions/verbodh
   git commit -m "<what it is>"
   ```
7. Check the PR size: `git diff --numstat upstream/main...HEAD`
8. Push the branch to the fork: `git push -u fork verbodh-<topic>`
9. Open the PR:
   ```bash
   gh pr create --repo ed-donner/undercover --base main --head verbodh-pte-ltd:verbodh-<topic> --title "<title>" --body "<what it is and what you found>"
   ```
10. Go back to your work: `git checkout main`, then `git stash pop`.

## Checks before step 8

- The PR has fewer than 5 files.
- The PR changes fewer than 2,000 lines.
- Every file is inside `community_contributions/`.
- The PR deletes no lines.
- The files contain no API keys and no `.env` contents. Check with `grep -E 'sk-or-|_API_KEY=' <files>`.

## If you pushed to the wrong remote

Delete the branch from `origin`. The PR uses the copy on `fork`, so the PR does not change:

```bash
git push origin --delete verbodh-<topic>
```
