"""riff CLI — Rapid Iterative Feedback Flow or riff is for fuckery."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Annotated, Callable, Optional, TypeVar

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text

from riff import __version__, forge
from riff.config import get_context_dir, set_context_dir, show_config
from riff.prompts import planner, reviewer, worker
from riff.runner import claude_interactive, claude_print
from riff.session import Session

_T = TypeVar("_T")

app = typer.Typer(
    name="riff",
    help="Rapid Iterative Feedback Flow or riff is for fuckery — automated claude agent review loop.",
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)
console = Console()


def _repo_root(start: Optional[Path] = None) -> Path:
    """Git repo top-level for `start` (default cwd), or `start` itself if not a repo."""
    start = (start or Path.cwd()).resolve()
    try:
        out = subprocess.run(
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
        )
        if out.returncode == 0 and out.stdout.strip():
            return Path(out.stdout.strip())
    except OSError:
        pass
    return start


def _project_root() -> Path:
    return _repo_root()


def _sessions_root() -> Path:
    return _project_root() / ".riff" / "sessions"


_CONTEXT_FILES = (
    ("PLANNER.md", "_default_planner_md"),
    ("WORKER.md", "_default_worker_md"),
    ("REVIEWER.md", "_default_reviewer_md"),
)


def _ensure_context_dir(context_dir: Path) -> None:
    context_dir.mkdir(parents=True, exist_ok=True)
    for name, default_fn in _CONTEXT_FILES:
        dest = context_dir / name
        if not dest.exists():
            dest.write_text(globals()[default_fn]())


def _default_planner_md() -> str:
    return """# Planner Agent Instructions

Add project-specific instructions for the planner agent (the interactive
clarification phase that writes ISSUE.md) here.

Examples:
- Topics to always clarify (deployment target, backwards-compat, data migrations)
- What a complete ISSUE.md must contain for this project
- Domain context the planner should ask about
- Constraints that shape the implementation plan
"""


def _default_worker_md() -> str:
    return """# Worker Agent Instructions

Add project-specific instructions for the worker agent here.

Examples:
- Preferred coding style or conventions
- Which test command to run (e.g. `uv run pytest`)
- Directories to avoid modifying
- Any constraints the worker should respect
"""


def _default_reviewer_md() -> str:
    return """# Reviewer Agent Instructions

Add project-specific instructions for the reviewer agent here.

