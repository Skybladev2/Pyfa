# PR branch sync (LangGraph)

Keeps the `focus-graph` branch of [PR #2741](https://github.com/pyfa-org/Pyfa/pull/2741)
up to date with `pyfa-org/Pyfa:master`. The graph is deterministic and does not call
an LLM: LangGraph is used to express the check/merge/verify/push flow.

## How it decides

1. Fetch `origin/focus-graph` and `upstream/master`.
2. Count the commits in `upstream/master` that are not in `origin/focus-graph`.
3. If the count is zero, do nothing.
4. Otherwise merge `upstream/master` into `focus-graph` with a merge commit,
   verify the base is contained in the result, and push to the PR branch.
5. If the merge conflicts, abort it and fail the run without pushing.

## Running locally

```bash
python -m venv .venv-pr-sync
.venv-pr-sync/bin/pip install -r automation/pr_sync/requirements.txt
.venv-pr-sync/bin/python -m automation.pr_sync --dry-run
```

Remove `--dry-run` to merge and push. The checkout must be allowed to push to
`origin/focus-graph`, and the working tree must be clean.

## Scheduled run

`.github/workflows/update-pr-branch.yml` runs the graph daily at 05:00 UTC and can
also be started manually from the Actions tab. Scheduled workflows execute from
the repository's default branch, so this workflow file has to live on `master`.

GitHub disables scheduled workflows in forks by default. Enable them once under
**Actions → Keep PR branch up to date → Enable workflow**.

The workflow pushes with the repository `GITHUB_TOKEN`, so the update does not
trigger further workflows on the PR branch (GitHub suppresses workflow runs from
token pushes). If the branch's CI must run after an automatic update, pass a
personal access token with `contents: write` to the checkout step instead.

## LangGraph Platform

`langgraph.json` exposes the graph as `pr_sync` for `langgraph dev` or deployment.
A hosted deployment still needs access to the repository and a push credential;
runs on the GitHub Actions schedule above are the supported path here.
