# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Joaquín Garzón

"""Exception hierarchy for the OpenText MCP server.

Messages are written to be shown to an AI agent: they are actionable and never
contain URLs, hostnames, tickets, passwords or response bodies.
"""


class McpServerError(Exception):
    """Base class for all errors raised by this package."""


class ConfigError(McpServerError):
    """The configuration is missing or invalid (the server must not start)."""


class InvalidInputError(McpServerError):
    """A tool argument failed validation."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(f"Invalid value for '{field}': {message}")
        self.field = field


class OtcsError(McpServerError):
    """Base class for errors talking to OpenText Content Management."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class OtcsAuthError(OtcsError):
    """Authentication with OpenText failed (HTTP 401 or rejected credentials)."""


class OtcsPermissionError(OtcsError):
    """The service account is not allowed to perform the operation (HTTP 403)."""


class OtcsNotFoundError(OtcsError):
    """The requested resource does not exist (HTTP 404)."""


class OtcsConflictError(OtcsError):
    """The change conflicts with a concurrent edit (HTTP 409)."""


class OtcsBadRequestError(OtcsError):
    """OpenText rejected the request as invalid (HTTP 400)."""


class OtcsRateLimitError(OtcsError):
    """OpenText is throttling requests (HTTP 429)."""

    def __init__(
        self, message: str, *, status_code: int | None = None, retry_after: float | None = None
    ) -> None:
        super().__init__(message, status_code=status_code)
        self.retry_after = retry_after


class OtcsServerError(OtcsError):
    """OpenText reported an internal error (HTTP 5xx)."""


class OtcsTimeoutError(OtcsError):
    """The request to OpenText timed out."""


class OtcsConnectionError(OtcsError):
    """The OpenText server could not be reached."""


class OtcsResponseError(OtcsError):
    """OpenText returned a response this server cannot use (malformed, oversized, redirect)."""
