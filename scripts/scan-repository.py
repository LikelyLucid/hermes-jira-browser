#!/usr/bin/env python3
"""Fail on high-confidence credentials or non-placeholder email addresses."""
from __future__ import annotations

from dataclasses import dataclass
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
TOKEN_PATTERNS = (
    (re.compile(r"-----BEGIN [A-Z ]+ PRIVATE KEY-----"), "private key"),
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "AWS access key"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "GitHub token"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), "GitHub token"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"), "Slack token"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"), "Google API key"),
)
CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?:api[_-]?(?:token|key)|access[_-]?token|"
    r"auth[_-]?token|client[_-]?secret|password|passwd|secret)"
    r"(?![A-Za-z0-9])\s*[:=]\s*"
    r"(?P<value>\"[^\"\n]*\"|'[^'\n]*'|[^\s,#;]+)"
)
PLACEHOLDER_DOMAINS = {"example.com", "example.org", "example.net", "example.invalid"}
# Keep this allowlist exact. Substring checks let real values such as
# "not-fake-secret" evade the scan.
PLACEHOLDER_SECRETS = {
    "...",
    "api-token",
    "api_token",
    "changeme",
    "dummy",
    "example",
    "fake",
    "fake-token",
    "placeholder",
    "token",
    "very-secret",
    "your-api-key",
    "your-api-token",
    "your-jira-api-token",
    "your-password",
    "your-secret",
    "your-token",
}


@dataclass(frozen=True)
class Finding:
    path: Path
    line_number: int
    label: str


def safe_tracked_paths(root: Path, relative_paths: Iterable[str | Path]) -> list[Path]:
    """Return tracked files whose resolved targets remain below *root*."""
    resolved_root = root.resolve()
    safe_paths: list[Path] = []
    for relative_path in relative_paths:
        path = root / relative_path
        try:
            resolved_path = path.resolve(strict=False)
            resolved_path.relative_to(resolved_root)
        except (OSError, ValueError):
            continue
        if resolved_path.is_file():
            safe_paths.append(path)
    return safe_paths


def tracked_files(root: Path = ROOT) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, check=True, capture_output=True
    )
    relative_paths = result.stdout.decode().split("\0")
    return safe_tracked_paths(root, (item for item in relative_paths if item))


def is_placeholder_email(address: str) -> bool:
    return address.rsplit("@", 1)[1].lower() in PLACEHOLDER_DOMAINS


def is_placeholder_secret(value: str) -> bool:
    normalized = value.strip().strip("\"'").lower()
    return normalized in PLACEHOLDER_SECRETS


def scan_text(text: str, path: Path = Path("<text>")) -> list[Finding]:
    findings: list[Finding] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        for match in EMAIL.finditer(line):
            if not is_placeholder_email(match.group()):
                findings.append(Finding(path, line_number, "email address"))
        for pattern, label in TOKEN_PATTERNS:
            if pattern.search(line):
                findings.append(Finding(path, line_number, label))
        assignment = CREDENTIAL_ASSIGNMENT.search(line)
        if assignment:
            value = assignment.group("value").strip("\"'")
            if (
                len(value) >= 8
                and not any(character in value for character in "()[]{}")
                and not is_placeholder_secret(value)
            ):
                findings.append(Finding(path, line_number, "credential assignment"))
    return findings


def write_findings(findings: Iterable[Finding], root: Path = ROOT) -> None:
    for finding in findings:
        try:
            display_path = finding.path.relative_to(root)
        except ValueError:
            display_path = finding.path
        print(
            f"{display_path}:{finding.line_number}: {finding.label}: <redacted>",
            file=sys.stderr,
        )


def main() -> int:
    findings: list[Finding] = []
    for path in tracked_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        findings.extend(scan_text(text, path))
    if findings:
        write_findings(findings)
        print(f"secret/PII scan failed with {len(findings)} finding(s)", file=sys.stderr)
        return 1
    print("secret/PII scan passed (no high-confidence findings)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
