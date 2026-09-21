from __future__ import annotations

import asyncio
import importlib.util
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from pydantic import ValidationError


MODULE_PATH = Path(__file__).resolve().parents[1] / "dashboard" / "plugin_api.py"
spec = importlib.util.spec_from_file_location("jira_browser_plugin_api", MODULE_PATH)
assert spec is not None and spec.loader is not None
plugin_api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin_api)


class JiraBrowserApiTests(unittest.TestCase):
    def test_mutation_requests_reject_whitespace_before_reserving_a_receipt(self):
        with self.assertRaises(ValueError):
            plugin_api.CommentRequest(body="   ", idempotency_key="comment-key-123456")
        with self.assertRaises(ValueError):
            plugin_api.TransitionRequest(transition_id="   ", idempotency_key="transition-key-123456")

    def test_router_exposes_expected_read_and_handoff_routes(self):
        routes = {(route.path, next(iter(route.methods))) for route in plugin_api.router.routes if route.methods}
        paths = {path for path, _method in routes}
        self.assertIn("/status", paths)
        self.assertIn("/settings", paths)
        self.assertIn("/issues", paths)
        self.assertIn("/issues/batch", paths)
        self.assertIn("/issues/{issue_key}", paths)
        self.assertIn("/issues/{issue_key}/repository-context", paths)
        self.assertIn("/issues/{issue_key}/attachments/{attachment_id}/preview", paths)
        self.assertIn("/issues/{issue_key}/comments", paths)
        self.assertIn("/issues/{issue_key}/transitions", paths)
        self.assertIn("/mappings/{jira_project_key}", paths)
        self.assertIn("/sessions/{jira_project_key}", paths)
        self.assertIn("/worktrees", paths)
        self.assertIn("/worktrees/cleanup", paths)
        self.assertIn("/links/{issue_id}", paths)
        self.assertIn("/links/{issue_id}/{session_id}", paths)

    def test_issue_batch_is_bounded_deduplicated_and_concurrency_limited(self):
        client = mock.Mock()
        client.config.base_url = "https://jira.example.invalid"
        client.issue_summary.side_effect = lambda key: {"id": key + "-id", "key": key, "summary": "safe"}
        client.transitions.side_effect = lambda key: [{"id": "31", "name": "Done"}]
        payload = plugin_api.IssueBatchRequest(
            issue_keys=["demo-1", "DEMO-2", "DEMO-1"],
            include_links=False,
            include_details=False,
            include_transitions=True,
            connection_id="local",
            profile_name="default",
            target_profile="default",
        )
        with mock.patch.object(plugin_api.SERVICE, "active_profile_name", return_value="default"), mock.patch.object(
            plugin_api, "_client", return_value=client
        ):
            result = asyncio.run(plugin_api.issue_batch(payload))

        self.assertEqual(result["requested_items"], 2)
        self.assertTrue(result["bounded"])
        self.assertEqual([item["issue"]["key"] for item in result["items"]], ["DEMO-1", "DEMO-2"])
        self.assertEqual(client.issue_summary.call_count, 2)
        self.assertEqual(client.transitions.call_count, 2)

    def test_issue_batch_passes_complete_owner_to_link_reads(self):
        client = mock.Mock()
        client.config.base_url = "https://jira.example.invalid"
        client.issue_summary.return_value = {"id": "1001", "key": "DEMO-1", "summary": "safe"}
        store = mock.Mock()
        store.links_for_issue.return_value = []
        payload = plugin_api.IssueBatchRequest(
            issue_keys=["DEMO-1"],
            include_links=True,
            include_details=False,
            include_transitions=False,
            connection_id="local",
            profile_name="default",
            target_profile="default",
        )
        with mock.patch.object(plugin_api.SERVICE, "active_profile_name", return_value="default"), mock.patch.object(
            plugin_api, "_client", return_value=client
        ), mock.patch.object(plugin_api, "_store", return_value=store):
            result = asyncio.run(plugin_api.issue_batch(payload))

        self.assertEqual(result["items"][0]["links"], [])
        store.links_for_issue.assert_called_once_with(
            "1001",
            jira_origin="https://jira.example.invalid",
            connection_id="local",
            profile_name="default",
            target_profile="default",
        )

        with self.assertRaises(ValidationError):
            plugin_api.IssueBatchRequest(issue_keys=["DEMO-1"])

    def test_issue_batch_rejects_invalid_keys_before_loading_jira(self):
        payload = plugin_api.IssueBatchRequest(
            issue_keys=["not-a-jira-key"],
            include_links=False,
            connection_id="local",
            profile_name="default",
            target_profile="default",
        )
        with mock.patch.object(plugin_api, "_client") as client:
            with self.assertRaises(plugin_api.HTTPException):
                asyncio.run(plugin_api.issue_batch(payload))
        client.assert_not_called()

    def test_focused_session_context_requires_owner_and_bounds_untrusted_content(self):
        missing = asyncio.run(plugin_api.focused_session_context("session-1"))
        self.assertEqual(missing, {"available": False, "reason": "owner_required"})

        client = mock.Mock()
        client.config.base_url = "https://jira.example.invalid/"
        client.issue.return_value = {
            "id": "1001",
            "key": "DEMO-1",
            "summary": "A" * 1_000,
            "status": "In Progress",
            "comments": [{"id": "c1", "body": "B" * 10_000}],
            "attachments": [{"id": "a1", "filename": "file.txt", "mime_type": "text/plain", "size": "12"}],
        }
        store = mock.Mock()
        store.link_for_session_owner.return_value = {
            "session_id": "session-1",
            "issue_id": "1001",
            "issue_key": "DEMO-1",
            "connection_id": "local",
            "profile_name": "default",
            "target_profile": "default",
        }
        with mock.patch.object(plugin_api.SERVICE, "active_profile_name", return_value="default"), mock.patch.object(
            plugin_api, "_client", return_value=client
        ), mock.patch.object(plugin_api, "_store", return_value=store):
            result = asyncio.run(
                plugin_api.focused_session_context(
                    "session-1", profile_name="default", connection_id="local", target_profile="default"
                )
            )

        self.assertTrue(result["available"])
        self.assertEqual(len(result["context"]["summary"]), plugin_api.MAX_CHAT_CONTEXT_SUMMARY_CHARS)
        self.assertEqual(len(result["context"]["comments"][0]["body"]), plugin_api.MAX_CHAT_CONTEXT_COMMENT_CHARS)
        self.assertEqual(result["context"]["issue_url"], "https://jira.example.invalid/browse/DEMO-1")

    def test_status_is_sanitised(self):
        with mock.patch.object(
            plugin_api.SERVICE,
            "config_status",
            return_value={
                "configured": True,
                "path": "/home/user/jira-config/config.json",
                "base_url": "https://jira.example.invalid",
                "email": "dev@example.com",
                "error": None,
            },
        ):
            result = asyncio.run(plugin_api.status())

        self.assertTrue(result["configured"])
        self.assertNotIn("token", str(result).lower())

    def test_repository_context_is_read_only_and_uses_server_side_store(self):
        store = mock.Mock()
        expected = {"status": "unavailable", "available": False, "reason": "worktree_missing"}
        with mock.patch.object(plugin_api, "_store", return_value=store), mock.patch.object(
            plugin_api.REPOSITORY_CONTEXT,
            "get_repository_context",
            return_value=expected,
        ) as context:
            result = asyncio.run(plugin_api.issue_repository_context("DEMO-42", base_ref="main"))

        self.assertEqual(result, expected)
        context.assert_called_once_with(store, "DEMO-42", base_ref="main")

    def test_repository_context_failures_become_renderer_safe_unavailable_payloads(self):
        with mock.patch.object(plugin_api, "_store", side_effect=RuntimeError("secret path")), mock.patch.object(
            plugin_api.REPOSITORY_CONTEXT,
            "unavailable_context",
            return_value={"status": "unavailable", "available": False, "reason": "repository_context_unavailable"},
        ) as unavailable:
            result = asyncio.run(plugin_api.issue_repository_context("DEMO-42"))

        self.assertEqual(result["status"], "unavailable")
        self.assertNotIn("secret path", str(result))
        unavailable.assert_called_once_with("DEMO-42")

    def test_settings_are_loaded_and_saved_through_service(self):
        settings = {
            "version": 1,
            "defaultView": "assigned",
            "pageSize": 50,
            "baseRef": "HEAD",
            "views": [{"id": "assigned", "label": "Assigned", "jql": "assignee = currentUser()"}],
        }
        with mock.patch.object(plugin_api.SERVICE, "load_settings", return_value=settings) as load:
            result = asyncio.run(plugin_api.settings())
        self.assertEqual(result["settings"], settings)
        load.assert_called_once_with()

        with mock.patch.object(plugin_api.SERVICE, "save_settings", return_value=settings) as save:
            result = asyncio.run(plugin_api.put_settings(settings))
        self.assertEqual(result["settings"], settings)
        save.assert_called_once_with(settings)

    def test_add_comment_uses_jira_client(self):
        client = mock.Mock()
        client.add_comment.return_value = {"id": "9001", "body": "Done"}
        payload = plugin_api.CommentRequest(body="Done", idempotency_key="comment-key-123456")
        with tempfile.TemporaryDirectory() as tmp:
            store = plugin_api.SERVICE.JiraStore(Path(tmp) / "state.sqlite3")
            with mock.patch.object(plugin_api, "_store", return_value=store), mock.patch.object(plugin_api, "_client", return_value=client):
                result = asyncio.run(plugin_api.add_comment("DEMO-42", payload))

        self.assertEqual(result["comment"]["id"], "9001")
        client.add_comment.assert_called_once_with("DEMO-42", "Done")

    def test_duplicate_comment_calls_jira_once_and_returns_stored_result(self):
        client = mock.Mock()
        client.add_comment.return_value = {"id": "9001", "body": "Done"}
        payload = plugin_api.CommentRequest(body="Done", idempotency_key="comment-key-123457")
        with tempfile.TemporaryDirectory() as tmp:
            store = plugin_api.SERVICE.JiraStore(Path(tmp) / "state.sqlite3")
            with mock.patch.object(plugin_api, "_store", return_value=store), mock.patch.object(plugin_api, "_client", return_value=client):
                first = asyncio.run(plugin_api.add_comment("DEMO-42", payload))
                second = asyncio.run(plugin_api.add_comment("DEMO-42", payload))

        self.assertEqual(first, second)
        client.add_comment.assert_called_once_with("DEMO-42", "Done")

    def test_concurrent_duplicate_comment_is_rejected_while_first_request_is_in_flight(self):
        entered = threading.Event()
        release = threading.Event()
        client = mock.Mock()

        def add_comment(_issue_key, _body):
            entered.set()
            if not release.wait(timeout=2):
                raise RuntimeError("test synchronization timed out")
            return {"id": "9001", "body": "Done"}

        client.add_comment.side_effect = add_comment
        payload = plugin_api.CommentRequest(body="Done", idempotency_key="comment-key-concurrent-1")

        async def exercise(store):
            with mock.patch.object(plugin_api, "_store", return_value=store), mock.patch.object(
                plugin_api, "_client", return_value=client
            ):
                first = asyncio.create_task(plugin_api.add_comment("DEMO-42", payload))
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                try:
                    with self.assertRaisesRegex(plugin_api.HTTPException, "already pending"):
                        await plugin_api.add_comment("DEMO-42", payload)
                finally:
                    release.set()
                return await first

        with tempfile.TemporaryDirectory() as tmp:
            store = plugin_api.SERVICE.JiraStore(Path(tmp) / "state.sqlite3")
            result = asyncio.run(exercise(store))

        self.assertEqual(result["comment"]["id"], "9001")
        client.add_comment.assert_called_once_with("DEMO-42", "Done")

    def test_duplicate_transition_calls_jira_once_and_returns_stored_result(self):
        client = mock.Mock()
        client.transition_issue.return_value = {"transition_id": "31"}
        payload = plugin_api.TransitionRequest(transition_id="31", idempotency_key="transition-key-123456")
        with tempfile.TemporaryDirectory() as tmp:
            store = plugin_api.SERVICE.JiraStore(Path(tmp) / "state.sqlite3")
            with mock.patch.object(plugin_api, "_store", return_value=store), mock.patch.object(plugin_api, "_client", return_value=client):
                first = asyncio.run(plugin_api.transition_issue("DEMO-42", payload))
                second = asyncio.run(plugin_api.transition_issue("DEMO-42", payload))

        self.assertEqual(first, second)
        client.transition_issue.assert_called_once_with("DEMO-42", "31")

    def test_conflicting_idempotency_key_is_rejected(self):
        client = mock.Mock()
        client.add_comment.return_value = {"id": "9001"}
        with tempfile.TemporaryDirectory() as tmp:
            store = plugin_api.SERVICE.JiraStore(Path(tmp) / "state.sqlite3")
            with mock.patch.object(plugin_api, "_store", return_value=store), mock.patch.object(plugin_api, "_client", return_value=client):
                asyncio.run(plugin_api.add_comment("DEMO-42", plugin_api.CommentRequest(body="Done", idempotency_key="same-key-1234567")))
                with self.assertRaisesRegex(plugin_api.HTTPException, "different mutation"):
                    asyncio.run(plugin_api.add_comment("DEMO-42", plugin_api.CommentRequest(body="Other", idempotency_key="same-key-1234567")))

        client.add_comment.assert_called_once_with("DEMO-42", "Done")

    def test_malformed_comment_response_keeps_pending_receipt_without_repeating_jira(self):
        payload = plugin_api.CommentRequest(body="Done", idempotency_key="comment-key-123460")
        client = mock.Mock()
        client.add_comment.side_effect = plugin_api.SERVICE.JiraAmbiguousError(
            "Jira returned an invalid comment response; the write outcome is unknown."
        )
        with tempfile.TemporaryDirectory() as tmp:
            store = plugin_api.SERVICE.JiraStore(Path(tmp) / "state.sqlite3")
            with mock.patch.object(plugin_api, "_store", return_value=store), mock.patch.object(plugin_api, "_client", return_value=client):
                with self.assertRaises(plugin_api.HTTPException):
                    asyncio.run(plugin_api.add_comment("DEMO-42", payload))
                with self.assertRaisesRegex(plugin_api.HTTPException, "already pending"):
                    asyncio.run(plugin_api.add_comment("DEMO-42", payload))
                receipt = store.get_mutation_receipt(payload.idempotency_key)
                self.assertIsNotNone(receipt)
                self.assertEqual(receipt["completed"], 0)

        client.add_comment.assert_called_once_with("DEMO-42", "Done")

    def test_remote_failure_keeps_pending_receipt_without_repeating_jira(self):
        payload = plugin_api.CommentRequest(body="Done", idempotency_key="comment-key-123463")
        client = mock.Mock()
        client.add_comment.side_effect = RuntimeError("request outcome unknown")
        with tempfile.TemporaryDirectory() as tmp:
            store = plugin_api.SERVICE.JiraStore(Path(tmp) / "state.sqlite3")
            with mock.patch.object(plugin_api, "_store", return_value=store), mock.patch.object(plugin_api, "_client", return_value=client):
                with self.assertRaises(plugin_api.HTTPException):
                    asyncio.run(plugin_api.add_comment("DEMO-42", payload))
                with self.assertRaisesRegex(plugin_api.HTTPException, "already pending"):
                    asyncio.run(plugin_api.add_comment("DEMO-42", payload))

        client.add_comment.assert_called_once_with("DEMO-42", "Done")

    def test_pre_jira_client_failure_releases_idempotency_claim(self):
        payload = plugin_api.CommentRequest(body="Done", idempotency_key="comment-key-123458")
        with tempfile.TemporaryDirectory() as tmp:
            store = plugin_api.SERVICE.JiraStore(Path(tmp) / "state.sqlite3")
            with mock.patch.object(plugin_api, "_store", return_value=store), mock.patch.object(
                plugin_api, "_client", side_effect=plugin_api.SERVICE.JiraPreRequestError("not configured")
            ):
                with self.assertRaises(plugin_api.HTTPException):
                    asyncio.run(plugin_api.add_comment("DEMO-42", payload))
            self.assertIsNone(store.get_mutation_receipt(payload.idempotency_key))

    def test_definitive_jira_rejection_releases_idempotency_claim(self):
        payload = plugin_api.CommentRequest(body="Done", idempotency_key="comment-key-123461")
        rejected = mock.Mock()
        rejected.add_comment.side_effect = plugin_api.SERVICE.JiraDefinitiveRejectionError("Jira rejected the comment.")
        accepted = mock.Mock()
        accepted.add_comment.return_value = {"id": "9002", "body": "Done"}
        with tempfile.TemporaryDirectory() as tmp:
            store = plugin_api.SERVICE.JiraStore(Path(tmp) / "state.sqlite3")
            with mock.patch.object(plugin_api, "_store", return_value=store), mock.patch.object(
                plugin_api, "_client", side_effect=[rejected, accepted]
            ):
                with self.assertRaises(plugin_api.HTTPException):
                    asyncio.run(plugin_api.add_comment("DEMO-42", payload))
                result = asyncio.run(plugin_api.add_comment("DEMO-42", payload))

        self.assertEqual(result["comment"]["id"], "9002")
        rejected.add_comment.assert_called_once_with("DEMO-42", "Done")
        accepted.add_comment.assert_called_once_with("DEMO-42", "Done")

    def test_local_pre_request_failure_releases_idempotency_claim(self):
        payload = plugin_api.CommentRequest(body="Done", idempotency_key="comment-key-123462")
        client = mock.Mock()
        client.add_comment.side_effect = plugin_api.SERVICE.JiraPreRequestError("invalid request")
        with tempfile.TemporaryDirectory() as tmp:
            store = plugin_api.SERVICE.JiraStore(Path(tmp) / "state.sqlite3")
            with mock.patch.object(plugin_api, "_store", return_value=store), mock.patch.object(plugin_api, "_client", return_value=client):
                with self.assertRaises(plugin_api.HTTPException):
                    asyncio.run(plugin_api.add_comment("DEMO-42", payload))
            self.assertIsNone(store.get_mutation_receipt(payload.idempotency_key))

    def test_attachment_preview_uses_jira_client_without_exposing_credentials(self):
        client = mock.Mock()
        client.attachment_preview.return_value = {
            "id": "7001",
            "filename": "expected-dashboard.png",
            "mime_type": "image/png",
            "data_url": "data:image/png;base64,iVBORw0K",
        }
        with mock.patch.object(plugin_api, "_client", return_value=client):
            result = asyncio.run(plugin_api.attachment_preview("DEMO-42", "7001"))

        self.assertTrue(result["attachment"]["data_url"].startswith("data:image/png;base64,"))
        self.assertNotIn("token", str(result).lower())
        client.attachment_preview.assert_called_once_with("DEMO-42", "7001")

    def test_transition_issue_uses_selected_transition(self):
        client = mock.Mock()
        client.transition_issue.return_value = {"transition_id": "31"}
        payload = plugin_api.TransitionRequest(transition_id="31", idempotency_key="transition-key-123459")
        with tempfile.TemporaryDirectory() as tmp:
            store = plugin_api.SERVICE.JiraStore(Path(tmp) / "state.sqlite3")
            with mock.patch.object(plugin_api, "_store", return_value=store), mock.patch.object(plugin_api, "_client", return_value=client):
                result = asyncio.run(plugin_api.transition_issue("DEMO-42", payload))

        self.assertEqual(result["transition_id"], "31")
        client.transition_issue.assert_called_once_with("DEMO-42", "31")

    def test_worktree_uses_stored_project_mapping_not_request_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_home = os.environ.get("HERMES_HOME")
            os.environ["HERMES_HOME"] = tmp
            try:
                store = plugin_api.SERVICE.JiraStore(Path(tmp) / "state.sqlite3")
                store.set_project_mapping(
                    jira_project_key="DEMO",
                    hermes_project_id="p_1",
                    hermes_project_label="Example Project",
                    repo_path="/trusted/repo",
                )
                payload = plugin_api.WorktreeRequest(
                    jira_project_key="DEMO",
                    issue_key="DEMO-42",
                    summary="Fix sync",
                )
                with mock.patch.object(plugin_api, "_store", return_value=store), mock.patch.object(
                    plugin_api.SERVICE,
                    "create_worktree",
                    return_value={
                        "created": True,
                        "path": "/trusted/repo/.worktrees/jira-DEMO-42",
                        "branch": "jira/DEMO-42",
                        "repo_path": "/trusted/repo",
                        "base_ref": "HEAD",
                    },
                ) as create:
                    result = asyncio.run(plugin_api.create_worktree(payload))
            finally:
                if old_home is None:
                    os.environ.pop("HERMES_HOME", None)
                else:
                    os.environ["HERMES_HOME"] = old_home

        self.assertEqual(result["branch"], "jira/DEMO-42")
        create.assert_called_once_with(
            repo_path="/trusted/repo",
            issue_key="DEMO-42",
            summary="Fix sync",
            base_ref="HEAD",
        )

    def test_link_derives_metadata_in_backend(self):
        store = mock.Mock()
        store.link_session.return_value = {"session_id": "session-1", "project_id": "p_1"}
        client = mock.Mock()
        client.config.base_url = "https://jira.example.invalid"
        client.issue.return_value = {"id": "10001", "key": "DEMO-42"}
        payload = plugin_api.SessionLinkRequest(
            issue_id="10001",
            issue_key="DEMO-42",
            session_id="session-1",
            connection_id="local",
            profile_name="default",
            target_profile="default",
            move_existing=True,
        )
        with mock.patch.object(plugin_api, "_store", return_value=store), mock.patch.object(
            plugin_api, "_client", return_value=client
        ), mock.patch.object(
            plugin_api.SERVICE,
            "validated_link_metadata",
            return_value={
                "project_id": "p_1",
                "worktree_path": "/trusted/repo/.worktrees/jira-DEMO-42",
                "branch": "jira/DEMO-42",
            },
        ) as validate:
            result = asyncio.run(plugin_api.link_session(payload))

        self.assertEqual(result["link"]["project_id"], "p_1")
        client.issue.assert_called_once_with("DEMO-42")
        validate.assert_called_once_with(store, issue_key="DEMO-42", session_id="session-1")
        store.link_session.assert_called_once_with(
            issue_id="10001",
            issue_key="DEMO-42",
            session_id="session-1",
            jira_origin="https://jira.example.invalid",
            connection_id="local",
            profile_name="default",
            target_profile="default",
            project_id="p_1",
            worktree_path="/trusted/repo/.worktrees/jira-DEMO-42",
            branch="jira/DEMO-42",
            clear_detachment=False,
            move_existing=True,
        )

    def test_unlink_removes_only_the_ticket_session_association(self):
        store = mock.Mock()
        store.unlink_session.return_value = True
        client = mock.Mock()
        client.config.base_url = "https://jira.example.invalid"
        with mock.patch.object(plugin_api, "_store", return_value=store), mock.patch.object(
            plugin_api, "_client", return_value=client
        ):
            result = asyncio.run(plugin_api.unlink_session(
                "10001",
                "session-1",
                connection_id=" local ",
                profile_name=" default ",
                target_profile=" default ",
            ))

        self.assertEqual(result, {"unlinked": True})
        store.unlink_session.assert_called_once_with(
            issue_id="10001",
            session_id="session-1",
            jira_origin="https://jira.example.invalid",
            connection_id="local",
            profile_name="default",
            target_profile="default",
        )

    def test_issue_links_include_archived_chat_title(self):
        store = mock.Mock()
        raw = [{
            "session_id": "session-1",
            "jira_origin": "https://jira.example.invalid",
            "connection_id": "local",
            "profile_name": "default",
            "target_profile": "default",
        }]
        detached = [{
            "session_id": "session-2",
            "jira_origin": "https://jira.example.invalid",
            "connection_id": "local",
            "profile_name": "default",
            "target_profile": "default",
        }]
        enriched = [{"session_id": "session-1", "chat_title": "Fix milk totals", "archived": True, "available": True}]
        store.links_for_issue.return_value = raw
        store.detached_links_for_issue.return_value = detached
        client = mock.Mock()
        client.config.base_url = "https://jira.example.invalid"
        with mock.patch.object(plugin_api, "_store", return_value=store), mock.patch.object(
            plugin_api, "_client", return_value=client
        ), mock.patch.object(
            plugin_api.SERVICE,
            "enrich_session_links",
            return_value=enriched,
        ) as enrich:
            result = asyncio.run(plugin_api.issue_links(
                "10001",
                connection_id="local",
                profile_name="default",
                target_profile="default",
            ))

        self.assertEqual(result["links"], enriched)
        self.assertEqual(result["detached"], detached)
        store.links_for_issue.assert_called_once_with(
            "10001",
            jira_origin="https://jira.example.invalid",
            connection_id="local",
            profile_name="default",
            target_profile="default",
        )
        store.detached_links_for_issue.assert_called_once_with(
            "10001",
            jira_origin="https://jira.example.invalid",
            connection_id="local",
            profile_name="default",
            target_profile="default",
        )
        enrich.assert_called_once_with(raw)

    def test_project_sessions_use_server_side_mapping_and_include_archived(self):
        store = mock.Mock()
        store.get_project_mapping.return_value = {"repo_path": "/trusted/repo"}
        sessions = [{"id": "archived", "archived": True, "cwd": "/trusted/repo/.worktrees/a"}]
        with mock.patch.object(plugin_api, "_store", return_value=store), mock.patch.object(
            plugin_api.SERVICE,
            "list_linkable_sessions",
            return_value=sessions,
        ) as scan:
            result = asyncio.run(plugin_api.project_sessions("DEMO"))

        self.assertEqual(result["sessions"], sessions)
        store.get_project_mapping.assert_called_once_with("DEMO")
        scan.assert_called_once_with("/trusted/repo")


if __name__ == "__main__":
    unittest.main()
