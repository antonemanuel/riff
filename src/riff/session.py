"""Session lifecycle: create, load, persist, query."""

import json
import re
from datetime import datetime
from pathlib import Path


def _slugify(text: str, max_len: int = 40) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text)
    return text[:max_len].rstrip("-")


class Session:
    def __init__(self, root: Path):
        self.root = root
        self._data: dict = {}
        if (root / "session.json").exists():
            self._data = json.loads((root / "session.json").read_text())

    # -- paths --

    @property
    def issue_path(self) -> Path:
        return self.root / "ISSUE.md"

    @property
    def clarifications_path(self) -> Path:
        return self.root / "CLARIFICATIONS.md"

    def iter_dir(self, n: int) -> Path:
        return self.root / f"iter-{n:02d}"

    def pr_path(self, n: int) -> Path:
        return self.iter_dir(n) / "PR.md"

    def review_path(self, n: int) -> Path:
        return self.iter_dir(n) / "REVIEW.md"

    def worker_log(self, n: int) -> Path:
        return self.iter_dir(n) / "worker.log"

    def reviewer_log(self, n: int) -> Path:
        return self.iter_dir(n) / "reviewer.log"

    # -- metadata --

    @property
    def task(self) -> str:
        return self._data.get("task", "")

    @property
    def status(self) -> str:
        return self._data.get("status", "planning")

    @status.setter
    def status(self, value: str) -> None:
        self._data["status"] = value
        self._save()

    @property
    def iteration(self) -> int:
        return self._data.get("iteration", 1)

    @iteration.setter
    def iteration(self, value: int) -> None:
        self._data["iteration"] = value
        self._save()

    @property
    def approved(self) -> bool:
        return self._data.get("approved", False)

    @approved.setter
    def approved(self, value: bool) -> None:
        self._data["approved"] = value
        self._save()

    @property
    def issue_number(self) -> int | None:
        return self._data.get("issue_number")

    @issue_number.setter
    def issue_number(self, value: int | None) -> None:
        self._data["issue_number"] = value
        self._save()

    @property
    def pr_number(self) -> int | None:
        return self._data.get("pr_number")

    @pr_number.setter
    def pr_number(self, value: int | None) -> None:
        self._data["pr_number"] = value
        self._save()

    @property
    def branch(self) -> str | None:
        return self._data.get("branch")

    @branch.setter
    def branch(self, value: str | None) -> None:
        self._data["branch"] = value
        self._save()

    @property
    def base_branch(self) -> str | None:
        return self._data.get("base_branch")

    @base_branch.setter
    def base_branch(self, value: str | None) -> None:
        self._data["base_branch"] = value
        self._save()

    # -- per-iteration phase records --

    def record_phase_session(self, n: int, phase: str, session_id: str | None) -> None:
        """Persist the claude session_id for a phase (worker/reviewer) of iteration n."""
        if not session_id:
            return
        iters = self._data.setdefault("iterations", {})
        iters.setdefault(str(n), {})[f"{phase}_session_id"] = session_id
        self._save()

    def _save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "session.json").write_text(json.dumps(self._data, indent=2))

    # -- factory --

    @classmethod
    def create(cls, sessions_root: Path, task: str) -> "Session":
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        slug = _slugify(task)
        name = f"{ts}-{slug}" if slug else ts
        root = sessions_root / name
        root.mkdir(parents=True, exist_ok=True)
        session = cls(root)
        session._data = {
            "task": task,
            "status": "planning",
            "iteration": 1,
            "approved": False,
            "created_at": datetime.now().isoformat(),
        }
        session._save()
        return session

    @classmethod
    def list_all(cls, sessions_root: Path) -> list["Session"]:
        if not sessions_root.exists():
            return []
        dirs = sorted(
            [d for d in sessions_root.iterdir() if d.is_dir()],
            key=lambda d: d.name,
            reverse=True,
        )
        return [cls(d) for d in dirs]
