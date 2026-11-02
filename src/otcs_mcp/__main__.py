# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Joaquín Garzón

"""Command-line entry point: ``python -m otcs_mcp``."""

import logging
import sys

import fastmcp

from otcs_mcp.config import Settings, Transport, load_settings
from otcs_mcp.errors import ConfigError
from otcs_mcp.server import build_server

logger = logging.getLogger("otcs_mcp")


def _configure_logging(settings: Settings) -> None:
    # stdout is reserved for the MCP protocol in stdio mode: log to stderr only.
    logging.basicConfig(
        stream=sys.stderr,
        level=settings.log_level.value,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def main() -> None:
    """Load the configuration and run the server on the configured transport."""
    try:
        settings = load_settings()
    except ConfigError as exc:
        sys.stderr.write(f"{exc}\n")
        raise SystemExit(2) from None
    _configure_logging(settings)
    for warning in settings.security_warnings():
        logger.warning(warning)
    if settings.mcp_transport is Transport.HTTP:
        sys.stderr.write("The HTTP transport is delivered in phase 4 of the project.\n")
        raise SystemExit(2)
    # FastMCP checks PyPI for updates when it prints its banner: never phone home from a server.
    fastmcp.settings.check_for_updates = "off"
    build_server(settings).run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main()
