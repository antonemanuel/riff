"""Unit tests for riff's pure helpers and session persistence."""

import subprocess

import pytest
import typer

from riff.cli import _forge_title, _is_approved, _repo_root, _resolve_gh
from riff.forge import _parse_number
from riff.prompts import worker
from riff.session import Session, _slugify


class TestResolveGh:
    def test_off_by_default(self):
        assert _resolve_gh(False, None, None) == (None, None)

    def test_gh_creates_both(self):
        assert _resolve_gh(True, None, None) == ("create", "create")

    def test_existing_overrides(self):
        assert _resolve_gh(True, 42, None) == (42, "create")
        assert _resolve_gh(True, None, 17) == ("create", 17)
        assert _resolve_gh(True, 42, 17) == (42, 17)

    def test_number_without_gh_raises(self):
        with pytest.raises(typer.BadParameter):
            _resolve_gh(False, 42, None)
        with pytest.raises(typer.BadParameter):
            _resolve_gh(False, None, 17)


class TestForgeTitle:
    def test_prefers_first_heading(self):
        assert (
            _forge_title("# Add water filter\n\n## Problem\n...", "task")
            == "Add water filter"
        )

    def test_falls_back_to_first_task_line(self):
        assert (
            _forge_title("no heading here", "  Build the thing\nmore")
            == "Build the thing"
        )

    def test_truncates_to_72(self):
        title = _forge_title("", "x" * 200)
        assert len(title) == 72


class TestParseNumber:
    def test_issue_url(self):
        assert _parse_number("https://github.com/me/repo/issues/42") == 42

    def test_pr_url_trailing_newline_and_slash(self):
        assert _parse_number("\nhttps://github.com/me/repo/pull/17/\n") == 17

    def test_uses_last_line(self):
        out = "Creating pull request...\nhttps://github.com/me/repo/pull/99"
        assert _parse_number(out) == 99


class TestRepoRoot:
    def test_finds_repo_root_from_subdir(self, tmp_path):
        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
        sub = tmp_path / "a" / "b"
        sub.mkdir(parents=True)
        assert _repo_root(sub).resolve() == tmp_path.resolve()

    def test_falls_back_to_start_when_not_a_repo(self, tmp_path):
        # tmp_path has no .git and (in CI) no parent repo
        assert _repo_root(tmp_path) == tmp_path.resolve()


class TestSlugify:
    def test_basic(self):
        assert (
            _slugify("Add rate limiting to the API") == "add-rate-limiting-to-the-api"
        )

    def test_strips_punctuation(self):
        assert _slugify("Fix bug #42: crash!") == "fix-bug-42-crash"

    def test_collapses_separators(self):
        assert _slugify("a   b___c--d") == "a-b-c-d"

    def test_truncates_and_trims_trailing_dash(self):
        slug = _slugify("word " * 20, max_len=10)
        assert len(slug) <= 10
        assert not slug.endswith("-")

    def test_empty(self):
        assert _slugify("!!!") == ""


class TestIsApproved:
    def test_exact_first_line(self):
        assert _is_approved("STATUS: APPROVED\n\n## Summary\nok") is True

    def test_leading_whitespace(self):
        assert _is_approved("\n\n  STATUS: APPROVED\n") is True

    def test_changes_requested(self):
        assert _is_approved("STATUS: CHANGES_REQUESTED\n\n## Issues\n- x") is False

    def test_template_line_not_approved(self):
        assert _is_approved("STATUS: APPROVED   (or CHANGES_REQUESTED)") is False

    def test_approved_only_on_first_line(self):
        assert _is_approved("## Summary\nSTATUS: APPROVED") is False

    def test_empty(self):
        assert _is_approved("") is False
        assert _is_approved("   \n  ") is False


