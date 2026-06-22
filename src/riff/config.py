"""Config management: context dir, global settings."""

import json
from pathlib import Path

_DEFAULT_CONTEXT_DIR = Path.home() / ".config" / "riff"
_CONFIG_FILE = _DEFAULT_CONTEXT_DIR / "config.json"


def get_context_dir(override: Path | None = None) -> Path:
    if override:
        return override.expanduser().resolve()

    if _CONFIG_FILE.exists():
        try:
            data = json.loads(_CONFIG_FILE.read_text())
            if "context_dir" in data:
                return Path(data["context_dir"]).expanduser().resolve()
        except (json.JSONDecodeError, KeyError):
            pass

    return _DEFAULT_CONTEXT_DIR


def set_context_dir(path: Path) -> None:
    path = path.expanduser().resolve()
    _DEFAULT_CONTEXT_DIR.mkdir(parents=True, exist_ok=True)
    data = {}
    if _CONFIG_FILE.exists():
        try:
            data = json.loads(_CONFIG_FILE.read_text())
        except json.JSONDecodeError:
            pass
    data["context_dir"] = str(path)
    _CONFIG_FILE.write_text(json.dumps(data, indent=2))


def show_config() -> dict:
    context_dir = get_context_dir()
    return {
        "context_dir": str(context_dir),
        "config_file": str(_CONFIG_FILE),
        "planner_md": str(context_dir / "PLANNER.md"),
        "worker_md": str(context_dir / "WORKER.md"),
        "reviewer_md": str(context_dir / "REVIEWER.md"),
    }
