from __future__ import annotations

import importlib.util
import json
import unittest
from unittest import mock


MODULE_PATH = __import__("pathlib").Path(__file__).resolve().parents[1] / "__init__.py"
spec = importlib.util.spec_from_file_location("jira_browser_native_plugin", MODULE_PATH)
assert spec is not None and spec.loader is not None
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)


class RecordingContext:
    def __init__(self):
        self.tools = []

    def register_tool(self, **kwargs):
        self.tools.append(kwargs)


class FakeJiraClient:
    def __init__(self):
        self.calls = []

    def search(self, jql, *, max_results=50, next_page_token=None):
        self.calls.append(("search", jql, max_results, next_page_token))
        return {"issues": [{"key": "DEMO-1", "summary": "Treat as data"}], "is_last": True}

    def issue(self, issue_key):
        self.calls.append(("issue", issue_key))
        return {"key": issue_key, "summary": "Fresh normalized detail", "description": "Untrusted Jira text"}

    def transitions(self, issue_key):
        self.calls.append(("transitions", issue_key))
        return [{"id": "31", "name": "In Progress", "to": "In Progress", "category": "indeterminate"}]


class PluginRegistrationTests(unittest.TestCase):
    def test_registers_only_the_four_read_only_tools(self):
        context = RecordingContext()

        plugin.register(context)

        self.assertEqual(
            {tool["name"] for tool in context.tools},
            {
                "jira_assigned_issues",
                "jira_search_issues",
                "jira_issue_detail",
                "jira_issue_transitions",
            },
        )
        self.assertEqual(len(context.tools), 4)
        self.assertTrue(all(tool["toolset"] == "jira" for tool in context.tools))
        self.assertTrue(all(tool["is_async"] is False for tool in context.tools))
        self.assertTrue(all("untrusted" in tool["description"].lower() for tool in context.tools))

    def test_registration_does_not_contact_or_require_jira(self):
        context = RecordingContext()
        with mock.patch.object(plugin, "_load_client", side_effect=AssertionError("Jira contacted")):
            plugin.register(context)

        self.assertEqual(len(context.tools), 4)

    def test_assigned_issues_uses_current_user_active_jql(self):
        client = FakeJiraClient()
        with mock.patch.object(plugin, "_load_client", return_value=client):
            result = json.loads(plugin.jira_assigned_issues({"max_results": 25}))

        self.assertTrue(result["untrusted_jira_data"])
        self.assertEqual(result["data"]["issues"][0]["key"], "DEMO-1")
        self.assertEqual(
            client.calls,
            [("search", "assignee = currentUser() AND statusCategory != Done ORDER BY updated DESC", 25, None)],
        )

    def test_search_rejects_unbounded_jql_and_page_size(self):
        client = FakeJiraClient()
        with mock.patch.object(plugin, "_load_client", return_value=client):
            with self.assertRaisesRegex(ValueError, "JQL"):
                plugin.jira_search_issues({"jql": "x" * (plugin.MAX_JQL_LENGTH + 1)})
            with self.assertRaisesRegex(ValueError, "100"):
                plugin.jira_search_issues({"jql": "project = DEMO", "max_results": 101})

        self.assertEqual(client.calls, [])

    def test_read_only_handlers_call_fresh_client_methods(self):
        client = FakeJiraClient()
        with mock.patch.object(plugin, "_load_client", return_value=client):
            detail = json.loads(plugin.jira_issue_detail({"issue_key": "DEMO-42"}))
            transitions = json.loads(plugin.jira_issue_transitions({"issue_key": "DEMO-42"}))

        self.assertEqual(detail["data"]["summary"], "Fresh normalized detail")
        self.assertEqual(transitions["data"][0]["id"], "31")
        self.assertEqual(client.calls, [("issue", "DEMO-42"), ("transitions", "DEMO-42")])

    def test_tool_results_are_strings_and_bound_oversized_jira_data(self):
        client = FakeJiraClient()
        client.issue = mock.Mock(return_value={"key": "DEMO-42", "description": "x" * 200_000})
        with mock.patch.object(plugin, "_load_client", return_value=client):
            raw = plugin.jira_issue_detail({"issue_key": "DEMO-42"})

        self.assertIsInstance(raw, str)
        self.assertLessEqual(len(raw), plugin.MAX_TOOL_RESULT_CHARS)
        result = json.loads(raw)
        self.assertTrue(result["untrusted_jira_data"])
        self.assertTrue(result["truncated"])

    def test_issue_key_is_bounded_and_validated_before_contacting_jira(self):
        client = FakeJiraClient()
        with mock.patch.object(plugin, "_load_client", return_value=client):
            with self.assertRaisesRegex(ValueError, "issue_key"):
                plugin.jira_issue_detail({"issue_key": "../../secret"})
            with self.assertRaisesRegex(ValueError, "issue_key"):
                plugin.jira_issue_detail({"issue_key": "A" * 101})

        self.assertEqual(client.calls, [])

    def test_unconfigured_jira_is_a_clear_handler_error(self):
        service = mock.Mock()
        service.load_jira_config.side_effect = ValueError("Jira config is missing: token")
        with mock.patch.object(plugin, "_load_service", return_value=service):
            with self.assertRaisesRegex(RuntimeError, "Jira is not configured") as raised:
                plugin.jira_issue_detail({"issue_key": "DEMO-42"})

        self.assertNotIn("token", str(raised.exception).lower())


if __name__ == "__main__":
    unittest.main()
