"""Build prompts for each phase."""

from pathlib import Path


def _read_if_exists(path: Path) -> str:
    return path.read_text() if path.exists() else ""


def planner(
    task: str,
    issue_path: Path,
    clarifications_path: Path,
    project_root: Path,
    planner_md: Path,
) -> str:
    planner_context = _read_if_exists(planner_md)

    return f"""You are the riff PLANNER, running INTERACTIVELY.

TASK
----
{task}

PROJECT CONTEXT (read-only, for understanding only — do NOT start implementing yet)
---------------
Follow the project's CLAUDE.md conventions (Claude Code loads it automatically).
{f"PLANNER.md (your custom instructions):{chr(10)}{planner_context}" if planner_context else ""}

YOUR JOB RIGHT NOW
------------------
Clarify the task with the user before any implementation. This is a fully
interactive session, so ask questions naturally — use the AskUserQuestion tool for
multiple-choice decisions, or plain prose for open ones. Cover scope, acceptance
criteria, edge cases, constraints, and preferred approach. Explore the codebase as
needed to ask informed questions.

When you and the user have agreed on a complete picture:
1. Write a structured ISSUE.md to: {issue_path}
   Include: ## Problem, ## Acceptance Criteria, ## Implementation Plan, ## Edge Cases
2. Write a short Q&A summary of the clarification to: {clarifications_path}
3. Tell the user that ISSUE.md is ready and they should exit this Claude session
   (type /exit or press Ctrl-D) to continue the riff worker/reviewer loop.

Do NOT start implementing anything — clarify and write the issue only.
"""


def worker(
    session_dir: Path,
    issue_path: Path,
    pr_path: Path,
    project_root: Path,
    worker_md: Path,
    iteration: int,
    prev_review_path: Path | None = None,
    prev_pr_paths: list[Path] | None = None,
    issue_number: int | None = None,
    pr_number: int | None = None,
) -> str:
    worker_context = _read_if_exists(worker_md)
    issue_text = _read_if_exists(issue_path)
    review_text = _read_if_exists(prev_review_path) if prev_review_path else ""

    prior_prs = []
    for i, path in enumerate(prev_pr_paths or [], start=1):
        text = _read_if_exists(path)
        if text:
            prior_prs.append(f"### Iteration {i} PR\n{text}")

    history_section = ""
    if prior_prs:
        joined = "\n\n".join(prior_prs)
        history_section = f"""
WORK SO FAR (PR summaries from previous iterations — these changes are ALREADY in the working tree)
--------------------------------------------------------------------------------------------------
{joined}
"""

    review_section = ""
    if review_text:
        review_section = f"""
REVIEWER FEEDBACK (iteration {iteration - 1} — address ALL points)
-----------------------------------------------------------------
{review_text}
"""

    if iteration > 1:
        ref = " (see WORK SO FAR)" if prior_prs else ""
        job_intro = (
            f"The working tree ALREADY contains changes from previous iterations{ref}, "
            "made by earlier worker runs. Build on them — do NOT reimplement from "
            "scratch. Read the current code to confirm its state, then address the "
            "reviewer feedback below."
        )
    else:
        job_intro = "Implement the changes described in the issue."

    forge_note = ""
    if pr_number:
        issue_ref = f" for issue #{issue_number}" if issue_number else ""
        forge_note = f"""
LINKED PR / ISSUE
-----------------
This work is on draft PR #{pr_number}{issue_ref}. riff commits and pushes your changes
after this iteration, so you don't need to. You MAY refine the PR via `gh pr edit {pr_number}`:
keep the title as the intended squash-merge commit subject and the body as its description;
put any extra notes in `gh pr comment {pr_number}`.
"""

    amendments_note = f"""
ISSUE = AGREED SPEC
-------------------
Treat the issue above as the agreed spec; do not rewrite its original sections. If the
spec itself must change (a criterion is infeasible, a constraint was missed, scope is
ambiguous), append a brief justified note under an "## Amendments" section at the END of
{issue_path} (e.g. "### Iteration {iteration}: <what changed and why>"), leaving the
original sections intact. Keep amendments minimal — the reviewer will scrutinize them.
"""

    return f"""You are a worker agent implementing a task. This is iteration {iteration}.

PROJECT CONTEXT
---------------
Project root: {project_root}
Follow the project's CLAUDE.md conventions (Claude Code loads it automatically).
{f"WORKER.md (your custom instructions):{chr(10)}{worker_context}" if worker_context else ""}

ISSUE TO IMPLEMENT
------------------
{issue_text if issue_text else f"(read from {issue_path})"}
{history_section}{review_section}{amendments_note}{forge_note}
YOUR JOB
--------
{job_intro}
1. Implement / adjust the code (address reviewer feedback if any)
2. Run the project's tests to confirm nothing is broken
3. Write a PR summary to: {pr_path}

PR summary format:
## Summary
## Changes Made
## How to Test
## Decisions & Trade-offs
## Known Issues

Work in: {project_root}
"""


def reviewer(
    issue_path: Path,
    pr_path: Path,
    review_path: Path,
    project_root: Path,
    reviewer_md: Path,
    iteration: int,
) -> str:
    reviewer_context = _read_if_exists(reviewer_md)
    issue_text = _read_if_exists(issue_path)
    pr_text = _read_if_exists(pr_path)

    return f"""You are a reviewer agent. This is iteration {iteration}.

PROJECT CONTEXT
---------------
Project root: {project_root}
Follow the project's CLAUDE.md conventions (Claude Code loads it automatically).
{f"REVIEWER.md (your review guidelines):{chr(10)}{reviewer_context}" if reviewer_context else ""}

ISSUE
-----
{issue_text if issue_text else f"(read from {issue_path})"}

PR SUMMARY
----------
{pr_text if pr_text else f"(read from {pr_path})"}

YOUR JOB
--------
1. Read the issue and PR summary. If the issue has an "## Amendments" section, treat
   the amended spec as current — but flag any amendment that looks like unjustified
   scope creep or a criterion quietly dropped to make the work pass.
2. Review the actual code changes (use git diff, read changed files in {project_root})
3. Run tests if you deem it necessary
4. Write your review to: {review_path}

Review file format:
STATUS: APPROVED   (or CHANGES_REQUESTED)

## Summary
(2-3 sentences)

## Issues
(bulleted list — omit section if none)

## Suggestions
(nice-to-haves — omit section if none)

CRITICAL: The very first line of {review_path} MUST be either:
  STATUS: APPROVED
  STATUS: CHANGES_REQUESTED

Work in: {project_root}
"""
