# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Joaquín Garzón

from pathlib import Path

import pytest

from otcs_mcp.config import Settings
from otcs_mcp.errors import ConfigError
from otcs_mcp.tls import check_tls_files


def _with_files(settings: Settings, tmp_path: Path, *, key_mode: int = 0o600) -> Settings:
    cert, key = tmp_path / "server.crt", tmp_path / "server.key"
    cert.write_text("cert")
    key.write_text("key")
    key.chmod(key_mode)
    return settings.model_copy(update={"tls_cert_file": cert, "tls_key_file": key})


def test_no_checks_run_when_tls_is_disabled(settings: Settings, tmp_path: Path) -> None:
    disabled = settings.model_copy(
        update={"tls_enabled": False, "tls_cert_file": tmp_path / "missing.crt"}
    )

    assert check_tls_files(disabled) == []


def test_files_with_private_key_permissions_pass_without_warnings(
    settings: Settings, tmp_path: Path
) -> None:
    assert check_tls_files(_with_files(settings, tmp_path)) == []


@pytest.mark.parametrize("mode", [0o640, 0o644, 0o604, 0o660, 0o777])
def test_key_readable_by_group_or_others_produces_a_warning(
    settings: Settings, tmp_path: Path, mode: int
) -> None:
    warnings = check_tls_files(_with_files(settings, tmp_path, key_mode=mode))

    assert len(warnings) == 1
    assert "0600" in warnings[0]
    assert f"{mode:04o}" in warnings[0]


@pytest.mark.parametrize(
    ("missing", "expected"),
    [
        ("cert", ["TLS_CERT_FILE"]),
        ("key", ["TLS_KEY_FILE"]),
        ("both", ["TLS_CERT_FILE", "TLS_KEY_FILE"]),
    ],
)
def test_missing_files_raise_config_error_naming_each_missing_variable(
    settings: Settings, tmp_path: Path, missing: str, expected: list[str]
) -> None:
    ready = _with_files(settings, tmp_path)
    if missing in {"cert", "both"}:
        ready.tls_cert_file.unlink()
    if missing in {"key", "both"}:
        ready.tls_key_file.unlink()

    with pytest.raises(ConfigError) as info:
        check_tls_files(ready)

    for name in expected:
        assert name in str(info.value)
    assert "TLS_AUTO_GENERATE" in str(info.value)
