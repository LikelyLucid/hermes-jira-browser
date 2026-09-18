from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "dashboard" / "repository_context.py"
spec = importlib.util.spec_from_file_location("jira_browser_repository_context", MODULE_PATH)
assert spec is not None and spec.loader is not None
repository_context = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repository_context)


class RepositoryContextResolutionTests(unittest.TestCase):
    def test_canonical_worktree_symlink_is_rejected_before_git_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            outside = root / "outside"
            (repo / ".worktrees").mkdir(parents=True)
            outside.mkdir()
            (outside / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
            (repo / ".worktrees" / "jira-DEMO-42").symlink_to(outside, target_is_directory=True)
            store = mock.Mock()
            store.get_project_mapping.return_value = {"repo_path": str(repo)}

            with mock.patch.object(repository_context, "_local_context") as local:
                result = repository_context.get_repository_context(store, "DEMO-42")

        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "worktree_unsafe")
        local.assert_not_called()

    def test_unregistered_nested_repository_is_not_treated_as_the_mapped_worktree(self):
        import subprocess

        def git(cwd: Path, *args: str) -> None:
            subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            worktree = repo / ".worktrees" / "jira-DEMO-42"
            worktree.mkdir(parents=True)
            git(repo, "init", "-q", "-b", "main")
            git(repo, "config", "user.name", "Test User")
            git(repo, "config", "user.email", "test@example.com")
            (repo / "README.md").write_text("mapped\n", encoding="utf-8")
            git(repo, "add", "README.md")
            git(repo, "commit", "-qm", "mapped")
            git(worktree, "init", "-q", "-b", "jira/DEMO-42")
            git(worktree, "config", "user.name", "Test User")
            git(worktree, "config", "user.email", "test@example.com")
            (worktree / "README.md").write_text("other\n", encoding="utf-8")
            git(worktree, "add", "README.md")
            git(worktree, "commit", "-qm", "other")
            store = mock.Mock()
            store.get_project_mapping.return_value = {"repo_path": str(repo)}

            result = repository_context.get_repository_context(store, "DEMO-42")

        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "worktree_repository_mismatch")

    def test_generic_subprocess_runner_bounds_captured_output_before_returning(self):
        result = repository_context._run_bounded(
            [sys.executable, "-c", f"print('x' * {repository_context.MAX_COMMAND_OUTPUT * 2})"],
            cwd=Path.cwd(),
            timeout=repository_context.GIT_TIMEOUT_SECONDS,
            environment=repository_context._git_environment(),
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(len(result.stdout), repository_context.MAX_COMMAND_OUTPUT)

    def test_missing_project_mapping_is_an_explicit_unavailable_state(self):
        store = mock.Mock()
        store.get_project_mapping.return_value = None

        result = repository_context.get_repository_context(store, "DEMO-42")

        self.assertEqual(result["status"], "unavailable")
        self.assertFalse(result["available"])
        self.assertEqual(result["reason"], "project_mapping_missing")
        self.assertEqual(result["issue_key"], "DEMO-42")
        store.get_project_mapping.assert_called_once_with("DEMO")

    def test_missing_mapped_repository_is_an_explicit_unavailable_state(self):
        store = mock.Mock()
        store.get_project_mapping.return_value = {"repo_path": "/does/not/exist"}

        result = repository_context.get_repository_context(store, "DEMO-42")

        self.assertEqual(result["status"], "unavailable")
        self.assertFalse(result["available"])
        self.assertEqual(result["reason"], "repository_missing")

    def test_missing_worktree_is_an_explicit_unavailable_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = mock.Mock()
            store.get_project_mapping.return_value = {"repo_path": tmp}

            result = repository_context.get_repository_context(store, "DEMO-42")

        self.assertEqual(result["status"], "unavailable")
        self.assertFalse(result["available"])
        self.assertEqual(result["reason"], "worktree_missing")

    def test_invalid_base_ref_is_renderer_safe_and_does_not_query_store(self):
        store = mock.Mock()

        result = repository_context.get_repository_context(store, "DEMO-42", base_ref="--upload-pack=evil")

        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "base_ref_invalid")
        store.get_project_mapping.assert_not_called()

    def test_revision_expressions_are_not_accepted_as_base_refs(self):
        store = mock.Mock()

        for value in ("HEAD^", "HEAD~2", "main:other", "main^{commit}"):
            with self.subTest(value=value):
                result = repository_context.get_repository_context(store, "DEMO-42", base_ref=value)
                self.assertEqual(result["reason"], "base_ref_invalid")

        store.get_project_mapping.assert_not_called()

    def test_missing_git_base_ref_is_explicitly_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            worktree = repo / ".worktrees" / "jira-DEMO-42"
            worktree.mkdir(parents=True)
            (worktree / ".git").write_text("gitdir: /tmp/git\n", encoding="utf-8")
            store = mock.Mock()
            store.get_project_mapping.return_value = {"repo_path": str(repo)}
            with mock.patch.object(repository_context, "_local_context", side_effect=RuntimeError("base_ref_unavailable")):
                result = repository_context.get_repository_context(store, "DEMO-42", base_ref="missing")

        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "base_ref_unavailable")

    def test_local_context_reports_bounded_git_state_against_validated_base(self):
        import subprocess

        def git(cwd: Path, *args: str) -> str:
            completed = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
            return completed.stdout.strip()

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            git(repo, "init", "-q", "-b", "main")
            git(repo, "config", "user.name", "Test User")
            git(repo, "config", "user.email", "test@example.com")
            (repo / "README.md").write_text("seed\n", encoding="utf-8")
            git(repo, "add", "README.md")
            git(repo, "commit", "-qm", "seed")
            worktree = repo / ".worktrees" / "jira-DEMO-42"
            worktree.parent.mkdir()
            git(repo, "worktree", "add", "-q", "-b", "jira/DEMO-42", str(worktree), "main")
            (worktree / "README.md").write_text("changed\n", encoding="utf-8")
            (worktree / "notes.txt").write_text("untracked\n", encoding="utf-8")
            git(worktree, "add", "README.md", "notes.txt")
            git(worktree, "commit", "-qm", "work in progress")
            (worktree / "README.md").write_text("changed again\n", encoding="utf-8")

            store = mock.Mock()
            store.get_project_mapping.return_value = {"repo_path": str(repo)}
            result = repository_context.get_repository_context(store, "DEMO-42", base_ref="main")

        self.assertEqual(result["status"], "available")
        self.assertTrue(result["available"])
        self.assertEqual(result["repository"]["branch"], "jira/DEMO-42")
        self.assertFalse(result["repository"]["clean"])
        self.assertEqual(result["repository"]["base_ref"], "main")
        self.assertEqual(result["repository"]["ahead"], 1)
        self.assertEqual(result["repository"]["behind"], 0)
        self.assertEqual(result["repository"]["changed_files"][0]["path"], "README.md")
        self.assertEqual(result["repository"]["recent_commits"][0]["subject"], "work in progress")

    def test_injected_github_adapter_returns_bounded_pr_and_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            worktree = repo / ".worktrees" / "jira-DEMO-42"
            worktree.mkdir(parents=True)
            (worktree / ".git").write_text("gitdir: /tmp/git\n", encoding="utf-8")
            adapter = mock.Mock()
            adapter.context.return_value = {
                "available": True,
                "reason": "ok",
                "pull_request": {
                    "url": "https://github.com/acme/demo/pull/42",
                    "number": 42,
                    "title": "Fix the dashboard",
                    "state": "OPEN",
                    "review_decision": "APPROVED",
                },
                "checks": [{"name": f"check-{index}", "state": "SUCCESS"} for index in range(100)],
            }
            store = mock.Mock()
            store.get_project_mapping.return_value = {"repo_path": str(repo)}
            with mock.patch.object(repository_context, "_local_context", return_value={
                "repo_path": str(repo), "worktree_path": str(worktree), "branch": "jira/DEMO-42",
                "base_ref": "HEAD", "base_commit": "abc", "clean": True, "changed_files": [],
                "changed_files_truncated": False, "recent_commits": [], "ahead": 0, "behind": 0,
            }):
                result = repository_context.get_repository_context(store, "DEMO-42", github=adapter)

        self.assertEqual(result["github"]["pull_request"]["number"], 42)
        self.assertEqual(len(result["github"]["checks"]), repository_context.MAX_CHECKS)
        self.assertTrue(result["github"]["checks_truncated"])
        adapter.context.assert_called_once_with(repository=repo.resolve(), branch="jira/DEMO-42")

    def test_gh_adapter_reports_absence_without_running_a_network_command(self):
        with mock.patch.object(repository_context.shutil, "which", return_value=None):
            adapter = repository_context.GhGitHubAdapter()

            with mock.patch.object(repository_context.subprocess, "run") as run:
                result = adapter.context(repository=Path("/repo"), branch="jira/DEMO-42")

        self.assertFalse(result["available"])
        self.assertEqual(result["reason"], "gh_not_installed")
        run.assert_not_called()

    def test_git_runner_is_noninteractive_argv_only_and_bounded(self):
        completed = mock.Mock(returncode=0, stdout="ok", stderr="")
        with mock.patch.object(repository_context, "_run_bounded", return_value=completed) as run:
            result = repository_context._run_git(Path("/repo"), "status", "--short")

        self.assertEqual(result.stdout, "ok")
        args, kwargs = run.call_args
        self.assertEqual(args[0][:2], ["git", "-c"])
        self.assertIn("core.fsmonitor=false", args[0])
        self.assertEqual(kwargs["cwd"], Path("/repo"))
        self.assertEqual(kwargs["timeout"], repository_context.GIT_TIMEOUT_SECONDS)
        self.assertEqual(kwargs["environment"]["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(kwargs["environment"]["GIT_CONFIG_NOSYSTEM"], "1")
        self.assertEqual(kwargs["environment"]["GIT_OPTIONAL_LOCKS"], "0")

    def test_repository_fsmonitor_command_is_not_executed(self):
        import subprocess

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            marker = repo / "fsmonitor-ran"
            hook = repo / "fsmonitor.sh"
            hook.write_text(
                f"#!/bin/sh\ntouch {marker}\nprintf 'token\\n'\n",
                encoding="utf-8",
            )
            hook.chmod(0o700)
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
            subprocess.run(["git", "config", "core.fsmonitor", str(hook)], cwd=repo, check=True)

            result = repository_context._run_git(repo, "status", "--short")

            self.assertEqual(result.returncode, 0)
            self.assertFalse(marker.exists())

    def test_authenticated_gh_adapter_reads_bounded_pr_json_without_network_library(self):
        adapter = repository_context.GhGitHubAdapter(executable="/usr/bin/gh")
        adapter._run = mock.Mock(side_effect=[
            mock.Mock(returncode=0, stdout="authenticated", stderr=""),
            mock.Mock(
                returncode=0,
                stdout='{"url":"https://github.com/acme/demo/pull/42","number":42,"title":"Fix","state":"OPEN","reviewDecision":"APPROVED","statusCheckRollup":[{"name":"CI","conclusion":"SUCCESS"}]}',
                stderr="",
            ),
        ])

        result = adapter.context(repository=Path("/repo"), branch="jira/DEMO-42")

        self.assertTrue(result["available"])
        self.assertEqual(result["pull_request"]["number"], 42)
        self.assertEqual(result["checks"][0]["conclusion"], "SUCCESS")
        self.assertEqual(adapter._run.call_args_list[0].args, (Path("/repo"), "auth", "status"))

    def test_authenticated_gh_adapter_reports_no_pull_request_without_failing_context(self):
        adapter = repository_context.GhGitHubAdapter(executable="/usr/bin/gh")
        adapter._run = mock.Mock(side_effect=[
            mock.Mock(returncode=0, stdout="authenticated", stderr=""),
            mock.Mock(returncode=1, stdout="", stderr="no pull requests found"),
        ])

        result = adapter.context(repository=Path("/repo"), branch="jira/DEMO-42")

        self.assertTrue(result["available"])
        self.assertEqual(result["reason"], "no_pull_request")
        self.assertIsNone(result["pull_request"])

    def test_gh_adapter_does_not_misreport_network_failure_as_no_pull_request(self):
        adapter = repository_context.GhGitHubAdapter(executable="/usr/bin/gh")
        adapter._run = mock.Mock(side_effect=[
            mock.Mock(returncode=0, stdout="authenticated", stderr=""),
            mock.Mock(returncode=1, stdout="", stderr="HTTP 503 service unavailable"),
        ])

        result = adapter.context(repository=Path("/repo"), branch="jira/DEMO-42")

        self.assertFalse(result["available"])
        self.assertEqual(result["reason"], "gh_query_failed")


if __name__ == "__main__":
    unittest.main()
