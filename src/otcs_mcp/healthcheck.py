# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Joaquín Garzón

"""Container health probe: ``python -m otcs_mcp.healthcheck`` (exit 0 = healthy).

It only calls the unauthenticated ``/healthz`` endpoint on the loopback interface,
so no credentials are involved and certificate verification is skipped on purpose
(the probe talks to the server's own process, whichever certificate it presents).
"""

import http.client
import os
import ssl
import sys

TIMEOUT_SECONDS = 3.0
_FALSE_VALUES = {"false", "0", "no", "off"}


def probe(port: int, *, use_tls: bool) -> bool:
    """Return True when GET /healthz answers 200 on 127.0.0.1:``port``."""
    connection: http.client.HTTPConnection
    if use_tls:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE  # loopback probe of our own process  # nosec B501
        connection = http.client.HTTPSConnection(
            "127.0.0.1", port, timeout=TIMEOUT_SECONDS, context=context
        )
    else:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=TIMEOUT_SECONDS)
    try:
        connection.request("GET", "/healthz", headers={"Host": "localhost"})
        return connection.getresponse().status == 200
    except (OSError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def main() -> int:
    """Read the port and TLS flag from the environment and probe the server."""
    try:
        port = int(os.environ.get("MCP_PORT", "8443"))
    except ValueError:
        return 1
    use_tls = os.environ.get("TLS_ENABLED", "true").strip().lower() not in _FALSE_VALUES
    return 0 if probe(port, use_tls=use_tls) else 1


if __name__ == "__main__":
    sys.exit(main())
