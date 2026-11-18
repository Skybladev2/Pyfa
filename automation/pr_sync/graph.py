"""LangGraph workflow that keeps a pull request branch current with its base branch.

The graph is intentionally small and deterministic:

    check -> (behind?) -> merge -> verify -> push -> report

Every run performs the check, so the workflow can run daily even when nothing has
changed. A merge and push only happen when the base branch is not already an
ancestor of the pull request branch.

The module uses plain ``git`` commands, which keeps it usable both in GitHub
Actions (where ``actions/checkout`` provides push credentials) and locally.
"""

from __future__ import annotations

import logging
import os
import subprocess
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph

log = logging.getLogger(__name__)

DEFAULT_BRANCH = "focus-graph"
DEFAULT_BASE_URL = "https://github.com/pyfa-org/Pyfa.git"
DEFAULT_BASE_BRANCH = "master"
DEFAULT_REMOTE = "origin"


class GitCommandError(RuntimeError):
    """Raised when a required git command fails."""


class SyncState(TypedDict, total=False):
    """State passed between LangGraph nodes."""

    repo_dir: str
    branch: str
    remote: str
    base_url: str
    base_remote: str
    base_branch: str
    dry_run: bool
    base_sha: str
    head_sha: str
    ahead_by: int
    behind_by: int
    needs_update: bool
    merged: bool
    pushed: bool
    message: str
    error: str


