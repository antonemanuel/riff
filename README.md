# riff

**Rapid Iterative Feedback Flow** or *riff is for fuckery* — run a planner/worker/reviewer Claude agent loop on any project until the reviewer approves.

*Still copy-pasting Copilot/Claude review comments right back into Claude, over and over? riff runs that loop for you.*

```
plan → implement → review → (repeat) → APPROVED
```

> **Honest caveat.** This is an opinionated stopgap. It buys better results mainly by spending
> more tokens, by running a separate planner, worker, and adversarial reviewer in a loop instead of
> one pass. As the underlying models and Claude Code itself get better at planning and self-review,
> a workflow like this will likely become unnecessary. Use it while the trade of tokens-for-quality
> is still worth it to you.

## Install

```bash
uv tool install git+https://github.com/antonemanuel/riff
```

Or to run without installing, from a clone:

```bash
uv run riff
```

## Usage

```bash
# Run a session; opens $EDITOR (vim) for you to write the task
riff run

# Or pass task inline
riff run --prompt "add rate limiting to the API"

# Or load from a markdown file
riff run --prompt path/to/task.md

# GitHub integration (requires gh installed + authed)
riff run --gh                    # create a new issue + draft PR, link them, commit/push each iter
riff run --gh --issue 42         # use existing issue #42 (seeds the task), create a new PR
riff run --gh --pr 17            # create a new issue, add commits to existing PR #17
riff run --gh --issue 42 --pr 17 # use both existing  (--issue/--pr without --gh is an error)

# List all sessions for this project
riff sessions
```

## Running in a sandbox (recommended)

riff drives the worker and reviewer with `claude --dangerously-skip-permissions`,
so those agents can run arbitrary commands and edit files **wherever riff runs**.
Run it on your host and that's your host. To contain it, run *all of riff* inside
a [Docker sandbox](https://www.docker.com/) (`sbx`), so riff, claude, the project,
and the `.riff/` logs all live in the one isolated box, and skipping permission
prompts becomes safe because nothing escapes it.

```bash
# 1. Create / start a sandbox with your project mounted
sbx create            # or: sbx run --branch <branch> from inside the repo

# 2. Open an interactive shell in it (riff's editor + planning Q&A need a TTY)
sbx exec -it <sandbox-name> bash

# 3. Inside the sandbox, one-time setup:
claude   # then /login to authenticate claude in this box
uv tool install git+https://github.com/antonemanuel/riff   # or: pip install ...

# 4. Run riff normally; it's now fully sandboxed
cd /path/to/project
riff run
```

Or run a single command without a shell (still needs `-it` for the editor/prompts):

```bash
sbx exec -it <sandbox-name> -- riff run -p "add rate limiting to the API"
```

> claude must be authenticated **inside** the sandbox (step 3); host auth does
> not carry over. `.riff/sessions/` is written inside the sandbox; use `sbx cp`
> to pull logs out if you want them on the host.

## Configuration

Context files (`PLANNER.md`, `WORKER.md`, `REVIEWER.md`) live at `~/.config/riff/` by default.
Run `riff init` to create them there, then edit to add project-specific instructions.

```bash
# Use a different context dir (e.g. shared team templates)
riff config set-context-dir ~/my-team-context/

# Per-session override
riff run --context-dir ./project-context/

# Show current config
riff config show
```

## Session Layout

Each session is logged under `.riff/sessions/<timestamp-slug>/` at the root of the
git repo you run riff in (it resolves the repo top-level, so it doesn't matter which
subdirectory you launch from; falls back to the current directory if not a git repo):

```
.riff/sessions/20260622-143022-add-rate-limiting/
├── PROMPT.md          # raw task as written
├── CLARIFICATIONS.md  # planner Q&A transcript
├── ISSUE.md           # structured plan (written by the planner)
├── session.json       # state + each phase's claude session_id (for --resume)
├── iter-01/
│   ├── PR.md          # worker's PR summary
│   ├── REVIEW.md      # reviewer's decision
│   ├── worker.log     # readable transcript: assistant text + tool calls
│   └── reviewer.log   # readable transcript: assistant text + tool calls
└── iter-02/...
```

`.riff/` is gitignored by default, so logs stay local.

To see what an agent did, read the per-phase `*.log` transcript. To **continue or
inspect** that exact conversation later, `session.json` stores each phase's Claude
`session_id`; riff also prints a ready-to-run `claude --resume <id>` after each phase
(resume from the same repo / sandbox the session ran in).

## Planner, Worker & Reviewer Context

| File | Purpose |
|------|---------|
| `PLANNER.md` | Instructions for the planner agent: what to always clarify, what a complete ISSUE.md needs |
| `WORKER.md` | Instructions for the worker agent: test commands, style conventions, constraints |
| `REVIEWER.md` | Instructions for the reviewer: acceptance bar, focus areas, required checks |

These files are read from your configured context dir each run, so edit them once and reuse across all projects.

## GitHub integration (`--gh`)

Requires the [`gh`](https://cli.github.com/) CLI, installed and authenticated in whatever
environment riff runs (including the sandbox). If `gh` is missing/unauthed, `--gh` becomes a
no-op for that run rather than failing.

`--gh` turns on a linked **issue + PR** pair. By default both are *created*; pass `--issue N`
and/or `--pr N` to use existing ones instead. `--issue`/`--pr` without `--gh` is an error.

| | created (default) | existing (`--issue N` / `--pr N`) |
|------|----------------|------------------------|
| **issue** | After planning, created from `ISSUE.md` | Seeds the task from the issue body, then posts the plan back as a comment |
| **PR** | After iteration 1, opened as a **draft**; marked **ready** on approval | `gh pr checkout`'d, commits added to its branch |

With `--gh`, riff owns the git lifecycle: it branches off your current branch (`riff/<session>`),
commits + pushes the worker's changes after each iteration, and posts each review as a PR comment.
Per the squash-merge convention, the PR **title** is the commit subject and the **body** is the
commit description; the worker may refine both via `gh` and add comments. The PR body gets
`Closes #<issue>`.
