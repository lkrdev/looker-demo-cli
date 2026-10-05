#!/usr/bin/env python3
"""Local secret and credential scanner for looker-demo-cli.

Runs as a pre-commit hook locally and in CI to detect:
- Looker SDK secrets (live LOOKERSDK_CLIENT_SECRET assignments)
- Google Cloud credentials (ADC files, service account JSON keys, API keys, OAuth tokens)
- Cryptographic private keys and sensitive certificate files
- Staged .env and credential files
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Files that should NEVER be committed under any circumstance
FORBIDDEN_FILE_PATTERNS = [
    re.compile(r"^\.env(\..+)?$", re.IGNORECASE),
    re.compile(r".*application_default_credentials\.json$", re.IGNORECASE),
    re.compile(r".*service[-_]?account.*\.json$", re.IGNORECASE),
    re.compile(r".*\.(pem|pkcs12|p12|key)$", re.IGNORECASE),
]

# Allowlisted filenames (e.g., templates or examples)
ALLOWLISTED_FILENAMES = {
    ".env.example",
    ".env.template",
    ".env.sample",
}

# Regex patterns for detecting real secrets in text content
SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "Looker Client Secret assignment",
        re.compile(r"""LOOKERSDK_CLIENT_SECRET\s*[:=]\s*['"]([a-zA-Z0-9_\-]{16,})['"]"""),
    ),
    (
        "Google API Key (AIza...)",
        re.compile(r"""\bAIza[0-9A-Za-z\-_]{30,40}\b"""),
    ),
    (
        "Google OAuth Access / Refresh Token",
        re.compile(r"""\b(ya29\.[0-9A-Za-z\-_]{20,}|1//[0-9A-Za-z\-_]{20,})\b"""),
    ),
    (
        "Cryptographic Private Key block",
        re.compile(r"""-----BEGIN (?:[A-Z0-9_-]+ )?PRIVATE KEY-----"""),
    ),
    (
        "Embedded Basic Auth in Looker URL",
        re.compile(r"""https?://[a-zA-Z0-9_.-]+:[a-zA-Z0-9_.-]+@[a-zA-Z0-9\-.]+\.looker\.com"""),
    ),
]

# Known harmless placeholders in templates or tests
SAFE_PLACEHOLDERS = {
    "<client_secret>",
    "<your-instance>",
    "<client_id>",
    "mock_secret",
    "test_secret",
    "dummy_secret",
    "placeholder",
    "{opts.client_secret}",
    "test-secret-key",
}

# Files to ignore (e.g. lockfiles, binary artifacts, self-tests)
IGNORE_FILES = {
    "uv.lock",
    ".demo-state.json",
    ".coverage",
    "test_check_secrets.py",
}


def check_file(path: Path) -> list[str]:
    violations: list[str] = []

    # 1. Filename checks
    if path.name not in ALLOWLISTED_FILENAMES:
        for pattern in FORBIDDEN_FILE_PATTERNS:
            if pattern.search(path.name):
                violations.append(f"Forbidden sensitive file name committed: {path.name}")
                return violations

    # Skip checking binaries and ignored metadata
    if path.name in IGNORE_FILES or not path.is_file():
        return violations

    try:
        content = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return violations

    # Check for GCP service account JSON structure
    if '"type": "service_account"' in content and '"private_key":' in content:
        if "# pragma: allowlist secret" not in content:
            violations.append("File contains GCP Service Account JSON credentials with private key")
            return violations

    # 2. Content regex checks
    for line_num, line in enumerate(content.splitlines(), start=1):
        if "# pragma: allowlist secret" in line or "# nosec" in line:
            continue

        for label, pattern in SECRET_PATTERNS:
            match = pattern.search(line)
            if match:
                matched_val = match.group(1) if match.groups() else match.group(0)
                if matched_val.lower() in SAFE_PLACEHOLDERS or any(p in matched_val for p in SAFE_PLACEHOLDERS):
                    continue
                # Mask secret in output to prevent terminal leakage
                masked = matched_val[:4] + "..." + matched_val[-3:] if len(matched_val) > 8 else "***"
                violations.append(f"Line {line_num}: {label} found (value: {masked})")

    return violations


def main() -> int:
    files_to_check = [Path(p) for p in sys.argv[1:] if Path(p).is_file()]
    if not files_to_check:
        return 0

    has_errors = False
    for file_path in files_to_check:
        violations = check_file(file_path)
        if violations:
            has_errors = True
            print(f"\n❌ \033[1;31mSecret Check Failure\033[0m: {file_path}")
            for v in violations:
                print(f"   • {v}")
            print("   \033[90m(Add '# pragma: allowlist secret' if this is a deliberate test mock)\033[0m")

    return 1 if has_errors else 0


if __name__ == "__main__":
    sys.exit(main())
