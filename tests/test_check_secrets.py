"""Unit tests for the secret scanner pre-commit hook."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.check_secrets import check_file

pytestmark = pytest.mark.unit


def test_clean_file_passes(tmp_path: Path) -> None:
    f = tmp_path / "clean.py"
    f.write_text("import os\nurl = os.getenv('LOOKERSDK_BASE_URL')\n", encoding="utf-8")
    assert check_file(f) == []


def test_looker_client_secret_assignment_fails(tmp_path: Path) -> None:
    f = tmp_path / "config.py"
    f.write_text('LOOKERSDK_CLIENT_SECRET = "abc12345678901234567890"\n', encoding="utf-8")
    violations = check_file(f)
    assert len(violations) == 1
    assert "Looker Client Secret assignment" in violations[0]


def test_looker_client_secret_placeholder_passes(tmp_path: Path) -> None:
    f = tmp_path / "template.txt"
    f.write_text('LOOKERSDK_CLIENT_SECRET="{opts.client_secret}"\n', encoding="utf-8")
    assert check_file(f) == []


def test_forbidden_file_name_fails(tmp_path: Path) -> None:
    f = tmp_path / ".env"
    f.write_text("FOO=BAR\n", encoding="utf-8")
    violations = check_file(f)
    assert len(violations) == 1
    assert "Forbidden sensitive file name" in violations[0]


def test_forbidden_adc_file_name_fails(tmp_path: Path) -> None:
    f = tmp_path / "application_default_credentials.json"
    f.write_text("{}", encoding="utf-8")
    violations = check_file(f)
    assert len(violations) == 1
    assert "Forbidden sensitive file name" in violations[0]


def test_google_api_key_fails(tmp_path: Path) -> None:
    f = tmp_path / "api.py"
    f.write_text('KEY = "AIzaSyD-123456789012345678901234567890"\n', encoding="utf-8")
    violations = check_file(f)
    assert len(violations) == 1
    assert "Google API Key" in violations[0]


def test_pragma_allowlist_passes(tmp_path: Path) -> None:
    f = tmp_path / "test_mock.py"
    f.write_text(
        'LOOKERSDK_CLIENT_SECRET = "real_looking_secret_key_12345" # pragma: allowlist secret\n', encoding="utf-8"
    )
    assert check_file(f) == []


def test_special_char_looker_secret_fails(tmp_path: Path) -> None:
    f = tmp_path / "secret.env"
    f.write_text('LOOKERSDK_CLIENT_SECRET = "abc/def+ghi=jkl123456789"\n', encoding="utf-8")
    violations = check_file(f)
    assert any("Looker Client Secret assignment" in v for v in violations)


def test_percent_encoded_looker_url_fails(tmp_path: Path) -> None:
    f = tmp_path / "url.py"
    f.write_text('URL = "https://user%40domain.com:p%40ss%2Bword@demo.looker.com/api"\n', encoding="utf-8")
    violations = check_file(f)
    assert len(violations) == 1
    assert "Embedded Basic Auth in Looker URL" in violations[0]


def test_minified_gcp_service_account_json_fails(tmp_path: Path) -> None:
    f = tmp_path / "sa.json"
    f.write_text(
        '{"type":"service_account","project_id":"p","private_key":"-----BEGIN PRIVATE KEY-----\\n..."}',
        encoding="utf-8",
    )
    violations = check_file(f)
    assert any("GCP Service Account JSON credentials" in v for v in violations)
