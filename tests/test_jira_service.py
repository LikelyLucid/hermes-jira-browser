from __future__ import annotations

import importlib.util
import json
import os
import stat
import sqlite3
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

    def test_search_fields_include_subtasks_for_progress_chips(self):
        self.assertIn("subtasks", jira_service.SEARCH_FIELDS)
        self.assertIn("subtasks", jira_service.DETAIL_FIELDS)

        payload = {
            "issues": [
                {
                    "id": "10001",
                    "key": "BUG-1",
                    "fields": {
                        "summary": "Parent bug",
                        "status": {"name": "In Progress", "statusCategory": {"key": "indeterminate"}},
                        "subtasks": [
                            {
                                "id": "10002",
                                "key": "BUG-2",
                                "fields": {
                                    "summary": "Child fix",
                                    "status": {"name": "Done", "statusCategory": {"key": "done"}},
                                },
                            }
                        ],
                    },
                }
            ]
        }

        issue = jira_service.normalise_search(payload)["issues"][0]
        self.assertEqual([child["key"] for child in issue["subtasks"]], ["BUG-2"])
        self.assertEqual(issue["subtasks"][0]["status_category"], "done")

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
                        "parent": {
                            "key": "DEMO-1",
                            "fields": {"summary": "Parent story"},
                        },
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
        self.assertEqual(result["issues"][0]["parent_key"], "DEMO-1")
        self.assertEqual(result["issues"][0]["parent_summary"], "Parent story")
        self.assertEqual(result["next_page_token"], "next-token")

    def test_normalises_story_points_from_the_selected_field(self):
        payload = {
            "id": "10001",
            "key": "DEMO-42",
            "fields": {
                "summary": "Story point bug",
                "customfield_10016": "3.5",
            },
        }

        result = jira_service._normalise_issue(
            payload,
            story_points_field={"id": "customfield_10016", "name": "Story Points"},
        )

        self.assertEqual(result["story_points"], 3.5)
        self.assertEqual(
            result["story_points_field"],
            {"id": "customfield_10016", "name": "Story Points"},
        )

    def test_normalises_missing_or_non_numeric_story_points_as_none(self):
        field = {"id": "customfield_10016", "name": "Story Points"}
        for value in (None, "", "not-a-number", True):
            with self.subTest(value=value):
                result = jira_service._normalise_issue(
                    {"id": "10001", "key": "DEMO-42", "fields": {"customfield_10016": value}},
                    story_points_field=field,
                )
                self.assertIsNone(result["story_points"])

    def test_normalises_detail_issue_hierarchy(self):
        payload = {
            "id": "10001",
            "key": "DEMO-1",
            "fields": {
                "summary": "Parent story",
                "subtasks": [
                    {
                        "id": "10002",
                        "key": "DEMO-2",
                        "fields": {
                            "summary": "Child task",
                            "status": {"name": "Done", "statusCategory": {"key": "done"}},
                            "issuetype": {"name": "Sub-task"},
                        },
                    }
                ],
            },
        }

        result = jira_service._normalise_issue(payload, detail=True)

        self.assertEqual(result["subtasks"][0]["key"], "DEMO-2")
        self.assertEqual(result["subtasks"][0]["status_category"], "done")

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
    def test_store_migration_quarantines_legacy_rows_without_fabricating_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "jira.sqlite3"
            with sqlite3.connect(path) as database:
                database.execute(
                    """
                    CREATE TABLE session_links (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        issue_id TEXT NOT NULL,
                        issue_key TEXT NOT NULL,
                        session_id TEXT NOT NULL,
                        project_id TEXT,
                        worktree_path TEXT,
                        branch TEXT,
                        created_at TEXT NOT NULL,
                        UNIQUE(issue_id, session_id)
                    )
                    """
                )
                database.execute(
                    "INSERT INTO session_links(issue_id, issue_key, session_id, created_at) VALUES (?, ?, ?, ?)",
                    ("10001", "DEMO-42", "legacy-session", "2026-01-01T00:00:00Z"),
                )
            path.chmod(0o600)

            store = jira_service.JiraStore(path)
            with store._connect() as database:
                row = database.execute(
                    "SELECT jira_origin, connection_id, profile_name, target_profile FROM session_links"
                ).fetchone()

            self.assertEqual(tuple(row), ("", "", "", ""))
            self.assertEqual(
                store.links_for_issue(
                    "10001",
                    jira_origin="https://jira.example.invalid",
                    connection_id="local",
                    profile_name="default",
                    target_profile="default",
                ),
                [],
            )

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

    def test_completed_mutation_receipts_are_pruned_to_a_bounded_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = jira_service.JiraStore(Path(tmp) / "jira.sqlite3")
            for index in range(3):
                key = f"comment-key-prune-{index:04d}"
                store.reserve_mutation(
                    idempotency_key=key,
                    action="comment",
                    issue_key="DEMO-42",
                    payload={"body": f"Done {index}"},
                )
                store.complete_mutation(key, {"comment": {"id": str(index)}})

            removed = store.prune_mutation_receipts(max_completed=2, retention_days=30)
            with store._connect() as db:
                completed = db.execute("SELECT COUNT(*) FROM mutation_receipts WHERE completed = 1").fetchone()[0]

        self.assertEqual(removed, 1)
        self.assertEqual(completed, 2)

    def test_completing_a_receipt_keeps_the_completed_count_within_the_hard_limit(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(
            jira_service, "MAX_COMPLETED_MUTATION_RECEIPTS", 2
        ):
            store = jira_service.JiraStore(Path(tmp) / "jira.sqlite3")
            for index in range(3):
                key = f"comment-key-hard-limit-{index:04d}"
                store.reserve_mutation(
                    idempotency_key=key,
                    action="comment",
                    issue_key="DEMO-42",
                    payload={"body": f"Done {index}"},
                )
                store.complete_mutation(key, {"comment": {"id": str(index)}})

            with store._connect() as db:
                completed = db.execute(
                    "SELECT COUNT(*) FROM mutation_receipts WHERE completed = 1"
                ).fetchone()[0]

        self.assertEqual(completed, 2)

    def test_expired_same_key_is_pruned_before_replay_lookup(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = jira_service.JiraStore(Path(tmp) / "jira.sqlite3")
            kwargs = {
                "idempotency_key": "comment-key-expired-0001",
                "action": "comment",
                "issue_key": "DEMO-42",
                "payload": {"body": "Done"},
            }
            store.reserve_mutation(**kwargs)
            store.complete_mutation(kwargs["idempotency_key"], {"comment": {"id": "1"}})
            with store._connect() as db:
                db.execute(
                    "UPDATE mutation_receipts SET completed_at = ?, updated_at = ? WHERE idempotency_key = ?",
                    ("2000-01-01T00:00:00Z", "2000-01-01T00:00:00Z", kwargs["idempotency_key"]),
                )

            self.assertEqual(store.reserve_mutation(**kwargs), {"status": "claimed"})

    def test_store_startup_prunes_expired_completed_receipts_without_new_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "jira.sqlite3"
            store = jira_service.JiraStore(path)
            key = "comment-key-startup-expired"
            store.reserve_mutation(
                idempotency_key=key,
                action="comment",
                issue_key="DEMO-42",
                payload={"body": "Done"},
            )
            store.complete_mutation(key, {"comment": {"id": "1"}})
            with store._connect() as db:
                db.execute(
                    "UPDATE mutation_receipts SET completed_at = ?, updated_at = ? WHERE idempotency_key = ?",
                    ("2000-01-01T00:00:00Z", "2000-01-01T00:00:00Z", key),
                )

            reopened = jira_service.JiraStore(path)
            self.assertIsNone(reopened.get_mutation_receipt(key))

    def test_pending_mutation_receipts_have_a_hard_global_limit(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(jira_service, "MAX_PENDING_MUTATION_RECEIPTS", 2):
            store = jira_service.JiraStore(Path(tmp) / "jira.sqlite3")
            for index in range(2):
                store.reserve_mutation(
                    idempotency_key=f"comment-key-pending-{index:04d}",
                    action="comment",
                    issue_key="DEMO-42",
                    payload={"body": f"Done {index}"},
                )

            with self.assertRaisesRegex(jira_service.MutationPendingError, "unresolved"):
                store.reserve_mutation(
                    idempotency_key="comment-key-pending-9999",
                    action="comment",
                    issue_key="DEMO-42",
                    payload={"body": "Another"},
                )

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
                jira_origin="https://jira.example.invalid",
                connection_id="local",
                profile_name="default",
                target_profile="default",
                project_id="p_123",
                worktree_path="/repo/example_project/.worktrees/jira-DEMO-42",
                branch="jira/DEMO-42-fix",
            )

            self.assertEqual(store.get_project_mapping("DEMO")["hermes_project_id"], "p_123")
            links = store.links_for_issue(
                "10001",
                jira_origin="https://jira.example.invalid",
                connection_id="local",
                profile_name="default",
                target_profile="default",
            )
            self.assertEqual(len(links), 1)
            self.assertEqual(links[0]["session_id"], "session-1")
            self.assertEqual(links[0]["branch"], "jira/DEMO-42-fix")

            self.assertTrue(store.unlink_session(
                issue_id="10001",
                session_id="session-1",
                jira_origin="https://jira.example.invalid",
                connection_id="local",
                profile_name="default",
                target_profile="default",
            ))
            self.assertEqual(
                store.links_for_issue(
                    "10001",
                    jira_origin="https://jira.example.invalid",
                    connection_id="local",
                    profile_name="default",
                    target_profile="default",
                ),
                [],
            )
            self.assertFalse(store.unlink_session(
                issue_id="10001",
                session_id="session-1",
                jira_origin="https://jira.example.invalid",
                connection_id="local",
                profile_name="default",
                target_profile="default",
            ))

    def test_store_migrates_pre_target_owner_unique_constraint_before_linking(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "jira.sqlite3"
            with sqlite3.connect(path) as database:
                database.execute(
                    """
                    CREATE TABLE session_links (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        issue_id TEXT NOT NULL,
                        issue_key TEXT NOT NULL,
                        jira_origin TEXT NOT NULL DEFAULT '',
                        connection_id TEXT NOT NULL DEFAULT 'local',
                        profile_name TEXT NOT NULL DEFAULT 'default',
                        target_profile TEXT NOT NULL DEFAULT 'default',
                        session_id TEXT NOT NULL,
                        project_id TEXT,
                        worktree_path TEXT,
                        branch TEXT,
                        detached INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL,
                        UNIQUE(jira_origin, issue_id, connection_id, profile_name, session_id)
                    )
                    """
                )
                database.execute(
                    """
                    INSERT INTO session_links
                        (issue_id, issue_key, jira_origin, connection_id, profile_name,
                         target_profile, session_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "10001",
                        "DEMO-42",
                        "https://jira.example.invalid",
                        "local",
                        "default",
                        "default",
                        "session-1",
                        "2026-01-01T00:00:00Z",
                    ),
                )
            path.chmod(0o600)

            store = jira_service.JiraStore(path)
            with store._connect() as database:
                table_sql = "".join(str(database.execute(
                    "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'session_links'"
                ).fetchone()[0]).lower().split())
            self.assertIn(
                "unique(jira_origin,issue_id,connection_id,profile_name,target_profile,session_id)",
                table_sql,
            )
            store.link_session(
                issue_id="10002",
                issue_key="DEMO-43",
                session_id="session-1",
                jira_origin="https://jira.example.invalid",
                connection_id="local",
                profile_name="default",
                target_profile="default",
                move_existing=True,
            )
            self.assertEqual(
                store.links_for_issue(
                    "10002",
                    jira_origin="https://jira.example.invalid",
                    connection_id="local",
                    profile_name="default",
                    target_profile="default",
                )[0]["session_id"],
                "session-1",
            )

    def test_manual_link_moves_a_chat_to_a_new_ticket_and_tombstones_the_old_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = jira_service.JiraStore(Path(tmp) / "jira.sqlite3")
            common = {
                "session_id": "session-1",
                "jira_origin": "https://jira.example.invalid",
                "connection_id": "local",
                "profile_name": "default",
                "target_profile": "default",
            }
            store.link_session(issue_id="10001", issue_key="DEMO-42", **common)
            moved = store.link_session(
                issue_id="10002",
                issue_key="DEMO-43",
                move_existing=True,
                **common,
            )

            self.assertEqual(moved["issue_key"], "DEMO-43")
            self.assertEqual(
                store.links_for_issue("10001", **{key: common[key] for key in ("jira_origin", "connection_id", "profile_name", "target_profile")}),
                [],
            )
            old_detached = store.detached_links_for_issue(
                "10001",
                **{key: common[key] for key in ("jira_origin", "connection_id", "profile_name", "target_profile")},
            )
            self.assertEqual([row["session_id"] for row in old_detached], ["session-1"])
            new_links = store.links_for_issue(
                "10002",
                **{key: common[key] for key in ("jira_origin", "connection_id", "profile_name", "target_profile")},
            )
            self.assertEqual([row["session_id"] for row in new_links], ["session-1"])

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
        links = [{
            "session_id": "session-1",
            "branch": "jira/DEMO-42",
            "connection_id": "local",
            "profile_name": "default",
            "target_profile": "default",
        }]
        with mock.patch.dict(sys.modules, {"hermes_state": module}):
            result = jira_service.enrich_session_links(links)

        self.assertEqual(result[0]["chat_title"], "Fix milk totals")
        self.assertTrue(result[0]["archived"])
        self.assertTrue(result[0]["available"])

    def test_foreign_owner_links_do_not_resolve_against_active_session_db(self):
        class FakeSessionDB:
            def __init__(self, read_only=False):
                self.read_only = read_only

            def get_session(self, session_id):
                raise AssertionError("foreign owner must not query active SessionDB")

            def close(self):
                pass

        module = types.ModuleType("hermes_state")
        setattr(module, "SessionDB", FakeSessionDB)
        links = [{
            "session_id": "same-id",
            "connection_id": "work-vps",
            "profile_name": "coder",
            "target_profile": "coder",
        }]
        with mock.patch.dict(sys.modules, {"hermes_state": module}), mock.patch.object(
            jira_service, "active_profile_name", return_value="default"
        ):
            result = jira_service.enrich_session_links(links)

        self.assertIsNone(result[0]["chat_title"])
        self.assertIsNone(result[0]["archived"])
        self.assertIsNone(result[0]["available"])

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
    def test_missing_settings_use_current_sprint_default_with_all_tickets_view(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "missing.json"
            settings = jira_service.load_settings(path)
            self.assertTrue(path.exists())

        self.assertEqual(settings["defaultView"], "current-sprint")
        self.assertEqual(settings["version"], 3)
        self.assertEqual(settings["viewMode"], "board")
        self.assertEqual(settings["storyPointsField"], "auto")
        self.assertIn("Backlog", [view["label"] for view in settings["views"]])
        self.assertIn("Bugs", [view["label"] for view in settings["views"]])
        self.assertEqual(settings["views"][0]["label"], "My current sprint")
        self.assertEqual(settings["views"][0]["jql"], "sprint in openSprints() AND assignee = currentUser() ORDER BY updated DESC")
        self.assertEqual(next(view["jql"] for view in settings["views"] if view["id"] == "all"), "ORDER BY updated DESC")

    def test_legacy_builtin_views_gain_sprint_and_all_once_without_losing_customizations(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            legacy = {
                **jira_service.DEFAULT_SETTINGS,
                "version": 1,
                "defaultView": "assigned",
                "views": [
                    {"id": "assigned", "label": "Assigned to me", "jql": "assignee = currentUser() AND statusCategory != Done ORDER BY updated DESC"},
                    {"id": "custom", "label": "Custom", "jql": "project = DEMO"},
                ],
            }
            path.write_text(json.dumps(legacy), encoding="utf-8")
            migrated = jira_service.load_settings(path)
            self.assertEqual(migrated["defaultView"], "current-sprint")
            self.assertEqual(migrated["version"], 3)
            self.assertEqual(migrated["views"][0]["id"], "current-sprint")
            self.assertIn("all", [view["id"] for view in migrated["views"]])
            self.assertEqual(next(view["jql"] for view in migrated["views"] if view["id"] == "custom"), "project = DEMO")
            self.assertEqual(jira_service.load_settings(path), migrated)
            migrated["views"] = [view for view in migrated["views"] if view["id"] != "all"]
            jira_service.save_settings(migrated, path)
            self.assertEqual(jira_service.load_settings(path), migrated)

    def test_partially_migrated_legacy_settings_do_not_restore_a_removed_view(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            settings = {**jira_service.DEFAULT_SETTINGS, "version": 1}
            settings["views"] = [view for view in settings["views"] if view["id"] != "all"]
            path.write_text(json.dumps(settings), encoding="utf-8")
            loaded = jira_service.load_settings(path)
            self.assertNotIn("all", [view["id"] for view in loaded["views"]])
            self.assertEqual(loaded["version"], 3)

    def test_prior_sprint_default_migrates_only_the_old_builtin_query(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            old_sprint = {**jira_service.DEFAULT_SETTINGS["views"][0], "label": "Current sprint", "jql": "sprint in openSprints() ORDER BY updated DESC"}
            settings = {**jira_service.DEFAULT_SETTINGS, "version": 2, "views": [old_sprint, *jira_service.DEFAULT_SETTINGS["views"][1:]]}
            path.write_text(json.dumps(settings), encoding="utf-8")
            upgraded = jira_service.load_settings(path)
            self.assertEqual(upgraded["version"], 3)
            self.assertEqual(upgraded["views"][0]["jql"], "sprint in openSprints() AND assignee = currentUser() ORDER BY updated DESC")
            self.assertEqual(upgraded["views"][0]["label"], "My current sprint")
            self.assertEqual(jira_service.load_settings(path), upgraded)
            for prior_version in (1, 2):
                custom = {**settings, "version": prior_version, "views": [{**old_sprint, "jql": "project = DEMO AND sprint in openSprints()"}, *settings["views"][1:]]}
                path.write_text(json.dumps(custom), encoding="utf-8")
                retained = jira_service.load_settings(path)
                self.assertEqual(retained["views"][0]["jql"], custom["views"][0]["jql"])
                self.assertEqual(retained["version"], 3)

    def test_legacy_explicit_custom_default_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            legacy = {
                **jira_service.DEFAULT_SETTINGS,
                "version": 1,
                "defaultView": "reported",
                "views": [view for view in jira_service.DEFAULT_SETTINGS["views"] if view["id"] not in {"current-sprint", "all"}],
            }
            path.write_text(json.dumps(legacy), encoding="utf-8")
            migrated = jira_service.load_settings(path)
            self.assertEqual(migrated["defaultView"], "reported")
            self.assertIn("all", [view["id"] for view in migrated["views"]])

    def test_default_views_include_human_preferences(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = jira_service.load_settings(Path(tmp) / "missing.json")

        assigned = next(view for view in settings["views"] if view["id"] == "assigned")
        backlog = next(view for view in settings["views"] if view["id"] == "backlog")
        self.assertEqual(assigned["layout"], "board")
        self.assertEqual(assigned["sort"], "updated")
        self.assertEqual(assigned["density"], "comfortable")
        self.assertEqual(backlog["layout"], "list")
        self.assertEqual(backlog["sort"], "priority")
        self.assertEqual(backlog["density"], "compact")

    def test_saved_view_preferences_are_validated_and_preserved(self):
        value = {
            "version": 1,
            "defaultView": "mine",
            "viewMode": "board",
            "pageSize": 50,
            "baseRef": "HEAD",
            "groupByStatus": True,
            "storyPointsField": "customfield_10016",
            "views": [{
                "id": "mine",
                "label": "Mine",
                "jql": "assignee = currentUser()",
                "layout": "list",
                "sort": "priority",
                "density": "compact",
            }],
        }

        settings = jira_service.validate_settings(value)

        self.assertEqual(settings["views"][0]["layout"], "list")
        self.assertEqual(settings["storyPointsField"], "customfield_10016")
        self.assertEqual(settings["views"][0]["sort"], "priority")
        self.assertEqual(settings["views"][0]["density"], "compact")

    def test_saved_view_preferences_reject_unknown_values(self):
        value = {
            "version": 1,
            "defaultView": "mine",
            "viewMode": "board",
            "pageSize": 50,
            "baseRef": "HEAD",
            "groupByStatus": True,
            "views": [{
                "id": "mine",
                "label": "Mine",
                "jql": "assignee = currentUser()",
                "layout": "calendar",
            }],
        }

        with self.assertRaisesRegex(ValueError, "layout"):
            jira_service.validate_settings(value)

    def test_settings_round_trip_as_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            value = {
                "version": 1,
                "defaultView": "mine",
                "viewMode": "list",
                "pageSize": 25,
                "baseRef": "main",
                "groupByStatus": False,
                "storyPointsField": "auto",
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

    def test_story_points_field_setting_accepts_auto_none_or_custom_field_id(self):
        base = {
            "version": 1,
            "defaultView": "mine",
            "viewMode": "list",
            "pageSize": 25,
            "baseRef": "HEAD",
            "groupByStatus": False,
            "views": [{"id": "mine", "label": "Mine", "jql": "project = DEMO"}],
        }

        self.assertEqual(jira_service.validate_settings(base)["storyPointsField"], "auto")
        self.assertEqual(
            jira_service.validate_settings({**base, "storyPointsField": "none"})["storyPointsField"],
            "none",
        )
        self.assertEqual(
            jira_service.validate_settings({**base, "storyPointsField": "customfield_10101"})["storyPointsField"],
            "customfield_10101",
        )
        with self.assertRaisesRegex(ValueError, "storyPointsField"):
            jira_service.validate_settings({**base, "storyPointsField": "Story Points"})

    def test_settings_reject_invalid_default_view(self):
        value = {
            "version": 1,
            "defaultView": "missing",
            "viewMode": "board",
            "pageSize": 50,
            "baseRef": "HEAD",
            "views": [{"id": "mine", "label": "Mine", "jql": "assignee = currentUser()"}],
        }
        with self.assertRaisesRegex(ValueError, "defaultView"):
            jira_service.validate_settings(value)

    def test_settings_reject_invalid_view_mode(self):
        value = {
            "version": 1,
            "defaultView": "mine",
            "viewMode": "calendar",
            "pageSize": 50,
            "baseRef": "HEAD",
            "views": [{"id": "mine", "label": "Mine", "jql": "assignee = currentUser()"}],
        }
        with self.assertRaisesRegex(ValueError, "viewMode"):
            jira_service.validate_settings(value)

    def test_legacy_settings_infer_list_mode_when_status_grouping_was_disabled(self):
        value = {
            "version": 1,
            "defaultView": "mine",
            "pageSize": 50,
            "baseRef": "HEAD",
            "groupByStatus": False,
            "views": [{"id": "mine", "label": "Mine", "jql": "assignee = currentUser()"}],
        }
        settings = jira_service.validate_settings(value)
        self.assertEqual(settings["viewMode"], "list")

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

    def test_client_auto_detects_story_points_and_requests_the_detected_field(self):
        config = jira_service.JiraConfig(
            base_url="https://jira.example.invalid",
            email="dev@example.com",
            api_token="very-secret",
        )
        client = jira_service.JiraClient(config)
        calls = []

        def fake_request(path, params=None, **_kwargs):
            calls.append((path, params or {}))
            if path == "/rest/api/3/field/search":
                return {"values": [{
                    "id": "customfield_10016",
                    "name": "Story Points",
                    "schema": {"custom": "com.atlassian.jira.plugin.system.customfieldtypes:float"},
                }]}
            return {
                "issues": [{
                    "id": "10001",
                    "key": "DEMO-42",
                    "fields": {"summary": "Bug", "customfield_10016": 3},
                }],
                "isLast": True,
            }

        with mock.patch.object(client, "_request", side_effect=fake_request):
            result = client.search("issuetype = Bug", max_results=25)

        self.assertEqual(calls[0][0], "/rest/api/3/field/search")
        self.assertIn("customfield_10016", calls[1][1]["fields"].split(","))
        self.assertEqual(result["story_points_field"], {"id": "customfield_10016", "name": "Story Points"})
        self.assertEqual(result["issues"][0]["story_points"], 3)

    def test_client_explicit_story_points_override_does_not_require_field_discovery(self):
        client = jira_service.JiraClient(
            jira_service.JiraConfig(
                base_url="https://jira.example.invalid",
                email="dev@example.com",
                api_token="very-secret",
            ),
            story_points_field="customfield_10101",
        )
        calls = []

        def fake_request(path, params=None, **_kwargs):
            calls.append((path, params or {}))
            return {
                "issues": [{
                    "id": "10001",
                    "key": "DEMO-42",
                    "fields": {"summary": "Bug", "customfield_10101": "5.5"},
                }],
                "isLast": True,
            }

        with mock.patch.object(client, "_request", side_effect=fake_request):
            result = client.search("issuetype = Bug")

        self.assertEqual([path for path, _params in calls], ["/rest/api/3/search/jql"])
        self.assertEqual(result["issues"][0]["story_points"], 5.5)
        self.assertEqual(result["story_points_field"]["id"], "customfield_10101")

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

    def test_add_comment_rejects_malformed_success_responses_as_ambiguous(self):
        client = jira_service.JiraClient(jira_service.JiraConfig(
            base_url="https://jira.example.invalid",
            email="dev@example.com",
            api_token="very-secret",
        ))

        for response_payload in (
            {},
            {"id": "9001"},
            {"id": "9001", "body": {}},
            {"id": "9001", "body": {"type": "doc", "content": [{}]}},
            {
                "id": "9" * 257,
                "body": "Implemented and verified.",
            },
            {
                "id": "9001",
                "body": "\ud800",
            },
            {
                "id": "\ud800",
                "body": "Implemented and verified.",
            },
        ):
            class Response:
                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return None

                def read(self, _limit=-1):
                    return json.dumps(response_payload).encode("utf-8")

            opener = mock.Mock()
            opener.open.return_value = Response()
            with self.subTest(response_payload=response_payload), mock.patch.object(
                jira_service.urllib.request, "build_opener", return_value=opener
            ), self.assertRaises(jira_service.JiraAmbiguousError):
                client.add_comment("DEMO-42", "Implemented and verified.")

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
                status = 204

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

    def test_transition_issue_rejects_empty_normal_success_as_ambiguous(self):
        client = jira_service.JiraClient(jira_service.JiraConfig(
            base_url="https://jira.example.invalid",
            email="dev@example.com",
            api_token="very-secret",
        ))

        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def read(self, _limit=-1):
                return b"{}"

        opener = mock.Mock()
        opener.open.return_value = Response()
        with mock.patch.object(jira_service.urllib.request, "build_opener", return_value=opener):
            with self.assertRaises(jira_service.JiraAmbiguousError):
                client.transition_issue("DEMO-42", "31")

    def test_transition_validator_rejects_unmarked_empty_payload(self):
        with self.assertRaises(jira_service.JiraAmbiguousError):
            jira_service._validate_transition_response({}, "31")


if __name__ == "__main__":
    unittest.main()
