"""Read-only repository and pull-request context for Jira issues."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Protocol


MAX_CHANGED_FILES = 100
MAX_RECENT_COMMITS = 20
MAX_CHECKS = 50
MAX_COMMAND_OUTPUT = 128 * 1024
GIT_TIMEOUT_SECONDS = 8
ISSUE_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*-[0-9]+$")


class GitHubAdapter(Protocol):
    def context(self, *, repository: Path, branch: str) -> dict[str, Any]:
        """Return bounded GitHub context for a local branch."""
        ...


def _unavailable(issue_key: str, reason: str, *, repository: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "available": False,
        "reason": reason,
        "issue_key": issue_key,
        "repository": repository,
        "github": {"available": False, "reason": "not_checked", "pull_request": None, "checks": []},
    }


def _normalise_issue_key(issue_key: str) -> str | None:
    value = issue_key.strip().upper()
    return value if ISSUE_KEY_RE.fullmatch(value) else None


def _validated_base_ref(base_ref: str) -> str:
    value = str(base_ref or "HEAD").strip()
    if not value or len(value) > 200 or value.startswith("-") or any(char.isspace() or ord(char) < 32 for char in value):
        raise ValueError("base_ref must be a non-empty Git ref without whitespace or options")
    if value == "HEAD":
        return value
    if not re.fullmatch(r"(?:refs/(?:heads|remotes)/)?[A-Za-z0-9][A-Za-z0-9._/-]*", value):
        raise ValueError("base_ref is not a valid branch or ref name")
    if "\x00" in value or ".." in value or "@{" in value or value.endswith(".") or value.endswith("/"):
        raise ValueError("base_ref is not a valid Git ref")
    if value.endswith(".lock") or "//" in value or any(part.startswith(".") or part.endswith(".") for part in value.split("/")):
        raise ValueError("base_ref is not a valid Git ref")
    return value


def _git_environment() -> dict[str, str]:
    environment = os.environ.copy()
    for key in list(environment):
        if key in {"GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT", "GIT_DIR", "GIT_WORK_TREE", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES"} or key.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")):
            environment.pop(key, None)
    environment.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ASKPASS": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_ATTR_NOSYSTEM": "1",
        }
    )
    return environment


def _run_bounded(
    argv: list[str],
    *,
    cwd: Path,
    timeout: int,
    environment: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    """Capture command output through temporary files so memory stays bounded."""
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=stdout_file,
            stderr=stderr_file,
            env=environment,
        )
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise
        stdout_file.seek(0)
        stderr_file.seek(0)
        stdout = stdout_file.read(MAX_COMMAND_OUTPUT).decode("utf-8", errors="replace")
        stderr = stderr_file.read(MAX_COMMAND_OUTPUT).decode("utf-8", errors="replace")
    return subprocess.CompletedProcess(argv, returncode, stdout, stderr)


def _run_git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return _run_bounded(
            ["git", "-c", f"core.hooksPath={os.devnull}", "-c", "core.fsmonitor=false", *args],
            cwd=cwd,
            timeout=GIT_TIMEOUT_SECONDS,
            environment=_git_environment(),
        )
    except FileNotFoundError as exc:
        raise RuntimeError("git_not_installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("git_timeout") from exc



def _git_success(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    result = _run_git(cwd, *args)
    if result.returncode != 0:
        raise RuntimeError("git_command_failed")
    return result


def _parse_changed_files(output: str) -> tuple[list[dict[str, str]], bool]:
    lines = output.splitlines()
    truncated = len(lines) > MAX_CHANGED_FILES
    changed: list[dict[str, str]] = []
    for line in lines[:MAX_CHANGED_FILES]:
        if len(line) < 3:
            continue
        status = line[:2].strip() or line[:2]
        path = line[3:].strip()
        if path:
            changed.append({"status": status, "path": path})
    return changed, truncated


def _parse_recent_commits(output: str) -> tuple[list[dict[str, str]], bool]:
    lines = output.splitlines()
    commits: list[dict[str, str]] = []
    for line in lines[:MAX_RECENT_COMMITS]:
        sha, separator, rest = line.partition("\t")
        if not separator:
            continue
        author, separator, tail = rest.partition("\t")
        if not separator:
            continue
        subject, separator, authored_at = tail.partition("\t")
        if not separator:
            authored_at = ""
        commits.append({"sha": sha, "subject": subject, "author": author, "authored_at": authored_at})
    return commits, len(lines) > MAX_RECENT_COMMITS


def _local_context(worktree: Path, *, repository: Path, branch: str, base_ref: str) -> dict[str, Any]:
    try:
        worktree_root = Path(_git_success(worktree, "rev-parse", "--show-toplevel").stdout.strip()).resolve()
        worktree_common_raw = _git_success(worktree, "rev-parse", "--git-common-dir").stdout.strip()
        repository_common_raw = _git_success(repository, "rev-parse", "--git-common-dir").stdout.strip()
        worktree_common = Path(worktree_common_raw)
        repository_common = Path(repository_common_raw)
        if not worktree_common.is_absolute():
            worktree_common = worktree / worktree_common
        if not repository_common.is_absolute():
            repository_common = repository / repository_common
        if worktree_root != worktree.resolve() or worktree_common.resolve() != repository_common.resolve():
            raise RuntimeError("worktree_repository_mismatch")
    except RuntimeError as exc:
        if str(exc) == "worktree_repository_mismatch":
            raise
        reason = str(exc) if str(exc) in {"git_not_installed", "git_timeout"} else "git_unavailable"
        raise RuntimeError(reason) from exc
    try:
        resolved_base = _git_success(
            worktree,
            "rev-parse",
            "--verify",
            "--end-of-options",
            f"{base_ref}^{{commit}}",
        ).stdout.strip()
    except RuntimeError as exc:
        reason = str(exc) if str(exc) in {"git_not_installed", "git_timeout"} else "base_ref_unavailable"
        raise RuntimeError(reason) from exc
    try:
        status_output = _git_success(worktree, "status", "--short", "--untracked-files=all", "--ignore-submodules=all").stdout
        branch_output = _git_success(worktree, "branch", "--show-current").stdout.strip()
        commits_output = _git_success(
            worktree,
            "log",
            f"-n{MAX_RECENT_COMMITS + 1}",
            "--format=%H%x09%an%x09%s%x09%aI",
        ).stdout
        ahead_behind = _git_success(
            worktree,
            "rev-list",
            "--left-right",
            "--count",
            f"{base_ref}...HEAD",
        ).stdout.strip().split()
    except RuntimeError as exc:
        reason = str(exc) if str(exc) in {"git_not_installed", "git_timeout"} else "git_unavailable"
        raise RuntimeError(reason) from exc

    if branch_output != branch:
        raise RuntimeError("worktree_branch_mismatch")
    if len(ahead_behind) != 2 or not all(value.isdigit() for value in ahead_behind):
        raise RuntimeError("git_unavailable")
    changed_files, changed_truncated = _parse_changed_files(status_output)
    recent_commits, recent_commits_truncated = _parse_recent_commits(commits_output)
    return {
        "repo_path": str(repository),
        "worktree_path": str(worktree),
        "branch": branch_output,
        "base_ref": base_ref,
        "base_commit": resolved_base,
        "clean": not bool(status_output.strip()),
        "changed_files": changed_files,
        "changed_files_truncated": changed_truncated,
        "recent_commits": recent_commits,
        "recent_commits_truncated": recent_commits_truncated,
        "ahead": int(ahead_behind[1]),
        "behind": int(ahead_behind[0]),
    }


def _bounded_check(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    name = str(value.get("name") or value.get("context") or "").strip()[:200]
    state = str(value.get("state") or value.get("conclusion") or value.get("status") or "").strip()[:80]
    if not name:
        return None
    return {"name": name, "state": state}


def _normalise_github(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"available": False, "reason": "adapter_invalid", "pull_request": None, "checks": []}
    checks = value.get("checks") if isinstance(value.get("checks"), list) else []
    bounded_checks = [check for raw in checks[:MAX_CHECKS] if (check := _bounded_check(raw)) is not None]
    pull_request = value.get("pull_request")
    if isinstance(pull_request, dict):
        pull_request = {
            "url": str(pull_request.get("url") or "")[:2_000],
            "number": int(pull_request["number"]) if str(pull_request.get("number") or "").isdigit() else None,
            "title": str(pull_request.get("title") or "")[:500],
            "state": str(pull_request.get("state") or "")[:80],
            "review_decision": str(pull_request.get("review_decision") or "")[:80],
        }
    else:
        pull_request = None
    return {
        "available": bool(value.get("available")),
        "reason": str(value.get("reason") or ("ok" if pull_request else "no_pull_request"))[:100],
        "pull_request": pull_request,
        "checks": bounded_checks,
        "checks_truncated": len(checks) > MAX_CHECKS,
    }


def unavailable_context(issue_key: str, reason: str = "repository_context_unavailable") -> dict[str, Any]:
    """Build a stable unavailable payload for renderer-facing callers."""
    normalized = _normalise_issue_key(issue_key) or str(issue_key).strip()
    return _unavailable(normalized, reason)


class GhGitHubAdapter:
    """Read-only GitHub adapter using an already-installed authenticated gh CLI."""

    def __init__(self, *, executable: str | None = None, timeout: int = GIT_TIMEOUT_SECONDS):
        self.executable = executable or shutil.which("gh")
        self.timeout = timeout

    def _run(self, cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
        if not self.executable:
            raise RuntimeError("gh_not_installed")
        environment = os.environ.copy()
        environment.update({"GH_PROMPT_DISABLED": "1", "GIT_TERMINAL_PROMPT": "0"})
        try:
            return _run_bounded(
                [self.executable, *args],
                cwd=cwd,
                timeout=self.timeout,
                environment=environment,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("gh_timeout") from exc

    def context(self, *, repository: Path, branch: str) -> dict[str, Any]:
        if not self.executable:
            return {"available": False, "reason": "gh_not_installed", "pull_request": None, "checks": []}
        auth = self._run(repository, "auth", "status")
        if auth.returncode != 0:
            return {"available": False, "reason": "gh_not_authenticated", "pull_request": None, "checks": []}
        result = self._run(
            repository,
            "pr",
            "view",
            branch,
            "--json",
            "url,number,title,state,reviewDecision,statusCheckRollup",
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").casefold()
            if any(marker in detail for marker in ("no pull request", "no pull requests", "could not find pull request")):
                return {"available": True, "reason": "no_pull_request", "pull_request": None, "checks": []}
            return {"available": False, "reason": "gh_query_failed", "pull_request": None, "checks": []}
        try:
            payload = json.loads((result.stdout or "")[:MAX_COMMAND_OUTPUT])
        except json.JSONDecodeError:
            return {"available": False, "reason": "gh_invalid_response", "pull_request": None, "checks": []}
        if not isinstance(payload, dict):
            return {"available": False, "reason": "gh_invalid_response", "pull_request": None, "checks": []}
        raw_checks = payload.get("statusCheckRollup")
        if not isinstance(raw_checks, list):
            raw_checks = []
        return {
            "available": True,
            "reason": "ok",
            "pull_request": {
                "url": payload.get("url"),
                "number": payload.get("number"),
                "title": payload.get("title"),
                "state": payload.get("state"),
                "review_decision": payload.get("reviewDecision"),
            },
            "checks": raw_checks if isinstance(raw_checks, list) else [],
        }


def get_repository_context(
    store: Any,
    issue_key: str,
    *,
    base_ref: str = "HEAD",
    github: GitHubAdapter | None = None,
) -> dict[str, Any]:
    """Resolve the mapped Jira worktree and return read-only local/PR state."""
    normalized = _normalise_issue_key(issue_key)
    if normalized is None:
        return _unavailable(str(issue_key).strip(), "issue_key_invalid")
    project_key = normalized.rsplit("-", 1)[0]
    try:
        requested_base = _validated_base_ref(base_ref)
    except ValueError:
        return _unavailable(normalized, "base_ref_invalid")
    try:
        mapping = store.get_project_mapping(project_key)
    except Exception:
        return _unavailable(normalized, "repository_context_unavailable")
    if not mapping:
        return _unavailable(normalized, "project_mapping_missing")
    repo_path = Path(str(mapping.get("repo_path") or "")).expanduser()
    if not repo_path.is_dir():
        return _unavailable(normalized, "repository_missing")
    repo_path = repo_path.resolve()
    expected_branch = f"jira/{normalized}"
    worktree = repo_path / ".worktrees" / expected_branch.replace("/", "-")
    if not worktree.is_dir() or not (worktree / ".git").exists():
        return _unavailable(normalized, "worktree_missing")
    expected_absolute = Path(os.path.abspath(worktree))
    if worktree.is_symlink() or (worktree / ".git").is_symlink() or worktree.resolve() != expected_absolute:
        return _unavailable(normalized, "worktree_unsafe")
    worktree = worktree.resolve()
    try:
        repository = _local_context(worktree, repository=repo_path, branch=expected_branch, base_ref=requested_base)
    except RuntimeError as exc:
        return _unavailable(normalized, str(exc) if str(exc) in {"worktree_branch_mismatch", "worktree_repository_mismatch", "base_ref_unavailable", "git_timeout", "git_not_installed"} else "git_unavailable")
    adapter = github or GhGitHubAdapter()
    try:
        github_context = _normalise_github(adapter.context(repository=repo_path, branch=expected_branch))
    except Exception:
        github_context = {"available": False, "reason": "github_unavailable", "pull_request": None, "checks": []}
    return {
        "status": "available",
        "available": True,
        "reason": None,
        "issue_key": normalized,
        "repository": repository,
        "github": github_context,
    }
