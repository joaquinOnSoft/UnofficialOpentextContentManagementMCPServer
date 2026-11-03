# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Joaquín Garzón

"""Command-line entry point: ``python -m otcs_mcp``."""

import logging
import sys
from typing import Any

import fastmcp
import uvicorn

from otcs_mcp.client import OtcsClient
from otcs_mcp.config import Settings, Transport, load_settings
from otcs_mcp.errors import ConfigError
from otcs_mcp.http_app import MCP_PATH, build_http_app
from otcs_mcp.logging_setup import configure_logging
from otcs_mcp.observability import Metrics
from otcs_mcp.server import build_server
from otcs_mcp.tls import check_tls_files

logger = logging.getLogger("otcs_mcp")


def run_http(settings: Settings, metrics: Metrics) -> None:
    """Serve the MCP endpoint over HTTPS (or plain HTTP when TLS is explicitly disabled)."""
    tls_warnings = check_tls_files(settings)
    for warning in tls_warnings:
        logger.warning(warning)
    mcp = build_server(settings, client=OtcsClient(settings, metrics=metrics), metrics=metrics)
    app = build_http_app(settings, mcp, metrics=metrics)
    tls_options: dict[str, Any] = (
        {"ssl_certfile": str(settings.tls_cert_file), "ssl_keyfile": str(settings.tls_key_file)}
        if settings.tls_enabled
        else {}
    )
    scheme = "https" if settings.tls_enabled else "http"
    logger.info(
        "Starting MCP server on %s://%s:%d%s",
        scheme,
        settings.mcp_host,
        settings.mcp_port,
        MCP_PATH,
    )
    uvicorn.run(
        app,
        host=settings.mcp_host,
        port=settings.mcp_port,
        log_config=None,  # our own logging configuration stays in charge
        access_log=False,  # the security middleware writes the access log
        server_header=False,
        **tls_options,
    )


def main() -> None:
    """Load the configuration and run the server on the configured transport."""
    try:
        settings = load_settings()
        configure_logging(settings)
        warnings = settings.security_warnings()
    except ConfigError as exc:
        sys.stderr.write(f"{exc}\n")
        raise SystemExit(2) from None
    for warning in warnings:
        logger.warning(warning)
    # FastMCP checks PyPI for updates when it prints its banner: never phone home from a server.
    fastmcp.settings.check_for_updates = "off"
    metrics = Metrics()
    if settings.mcp_transport is Transport.HTTP:
        try:
            run_http(settings, metrics)
        except ConfigError as exc:
            logger.error("%s", exc)
            raise SystemExit(2) from None
        return
    server = build_server(settings, client=OtcsClient(settings, metrics=metrics), metrics=metrics)
    server.run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main()
