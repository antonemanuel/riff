"""GitHub (gh) + git helpers for issue/PR integration.

All external effects live here so the rest of riff stays testable. Every call
shells out to `gh` or `git`; callers are responsible for checking gh_available()
and handling ForgeError.
"""

import json
import subprocess
from pathlib import Path


class ForgeError(RuntimeError):
    """A gh/git command failed."""


def _run(
    args: list[str], cwd: Path | None = None, check: bool = True
) -> subprocess.CompletedProcess:
    try:
        proc = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    except OSError as e:
        raise ForgeError(f"could not run {args[0]!r}: {e}") from e
    if check and proc.returncode != 0:
        raise ForgeError(
            f"{' '.join(args[:3])} failed: {proc.stderr.strip() or proc.stdout.strip()}"
        )
    return proc


def gh_available() -> bool:
    return _run(["gh", "--version"], check=False).returncode == 0


def _parse_number(text: str) -> int:
    """gh create prints the new issue/PR URL; the number is the last path segment."""
    url = text.strip().splitlines()[-1].strip()
    return int(url.rstrip("/").split("/")[-1])


# -- issues --


def get_issue(number: int, cwd: Path | None = None) -> dict:
    out = _run(
        [
            "gh",
            "issue",
            "view",
            str(number),
            "--json",
            "number,title,body,comments,url,state",
        ],
        cwd=cwd,
    )
    return json.loads(out.stdout)


def create_issue(title: str, body: str, cwd: Path | None = None) -> int:
    out = _run(["gh", "issue", "create", "--title", title, "--body", body], cwd=cwd)
    return _parse_number(out.stdout)


def comment_issue(number: int, body: str, cwd: Path | None = None) -> None:
    _run(["gh", "issue", "comment", str(number), "--body", body], cwd=cwd)


# -- pull requests --


def create_pr(
    title: str,
    body: str,
    *,
    base: str | None = None,
    head: str | None = None,
    draft: bool = True,
    cwd: Path | None = None,
) -> int:
    args = ["gh", "pr", "create", "--title", title, "--body", body]
    if base:
        args += ["--base", base]
    if head:
        args += ["--head", head]
    if draft:
        args.append("--draft")
    out = _run(args, cwd=cwd)
    return _parse_number(out.stdout)


def comment_pr(number: int, body: str, cwd: Path | None = None) -> None:
    _run(["gh", "pr", "comment", str(number), "--body", body], cwd=cwd)


def checkout_pr(number: int, cwd: Path | None = None) -> None:
    _run(["gh", "pr", "checkout", str(number)], cwd=cwd)


def mark_pr_ready(number: int, cwd: Path | None = None) -> None:
    _run(["gh", "pr", "ready", str(number)], cwd=cwd)


# -- git --


def current_branch(cwd: Path | None = None) -> str:
    return _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd).stdout.strip()


def create_branch(name: str, cwd: Path | None = None) -> None:
    _run(["git", "switch", "-c", name], cwd=cwd)


def has_uncommitted_changes(cwd: Path | None = None) -> bool:
    return bool(_run(["git", "status", "--porcelain"], cwd=cwd).stdout.strip())


def commit_all(message: str, cwd: Path | None = None) -> None:
    _run(["git", "add", "-A"], cwd=cwd)
    _run(["git", "commit", "-m", message], cwd=cwd)


def push(branch: str, cwd: Path | None = None) -> None:
    _run(["git", "push", "-u", "origin", branch], cwd=cwd)
