# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Joaquín Garzón

"""Checks on the TLS files used by the HTTPS transport."""

import stat

from otcs_mcp.config import Settings
from otcs_mcp.errors import ConfigError


def check_tls_files(settings: Settings) -> list[str]:
    """Verify the certificate and key exist; return warnings about their permissions.

    Raises ConfigError when TLS is enabled but a file is missing. The container
    entrypoint generates them on first start; outside Docker provide them yourself.
    """
    if not settings.tls_enabled:
        return []
    missing = [
        name
        for name, path in (
            ("TLS_CERT_FILE", settings.tls_cert_file),
            ("TLS_KEY_FILE", settings.tls_key_file),
        )
        if not path.is_file()
    ]
    if missing:
        raise ConfigError(
            f"TLS is enabled but {' and '.join(missing)} not found. Start the server through "
            "the Docker entrypoint (TLS_AUTO_GENERATE=true), provide the files, or set "
            "TLS_ENABLED=false."
        )
    warnings: list[str] = []
    mode = stat.S_IMODE(settings.tls_key_file.stat().st_mode)
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        warnings.append(
            f"TLS_KEY_FILE permissions are {mode:04o}; "
            "the private key should be readable by its owner only (0600)."
        )
    return warnings