Examples:
- Review focus areas (security, performance, style)
- Acceptance bar (what counts as APPROVED)
- Tests that must pass before approval
- Code style standards to enforce
"""


def _run_planner(
    session: Session,
    task: str,
    project_root: Path,
    context_dir: Path,
) -> bool:
    """Hand the terminal to an interactive Claude session. True once ISSUE.md exists."""
    planner_md = context_dir / "PLANNER.md"

    console.print(Rule("[bold cyan]Planning Phase (interactive)[/bold cyan]"))
    console.print(f"[dim]Session: {session.root.name}[/dim]")
    console.print(
        "[dim]The planner will ask clarifying questions interactively. When it has "
        "written ISSUE.md, exit Claude (/exit or Ctrl-D) to continue the loop.[/dim]\n"
    )

    initial_prompt = planner(
        task=task,
        issue_path=session.issue_path,
        clarifications_path=session.clarifications_path,
        project_root=project_root,
        planner_md=planner_md,
    )

    claude_interactive(initial_prompt, bypass_permissions=True, cwd=project_root)

    if not session.issue_path.exists():
        console.print(
            "\n[yellow]No ISSUE.md was written; clarification incomplete. "
            "Run `riff run` again when ready.[/yellow]"
        )
        return False
    return True


def _run_worker(session: Session, project_root: Path, context_dir: Path) -> bool:
    n = session.iteration
    iter_dir = session.iter_dir(n)
    iter_dir.mkdir(parents=True, exist_ok=True)

    prev_review = session.review_path(n - 1) if n > 1 else None
    prev_prs = [session.pr_path(i) for i in range(1, n)]

    console.print(Rule(f"[bold green]Worker iteration {n}[/bold green]"))
    console.print(f"[dim]Writing PR to: {session.pr_path(n)}[/dim]\n")

    prompt = worker(
        session_dir=session.root,
        issue_path=session.issue_path,
        pr_path=session.pr_path(n),
        project_root=project_root,
        worker_md=context_dir / "WORKER.md",
        iteration=n,
        prev_review_path=prev_review,
        prev_pr_paths=prev_prs,
        issue_number=session.issue_number,
        pr_number=session.pr_number,
    )

    exit_code, _, claude_sid = claude_print(
        prompt,
        bypass_permissions=True,
        log_file=session.worker_log(n),
        cwd=project_root,
    )
    session.record_phase_session(n, "worker", claude_sid)
    if claude_sid:
        console.print(f"\n[dim]Worker session:[/dim] claude --resume {claude_sid}")

    if exit_code != 0:
        console.print(f"[red]Worker exited with code {exit_code}[/red]")
        return False

    if not session.pr_path(n).exists():
        console.print("[red]Worker did not produce PR.md; check worker.log[/red]")
        return False

    console.print(f"\n[green]PR written:[/green] {session.pr_path(n)}")
    return True


def _is_approved(review_text: str) -> bool:
    """A review is approved iff its first non-empty line is exactly STATUS: APPROVED."""
    stripped = review_text.strip()
    if not stripped:
        return False
    return stripped.splitlines()[0].strip() == "STATUS: APPROVED"


def _run_reviewer(session: Session, project_root: Path, context_dir: Path) -> bool:
    n = session.iteration
    iter_dir = session.iter_dir(n)
    iter_dir.mkdir(parents=True, exist_ok=True)

    console.print(Rule(f"[bold yellow]Reviewer iteration {n}[/bold yellow]"))
    console.print(f"[dim]Writing review to: {session.review_path(n)}[/dim]\n")

    prompt = reviewer(
        issue_path=session.issue_path,
        pr_path=session.pr_path(n),
        review_path=session.review_path(n),
        project_root=project_root,
        reviewer_md=context_dir / "REVIEWER.md",
        iteration=n,
    )

    exit_code, _, claude_sid = claude_print(
        prompt,
        bypass_permissions=True,
        log_file=session.reviewer_log(n),
        cwd=project_root,
    )
    session.record_phase_session(n, "reviewer", claude_sid)
    if claude_sid:
        console.print(f"\n[dim]Reviewer session:[/dim] claude --resume {claude_sid}")

    if exit_code != 0:
        console.print(f"[red]Reviewer exited with code {exit_code}[/red]")
        return False

    if not session.review_path(n).exists():
        console.print(
            "[red]Reviewer did not produce REVIEW.md; check reviewer.log[/red]"
        )
        return False

    review_text = session.review_path(n).read_text()
    approved = _is_approved(review_text)

    if approved:
        console.print(
            "\n[bold green]APPROVED[/bold green]. The reviewer accepted the work."
        )
        session.approved = True
    else:
        console.print("\n[bold yellow]CHANGES REQUESTED[/bold yellow]. See review:")
        console.print(
            Panel(
                Markdown(review_text),
                title="[yellow]Review[/yellow]",
                border_style="yellow",
            )
        )

    return True


def _get_task_text(prompt: Optional[str]) -> str:
    """Resolve task text from --prompt string/file, or open vim for editing."""
    if prompt:
        candidate = Path(prompt)
        if candidate.exists() and candidate.is_file():
            return candidate.read_text().strip()
        return prompt.strip()

    # No --prompt: open the editor with a fresh scratch markdown file
    import tempfile

    editor = _find_editor()
    placeholder = "<!-- Describe your task here, then save and quit -->\n\n"
    with tempfile.NamedTemporaryFile(
        mode="w+", suffix=".md", prefix="riff-prompt-", delete=False
    ) as f:
        f.write(placeholder)
        scratch = Path(f.name)

    try:
        ret = subprocess.run([editor, str(scratch)])
        if ret.returncode != 0:
            console.print(f"[red]Editor exited with code {ret.returncode}[/red]")
            raise typer.Exit(1)

        text = scratch.read_text().strip()
    finally:
        scratch.unlink(missing_ok=True)

    # Remove the placeholder comment line
    lines = [line for line in text.splitlines() if not line.strip().startswith("<!--")]
    text = "\n".join(lines).strip()
    if not text:
        console.print("[red]No task description written. Aborting.[/red]")
        raise typer.Exit(1)
    return text


def _find_editor() -> str:
    import os
    import shutil

    for var in ("VISUAL", "EDITOR"):
        val = os.environ.get(var)
        if val and shutil.which(val.split()[0]):
            return val
    for fallback in ("vim", "vi", "nano"):
        if shutil.which(fallback):
            return fallback
    console.print(
        "[red]No editor found. Set $VISUAL or $EDITOR, or use --prompt.[/red]"
    )
    raise typer.Exit(1)


# --- GitHub issue / PR integration (all gh/git effects go through riff.forge) ---


def _read(path: Path) -> str:
    return path.read_text() if path.exists() else ""


def _resolve_gh(
    gh: bool, issue: int | None, pr: int | None
) -> tuple[str | int | None, str | int | None]:
    """Resolve GitHub integration modes from the flags.

    --gh turns it on (create a new issue + draft PR). --issue/--pr override with an
    existing number and require --gh. Returns (issue_mode, pr_mode), each of which is
    None (off) | "create" | int (existing #n).
    """
    if (issue is not None or pr is not None) and not gh:
        raise typer.BadParameter("--issue / --pr require --gh")
    if not gh:
        return None, None
    return (
        issue if issue is not None else "create",
        pr if pr is not None else "create",
    )


def _forge_title(issue_text: str, task: str) -> str:
    """Concise title (the squash-merge subject): first markdown heading, else first line."""
    for line in issue_text.splitlines():
        s = line.strip()
        if s.startswith("#"):
            return s.lstrip("#").strip()[:72]
    first = next((ln for ln in task.splitlines() if ln.strip()), "riff task")
    return first.strip()[:72]


def _forge_do(action: str, fn: Callable[[], object]) -> bool:
    """Run a forge side-effect; warn (don't crash the loop) on failure."""
    try:
        fn()
        return True
    except forge.ForgeError as e:
        console.print(f"[yellow]gh/git: {action} failed: {e}[/yellow]")
        return False


def _forge_get(action: str, fn: Callable[[], _T]) -> _T | None:
    try:
        return fn()
    except forge.ForgeError as e:
        console.print(f"[yellow]gh/git: {action} failed: {e}[/yellow]")
        return None


def _forge_sync_issue(
    session: Session, project_root: Path, issue_mode: str | int | None
) -> None:
    """After planning: create the issue from ISSUE.md, or annotate an existing one."""
    issue_text = _read(session.issue_path)
    if issue_mode == "create":
        title = _forge_title(issue_text, session.task)
        num = _forge_get(
            "create issue",
            lambda: forge.create_issue(title, issue_text or session.task, project_root),
        )
        if num is not None:
            session.issue_number = num
            console.print(f"[green]Created issue #{num}[/green]")
    elif isinstance(issue_mode, int):
        issue_no = issue_mode
        _forge_do(
            f"comment issue #{issue_no}",
            lambda: forge.comment_issue(
                issue_no, f"### riff plan\n\n{issue_text}", project_root
            ),
        )


def _forge_setup_branch(
    session: Session, project_root: Path, pr_mode: str | int
) -> None:
    if isinstance(pr_mode, int):
        pr_no = pr_mode
        if _forge_do(
            f"checkout PR #{pr_no}", lambda: forge.checkout_pr(pr_no, project_root)
        ):
            session.pr_number = pr_no
            session.branch = _forge_get(
                "read branch", lambda: forge.current_branch(project_root)
            )
    else:  # "create"
        base = _forge_get("read branch", lambda: forge.current_branch(project_root))
        name = f"riff/{session.root.name}"
        if _forge_do(
            f"create branch {name}", lambda: forge.create_branch(name, project_root)
        ):
            session.branch = name
            session.base_branch = base


def _forge_commit_push(session: Session, project_root: Path, n: int) -> bool:
    if not _forge_get(
        "git status", lambda: forge.has_uncommitted_changes(project_root)
    ):
        return False
    title = _forge_title(_read(session.issue_path), session.task)
    if not _forge_do(
        f"commit iteration {n}",
        lambda: forge.commit_all(f"{title} (riff iter {n})", project_root),
    ):
        return False
    branch = session.branch
    if branch:
        _forge_do("push", lambda: forge.push(branch, project_root))
    return True


def _forge_open_pr(session: Session, project_root: Path) -> None:
    title = _forge_title(_read(session.issue_path), session.task)
    body = (
        _read(session.pr_path(session.iteration))
        or _read(session.issue_path)
        or session.task
    )
    issue_no = session.issue_number
    if issue_no is not None:
        body += f"\n\nCloses #{issue_no}"
    base = session.base_branch
    num = _forge_get(
        "open draft PR",
        lambda: forge.create_pr(title, body, base=base, draft=True, cwd=project_root),
    )
    if num is not None:
        session.pr_number = num
        console.print(
            f"[green]Opened draft PR #{num}[/green] [dim](marked ready on approval)[/dim]"
        )
        if issue_no is not None:
            _forge_do(
                "link issue",
                lambda: forge.comment_issue(
                    issue_no, f"Tracked by PR #{num}.", project_root
                ),
            )


def _forge_post_review(session: Session, project_root: Path, n: int) -> None:
    pr_no = session.pr_number
    review = _read(session.review_path(n))
    if pr_no is not None and review:
        _forge_do(
            f"post review #{n}",
            lambda: forge.comment_pr(
                pr_no, f"### riff review (iteration {n})\n\n{review}", project_root
            ),
        )


@app.command()
def run(
    prompt: Annotated[
        Optional[str],
        typer.Option(
            "--prompt",
            "-p",
            help="Task description (string) or path to a .md file. "
            "Omit to open $EDITOR (default: vim).",
        ),
    ] = None,
    context_dir: Annotated[
        Optional[Path],
        typer.Option(
            "--context-dir",
            "-c",
            help="Directory containing PLANNER.md, WORKER.md and REVIEWER.md",
        ),
    ] = None,
    gh: Annotated[
        bool,
        typer.Option(
            "--gh",
            help="Enable GitHub integration (via gh): create a new issue + draft PR. "
            "Add --issue/--pr to use existing ones instead.",
        ),
    ] = False,
    issue: Annotated[
        Optional[int],
        typer.Option(
            "--issue",
            help="Use existing issue #N instead of creating one (requires --gh).",
        ),
    ] = None,
    pr: Annotated[
        Optional[int],
        typer.Option(
            "--pr", help="Use existing PR #N instead of creating one (requires --gh)."
        ),
    ] = None,
    max_iterations: Annotated[
        int, typer.Option("--max-iter", help="Safety limit")
    ] = 10,
) -> None:
    """Run a new riff session: plan → work → review loop."""
    issue_mode, pr_mode = _resolve_gh(gh, issue, pr)
    project_root = _project_root()

    if gh and not forge.gh_available():
        console.print(
            "[yellow]gh CLI not available or not authenticated; GitHub integration disabled for this run.[/yellow]"
        )
        issue_mode = pr_mode = None

    # Resolve the task — seed from an existing issue if one was given.
    if isinstance(issue_mode, int):
        issue_no = issue_mode
        data = _forge_get(
            f"fetch issue #{issue_no}", lambda: forge.get_issue(issue_no, project_root)
        )
        if data:
            task_text = f"# {data['title']}\n\n{data.get('body') or ''}".strip()
            if prompt:
                task_text += f"\n\n## Extra instructions\n{_get_task_text(prompt)}"
        else:
            issue_mode = None
            task_text = _get_task_text(prompt)
    else:
        task_text = _get_task_text(prompt)

    if not task_text:
        console.print("[red]Task description is required.[/red]")
        raise typer.Exit(1)

    ctx_dir = get_context_dir(context_dir)
    _ensure_context_dir(ctx_dir)

    sessions_root = _sessions_root()
    sessions_root.mkdir(parents=True, exist_ok=True)

    session = Session.create(sessions_root, task_text)
    # Persist the raw prompt so it can be referenced later
    (session.root / "PROMPT.md").write_text(task_text)
    if isinstance(issue_mode, int):
        session.issue_number = issue_mode
    console.print(
        Panel(
            Text(task_text, style="bold"),
            title=f"[bold]riff[/bold] new session: [cyan]{session.root.name}[/cyan]",
            subtitle=f"context: {ctx_dir}",
            border_style="bright_blue",
        )
    )
    console.print(f"[dim]Session stored in:[/dim] {session.root}")

    # Phase 1: Planning
    session.status = "planning"
    ok = _run_planner(session, task_text, project_root, ctx_dir)
    if not ok:
        console.print(
            "[red]Planning did not complete. Run `riff run` to begin a new session.[/red]"
        )
        raise typer.Exit(1)

    console.print(f"\n[green]ISSUE.md written:[/green] {session.issue_path}")

    # Connect the issue (create from the plan, or annotate the existing one).
    if issue_mode is not None:
        _forge_sync_issue(session, project_root, issue_mode)

    session.status = "working"

    # Phase 2+: Work → Review loop
    _run_loop(session, project_root, ctx_dir, max_iterations, pr_mode)


def _run_loop(
    session: Session,
    project_root: Path,
    ctx_dir: Path,
    max_iterations: int,
    pr_mode: str | int | None = None,
) -> None:
    if pr_mode is not None:
        _forge_setup_branch(session, project_root, pr_mode)

    while session.iteration <= max_iterations:
        n = session.iteration

        # Worker phase
        session.status = "working"
        if not _run_worker(session, project_root, ctx_dir):
            console.print(
                "[red]Worker failed. Check the worker log in the session dir.[/red]"
            )
            sys.exit(1)

        # Commit + push the worker's changes; open the draft PR after the first commit.
        if pr_mode is not None:
            committed = _forge_commit_push(session, project_root, n)
            if committed and pr_mode == "create" and session.pr_number is None:
                _forge_open_pr(session, project_root)

        # Reviewer phase
        session.status = "reviewing"
        if not _run_reviewer(session, project_root, ctx_dir):
            console.print(
                "[red]Reviewer failed. Check the reviewer log in the session dir.[/red]"
            )
            sys.exit(1)

        if session.pr_number is not None:
            _forge_post_review(session, project_root, n)

        if session.approved:
            session.status = "done"
            pr_no = session.pr_number
            if pr_no is not None:
                _forge_do(
                    "mark PR ready", lambda: forge.mark_pr_ready(pr_no, project_root)
                )
            _print_done(session)
            return

        # Next iteration
        session.iteration = n + 1
        console.print(f"\n[cyan]Starting iteration {session.iteration}...[/cyan]\n")

    console.print(f"[red]Reached max iterations ({max_iterations}). Stopping.[/red]")
    console.print("Review the session log in the session dir.")
    sys.exit(1)


def _print_done(session: Session) -> None:
    final_pr = session.pr_path(session.iteration)
    console.print()
    console.print(Rule("[bold green]Session Complete[/bold green]"))
    console.print(f"Session log: [cyan]{session.root}[/cyan]")
    if final_pr.exists():
        console.print("\n[bold]Final PR summary:[/bold]")
        console.print(Panel(Markdown(final_pr.read_text()), border_style="green"))


@app.command()
def sessions() -> None:
    """List all sessions for the current project."""
    all_sessions = Session.list_all(_sessions_root())
    if not all_sessions:
        console.print("[dim]No sessions found in this project.[/dim]")
        return

    for s in all_sessions:
        status_color = (
            "green" if s.approved else "cyan" if s.status == "done" else "yellow"
        )
        console.print(
            f"[{status_color}]{s.root.name}[/{status_color}]  "
            f"[dim]{s.status} | iter {s.iteration}[/dim]  "
            f"{s.task[:60]}"
        )


config_app = typer.Typer(
    help="Manage riff configuration.",
    context_settings={"help_option_names": ["-h", "--help"]},
)
app.add_typer(config_app, name="config")


@config_app.command("show")
def config_show() -> None:
    """Show current configuration."""
    cfg = show_config()
    for k, v in cfg.items():
        console.print(f"[dim]{k}:[/dim] {v}")


@config_app.command("set-context-dir")
def config_set_context_dir(
    path: Annotated[Path, typer.Argument(help="Path to context dir")],
) -> None:
    """Set the directory where PLANNER.md, WORKER.md and REVIEWER.md are stored."""
    set_context_dir(path)
    _ensure_context_dir(path.expanduser().resolve())
    console.print(f"[green]Context dir set to:[/green] {path.expanduser().resolve()}")
    console.print(
        "Edit PLANNER.md, WORKER.md and REVIEWER.md there to customize agent behavior."
    )


@app.command()
def init(
    context_dir: Annotated[
        Optional[Path],
        typer.Option("--context-dir", "-c"),
    ] = None,
) -> None:
    """Create default PLANNER.md, WORKER.md and REVIEWER.md in the context directory."""
    ctx_dir = get_context_dir(context_dir)
    ctx_dir.mkdir(parents=True, exist_ok=True)

    for name, default_fn in _CONTEXT_FILES:
        dest = ctx_dir / name
        if dest.exists():
            console.print(f"[dim]Already exists:[/dim] {dest}")
        else:
            dest.write_text(globals()[default_fn]())
            console.print(f"[green]Created:[/green] {dest}")

    console.print("\nEdit these files to customize agent behavior.")
    console.print(f"Context dir: [cyan]{ctx_dir}[/cyan]")


@app.callback(invoke_without_command=True)
def main(
    version: Annotated[bool, typer.Option("--version", "-v")] = False,
) -> None:
    if version:
        console.print(f"riff {__version__}")
        raise typer.Exit()