def _git(repo_dir: str, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ("git", *args),
        cwd=repo_dir,
        text=True,
        capture_output=True,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise GitCommandError(f"git {' '.join(args)} failed: {detail}")
    return result


def _rev(repo_dir: str, ref: str) -> str:
    return _git(repo_dir, "rev-parse", ref).stdout.strip()


def check(state: SyncState) -> SyncState:
    """Fetch both branches and decide whether the PR branch is behind its base."""
    repo_dir = state.get("repo_dir", ".")
    branch = state.get("branch", DEFAULT_BRANCH)
    remote = state.get("remote", DEFAULT_REMOTE)
    base_url = state.get("base_url", DEFAULT_BASE_URL)
    base_remote = state.get("base_remote", "upstream")
    base_branch = state.get("base_branch", DEFAULT_BASE_BRANCH)

    try:
        _git(repo_dir, "rev-parse", "--git-dir")
        dirty = _git(repo_dir, "status", "--porcelain").stdout.strip()
        if dirty:
            raise GitCommandError("working tree has uncommitted changes; refusing to update the branch")

        head_ref = f"refs/remotes/{remote}/{branch}"
        base_ref = f"refs/remotes/{base_remote}/{base_branch}"

        _git(repo_dir, "fetch", "--no-tags", remote, f"+refs/heads/{branch}:{head_ref}")
        _git(repo_dir, "fetch", "--no-tags", base_url, f"+refs/heads/{base_branch}:{base_ref}")

        behind_by = int(_git(repo_dir, "rev-list", "--count", f"{head_ref}..{base_ref}").stdout.strip())
        ahead_by = int(_git(repo_dir, "rev-list", "--count", f"{base_ref}..{head_ref}").stdout.strip())

        log.info(
            "%s/%s is %s commit(s) ahead and %s behind %s/%s",
            remote, branch, ahead_by, behind_by, base_remote, base_branch,
        )
        return {
            "repo_dir": repo_dir,
            "branch": branch,
            "remote": remote,
            "base_url": base_url,
            "base_remote": base_remote,
            "base_branch": base_branch,
            "base_sha": _rev(repo_dir, base_ref),
            "head_sha": _rev(repo_dir, head_ref),
            "ahead_by": ahead_by,
            "behind_by": behind_by,
            "needs_update": behind_by > 0,
            "error": "",
        }
    except (GitCommandError, subprocess.SubprocessError) as exc:
        return {"error": str(exc), "needs_update": False}


def route_after_check(state: SyncState) -> Literal["merge", "report"]:
    if state.get("error") or not state.get("needs_update"):
        return "report"
    return "merge"


def merge(state: SyncState) -> SyncState:
    """Merge the base branch into the PR branch, aborting cleanly on conflicts."""
    if state.get("error"):
        return {}

    repo_dir = state["repo_dir"]
    branch = state["branch"]
    remote = state["remote"]
    base_ref = f"refs/remotes/{state['base_remote']}/{state['base_branch']}"
    merge_message = f"Merge {state['base_remote']}/{state['base_branch']} into {branch}"

    if state.get("dry_run"):
        return {
            "merged": False,
            "message": f"would merge {state['base_remote']}/{state['base_branch']} into {branch}",
        }

    try:
        _git(repo_dir, "checkout", "-B", branch, f"refs/remotes/{remote}/{branch}")
        result = _git(repo_dir, "merge", "--no-ff", "--no-edit", base_ref, "-m", merge_message, check=False)
        if result.returncode != 0:
            _git(repo_dir, "merge", "--abort", check=False)
            detail = (result.stderr or result.stdout).strip()
            raise GitCommandError(f"merge of {base_ref} failed and was aborted: {detail}")
        return {"merged": True, "head_sha": _rev(repo_dir, "HEAD")}
    except (GitCommandError, subprocess.SubprocessError, KeyError) as exc:
        return {"error": str(exc), "merged": False}


def verify(state: SyncState) -> SyncState:
    """Confirm the merge result actually contains the base branch."""
    if state.get("error") or not state.get("merged"):
        return {}

    repo_dir = state["repo_dir"]
    base_ref = f"refs/remotes/{state['base_remote']}/{state['base_branch']}"
    result = _git(repo_dir, "merge-base", "--is-ancestor", base_ref, "HEAD", check=False)
    if result.returncode != 0:
        return {"error": f"merged branch does not contain {base_ref}"}
    return {}


def push(state: SyncState) -> SyncState:
    """Push the merge commit to the PR branch unless this is a dry run."""
    if state.get("error") or not state.get("merged"):
        return {}
    if state.get("dry_run"):
        return {"pushed": False}

    repo_dir = state["repo_dir"]
    branch = state["branch"]
    remote = state["remote"]
    try:
        _git(repo_dir, "push", remote, f"HEAD:refs/heads/{branch}")
        return {"pushed": True, "head_sha": _rev(repo_dir, "HEAD")}
    except (GitCommandError, subprocess.SubprocessError) as exc:
        return {"error": str(exc), "pushed": False}


def report(state: SyncState) -> SyncState:
    """Build a short human-readable outcome for logs and the CLI output."""
    branch = state.get("branch", DEFAULT_BRANCH)
    base = f"{state.get('base_remote', 'upstream')}/{state.get('base_branch', DEFAULT_BASE_BRANCH)}"

    if state.get("error"):
        message = f"error: {state['error']}"
    elif state.get("pushed"):
        message = f"merged {base} into {branch} and pushed (head {state.get('head_sha', '?')[:9]})"
    elif state.get("merged"):
        message = f"merged {base} into {branch}; not pushed"
    elif state.get("dry_run") and state.get("needs_update"):
        message = f"dry run: {branch} would merge {base}"
    elif state.get("needs_update"):
        message = f"{branch} needs an update from {base}"
    else:
        message = f"{branch} already contains {base}; nothing to do"

    log.info("%s", message)
    return {"message": message}


def build_graph():
    """Compile the LangGraph state machine."""
    builder = StateGraph(SyncState)
    builder.add_node("check", check)
    builder.add_node("merge", merge)
    builder.add_node("verify", verify)
    builder.add_node("push", push)
    builder.add_node("report", report)

    builder.add_edge(START, "check")
    builder.add_conditional_edges("check", route_after_check, {"merge": "merge", "report": "report"})
    builder.add_edge("merge", "verify")
    builder.add_edge("verify", "push")
    builder.add_edge("push", "report")
    builder.add_edge("report", END)
    return builder.compile()


graph = build_graph()


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-dir", default=os.environ.get("PR_SYNC_REPO_DIR", "."))
    parser.add_argument("--branch", default=os.environ.get("PR_SYNC_BRANCH", DEFAULT_BRANCH))
    parser.add_argument("--remote", default=os.environ.get("PR_SYNC_REMOTE", DEFAULT_REMOTE))
    parser.add_argument("--base-url", default=os.environ.get("PR_SYNC_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--base-remote", default=os.environ.get("PR_SYNC_BASE_REMOTE", "upstream"))
    parser.add_argument("--base-branch", default=os.environ.get("PR_SYNC_BASE_BRANCH", DEFAULT_BASE_BRANCH))
    dry_run_default = os.environ.get("PR_SYNC_DRY_RUN", "").lower() == "true"
    parser.add_argument("--dry-run", action="store_true", default=dry_run_default)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(message)s")

    result = graph.invoke(
        {
            "repo_dir": args.repo_dir,
            "branch": args.branch,
            "remote": args.remote,
            "base_url": args.base_url,
            "base_remote": args.base_remote,
            "base_branch": args.base_branch,
            "dry_run": args.dry_run,
        }
    )

    summary = {
        key: result.get(key)
        for key in (
            "branch",
            "base_sha",
            "head_sha",
            "ahead_by",
            "behind_by",
            "needs_update",
            "merged",
            "pushed",
            "message",
            "error",
        )
        if key in result
    }
    print(json.dumps(summary, indent=2))
    return 1 if result.get("error") else 0


if __name__ == "__main__":
    raise SystemExit(main())
