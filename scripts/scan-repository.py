#!/usr/bin/env python3
"""Fail on high-confidence credentials or non-placeholder email addresses."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PATTERNS = (
    (re.compile(r"-----BEGIN [A-Z ]+ PRIVATE KEY-----"), "private key"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS access key"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "GitHub token"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"), "Slack token"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"), "Google API key"),
    (
        re.compile(
            r"(?i)(?:api[_-]?token|access[_-]?token|client[_-]?secret|password|passwd|secret)"
            r"\s*[:=]\s*[\"']([^\"'\n]{24,})[\"']"
        ),
        "credential assignment",
    ),
)
PLACEHOLDER_DOMAINS = {"example.com", "example.org", "example.net", "example.invalid"}
PLACEHOLDER_WORDS = ("example", "placeholder", "changeme", "your-", "dummy", "fake")


def tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True
    )
    return [ROOT / item for item in result.stdout.decode().split("\0") if item]


def is_placeholder_email(address: str) -> bool:
    return address.rsplit("@", 1)[1].lower() in PLACEHOLDER_DOMAINS


def is_placeholder_secret(value: str) -> bool:
    lowered = value.lower()
    return any(word in lowered for word in PLACEHOLDER_WORDS)


def main() -> int:
    findings: list[tuple[Path, int, str, str]] = []
    for path in tracked_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for line_number, line in enumerate(text.splitlines(), 1):
            for match in EMAIL.finditer(line):
                if not is_placeholder_email(match.group()):
                    findings.append((path, line_number, "email address", match.group()))
            for pattern, label in PATTERNS:
                match = pattern.search(line)
                if not match:
                    continue
                value = match.group(1) if pattern.groups else match.group()
                if label == "credential assignment" and is_placeholder_secret(value):
                    continue
                findings.append((path, line_number, label, "<redacted>"))
    if findings:
        for path, line_number, label, value in findings:
            print(f"{path.relative_to(ROOT)}:{line_number}: {label}: {value}", file=sys.stderr)
        print(f"secret/PII scan failed with {len(findings)} finding(s)", file=sys.stderr)
        return 1
    print("secret/PII scan passed (no high-confidence findings)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
