from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "desktop" / "data.js"


class DesktopDataLayerTests(unittest.TestCase):
    def test_scope_key_contains_every_route_identity(self):
        source = DATA.read_text(encoding="utf-8")
        self.assertIn("origin", source)
        self.assertIn("connectionId", source)
        self.assertIn("profile", source)
        self.assertIn("targetProfile", source)
        self.assertIn("jira-browser", source)

    def test_batch_loader_is_bounded_and_uses_shared_query_client(self):
        source = DATA.read_text(encoding="utf-8")
        self.assertIn("MAX_BATCH_ISSUES = 50", source)
        self.assertIn("queryClient.fetchQuery", source)
        self.assertIn("/issues/batch", source)
        self.assertIn(".slice(0, MAX_BATCH_ISSUES)", source)

    def test_batch_loader_validates_keys_and_sends_complete_owner_scope(self):
        source = DATA.read_text(encoding="utf-8")
        self.assertIn("ISSUE_KEY_PATTERN", source)
        self.assertIn("Invalid Jira issue key", source)
        self.assertIn("connection_id: scopeValue.connectionId", source)
        self.assertIn("profile_name: scopeValue.profile", source)
        self.assertIn("target_profile: scopeValue.targetProfile", source)

    def test_snapshot_writer_is_lru_bounded(self):
        source = DATA.read_text(encoding="utf-8")
        self.assertIn("MAX_SNAPSHOTS = 6", source)
        self.assertIn("slice(0, limit)", source)
        self.assertIn("storedAt", source)


    def test_data_module_is_node_syntax_valid(self):
        result = subprocess.run(
            ["node", "--check", str(DATA)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
