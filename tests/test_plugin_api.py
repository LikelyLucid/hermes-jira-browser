from __future__ import annotations

import asyncio
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "dashboard" / "plugin_api.py"
spec = importlib.util.spec_from_file_location("jira_browser_plugin_api", MODULE_PATH)
assert spec is not None and spec.loader is not None
plugin_api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin_api)


class JiraBrowserApiTests(unittest.TestCase):
    def test_router_exposes_expected_read_and_handoff_routes(self):
        routes = {(route.path, next(iter(route.methods))) for route in plugin_api.router.routes if route.methods}
        paths = {path for path, _method in routes}
        self.assertIn("/status", paths)
        self.assertIn("/settings", paths)
        self.assertIn("/issues", paths)
        self.assertIn("/issues/{issue_key}", paths)
        self.assertIn("/issues/{issue_key}/attachments/{attachment_id}/preview", paths)
        self.assertIn("/issues/{issue_key}/comments", paths)
        self.assertIn("/issues/{issue_key}/transitions", paths)
        self.assertIn("/mappings/{jira_project_key}", paths)
        self.assertIn("/sessions/{jira_project_key}", paths)
        self.assertIn("/worktrees", paths)
        self.assertIn("/worktrees/cleanup", paths)
        self.assertIn("/links/{issue_id}", paths)
        self.assertIn("/links/{issue_id}/{session_id}", paths)

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
        payload = plugin_api.CommentRequest(body="Done")
        with mock.patch.object(plugin_api, "_client", return_value=client):
            result = asyncio.run(plugin_api.add_comment("DEMO-42", payload))

        self.assertEqual(result["comment"]["id"], "9001")
        client.add_comment.assert_called_once_with("DEMO-42", "Done")

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
        payload = plugin_api.TransitionRequest(transition_id="31")
        with mock.patch.object(plugin_api, "_client", return_value=client):
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
        payload = plugin_api.SessionLinkRequest(
            issue_id="10001",
            issue_key="DEMO-42",
            session_id="session-1",
        )
        with mock.patch.object(plugin_api, "_store", return_value=store), mock.patch.object(
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
        validate.assert_called_once_with(store, issue_key="DEMO-42", session_id="session-1")
        store.link_session.assert_called_once_with(
            issue_id="10001",
            issue_key="DEMO-42",
            session_id="session-1",
            project_id="p_1",
            worktree_path="/trusted/repo/.worktrees/jira-DEMO-42",
            branch="jira/DEMO-42",
        )

    def test_unlink_removes_only_the_ticket_session_association(self):
        store = mock.Mock()
        store.unlink_session.return_value = True
        with mock.patch.object(plugin_api, "_store", return_value=store):
            result = asyncio.run(plugin_api.unlink_session("10001", "session-1"))

        self.assertEqual(result, {"unlinked": True})
        store.unlink_session.assert_called_once_with(issue_id="10001", session_id="session-1")

    def test_issue_links_include_archived_chat_title(self):
        store = mock.Mock()
        raw = [{"session_id": "session-1"}]
        enriched = [{"session_id": "session-1", "chat_title": "Fix milk totals", "archived": True, "available": True}]
        store.links_for_issue.return_value = raw
        with mock.patch.object(plugin_api, "_store", return_value=store), mock.patch.object(
            plugin_api.SERVICE,
            "enrich_session_links",
            return_value=enriched,
        ) as enrich:
            result = asyncio.run(plugin_api.issue_links("10001"))

        self.assertEqual(result["links"], enriched)
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
