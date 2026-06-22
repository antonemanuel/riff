"""Subprocess wrappers for invoking the claude CLI."""

import json
import subprocess
import sys
from pathlib import Path


def _base_args(bypass_permissions: bool = False) -> list[str]:
    args = ["claude"]
    if bypass_permissions:
        args.append("--dangerously-skip-permissions")
    return args


# Input keys to surface when showing a tool call, in priority order.
_TOOL_ARG_KEYS = (
    "command",
    "file_path",
    "path",
    "pattern",
    "query",
    "url",
    "description",
    "prompt",
)


def _tool_label(block: dict) -> str:
    """Concise label for a tool_use block, e.g. `Read(app.py)`."""
    name = block.get("name", "tool")
    inp = block.get("input") or {}
    arg = ""
    for key in _TOOL_ARG_KEYS:
        val = inp.get(key)
        if isinstance(val, str) and val.strip():
            arg = " ".join(val.split())
            break
    if len(arg) > 70:
        arg = arg[:67] + "..."
    return f"{name}({arg})" if arg else name


def claude_interactive(
    prompt: str,
    *,
    bypass_permissions: bool = False,
    cwd: Path | None = None,
) -> int:
    """Run claude in interactive TUI mode, seeded with `prompt`.

    Inherits the terminal (stdin/stdout/stderr) so Claude Code's interactive UI —
    including the AskUserQuestion tool — works normally. Blocks until the user
    exits the session. Returns the process exit code.
    """
    args = _base_args(bypass_permissions)
    args.append(prompt)
    return subprocess.run(args, cwd=cwd).returncode


def claude_print(
    prompt: str,
    *,
    resume_id: str | None = None,
    bypass_permissions: bool = False,
    log_file: Path | None = None,
    cwd: Path | None = None,
) -> tuple[int, str, str | None]:
    """Run `claude -p` with stream-json, printing assistant text live.

    Streams assistant text and a one-line marker per tool call to stdout, so each
    phase shows progress in real time. When `log_file` is given, the same
    human-readable feed is written there as a transcript of what the agent did.

    Returns (exit_code, final_text, claude_session_id), where final_text is the
    model's final result text (the worker/reviewer phases read their outputs from
    disk, so they ignore it).
    """
    args = _base_args(bypass_permissions)
    if resume_id:
        args += ["--resume", resume_id]
    args += ["--output-format", "stream-json", "--verbose", "-p", prompt]

    log_handle = open(log_file, "w") if log_file else None
    session_id: str | None = None
    result_text: str | None = None
    streamed: list[str] = []

    def emit(term: str, plain: str | None = None) -> None:
        """Write to the terminal (with styling) and the plain-text transcript."""
        sys.stdout.write(term)
        sys.stdout.flush()
        if log_handle:
            log_handle.write(plain if plain is not None else term)
            log_handle.flush()

    try:
        proc = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=cwd,
        )
        assert proc.stdout
        for raw_line in proc.stdout:
            try:
                obj = json.loads(raw_line)
            except json.JSONDecodeError:
                # Non-JSON line (e.g. status banner) — show but don't log as transcript
                sys.stdout.write(raw_line)
                sys.stdout.flush()
                continue

            if not session_id and "session_id" in obj:
                session_id = obj["session_id"]

            msg_type = obj.get("type")
            if msg_type == "assistant":
                for block in (obj.get("message") or {}).get("content", []):
                    btype = block.get("type")
                    if btype == "text":
                        text = block["text"]
                        streamed.append(text)
                        emit(text)
                    elif btype == "tool_use":
                        label = _tool_label(block)
                        emit(f"\n\033[2m· {label}\033[0m\n", f"\n· {label}\n")
            elif msg_type == "result" and isinstance(obj.get("result"), str):
                result_text = obj["result"]

        proc.wait()
        final_text = result_text if result_text is not None else "".join(streamed)
        return proc.returncode, final_text, session_id
    finally:
        if log_handle:
            log_handle.close()