class TestSession:
    def test_create_and_reload_roundtrip(self, tmp_path):
        s = Session.create(tmp_path, "Add a feature")
        assert s.task == "Add a feature"
        assert s.status == "planning"
        assert s.iteration == 1
        assert s.approved is False

        reloaded = Session(s.root)
        assert reloaded.task == "Add a feature"
        assert reloaded.status == "planning"

    def test_setters_persist_to_disk(self, tmp_path):
        s = Session.create(tmp_path, "task")
        s.status = "working"
        s.iteration = 3
        s.approved = True

        reloaded = Session(s.root)
        assert reloaded.status == "working"
        assert reloaded.iteration == 3
        assert reloaded.approved is True

    def test_forge_fields_persist(self, tmp_path):
        s = Session.create(tmp_path, "task")
        assert s.issue_number is None and s.pr_number is None and s.branch is None
        s.issue_number = 42
        s.pr_number = 17
        s.branch = "riff/add-water-filter"

        reloaded = Session(s.root)
        assert reloaded.issue_number == 42
        assert reloaded.pr_number == 17
        assert reloaded.branch == "riff/add-water-filter"

    def test_iteration_paths(self, tmp_path):
        s = Session.create(tmp_path, "task")
        assert s.pr_path(2).name == "PR.md"
        assert s.pr_path(2).parent.name == "iter-02"
        assert s.review_path(11).parent.name == "iter-11"

    def test_list_all(self, tmp_path):
        Session.create(tmp_path, "one")
        Session.create(tmp_path, "two")
        assert len(Session.list_all(tmp_path)) == 2

    def test_record_phase_session_persists(self, tmp_path):
        s = Session.create(tmp_path, "task")
        s.record_phase_session(1, "worker", "sid-abc")
        s.record_phase_session(1, "reviewer", "sid-def")
        s.record_phase_session(2, "worker", "sid-ghi")

        iters = Session(s.root)._data["iterations"]
        assert iters["1"] == {
            "worker_session_id": "sid-abc",
            "reviewer_session_id": "sid-def",
        }
        assert iters["2"] == {"worker_session_id": "sid-ghi"}

    def test_record_phase_session_ignores_none(self, tmp_path):
        s = Session.create(tmp_path, "task")
        s.record_phase_session(1, "worker", None)
        assert "iterations" not in Session(s.root)._data


class TestWorkerPrompt:
    def _issue(self, tmp_path):
        issue = tmp_path / "ISSUE.md"
        issue.write_text("## Problem\nDo the thing")
        return issue

    def test_first_iteration_has_no_history_or_review(self, tmp_path):
        prompt = worker(
            session_dir=tmp_path,
            issue_path=self._issue(tmp_path),
            pr_path=tmp_path / "PR.md",
            project_root=tmp_path,
            worker_md=tmp_path / "WORKER.md",
            iteration=1,
        )
        assert "WORK SO FAR" not in prompt
        assert "REVIEWER FEEDBACK" not in prompt
        assert "Implement the changes described in the issue." in prompt

    def test_later_iteration_indexes_prior_prs_and_review(self, tmp_path):
        pr1 = tmp_path / "PR1.md"
        pr1.write_text("first pr summary")
        pr2 = tmp_path / "PR2.md"
        pr2.write_text("second pr summary")
        review = tmp_path / "REVIEW.md"
        review.write_text("STATUS: CHANGES_REQUESTED\nfix the bug")

        prompt = worker(
            session_dir=tmp_path,
            issue_path=self._issue(tmp_path),
            pr_path=tmp_path / "PR.md",
            project_root=tmp_path,
            worker_md=tmp_path / "WORKER.md",
            iteration=3,
            prev_review_path=review,
            prev_pr_paths=[pr1, pr2],
        )
        assert "WORK SO FAR" in prompt
        assert "### Iteration 1 PR" in prompt
        assert "### Iteration 2 PR" in prompt
        assert "first pr summary" in prompt
        assert "second pr summary" in prompt
        assert "REVIEWER FEEDBACK (iteration 2" in prompt
        assert "ALREADY in the working tree" in prompt

    def test_missing_prior_pr_files_are_skipped(self, tmp_path):
        prompt = worker(
            session_dir=tmp_path,
            issue_path=self._issue(tmp_path),
            pr_path=tmp_path / "PR.md",
            project_root=tmp_path,
            worker_md=tmp_path / "WORKER.md",
            iteration=2,
            prev_pr_paths=[tmp_path / "does-not-exist.md"],
        )
        assert "WORK SO FAR" not in prompt
