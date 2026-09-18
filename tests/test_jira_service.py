from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "dashboard" / "jira_service.py"
spec = importlib.util.spec_from_file_location("jira_browser_service", MODULE_PATH)
assert spec is not None and spec.loader is not None
jira_service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(jira_service)


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


class JiraConfigTests(unittest.TestCase):
    def test_loads_example_project_compatible_config_without_exposing_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "baseUrl": "https://jira.example.invalid/",
                        "email": "dev@example.com",
                        "token": "very-secret",
                    }
                ),
                encoding="utf-8",
            )
            config_path.chmod(0o600)

            config = jira_service.load_jira_config(config_path)
            status = jira_service.config_status(config_path)

            self.assertEqual(config.base_url, "https://jira.example.invalid")
            self.assertEqual(config.api_token, "very-secret")
            self.assertTrue(status["configured"])
            self.assertEqual(status["base_url"], "https://jira.example.invalid")
            self.assertEqual(status["email"], "dev@example.com")
            self.assertNotIn("apiToken", status)
            self.assertNotIn("very-secret", json.dumps(status))

    def test_environment_token_matches_example_project_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(
                json.dumps({"baseUrl": "https://example.atlassian.net", "email": "dev@example.com"}),
                encoding="utf-8",
            )
            path.chmod(0o600)
            with mock.patch.dict(os.environ, {"JIRA_API_TOKEN": "from-env"}, clear=False):
                config = jira_service.load_jira_config(path)
            self.assertEqual(config.api_token, "from-env")

    def test_environment_only_config_normalises_example_project_site(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing_path = Path(tmp) / "missing.json"
            with mock.patch.dict(
                os.environ,
                {
                    "JIRA_SITE": "jira.example.invalid",
                    "JIRA_EMAIL": "dev@example.com",
                    "JIRA_API_TOKEN": "from-env",
                },
                clear=False,
            ):
                config = jira_service.load_jira_config(missing_path)
            self.assertEqual(config.base_url, "https://jira.example.invalid")

    def test_complete_environment_config_does_not_read_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            config_path.write_text("not-json", encoding="utf-8")
            config_path.chmod(0o600)
            with mock.patch.dict(
                os.environ,
                {
                    "JIRA_BASE_URL": "https://jira.example.invalid",
                    "JIRA_EMAIL": "dev@example.com",
                    "JIRA_API_TOKEN": "from-env",
                },
                clear=False,
            ):
                config = jira_service.load_jira_config(config_path)
            self.assertEqual(config.api_token, "from-env")

    def test_rejects_symlinked_credential_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target.json"
            target.write_text(
                json.dumps({"baseUrl": "https://jira.example.invalid", "email": "dev@example.com", "token": "secret"}),
                encoding="utf-8",
            )
            target.chmod(0o600)
            config_path = Path(tmp) / "config.json"
            config_path.symlink_to(target)
            with mock.patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(ValueError, "symlink"):
                    jira_service.load_jira_config(config_path)

    def test_rejects_symlinked_credential_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            actual_parent = root / "actual"
            actual_parent.mkdir()
            config_path = actual_parent / "config.json"
            config_path.write_text(
                json.dumps({"baseUrl": "https://jira.example.invalid", "email": "dev@example.com", "token": "secret"}),
                encoding="utf-8",
            )
            config_path.chmod(0o600)
            linked_parent = root / "linked"
            linked_parent.symlink_to(actual_parent, target_is_directory=True)

            with mock.patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(ValueError, "symlink"):
                    jira_service.load_jira_config(linked_parent / "config.json")

    def test_file_credentials_fail_closed_without_safe_open_primitives(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            config_path.write_text(
                json.dumps({"baseUrl": "https://jira.example.invalid", "email": "dev@example.com", "token": "secret"}),
                encoding="utf-8",
            )
            config_path.chmod(0o600)
            with mock.patch.dict(os.environ, {}, clear=True):
                with mock.patch.object(jira_service.os, "O_NOFOLLOW", None, create=True):
                    with self.assertRaisesRegex(ValueError, "JIRA_BASE_URL"):
                        jira_service.load_jira_config(config_path)

    def test_rejects_non_regular_credential_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            config_path.mkdir(mode=0o700)
            with mock.patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(ValueError, "regular"):
                    jira_service.load_jira_config(config_path)

    def test_rejects_oversized_credential_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            config_path.write_bytes(b"{" + b"x" * jira_service.MAX_JIRA_CONFIG_BYTES)
            config_path.chmod(0o600)
            with mock.patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(ValueError, "large"):
                    jira_service.load_jira_config(config_path)

    def test_accepts_only_clean_https_origins(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            valid = {"baseUrl": "https://jira.example.invalid///", "email": "dev@example.com", "token": "secret"}
            path.write_text(json.dumps(valid), encoding="utf-8")
            path.chmod(0o600)
            self.assertEqual(jira_service.load_jira_config(path).base_url, "https://jira.example.invalid")

            for base_url in (
                "https://jira.example.invalid/path",
                "https://user:pass@jira.example.invalid",
                "https://jira.example.invalid?token=leak",
                "https://jira.example.invalid#fragment",
                "https:///missing-host",
            ):
                path.write_text(json.dumps({**valid, "baseUrl": base_url}), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "origin"):
                    jira_service.load_jira_config(path)

    def test_rejects_plaintext_jira_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(
                json.dumps({"baseUrl": "http://jira.example.com", "email": "dev@example.com", "token": "secret"}),
                encoding="utf-8",
            )
            path.chmod(0o600)
            with self.assertRaisesRegex(ValueError, "https"):
                jira_service.load_jira_config(path)

    def test_rejects_group_or_other_readable_credential_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(
                json.dumps({"baseUrl": "https://jira.example.invalid", "email": "dev@example.com", "token": "secret"}),
                encoding="utf-8",
            )
            path.chmod(0o644)

            with self.assertRaisesRegex(ValueError, "permissions"):
                jira_service.load_jira_config(path)

    def test_rejects_incomplete_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps({"baseUrl": "https://example.invalid"}), encoding="utf-8")
            path.chmod(0o600)
            with self.assertRaisesRegex(ValueError, "email"):
                jira_service.load_jira_config(path)


class JiraNormalisationTests(unittest.TestCase):
    def test_adf_to_text_keeps_paragraphs_lists_and_links(self):
        value = {
            "type": "doc",
            "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "First"}]},
                {
                    "type": "bulletList",
                    "content": [
                        {
                            "type": "listItem",
                            "content": [
                                {"type": "paragraph", "content": [{"type": "text", "text": "Second"}]}
                            ],
                        }
                    ],
                },
            ],
        }

        self.assertEqual(jira_service.adf_to_text(value), "First\n\n• Second")

    def test_adf_traversal_stops_at_depth_node_and_output_limits(self):
        value = {"type": "doc", "content": [{"type": "text", "text": "x"}] * 20}
        for _ in range(10):
            value = {"type": "paragraph", "content": [value]}

        with mock.patch.multiple(
            jira_service,
            MAX_ADF_DEPTH=4,
            MAX_ADF_NODES=5,
            MAX_ADF_OUTPUT_CHARS=12,
        ):
            result = jira_service.adf_to_text(value)

        self.assertLessEqual(len(result), 12)

    def test_attachment_reference_traversal_stops_at_depth_and_node_limits(self):
        value = {
            "type": "inlineCard",
            "attrs": {"url": "https://jira.example.invalid/rest/api/3/attachment/content/7001"},
        }
        for _ in range(10):
            value = {"type": "paragraph", "content": [value]}

        with mock.patch.multiple(jira_service, MAX_ADF_DEPTH=4, MAX_ADF_NODES=5):
            attachment_ids, filenames = jira_service._adf_attachment_references(value)

        self.assertEqual(attachment_ids, set())
        self.assertEqual(filenames, set())

    def test_malformed_attachment_size_does_not_abort_issue_normalisation(self):
        payload = {
            "id": "10001",
            "key": "DEMO-42",
            "fields": {
                "summary": "Malformed attachment",
                "attachment": [{"id": "7001", "filename": "bad.bin", "size": "not-a-size"}],
            },
        }

        result = jira_service._normalise_issue(payload, detail=True)

        self.assertEqual(result["attachments"][0]["size"], 0)

    def test_normalises_search_issues(self):
        payload = {
            "issues": [
                {
                    "id": "10001",
                    "key": "DEMO-42",
                    "fields": {
                        "summary": "Repair sync",
                        "status": {"name": "In Progress", "statusCategory": {"key": "indeterminate"}},
                        "priority": {"name": "High"},
                        "issuetype": {"name": "Bug"},
                        "assignee": {"displayName": "Alex"},
                        "project": {"key": "DEMO", "name": "Example Project"},
                        "updated": "2026-09-18T08:00:00.000+1200",
                    },
                }
            ],
            "nextPageToken": "next-token",
        }

        result = jira_service.normalise_search(payload)

        self.assertEqual(result["issues"][0]["key"], "DEMO-42")
        self.assertEqual(result["issues"][0]["project_key"], "DEMO")
        self.assertEqual(result["issues"][0]["status_category"], "indeterminate")
        self.assertEqual(result["next_page_token"], "next-token")

    def test_detail_normalises_attachments_and_associates_comment_media_by_filename(self):
        payload = {
            "id": "10001",
            "key": "DEMO-42",
            "fields": {
                "summary": "Review stakeholder screenshot",
                "attachment": [
                    {
                        "id": "7001",
                        "filename": "expected-dashboard.png",
                        "mimeType": "image/png",
                        "size": 27_030,
                        "created": "2026-09-18T08:00:00.000+1200",
                        "author": {"displayName": "Stakeholder"},
                        "content": "https://jira.example.invalid/secure/attachment/7001/file.png",
                        "thumbnail": "https://jira.example.invalid/secure/thumbnail/7001/file.png",
                    }
                ],
                "comment": {
                    "comments": [
                        {
                            "id": "9001",
                            "author": {"displayName": "Stakeholder"},
                            "body": {
                                "type": "doc",
                                "content": [
                                    {
                                        "type": "mediaSingle",
                                        "content": [
                                            {
                                                "type": "media",
                                                "attrs": {
                                                    "id": "media-uuid",
                                                    "type": "file",
                                                    "alt": "expected-dashboard.png",
                                                },
                                            }
                                        ],
                                    }
                                ],
                            },
                        }
                    ]
                },
            },
        }

        result = jira_service._normalise_issue(payload, detail=True)

        attachment = result["attachments"][0]
        self.assertEqual(attachment["id"], "7001")
        self.assertEqual(attachment["filename"], "expected-dashboard.png")
        self.assertEqual(attachment["mime_type"], "image/png")
        self.assertTrue(attachment["is_image"])
        self.assertNotIn("content", attachment)
        self.assertNotIn("thumbnail", attachment)
        self.assertEqual(result["comments"][0]["attachments"], [attachment])

    def test_comment_media_does_not_guess_from_media_service_id_or_duplicate_filename(self):
        attachments = [
            {"id": "7001", "filename": "screenshot.png", "mime_type": "image/png"},
            {"id": "7002", "filename": "screenshot.png", "mime_type": "image/png"},
        ]
        comment = {
            "id": "9001",
            "body": {
                "type": "doc",
                "content": [
                    {
                        "type": "media",
                        "attrs": {"id": "7001", "type": "file"},
                    },
                    {
                        "type": "media",
                        "attrs": {"id": "media-services-uuid", "type": "file", "alt": "screenshot.png"},
                    },
                ],
            },
        }

        result = jira_service._normalise_comment(comment, attachments)

        self.assertEqual(result["attachments"], [])

    def test_comment_attachment_link_uses_explicit_jira_attachment_id(self):
        attachments = [
            {"id": "7001", "filename": "one.png", "mime_type": "image/png"},
            {"id": "7002", "filename": "two.png", "mime_type": "image/png"},
        ]
        comment = {
            "id": "9001",
            "body": {
                "type": "doc",
                "content": [
                    {
                        "type": "inlineCard",
                        "attrs": {
                            "url": "https://jira.example.invalid/rest/api/3/attachment/content/7002",
                        },
                    }
                ],
            },
        }

        result = jira_service._normalise_comment(comment, attachments)

        self.assertEqual([value["id"] for value in result["attachments"]], ["7002"])

    def test_long_media_alt_does_not_suppress_later_attachment_id(self):
        body = {
            "type": "doc",
            "content": [
                {"type": "media", "attrs": {"alt": "x" * (jira_service.MAX_ADF_OUTPUT_CHARS + 1)}},
                {
                    "type": "inlineCard",
                    "attrs": {"url": "https://jira.example.invalid/rest/api/3/attachment/content/7002"},
                },
            ],
        }

        attachment_ids, _ = jira_service._adf_attachment_references(body)

        self.assertEqual(attachment_ids, {"7002"})

    def test_attachment_references_have_a_count_limit(self):
        body = {
            "type": "doc",
            "content": [
                {
                    "type": "inlineCard",
                    "attrs": {"url": f"https://jira.example.invalid/rest/api/3/attachment/content/{index}"},
                }
                for index in range(1_001)
            ],
        }

        attachment_ids, _ = jira_service._adf_attachment_references(body)

        self.assertEqual(len(attachment_ids), 1_000)


class WorktreeTests(unittest.TestCase):
    def test_branch_is_stable_when_summary_changes(self):
        self.assertEqual(jira_service.branch_name("DEMO-42", "Fix: Milk / sync!"), "jira/DEMO-42")
        self.assertEqual(jira_service.branch_name("DEMO-42", "Completely different"), "jira/DEMO-42")

    def test_creates_idempotent_worktree_from_project_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            git(root, "init", "-q", "-b", "main")
            git(root, "config", "user.name", "Test User")
            git(root, "config", "user.email", "test@example.com")
            (root / "README.md").write_text("seed\n", encoding="utf-8")
            git(root, "add", "README.md")
            git(root, "commit", "-qm", "seed")

            first = jira_service.create_worktree(
                repo_path=root,
                issue_key="DEMO-42",
                summary="Fix milk sync",
            )
            second = jira_service.create_worktree(
                repo_path=root,
                issue_key="DEMO-42",
                summary="Fix milk sync",
            )

            self.assertTrue(first["created"])
            self.assertFalse(second["created"])
            self.assertEqual(first["path"], second["path"])
            worktree = Path(first["path"])
            self.assertTrue(worktree.is_dir())
            self.assertEqual(git(worktree, "branch", "--show-current"), "jira/DEMO-42")
            self.assertEqual(worktree.parent, root / ".worktrees")
            self.assertIn("locked", git(root, "worktree", "list", "--porcelain"))

    def test_existing_branch_attached_to_new_worktree_is_not_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            git(root, "init", "-q", "-b", "main")
            git(root, "config", "user.name", "Test User")
            git(root, "config", "user.email", "test@example.com")
            (root / "README.md").write_text("seed\\n", encoding="utf-8")
            git(root, "add", "README.md")
            git(root, "commit", "-qm", "seed")
            git(root, "branch", "jira/DEMO-77")

            result = jira_service.create_worktree(
                repo_path=root,
                issue_key="DEMO-77",
                summary="Existing branch",
            )

            self.assertFalse(result["created"])
            self.assertTrue((root / ".worktrees" / "jira-DEMO-77").is_dir())
            self.assertEqual(git(root / ".worktrees" / "jira-DEMO-77", "branch", "--show-current"), "jira/DEMO-77")

    def test_cleanup_does_not_delete_a_reused_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            git(root, "init", "-q", "-b", "main")
            git(root, "config", "user.name", "Test User")
            git(root, "config", "user.email", "test@example.com")
            (root / "README.md").write_text("seed\n", encoding="utf-8")
            git(root, "add", "README.md")
            git(root, "commit", "-qm", "seed")
            git(root, "branch", "jira/DEMO-77")

            jira_service.create_worktree(repo_path=root, issue_key="DEMO-77", summary="Existing branch")
            result = jira_service.cleanup_worktree(repo_path=root, issue_key="DEMO-77")

            self.assertTrue(result["removed"])
            self.assertEqual(git(root, "rev-parse", "--verify", "refs/heads/jira/DEMO-77"), git(root, "rev-parse", "main"))

    def test_rejects_option_like_base_ref_and_disables_checkout_hooks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            git(root, "init", "-q", "-b", "main")
            git(root, "config", "user.name", "Test User")
            git(root, "config", "user.email", "test@example.com")
            (root / "README.md").write_text("seed\n", encoding="utf-8")
            git(root, "add", "README.md")
            git(root, "commit", "-qm", "seed")
            marker = Path(tmp) / "hook-ran"
            hook = root / ".git" / "hooks" / "post-checkout"
            hook.write_text(f"#!/bin/sh\ntouch '{marker}'\n", encoding="utf-8")
            hook.chmod(0o755)

            with self.assertRaisesRegex(ValueError, "not an option"):
                jira_service.create_worktree(
                    repo_path=root,
                    issue_key="DEMO-99",
                    summary="Unsafe ref",
                    base_ref="--orphan",
                )
            jira_service.create_worktree(repo_path=root, issue_key="DEMO-99", summary="Safe")
            self.assertFalse(marker.exists())

    def test_create_rejects_symlinked_worktrees_directory_before_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            outside = Path(tmp) / "outside"
            root.mkdir()
            outside.mkdir()
            git(root, "init", "-q", "-b", "main")
            git(root, "config", "user.name", "Test User")
            git(root, "config", "user.email", "test@example.com")
            (root / "README.md").write_text("seed\n", encoding="utf-8")
            git(root, "add", "README.md")
            git(root, "commit", "-qm", "seed")
            (root / ".worktrees").symlink_to(outside, target_is_directory=True)

            with self.assertRaisesRegex(ValueError, "symlink"):
                jira_service.create_worktree(repo_path=root, issue_key="DEMO-88", summary="Unsafe")
            self.assertFalse((outside / "jira-DEMO-88").exists())

    def test_cleanup_rejects_symlinked_worktrees_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            outside = Path(tmp) / "outside"
            root.mkdir()
            outside.mkdir()
            git(root, "init", "-q", "-b", "main")
            git(root, "config", "user.name", "Test User")
            git(root, "config", "user.email", "test@example.com")
            (root / "README.md").write_text("seed\n", encoding="utf-8")
            git(root, "add", "README.md")
            git(root, "commit", "-qm", "seed")
            (root / ".worktrees").symlink_to(outside, target_is_directory=True)

            with self.assertRaisesRegex(ValueError, "symlink"):
                jira_service.cleanup_worktree(repo_path=root, issue_key="DEMO-88")

    def test_create_rejects_symlinked_worktree_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            outside = Path(tmp) / "outside"
            root.mkdir()
            outside.mkdir()
            git(root, "init", "-q", "-b", "main")
            git(root, "config", "user.name", "Test User")
            git(root, "config", "user.email", "test@example.com")
            (root / "README.md").write_text("seed\n", encoding="utf-8")
            git(root, "add", "README.md")
            git(root, "commit", "-qm", "seed")
            (root / ".worktrees").mkdir()
            (root / ".worktrees" / "jira-DEMO-89").symlink_to(outside, target_is_directory=True)

            with self.assertRaisesRegex(ValueError, "symlink"):
                jira_service.create_worktree(repo_path=root, issue_key="DEMO-89", summary="Unsafe")


class StoreTests(unittest.TestCase):
    def test_store_hardens_existing_directory_and_database_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "jira-browser"
            directory.mkdir(mode=0o755)
            database = directory / "state.sqlite3"
            database.touch(mode=0o644)

            jira_service.JiraStore(database)

            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(database.stat().st_mode), 0o600)

    def test_store_rejects_symlinked_or_non_regular_storage_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            actual_parent = root / "actual"
            actual_parent.mkdir()
            linked_parent = root / "linked-parent"
            linked_parent.symlink_to(actual_parent, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symlink"):
                jira_service.JiraStore(linked_parent / "state.sqlite3")

            actual_database = actual_parent / "actual.sqlite3"
            actual_database.touch(mode=0o600)
            linked_database = actual_parent / "linked.sqlite3"
            linked_database.symlink_to(actual_database)
            with self.assertRaisesRegex(ValueError, "symlink"):
                jira_service.JiraStore(linked_database)

            database_directory = actual_parent / "database-directory"
            database_directory.mkdir(mode=0o700)
            with self.assertRaisesRegex(ValueError, "regular"):
                jira_service.JiraStore(database_directory)

    def test_store_connects_through_nofollow_uri_and_rejects_post_preopen_swap(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            database = root / "state.sqlite3"
            store = jira_service.JiraStore(database)
            captured = {}
            real_connect = jira_service.sqlite3.connect

            def capture_connect(*args, **kwargs):
                captured["database"] = args[0]
                captured["kwargs"] = kwargs
                return real_connect(*args, **kwargs)

            with mock.patch.object(jira_service.sqlite3, "connect", side_effect=capture_connect):
                with store._connect() as connection:
                    connection.execute("SELECT 1")

            self.assertTrue(captured["kwargs"]["uri"])
            self.assertIn("nofollow=1", captured["database"])

            replacement = root / "replacement.sqlite3"
            replacement.touch(mode=0o600)
            original_connect = jira_service.sqlite3.connect

            def swap_then_connect(*args, **kwargs):
                database.unlink()
                database.symlink_to(replacement)
                return original_connect(*args, **kwargs)

            with mock.patch.object(jira_service.sqlite3, "connect", side_effect=swap_then_connect):
                with self.assertRaises(ValueError):
                    store._connect()
    def test_mutation_receipt_replays_completed_result_and_rejects_conflicts(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = jira_service.JiraStore(Path(tmp) / "jira.sqlite3")
            first = store.reserve_mutation(
                idempotency_key="comment-key-123456",
                action="comment",
                issue_key="DEMO-42",
                payload={"body": "Done"},
            )
            self.assertEqual(first["status"], "claimed")
            result = {"comment": {"id": "9001", "body": "Done"}}
            store.complete_mutation("comment-key-123456", result)
            receipt = store.get_mutation_receipt("comment-key-123456")
            self.assertEqual(receipt["claimed"], 1)
            self.assertEqual(receipt["completed"], 1)
            self.assertEqual(len(receipt["payload_sha256"]), 64)
            self.assertLessEqual(len(receipt["result_json"].encode("utf-8")), jira_service.MAX_MUTATION_RESULT_BYTES)

            replay = store.reserve_mutation(
                idempotency_key="comment-key-123456",
                action="comment",
                issue_key="DEMO-42",
                payload={"body": "Done"},
            )
            self.assertEqual(replay, {"status": "completed", "result": result})
            with self.assertRaisesRegex(ValueError, "different mutation"):
                store.reserve_mutation(
                    idempotency_key="comment-key-123456",
                    action="transition",
                    issue_key="DEMO-42",
                    payload={"transition_id": "31"},
                )

    def test_pending_mutation_is_not_repeated_and_pre_jira_failure_can_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = jira_service.JiraStore(Path(tmp) / "jira.sqlite3")
            kwargs = {
                "idempotency_key": "transition-key-123456",
                "action": "transition",
                "issue_key": "DEMO-42",
                "payload": {"transition_id": "31"},
            }
            self.assertEqual(store.reserve_mutation(**kwargs)["status"], "claimed")
            with self.assertRaises(jira_service.MutationPendingError):
                store.reserve_mutation(**kwargs)
            store.release_mutation(kwargs["idempotency_key"])
            self.assertEqual(store.reserve_mutation(**kwargs)["status"], "claimed")

    def test_maps_project_and_links_chat_and_worktree(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = jira_service.JiraStore(Path(tmp) / "jira.sqlite3")
            store.set_project_mapping(
                jira_project_key="DEMO",
                hermes_project_id="p_123",
                hermes_project_label="Example Project",
                repo_path="/repo/example_project",
            )
            store.link_session(
                issue_id="10001",
                issue_key="DEMO-42",
                session_id="session-1",
                project_id="p_123",
                worktree_path="/repo/example_project/.worktrees/jira-DEMO-42",
                branch="jira/DEMO-42-fix",
            )

            self.assertEqual(store.get_project_mapping("DEMO")["hermes_project_id"], "p_123")
            links = store.links_for_issue("10001")
            self.assertEqual(len(links), 1)
            self.assertEqual(links[0]["session_id"], "session-1")
            self.assertEqual(links[0]["branch"], "jira/DEMO-42-fix")

            self.assertTrue(store.unlink_session(issue_id="10001", session_id="session-1"))
            self.assertEqual(store.links_for_issue("10001"), [])
            self.assertFalse(store.unlink_session(issue_id="10001", session_id="session-1"))

    def test_session_links_include_title_and_keep_archived_chats(self):
        class FakeSessionDB:
            def __init__(self, read_only=False):
                self.read_only = read_only

            def get_session(self, session_id):
                return {"id": session_id, "title": "Fix milk totals", "archived": 1}

            def close(self):
                pass

        module = types.ModuleType("hermes_state")
        module.SessionDB = FakeSessionDB
        links = [{"session_id": "session-1", "branch": "jira/DEMO-42"}]
        with mock.patch.dict(sys.modules, {"hermes_state": module}):
            result = jira_service.enrich_session_links(links)

        self.assertEqual(result[0]["chat_title"], "Fix milk totals")
        self.assertTrue(result[0]["archived"])
        self.assertTrue(result[0]["available"])

    def test_project_session_scan_pages_and_keeps_archived_worktree_chats(self):
        calls = []
        rows = [
            {"id": "active", "title": "Active", "cwd": "/repo/.worktrees/a", "archived": 0},
            {"id": "elsewhere", "title": "Other", "cwd": "/other/repo", "archived": 0},
            {"id": "archived", "title": "Archived", "cwd": "/repo/.worktrees/a", "archived": 1},
            {"id": "worker", "title": "Worker", "cwd": "/repo/.worktrees/a", "source": "kanban"},
        ]

        class FakeSessionDB:
            def __init__(self, read_only=False):
                self.read_only = read_only

            def list_sessions_rich(self, **kwargs):
                calls.append(kwargs)
                offset = kwargs["offset"]
                limit = kwargs["limit"]
                return rows[offset : offset + limit]

            def close(self):
                pass

        module = types.ModuleType("hermes_state")
        module.SessionDB = FakeSessionDB
        with mock.patch.dict(sys.modules, {"hermes_state": module}):
            result = jira_service.list_linkable_sessions("/repo", batch_size=2)

        self.assertEqual([session["id"] for session in result], ["active", "archived"])
        self.assertEqual([call["offset"] for call in calls], [0, 2, 4])
        self.assertTrue(all(call["include_archived"] for call in calls))


class SettingsTests(unittest.TestCase):
    def test_missing_settings_use_assigned_to_me_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "missing.json"
            settings = jira_service.load_settings(path)
            self.assertTrue(path.exists())

        self.assertEqual(settings["defaultView"], "assigned")
        self.assertIn("assignee = currentUser()", settings["views"][0]["jql"])

    def test_settings_round_trip_as_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            value = {
                "version": 1,
                "defaultView": "mine",
                "pageSize": 25,
                "baseRef": "main",
                "groupByStatus": False,
                "views": [
                    {
                        "id": "mine",
                        "label": "My current tickets",
                        "jql": "assignee = currentUser() AND statusCategory != Done ORDER BY updated DESC",
                    }
                ],
            }
            saved = jira_service.save_settings(value, path)

            self.assertEqual(saved, value)
            self.assertEqual(jira_service.load_settings(path), value)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), value)

    def test_settings_reject_invalid_default_view(self):
        value = {
            "version": 1,
            "defaultView": "missing",
            "pageSize": 50,
            "baseRef": "HEAD",
            "views": [{"id": "mine", "label": "Mine", "jql": "assignee = currentUser()"}],
        }
        with self.assertRaisesRegex(ValueError, "defaultView"):
            jira_service.validate_settings(value)

    def test_loading_settings_persists_new_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(json.dumps({
                "version": 1,
                "defaultView": "mine",
                "pageSize": 50,
                "baseRef": "HEAD",
                "views": [{"id": "mine", "label": "Mine", "jql": "assignee = currentUser()"}],
            }), encoding="utf-8")

            settings = jira_service.load_settings(path)

            self.assertTrue(settings["groupByStatus"])
            self.assertTrue(json.loads(path.read_text(encoding="utf-8"))["groupByStatus"])


class JiraClientTests(unittest.TestCase):
    def test_json_responses_are_bounded_before_decoding(self):
        client = jira_service.JiraClient(jira_service.JiraConfig(
            base_url="https://jira.example.invalid",
            email="dev@example.com",
            api_token="very-secret",
        ))
        captured = {}

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            @staticmethod
            def read(limit):
                captured["limit"] = limit
                return b"x" * limit

        opener = mock.Mock()
        opener.open.return_value = Response()
        with mock.patch.object(jira_service.urllib.request, "build_opener", return_value=opener):
            with self.assertRaisesRegex(RuntimeError, "too large"):
                client._request("/rest/api/3/issue/DEMO-42")

        self.assertEqual(captured["limit"], jira_service.MAX_JIRA_JSON_BYTES + 1)

    def test_issue_pages_all_comments_instead_of_using_embedded_comment_window(self):
        client = jira_service.JiraClient(jira_service.JiraConfig(
            base_url="https://jira.example.invalid",
            email="dev@example.com",
            api_token="very-secret",
        ))
        calls = []

        def fake_request(path, params=None, **_kwargs):
            calls.append((path, params or {}))
            if path == "/rest/api/3/issue/DEMO-42":
                return {
                    "id": "10001",
                    "key": "DEMO-42",
                    "fields": {
                        "summary": "Review screenshots",
                        "attachment": [{"id": "7001", "filename": "screen.png", "mimeType": "image/png"}],
                    },
                }
            start_at = int((params or {}).get("startAt", 0))
            comments = [
                {"id": "1", "body": "First"},
                {"id": "2", "body": "Second"},
            ] if start_at == 0 else [{"id": "3", "body": "Third"}]
            return {"startAt": start_at, "maxResults": 2, "total": 3, "comments": comments}

        with mock.patch.object(client, "_request", side_effect=fake_request):
            result = client.issue("DEMO-42")

        self.assertEqual([comment["id"] for comment in result["comments"]], ["1", "2", "3"])
        issue_call = calls[0]
        self.assertNotIn("comment", issue_call[1]["fields"].split(","))
        self.assertEqual(
            [params["startAt"] for path, params in calls if path.endswith("/comment")],
            [0, 2],
        )

    def test_issue_caps_a_page_that_ignores_max_results(self):
        client = jira_service.JiraClient(jira_service.JiraConfig(
            base_url="https://jira.example.invalid",
            email="dev@example.com",
            api_token="very-secret",
        ))
        calls = []

        def fake_request(path, params=None, **_kwargs):
            calls.append((path, params or {}))
            if path == "/rest/api/3/issue/DEMO-43":
                return {"id": "10002", "key": "DEMO-43", "fields": {"summary": "Many comments"}}
            return {
                "startAt": 0,
                "maxResults": 1_500,
                "total": 2_000,
                "comments": [{"id": str(index), "body": "Comment"} for index in range(1_501)],
            }

        with mock.patch.object(client, "_request", side_effect=fake_request):
            result = client.issue("DEMO-43")

        self.assertEqual(len(result["comments"]), jira_service.MAX_ISSUE_COMMENTS)
        self.assertEqual(result["comments"][0]["id"], "0")
        self.assertEqual(result["comments"][-1]["id"], "999")
        self.assertTrue(result["comments_truncated"])
        self.assertEqual(
            [params["maxResults"] for path, params in calls if path.endswith("/comment")],
            [100],
        )

    def test_attachment_bytes_accepts_jira_thumbnail_response(self):
        client = jira_service.JiraClient(jira_service.JiraConfig(
            base_url="https://jira.example.invalid",
            email="dev@example.com",
            api_token="very-secret",
        ))
        captured = {}

        def fake_request(request, timeout):
            captured["accept"] = request.headers.get("Accept")

            class Headers:
                @staticmethod
                def get_content_type():
                    return "image/png"

            class Response:
                headers = Headers()

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return None

                @staticmethod
                def read(limit):
                    return b"thumbnail"[:limit]

            return Response()

        opener = mock.Mock()
        opener.open.side_effect = fake_request
        with mock.patch.object(jira_service.urllib.request, "build_opener", return_value=opener):
            body, content_type = client._request_bytes(
                "/rest/api/3/attachment/thumbnail/7001",
                {"redirect": "false"},
                max_bytes=1024,
            )

        self.assertEqual(captured["accept"], "*/*")
        self.assertEqual(body, b"thumbnail")
        self.assertEqual(content_type, "image/png")

    def test_attachment_preview_verifies_membership_and_returns_bounded_data_url(self):
        client = jira_service.JiraClient(jira_service.JiraConfig(
            base_url="https://jira.example.invalid",
            email="dev@example.com",
            api_token="very-secret",
        ))
        attachment = {
            "id": "7001",
            "filename": "expected-dashboard.png",
            "mime_type": "image/png",
            "size": 27_030,
            "is_image": True,
        }
        with mock.patch.object(client, "attachments", return_value=[attachment]) as attachments, mock.patch.object(
            client,
            "_request_bytes",
            return_value=(b"\x89PNG\r\n", "image/png"),
        ) as request_bytes:
            result = client.attachment_preview("DEMO-42", "7001")

        attachments.assert_called_once_with("DEMO-42")
        request_bytes.assert_called_once_with(
            "/rest/api/3/attachment/thumbnail/7001",
            {
                "redirect": "false",
                "fallbackToDefault": "false",
                "width": 1200,
                "height": 900,
            },
            max_bytes=jira_service.MAX_ATTACHMENT_PREVIEW_BYTES,
        )
        self.assertEqual(result["id"], "7001")
        self.assertEqual(result["filename"], "expected-dashboard.png")
        self.assertEqual(result["data_url"], "data:image/png;base64,iVBORw0K")
        self.assertNotIn("very-secret", json.dumps(result))

    def test_attachment_preview_rejects_an_attachment_from_another_issue(self):
        client = jira_service.JiraClient(jira_service.JiraConfig(
            base_url="https://jira.example.invalid",
            email="dev@example.com",
            api_token="very-secret",
        ))
        with mock.patch.object(client, "attachments", return_value=[]), mock.patch.object(
            client,
            "_request_bytes",
        ) as request_bytes:
            with self.assertRaisesRegex(ValueError, "does not belong"):
                client.attachment_preview("DEMO-42", "7001")

        request_bytes.assert_not_called()

    def test_jira_redirects_are_refused_before_forwarding_authorization(self):
        request = jira_service.urllib.request.Request(
            "https://jira.example.invalid/rest/api/3/issue/DEMO-42",
            headers={"Authorization": "Basic secret"},
        )
        handler = jira_service.NoJiraRedirects()

        with self.assertRaisesRegex(RuntimeError, "redirect"):
            handler.redirect_request(
                request,
                None,
                302,
                "Found",
                {},
                "https://attacker.example/collect",
            )

    def test_client_uses_example_project_search_endpoint_and_never_returns_auth(self):
        config = jira_service.JiraConfig(
            base_url="https://jira.example.invalid",
            email="dev@example.com",
            api_token="very-secret",
        )
        client = jira_service.JiraClient(config)
        captured = {}

        def fake_request(request, timeout):
            captured["url"] = request.full_url
            captured["authorization"] = request.headers.get("Authorization")

            class Response:
                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return None

                def read(self, _limit=-1):
                    return json.dumps({"issues": []}).encode("utf-8")

            return Response()

        opener = mock.Mock()
        opener.open.side_effect = fake_request
        with mock.patch.object(jira_service.urllib.request, "build_opener", return_value=opener):
            result = client.search("assignee = currentUser()", max_results=25)

        self.assertIn("/rest/api/3/search/jql?", captured["url"])
        self.assertIn("maxResults=25", captured["url"])
        self.assertTrue(captured["authorization"].startswith("Basic "))
        self.assertEqual(result["issues"], [])
        self.assertNotIn("very-secret", json.dumps(result))

    def test_add_comment_posts_adf_and_returns_normalised_comment(self):
        config = jira_service.JiraConfig(
            base_url="https://jira.example.invalid",
            email="dev@example.com",
            api_token="very-secret",
        )
        client = jira_service.JiraClient(config)
        captured = {}

        def fake_request(request, timeout):
            captured["method"] = request.get_method()
            captured["url"] = request.full_url
            captured["body"] = json.loads(request.data.decode("utf-8"))

            class Response:
                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return None

                def read(self, _limit=-1):
                    return json.dumps({
                        "id": "9001",
                        "author": {"displayName": "Dev"},
                        "created": "2026-09-18T10:00:00.000+0000",
                        "updated": "2026-09-18T10:00:00.000+0000",
                        "body": captured["body"]["body"],
                    }).encode("utf-8")

            return Response()

        opener = mock.Mock()
        opener.open.side_effect = fake_request
        with mock.patch.object(jira_service.urllib.request, "build_opener", return_value=opener):
            result = client.add_comment("DEMO-42", "Implemented and verified.")

        self.assertEqual(captured["method"], "POST")
        self.assertTrue(captured["url"].endswith("/rest/api/3/issue/DEMO-42/comment"))
        self.assertEqual(captured["body"]["body"]["type"], "doc")
        self.assertEqual(result["body"], "Implemented and verified.")

    def test_transitions_returns_safe_status_choices(self):
        client = jira_service.JiraClient(jira_service.JiraConfig(
            base_url="https://jira.example.invalid",
            email="dev@example.com",
            api_token="very-secret",
        ))

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def read(self, _limit=-1):
                return json.dumps({"transitions": [
                    {"id": "31", "name": "In Progress", "to": {"name": "In Progress", "statusCategory": {"key": "indeterminate"}}}
                ]}).encode("utf-8")

        opener = mock.Mock()
        opener.open.return_value = Response()
        with mock.patch.object(jira_service.urllib.request, "build_opener", return_value=opener):
            result = client.transitions("DEMO-42")

        self.assertEqual(result, [{"id": "31", "name": "In Progress", "to": "In Progress", "category": "indeterminate"}])

    def test_transition_issue_posts_selected_transition(self):
        client = jira_service.JiraClient(jira_service.JiraConfig(
            base_url="https://jira.example.invalid",
            email="dev@example.com",
            api_token="very-secret",
        ))
        captured = {}

        def fake_request(request, timeout):
            captured["method"] = request.get_method()
            captured["body"] = json.loads(request.data.decode("utf-8"))

            class Response:
                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return None

                def read(self, _limit=-1):
                    return b""

            return Response()

        opener = mock.Mock()
        opener.open.side_effect = fake_request
        with mock.patch.object(jira_service.urllib.request, "build_opener", return_value=opener):
            result = client.transition_issue("DEMO-42", "31")

        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["body"], {"transition": {"id": "31"}})
        self.assertEqual(result, {"transition_id": "31"})


if __name__ == "__main__":
    unittest.main()
