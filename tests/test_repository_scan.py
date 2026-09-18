from __future__ import annotations

import importlib.util
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path


SCAN_PATH = Path(__file__).parents[1] / "scripts" / "scan-repository.py"
SPEC = importlib.util.spec_from_file_location("repository_scan", SCAN_PATH)
assert SPEC and SPEC.loader
scan = importlib.util.module_from_spec(SPEC)
sys.modules["repository_scan"] = scan
SPEC.loader.exec_module(scan)


class RepositoryScannerTests(unittest.TestCase):
    def assert_detected(self, text: str, label: str) -> None:
        findings = scan.scan_text(text)
        self.assertTrue(any(finding.label == label for finding in findings), findings)

    def test_detects_asia_aws_key(self) -> None:
        key = "AS" + "IA" + "A" * 16
        self.assert_detected(f"aws_access_key_id: {key}\n", "AWS access key")

    def test_detects_github_fine_grained_token(self) -> None:
        token = "github" + "_pat_" + "A" * 30
        self.assert_detected(f"token: {token}\n", "GitHub token")

    def test_detects_unquoted_credential_assignment(self) -> None:
        value = "s" * 32
        self.assert_detected(f"JIRA_API_TOKEN={value}\n", "credential assignment")
        self.assert_detected(f"password: {value}\n", "credential assignment")

    def test_placeholder_bypass_requires_an_exact_placeholder(self) -> None:
        key = "api" + "Token"
        placeholder = "your-" + "jira-api-token"
        self.assertEqual(scan.scan_text(f"{key}: {placeholder}\n"), [])
        self.assert_detected(
            f"{key}: not-fake-" + "s" * 32 + "\n", "credential assignment"
        )
        self.assert_detected(
            f"{key}: " + "your-jira-api-token-" + "suffix\n",
            "credential assignment",
        )

    def test_redacts_email_and_secret_values_from_report(self) -> None:
        email = "person" + "@" + "company" + ".test"
        secret = "s" * 32
        findings = scan.scan_text(f"email = {email}\nAPI_TOKEN={secret}\n")
        stream = io.StringIO()
        with redirect_stderr(stream):
            scan.write_findings(findings, Path("repo"))
        report = stream.getvalue()
        self.assertIn("email address: <redacted>", report)
        self.assertIn("credential assignment: <redacted>", report)
        self.assertNotIn(email, report)
        self.assertNotIn(secret, report)

    def test_skips_symlinks_resolved_outside_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "root"
            outside = Path(directory) / "outside.txt"
            root.mkdir()
            outside.write_text("password: " + "s" * 32, encoding="utf-8")
            (root / "inside.txt").write_text("safe\n", encoding="utf-8")
            (root / "outside-link").symlink_to(outside)
            paths = scan.safe_tracked_paths(root, ["inside.txt", "outside-link"])
            self.assertEqual(paths, [root / "inside.txt"])


if __name__ == "__main__":
    unittest.main()
