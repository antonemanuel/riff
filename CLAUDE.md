# riff — Rapid Iterative Feedback Flow or riff is for fuckery

## Project Overview

`riff` is a Python CLI tool that orchestrates a planner/worker/reviewer agent loop using the Claude Code CLI. It automates: plan → implement → review → repeat until approved.

## Package Layout

```
src/riff/
├── cli.py       # typer CLI (run, sessions, config, init)
├── session.py   # Session dataclass + disk persistence
├── runner.py    # claude subprocess wrappers (claude_print, claude_interactive)
├── prompts.py   # Prompt builders for each phase (planner, worker, reviewer)
├── forge.py     # GitHub (gh) + git helpers for --issue/--pr integration
└── config.py    # Context dir config (~/.config/riff/config.json)
```

## Dev Setup

```bash
uv sync
uv run riff --help
```

## Install Globally

```bash
uv tool install git+https://github.com/antonemanuel/riff
```

## Running Tests

```bash
uv run pytest
```

## Key Design Decisions

- The planner phase runs an interactive `claude` session (inherits the TTY) so AskUserQuestion and the full UI work; the loop resumes once ISSUE.md exists
- `claude --output-format stream-json -p --dangerously-skip-permissions` streams worker/reviewer output to the terminal and a readable transcript log
- Each worker/reviewer iteration is a fresh (clean-context) `claude` run; continuity is via files (ISSUE.md, prior PRs, latest review) + the working tree
- Sessions are stored in `.riff/sessions/` at the git repo root (gitignored); each phase's claude session_id is saved for `--resume`
- Context files (PLANNER.md, WORKER.md, REVIEWER.md) are global at `~/.config/riff/` by default, configurable per-session

## Coding Standards

- Python 3.11+, typed where practical
- No comments unless the why is non-obvious
- Keep prompts.py as the single source of truth for all agent instructions

## Commits

- Use [Conventional Commits](https://www.conventionalcommits.org/) for all commit messages (e.g. `feat:`, `fix:`, `chore:`, `docs:`, `refactor:`, `test:`), optionally with a scope like `feat(forge): ...`
